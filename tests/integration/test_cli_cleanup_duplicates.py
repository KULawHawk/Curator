"""Integration tests for `curator cleanup duplicates` CLI (Phase Gamma F7, v0.28)."""

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
    return tmp_path / "curator_dupe_cli.db"


def _isolated_safety(monkeypatch) -> None:
    """Force CleanupService to use a permissive SafetyService."""
    real_init = CleanupService.__init__
    def patched_init(self, safety, *args, **kwargs):
        loose = SafetyService(app_data_paths=[], os_managed_paths=[])
        real_init(self, loose, *args, **kwargs)
    monkeypatch.setattr(CleanupService, "__init__", patched_init)


# ---------------------------------------------------------------------------
# Help + JSON contract
# ---------------------------------------------------------------------------


class TestCleanupDuplicatesHelp:
    def test_subcommand_listed_in_cleanup_help(self, runner, db_path):
        result = runner.invoke(
            app, ["--db", str(db_path), "cleanup", "--help"],
        )
        assert result.exit_code == 0
        assert "duplicates" in result.stdout

    def test_duplicates_help_lists_strategies(self, runner, db_path, strip_ansi):
        result = runner.invoke(
            app, ["--db", str(db_path), "cleanup", "duplicates", "--help"],
        )
        assert result.exit_code == 0
        # v1.7.68: ANSI strip via shared fixture (hoisted from v1.7.62 inline regex).
        output = strip_ansi(result.output)
        for s in ("shortest_path", "longest_path", "oldest", "newest"):
            assert s in output
        assert "--keep-under" in output
        assert "--apply" in output


# ---------------------------------------------------------------------------
# Plan + apply round trip with real scan
# ---------------------------------------------------------------------------


class TestCleanupDuplicatesPlanApply:
    def test_plan_finds_duplicates_after_real_scan(
        self, runner, db_path, tmp_path, monkeypatch
    ):
        _isolated_safety(monkeypatch)
        media = tmp_path / "media"
        (media / "Library").mkdir(parents=True)
        (media / "Downloads").mkdir(parents=True)
        (media / "Backup").mkdir(parents=True)
        # Three identical files (same xxhash3_128).
        identical = b"identical bytes for dedup test - longer to be visible"
        (media / "Library" / "song.mp3").write_bytes(identical)
        (media / "Downloads" / "song copy.mp3").write_bytes(identical)
        (media / "Backup" / "song.mp3").write_bytes(identical)
        # And one unique file that should NOT be flagged.
        (media / "Library" / "other.mp3").write_bytes(b"different content")

        scan = runner.invoke(
            app, ["--db", str(db_path), "scan", "local", str(media)],
        )
        assert scan.exit_code == 0, scan.stdout

        # Plan only.
        result = runner.invoke(
            app,
            ["--json", "--db", str(db_path), "cleanup", "duplicates"],
        )
        assert result.exit_code == 0, result.stdout + (result.stderr or "")
        payload = json.loads(result.stdout)
        # 3-file group => 2 duplicates flagged (the keeper is NOT a finding).
        assert payload["plan"]["count"] == 2
        # All findings share one dupset_id.
        dupsets = {f["details"]["dupset_id"] for f in payload["plan"]["findings"]}
        assert len(dupsets) == 1
        # Plan-only: no files removed yet.
        assert (media / "Library" / "song.mp3").exists()
        assert (media / "Downloads" / "song copy.mp3").exists()
        assert (media / "Backup" / "song.mp3").exists()
        # The unique file isn't in findings.
        flagged_paths = {f["path"] for f in payload["plan"]["findings"]}
        assert str(media / "Library" / "other.mp3") not in flagged_paths

    def test_apply_removes_only_duplicates_keeps_keeper(
        self, runner, db_path, tmp_path, monkeypatch
    ):
        _isolated_safety(monkeypatch)
        media = tmp_path / "media"
        (media / "Library").mkdir(parents=True)
        (media / "Downloads").mkdir(parents=True)
        (media / "Backup" / "Old").mkdir(parents=True)
        identical = b"X" * 100
        # Library path is shortest -> default shortest_path keeps it.
        (media / "Library" / "song.mp3").write_bytes(identical)
        (media / "Downloads" / "song.mp3").write_bytes(identical)
        (media / "Backup" / "Old" / "song.mp3").write_bytes(identical)

        runner.invoke(
            app, ["--db", str(db_path), "scan", "local", str(media)],
        )

        # Apply with --no-trash so behaviour is deterministic regardless of OS trash backend.
        result = runner.invoke(
            app,
            ["--json", "--db", str(db_path), "cleanup", "duplicates",
             "--apply", "--no-trash"],
        )
        assert result.exit_code == 0, result.stdout
        payload = json.loads(result.stdout)
        assert payload["apply"]["deleted_count"] == 2
        assert payload["apply"]["failed_count"] == 0

        # The keeper (shortest path = Library) survives; the others gone.
        assert (media / "Library" / "song.mp3").exists()
        assert not (media / "Downloads" / "song.mp3").exists()
        assert not (media / "Backup" / "Old" / "song.mp3").exists()

    def test_keep_under_flips_keeper(
        self, runner, db_path, tmp_path, monkeypatch
    ):
        _isolated_safety(monkeypatch)
        media = tmp_path / "media"
        (media / "Library" / "deep" / "nested").mkdir(parents=True)
        (media / "Downloads").mkdir(parents=True)
        identical = b"Y" * 50
        # Without --keep-under, shortest_path picks Downloads (shorter path).
        # With --keep-under under Library, the deeper Library file wins.
        (media / "Downloads" / "x.dat").write_bytes(identical)
        (media / "Library" / "deep" / "nested" / "x.dat").write_bytes(identical)

        runner.invoke(
            app, ["--db", str(db_path), "scan", "local", str(media)],
        )

        # First, plan WITHOUT --keep-under -> Downloads is the keeper.
        plain = runner.invoke(
            app,
            ["--json", "--db", str(db_path), "cleanup", "duplicates"],
        )
        plain_payload = json.loads(plain.stdout)
        assert plain_payload["plan"]["count"] == 1
        plain_kept = plain_payload["plan"]["findings"][0]["details"]["kept_path"]
        assert "Downloads" in plain_kept

        # Now plan WITH --keep-under pointing at Library -> Library wins.
        with_under = runner.invoke(
            app,
            ["--json", "--db", str(db_path), "cleanup", "duplicates",
             "--keep-under", str(media / "Library")],
        )
        under_payload = json.loads(with_under.stdout)
        assert under_payload["plan"]["count"] == 1
        kept = under_payload["plan"]["findings"][0]["details"]["kept_path"]
        assert "Library" in kept
        assert "keep_under" in under_payload["plan"]["findings"][0]["details"]["kept_reason"]

    def test_no_duplicates_returns_clean_plan(
        self, runner, db_path, tmp_path, monkeypatch
    ):
        _isolated_safety(monkeypatch)
        media = tmp_path / "unique_only"
        media.mkdir()
        (media / "a.txt").write_bytes(b"alpha")
        (media / "b.txt").write_bytes(b"beta")
        (media / "c.txt").write_bytes(b"gamma")

        runner.invoke(
            app, ["--db", str(db_path), "scan", "local", str(media)],
        )

        result = runner.invoke(
            app,
            ["--json", "--db", str(db_path), "cleanup", "duplicates"],
        )
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["plan"]["count"] == 0
