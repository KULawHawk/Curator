"""Integration tests for OrganizeService MB-enrichment wiring (v0.32)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from curator.models.file import FileEntity
from curator.models.source import SourceConfig
from curator.services.music import MusicMetadata, MusicService
from curator.services.musicbrainz import MusicBrainzMatch
from curator.services.organize import OrganizeService
from curator.services.safety import SafetyService
from curator.storage import CuratorDB
from curator.storage.repositories.file_repo import FileRepository
from curator.storage.repositories.source_repo import SourceRepository


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path):
    db_path = tmp_path / "organize_mb.db"
    database = CuratorDB(db_path)
    database.init()
    yield database


@pytest.fixture
def file_repo(db):
    return FileRepository(db)


@pytest.fixture
def source_repo(db):
    return SourceRepository(db)


@pytest.fixture
def loose_safety():
    return SafetyService(app_data_paths=[], os_managed_paths=[])


def _seed_source(source_repo: SourceRepository, source_id: str = "local") -> None:
    try:
        source_repo.insert(SourceConfig(
            source_id=source_id,
            source_type="local",
            display_name=source_id,
        ))
    except Exception:
        pass


def _seed_file(file_repo: FileRepository, *, path: str) -> FileEntity:
    entity = FileEntity(
        curator_id=uuid4(),
        source_id="local",
        source_path=path,
        size=100,
        mtime=datetime(2024, 1, 1),
        extension=Path(path).suffix.lower() or None,
    )
    file_repo.upsert(entity)
    return entity


def _make_music_service_returning(meta: MusicMetadata) -> MusicService:
    """Build a MusicService whose read_tags always returns ``meta``."""
    svc = MusicService()
    svc.read_tags = MagicMock(return_value=meta)  # type: ignore[method-assign]
    svc.is_audio_file = MagicMock(return_value=True)  # type: ignore[method-assign]
    return svc


# ===========================================================================
# Integration: enrich_mb=True actually fires when conditions met
# ===========================================================================


class TestOrganizePlanMBEnrichment:
    def test_enrichment_fires_for_filename_only_track(
        self, file_repo, source_repo, loose_safety, tmp_path
    ):
        _seed_source(source_repo)
        track_path = str(tmp_path / "06 - Pink Floyd - Comfortably Numb.mp3")
        # File on disk so safety check passes.
        Path(track_path).write_bytes(b"fake mp3")
        _seed_file(file_repo, path=track_path)

        # Music service returns metadata with the _filename_source marker
        # set + missing album/year.
        meta = MusicMetadata(
            artist="Pink Floyd",
            title="Comfortably Numb",
            track_number=6,
        )
        meta.raw["_filename_source"] = "true"
        music = _make_music_service_returning(meta)

        # Mock MB client returns a complete match.
        mb_client = MagicMock()
        mb_client.lookup_recording.return_value = MusicBrainzMatch(
            recording_mbid="rec-mbid-1",
            artist="Pink Floyd",
            title="Comfortably Numb",
            album="The Wall",
            year=1979,
            track_number=6,
            score=98,
        )

        org = OrganizeService(
            file_repo=file_repo,
            safety=loose_safety,
            music=music,
            mb_client=mb_client,
        )
        plan = org.plan(
            source_id="local",
            organize_type="music",
            target_root=tmp_path / "Music",
            enrich_mb=True,
        )

        # MB was called.
        assert mb_client.lookup_recording.call_count == 1
        # The proposed destination uses the enriched album name.
        proposals = list(plan.safe.proposals.values())
        assert len(proposals) == 1
        assert "The Wall" in proposals[0]
        assert "Pink Floyd" in proposals[0]

    def test_enrichment_skips_when_enrich_mb_false(
        self, file_repo, source_repo, loose_safety, tmp_path
    ):
        """Default behavior (no --enrich-mb): MB never called even with mb_client set."""
        _seed_source(source_repo)
        track_path = str(tmp_path / "track.mp3")
        Path(track_path).write_bytes(b"fake")
        _seed_file(file_repo, path=track_path)

        meta = MusicMetadata(artist="X", title="Y")
        meta.raw["_filename_source"] = "true"
        music = _make_music_service_returning(meta)
        mb_client = MagicMock()

        org = OrganizeService(
            file_repo=file_repo,
            safety=loose_safety,
            music=music,
            mb_client=mb_client,
        )
        org.plan(
            source_id="local",
            organize_type="music",
            target_root=tmp_path / "Music",
            enrich_mb=False,  # default
        )
        assert mb_client.lookup_recording.call_count == 0

    def test_enrichment_skips_for_files_with_real_tags(
        self, file_repo, source_repo, loose_safety, tmp_path
    ):
        """Files where mutagen got real tags (no _filename_source marker) are skipped."""
        _seed_source(source_repo)
        track_path = str(tmp_path / "tagged.mp3")
        Path(track_path).write_bytes(b"fake")
        _seed_file(file_repo, path=track_path)

        # Real tags from mutagen \u2014 no _filename_source marker.
        meta = MusicMetadata(artist="X", title="Y", album="Z")
        # Note: NO meta.raw["_filename_source"] = "true"
        music = _make_music_service_returning(meta)
        mb_client = MagicMock()

        org = OrganizeService(
            file_repo=file_repo,
            safety=loose_safety,
            music=music,
            mb_client=mb_client,
        )
        org.plan(
            source_id="local",
            organize_type="music",
            target_root=tmp_path / "Music",
            enrich_mb=True,
        )
        # Even though enrich_mb=True, the file had real tags so MB wasn't called.
        assert mb_client.lookup_recording.call_count == 0

    def test_enrichment_skips_when_mb_client_is_none(
        self, file_repo, source_repo, loose_safety, tmp_path
    ):
        """enrich_mb=True with no mb_client must not crash."""
        _seed_source(source_repo)
        track_path = str(tmp_path / "track.mp3")
        Path(track_path).write_bytes(b"fake")
        _seed_file(file_repo, path=track_path)

        meta = MusicMetadata(artist="X", title="Y")
        meta.raw["_filename_source"] = "true"
        music = _make_music_service_returning(meta)

        org = OrganizeService(
            file_repo=file_repo,
            safety=loose_safety,
            music=music,
            mb_client=None,  # no client
        )
        # Should not raise.
        plan = org.plan(
            source_id="local",
            organize_type="music",
            target_root=tmp_path / "Music",
            enrich_mb=True,
        )
        # And the proposal still happens (just without enrichment).
        assert len(plan.safe.proposals) == 1

    def test_mb_client_exception_does_not_fail_plan(
        self, file_repo, source_repo, loose_safety, tmp_path
    ):
        _seed_source(source_repo)
        track_path = str(tmp_path / "track.mp3")
        Path(track_path).write_bytes(b"fake")
        _seed_file(file_repo, path=track_path)

        meta = MusicMetadata(artist="X", title="Y")
        meta.raw["_filename_source"] = "true"
        music = _make_music_service_returning(meta)
        mb_client = MagicMock()
        mb_client.lookup_recording.side_effect = RuntimeError("network down")

        org = OrganizeService(
            file_repo=file_repo,
            safety=loose_safety,
            music=music,
            mb_client=mb_client,
        )
        # Plan must succeed even though MB raised.
        plan = org.plan(
            source_id="local",
            organize_type="music",
            target_root=tmp_path / "Music",
            enrich_mb=True,
        )
        # MB was attempted.
        assert mb_client.lookup_recording.call_count == 1
        # And the proposal still exists with the un-enriched data.
        assert len(plan.safe.proposals) == 1


# ===========================================================================
# CLI flag validation
# ===========================================================================


class TestEnrichMbCliValidation:
    def test_help_lists_enrich_mb(self, tmp_path):
        from typer.testing import CliRunner
        from curator.cli.main import app
        runner = CliRunner()
        result = runner.invoke(
            app, ["--db", str(tmp_path / "x.db"), "organize", "--help"],
        )
        assert result.exit_code == 0
        assert "--enrich-mb" in result.stdout
        assert "MusicBrainz" in result.stdout

    def test_enrich_mb_requires_mb_contact(self, tmp_path):
        from typer.testing import CliRunner
        from curator.cli.main import app
        runner = CliRunner()
        result = runner.invoke(
            app,
            ["--db", str(tmp_path / "x.db"),
             "organize", "local",
             "--type", "music",
             "--target", str(tmp_path / "out"),
             "--enrich-mb"],
        )
        assert result.exit_code == 2
        # Either stderr or stdout must mention --mb-contact.
        combined = result.stdout + (result.stderr or "")
        assert "mb-contact" in combined.lower() or "contact" in combined.lower()

    def test_enrich_mb_only_with_music_type(self, tmp_path):
        from typer.testing import CliRunner
        from curator.cli.main import app
        runner = CliRunner()
        result = runner.invoke(
            app,
            ["--db", str(tmp_path / "x.db"),
             "organize", "local",
             "--type", "photo",
             "--target", str(tmp_path / "out"),
             "--enrich-mb",
             "--mb-contact", "test@example.com"],
        )
        assert result.exit_code == 2
        combined = result.stdout + (result.stderr or "")
        assert "music" in combined.lower()
