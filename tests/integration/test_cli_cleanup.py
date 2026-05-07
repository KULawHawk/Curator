"""Integration tests for `curator cleanup ...` CLI subcommands (Phase Gamma F6)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from curator.cli.main import app
from curator.services.cleanup import CleanupService
from curator.services.safety import SafetyService


pytestmark = pytest.mark.integration


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "curator_cleanup_cli.db"


def _isolated_safety(monkeypatch) -> None:
    """Force tmp_path subtrees to be SAFE under cleanup's SafetyService.

    On Windows, %TEMP% often sits under %LOCALAPPDATA%, which the
    default SafetyService classifies as APP_DATA. Cleanup itself
    doesn't refuse on CAUTION, but we want clean output for these
    tests that don't depend on the platform's app-data registry.
    """
    real_init = CleanupService.__init__
    def patched_init(self, safety, *args, **kwargs):
        loose = SafetyService(app_data_paths=[], os_managed_paths=[])
        real_init(self, loose, *args, **kwargs)
    monkeypatch.setattr(CleanupService, "__init__", patched_init)


# ---------------------------------------------------------------------------
# empty-dirs subcommand
# ---------------------------------------------------------------------------


class TestCleanupEmptyDirs:
    def test_plan_only_lists_empty_dirs(
        self, runner, db_path, tmp_path, monkeypatch
    ):
        _isolated_safety(monkeypatch)
        (tmp_path / "tree" / "lonely").mkdir(parents=True)
        result = runner.invoke(
            app,
            ["--db", str(db_path), "cleanup", "empty-dirs",
             str(tmp_path / "tree")],
        )
        assert result.exit_code == 0, result.stdout + (result.stderr or "")
        assert "Cleanup (empty dir)" in result.stdout
        assert "lonely" in result.stdout
        # Plan-only: directory still exists.
        assert (tmp_path / "tree" / "lonely").exists()

    def test_apply_actually_removes(
        self, runner, db_path, tmp_path, monkeypatch
    ):
        _isolated_safety(monkeypatch)
        (tmp_path / "tree" / "to_remove").mkdir(parents=True)
        result = runner.invoke(
            app,
            ["--db", str(db_path), "cleanup", "empty-dirs",
             str(tmp_path / "tree"), "--apply"],
        )
        assert result.exit_code == 0, result.stdout
        assert "Cleanup apply" in result.stdout
        assert not (tmp_path / "tree" / "to_remove").exists()

    def test_strict_mode_ignores_thumbs_db(
        self, runner, db_path, tmp_path, monkeypatch
    ):
        _isolated_safety(monkeypatch)
        d = tmp_path / "with_thumbs"
        d.mkdir()
        (d / "Thumbs.db").write_bytes(b"x")
        # Without --strict, the Thumbs.db-only dir should be flagged.
        result_default = runner.invoke(
            app,
            ["--json", "--db", str(db_path), "cleanup", "empty-dirs",
             str(tmp_path)],
        )
        payload = json.loads(result_default.stdout)
        assert payload["plan"]["count"] == 1
        # With --strict, it should NOT be flagged.
        result_strict = runner.invoke(
            app,
            ["--json", "--db", str(db_path), "cleanup", "empty-dirs",
             str(tmp_path), "--strict"],
        )
        payload_strict = json.loads(result_strict.stdout)
        assert payload_strict["plan"]["count"] == 0


# ---------------------------------------------------------------------------
# junk subcommand
# ---------------------------------------------------------------------------


class TestCleanupJunk:
    def test_plan_finds_junk(self, runner, db_path, tmp_path, monkeypatch):
        _isolated_safety(monkeypatch)
        (tmp_path / "Thumbs.db").write_bytes(b"x")
        (tmp_path / ".DS_Store").write_bytes(b"x")
        (tmp_path / "real_file.txt").write_text("keep")
        result = runner.invoke(
            app,
            ["--json", "--db", str(db_path), "cleanup", "junk", str(tmp_path)],
        )
        assert result.exit_code == 0, result.stdout
        payload = json.loads(result.stdout)
        assert payload["plan"]["count"] == 2
        # The non-junk file is still there because plan-only doesn't apply.
        assert (tmp_path / "real_file.txt").exists()
        assert (tmp_path / "Thumbs.db").exists()

    def test_apply_with_no_trash_permadeletes(
        self, runner, db_path, tmp_path, monkeypatch
    ):
        _isolated_safety(monkeypatch)
        junk = tmp_path / "Thumbs.db"
        junk.write_bytes(b"x")
        result = runner.invoke(
            app,
            ["--db", str(db_path), "cleanup", "junk",
             str(tmp_path), "--apply", "--no-trash"],
        )
        assert result.exit_code == 0, result.stdout
        assert "Cleanup apply" in result.stdout
        assert not junk.exists()

    def test_safety_refuse_skipped_in_apply(
        self, runner, db_path, tmp_path, monkeypatch
    ):
        # Configure CleanupService with tmp_path REGISTERED as os_managed
        # so safety REFUSES every cleanup target.
        real_init = CleanupService.__init__
        def patched_init(self, safety, *args, **kwargs):
            strict = SafetyService(
                app_data_paths=[], os_managed_paths=[tmp_path],
            )
            real_init(self, strict, *args, **kwargs)
        monkeypatch.setattr(CleanupService, "__init__", patched_init)

        junk = tmp_path / "Thumbs.db"
        junk.write_bytes(b"x")
        result = runner.invoke(
            app,
            ["--json", "--db", str(db_path), "cleanup", "junk",
             str(tmp_path), "--apply"],
        )
        assert result.exit_code == 0, result.stdout
        payload = json.loads(result.stdout)
        # Plan found it; apply skipped it.
        assert payload["plan"]["count"] == 1
        assert payload["apply"]["deleted_count"] == 0
        assert payload["apply"]["skipped_count"] == 1
        # File preserved \u2014 SafetyService's hard-block worked.
        assert junk.exists()


# ---------------------------------------------------------------------------
# broken-symlinks subcommand
# ---------------------------------------------------------------------------


def _can_symlink(tmp_path: Path) -> bool:
    src = tmp_path / "_probe_target"
    src.write_text("x")
    link = tmp_path / "_probe"
    try:
        link.symlink_to(src)
        link.unlink()
        src.unlink()
        return True
    except (OSError, NotImplementedError):
        return False


class TestCleanupBrokenSymlinks:
    def test_plan_finds_broken_symlink(
        self, runner, db_path, tmp_path, monkeypatch
    ):
        if not _can_symlink(tmp_path):
            pytest.skip("symlink creation requires admin/dev mode")
        _isolated_safety(monkeypatch)
        target = tmp_path / "real.txt"
        target.write_text("x")
        link = tmp_path / "broken_link"
        link.symlink_to(target)
        target.unlink()  # break it

        result = runner.invoke(
            app,
            ["--json", "--db", str(db_path), "cleanup", "broken-symlinks",
             str(tmp_path)],
        )
        assert result.exit_code == 0, result.stdout
        payload = json.loads(result.stdout)
        assert payload["plan"]["count"] == 1
        # Plan-only: link still there.
        assert link.is_symlink()

    def test_apply_unlinks_broken_symlink(
        self, runner, db_path, tmp_path, monkeypatch
    ):
        if not _can_symlink(tmp_path):
            pytest.skip("symlink creation requires admin/dev mode")
        _isolated_safety(monkeypatch)
        target = tmp_path / "real.txt"
        target.write_text("x")
        link = tmp_path / "broken_link"
        link.symlink_to(target)
        target.unlink()

        result = runner.invoke(
            app,
            ["--db", str(db_path), "cleanup", "broken-symlinks",
             str(tmp_path), "--apply"],
        )
        assert result.exit_code == 0, result.stdout
        # Link should be gone.
        assert not link.is_symlink()


# ---------------------------------------------------------------------------
# Top-level cleanup --help
# ---------------------------------------------------------------------------


class TestCleanupHelp:
    def test_help_lists_subcommands(self, runner, db_path):
        result = runner.invoke(
            app, ["--db", str(db_path), "cleanup", "--help"],
        )
        assert result.exit_code == 0
        for sub in ("empty-dirs", "broken-symlinks", "junk"):
            assert sub in result.stdout
