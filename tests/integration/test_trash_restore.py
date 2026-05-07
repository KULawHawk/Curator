"""Integration tests for TrashService.

Covers:
  * Trash a real file (sends to OS Recycle Bin via vendored send2trash).
  * Snapshot of bundle memberships + flex attrs.
  * File row soft-delete after trash.
  * Audit log entry written on trash.
  * Recycle Bin lookup populates ``os_trash_location`` on Windows (Q14).
  * Round-trip restore on Windows (trash → restore → file back).
  * NotInTrashError when restoring a file that was never trashed.
"""

from __future__ import annotations

import sys
from datetime import datetime
from uuid import uuid4

import pytest

from curator.models import BundleEntity, BundleMembership, FileEntity
from curator.services import (
    NotInTrashError,
    RestoreImpossibleError,
    Send2TrashUnavailableError,
)


pytestmark = pytest.mark.integration


def _scan_one_file(make_file, services, repos, name: str, content: str = "") -> FileEntity:
    """Helper: write a file, scan it, return the persisted FileEntity."""
    p = make_file(name, content)
    services.scan.scan(source_id="local", root=str(p.parent))
    f = repos.files.find_by_path("local", str(p))
    assert f is not None
    return f


def test_trash_sends_to_os_and_soft_deletes_file_row(make_file, tmp_tree, services, repos):
    f = _scan_one_file(make_file, services, repos, "doomed.txt", "delete me")
    assert (tmp_tree / "doomed.txt").exists()

    record = services.trash.send_to_trash(
        f.curator_id, reason="manual test", actor="test_user",
    )

    # File is gone from disk (in OS Recycle Bin)
    assert not (tmp_tree / "doomed.txt").exists()
    # File row soft-deleted
    f_after = repos.files.get(f.curator_id)
    assert f_after.is_deleted
    # TrashRecord persisted
    assert record.curator_id == f.curator_id
    assert record.reason == "manual test"
    # Audit log
    audit_entries = repos.audit.query(action="trash")
    assert len(audit_entries) == 1
    assert audit_entries[0].actor == "test_user"


def test_trash_snapshots_bundle_memberships_and_flex_attrs(make_file, services, repos):
    f = _scan_one_file(make_file, services, repos, "to_trash.txt", "x")
    f.set_flex("important_flex", "remember_me")
    repos.files.update(f)

    # Add to a bundle.
    bundle = BundleEntity(bundle_type="manual", name="test_bundle")
    repos.bundles.insert(bundle)
    repos.bundles.add_membership(BundleMembership(
        bundle_id=bundle.bundle_id, curator_id=f.curator_id, role="primary",
    ))

    record = services.trash.send_to_trash(f.curator_id, reason="snapshot test")

    # Snapshots reflect pre-trash state
    assert len(record.bundle_memberships_snapshot) == 1
    assert record.bundle_memberships_snapshot[0]["bundle_id"] == str(bundle.bundle_id)
    assert record.bundle_memberships_snapshot[0]["role"] == "primary"
    assert record.file_attrs_snapshot.get("important_flex") == "remember_me"


def test_trash_records_os_trash_location_on_windows(make_file, services, repos):
    """Q14: ``os_trash_location`` is populated after the Recycle Bin lookup.

    Skipped on non-Windows where the lookup short-circuits to None.
    """
    if sys.platform != "win32":
        pytest.skip("Recycle Bin lookup is Windows-only")

    f = _scan_one_file(make_file, services, repos, "q14_target.txt", "q14")
    record = services.trash.send_to_trash(f.curator_id, reason="q14 test")

    assert record.os_trash_location is not None, (
        "Q14 regression: os_trash_location should be populated after "
        "the recycle-bin lookup"
    )
    # The recorded location is the $R companion file inside the bin.
    assert "$R" in record.os_trash_location
    assert "$Recycle.Bin" in record.os_trash_location


def test_restore_round_trip_on_windows(make_file, tmp_tree, services, repos):
    """Q14 round-trip: trash a file, restore it, verify it's back on disk."""
    if sys.platform != "win32":
        pytest.skip("OS-trash restore is Windows-only in Phase Alpha")

    f = _scan_one_file(make_file, services, repos, "round_trip.txt", "q14 round-trip")
    original_path = f.source_path

    services.trash.send_to_trash(f.curator_id, reason="round-trip test")
    assert not (tmp_tree / "round_trip.txt").exists()

    restored = services.trash.restore(f.curator_id)

    # File is back on disk…
    assert (tmp_tree / "round_trip.txt").exists()
    # …the file row is reactivated…
    assert restored.is_deleted is False
    assert restored.source_path == original_path
    # …and the trash record was deleted.
    assert services.trash.is_in_trash(f.curator_id) is False


def test_restore_raises_for_unknown_curator_id(services, repos):
    bogus = uuid4()
    with pytest.raises(NotInTrashError):
        services.trash.restore(bogus)


def test_is_in_trash_reflects_state(make_file, services, repos):
    f = _scan_one_file(make_file, services, repos, "x.txt", "x")
    assert services.trash.is_in_trash(f.curator_id) is False

    services.trash.send_to_trash(f.curator_id, reason="test")
    assert services.trash.is_in_trash(f.curator_id) is True


def test_list_trashed_returns_record(make_file, services, repos):
    a = _scan_one_file(make_file, services, repos, "a.txt", "a")
    b = _scan_one_file(make_file, services, repos, "b.txt", "b")
    services.trash.send_to_trash(a.curator_id, reason="r1", actor="alice")
    services.trash.send_to_trash(b.curator_id, reason="r2", actor="bob")

    all_trashed = services.trash.list_trashed()
    assert len(all_trashed) == 2
    by_alice = services.trash.list_trashed(actor="alice")
    assert len(by_alice) == 1
    assert by_alice[0].reason == "r1"
