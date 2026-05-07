"""Unit tests for CleanupService (Phase Gamma F6).

Covers:
    * find_empty_dirs (cascade, system-junk handling, strict mode)
    * find_broken_symlinks (Windows-skip-aware)
    * find_junk_files (default patterns + custom patterns + globs)
    * apply (rmdir / unlink / send2trash branches, safety REFUSE,
      missing target, audit hooks, error handling)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from curator.services.cleanup import (
    DEFAULT_JUNK_PATTERNS,
    SYSTEM_JUNK_NAMES,
    ApplyOutcome,
    CleanupFinding,
    CleanupKind,
    CleanupReport,
    CleanupService,
)
from curator.services.safety import (
    SafetyConcern,
    SafetyLevel,
    SafetyReport,
    SafetyService,
)


# ---------------------------------------------------------------------------
# Symlink-capability detection (skip on Windows without dev mode)
# ---------------------------------------------------------------------------

def _can_make_symlinks(tmp_path: Path) -> bool:
    src = tmp_path / "_symlink_probe_target"
    src.write_text("x")
    link = tmp_path / "_symlink_probe"
    try:
        link.symlink_to(src)
        link.unlink()
        src.unlink()
        return True
    except (OSError, NotImplementedError):
        return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_service(audit=None, *, safety: SafetyService | None = None) -> CleanupService:
    """Build a CleanupService with a permissive SafetyService by default."""
    if safety is None:
        safety = SafetyService(app_data_paths=[], os_managed_paths=[])
    return CleanupService(safety, audit=audit)


# ===========================================================================
# find_empty_dirs
# ===========================================================================


class TestFindEmptyDirs:
    def test_nonexistent_root_returns_error(self, tmp_path):
        svc = _build_service()
        ghost = tmp_path / "ghost"
        report = svc.find_empty_dirs(ghost)
        assert report.count == 0
        assert any("does not exist" in e for e in report.errors)

    def test_not_a_directory_returns_error(self, tmp_path):
        svc = _build_service()
        f = tmp_path / "file.txt"
        f.write_text("x")
        report = svc.find_empty_dirs(f)
        assert report.count == 0
        assert any("not a directory" in e for e in report.errors)

    def test_no_empty_dirs(self, tmp_path):
        # Tree with content; nothing flagged.
        svc = _build_service()
        (tmp_path / "a" / "b").mkdir(parents=True)
        (tmp_path / "a" / "b" / "file.txt").write_text("hi")
        report = svc.find_empty_dirs(tmp_path)
        assert report.count == 0

    def test_finds_empty_leaf(self, tmp_path):
        svc = _build_service()
        (tmp_path / "lonely").mkdir()
        report = svc.find_empty_dirs(tmp_path)
        assert report.count == 1
        assert report.findings[0].path == str(tmp_path / "lonely")
        assert report.findings[0].kind == CleanupKind.EMPTY_DIR

    def test_root_itself_not_flagged(self, tmp_path):
        # Even if the root has no content, we don't flag it for deletion
        # \u2014 the user asked us to walk it, not delete it.
        svc = _build_service()
        empty_root = tmp_path / "root"
        empty_root.mkdir()
        report = svc.find_empty_dirs(empty_root)
        assert report.count == 0

    def test_thumbsdb_only_dir_flagged_when_ignoring_junk(self, tmp_path):
        # Directory containing only Thumbs.db is "effectively empty".
        svc = _build_service()
        d = tmp_path / "with_thumbs"
        d.mkdir()
        (d / "Thumbs.db").write_bytes(b"junk")
        report = svc.find_empty_dirs(tmp_path, ignore_system_junk=True)
        assert report.count == 1
        assert report.findings[0].details["system_junk_present"] == ["Thumbs.db"]

    def test_thumbsdb_only_dir_not_flagged_when_strict(self, tmp_path):
        svc = _build_service()
        d = tmp_path / "with_thumbs"
        d.mkdir()
        (d / "Thumbs.db").write_bytes(b"junk")
        report = svc.find_empty_dirs(tmp_path, ignore_system_junk=False)
        assert report.count == 0

    def test_cascade_parent_becomes_empty_after_child(self, tmp_path):
        # parent/ contains only child/, child/ contains nothing.
        # Bottom-up walk: child flagged first, then parent flagged
        # because its only contents are an already-empty dir.
        svc = _build_service()
        (tmp_path / "parent" / "child").mkdir(parents=True)
        report = svc.find_empty_dirs(tmp_path)
        paths = {f.path for f in report.findings}
        assert str(tmp_path / "parent" / "child") in paths
        assert str(tmp_path / "parent") in paths

    def test_dir_with_meaningful_file_not_flagged(self, tmp_path):
        svc = _build_service()
        d = tmp_path / "real"
        d.mkdir()
        (d / "doc.pdf").write_text("important")
        report = svc.find_empty_dirs(tmp_path)
        assert all(f.path != str(d) for f in report.findings)

    def test_system_junk_constants(self):
        # Common platform-junk filenames are recognized.
        for name in ("Thumbs.db", ".DS_Store", "desktop.ini"):
            assert name in SYSTEM_JUNK_NAMES


# ===========================================================================
# find_broken_symlinks
# ===========================================================================


class TestFindBrokenSymlinks:
    def test_nonexistent_root_returns_error(self, tmp_path):
        svc = _build_service()
        report = svc.find_broken_symlinks(tmp_path / "ghost")
        assert any("does not exist" in e for e in report.errors)

    def test_no_symlinks_no_findings(self, tmp_path):
        svc = _build_service()
        (tmp_path / "a.txt").write_text("hi")
        report = svc.find_broken_symlinks(tmp_path)
        assert report.count == 0

    def test_finds_broken_symlink(self, tmp_path):
        if not _can_make_symlinks(tmp_path):
            pytest.skip("symlink creation requires admin/dev mode on this platform")
        svc = _build_service()
        target = tmp_path / "target.txt"
        target.write_text("hi")
        link = tmp_path / "link"
        link.symlink_to(target)
        # Now break the link by removing the target.
        target.unlink()

        report = svc.find_broken_symlinks(tmp_path)
        assert report.count == 1
        assert report.findings[0].path == str(link)
        assert report.findings[0].kind == CleanupKind.BROKEN_SYMLINK

    def test_valid_symlink_not_flagged(self, tmp_path):
        if not _can_make_symlinks(tmp_path):
            pytest.skip("symlink creation requires admin/dev mode on this platform")
        svc = _build_service()
        target = tmp_path / "target.txt"
        target.write_text("hi")
        link = tmp_path / "link"
        link.symlink_to(target)
        # Target still exists \u2014 link is healthy.
        report = svc.find_broken_symlinks(tmp_path)
        assert report.count == 0


# ===========================================================================
# find_junk_files
# ===========================================================================


class TestFindJunkFiles:
    def test_nonexistent_root_returns_error(self, tmp_path):
        svc = _build_service()
        report = svc.find_junk_files(tmp_path / "ghost")
        assert any("does not exist" in e for e in report.errors)

    def test_no_junk(self, tmp_path):
        svc = _build_service()
        (tmp_path / "a.txt").write_text("hi")
        (tmp_path / "b.pdf").write_bytes(b"%PDF")
        report = svc.find_junk_files(tmp_path)
        assert report.count == 0

    def test_finds_thumbs_db(self, tmp_path):
        svc = _build_service()
        (tmp_path / "Thumbs.db").write_bytes(b"junk")
        report = svc.find_junk_files(tmp_path)
        assert report.count == 1
        assert report.findings[0].details["matched_pattern"] == "Thumbs.db"

    def test_finds_ds_store(self, tmp_path):
        svc = _build_service()
        (tmp_path / ".DS_Store").write_bytes(b"junk")
        report = svc.find_junk_files(tmp_path)
        assert report.count == 1
        assert report.findings[0].details["matched_pattern"] == ".DS_Store"

    def test_finds_desktop_ini(self, tmp_path):
        svc = _build_service()
        (tmp_path / "desktop.ini").write_text("junk")
        report = svc.find_junk_files(tmp_path)
        assert report.count == 1

    def test_finds_office_lock_file_via_glob(self, tmp_path):
        svc = _build_service()
        # Office leaves these when a doc is open.
        (tmp_path / "~$report.docx").write_bytes(b"lock")
        report = svc.find_junk_files(tmp_path)
        assert report.count == 1
        assert report.findings[0].details["matched_pattern"] == "~$*"

    def test_finds_tmp_files(self, tmp_path):
        svc = _build_service()
        (tmp_path / "scratch.tmp").write_text("temp")
        (tmp_path / "backup.bak").write_text("bak")
        report = svc.find_junk_files(tmp_path)
        # Both .tmp and .bak match different default patterns.
        assert report.count == 2
        patterns = {f.details["matched_pattern"] for f in report.findings}
        assert "*.tmp" in patterns
        assert "*.bak" in patterns

    def test_finds_apple_double(self, tmp_path):
        svc = _build_service()
        (tmp_path / "._sidecar.jpg").write_bytes(b"meta")
        report = svc.find_junk_files(tmp_path)
        assert report.count == 1
        assert report.findings[0].details["matched_pattern"] == "._*"

    def test_walks_subdirectories(self, tmp_path):
        svc = _build_service()
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "Thumbs.db").write_bytes(b"junk")
        report = svc.find_junk_files(tmp_path)
        assert report.count == 1
        assert report.findings[0].path.endswith("Thumbs.db")

    def test_custom_patterns_override_default(self, tmp_path):
        svc = _build_service()
        (tmp_path / "Thumbs.db").write_bytes(b"x")
        (tmp_path / "wanted.txt").write_text("x")
        report = svc.find_junk_files(tmp_path, patterns=["wanted.txt"])
        # Only wanted.txt matches; Thumbs.db doesn't because we
        # overrode with a custom pattern list.
        assert report.count == 1
        assert report.findings[0].path.endswith("wanted.txt")

    def test_records_size(self, tmp_path):
        svc = _build_service()
        (tmp_path / "Thumbs.db").write_bytes(b"x" * 1024)
        report = svc.find_junk_files(tmp_path)
        assert report.findings[0].size == 1024
        assert report.total_size == 1024


# ===========================================================================
# apply
# ===========================================================================


class TestApply:
    def test_empty_dir_applied(self, tmp_path):
        svc = _build_service()
        d = tmp_path / "to_remove"
        d.mkdir()
        report = svc.find_empty_dirs(tmp_path)
        assert report.count == 1

        result = svc.apply(report)
        assert result.deleted_count == 1
        assert result.failed_count == 0
        assert not d.exists()

    def test_empty_dir_with_thumbs_db_applied(self, tmp_path):
        svc = _build_service()
        d = tmp_path / "thumbs_only"
        d.mkdir()
        (d / "Thumbs.db").write_bytes(b"junk")
        report = svc.find_empty_dirs(tmp_path)
        assert report.count == 1

        result = svc.apply(report)
        assert result.deleted_count == 1
        # Both the dir AND the Thumbs.db inside are gone.
        assert not d.exists()

    def test_broken_symlink_applied(self, tmp_path):
        if not _can_make_symlinks(tmp_path):
            pytest.skip("symlink creation requires admin/dev mode")
        svc = _build_service()
        target = tmp_path / "target.txt"
        target.write_text("hi")
        link = tmp_path / "link"
        link.symlink_to(target)
        target.unlink()

        report = svc.find_broken_symlinks(tmp_path)
        assert report.count == 1
        result = svc.apply(report)
        assert result.deleted_count == 1
        assert not link.is_symlink()

    def test_junk_file_applied_via_unlink_when_no_trash(self, tmp_path):
        svc = _build_service()
        junk = tmp_path / "Thumbs.db"
        junk.write_bytes(b"x")
        report = svc.find_junk_files(tmp_path)
        result = svc.apply(report, use_trash=False)
        assert result.deleted_count == 1
        assert not junk.exists()

    def test_safety_refuse_skips(self, tmp_path):
        # Build a SafetyService that REFUSES the tmp_path tree.
        # (Easiest way: register tmp_path as os_managed.)
        svc = _build_service(
            safety=SafetyService(
                app_data_paths=[], os_managed_paths=[tmp_path],
            ),
        )
        junk = tmp_path / "Thumbs.db"
        junk.write_bytes(b"x")
        report = svc.find_junk_files(tmp_path)
        result = svc.apply(report)
        assert result.deleted_count == 0
        assert result.skipped_count == 1
        assert result.results[0].outcome == ApplyOutcome.SKIPPED_REFUSE
        assert "REFUSE" in (result.results[0].error or "")
        # File still there.
        assert junk.exists()

    def test_missing_target_recorded(self, tmp_path):
        # Plan finds a file, then user deletes it before apply runs.
        svc = _build_service()
        junk = tmp_path / "Thumbs.db"
        junk.write_bytes(b"x")
        report = svc.find_junk_files(tmp_path)
        # Manually remove before apply.
        junk.unlink()
        result = svc.apply(report)
        assert result.deleted_count == 0
        assert result.skipped_count == 1
        assert result.results[0].outcome == ApplyOutcome.SKIPPED_MISSING

    def test_audit_logged_per_deletion(self, tmp_path):
        audit = MagicMock()
        svc = _build_service(audit=audit)
        junk = tmp_path / "Thumbs.db"
        junk.write_bytes(b"x")
        report = svc.find_junk_files(tmp_path)
        svc.apply(report, use_trash=False)
        assert audit.log.call_count == 1
        kwargs = audit.log.call_args.kwargs
        assert kwargs["actor"] == "curator.cleanup"
        assert kwargs["action"] == "cleanup.junk_file.delete"
        assert kwargs["entity_type"] == "path"
        assert kwargs["entity_id"] == str(junk)

    def test_no_audit_when_audit_is_none(self, tmp_path):
        svc = _build_service(audit=None)
        junk = tmp_path / "Thumbs.db"
        junk.write_bytes(b"x")
        report = svc.find_junk_files(tmp_path)
        # Should not raise.
        result = svc.apply(report, use_trash=False)
        assert result.deleted_count == 1

    def test_failed_delete_recorded(self, tmp_path, monkeypatch):
        svc = _build_service()
        junk = tmp_path / "Thumbs.db"
        junk.write_bytes(b"x")
        report = svc.find_junk_files(tmp_path)

        # Force unlink to raise.
        original_unlink = Path.unlink
        def boom(self, *a, **kw):
            if self.name == "Thumbs.db":
                raise PermissionError("locked")
            return original_unlink(self, *a, **kw)
        monkeypatch.setattr(Path, "unlink", boom)

        result = svc.apply(report, use_trash=False)
        assert result.deleted_count == 0
        assert result.failed_count == 1
        assert "locked" in (result.results[0].error or "")
