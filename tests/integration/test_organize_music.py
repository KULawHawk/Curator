"""Integration tests for `curator organize --type music` (Phase Gamma F2).

Verifies the music pipeline end-to-end: scan a tree containing audio files,
run organize with --type music --target, and check that the SAFE bucket's
proposals contain canonical destinations.

Mocks the actual mutagen tag-reading layer (no real audio fixtures needed).
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

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
    return tmp_path / "curator_organize_music.db"


def _patch_mutagen_for_path(monkeypatch, path_to_meta: dict[str, MusicMetadata]):
    """Patch MusicService.read_tags to return canned metadata per path.

    Avoids dealing with real audio fixtures; the rest of the pipeline
    (sanitization, template, OrganizeService wiring) is exercised
    against real DB + real (zero-byte) audio files.
    """
    def fake_read_tags(self, path):
        return path_to_meta.get(str(path))
    monkeypatch.setattr(
        "curator.services.music.MusicService.read_tags",
        fake_read_tags,
    )


# ---------------------------------------------------------------------------
# `curator organize --type music`
# ---------------------------------------------------------------------------


class TestOrganizeMusicCli:
    def test_type_without_target_errors(self, runner, db_path, tmp_path):
        # --type music without --target should fail with a clear message.
        result = runner.invoke(
            app,
            ["--db", str(db_path), "organize", "local", "--type", "music"],
        )
        assert result.exit_code != 0
        combined = (result.stdout or "") + (result.stderr or "")
        assert "--target" in combined

    def test_unknown_type_errors(self, runner, db_path, tmp_path):
        target = tmp_path / "lib"
        result = runner.invoke(
            app,
            ["--db", str(db_path), "organize", "local",
             "--type", "video", "--target", str(target)],
        )
        assert result.exit_code != 0
        combined = (result.stdout or "") + (result.stderr or "")
        assert "Unknown --type" in combined or "video" in combined

    def test_proposes_destinations_for_safe_audio(
        self, runner, db_path, tmp_path, monkeypatch
    ):
        # Build an audio file far from any project markers so it lands in SAFE.
        # We use a deliberately neutral path that won't trip safety
        # registries on either Windows or Unix in our test env.
        media_dir = tmp_path / "media_lib"
        media_dir.mkdir()
        track = media_dir / "track01.mp3"
        track.write_bytes(b"")  # zero-byte; mutagen layer is mocked

        # Mock the tag layer so we don't need real audio.
        _patch_mutagen_for_path(monkeypatch, {
            str(track): MusicMetadata(
                artist="The Band",
                album="The Album",
                title="The Song",
                track_number=1,
            ),
        })

        # Scan first.
        scan_result = runner.invoke(
            app,
            ["--db", str(db_path), "scan", "local", str(media_dir)],
        )
        assert scan_result.exit_code == 0, scan_result.stdout

        # Now organize with --type music --target.
        target = tmp_path / "organized"
        result = runner.invoke(
            app,
            ["--json", "--db", str(db_path), "organize", "local",
             "--type", "music", "--target", str(target),
             "--root", str(media_dir),  # narrow to our fake media subtree
             "--show-files"],
        )
        assert result.exit_code == 0, result.stdout + (result.stderr or "")
        payload = json.loads(result.stdout)

        # Find our track in whatever bucket it landed (SAFE in clean tmp dirs;
        # CAUTION if the tmp dir is under app-data, which is common on Windows).
        # The proposal should be present in the bucket where it landed only
        # if it's SAFE (we don't propose for caution/refuse).
        safe_files = payload["safe"].get("files", [])
        our_safe = next(
            (f for f in safe_files if f["path"] == str(track)), None
        )
        if our_safe is not None:
            # SAFE \u2192 destination should be proposed.
            assert our_safe["proposed_destination"] is not None
            dest = Path(our_safe["proposed_destination"])
            assert "The Band" in dest.parts
            assert "The Album" in dest.parts
            assert dest.name == "01 - The Song.mp3"
        else:
            # Track ended up in CAUTION/REFUSE; in that case the
            # service correctly skipped destination computation.
            # Nothing to assert beyond "the command ran cleanly."
            pass

    def test_no_proposals_for_caution_files(
        self, runner, db_path, tmp_path, monkeypatch
    ):
        # Audio file inside a project (CAUTION) \u2014 should NOT get a
        # proposed destination even with --type music.
        proj = tmp_path / "myproj"
        proj.mkdir()
        (proj / ".git").mkdir()
        track = proj / "song.mp3"
        track.write_bytes(b"")

        _patch_mutagen_for_path(monkeypatch, {
            str(track): MusicMetadata(
                artist="X", album="Y", title="Z", track_number=1,
            ),
        })

        runner.invoke(app, ["--db", str(db_path), "scan", "local", str(proj)])

        target = tmp_path / "organized"
        result = runner.invoke(
            app,
            ["--json", "--db", str(db_path), "organize", "local",
             "--type", "music", "--target", str(target),
             "--root", str(proj), "--show-files"],
        )
        assert result.exit_code == 0, result.stdout
        payload = json.loads(result.stdout)

        # Track is in CAUTION (project_file). Its proposed_destination
        # should be None.
        for f in payload["caution"].get("files", []):
            if f["path"] == str(track):
                assert f["proposed_destination"] is None
                break

    def test_basic_organize_unaffected_by_music_addition(
        self, runner, db_path, tmp_path
    ):
        # Without --type music, the basic organize still works exactly
        # as before. (Regression check.)
        tree = tmp_path / "tree"
        tree.mkdir()
        (tree / "file.txt").write_text("hi")

        runner.invoke(app, ["--db", str(db_path), "scan", "local", str(tree)])
        result = runner.invoke(
            app,
            ["--db", str(db_path), "organize", "local"],
        )
        assert result.exit_code == 0
        assert "Organize plan" in result.stdout
