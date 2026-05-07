"""Model invariant tests.

These cover the three-tier attribute system (fixed / flex / computed) on
:class:`CuratorEntity` subclasses, plus a handful of model-specific
invariants that we don't want to lose without a deliberate decision.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import pytest

from curator.models import (
    AuditEntry,
    BundleEntity,
    BundleMembership,
    FileEntity,
    LineageEdge,
    LineageKind,
    SourceConfig,
    TrashRecord,
)


# ---------------------------------------------------------------------------
# FileEntity
# ---------------------------------------------------------------------------

class TestFileEntity:
    def test_curator_id_is_uuid_assigned_automatically(self):
        f = FileEntity(
            source_id="local",
            source_path="/tmp/x",
            size=10,
            mtime=datetime.utcnow(),
        )
        assert isinstance(f.curator_id, UUID)

    def test_distinct_files_get_distinct_ids(self):
        a = FileEntity(source_id="local", source_path="/tmp/a", size=1, mtime=datetime.utcnow())
        b = FileEntity(source_id="local", source_path="/tmp/b", size=1, mtime=datetime.utcnow())
        assert a.curator_id != b.curator_id

    def test_flex_attrs_round_trip(self):
        f = FileEntity(
            source_id="local",
            source_path="/tmp/x",
            size=1,
            mtime=datetime.utcnow(),
        )
        f.set_flex("topic", "stats")
        f.set_flex("priority", 5)
        assert f.flex.get("topic") == "stats"
        assert f.flex.get("priority") == 5

    def test_is_deleted_reflects_deleted_at(self):
        f = FileEntity(source_id="local", source_path="/tmp/x", size=1, mtime=datetime.utcnow())
        assert not f.is_deleted
        f.deleted_at = datetime.utcnow()
        assert f.is_deleted

    def test_has_full_hash_only_when_xxhash_present(self):
        f = FileEntity(source_id="local", source_path="/tmp/x", size=1, mtime=datetime.utcnow())
        assert not f.has_full_hash
        f.xxhash3_128 = "deadbeef" * 4
        assert f.has_full_hash


# ---------------------------------------------------------------------------
# LineageEdge / LineageKind
# ---------------------------------------------------------------------------

class TestLineageEdge:
    def test_lineage_kind_values_are_stable(self):
        # These string values are persisted in the DB; changing them
        # would break existing indexes. Pin them here.
        assert LineageKind.DUPLICATE.value == "duplicate"
        assert LineageKind.NEAR_DUPLICATE.value == "near_duplicate"
        assert LineageKind.DERIVED_FROM.value == "derived_from"
        assert LineageKind.VERSION_OF.value == "version_of"
        assert LineageKind.REFERENCED_BY.value == "referenced_by"
        assert LineageKind.SAME_LOGICAL_FILE.value == "same_logical_file"

    def test_edge_id_assigned_automatically(self):
        e = LineageEdge(
            from_curator_id=UUID(int=0),
            to_curator_id=UUID(int=1),
            edge_kind=LineageKind.DUPLICATE,
            confidence=1.0,
            detected_by="test",
        )
        assert isinstance(e.edge_id, UUID)


# ---------------------------------------------------------------------------
# BundleEntity / BundleMembership
# ---------------------------------------------------------------------------

class TestBundle:
    def test_bundle_id_assigned_automatically(self):
        b = BundleEntity(bundle_type="manual", name="test")
        assert isinstance(b.bundle_id, UUID)

    def test_membership_role_defaults_to_member(self):
        m = BundleMembership(bundle_id=UUID(int=0), curator_id=UUID(int=1))
        assert m.role == "member"


# ---------------------------------------------------------------------------
# TrashRecord
# ---------------------------------------------------------------------------

class TestTrashRecord:
    def test_snapshot_defaults_are_safe(self):
        r = TrashRecord(
            curator_id=UUID(int=0),
            original_source_id="local",
            original_path="/tmp/x",
            trashed_by="user",
            reason="manual",
        )
        assert r.bundle_memberships_snapshot == []
        assert r.file_attrs_snapshot == {}
        assert r.os_trash_location is None


# ---------------------------------------------------------------------------
# AuditEntry
# ---------------------------------------------------------------------------

class TestAuditEntry:
    def test_details_default_is_empty_dict(self):
        e = AuditEntry(actor="user", action="test")
        assert e.details == {}

    def test_audit_id_defaults_to_minus_one(self):
        # The DB assigns audit_id via AUTOINCREMENT after INSERT; the
        # in-memory entity uses -1 as a "not yet persisted" sentinel.
        e = AuditEntry(actor="user", action="test")
        assert e.audit_id == -1


# ---------------------------------------------------------------------------
# SourceConfig
# ---------------------------------------------------------------------------

class TestSourceConfig:
    def test_default_enabled(self):
        s = SourceConfig(source_id="local", source_type="local")
        assert s.enabled is True

    def test_config_default_is_empty_dict(self):
        s = SourceConfig(source_id="local", source_type="local")
        assert s.config == {}
