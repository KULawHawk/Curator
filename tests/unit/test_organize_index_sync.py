"""Index-sync tests for OrganizeService stage / apply / revert (v0.33).

Closes the phantom-file gap for organize moves. After a move, the
FileEntity's source_path should reflect where the file actually is
on disk -- not where it used to be.

Covers:
    * stage move updates source_path to the staged location
    * apply move updates source_path to the target location
    * revert move resets source_path back to the original
    * find_by_path on the OLD path returns None after move
    * find_by_path on the NEW path returns the updated entity
    * MOVED outcome triggers sync; SKIPPED / FAILED do not
    * update() exception is swallowed (best-effort; move still succeeds)
    * get() exception is swallowed
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest

from curator.models.file import FileEntity
from curator.models.source import SourceConfig
from curator.services.music import MusicMetadata, MusicService
from curator.services.organize import (
    OrganizeService,
    StageOutcome,
)
from curator.services.safety import SafetyService
from curator.storage import CuratorDB
from curator.storage.repositories.file_repo import FileRepository
from curator.storage.repositories.source_repo import SourceRepository


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path):
    db_path = tmp_path / "organize_index_sync.db"
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
        size=4,
        mtime=datetime(2024, 1, 1),
        extension=Path(path).suffix.lower() or None,
    )
    file_repo.upsert(entity)
    return entity


def _make_music_service_returning(meta: MusicMetadata) -> MusicService:
    """MusicService whose read_tags returns ``meta`` and is_audio_file returns True."""
    svc = MusicService()
    svc.read_tags = MagicMock(return_value=meta)  # type: ignore[method-assign]
    svc.is_audio_file = MagicMock(return_value=True)  # type: ignore[method-assign]
    return svc


# ===========================================================================
# Stage / apply update source_path
# ===========================================================================


class TestStageMoveUpdatesIndex:
    def test_apply_updates_source_path_to_target(
        self, file_repo, source_repo, loose_safety, tmp_path
    ):
        """Headline test: after apply, FileEntity.source_path = target location."""
        _seed_source(source_repo)
        original = tmp_path / "library" / "untagged.mp3"
        original.parent.mkdir(parents=True)
        original.write_bytes(b"fake")

        entity = _seed_file(file_repo, path=str(original))
        original_id = entity.curator_id

        # Music service returns full tags so propose_destination produces
        # a real proposal under target_root.
        music = _make_music_service_returning(MusicMetadata(
            artist="Pink Floyd", album="The Wall",
            title="Comfortably Numb", track_number=6,
        ))

        target = tmp_path / "Music"
        org = OrganizeService(
            file_repo=file_repo, safety=loose_safety, music=music,
        )
        plan = org.plan(
            source_id="local", organize_type="music", target_root=target,
        )
        assert len(plan.safe.proposals) == 1, "plan should propose one move"

        report = org.apply(plan)
        assert report.moved_count == 1
        assert report.failed_count == 0

        # FileEntity now points at the new location.
        updated = file_repo.get(original_id)
        assert updated is not None
        assert updated.deleted_at is None  # not deleted, just moved
        # The new source_path should contain the target dir + canonical structure.
        assert "Music" in updated.source_path
        assert "Pink Floyd" in updated.source_path
        assert "The Wall" in updated.source_path
        # And NOT the original path anymore.
        assert updated.source_path != str(original)

    def test_find_by_path_works_for_new_path_after_apply(
        self, file_repo, source_repo, loose_safety, tmp_path
    ):
        """After apply, find_by_path(NEW path) returns the entity."""
        _seed_source(source_repo)
        original = tmp_path / "library" / "x.mp3"
        original.parent.mkdir(parents=True)
        original.write_bytes(b"fake")

        entity = _seed_file(file_repo, path=str(original))

        music = _make_music_service_returning(MusicMetadata(
            artist="Artist", album="Album", title="Title", track_number=1,
        ))
        target = tmp_path / "Music"
        org = OrganizeService(
            file_repo=file_repo, safety=loose_safety, music=music,
        )
        plan = org.plan(
            source_id="local", organize_type="music", target_root=target,
        )
        report = org.apply(plan)
        new_path = report.moves[0].staged

        # find_by_path on the new path returns the same entity.
        found = file_repo.find_by_path("local", new_path)
        assert found is not None
        assert found.curator_id == entity.curator_id

    def test_find_by_path_old_path_returns_none_after_apply(
        self, file_repo, source_repo, loose_safety, tmp_path
    ):
        """After apply, find_by_path(OLD path) returns None (no phantom)."""
        _seed_source(source_repo)
        original = tmp_path / "library" / "y.mp3"
        original.parent.mkdir(parents=True)
        original.write_bytes(b"fake")

        _seed_file(file_repo, path=str(original))

        music = _make_music_service_returning(MusicMetadata(
            artist="A", album="B", title="C", track_number=2,
        ))
        target = tmp_path / "Music"
        org = OrganizeService(
            file_repo=file_repo, safety=loose_safety, music=music,
        )
        plan = org.plan(
            source_id="local", organize_type="music", target_root=target,
        )
        org.apply(plan)

        # OLD path should not return any entity.
        phantom = file_repo.find_by_path("local", str(original))
        assert phantom is None, "old path should not be queryable after move"

    def test_stage_mode_also_updates_source_path(
        self, file_repo, source_repo, loose_safety, tmp_path
    ):
        """Stage mode (not just apply) syncs the index too."""
        _seed_source(source_repo)
        original = tmp_path / "library" / "z.mp3"
        original.parent.mkdir(parents=True)
        original.write_bytes(b"fake")

        entity = _seed_file(file_repo, path=str(original))

        music = _make_music_service_returning(MusicMetadata(
            artist="A", album="B", title="C", track_number=3,
        ))
        target = tmp_path / "Music"
        stage_root = tmp_path / "Staging"
        org = OrganizeService(
            file_repo=file_repo, safety=loose_safety, music=music,
        )
        plan = org.plan(
            source_id="local", organize_type="music", target_root=target,
        )
        report = org.stage(plan, stage_root=stage_root)
        assert report.moved_count == 1

        updated = file_repo.get(entity.curator_id)
        assert updated is not None
        assert "Staging" in updated.source_path
        assert updated.source_path == report.moves[0].staged


# ===========================================================================
# Revert restores source_path
# ===========================================================================


class TestRevertMoveUpdatesIndex:
    def test_revert_resets_source_path_to_original(
        self, file_repo, source_repo, loose_safety, tmp_path
    ):
        _seed_source(source_repo)
        original = tmp_path / "library" / "v.mp3"
        original.parent.mkdir(parents=True)
        original.write_bytes(b"fake")

        entity = _seed_file(file_repo, path=str(original))
        original_path_str = str(original)

        music = _make_music_service_returning(MusicMetadata(
            artist="A", album="B", title="C", track_number=4,
        ))
        target = tmp_path / "Music"
        stage_root = tmp_path / "Staging"
        org = OrganizeService(
            file_repo=file_repo, safety=loose_safety, music=music,
        )
        plan = org.plan(
            source_id="local", organize_type="music", target_root=target,
        )
        org.stage(plan, stage_root=stage_root)

        # After stage, the entity points at the staged location.
        staged_entity = file_repo.get(entity.curator_id)
        assert "Staging" in staged_entity.source_path

        # Revert.
        revert_report = org.revert_stage(stage_root)
        assert revert_report.restored_count == 1

        # Now the entity points back at the original path.
        reverted = file_repo.get(entity.curator_id)
        assert reverted is not None
        assert reverted.source_path == original_path_str


# ===========================================================================
# Defensive: only MOVED triggers sync
# ===========================================================================


class TestSyncOnlyOnMoved:
    def test_skipped_no_proposal_does_not_update(
        self, file_repo, source_repo, loose_safety, tmp_path
    ):
        """A SAFE file with no proposal isn't moved \u2014 don't sync."""
        _seed_source(source_repo)
        original = tmp_path / "library" / "no_proposal.mp3"
        original.parent.mkdir(parents=True)
        original.write_bytes(b"fake")

        entity = _seed_file(file_repo, path=str(original))

        # Music service returns metadata with NO useful tags so no proposal.
        empty_meta = MusicMetadata()
        music = _make_music_service_returning(empty_meta)

        target = tmp_path / "Music"
        stage_root = tmp_path / "Staging"
        org = OrganizeService(
            file_repo=file_repo, safety=loose_safety, music=music,
        )
        plan = org.plan(
            source_id="local", organize_type="music", target_root=target,
        )
        report = org.stage(plan, stage_root=stage_root)

        # Either NO outcomes (file went to safe but had no proposal,
        # so it was skipped) or one SKIPPED_NO_PROPOSAL outcome.
        moved = [m for m in report.moves if m.outcome == StageOutcome.MOVED]
        assert moved == [], "no proposal => no move"

        # Entity still at original path.
        unchanged = file_repo.get(entity.curator_id)
        assert unchanged.source_path == str(original)

    def test_skipped_collision_does_not_update(
        self, file_repo, source_repo, loose_safety, tmp_path
    ):
        _seed_source(source_repo)
        original = tmp_path / "library" / "x.mp3"
        original.parent.mkdir(parents=True)
        original.write_bytes(b"fake")

        entity = _seed_file(file_repo, path=str(original))

        music = _make_music_service_returning(MusicMetadata(
            artist="A", album="B", title="C", track_number=1,
        ))
        target = tmp_path / "Music"
        org = OrganizeService(
            file_repo=file_repo, safety=loose_safety, music=music,
        )
        plan = org.plan(
            source_id="local", organize_type="music", target_root=target,
        )

        # Pre-create the destination so the move collides.
        dest = Path(plan.safe.proposals[str(entity.curator_id)])
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"already here")

        report = org.apply(plan)
        # A collision should be recorded; no MOVED outcomes.
        collisions = [m for m in report.moves if m.outcome == StageOutcome.SKIPPED_COLLISION]
        assert len(collisions) == 1
        moved = [m for m in report.moves if m.outcome == StageOutcome.MOVED]
        assert moved == []

        # Entity unchanged.
        unchanged = file_repo.get(entity.curator_id)
        assert unchanged.source_path == str(original)


# ===========================================================================
# Defensive: failures don't crash the move
# ===========================================================================


class TestSyncDefensive:
    def test_update_exception_does_not_fail_move(
        self, source_repo, loose_safety, tmp_path
    ):
        """If file_repo.update raises, the on-disk move still succeeds."""
        _seed_source(source_repo)
        original = tmp_path / "library" / "x.mp3"
        original.parent.mkdir(parents=True)
        original.write_bytes(b"fake")

        # MagicMock file_repo: get returns a real-looking entity, but
        # update raises.
        cid = uuid4()
        fake_entity = FileEntity(
            curator_id=cid,
            source_id="local",
            source_path=str(original),
            size=4,
            mtime=datetime(2024, 1, 1),
            extension=".mp3",
        )

        bad_repo = MagicMock()
        bad_repo.query.return_value = [fake_entity]
        bad_repo.get.return_value = fake_entity
        bad_repo.update.side_effect = RuntimeError("DB locked")

        music = _make_music_service_returning(MusicMetadata(
            artist="A", album="B", title="C", track_number=1,
        ))
        target = tmp_path / "Music"
        org = OrganizeService(
            file_repo=bad_repo, safety=loose_safety, music=music,
        )
        plan = org.plan(
            source_id="local", organize_type="music", target_root=target,
        )
        report = org.apply(plan)

        # The on-disk move succeeded even though update raised.
        assert report.moved_count == 1
        assert report.failed_count == 0
        # And the file IS at its new location on disk.
        assert not original.exists()
        moved_to = Path(report.moves[0].staged)
        assert moved_to.exists()
        # And update WAS attempted (proves the exception path was exercised).
        assert bad_repo.update.call_count == 1

    def test_get_exception_does_not_fail_move(
        self, source_repo, loose_safety, tmp_path
    ):
        """If file_repo.get raises during sync, the on-disk move still succeeds."""
        _seed_source(source_repo)
        original = tmp_path / "library" / "x.mp3"
        original.parent.mkdir(parents=True)
        original.write_bytes(b"fake")

        cid = uuid4()
        fake_entity = FileEntity(
            curator_id=cid,
            source_id="local",
            source_path=str(original),
            size=4,
            mtime=datetime(2024, 1, 1),
            extension=".mp3",
        )

        # The query in plan() needs to return an entity, but the get()
        # call inside _sync_index_after_move raises.
        bad_repo = MagicMock()
        bad_repo.query.return_value = [fake_entity]
        bad_repo.get.side_effect = RuntimeError("DB unreachable")

        music = _make_music_service_returning(MusicMetadata(
            artist="A", album="B", title="C", track_number=1,
        ))
        target = tmp_path / "Music"
        org = OrganizeService(
            file_repo=bad_repo, safety=loose_safety, music=music,
        )
        plan = org.plan(
            source_id="local", organize_type="music", target_root=target,
        )
        report = org.apply(plan)

        assert report.moved_count == 1
        assert report.failed_count == 0
        # update was NOT called (we never got past the failing get).
        assert bad_repo.update.call_count == 0
