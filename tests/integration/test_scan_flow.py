"""Integration test — full ScanService flow against a temp file tree.

Promoted from the Step 5 8-step end-to-end smoke test.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from curator.models import LineageKind


pytestmark = pytest.mark.integration


def test_scan_against_real_tree_dedups_and_classifies(make_file, tmp_tree, services, repos):
    """Single comprehensive integration test covering the scan flow."""

    # Build a small mixed tree.
    make_file("data/report.txt", "first draft")
    make_file("data/report_copy.txt", "first draft")  # exact duplicate
    make_file("data/notes.md", "# Notes\nsome content here")
    make_file("data/script.py", "print('hi')\n")
    make_file("data/.hidden", "hidden")
    make_file("data/skip_me/should_skip.txt", "ignored")

    report = services.scan.scan(
        source_id="local",
        root=str(tmp_tree / "data"),
        options={"ignore": ["skip_me"]},
    )

    # Phase 1: enumeration / persistence
    assert report.files_seen == 5  # not 6 — skip_me was ignored
    assert report.files_new == 5
    assert report.errors == 0

    # Phase 2: hashing
    assert report.files_hashed == 5
    assert report.cache_hits == 0
    assert report.bytes_read > 0

    # Phase 3: classifications + lineage
    assert report.classifications_assigned >= 4  # all text-ish files
    assert report.lineage_edges_created >= 1     # report.txt + report_copy.txt

    # Persisted state
    assert repos.files.count() == 5
    dup_edges = repos.lineage.list_by_kind(LineageKind.DUPLICATE)
    assert len(dup_edges) >= 1
    # Audit log has start + complete
    audit_entries = repos.audit.query()
    actions = {e.action for e in audit_entries}
    assert "scan.start" in actions
    assert "scan.complete" in actions


def test_rescan_uses_cache(make_file, tmp_tree, services, repos):
    """Second scan should hit the cache and not re-hash unchanged files."""
    make_file("a.py", "print('hi')")
    make_file("b.py", "print('bye')")

    # First scan — no cache.
    r1 = services.scan.scan(source_id="local", root=str(tmp_tree))
    assert r1.files_hashed == 2
    assert r1.cache_hits == 0

    # Second scan — everything cached.
    r2 = services.scan.scan(source_id="local", root=str(tmp_tree))
    assert r2.files_hashed == 0
    assert r2.cache_hits == 2
    assert r2.bytes_read == 0
    assert r2.files_unchanged == 2
    assert r2.files_new == 0


def test_scan_handles_empty_dir(tmp_tree, services):
    """A directory with no files should complete cleanly with 0 metrics."""
    report = services.scan.scan(source_id="local", root=str(tmp_tree))
    assert report.files_seen == 0
    assert report.files_hashed == 0
    assert report.errors == 0


def test_scan_creates_source_config_on_first_use(tmp_tree, services, repos):
    """ScanService auto-creates a SourceConfig when one doesn't exist yet."""
    assert repos.sources.get("local") is None
    services.scan.scan(source_id="local", root=str(tmp_tree))
    cfg = repos.sources.get("local")
    assert cfg is not None
    assert cfg.source_type == "local"


def test_scan_rejects_unknown_source_id(tmp_tree, services):
    """No source plugin matches → RuntimeError, not silent miss."""
    with pytest.raises(RuntimeError, match="No source plugin"):
        services.scan.scan(source_id="nonexistent_source_42", root=str(tmp_tree))


def test_scan_changed_file_invalidates_hashes_and_re_hashes(make_file, tmp_tree, services, repos):
    """When a file's mtime+size changes, the scan re-hashes it."""
    p = make_file("a.py", "v1\n")
    services.scan.scan(source_id="local", root=str(tmp_tree))
    f1 = repos.files.find_by_path("local", str(p))
    h1 = f1.xxhash3_128

    # Change content.
    p.write_text("v2 — much longer content now\n")

    r2 = services.scan.scan(source_id="local", root=str(tmp_tree))
    assert r2.files_updated == 1
    assert r2.files_hashed == 1
    f2 = repos.files.find_by_path("local", str(p))
    assert f2.xxhash3_128 != h1
