"""Integration tests for `curator organize --type photo` (Phase Gamma F3).

Exercises the full pipeline: scan a tree containing real EXIF-tagged
JPEGs (built programmatically with Pillow, no fixtures needed), run
`organize --type photo --target <dir>`, verify proposals; then test
the full --apply round trip with revert.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from curator.cli.main import app
from curator.services.organize import OrganizeService
from curator.services.safety import SafetyService


pytestmark = pytest.mark.integration


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "curator_photo_cli.db"


def _isolated_safety_env(monkeypatch) -> None:
    """Force tmp_path subtrees to be SAFE on Windows test envs."""
    real_init = OrganizeService.__init__
    def patched_init(self, file_repo, safety, *args, **kwargs):
        loose = SafetyService(app_data_paths=[], os_managed_paths=[])
        real_init(self, file_repo, loose, *args, **kwargs)
    monkeypatch.setattr(OrganizeService, "__init__", patched_init)


def _make_jpeg(path: Path, *, datetime_original: str | None) -> None:
    """Create a 1x1 JPEG with optional EXIF DateTimeOriginal."""
    from PIL import Image
    img = Image.new("RGB", (1, 1), color=(0, 128, 255))
    if datetime_original is not None:
        exif = img.getexif()
        exif[36867] = datetime_original
        img.save(path, format="JPEG", exif=exif)
    else:
        img.save(path, format="JPEG")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestOrganizePhotoCli:
    def test_unknown_type_still_errors(self, runner, db_path, tmp_path):
        # Sanity: --type=video should still error (not silently accepted).
        result = runner.invoke(
            app,
            ["--db", str(db_path), "organize", "local",
             "--type", "video", "--target", str(tmp_path / "lib")],
        )
        assert result.exit_code != 0
        combined = (result.stdout or "") + (result.stderr or "")
        assert "video" in combined.lower() or "unknown" in combined.lower()

    def test_type_music_help_mentions_photo(self, runner, db_path):
        # The --type help text should now mention both options.
        result = runner.invoke(
            app, ["--db", str(db_path), "organize", "--help"],
        )
        assert "music" in result.stdout
        assert "photo" in result.stdout

    def test_plan_with_type_photo_proposes_dated_destinations(
        self, runner, db_path, tmp_path, monkeypatch
    ):
        _isolated_safety_env(monkeypatch)

        media = tmp_path / "photos_in"
        media.mkdir()
        # Real EXIF-tagged JPEGs.
        _make_jpeg(media / "vacation.jpg", datetime_original="2024:03:15 10:00:00")
        _make_jpeg(media / "another.jpg", datetime_original="2024:03:15 11:30:00")

        scan = runner.invoke(
            app, ["--db", str(db_path), "scan", "local", str(media)],
        )
        assert scan.exit_code == 0

        target = tmp_path / "library"
        result = runner.invoke(
            app,
            ["--json", "--db", str(db_path), "organize", "local",
             "--type", "photo", "--target", str(target),
             "--root", str(media), "--show-files"],
        )
        assert result.exit_code == 0, result.stdout + (result.stderr or "")
        payload = json.loads(result.stdout)

        # Both photos should be in SAFE with proposed destinations.
        safe_files = payload["safe"].get("files", [])
        for f in safe_files:
            if f["path"].endswith(".jpg"):
                assert f["proposed_destination"] is not None
                # Path should include 2024 / 2024-03-15.
                dest = Path(f["proposed_destination"])
                assert "2024" in dest.parts
                assert "2024-03-15" in dest.parts

    def test_apply_round_trip_for_photos(
        self, runner, db_path, tmp_path, monkeypatch
    ):
        _isolated_safety_env(monkeypatch)

        media = tmp_path / "in"
        media.mkdir()
        photo_a = media / "IMG_001.jpg"
        photo_b = media / "IMG_002.jpg"
        _make_jpeg(photo_a, datetime_original="2024:06:01 09:00:00")
        _make_jpeg(photo_b, datetime_original="2024:06:01 09:30:00")

        runner.invoke(app, ["--db", str(db_path), "scan", "local", str(media)])

        target = tmp_path / "lib"
        result = runner.invoke(
            app,
            ["--json", "--db", str(db_path), "organize", "local",
             "--type", "photo", "--target", str(target),
             "--apply", "--root", str(media)],
        )
        assert result.exit_code == 0, result.stdout + (result.stderr or "")
        payload = json.loads(result.stdout)
        assert payload["stage"]["mode"] == "apply"
        assert payload["stage"]["moved_count"] == 2

        # Both ended up under target / 2024 / 2024-06-01 /
        final_a = target / "2024" / "2024-06-01" / "IMG_001.jpg"
        final_b = target / "2024" / "2024-06-01" / "IMG_002.jpg"
        assert final_a.exists()
        assert final_b.exists()
        # Originals gone.
        assert not photo_a.exists()
        assert not photo_b.exists()

        # Revert restores both.
        revert = runner.invoke(
            app, ["--db", str(db_path), "organize-revert", str(target)],
        )
        assert revert.exit_code == 0
        assert photo_a.exists()
        assert photo_b.exists()

    def test_no_exif_falls_back_to_mtime_grouping(
        self, runner, db_path, tmp_path, monkeypatch
    ):
        _isolated_safety_env(monkeypatch)

        media = tmp_path / "no_exif"
        media.mkdir()
        no_exif = media / "scan.jpg"
        _make_jpeg(no_exif, datetime_original=None)
        ts = datetime(2015, 8, 20, 12, 0, 0).timestamp()
        os.utime(no_exif, (ts, ts))

        runner.invoke(app, ["--db", str(db_path), "scan", "local", str(media)])

        target = tmp_path / "lib"
        result = runner.invoke(
            app,
            ["--json", "--db", str(db_path), "organize", "local",
             "--type", "photo", "--target", str(target),
             "--root", str(media), "--show-files"],
        )
        assert result.exit_code == 0, result.stdout
        payload = json.loads(result.stdout)
        # The single photo should still get a proposed destination, derived from mtime.
        proposed = next(
            (f["proposed_destination"]
             for f in payload["safe"].get("files", [])
             if f["path"].endswith(".jpg")),
            None,
        )
        assert proposed is not None
        dest = Path(proposed)
        assert "2015" in dest.parts
        assert "2015-08-20" in dest.parts
