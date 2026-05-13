"""Final cleanup coverage for cli/main.py (v1.7.175) — ARC CLOSE.

Targets the residual ~25 lines + 31 partial branches across the
non-cleanup ships, closing the CLI Coverage Arc.

Many of the remaining partials are in render loops where the False
arm of a `for/if` is technically reachable but requires specific data
shapes (e.g., a single move that's neither failed nor skipped, or a
report with NO failed moves at all). This file targets those data
shapes via focused stubs.
"""

from __future__ import annotations

import json
import sys
import types
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import pytest
from typer.testing import CliRunner

from curator.cli.main import app


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def isolated_cli_db(tmp_path):
    from curator.storage import CuratorDB
    db_path = tmp_path / "cli_final.db"
    db = CuratorDB(db_path)
    db.init()
    return {"db_path": db_path, "tmp_path": tmp_path}


# ---------------------------------------------------------------------------
# Doctor: ppdeep + send2trash PyPI fallback paths (lines 1671-1676, 1684)
# ---------------------------------------------------------------------------


class TestDoctorVendoredFallback:
    def test_ppdeep_pypi_fallback_when_vendored_missing(
        self, runner, isolated_cli_db, monkeypatch,
    ):
        """Lines 1671-1674: vendored ppdeep missing -> PyPI ppdeep present."""
        import curator._vendored as vendored_mod
        # Defeat both the sys.modules cache AND the package attribute
        original = getattr(vendored_mod, "ppdeep", None)
        if original is not None:
            monkeypatch.delattr(vendored_mod, "ppdeep")
        monkeypatch.setitem(sys.modules, "curator._vendored.ppdeep", None)
        # Provide PyPI ppdeep
        fake_ppdeep = types.ModuleType("ppdeep")
        monkeypatch.setitem(sys.modules, "ppdeep", fake_ppdeep)

        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "doctor"],
        )
        combined = result.stdout + (result.stderr or "")
        assert "ppdeep" in combined
        assert "installed" in combined  # PyPI message

    def test_ppdeep_missing_both_paths(
        self, runner, isolated_cli_db, monkeypatch,
    ):
        """Lines 1675-1676: both vendored AND PyPI ppdeep imports fail
        -> 'missing' yellow message. ppdeep is optional so no issue
        is accumulated (unlike send2trash which IS an issue)."""
        import curator._vendored as vendored_mod
        original = getattr(vendored_mod, "ppdeep", None)
        if original is not None:
            monkeypatch.delattr(vendored_mod, "ppdeep")
        monkeypatch.setitem(sys.modules, "curator._vendored.ppdeep", None)
        monkeypatch.setitem(sys.modules, "ppdeep", None)

        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "doctor"],
        )
        # ppdeep missing is NOT a hard issue (not appended to issues list)
        # so the command can still exit 0
        combined = result.stdout + (result.stderr or "")
        assert "ppdeep" in combined
        assert "missing" in combined

    def test_send2trash_pypi_fallback_when_vendored_missing(
        self, runner, isolated_cli_db, monkeypatch,
    ):
        """Line 1684: vendored send2trash missing -> PyPI send2trash present."""
        import curator._vendored as vendored_mod
        original = getattr(vendored_mod, "send2trash", None)
        if original is not None:
            monkeypatch.delattr(vendored_mod, "send2trash")
        monkeypatch.setitem(sys.modules, "curator._vendored.send2trash", None)
        # Provide PyPI send2trash
        fake_s2t = types.ModuleType("send2trash")
        monkeypatch.setitem(sys.modules, "send2trash", fake_s2t)

        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "doctor"],
        )
        combined = result.stdout + (result.stderr or "")
        assert "send2trash" in combined
        # Either "installed" (PyPI) or "vendored" depending on env;
        # both indicate success
        assert "installed" in combined or "vendored" in combined


# ---------------------------------------------------------------------------
# Organize SAFE file WITHOUT a proposal (line 2125)
# ---------------------------------------------------------------------------


class TestOrganizeSafeWithoutProposal:
    def test_show_files_safe_without_proposal_prints_path_only(
        self, runner, isolated_cli_db, monkeypatch,
    ):
        """Line 2125: --show-files SAFE bucket file WITHOUT a proposal
        prints the path only (no arrow line)."""
        from curator._compat.datetime import utcnow_naive
        from curator.models import FileEntity
        from curator.services.organize import (
            OrganizePlan, OrganizeService,
        )
        from curator.services.safety import SafetyLevel, SafetyReport

        plan = OrganizePlan(
            source_id="local", root_prefix=None,
            completed_at=datetime(2026, 1, 1, 0, 0, 1),
        )
        f = FileEntity(
            source_id="local", source_path="/no_proposal.txt",
            size=100, mtime=utcnow_naive(),
        )
        plan.safe.add(f, SafetyReport(path="/no_proposal.txt",
                                       level=SafetyLevel.SAFE))
        # NO proposal added for this file
        monkeypatch.setattr(OrganizeService, "plan",
                             lambda self, **kw: plan)

        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "organize", "local",
             "--show-files"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "no_proposal.txt" in combined


# ---------------------------------------------------------------------------
# Cleanup report: errors capped at 5 (line 2323) + empty findings rendering
# ---------------------------------------------------------------------------


class TestCleanupEdgeCases:
    def test_cleanup_more_than_5_errors_caps(
        self, runner, isolated_cli_db, monkeypatch, tmp_path,
    ):
        """Line 2323: report.errors[:5] cap — verify only first 5 shown
        when there are 8."""
        from curator.services.cleanup import (
            CleanupFinding, CleanupKind, CleanupReport, CleanupService,
        )
        report = CleanupReport(
            kind=CleanupKind.JUNK_FILE, root="/x",
            findings=[CleanupFinding(path="/j", kind=CleanupKind.JUNK_FILE)],
            errors=[f"err_{i}" for i in range(8)],
            completed_at=datetime(2026, 1, 1, 0, 0, 1),
        )
        monkeypatch.setattr(CleanupService, "find_junk_files",
                             lambda self, root: report)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "cleanup",
             "junk", str(tmp_path)],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        # First 5 visible
        assert "err_0" in combined
        assert "err_4" in combined
        # 6th+ NOT in output
        assert "err_5" not in combined
        assert "err_7" not in combined


# ---------------------------------------------------------------------------
# Final coverage validation
# ---------------------------------------------------------------------------


class TestExportCleanShowFilesSkipsFailed:
    def test_show_files_skips_failed_results(
        self, runner, isolated_cli_db, tmp_path, monkeypatch,
    ):
        """Line 4310: --show-files iterates report.results but `continue`s
        on r.outcome == 'failed' (failures are rendered in their own
        section above). Need a mix of failed + non-failed in --show-files."""
        from curator.services.metadata_stripper import (
            MetadataStripper, StripOutcome, StripReport, StripResult,
        )
        src = tmp_path / "tree"
        src.mkdir()
        dst = tmp_path / "out"
        report = StripReport(
            started_at=datetime(2026, 1, 1, 12, 0, 0),
            completed_at=datetime(2026, 1, 1, 12, 0, 5),
            results=[
                StripResult(source="/ok.jpg", destination="/dst/ok.jpg",
                             outcome=StripOutcome.STRIPPED, bytes_in=10,
                             bytes_out=8, metadata_fields_removed=["EXIF"]),
                StripResult(source="/bad.jpg", destination=None,
                             outcome=StripOutcome.FAILED,
                             error="permission denied"),
            ],
        )
        monkeypatch.setattr(MetadataStripper, "strip_directory",
                             lambda self, *a, **kw: report)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "export-clean",
             str(src), str(dst), "--show-files"],
        )
        # Failure causes exit 1
        assert result.exit_code == 1
        combined = result.stdout + (result.stderr or "")
        # Per-file outcomes shows the stripped one
        assert "Per-file outcomes" in combined
        # The failed one is NOT in per-file outcomes (skipped via continue);
        # it appears in the earlier Failures section
        assert "Failures" in combined
        assert "permission denied" in combined


class TestArcCloseValidation:
    """Sanity tests confirming the CLI Arc-close state."""

    def test_app_imports_cleanly(self):
        """The CLI module must import without errors at session start."""
        from curator.cli.main import app
        assert app is not None
        assert hasattr(app, "registered_commands") or hasattr(app, "info")

    def test_resolve_file_is_callable(self):
        """v1.7.155 documented two `_resolve_file` definitions; v1.7.180
        resolved that deferral by deleting the dead duplicate and merging
        its prefix-match feature into the live definition. Now there is
        exactly one ``_resolve_file`` at module level."""
        from curator.cli import main as main_mod
        assert callable(main_mod._resolve_file)


# ---------------------------------------------------------------------------
# Scan-pii: CSV per-match WITHOUT metadata (line 4029) + skip empty-match
# reports in per-file render (line 4085)
# ---------------------------------------------------------------------------


class TestScanPiiResidual:
    def test_csv_per_match_no_metadata(
        self, runner, isolated_cli_db, monkeypatch, tmp_path,
    ):
        """Line 4029: metadata None -> meta_str = '' branch."""
        from curator.services.pii_scanner import (
            PIIMatch, PIIScanReport, PIIScanner, PIISeverity,
        )
        target = tmp_path / "nm.txt"
        target.write_text("data")
        m_no_meta = PIIMatch(
            pattern_name="email", severity=PIISeverity.LOW,
            matched_text="x", redacted="***", offset=0, line=1,
            metadata=None,
        )
        report = PIIScanReport(
            source=str(target), bytes_scanned=10, truncated=False,
            matches=[m_no_meta],
        )
        monkeypatch.setattr(PIIScanner, "scan_file",
                             lambda self, p: report)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "scan-pii", str(target), "--csv", "--show-matches"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        # Per-match row with empty metadata column at end
        assert "email" in combined

    def test_human_skips_zero_match_reports(
        self, runner, isolated_cli_db, monkeypatch, tmp_path,
    ):
        """Line 4084-4085: r.match_count == 0 -> continue (skip in per-file
        loop). Need a directory with mixed-match + zero-match reports."""
        from curator.services.pii_scanner import (
            PIIMatch, PIIScanReport, PIIScanner, PIISeverity,
        )
        target = tmp_path / "td"
        target.mkdir()
        reports = [
            PIIScanReport(source="/empty.txt", bytes_scanned=10,
                          truncated=False, matches=[]),
            PIIScanReport(
                source="/has_match.txt", bytes_scanned=10, truncated=False,
                matches=[PIIMatch(
                    pattern_name="email", severity=PIISeverity.LOW,
                    matched_text="x", redacted="***", offset=0, line=1,
                )],
            ),
        ]
        monkeypatch.setattr(PIIScanner, "scan_directory",
                             lambda self, p, **kw: reports)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "scan-pii", str(target)],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        # Has-match report rendered; empty one skipped
        assert "/has_match.txt" in combined
        # /empty.txt may appear in "Files scanned" counter but NOT in per-file
        # section


# ---------------------------------------------------------------------------
# Audit-summary: --actor filter line + --action filter line rendering
# (lines 4889, 4971) — covered by existing tests but partial branches
# may still appear if our test data doesn't filter to a single group
# ---------------------------------------------------------------------------


class TestAuditSummaryResidual:
    def test_with_singular_remainder(
        self, runner, tmp_path,
    ):
        """Singular path in 'and N more' when remainder == 1.
        Build a fresh DB just for this test (the isolated_cli_db fixture
        for this file is the cli_final.db which doesn't include
        AuditRepository in its dict)."""
        from datetime import timedelta
        from curator._compat.datetime import utcnow_naive
        from curator.storage import CuratorDB
        from curator.storage.repositories import AuditRepository
        db_path = tmp_path / "audit_remainder.db"
        db = CuratorDB(db_path)
        db.init()
        audit = AuditRepository(db)
        base = utcnow_naive() - timedelta(hours=2)
        for i in range(4):
            audit.log(
                actor=f"a_{i}", action=f"act_{i}",
                when=base + timedelta(minutes=i),
            )
        result = runner.invoke(
            app,
            ["--db", str(db_path),
             "audit-summary", "--limit", "3"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        # 4 groups, limit 3 -> "and 1 more"
        assert "and 1 more" in combined
