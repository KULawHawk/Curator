"""Coverage closure for cli/main.py `cleanup_app` (v1.7.164).

Tier 3 sub-ship 10 of the CLI Coverage Arc.

Targets the 4 cleanup subcommands + 4 rendering/conversion helpers:
- `_render_cleanup_report` (lines 2316-2370 — junk/symlink/empty branches + errors + count==0 + count>20 cap)
- `_render_cleanup_apply` (lines 2377-2395)
- `_cleanup_report_to_dict` / `_cleanup_apply_to_dict` (covered via JSON paths)
- `_run_cleanup` (shared driver)
- `cleanup empty-dirs` / `broken-symlinks` / `junk` / `duplicates`
- `_render_duplicate_report` (lines 2569-2635, including fuzzy mode + cap at 20)
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from curator.cli.main import app
from curator.services.cleanup import (
    ApplyOutcome, ApplyReport, ApplyResult,
    CleanupFinding, CleanupKind, CleanupReport,
)


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def isolated_cli_db(tmp_path):
    from curator.storage import CuratorDB
    db_path = tmp_path / "cli_cleanup.db"
    db = CuratorDB(db_path)
    db.init()
    return {"db_path": db_path, "tmp_path": tmp_path}


def _build_report(
    kind: CleanupKind,
    *,
    root: str = "/x",
    findings: list[CleanupFinding] | None = None,
    errors: list[str] | None = None,
) -> CleanupReport:
    return CleanupReport(
        kind=kind, root=root,
        findings=findings or [],
        completed_at=datetime(2026, 1, 1, 0, 0, 1),
        errors=errors or [],
    )


def _apply_report(
    *, kind: CleanupKind = CleanupKind.JUNK_FILE,
    deleted: int = 0, skipped: int = 0, failed: int = 0,
    with_errors: bool = False,
) -> ApplyReport:
    rep = ApplyReport(
        kind=kind, completed_at=datetime(2026, 1, 1, 0, 0, 1),
    )
    for i in range(deleted):
        rep.results.append(ApplyResult(
            finding=CleanupFinding(path=f"/d{i}", kind=kind, size=10),
            outcome=ApplyOutcome.DELETED,
        ))
    for i in range(skipped):
        rep.results.append(ApplyResult(
            finding=CleanupFinding(path=f"/s{i}", kind=kind, size=10),
            outcome=ApplyOutcome.SKIPPED_REFUSE,
            error="too dangerous" if with_errors else None,
        ))
    for i in range(failed):
        rep.results.append(ApplyResult(
            finding=CleanupFinding(path=f"/f{i}", kind=kind, size=10),
            outcome=ApplyOutcome.FAILED,
            error=f"io_err_{i}" if with_errors else None,
        ))
    return rep


# ---------------------------------------------------------------------------
# cleanup empty-dirs
# ---------------------------------------------------------------------------


class TestCleanupEmptyDirs:
    def test_dry_run_empty_result(self, runner, isolated_cli_db, monkeypatch):
        from curator.services.cleanup import CleanupService
        monkeypatch.setattr(
            CleanupService, "find_empty_dirs",
            lambda self, root, *, ignore_system_junk: _build_report(
                CleanupKind.EMPTY_DIR, root=str(root),
            ),
        )
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "cleanup",
             "empty-dirs", str(isolated_cli_db["tmp_path"])],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "Cleanup" in combined
        assert "Nothing to clean up" in combined

    def test_dry_run_with_findings_human(
        self, runner, isolated_cli_db, monkeypatch,
    ):
        """Renders EMPTY_DIR findings + system_junk_present detail."""
        from curator.services.cleanup import CleanupService
        findings = [
            CleanupFinding(
                path=f"/empty{i}", kind=CleanupKind.EMPTY_DIR,
                details={"system_junk_present": ["Thumbs.db"]} if i == 0 else {},
            )
            for i in range(3)
        ]
        monkeypatch.setattr(
            CleanupService, "find_empty_dirs",
            lambda self, root, *, ignore_system_junk: _build_report(
                CleanupKind.EMPTY_DIR, root=str(root), findings=findings,
            ),
        )
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "cleanup",
             "empty-dirs", str(isolated_cli_db["tmp_path"])],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "empty dir" in combined.lower()
        assert "/empty0" in combined
        assert "Thumbs.db" in combined
        assert "plan preview" in combined

    def test_strict_flag_passes_through(
        self, runner, isolated_cli_db, monkeypatch,
    ):
        from curator.services.cleanup import CleanupService
        captured = {}

        def _stub(self, root, *, ignore_system_junk):
            captured["ignore_system_junk"] = ignore_system_junk
            return _build_report(CleanupKind.EMPTY_DIR, root=str(root))

        monkeypatch.setattr(CleanupService, "find_empty_dirs", _stub)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "cleanup",
             "empty-dirs", str(isolated_cli_db["tmp_path"]), "--strict"],
        )
        assert result.exit_code == 0
        assert captured["ignore_system_junk"] is False

    def test_apply_flag_triggers_apply(
        self, runner, isolated_cli_db, monkeypatch,
    ):
        from curator.services.cleanup import CleanupService
        report = _build_report(
            CleanupKind.EMPTY_DIR,
            findings=[CleanupFinding(path="/e1", kind=CleanupKind.EMPTY_DIR)],
        )
        monkeypatch.setattr(
            CleanupService, "find_empty_dirs",
            lambda self, root, *, ignore_system_junk: report,
        )
        monkeypatch.setattr(
            CleanupService, "apply",
            lambda self, r, *, use_trash: _apply_report(
                kind=CleanupKind.EMPTY_DIR, deleted=1,
            ),
        )
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "cleanup",
             "empty-dirs", str(isolated_cli_db["tmp_path"]), "--apply"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "Cleanup apply" in combined
        assert "deleted=1" in combined

    def test_json_output(self, runner, isolated_cli_db, monkeypatch):
        from curator.services.cleanup import CleanupService
        monkeypatch.setattr(
            CleanupService, "find_empty_dirs",
            lambda self, root, *, ignore_system_junk: _build_report(
                CleanupKind.EMPTY_DIR, root=str(root),
            ),
        )
        result = runner.invoke(
            app,
            ["--json", "--db", str(isolated_cli_db["db_path"]), "cleanup",
             "empty-dirs", str(isolated_cli_db["tmp_path"])],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert '"plan"' in combined
        assert '"kind": "empty_dir"' in combined

    def test_json_output_with_apply(self, runner, isolated_cli_db, monkeypatch):
        from curator.services.cleanup import CleanupService
        monkeypatch.setattr(
            CleanupService, "find_empty_dirs",
            lambda self, root, *, ignore_system_junk: _build_report(
                CleanupKind.EMPTY_DIR,
                findings=[CleanupFinding(path="/e1", kind=CleanupKind.EMPTY_DIR)],
            ),
        )
        monkeypatch.setattr(
            CleanupService, "apply",
            lambda self, r, *, use_trash: _apply_report(
                kind=CleanupKind.EMPTY_DIR, deleted=1,
            ),
        )
        result = runner.invoke(
            app,
            ["--json", "--db", str(isolated_cli_db["db_path"]), "cleanup",
             "empty-dirs", str(isolated_cli_db["tmp_path"]), "--apply"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert '"apply"' in combined
        assert '"deleted_count": 1' in combined


# ---------------------------------------------------------------------------
# cleanup broken-symlinks
# ---------------------------------------------------------------------------


class TestCleanupBrokenSymlinks:
    def test_dry_run_with_findings(self, runner, isolated_cli_db, monkeypatch):
        from curator.services.cleanup import CleanupService
        findings = [
            CleanupFinding(
                path=f"/link{i}", kind=CleanupKind.BROKEN_SYMLINK,
                details={"target": f"/nowhere{i}"},
            )
            for i in range(2)
        ]
        monkeypatch.setattr(
            CleanupService, "find_broken_symlinks",
            lambda self, root: _build_report(
                CleanupKind.BROKEN_SYMLINK, root=str(root), findings=findings,
            ),
        )
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "cleanup",
             "broken-symlinks", str(isolated_cli_db["tmp_path"])],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "broken symlink" in combined.lower()
        # `-> target` detail shown for symlinks
        assert "/nowhere0" in combined


# ---------------------------------------------------------------------------
# cleanup junk
# ---------------------------------------------------------------------------


class TestCleanupJunk:
    def test_junk_with_pattern_detail(self, runner, isolated_cli_db, monkeypatch):
        from curator.services.cleanup import CleanupService
        findings = [
            CleanupFinding(
                path="/x/Thumbs.db", kind=CleanupKind.JUNK_FILE, size=1024,
                details={"matched_pattern": "Thumbs.db"},
            ),
        ]
        monkeypatch.setattr(
            CleanupService, "find_junk_files",
            lambda self, root: _build_report(
                CleanupKind.JUNK_FILE, root=str(root), findings=findings,
            ),
        )
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "cleanup",
             "junk", str(isolated_cli_db["tmp_path"])],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "junk file" in combined.lower()
        assert "Thumbs.db" in combined

    def test_apply_with_no_trash_passes_use_trash_false(
        self, runner, isolated_cli_db, monkeypatch,
    ):
        from curator.services.cleanup import CleanupService
        captured = {}

        def _stub_apply(self, r, *, use_trash):
            captured["use_trash"] = use_trash
            return _apply_report(deleted=1)

        monkeypatch.setattr(
            CleanupService, "find_junk_files",
            lambda self, root: _build_report(
                CleanupKind.JUNK_FILE,
                findings=[CleanupFinding(path="/j", kind=CleanupKind.JUNK_FILE)],
            ),
        )
        monkeypatch.setattr(CleanupService, "apply", _stub_apply)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "cleanup",
             "junk", str(isolated_cli_db["tmp_path"]),
             "--apply", "--no-trash"],
        )
        assert result.exit_code == 0
        assert captured["use_trash"] is False


# ---------------------------------------------------------------------------
# _render_cleanup_report — cap at 20 + errors
# ---------------------------------------------------------------------------


class TestRenderCleanupReportCapAndErrors:
    def test_capped_at_20_with_remainder(
        self, runner, isolated_cli_db, monkeypatch,
    ):
        """Lines 2356-2357: > 20 findings shows 'and N more'."""
        from curator.services.cleanup import CleanupService
        findings = [
            CleanupFinding(path=f"/j{i}", kind=CleanupKind.JUNK_FILE, size=10)
            for i in range(25)
        ]
        monkeypatch.setattr(
            CleanupService, "find_junk_files",
            lambda self, root: _build_report(
                CleanupKind.JUNK_FILE, findings=findings,
            ),
        )
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "cleanup",
             "junk", str(isolated_cli_db["tmp_path"])],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "and 5 more" in combined

    def test_errors_section_rendered(
        self, runner, isolated_cli_db, monkeypatch,
    ):
        """Lines 2359-2362: report.errors -> 'Errors during walk' section."""
        from curator.services.cleanup import CleanupService
        errors = [f"perm denied at /e{i}" for i in range(3)]
        monkeypatch.setattr(
            CleanupService, "find_junk_files",
            lambda self, root: _build_report(
                CleanupKind.JUNK_FILE,
                findings=[CleanupFinding(path="/j", kind=CleanupKind.JUNK_FILE)],
                errors=errors,
            ),
        )
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "cleanup",
             "junk", str(isolated_cli_db["tmp_path"])],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "Errors during walk" in combined
        assert "perm denied" in combined


# ---------------------------------------------------------------------------
# _render_cleanup_apply — failures + skipped detail
# ---------------------------------------------------------------------------


class TestRenderCleanupApplyDetails:
    def test_failures_and_skipped_rendered(
        self, runner, isolated_cli_db, monkeypatch,
    ):
        from curator.services.cleanup import CleanupService
        monkeypatch.setattr(
            CleanupService, "find_junk_files",
            lambda self, root: _build_report(
                CleanupKind.JUNK_FILE,
                findings=[CleanupFinding(path="/j", kind=CleanupKind.JUNK_FILE)],
            ),
        )
        monkeypatch.setattr(
            CleanupService, "apply",
            lambda self, r, *, use_trash: _apply_report(
                deleted=1, skipped=2, failed=1, with_errors=True,
            ),
        )
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "cleanup",
             "junk", str(isolated_cli_db["tmp_path"]), "--apply"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "skipped=2" in combined
        assert "failed=1" in combined
        # Non-DELETED outcomes shown
        assert "skipped_refuse" in combined or "failed" in combined
        assert "io_err_0" in combined


# ---------------------------------------------------------------------------
# cleanup duplicates — _render_duplicate_report
# ---------------------------------------------------------------------------


def _dup_findings(*, n_groups: int = 1, per_group: int = 2,
                   match_kind: str = "exact") -> list[CleanupFinding]:
    findings = []
    for g in range(n_groups):
        for d in range(per_group):
            findings.append(CleanupFinding(
                path=f"/g{g}_dup{d}", kind=CleanupKind.DUPLICATE_FILE, size=100,
                details={
                    "dupset_id": f"hash_{g}",
                    "kept_path": f"/g{g}_keeper",
                    "kept_reason": "shortest_path",
                    "match_kind": match_kind,
                },
            ))
    return findings


class TestCleanupDuplicates:
    def test_no_duplicates(self, runner, isolated_cli_db, monkeypatch):
        from curator.services.cleanup import CleanupService
        monkeypatch.setattr(
            CleanupService, "find_duplicates",
            lambda self, **kw: _build_report(CleanupKind.DUPLICATE_FILE),
        )
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "cleanup", "duplicates"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "No duplicates found" in combined

    def test_exact_match_with_groups(self, runner, isolated_cli_db, monkeypatch):
        from curator.services.cleanup import CleanupService
        findings = _dup_findings(n_groups=2, per_group=3, match_kind="exact")
        monkeypatch.setattr(
            CleanupService, "find_duplicates",
            lambda self, **kw: _build_report(
                CleanupKind.DUPLICATE_FILE, findings=findings,
            ),
        )
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "cleanup", "duplicates"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "exact" in combined
        assert "duplicate group" in combined.lower()
        assert "Set 1" in combined
        assert "g0_keeper" in combined
        assert "g0_dup0" in combined
        assert "plan preview" in combined

    def test_fuzzy_match_kind_label(self, runner, isolated_cli_db, monkeypatch):
        """Verify fuzzy match-kind label appears."""
        from curator.services.cleanup import CleanupService
        findings = _dup_findings(n_groups=1, per_group=2, match_kind="fuzzy")
        monkeypatch.setattr(
            CleanupService, "find_duplicates",
            lambda self, **kw: _build_report(
                CleanupKind.DUPLICATE_FILE, findings=findings,
            ),
        )
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "cleanup",
             "duplicates", "--match-kind", "fuzzy"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "fuzzy" in combined.lower()

    def test_groups_capped_at_20(self, runner, isolated_cli_db, monkeypatch):
        """Lines 2620-2621: > 20 groups -> 'and N more' message."""
        from curator.services.cleanup import CleanupService
        findings = _dup_findings(n_groups=25, per_group=2)
        monkeypatch.setattr(
            CleanupService, "find_duplicates",
            lambda self, **kw: _build_report(
                CleanupKind.DUPLICATE_FILE, findings=findings,
            ),
        )
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "cleanup", "duplicates"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "and 5 more duplicate group" in combined

    def test_errors_rendered(self, runner, isolated_cli_db, monkeypatch):
        """Lines 2623-2626: report.errors -> 'Errors during query' section."""
        from curator.services.cleanup import CleanupService
        monkeypatch.setattr(
            CleanupService, "find_duplicates",
            lambda self, **kw: _build_report(
                CleanupKind.DUPLICATE_FILE,
                findings=_dup_findings(n_groups=1, per_group=2),
                errors=["query failed at /x", "hash missing"],
            ),
        )
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "cleanup", "duplicates"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "Errors during query" in combined

    def test_json_output(self, runner, isolated_cli_db, monkeypatch):
        from curator.services.cleanup import CleanupService
        monkeypatch.setattr(
            CleanupService, "find_duplicates",
            lambda self, **kw: _build_report(
                CleanupKind.DUPLICATE_FILE,
                findings=_dup_findings(n_groups=1, per_group=2),
            ),
        )
        result = runner.invoke(
            app,
            ["--json", "--db", str(isolated_cli_db["db_path"]),
             "cleanup", "duplicates"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert '"kind": "duplicate_file"' in combined
        assert '"findings"' in combined

    def test_apply_with_options_passes_through(
        self, runner, isolated_cli_db, monkeypatch,
    ):
        """Verify --source, --root, --keep-strategy, --keep-under,
        --similarity-threshold all reach find_duplicates."""
        from curator.services.cleanup import CleanupService
        captured = {}

        def _stub(self, **kw):
            captured.update(kw)
            return _build_report(
                CleanupKind.DUPLICATE_FILE,
                findings=_dup_findings(n_groups=1, per_group=2),
            )

        monkeypatch.setattr(CleanupService, "find_duplicates", _stub)
        monkeypatch.setattr(
            CleanupService, "apply",
            lambda self, r, *, use_trash: _apply_report(
                kind=CleanupKind.DUPLICATE_FILE, deleted=2,
            ),
        )
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "cleanup", "duplicates",
             "--source", "local", "--root", "/sub",
             "--keep-strategy", "newest", "--keep-under", "/keep",
             "--similarity-threshold", "0.9",
             "--apply", "--no-trash"],
        )
        assert result.exit_code == 0
        assert captured.get("source_id") == "local"
        assert captured.get("root_prefix") == "/sub"
        assert captured.get("keep_strategy") == "newest"
        assert captured.get("keep_under") == "/keep"
        assert captured.get("similarity_threshold") == 0.9
