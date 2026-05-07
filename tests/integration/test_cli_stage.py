"""CLI integration tests for `curator organize --stage` + `organize-revert`.

End-to-end exercise of the v0.22 stage/revert UX. Mocks the mutagen
tag-reader so we don't need real audio fixtures, but stage/revert
moves are real shutil operations on tmp_path.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from curator.cli.main import app
from curator.services.music import MusicMetadata


pytestmark = pytest.mark.integration


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "curator_stage_cli.db"


def _patch_tags(monkeypatch, mapping: dict[str, MusicMetadata]) -> None:
    """Make MusicService.read_tags return preset MusicMetadata for each path."""
    def fake_read_tags(self, path):
        return mapping.get(str(path))
    monkeypatch.setattr(
        "curator.services.music.MusicService.read_tags",
        fake_read_tags,
    )


def _isolated_safety_env(tmp_path, monkeypatch) -> None:
    """Force tmp_path subtrees to be SAFE.

    On Windows, %TEMP% often sits under %LOCALAPPDATA%, which makes
    SafetyService classify everything in tmp_path as APP_DATA \u2192 CAUTION.
    For these stage tests we need files to land in SAFE; clear those
    registries via a SafetyService swap.
    """
    from curator.services.safety import SafetyService
    from curator.services.organize import OrganizeService

    real_init = OrganizeService.__init__

    def patched_init(self, file_repo, safety, *args, **kwargs):
        # Replace the configured SafetyService with one that has empty
        # app-data + os-managed registries, so tmp_path classifies as SAFE.
        # **kwargs is future-proof: as OrganizeService gains new optional
        # collaborators (music, photo, etc.), this stays correct.
        loose = SafetyService(app_data_paths=[], os_managed_paths=[])
        real_init(self, file_repo, loose, *args, **kwargs)

    monkeypatch.setattr(OrganizeService, "__init__", patched_init)


# ---------------------------------------------------------------------------
# CLI: --stage validation
# ---------------------------------------------------------------------------


class TestStageValidation:
    def test_stage_without_type_errors(self, runner, db_path, tmp_path):
        result = runner.invoke(
            app,
            ["--db", str(db_path), "organize", "local",
             "--stage", str(tmp_path / "stage")],
        )
        assert result.exit_code != 0
        combined = (result.stdout or "") + (result.stderr or "")
        assert "--stage" in combined and "--type" in combined

    def test_stage_without_target_errors(self, runner, db_path, tmp_path):
        result = runner.invoke(
            app,
            ["--db", str(db_path), "organize", "local",
             "--type", "music",
             "--stage", str(tmp_path / "stage")],
        )
        # Will error at --type/--target check first \u2014 that's fine.
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# CLI: end-to-end stage + revert round trip
# ---------------------------------------------------------------------------


class TestStageRevertRoundTrip:
    def test_stage_then_revert_restores_files(
        self, runner, db_path, tmp_path, monkeypatch
    ):
        _isolated_safety_env(tmp_path, monkeypatch)

        media = tmp_path / "music_in"
        media.mkdir()
        track1 = media / "01-foo.mp3"
        track2 = media / "02-bar.mp3"
        track1.write_bytes(b"audio1")
        track2.write_bytes(b"audio2")

        _patch_tags(monkeypatch, {
            str(track1): MusicMetadata(
                artist="Foo Band", album="First", title="Foo", track_number=1,
            ),
            str(track2): MusicMetadata(
                artist="Foo Band", album="First", title="Bar", track_number=2,
            ),
        })

        # Scan
        scan = runner.invoke(
            app, ["--db", str(db_path), "scan", "local", str(media)],
        )
        assert scan.exit_code == 0, scan.stdout

        target = tmp_path / "library"
        stage = tmp_path / "staging"

        # Stage
        result = runner.invoke(
            app,
            ["--json", "--db", str(db_path), "organize", "local",
             "--type", "music", "--target", str(target),
             "--stage", str(stage),
             "--root", str(media)],
        )
        assert result.exit_code == 0, result.stdout + (result.stderr or "")
        payload = json.loads(result.stdout)
        assert "stage" in payload
        assert payload["stage"]["moved_count"] == 2
        assert payload["stage"]["failed_count"] == 0

        # Originals are gone, stage tree exists with correct layout.
        assert not track1.exists()
        assert not track2.exists()
        assert (stage / "Foo Band" / "First" / "01 - Foo.mp3").exists()
        assert (stage / "Foo Band" / "First" / "02 - Bar.mp3").exists()

        # Manifest written.
        manifest = stage / ".curator_stage_manifest.json"
        assert manifest.exists()
        entries = json.loads(manifest.read_text())
        assert len(entries) == 2

        # Revert
        revert = runner.invoke(
            app,
            ["--json", "--db", str(db_path), "organize-revert", str(stage)],
        )
        assert revert.exit_code == 0, revert.stdout + (revert.stderr or "")
        revert_payload = json.loads(revert.stdout)
        assert revert_payload["restored_count"] == 2
        assert revert_payload["skipped_count"] == 0

        # Originals restored, manifest gone.
        assert track1.exists() and track1.read_bytes() == b"audio1"
        assert track2.exists() and track2.read_bytes() == b"audio2"
        assert not manifest.exists()

    def test_revert_unknown_dir_errors_cleanly(
        self, runner, db_path, tmp_path
    ):
        empty = tmp_path / "no_manifest_here"
        empty.mkdir()
        result = runner.invoke(
            app, ["--db", str(db_path), "organize-revert", str(empty)],
        )
        assert result.exit_code != 0
        combined = (result.stdout or "") + (result.stderr or "")
        assert "manifest" in combined.lower()

    def test_human_render_shows_stage_summary(
        self, runner, db_path, tmp_path, monkeypatch
    ):
        _isolated_safety_env(tmp_path, monkeypatch)

        media = tmp_path / "in"
        media.mkdir()
        t = media / "x.mp3"
        t.write_bytes(b"a")
        _patch_tags(monkeypatch, {
            str(t): MusicMetadata(artist="A", album="B", title="C", track_number=1),
        })

        runner.invoke(app, ["--db", str(db_path), "scan", "local", str(media)])

        target = tmp_path / "lib"
        stage = tmp_path / "st"
        result = runner.invoke(
            app,
            ["--db", str(db_path), "organize", "local",
             "--type", "music", "--target", str(target),
             "--stage", str(stage), "--root", str(media)],
        )
        assert result.exit_code == 0, result.stdout
        # Human renderer printed both plan + stage sections.
        assert "Organize plan" in result.stdout
        assert "Stage" in result.stdout
        assert "moved=" in result.stdout
