"""Trash service — dual-trash with restore registry.

DESIGN.md §10.

When a file is trashed, Curator does FIVE things atomically:

  1. **Pre-trash hook** — gives plugins a chance to veto
     (``curator_pre_trash`` returning ``ConfirmationResult(allow=False)``).
  2. **Snapshot metadata** — bundle memberships, flex attrs, hash. These
     go into the :class:`TrashRecord` so restore can reverse exactly.
  3. **Send to OS trash** — Windows Recycle Bin / macOS Trash / Linux
     freedesktop trash, via ``send2trash``. The OS handles the actual
     bytes.
  4. **Insert TrashRecord row** — Curator's authoritative restore record.
  5. **Soft-delete the file row** — set ``deleted_at = now()``.
     Lineage edges and (now-detached) bundle memberships are preserved
     so that audit queries still resolve.

Plus a **post-trash hook** (``curator_post_trash``) for plugin
notifications, and an audit log entry written by the caller (typically
:class:`AuditService.log()`).

Restore reverses these steps: pre-restore hook → restore from OS trash
(if location known) → reactivate file row → restore bundle memberships
+ flex attrs from snapshot → delete trash record → post-restore hook.

**send2trash availability**
Phase Alpha imports the (eventual) vendored copy first, then PyPI's
``send2trash``, then fails fast if neither is installed. We do NOT
silently fall back to ``os.remove`` — that would destroy data without
a restore path. Better to refuse than to delete.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from typing import Any
from uuid import UUID

import pluggy
from loguru import logger

from curator.models.file import FileEntity
from curator.models.results import ConfirmationResult
from curator.models.trash import TrashRecord
from curator.storage.repositories.audit_repo import AuditRepository
from curator.storage.repositories.bundle_repo import BundleRepository
from curator.storage.repositories.file_repo import FileRepository
from curator.storage.repositories.trash_repo import TrashRepository


# ---------------------------------------------------------------------------
# send2trash availability check
# ---------------------------------------------------------------------------
_send2trash = None
try:
    from curator._vendored.send2trash import send2trash as _send2trash  # type: ignore[import-not-found]
except ImportError:
    try:
        from send2trash import send2trash as _send2trash  # type: ignore[import-not-found]
    except ImportError:
        _send2trash = None


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class TrashError(Exception):
    """Base for trash/restore-specific errors."""


class TrashVetoed(TrashError):
    """A plugin vetoed the trash operation via ``pre_trash`` hook."""


class RestoreVetoed(TrashError):
    """A plugin vetoed the restore operation via ``pre_restore`` hook."""


class NotInTrashError(TrashError):
    """Restore was requested for a file that has no TrashRecord."""


class RestoreImpossibleError(TrashError):
    """Trash record exists but Curator can't restore (no OS trash location)."""


class FileNotFoundError(TrashError):
    """The file row doesn't exist in Curator's index."""


class Send2TrashUnavailableError(TrashError):
    """send2trash isn't installed; we refuse to trash without it."""


# ---------------------------------------------------------------------------
# TrashService
# ---------------------------------------------------------------------------

class TrashService:
    """Dual-trash with snapshot-based restore.

    The service composes several repositories. It does NOT manage
    transactions across them — each call to ``trash()`` / ``restore()``
    is a sequence of independent statements. The order is chosen so
    that a partial failure leaves Curator's DB and the OS trash in a
    consistent state.
    """

    def __init__(
        self,
        plugin_manager: pluggy.PluginManager,
        file_repo: FileRepository,
        trash_repo: TrashRepository,
        bundle_repo: BundleRepository,
        audit_repo: AuditRepository,
    ):
        self.pm = plugin_manager
        self.files = file_repo
        self.trash = trash_repo
        self.bundles = bundle_repo
        self.audit = audit_repo

    # ------------------------------------------------------------------
    # Trash
    # ------------------------------------------------------------------

    def send_to_trash(
        self,
        curator_id: UUID,
        *,
        reason: str,
        actor: str = "user",
    ) -> TrashRecord:
        """Trash a file and snapshot enough metadata to restore later.

        Steps:
            1. Pre-trash plugin hook (may veto).
            2. Snapshot bundle memberships + flex attrs.
            3. send2trash on the file (Windows Recycle Bin / macOS Trash / etc.).
            4. Insert TrashRecord.
            5. Mark file row deleted_at = now().
            6. Post-trash plugin hook.
            7. Audit log entry.

        Raises:
            FileNotFoundError: no file row for ``curator_id``.
            Send2TrashUnavailableError: send2trash not importable.
            TrashVetoed: a plugin returned ConfirmationResult(allow=False).
        """
        if _send2trash is None:
            raise Send2TrashUnavailableError(
                "send2trash is not available. Install it (pip install send2trash) or "
                "wait for the vendored copy in Step 8 before trashing files."
            )

        file = self.files.get(curator_id)
        if file is None:
            raise FileNotFoundError(f"No file with curator_id={curator_id}")

        # Step 1: pre-trash hook (veto check)
        veto = self._check_pre_trash_veto(file, reason)
        if veto is not None:
            raise TrashVetoed(
                f"Plugin {veto.plugin} vetoed trash: {veto.reason or '(no reason given)'}"
            )

        # Step 2: snapshot metadata BEFORE the file is gone
        memberships_snapshot = [
            {
                "bundle_id": str(m.bundle_id),
                "role": m.role,
                "confidence": m.confidence,
            }
            for m in self.bundles.get_memberships_for_file(curator_id)
        ]
        attrs_snapshot = dict(file.flex)

        # Step 3: send to OS trash
        try:
            _send2trash(file.source_path)
            os_trash_location = self._derive_os_trash_location(file.source_path)
        except Exception as e:
            # If send2trash fails, we have NOT modified anything yet —
            # bail without inserting a TrashRecord or marking deleted.
            logger.error(
                "send2trash failed for {p}: {e}",
                p=file.source_path, e=e,
            )
            raise TrashError(f"Failed to send to OS trash: {e}") from e

        # Step 4: insert TrashRecord
        record = TrashRecord(
            curator_id=curator_id,
            original_source_id=file.source_id,
            original_path=file.source_path,
            file_hash=file.xxhash3_128,
            trashed_by=actor,
            reason=reason,
            bundle_memberships_snapshot=memberships_snapshot,
            file_attrs_snapshot=attrs_snapshot,
            os_trash_location=os_trash_location,
        )
        self.trash.insert(record)

        # Step 5: soft-delete the file row
        self.files.mark_deleted(curator_id)

        # Step 6: post-trash hook (notification only; no return value used)
        try:
            self.pm.hook.curator_post_trash(trash_record=record)
        except Exception as e:  # pragma: no cover — defensive
            logger.warning("post_trash hook raised: {e}", e=e)

        # Step 7: audit log
        self.audit.log(
            actor=actor,
            action="trash",
            entity_type="file",
            entity_id=str(curator_id),
            details={
                "reason": reason,
                "original_path": file.source_path,
                "os_trash_location": os_trash_location,
            },
        )

        return record

    # ------------------------------------------------------------------
    # Restore
    # ------------------------------------------------------------------

    def restore(
        self,
        curator_id: UUID,
        *,
        target_path: str | None = None,
        actor: str = "user",
    ) -> FileEntity:
        """Restore a previously-trashed file.

        Args:
            curator_id: the file to restore.
            target_path: optional alternative restore path. If not set,
                         falls back to the trash record's
                         ``restore_path_override`` then to ``original_path``.
            actor: who's doing the restore (audit log).

        Raises:
            NotInTrashError: no TrashRecord for ``curator_id``.
            RestoreVetoed: a plugin returned ConfirmationResult(allow=False).
            RestoreImpossibleError: the OS trash location wasn't recorded;
                                     user must restore manually.
        """
        record = self.trash.get(curator_id)
        if record is None:
            raise NotInTrashError(f"No trash record for curator_id={curator_id}")

        restore_path = (
            target_path
            or record.restore_path_override
            or record.original_path
        )

        # Pre-restore hook (veto check)
        veto = self._check_pre_restore_veto(record, restore_path)
        if veto is not None:
            raise RestoreVetoed(
                f"Plugin {veto.plugin} vetoed restore: {veto.reason or '(no reason given)'}"
            )

        # Restore from OS trash (Phase Alpha: only supported when
        # os_trash_location is recorded — currently always None on
        # Windows because send2trash doesn't return the new location).
        if record.os_trash_location is None:
            raise RestoreImpossibleError(
                "No OS trash location recorded for this file. "
                "Restore manually from the system Recycle Bin / Trash, "
                "then run `curator scan` to re-index. "
                "(Phase Alpha limitation; future versions will track this.)"
            )

        try:
            self._restore_from_os_trash(record.os_trash_location, restore_path)
        except Exception as e:
            raise RestoreImpossibleError(
                f"Failed to restore from {record.os_trash_location}: {e}"
            ) from e

        # Reactivate file row + restore metadata
        file = self.files.get(curator_id)
        if file is None:  # pragma: no cover — defensive (FK should prevent this)
            raise FileNotFoundError(
                f"File row gone after restore for curator_id={curator_id}"
            )
        file.deleted_at = None
        file.source_path = restore_path
        for k, v in record.file_attrs_snapshot.items():
            file.set_flex(k, v)
        self.files.update(file)
        self.files.undelete(curator_id)

        # Restore bundle memberships from snapshot
        from curator.models.bundle import BundleMembership
        for m in record.bundle_memberships_snapshot:
            try:
                self.bundles.add_membership(
                    BundleMembership(
                        bundle_id=UUID(m["bundle_id"]),
                        curator_id=curator_id,
                        role=m.get("role", "member"),
                        confidence=m.get("confidence", 1.0),
                    )
                )
            except Exception as e:  # pragma: no cover — defensive
                logger.warning(
                    "couldn't restore bundle membership {bid}: {e}",
                    bid=m.get("bundle_id"), e=e,
                )

        # Remove the trash record
        self.trash.delete(curator_id)

        # Post-restore hook
        try:
            self.pm.hook.curator_post_restore(file=file)
        except Exception as e:  # pragma: no cover
            logger.warning("post_restore hook raised: {e}", e=e)

        # Audit
        self.audit.log(
            actor=actor,
            action="restore",
            entity_type="file",
            entity_id=str(curator_id),
            details={"restored_to": restore_path},
        )

        return file

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def list_trashed(
        self,
        *,
        since: datetime | None = None,
        actor: str | None = None,
        limit: int | None = None,
    ) -> list[TrashRecord]:
        return self.trash.list(since=since, actor=actor, limit=limit)

    def is_in_trash(self, curator_id: UUID) -> bool:
        return self.trash.get(curator_id) is not None

    # ------------------------------------------------------------------
    # Internal: hook handling
    # ------------------------------------------------------------------

    def _check_pre_trash_veto(
        self,
        file: FileEntity,
        reason: str,
    ) -> ConfirmationResult | None:
        """Run pre-trash hooks; return the first veto, or None."""
        try:
            results = self.pm.hook.curator_pre_trash(file=file, reason=reason)
        except Exception as e:  # pragma: no cover — defensive
            logger.warning("pre_trash hook raised: {e}", e=e)
            return None
        for r in results:
            if r is not None and not r.allow:
                return r
        return None

    def _check_pre_restore_veto(
        self,
        record: TrashRecord,
        target_path: str,
    ) -> ConfirmationResult | None:
        try:
            results = self.pm.hook.curator_pre_restore(
                trash_record=record, target_path=target_path
            )
        except Exception as e:  # pragma: no cover
            logger.warning("pre_restore hook raised: {e}", e=e)
            return None
        for r in results:
            if r is not None and not r.allow:
                return r
        return None

    # ------------------------------------------------------------------
    # Internal: OS trash interaction (Phase Alpha local-only)
    # ------------------------------------------------------------------

    def _derive_os_trash_location(self, original_path: str) -> str | None:
        """Find the file's location inside the OS Recycle Bin (Q14, Windows).

        Called immediately after :func:`send2trash` returns, so the file
        has just been moved into the Recycle Bin. We scan the same-drive
        ``$Recycle.Bin`` folder, parse every ``$IXXXXXX.ext`` metadata
        file, and find the most recently deleted one whose recorded
        original path matches.

        Returns the absolute path of the corresponding ``$RXXXXXX.ext``
        content file, or ``None`` if:

          * we're not on Windows (mac/Linux trash tracking is Phase Beta+),
          * no matching entry was found (file went somewhere unexpected),
          * the recycle-bin reader module couldn't be imported.

        ``None`` triggers the same Phase Alpha fallback behaviour as
        before: ``restore`` raises :class:`RestoreImpossibleError` and
        the user restores manually + re-scans.
        """
        if sys.platform != "win32":
            return None
        try:
            from curator._vendored.send2trash.win.recycle_bin import (
                find_in_recycle_bin,
            )
        except ImportError:
            return None

        try:
            entry = find_in_recycle_bin(original_path)
        except Exception as e:  # pragma: no cover — defensive
            logger.warning(
                "recycle-bin lookup failed for {p}: {e}",
                p=original_path, e=e,
            )
            return None
        if entry is None:
            return None
        return str(entry.content_path)

    def _restore_from_os_trash(self, os_location: str, target_path: str) -> None:
        """Move a file from OS trash back to ``target_path``.

        On Windows, ``os_location`` is the ``$RXXXXXX.ext`` file inside
        ``<drive>:\\$Recycle.Bin\\<SID>\\``. We rename it to the target,
        then delete the matching ``$IXXXXXX.ext`` metadata file so the
        Recycle Bin stays consistent.
        """
        if not os.path.exists(os_location):
            raise RestoreImpossibleError(
                f"OS trash location {os_location} doesn't exist on disk"
            )
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        os.replace(os_location, target_path)

        # Best-effort: delete the matching $I metadata so the bin stays clean.
        # Failure here doesn't fail the restore — the file is back where it
        # belongs and that's what matters.
        index_companion = os.path.join(
            os.path.dirname(os_location),
            os.path.basename(os_location).replace("$R", "$I", 1),
        )
        try:
            if os.path.exists(index_companion):
                os.remove(index_companion)
        except OSError as e:  # pragma: no cover — cosmetic
            logger.debug(
                "couldn't remove $I companion {i}: {e}",
                i=index_companion, e=e,
            )
