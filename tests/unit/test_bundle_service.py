"""Focused unit tests for BundleService (services/bundle.py).

Existing integration tests (test_cli_bundles.py + test_gui_bundle_editor.py)
cover happy paths at 53%. This file targets the 47% gap:

* create_manual error branches (empty members, invalid primary_id)
* propose_auto + confirm_proposal (plugin-driven flow not in integration tests)
* members() and raw_memberships() resolution
* cross_source_check() with reachable + missing + stat-missing combinations
* Read methods: get, member_count, list_all, find_by_name
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable
from uuid import UUID, uuid4

import pytest

from curator.models.bundle import BundleEntity, BundleMembership
from curator.models.file import FileEntity
from curator.models.results import BundleProposal, BundleProposalMember
from curator.services.bundle import BundleService


# ===========================================================================
# Stubs
# ===========================================================================


class StubBundleRepository:
    """Minimal BundleRepository for unit tests."""

    def __init__(self):
        self.bundles: dict[UUID, BundleEntity] = {}
        self.memberships: dict[UUID, list[BundleMembership]] = {}
        self.inserted: list[BundleEntity] = []
        self.deleted: list[UUID] = []
        self.removed_memberships: list[tuple[UUID, UUID]] = []

    def insert(self, bundle: BundleEntity) -> None:
        self.bundles[bundle.bundle_id] = bundle
        self.memberships.setdefault(bundle.bundle_id, [])
        self.inserted.append(bundle)

    def add_membership(self, m: BundleMembership) -> None:
        self.memberships.setdefault(m.bundle_id, []).append(m)

    def remove_membership(self, bundle_id: UUID, curator_id: UUID) -> None:
        self.removed_memberships.append((bundle_id, curator_id))
        self.memberships[bundle_id] = [
            m for m in self.memberships.get(bundle_id, [])
            if m.curator_id != curator_id
        ]

    def delete(self, bundle_id: UUID) -> None:
        self.deleted.append(bundle_id)
        self.bundles.pop(bundle_id, None)
        self.memberships.pop(bundle_id, None)

    def get(self, bundle_id: UUID) -> BundleEntity | None:
        return self.bundles.get(bundle_id)

    def get_memberships(self, bundle_id: UUID) -> list[BundleMembership]:
        return list(self.memberships.get(bundle_id, []))

    def member_count(self, bundle_id: UUID) -> int:
        return len(self.memberships.get(bundle_id, []))

    def list_all(self, *, bundle_type: str | None = None) -> list[BundleEntity]:
        if bundle_type is None:
            return list(self.bundles.values())
        return [b for b in self.bundles.values() if b.bundle_type == bundle_type]

    def find_by_name(self, name: str) -> list[BundleEntity]:
        return [b for b in self.bundles.values() if b.name == name]


class StubFileRepository:
    """Minimal FileRepository — only `get` is used by BundleService."""

    def __init__(self, files: list[FileEntity] | None = None):
        self._files: dict[UUID, FileEntity] = {
            f.curator_id: f for f in (files or [])
        }

    def get(self, curator_id: UUID) -> FileEntity | None:
        return self._files.get(curator_id)


# Pluggy stubs

@dataclass
class StubHookCaller:
    impl: Callable[..., list[Any]] = field(default_factory=lambda: lambda **_: [])

    def __call__(self, **kwargs) -> list[Any]:
        return self.impl(**kwargs)


@dataclass
class StubHooks:
    curator_propose_bundle: StubHookCaller = field(default_factory=StubHookCaller)
    curator_source_stat: StubHookCaller = field(default_factory=StubHookCaller)


@dataclass
class StubPluginManager:
    hook: StubHooks = field(default_factory=StubHooks)

    def set_propose(self, fn):
        self.hook.curator_propose_bundle = StubHookCaller(impl=fn)

    def set_source_stat(self, fn):
        self.hook.curator_source_stat = StubHookCaller(impl=fn)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_service(
    *,
    bundle_repo: StubBundleRepository | None = None,
    file_repo: StubFileRepository | None = None,
    plugin_manager: StubPluginManager | None = None,
):
    return BundleService(
        plugin_manager=plugin_manager or StubPluginManager(),
        bundle_repo=bundle_repo or StubBundleRepository(),
        file_repo=file_repo or StubFileRepository(),
    )


def make_file_entity(
    *,
    curator_id: UUID | None = None,
    source_id: str = "local",
    source_path: str = "/data/x.txt",
) -> FileEntity:
    from datetime import datetime
    return FileEntity(
        curator_id=curator_id or uuid4(),
        source_id=source_id,
        source_path=source_path,
        size=100,
        mtime=datetime(2026, 5, 12),
    )


# ===========================================================================
# create_manual
# ===========================================================================


class TestCreateManual:
    def test_creates_bundle_with_single_member(self):
        repo = StubBundleRepository()
        svc = make_service(bundle_repo=repo)
        cid = uuid4()
        bundle = svc.create_manual(name="My Bundle", member_ids=[cid])
        assert bundle.name == "My Bundle"
        assert bundle.bundle_type == "manual"
        assert bundle.confidence == 1.0
        assert len(repo.memberships[bundle.bundle_id]) == 1
        # First member becomes primary by default
        assert repo.memberships[bundle.bundle_id][0].role == "primary"

    def test_creates_bundle_with_multiple_members(self):
        repo = StubBundleRepository()
        svc = make_service(bundle_repo=repo)
        cid_a, cid_b, cid_c = uuid4(), uuid4(), uuid4()
        bundle = svc.create_manual(
            name="Multi", member_ids=[cid_a, cid_b, cid_c],
        )
        members = repo.memberships[bundle.bundle_id]
        assert len(members) == 3
        # First member is primary, rest are members
        roles = {m.curator_id: m.role for m in members}
        assert roles[cid_a] == "primary"
        assert roles[cid_b] == "member"
        assert roles[cid_c] == "member"

    def test_creates_bundle_with_explicit_primary(self):
        repo = StubBundleRepository()
        svc = make_service(bundle_repo=repo)
        cid_a, cid_b = uuid4(), uuid4()
        bundle = svc.create_manual(
            name="Test", member_ids=[cid_a, cid_b], primary_id=cid_b,
        )
        members = repo.memberships[bundle.bundle_id]
        roles = {m.curator_id: m.role for m in members}
        # cid_b becomes primary (explicitly set), cid_a is member
        assert roles[cid_b] == "primary"
        assert roles[cid_a] == "member"

    def test_creates_bundle_with_description(self):
        svc = make_service()
        bundle = svc.create_manual(
            name="With Desc", member_ids=[uuid4()],
            description="A bundle with a description",
        )
        assert bundle.description == "A bundle with a description"

    def test_empty_member_ids_raises(self):
        svc = make_service()
        with pytest.raises(ValueError, match="at least one member"):
            svc.create_manual(name="Empty", member_ids=[])

    def test_primary_id_not_in_members_raises(self):
        # Line 78: ValueError when primary_id is not in member_ids.
        svc = make_service()
        cid_in = uuid4()
        cid_out = uuid4()  # not in member_ids
        with pytest.raises(ValueError, match="primary_id must be in member_ids"):
            svc.create_manual(
                name="Bad Primary",
                member_ids=[cid_in],
                primary_id=cid_out,
            )


# ===========================================================================
# add_member / remove_member / dissolve
# ===========================================================================


class TestMembershipManagement:
    def test_add_member(self):
        repo = StubBundleRepository()
        svc = make_service(bundle_repo=repo)
        bundle = svc.create_manual(name="X", member_ids=[uuid4()])
        new_cid = uuid4()
        membership = svc.add_member(bundle.bundle_id, new_cid)
        assert membership.bundle_id == bundle.bundle_id
        assert membership.curator_id == new_cid
        assert membership.role == "member"  # default
        # Bundle now has 2 members
        assert len(repo.memberships[bundle.bundle_id]) == 2

    def test_add_member_with_custom_role_and_confidence(self):
        repo = StubBundleRepository()
        svc = make_service(bundle_repo=repo)
        bundle = svc.create_manual(name="X", member_ids=[uuid4()])
        cid = uuid4()
        membership = svc.add_member(
            bundle.bundle_id, cid, role="primary", confidence=0.8,
        )
        assert membership.role == "primary"
        assert membership.confidence == 0.8

    def test_remove_member(self):
        repo = StubBundleRepository()
        svc = make_service(bundle_repo=repo)
        cid_a, cid_b = uuid4(), uuid4()
        bundle = svc.create_manual(name="X", member_ids=[cid_a, cid_b])
        svc.remove_member(bundle.bundle_id, cid_a)
        remaining = [m.curator_id for m in repo.memberships[bundle.bundle_id]]
        assert cid_a not in remaining
        assert cid_b in remaining

    def test_dissolve(self):
        repo = StubBundleRepository()
        svc = make_service(bundle_repo=repo)
        bundle = svc.create_manual(name="X", member_ids=[uuid4()])
        svc.dissolve(bundle.bundle_id)
        assert bundle.bundle_id in repo.deleted
        assert bundle.bundle_id not in repo.bundles


# ===========================================================================
# propose_auto (lines 138-141)
# ===========================================================================


class TestProposeAuto:
    def test_empty_files_returns_empty(self):
        # Line 138-139: empty file list short-circuits without invoking plugins.
        svc = make_service()
        result = svc.propose_auto([])
        assert result == []

    def test_no_proposers_returns_empty(self):
        # Plugin hook returns nothing useful (just None values).
        pm = StubPluginManager()
        pm.set_propose(lambda **_: [None, None])
        svc = make_service(plugin_manager=pm)
        f = make_file_entity()
        result = svc.propose_auto([f])
        assert result == []

    def test_collects_non_none_proposals(self):
        # Line 141: filters out None results from hook.
        pm = StubPluginManager()
        f1 = make_file_entity()
        f2 = make_file_entity()
        proposal = BundleProposal(
            proposer="test-plugin",
            name="Suggested Bundle",
            confidence=0.9,
            members=[
                BundleProposalMember(curator_id=f1.curator_id),
                BundleProposalMember(curator_id=f2.curator_id),
            ],
        )
        pm.set_propose(lambda **_: [None, proposal, None])
        svc = make_service(plugin_manager=pm)
        result = svc.propose_auto([f1, f2])
        assert len(result) == 1
        assert result[0].proposer == "test-plugin"


# ===========================================================================
# confirm_proposal (lines 145-161)
# ===========================================================================


class TestConfirmProposal:
    def test_materializes_proposal_into_bundle(self):
        repo = StubBundleRepository()
        svc = make_service(bundle_repo=repo)
        cid_a, cid_b = uuid4(), uuid4()
        proposal = BundleProposal(
            proposer="dup-detector",
            name="Duplicate set",
            description="Found by content similarity",
            confidence=0.95,
            members=[
                BundleProposalMember(curator_id=cid_a, role="primary", confidence=1.0),
                BundleProposalMember(curator_id=cid_b, role="member", confidence=0.9),
            ],
        )
        bundle = svc.confirm_proposal(proposal)
        assert bundle.name == "Duplicate set"
        assert bundle.description == "Found by content similarity"
        assert bundle.confidence == 0.95
        assert bundle.bundle_type == "plugin:dup-detector"
        # Memberships were created with proposed roles + confidences
        members = repo.memberships[bundle.bundle_id]
        assert len(members) == 2
        by_cid = {m.curator_id: m for m in members}
        assert by_cid[cid_a].role == "primary"
        assert by_cid[cid_a].confidence == 1.0
        assert by_cid[cid_b].role == "member"
        assert by_cid[cid_b].confidence == 0.9


# ===========================================================================
# members + raw_memberships (lines 177-183)
# ===========================================================================


class TestMembers:
    def test_resolves_curator_ids_to_file_entities(self):
        # Lines 177-183: members() resolves each membership's curator_id
        # to a FileEntity via files.get(). Missing files are skipped.
        f1 = make_file_entity()
        f2 = make_file_entity()
        file_repo = StubFileRepository(files=[f1, f2])
        repo = StubBundleRepository()
        svc = make_service(bundle_repo=repo, file_repo=file_repo)
        bundle = svc.create_manual(
            name="Test",
            member_ids=[f1.curator_id, f2.curator_id],
        )
        result = svc.members(bundle.bundle_id)
        assert len(result) == 2
        cids = {f.curator_id for f in result}
        assert cids == {f1.curator_id, f2.curator_id}

    def test_skips_files_with_missing_row(self):
        # If a curator_id no longer has a FileEntity row (e.g. hard-deleted),
        # members() silently skips it. raw_memberships() still returns it.
        f1 = make_file_entity()
        ghost_cid = uuid4()  # not in file_repo
        file_repo = StubFileRepository(files=[f1])
        svc = make_service(file_repo=file_repo)
        bundle = svc.create_manual(
            name="Test",
            member_ids=[f1.curator_id, ghost_cid],
        )
        result = svc.members(bundle.bundle_id)
        # Only f1 came back; ghost was dropped
        assert len(result) == 1
        assert result[0].curator_id == f1.curator_id

    def test_raw_memberships_includes_missing(self):
        f1 = make_file_entity()
        ghost_cid = uuid4()
        file_repo = StubFileRepository(files=[f1])
        svc = make_service(file_repo=file_repo)
        bundle = svc.create_manual(
            name="Test",
            member_ids=[f1.curator_id, ghost_cid],
        )
        raw = svc.raw_memberships(bundle.bundle_id)
        # Both memberships present even though ghost_cid has no file
        assert len(raw) == 2


# ===========================================================================
# Read methods
# ===========================================================================


class TestReads:
    def test_get_returns_bundle(self):
        svc = make_service()
        bundle = svc.create_manual(name="X", member_ids=[uuid4()])
        result = svc.get(bundle.bundle_id)
        assert result is not None
        assert result.bundle_id == bundle.bundle_id

    def test_get_returns_none_for_unknown_id(self):
        svc = make_service()
        assert svc.get(uuid4()) is None

    def test_member_count(self):
        svc = make_service()
        cids = [uuid4(), uuid4(), uuid4()]
        bundle = svc.create_manual(name="Three", member_ids=cids)
        assert svc.member_count(bundle.bundle_id) == 3

    def test_list_all_returns_every_bundle(self):
        svc = make_service()
        a = svc.create_manual(name="A", member_ids=[uuid4()])
        b = svc.create_manual(name="B", member_ids=[uuid4()])
        c = svc.create_manual(name="C", member_ids=[uuid4()])
        all_bundles = svc.list_all()
        ids = {x.bundle_id for x in all_bundles}
        assert ids == {a.bundle_id, b.bundle_id, c.bundle_id}

    def test_list_all_filters_by_bundle_type(self):
        repo = StubBundleRepository()
        svc = make_service(bundle_repo=repo)
        manual = svc.create_manual(name="M", member_ids=[uuid4()])
        # Inject a plugin-bundle via confirm_proposal
        proposal = BundleProposal(
            proposer="x",
            name="P",
            confidence=1.0,
            members=[BundleProposalMember(curator_id=uuid4())],
        )
        plugin_bundle = svc.confirm_proposal(proposal)
        manuals = svc.list_all(bundle_type="manual")
        plugins = svc.list_all(bundle_type="plugin:x")
        assert {m.bundle_id for m in manuals} == {manual.bundle_id}
        assert {p.bundle_id for p in plugins} == {plugin_bundle.bundle_id}

    def test_find_by_name(self):
        svc = make_service()
        a = svc.create_manual(name="Reports", member_ids=[uuid4()])
        b = svc.create_manual(name="Reports", member_ids=[uuid4()])  # same name
        svc.create_manual(name="Other", member_ids=[uuid4()])
        result = svc.find_by_name("Reports")
        assert len(result) == 2
        assert {x.bundle_id for x in result} == {a.bundle_id, b.bundle_id}


# ===========================================================================
# cross_source_check (lines 213-241)
# ===========================================================================


class TestCrossSourceCheck:
    def test_all_reachable(self):
        # Every member has a FileEntity AND source.stat returns non-None.
        f1 = make_file_entity()
        f2 = make_file_entity()
        file_repo = StubFileRepository(files=[f1, f2])
        pm = StubPluginManager()
        # source.stat returns a non-None object for any input
        pm.set_source_stat(lambda **_: [{"size": 100, "mtime": 0}])
        svc = make_service(file_repo=file_repo, plugin_manager=pm)
        bundle = svc.create_manual(
            name="Bundle",
            member_ids=[f1.curator_id, f2.curator_id],
        )
        result = svc.cross_source_check(bundle.bundle_id)
        assert result["total"] == 2
        assert result["reachable"] == 2
        assert result["missing"] == []

    def test_missing_file_entity_marks_missing(self):
        # Membership exists but FileEntity is gone (hard-deleted).
        # Lines 220-223: f is None → record cid as missing, continue.
        f1 = make_file_entity()
        ghost_cid = uuid4()
        file_repo = StubFileRepository(files=[f1])  # ghost_cid absent
        pm = StubPluginManager()
        pm.set_source_stat(lambda **_: [{"size": 100}])
        svc = make_service(file_repo=file_repo, plugin_manager=pm)
        bundle = svc.create_manual(
            name="Bundle",
            member_ids=[f1.curator_id, ghost_cid],
        )
        result = svc.cross_source_check(bundle.bundle_id)
        assert result["total"] == 2
        assert result["reachable"] == 1
        assert str(ghost_cid) in result["missing"]

    def test_stat_returns_none_marks_missing(self):
        # File entity exists, but source.stat returns no usable result.
        # Lines 236-239: stat is None → record cid as missing.
        f1 = make_file_entity()
        file_repo = StubFileRepository(files=[f1])
        pm = StubPluginManager()
        # source.stat returns [None] (no plugin claimed the source)
        pm.set_source_stat(lambda **_: [None])
        svc = make_service(file_repo=file_repo, plugin_manager=pm)
        bundle = svc.create_manual(
            name="Bundle",
            member_ids=[f1.curator_id],
        )
        result = svc.cross_source_check(bundle.bundle_id)
        assert result["total"] == 1
        assert result["reachable"] == 0
        assert str(f1.curator_id) in result["missing"]

    def test_empty_bundle_returns_zero_totals(self):
        svc = make_service()
        # Create a bundle directly via repo (create_manual requires members)
        repo = StubBundleRepository()
        bundle = BundleEntity(
            bundle_type="manual", name="Empty", confidence=1.0,
        )
        repo.insert(bundle)
        svc = make_service(bundle_repo=repo)
        result = svc.cross_source_check(bundle.bundle_id)
        assert result["total"] == 0
        assert result["reachable"] == 0
        assert result["missing"] == []
