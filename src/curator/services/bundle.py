"""Bundle service — manage groups of related files.

DESIGN.md §9.

Bundles are sets of files that belong together (a clinical assessment
package, a course materials set, a project deliverable). This service
provides:

  * ``create_manual`` — explicit user-created bundle.
  * ``propose_auto`` — run plugin proposers, return suggestions.
  * ``confirm_proposal`` — materialize a plugin proposal as a real bundle.
  * ``add_member`` / ``remove_member`` — membership management.
  * ``members`` — get :class:`FileEntity` objects in a bundle.
  * ``cross_source_check`` — verify all members are still reachable.

Cross-source bundles (Phase Beta+): a bundle can have members from
``local`` + ``gdrive`` + ``onedrive``. The bundle's tables only store
``curator_id``; per-member source resolution is via
``FileEntity.source_id`` looked up by :class:`FileRepository`.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pluggy
from loguru import logger

from curator.models.bundle import BundleEntity, BundleMembership
from curator.models.file import FileEntity
from curator.models.results import BundleProposal
from curator.storage.repositories.bundle_repo import BundleRepository
from curator.storage.repositories.file_repo import FileRepository


class BundleService:
    """Bundle CRUD + plugin-driven proposal workflow."""

    def __init__(
        self,
        plugin_manager: pluggy.PluginManager,
        bundle_repo: BundleRepository,
        file_repo: FileRepository,
    ):
        self.pm = plugin_manager
        self.bundles = bundle_repo
        self.files = file_repo

    # ------------------------------------------------------------------
    # Manual bundles
    # ------------------------------------------------------------------

    def create_manual(
        self,
        name: str,
        member_ids: list[UUID],
        *,
        description: str | None = None,
        primary_id: UUID | None = None,
    ) -> BundleEntity:
        """Create a user-defined bundle with the given members.

        Args:
            name: bundle name (not unique; same name allowed).
            member_ids: ordered list of curator_ids to add as members.
            description: optional longer text.
            primary_id: if set, this curator_id is the bundle's primary
                        member (role='primary'). Otherwise the first id
                        in ``member_ids`` becomes primary.

        Raises:
            ValueError: if member_ids is empty or primary_id isn't in members.
        """
        if not member_ids:
            raise ValueError("Bundle must have at least one member")
        if primary_id is not None and primary_id not in member_ids:
            raise ValueError("primary_id must be in member_ids")

        bundle = BundleEntity(
            bundle_type="manual",
            name=name,
            description=description,
            confidence=1.0,
        )
        self.bundles.insert(bundle)

        primary = primary_id if primary_id is not None else member_ids[0]
        for cid in member_ids:
            self.bundles.add_membership(
                BundleMembership(
                    bundle_id=bundle.bundle_id,
                    curator_id=cid,
                    role=("primary" if cid == primary else "member"),
                    confidence=1.0,
                )
            )

        return bundle

    def add_member(
        self,
        bundle_id: UUID,
        curator_id: UUID,
        *,
        role: str = "member",
        confidence: float = 1.0,
    ) -> BundleMembership:
        """Add a single file to an existing bundle."""
        membership = BundleMembership(
            bundle_id=bundle_id,
            curator_id=curator_id,
            role=role,
            confidence=confidence,
        )
        self.bundles.add_membership(membership)
        return membership

    def remove_member(self, bundle_id: UUID, curator_id: UUID) -> None:
        """Remove a file from a bundle. The file itself is not affected."""
        self.bundles.remove_membership(bundle_id, curator_id)

    def dissolve(self, bundle_id: UUID) -> None:
        """Delete a bundle. Memberships cascade-delete; member files are kept."""
        self.bundles.delete(bundle_id)

    # ------------------------------------------------------------------
    # Plugin proposals
    # ------------------------------------------------------------------

    def propose_auto(self, files: list[FileEntity]) -> list[BundleProposal]:
        """Run plugin proposers on a candidate set, return all proposals.

        Empty list means no plugin recognized a pattern. Each proposal
        carries a confidence; the caller (UI / rules engine / user)
        decides which to materialize via :meth:`confirm_proposal`.
        """
        if not files:
            return []
        results = self.pm.hook.curator_propose_bundle(files=files)
        return [r for r in results if r is not None]

    def confirm_proposal(self, proposal: BundleProposal) -> BundleEntity:
        """Materialize a :class:`BundleProposal` into a stored bundle."""
        bundle = BundleEntity(
            bundle_type=f"plugin:{proposal.proposer}",
            name=proposal.name,
            description=proposal.description,
            confidence=proposal.confidence,
        )
        self.bundles.insert(bundle)
        for member in proposal.members:
            self.bundles.add_membership(
                BundleMembership(
                    bundle_id=bundle.bundle_id,
                    curator_id=member.curator_id,
                    role=member.role,
                    confidence=member.confidence,
                )
            )
        return bundle

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get(self, bundle_id: UUID) -> BundleEntity | None:
        return self.bundles.get(bundle_id)

    def members(self, bundle_id: UUID) -> list[FileEntity]:
        """Return :class:`FileEntity` for every member of this bundle.

        Members that no longer have a file row (e.g. hard-deleted) are
        silently skipped — use :meth:`raw_memberships` if you want to
        see those.
        """
        memberships = self.bundles.get_memberships(bundle_id)
        out: list[FileEntity] = []
        for m in memberships:
            f = self.files.get(m.curator_id)
            if f is not None:
                out.append(f)
        return out

    def raw_memberships(self, bundle_id: UUID) -> list[BundleMembership]:
        """Return the raw membership rows (including those with missing files)."""
        return self.bundles.get_memberships(bundle_id)

    def member_count(self, bundle_id: UUID) -> int:
        return self.bundles.member_count(bundle_id)

    def list_all(self, *, bundle_type: str | None = None) -> list[BundleEntity]:
        return self.bundles.list_all(bundle_type=bundle_type)

    def find_by_name(self, name: str) -> list[BundleEntity]:
        return self.bundles.find_by_name(name)

    # ------------------------------------------------------------------
    # Audit / health
    # ------------------------------------------------------------------

    def cross_source_check(self, bundle_id: UUID) -> dict[str, Any]:
        """Verify every member is still present in its source.

        Returns a dict with keys:
            ``total``     — member count
            ``reachable`` — count of members where source.stat() succeeded
            ``missing``   — list of curator_id strings whose source.stat() failed

        Used by the CLI ``curator doctor`` command and by the rules
        engine before destructive actions.
        """
        members = self.bundles.get_memberships(bundle_id)
        result: dict[str, Any] = {
            "total": len(members),
            "reachable": 0,
            "missing": [],
        }
        for membership in members:
            f = self.files.get(membership.curator_id)
            if f is None:
                result["missing"].append(str(membership.curator_id))
                continue
            try:
                stat_results = self.pm.hook.curator_source_stat(
                    source_id=f.source_id, file_id=f.source_path,
                )
                stat = next((s for s in stat_results if s is not None), None)
            except Exception as e:  # pragma: no cover — defensive
                logger.warning(
                    "source.stat failed for {p}: {e}",
                    p=f.source_path, e=e,
                )
                stat = None

            if stat is not None:
                result["reachable"] += 1
            else:
                result["missing"].append(str(membership.curator_id))

        return result
