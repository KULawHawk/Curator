"""Focused unit tests for MigrationService cross-source execution.

Sub-ship 3 of the Migration Phase Gamma arc (v1.7.91).
Scope plan: docs/MIGRATION_PHASE_GAMMA_SCOPE.md

This is the biggest sub-ship in the arc by line count. Targets:

* `_execute_one_cross_source` happy path + failure paths (lines 1099-1233)
* Cross-source on-conflict dispatch (lines 1140-1178)
* FileEntity index update + cross-source trash (lines 1198, 1204-1207, 1218-1227)
* `_cross_source_overwrite_with_backup` full body (lines 1530, 1576-1613, 1630-1655, 1672-1692)
* `_find_existing_dst_file_id_for_overwrite` strategies (~1466 area)
* `_attempt_cross_source_backup_rename` plugin dispatch

Strategy: test `_execute_one_cross_source` directly (bypassing `apply()`)
so the test isolates cross-source orchestration from apply()'s gates.
Cross-source byte transfer is mocked by monkeypatching
`_cross_source_transfer` to return canned (outcome, file_id, hash) tuples.

Stubs reused from v1.7.89/90 via import (Lesson #84).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

import pytest

from curator.models.file import FileEntity
from curator.services.migration import (
    MigrationConflictError,
    MigrationMove,
    MigrationOutcome,
)
from curator.services.safety import SafetyLevel

# Reuse stubs from v1.7.89 (Lesson #84 — stub patterns compound across sub-ships).
from tests.unit.test_migration_plan_apply import (
    NOW,
    StubAuditRepository,
    StubFileRepository,
    StubSafetyService,
    make_service,
)


# ===========================================================================
# Pluggy stub for migration (new this sub-ship)
# ===========================================================================


@dataclass
class StubMigrationHooks:
    """Configurable hook namespace for migration tests.

    Each hook name is a callable that returns a list (pluggy convention)
    OR a single value (legacy behavior). Tests assign per-hook callables
    via `StubMigrationPluginManager.set_hook(name, fn)`. Missing hooks
    raise AttributeError on access (per `_attempt_cross_source_backup_rename`
    line 1763).
    """

    _impls: dict[str, Callable[..., Any]] = field(default_factory=dict)
    _missing: set[str] = field(default_factory=set)

    def __getattr__(self, name: str) -> Callable[..., Any]:
        # __getattr__ is only called when the attribute isn't found
        # normally, so this is safe.
        if name in self._missing:
            raise AttributeError(
                f"'StubMigrationHooks' has no hook {name!r}"
            )
        return self._impls.get(name, lambda **_: [])


@dataclass
class StubMigrationPluginManager:
    hook: StubMigrationHooks = field(default_factory=StubMigrationHooks)

    def set_hook(self, name: str, fn: Callable[..., Any]) -> None:
        self.hook._impls[name] = fn

    def remove_hook(self, name: str) -> None:
        """Mark a hook as unregistered (getattr will raise AttributeError)."""
        self.hook._missing.add(name)


# ===========================================================================
# Helpers
# ===========================================================================


def _make_cross_source_setup(
    *,
    pm: StubMigrationPluginManager | None = None,
    audit: bool = False,
    file_repo: StubFileRepository | None = None,
):
    """Build a service + move + (optional audit) for cross-source tests.

    The move's src/dst paths are plain strings (not real files); the
    cross-source transfer is mocked separately by tests that need it.
    """
    audit_repo = StubAuditRepository() if audit else None
    if file_repo is None:
        # Default entity present so index update can succeed.
        entity = FileEntity(
            source_id="local",
            source_path="/src/file.txt",
            size=100,
            mtime=NOW,
            xxhash3_128="src_hash",
        )
        file_repo = StubFileRepository(files=[entity])

    svc = make_service(file_repo=file_repo, audit=audit_repo)
    svc.pm = pm  # may be None
    # Take the first entity's curator_id (if any) for the move
    move_curator_id = next(iter(file_repo._files), uuid4())
    move = MigrationMove(
        curator_id=move_curator_id,
        src_path="/src/file.txt",
        dst_path="dst/file.txt",  # cross-source: not necessarily a real path
        safety_level=SafetyLevel.SAFE,
        size=100,
        src_xxhash="src_hash",
    )
    return svc, move, audit_repo, file_repo


def _set_transfer_result(
    monkeypatch, svc, outcome: MigrationOutcome,
    actual_file_id: str = "dst/file.txt",
    verified_hash: str | None = "src_hash",
):
    """Monkeypatch _cross_source_transfer to return a canned tuple."""
    def fake_transfer(*, src_source_id, src_file_id, src_xxhash,
                      dst_source_id, dst_path, verify_hash):
        return (outcome, actual_file_id, verified_hash)
    monkeypatch.setattr(svc, "_cross_source_transfer", fake_transfer)


def _set_transfer_raises(monkeypatch, svc, exc: Exception):
    def fake_transfer(**kwargs):
        raise exc
    monkeypatch.setattr(svc, "_cross_source_transfer", fake_transfer)


# ===========================================================================
# _execute_one_cross_source — happy path + transfer failure boundary
# ===========================================================================


class TestCrossSourceHappyPath:
    def test_moved_outcome_writes_index_and_trashes_src(
        self, monkeypatch,
    ):
        # Happy path: transfer succeeds; FileEntity updated; trash hook
        # called with deleted=True; outcome=MOVED; audit_move emitted.
        pm = StubMigrationPluginManager()
        trash_calls: list[dict[str, Any]] = []

        def trash_hook(*, source_id, file_id, to_trash):
            trash_calls.append(
                {"source_id": source_id, "file_id": file_id, "to_trash": to_trash}
            )
            return [True]  # pluggy list-result with success

        pm.set_hook("curator_source_delete", trash_hook)
        svc, move, audit, file_repo = _make_cross_source_setup(pm=pm, audit=True)
        _set_transfer_result(
            monkeypatch, svc, MigrationOutcome.MOVED,
            actual_file_id="dst/file.txt", verified_hash="src_hash",
        )

        svc._execute_one_cross_source(
            move, verify_hash=True, keep_source=False,
            src_source_id="local", dst_source_id="gdrive",
        )

        assert move.outcome == MigrationOutcome.MOVED
        assert move.dst_path == "dst/file.txt"
        # Index updated: entity now has new source_id + source_path
        updated_entity = file_repo._files[move.curator_id]
        assert updated_entity.source_id == "gdrive"
        assert updated_entity.source_path == "dst/file.txt"
        # Trash hook called
        assert len(trash_calls) == 1
        assert trash_calls[0]["source_id"] == "local"
        # Audit move emitted
        # AuditRepository receives via either .log() or .insert();
        # _audit_move uses .insert() so check that path
        # (StubAuditRepository implements .log; insert is missing -- but
        # the warning is caught in the except clause).

    def test_transfer_exception_marks_failed(self, monkeypatch):
        # Lines 1115-1118: any exception from _cross_source_transfer →
        # outcome=FAILED with the exception details.
        svc, move, _, _ = _make_cross_source_setup()
        _set_transfer_raises(monkeypatch, svc, RuntimeError("transfer boom"))

        svc._execute_one_cross_source(
            move, verify_hash=True, keep_source=False,
            src_source_id="local", dst_source_id="gdrive",
        )

        assert move.outcome == MigrationOutcome.FAILED
        assert "RuntimeError" in (move.error or "")
        assert "transfer boom" in (move.error or "")

    def test_hash_mismatch_propagated(self, monkeypatch):
        # When transfer returns HASH_MISMATCH outcome, that's surfaced
        # to the move with a "hash mismatch" error message.
        svc, move, _, _ = _make_cross_source_setup()
        _set_transfer_result(
            monkeypatch, svc, MigrationOutcome.HASH_MISMATCH,
            verified_hash="wrong_hash",
        )

        svc._execute_one_cross_source(
            move, verify_hash=True, keep_source=False,
            src_source_id="local", dst_source_id="gdrive",
        )

        assert move.outcome == MigrationOutcome.HASH_MISMATCH
        assert "hash mismatch" in (move.error or "")


# ===========================================================================
# _execute_one_cross_source — collision dispatch (4 modes + defensive)
# ===========================================================================


class TestCrossSourceCollisionDispatch:
    def test_skip_mode_keeps_skipped_collision(self, monkeypatch):
        # Lines 1141-1143: mode=skip → outcome stays SKIPPED_COLLISION.
        svc, move, _, _ = _make_cross_source_setup()
        svc.set_on_conflict_mode("skip")
        _set_transfer_result(
            monkeypatch, svc, MigrationOutcome.SKIPPED_COLLISION,
        )

        svc._execute_one_cross_source(
            move, verify_hash=True, keep_source=False,
            src_source_id="local", dst_source_id="gdrive",
        )

        assert move.outcome == MigrationOutcome.SKIPPED_COLLISION

    def test_fail_mode_marks_failed_due_to_conflict(self, monkeypatch):
        # Lines 1144-1152: mode=fail → outcome=FAILED_DUE_TO_CONFLICT
        # with error message + audit_conflict emitted (cross_source=True).
        svc, move, _, _ = _make_cross_source_setup(audit=True)
        svc.set_on_conflict_mode("fail")
        _set_transfer_result(
            monkeypatch, svc, MigrationOutcome.SKIPPED_COLLISION,
        )

        svc._execute_one_cross_source(
            move, verify_hash=True, keep_source=False,
            src_source_id="local", dst_source_id="gdrive",
        )

        assert move.outcome == MigrationOutcome.FAILED_DUE_TO_CONFLICT
        assert "cross-source" in (move.error or "").lower()

    def test_overwrite_with_backup_success(self, monkeypatch):
        # Lines 1153-1163: mode=overwrite-with-backup → calls
        # _cross_source_overwrite_with_backup, finalize as
        # MOVED_OVERWROTE_WITH_BACKUP.
        svc, move, _, _ = _make_cross_source_setup()
        svc.set_on_conflict_mode("overwrite-with-backup")
        # First transfer returns SKIPPED_COLLISION (collision detected)
        # Then the backup helper returns success tuple
        _set_transfer_result(
            monkeypatch, svc, MigrationOutcome.SKIPPED_COLLISION,
        )

        def fake_backup(m, *, verify_hash, src_source_id, dst_source_id):
            return (MigrationOutcome.MOVED, "dst/file.txt", "src_hash")

        monkeypatch.setattr(
            svc, "_cross_source_overwrite_with_backup", fake_backup,
        )

        svc._execute_one_cross_source(
            move, verify_hash=True, keep_source=False,
            src_source_id="local", dst_source_id="gdrive",
        )

        assert move.outcome == MigrationOutcome.MOVED_OVERWROTE_WITH_BACKUP

    def test_overwrite_with_backup_degraded_returns_none(self, monkeypatch):
        # Lines 1159-1160: backup helper returns None → degraded;
        # outcome already set inside helper; early return.
        svc, move, _, _ = _make_cross_source_setup()
        svc.set_on_conflict_mode("overwrite-with-backup")
        _set_transfer_result(
            monkeypatch, svc, MigrationOutcome.SKIPPED_COLLISION,
        )

        def fake_backup(m, **_):
            m.outcome = MigrationOutcome.SKIPPED_COLLISION  # degrade
            return None

        monkeypatch.setattr(
            svc, "_cross_source_overwrite_with_backup", fake_backup,
        )

        svc._execute_one_cross_source(
            move, verify_hash=True, keep_source=False,
            src_source_id="local", dst_source_id="gdrive",
        )

        assert move.outcome == MigrationOutcome.SKIPPED_COLLISION

    def test_rename_with_suffix_success(self, monkeypatch):
        # Lines 1164-1174: mode=rename-with-suffix → calls
        # _cross_source_rename_with_suffix, finalize as
        # MOVED_RENAMED_WITH_SUFFIX.
        svc, move, _, _ = _make_cross_source_setup()
        svc.set_on_conflict_mode("rename-with-suffix")
        _set_transfer_result(
            monkeypatch, svc, MigrationOutcome.SKIPPED_COLLISION,
        )

        def fake_rename(m, *, verify_hash, src_source_id, dst_source_id):
            return (MigrationOutcome.MOVED, "dst/file.curator-1.txt", "src_hash")

        monkeypatch.setattr(
            svc, "_cross_source_rename_with_suffix", fake_rename,
        )

        svc._execute_one_cross_source(
            move, verify_hash=True, keep_source=False,
            src_source_id="local", dst_source_id="gdrive",
        )

        assert move.outcome == MigrationOutcome.MOVED_RENAMED_WITH_SUFFIX

    def test_rename_with_suffix_degraded_returns_none(self, monkeypatch):
        # Lines 1170-1171: rename helper returns None → degraded.
        svc, move, _, _ = _make_cross_source_setup()
        svc.set_on_conflict_mode("rename-with-suffix")
        _set_transfer_result(
            monkeypatch, svc, MigrationOutcome.SKIPPED_COLLISION,
        )

        def fake_rename(m, **_):
            m.outcome = MigrationOutcome.SKIPPED_COLLISION
            return None

        monkeypatch.setattr(
            svc, "_cross_source_rename_with_suffix", fake_rename,
        )

        svc._execute_one_cross_source(
            move, verify_hash=True, keep_source=False,
            src_source_id="local", dst_source_id="gdrive",
        )

        assert move.outcome == MigrationOutcome.SKIPPED_COLLISION

    def test_unknown_mode_defensive_fallback(self, monkeypatch):
        # Lines 1175-1178: defensive `else` for unknown mode.
        # Bypass setter validation to reach this branch.
        svc, move, _, _ = _make_cross_source_setup()
        svc._on_conflict_mode = "bogus-mode"
        _set_transfer_result(
            monkeypatch, svc, MigrationOutcome.SKIPPED_COLLISION,
        )

        svc._execute_one_cross_source(
            move, verify_hash=True, keep_source=False,
            src_source_id="local", dst_source_id="gdrive",
        )

        # Falls back to keeping the SKIPPED_COLLISION outcome
        assert move.outcome == MigrationOutcome.SKIPPED_COLLISION


# ===========================================================================
# _execute_one_cross_source — keep_source + index + trash branches
# ===========================================================================


class TestCrossSourceKeepSourceAndIndex:
    def test_keep_source_marks_copied_skips_index_and_trash(
        self, monkeypatch,
    ):
        # Lines 1187-1192: keep_source=True → outcome=COPIED, no index
        # update, no trash, audit_copy emitted.
        pm = StubMigrationPluginManager()
        trash_calls: list[Any] = []

        def trash_hook(**kw):
            trash_calls.append(kw)
            return [True]

        pm.set_hook("curator_source_delete", trash_hook)
        svc, move, _, file_repo = _make_cross_source_setup(pm=pm)
        _set_transfer_result(
            monkeypatch, svc, MigrationOutcome.MOVED, "dst/file.txt", "src_hash",
        )

        svc._execute_one_cross_source(
            move, verify_hash=True, keep_source=True,  # KEEP
            src_source_id="local", dst_source_id="gdrive",
        )

        assert move.outcome == MigrationOutcome.COPIED
        # No index updates
        assert len(file_repo.updates) == 0
        # No trash call
        assert len(trash_calls) == 0

    def test_filed_entity_vanished_marks_failed(self, monkeypatch):
        # Line 1198: files.get(curator_id) returns None → RuntimeError
        # caught at lines 1204-1207 → outcome=FAILED with "index update".
        # Trigger: pass a move with a curator_id that's NOT in file_repo.
        file_repo = StubFileRepository(files=[])  # empty
        svc, move, _, _ = _make_cross_source_setup(file_repo=file_repo)
        move.curator_id = uuid4()  # definitely not in repo
        _set_transfer_result(
            monkeypatch, svc, MigrationOutcome.MOVED, "dst/file.txt", "src_hash",
        )

        svc._execute_one_cross_source(
            move, verify_hash=True, keep_source=False,
            src_source_id="local", dst_source_id="gdrive",
        )

        assert move.outcome == MigrationOutcome.FAILED
        assert "index update failed" in (move.error or "")
        assert "vanished" in (move.error or "")

    def test_index_update_exception_marks_failed(self, monkeypatch):
        # Lines 1204-1207: file_repo.update raises → caught,
        # outcome=FAILED with "index update failed".
        svc, move, _, file_repo = _make_cross_source_setup()
        _set_transfer_result(
            monkeypatch, svc, MigrationOutcome.MOVED, "dst/file.txt", "src_hash",
        )

        def boom_update(entity):
            raise RuntimeError("DB locked")

        monkeypatch.setattr(file_repo, "update", boom_update)

        svc._execute_one_cross_source(
            move, verify_hash=True, keep_source=False,
            src_source_id="local", dst_source_id="gdrive",
        )

        assert move.outcome == MigrationOutcome.FAILED
        assert "index update failed" in (move.error or "")
        assert "DB locked" in (move.error or "")


# ===========================================================================
# _execute_one_cross_source — trash plugin error/false paths
# ===========================================================================


class TestCrossSourceTrashHook:
    def test_trash_returns_false_appends_error_but_succeeds(
        self, monkeypatch,
    ):
        # Lines 1217-1221: trash plugin returns False (or None, or
        # falsy non-True) → move.error gets appended, but move.outcome
        # is still MOVED (trash is best-effort).
        pm = StubMigrationPluginManager()
        pm.set_hook("curator_source_delete", lambda **_: [False])
        svc, move, _, _ = _make_cross_source_setup(pm=pm)
        _set_transfer_result(
            monkeypatch, svc, MigrationOutcome.MOVED, "dst/file.txt", "src_hash",
        )

        svc._execute_one_cross_source(
            move, verify_hash=True, keep_source=False,
            src_source_id="local", dst_source_id="gdrive",
        )

        assert move.outcome == MigrationOutcome.MOVED
        assert "trash failed" in (move.error or "")
        assert "False" in (move.error or "")

    def test_trash_raises_appends_error_but_succeeds(self, monkeypatch):
        # Lines 1222-1230: trash try/except defensive boundary.
        # NOTE: _hook_first_result itself swallows non-FileExistsError
        # exceptions (line 2255), so a plugin hook raising RuntimeError
        # gets converted to a None return (which hits the "not deleted"
        # branch at lines 1217-1221, not the outer except). The outer
        # except at 1222-1230 is a defensive boundary that only fires
        # if _hook_first_result itself raises (e.g. unforeseen edge
        # cases or future refactors). Test it by monkeypatching the
        # helper directly -- which documents both the defensive
        # boundary AND the unreachable-via-plugin contract.
        svc, move, _, _ = _make_cross_source_setup()
        _set_transfer_result(
            monkeypatch, svc, MigrationOutcome.MOVED, "dst/file.txt", "src_hash",
        )

        def boom_helper(hook_name, **kw):
            if hook_name == "curator_source_delete":
                raise RuntimeError("hook helper propagated")
            return None

        monkeypatch.setattr(svc, "_hook_first_result", boom_helper)

        svc._execute_one_cross_source(
            move, verify_hash=True, keep_source=False,
            src_source_id="local", dst_source_id="gdrive",
        )

        assert move.outcome == MigrationOutcome.MOVED
        assert "trash failed" in (move.error or "")
        assert "RuntimeError" in (move.error or "")
        assert "hook helper propagated" in (move.error or "")


# ===========================================================================
# _cross_source_overwrite_with_backup — full body
# ===========================================================================


class TestCrossSourceOverwriteBackup:
    """Full coverage of the backup retry flow."""

    def _prep_svc(self, monkeypatch, audit: bool = True):
        """Build a service + move + audit ready for backup tests."""
        svc, move, audit_repo, _ = _make_cross_source_setup(audit=audit)
        return svc, move, audit_repo

    def test_happy_path_returns_moved_tuple(self, monkeypatch):
        # Lines 1576-1613: find succeeds + rename succeeds + retry
        # transfer succeeds → returns (MOVED, file_id, hash).
        svc, move, audit = self._prep_svc(monkeypatch)
        monkeypatch.setattr(
            svc, "_find_existing_dst_file_id_for_overwrite",
            lambda *a, **kw: "existing_dst_id",
        )
        monkeypatch.setattr(
            svc, "_attempt_cross_source_backup_rename",
            lambda *a, **kw: (True, None),
        )
        monkeypatch.setattr(
            svc, "_cross_source_transfer",
            lambda **kw: (MigrationOutcome.MOVED, "new_dst_id", "src_hash"),
        )

        result = svc._cross_source_overwrite_with_backup(
            move, verify_hash=True,
            src_source_id="local", dst_source_id="gdrive",
        )

        assert result == (MigrationOutcome.MOVED, "new_dst_id", "src_hash")
        # Audit emitted for the successful rename BEFORE retry (DM-5)
        backup_audits = [
            e for e in audit.entries
            if e["details"].get("mode") == "overwrite-with-backup"
        ]
        assert len(backup_audits) >= 1

    def test_existing_file_not_found_degrades_to_skip(self, monkeypatch):
        # Lines 1576-1592: existing_file_id is None → degrade to skip.
        svc, move, audit = self._prep_svc(monkeypatch)
        monkeypatch.setattr(
            svc, "_find_existing_dst_file_id_for_overwrite",
            lambda *a, **kw: None,
        )

        result = svc._cross_source_overwrite_with_backup(
            move, verify_hash=True,
            src_source_id="local", dst_source_id="gdrive",
        )

        assert result is None
        assert move.outcome == MigrationOutcome.SKIPPED_COLLISION
        # Degrade audit emitted
        degrade_audits = [
            e for e in audit.entries
            if "degraded" in e["details"].get("mode", "")
        ]
        assert len(degrade_audits) >= 1

    def test_rename_fails_degrades_to_skip(self, monkeypatch):
        # Lines 1597-1613: rename hook fails → degrade to skip.
        svc, move, audit = self._prep_svc(monkeypatch)
        monkeypatch.setattr(
            svc, "_find_existing_dst_file_id_for_overwrite",
            lambda *a, **kw: "existing_dst_id",
        )
        monkeypatch.setattr(
            svc, "_attempt_cross_source_backup_rename",
            lambda *a, **kw: (False, "plugin doesn't implement"),
        )

        result = svc._cross_source_overwrite_with_backup(
            move, verify_hash=True,
            src_source_id="local", dst_source_id="gdrive",
        )

        assert result is None
        assert move.outcome == MigrationOutcome.SKIPPED_COLLISION
        # Degrade audit captures the reason
        degrade_audits = [
            e for e in audit.entries
            if "degraded" in e["details"].get("mode", "")
        ]
        assert len(degrade_audits) >= 1
        assert "plugin doesn't implement" in str(degrade_audits[0]["details"])

    def test_retry_transfer_raises_marks_failed_with_backup_preserved(
        self, monkeypatch,
    ):
        # Lines 1630-1655: retry _cross_source_transfer raises →
        # outcome=FAILED with "backup at ... preserved per DM-5".
        svc, move, audit = self._prep_svc(monkeypatch)
        monkeypatch.setattr(
            svc, "_find_existing_dst_file_id_for_overwrite",
            lambda *a, **kw: "existing_dst_id",
        )
        monkeypatch.setattr(
            svc, "_attempt_cross_source_backup_rename",
            lambda *a, **kw: (True, None),
        )

        def boom_transfer(**kw):
            raise RuntimeError("rate limited")

        monkeypatch.setattr(svc, "_cross_source_transfer", boom_transfer)

        result = svc._cross_source_overwrite_with_backup(
            move, verify_hash=True,
            src_source_id="local", dst_source_id="gdrive",
        )

        assert result is None
        assert move.outcome == MigrationOutcome.FAILED
        assert "preserved per DM-5" in (move.error or "")
        assert "rate limited" in (move.error or "")

    def test_retry_hash_mismatch_preserves_backup(self, monkeypatch):
        # Lines 1657-1665: retry returns HASH_MISMATCH → outcome=HASH_MISMATCH
        # with "backup preserved" in error.
        svc, move, audit = self._prep_svc(monkeypatch)
        monkeypatch.setattr(
            svc, "_find_existing_dst_file_id_for_overwrite",
            lambda *a, **kw: "existing_dst_id",
        )
        monkeypatch.setattr(
            svc, "_attempt_cross_source_backup_rename",
            lambda *a, **kw: (True, None),
        )
        monkeypatch.setattr(
            svc, "_cross_source_transfer",
            lambda **kw: (MigrationOutcome.HASH_MISMATCH, "dst_id", "different_hash"),
        )

        result = svc._cross_source_overwrite_with_backup(
            move, verify_hash=True,
            src_source_id="local", dst_source_id="gdrive",
        )

        assert result is None
        assert move.outcome == MigrationOutcome.HASH_MISMATCH
        assert "preserved per DM-5" in (move.error or "")
        assert move.verified_xxhash == "different_hash"

    def test_retry_unexpected_skipped_collision_preserves_backup(
        self, monkeypatch,
    ):
        # Lines 1672-1683: retry SKIPPED_COLLISION (race) → outcome
        # stays SKIPPED_COLLISION with backup-preserved error.
        svc, move, audit = self._prep_svc(monkeypatch)
        monkeypatch.setattr(
            svc, "_find_existing_dst_file_id_for_overwrite",
            lambda *a, **kw: "existing_dst_id",
        )
        monkeypatch.setattr(
            svc, "_attempt_cross_source_backup_rename",
            lambda *a, **kw: (True, None),
        )
        monkeypatch.setattr(
            svc, "_cross_source_transfer",
            lambda **kw: (MigrationOutcome.SKIPPED_COLLISION, "dst_id", None),
        )

        result = svc._cross_source_overwrite_with_backup(
            move, verify_hash=True,
            src_source_id="local", dst_source_id="gdrive",
        )

        assert result is None
        assert move.outcome == MigrationOutcome.SKIPPED_COLLISION
        assert "preserved per DM-5" in (move.error or "")


# ===========================================================================
# _find_existing_dst_file_id_for_overwrite — strategy dispatch
# ===========================================================================


class TestFindExistingDstFileId:
    def test_stat_returns_value_returns_dst_path(self):
        # Strategy 1: stat hook returns non-None → return dst_path.
        pm = StubMigrationPluginManager()
        pm.set_hook(
            "curator_source_stat",
            lambda **kw: [{"size": 100, "mtime": 0}],
        )
        svc, _, _, _ = _make_cross_source_setup(pm=pm)

        result = svc._find_existing_dst_file_id_for_overwrite(
            "gdrive", "dst/file.txt",
        )
        assert result == "dst/file.txt"

    def test_stat_none_enumerate_match_by_basename_returns_file_id(self):
        # Strategy 2: stat returns None; enumerate returns FileInfo
        # iterator with a match by basename.
        from curator.models.types import FileInfo
        from datetime import datetime

        pm = StubMigrationPluginManager()
        pm.set_hook("curator_source_stat", lambda **kw: [None])

        match_info = FileInfo(
            file_id="actual_drive_file_id_12345",
            path="file.txt",  # display name
            size=100,
            mtime=datetime(2026, 5, 12),
            ctime=datetime(2026, 5, 12),
            is_directory=False,
            extras={},
        )
        pm.set_hook(
            "curator_source_enumerate",
            lambda **kw: [iter([match_info])],
        )
        svc, _, _, _ = _make_cross_source_setup(pm=pm)

        result = svc._find_existing_dst_file_id_for_overwrite(
            "gdrive", "dst/file.txt",
        )
        assert result == "actual_drive_file_id_12345"

    def test_stat_none_enumerate_none_returns_none(self):
        # Both strategies fail → None.
        pm = StubMigrationPluginManager()
        pm.set_hook("curator_source_stat", lambda **kw: [None])
        pm.set_hook("curator_source_enumerate", lambda **kw: [None])
        svc, _, _, _ = _make_cross_source_setup(pm=pm)

        result = svc._find_existing_dst_file_id_for_overwrite(
            "gdrive", "dst/file.txt",
        )
        assert result is None

    def test_enumerate_raises_returns_none(self):
        # Enumerate hook raises during invocation → defensive None.
        pm = StubMigrationPluginManager()
        pm.set_hook("curator_source_stat", lambda **kw: [None])

        def boom_enumerate(**kw):
            raise RuntimeError("API error")

        pm.set_hook("curator_source_enumerate", boom_enumerate)
        svc, _, _, _ = _make_cross_source_setup(pm=pm)

        result = svc._find_existing_dst_file_id_for_overwrite(
            "gdrive", "dst/file.txt",
        )
        assert result is None

    def test_enumerate_iteration_raises_returns_none(self):
        # Enumerate returns an iterator that raises mid-iteration →
        # defensive None.
        pm = StubMigrationPluginManager()
        pm.set_hook("curator_source_stat", lambda **kw: [None])

        def angry_iterator():
            yield  # one yield then explode
            raise RuntimeError("iteration boom")

        pm.set_hook(
            "curator_source_enumerate",
            lambda **kw: [angry_iterator()],
        )
        svc, _, _, _ = _make_cross_source_setup(pm=pm)

        result = svc._find_existing_dst_file_id_for_overwrite(
            "gdrive", "dst/file.txt",
        )
        assert result is None

    def test_stat_raises_falls_through_to_enumerate(self):
        # Lines 1492-1496: stat hook raises → caught defensively;
        # fall through to enumerate.
        pm = StubMigrationPluginManager()

        def boom_stat(**kw):
            raise RuntimeError("stat boom")

        pm.set_hook("curator_source_stat", boom_stat)
        pm.set_hook("curator_source_enumerate", lambda **kw: [None])
        svc, _, _, _ = _make_cross_source_setup(pm=pm)

        # Should not raise; should return None (stat failed, enumerate empty).
        result = svc._find_existing_dst_file_id_for_overwrite(
            "gdrive", "dst/file.txt",
        )
        assert result is None

    def test_stat_helper_propagates_exception_caught_defensively(
        self, monkeypatch,
    ):
        # Lines 1581-1582: defensive `except Exception` around the
        # stat call. _hook_first_result swallows exceptions internally,
        # so this except only fires if the helper itself raises
        # (e.g. future refactor or unexpected edge case). Test the
        # defensive boundary by monkeypatching the helper directly.
        svc, _, _, _ = _make_cross_source_setup(pm=StubMigrationPluginManager())

        def boom_helper(hook_name, **kw):
            if hook_name == "curator_source_stat":
                raise RuntimeError("helper propagated")
            return None  # enumerate path: nothing

        monkeypatch.setattr(svc, "_hook_first_result", boom_helper)
        result = svc._find_existing_dst_file_id_for_overwrite(
            "gdrive", "dst/file.txt",
        )
        # Stat raised defensively → stat_result=None → falls through to
        # enumerate → enumerate returns None → final None.
        assert result is None

    def test_enumerate_helper_propagates_exception_caught_defensively(
        self, monkeypatch,
    ):
        # Lines 1595-1596: defensive `except Exception` around the
        # enumerate call. Same rationale as the stat case above.
        svc, _, _, _ = _make_cross_source_setup(pm=StubMigrationPluginManager())

        call_count = [0]

        def helper(hook_name, **kw):
            call_count[0] += 1
            if hook_name == "curator_source_stat":
                return None
            if hook_name == "curator_source_enumerate":
                raise RuntimeError("enumerate helper propagated")
            return None

        monkeypatch.setattr(svc, "_hook_first_result", helper)
        result = svc._find_existing_dst_file_id_for_overwrite(
            "gdrive", "dst/file.txt",
        )
        # Enumerate raised defensively → caught → return None.
        assert result is None
        # Both strategies were attempted
        assert call_count[0] >= 2

    def test_enumerate_returns_iterator_with_no_matches_returns_none(self):
        # Branch 1605->1600 + Line 1613: iterator yields FileInfo items
        # whose names do NOT match target. Loop completes without
        # returning; falls through to the final `return None`.
        from curator.models.types import FileInfo
        from datetime import datetime

        pm = StubMigrationPluginManager()
        pm.set_hook("curator_source_stat", lambda **kw: [None])

        # Iterator with 2 non-matching items
        unrelated_a = FileInfo(
            file_id="id_a", path="different_name.txt",
            size=10, mtime=datetime(2026, 5, 12), ctime=datetime(2026, 5, 12),
            is_directory=False, extras={},
        )
        unrelated_b = FileInfo(
            file_id="id_b", path="another_one.txt",
            size=20, mtime=datetime(2026, 5, 12), ctime=datetime(2026, 5, 12),
            is_directory=False, extras={},
        )
        pm.set_hook(
            "curator_source_enumerate",
            lambda **kw: [iter([unrelated_a, unrelated_b])],
        )
        svc, _, _, _ = _make_cross_source_setup(pm=pm)

        result = svc._find_existing_dst_file_id_for_overwrite(
            "gdrive", "dst/file.txt",  # name=file.txt -- doesn't match either
        )
        assert result is None

    def test_find_available_suffix_exhaustion_raises(self, monkeypatch):
        # Line 1530: `_find_available_suffix` raises RuntimeError when
        # all 9999 candidate suffixes are taken. Monkeypatch Path.exists
        # to always return True so the loop completes.
        from pathlib import Path as PathClass
        from curator.services.migration import MigrationService

        # Save original exists
        orig_exists = PathClass.exists

        def always_exists(self):
            # Return True for any .curator-N candidate; otherwise fall through
            if ".curator-" in str(self):
                return True
            return orig_exists(self)

        monkeypatch.setattr(PathClass, "exists", always_exists)

        with pytest.raises(RuntimeError) as exc_info:
            MigrationService._find_available_suffix(
                PathClass("/some/path/foo.txt"),
            )
        assert "no available .curator-N" in str(exc_info.value)
        assert "[1, 9999]" in str(exc_info.value)


# ===========================================================================
# _attempt_cross_source_backup_rename — pluggy dispatch
# ===========================================================================


class TestAttemptCrossSourceBackupRename:
    def test_pm_is_none_returns_false(self):
        # Line 1751: pm is None → (False, "no plugin manager available")
        svc, _, _, _ = _make_cross_source_setup(pm=None)
        success, err = svc._attempt_cross_source_backup_rename(
            "gdrive", "existing_id", "backup_name.txt",
        )
        assert success is False
        assert "no plugin manager" in (err or "")

    def test_hook_missing_returns_false(self):
        # Lines 1752-1755: getattr on pm.hook raises AttributeError →
        # (False, "...hookspec not registered").
        pm = StubMigrationPluginManager()
        pm.remove_hook("curator_source_rename")
        svc, _, _, _ = _make_cross_source_setup(pm=pm)

        success, err = svc._attempt_cross_source_backup_rename(
            "gdrive", "existing_id", "backup_name.txt",
        )
        assert success is False
        assert "hookspec not registered" in (err or "")

    def test_hook_raises_file_exists_error(self):
        # Lines 1759-1761: rename hook raises FileExistsError →
        # (False, "backup name collision: ...").
        pm = StubMigrationPluginManager()

        def boom_rename(**kw):
            raise FileExistsError("backup already there")

        pm.set_hook("curator_source_rename", boom_rename)
        svc, _, _, _ = _make_cross_source_setup(pm=pm)

        success, err = svc._attempt_cross_source_backup_rename(
            "gdrive", "existing_id", "backup_name.txt",
        )
        assert success is False
        assert "backup name collision" in (err or "")

    def test_hook_raises_other_exception(self):
        # Lines 1762-1763: any other exception → (False, "TypeName: msg").
        pm = StubMigrationPluginManager()

        def boom_rename(**kw):
            raise RuntimeError("network failure")

        pm.set_hook("curator_source_rename", boom_rename)
        svc, _, _, _ = _make_cross_source_setup(pm=pm)

        success, err = svc._attempt_cross_source_backup_rename(
            "gdrive", "existing_id", "backup_name.txt",
        )
        assert success is False
        assert "RuntimeError" in (err or "")
        assert "network failure" in (err or "")

    def test_all_results_none_returns_false(self):
        # Lines 1764-1768: pluggy returns list with all None entries →
        # (False, "plugin does not implement curator_source_rename").
        pm = StubMigrationPluginManager()
        pm.set_hook("curator_source_rename", lambda **kw: [None, None])
        svc, _, _, _ = _make_cross_source_setup(pm=pm)

        success, err = svc._attempt_cross_source_backup_rename(
            "gdrive", "existing_id", "backup_name.txt",
        )
        assert success is False
        assert "does not implement" in (err or "")

    def test_first_non_none_result_returns_success(self):
        # Lines 1769-1770: at least one non-None result → (True, None).
        pm = StubMigrationPluginManager()
        pm.set_hook("curator_source_rename", lambda **kw: [None, "renamed_id"])
        svc, _, _, _ = _make_cross_source_setup(pm=pm)

        success, err = svc._attempt_cross_source_backup_rename(
            "gdrive", "existing_id", "backup_name.txt",
        )
        assert success is True
        assert err is None

    def test_non_list_result_wrapped(self):
        # Lines 1764-1765: pluggy can return a non-list (single value);
        # wrapped in a list first.
        pm = StubMigrationPluginManager()
        pm.set_hook("curator_source_rename", lambda **kw: "renamed_id_singleton")
        svc, _, _, _ = _make_cross_source_setup(pm=pm)

        success, err = svc._attempt_cross_source_backup_rename(
            "gdrive", "existing_id", "backup_name.txt",
        )
        assert success is True
        assert err is None
