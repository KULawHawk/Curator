"""Tests for v1.0.0a1 MigrationService Phase 1 (same-source local→local).

Strategy:
  * Build a fully-wired CuratorRuntime against a temp DB with REAL files
    on disk (so shutil.copy2, hash recomputation, and trash all exercise
    the real OS layer).
  * Test ``plan()`` directly: walks files, partitions by safety, computes
    dst paths, applies extension filter.
  * Test ``apply()`` directly: every code path (MOVED / SKIPPED_NOT_SAFE
    / SKIPPED_COLLISION / SKIPPED_DB_GUARD / HASH_MISMATCH / FAILED).
  * Test the headline invariant: after a successful move, the FileEntity's
    ``source_path`` equals the new location, ``curator_id`` is unchanged,
    the source file no longer exists at the original location, and the
    dst file exists with matching xxhash3_128.

Key invariants we prove:
  * curator_id constancy across the move.
  * Hash-verify-before-move discipline: hash mismatch leaves source intact.
  * Lineage / bundle membership rows are NOT touched (still point at the
    same curator_id).
  * Audit log records the move with the right actor + action.
  * DB-guard skips Curator's own DB file.
"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest
import xxhash

from curator.cli.runtime import build_runtime
from curator.config import Config
from curator.models.bundle import BundleEntity, BundleMembership
from curator.models.file import FileEntity
from curator.models.lineage import LineageEdge, LineageKind
from curator.models.source import SourceConfig
from curator.services.migration import (
    MigrationOutcome,
    MigrationPlan,
    MigrationMove,
    MigrationReport,
    MigrationService,
    _compute_dst_path,
    _xxhash3_128_of_file,
)
from curator.services.safety import SafetyLevel


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _seed_real_file(rt, path: Path, content: bytes = b"hello world\n") -> FileEntity:
    """Create a real file on disk + index it. Returns the FileEntity with
    real xxhash3_128 cached."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    h = xxhash.xxh3_128(content).hexdigest()
    e = FileEntity(
        curator_id=uuid4(),
        source_id="local",
        source_path=str(path),
        size=len(content),
        mtime=datetime.fromtimestamp(path.stat().st_mtime),
        extension=path.suffix.lower(),
        xxhash3_128=h,
    )
    rt.file_repo.upsert(e)
    return e


@pytest.fixture
def migration_runtime(tmp_path):
    """A real CuratorRuntime with the local source registered."""
    db_path = tmp_path / "migration.db"
    cfg = Config.load()
    rt = build_runtime(
        config=cfg, db_path_override=db_path,
        json_output=False, no_color=True, verbosity=0,
    )
    try:
        rt.source_repo.insert(SourceConfig(
            source_id="local", source_type="local", display_name="Local",
        ))
    except Exception:
        pass
    return rt


@pytest.fixture
def migration_service(migration_runtime):
    """A MigrationService whose safety check is stubbed to return SAFE.

    Most tests are about migration mechanics, not safety semantics.
    Pytest's tmp_path lives under ``%LOCALAPPDATA%\\Temp`` on Windows,
    which the real SafetyService correctly flags as CAUTION (app-data).
    To test migration mechanics with SAFE files we stub the safety
    check; tests that EXPLICITLY exercise CAUTION/REFUSE behavior use
    a separate fixture that preserves real safety logic.
    """
    from curator.services.safety import SafetyReport, SafetyLevel as SL
    rt = migration_runtime
    rt.safety.check_path = lambda p, **kw: SafetyReport(path=p, level=SL.SAFE)
    return MigrationService(
        file_repo=rt.file_repo,
        safety=rt.safety,
        audit=rt.audit_repo,
    )


@pytest.fixture
def migration_service_real_safety(migration_runtime):
    """A MigrationService with the unmodified SafetyService, for tests
    that need real CAUTION/REFUSE detection."""
    rt = migration_runtime
    return MigrationService(
        file_repo=rt.file_repo,
        safety=rt.safety,
        audit=rt.audit_repo,
    )


@pytest.fixture
def src_tree(tmp_path, migration_runtime):
    """A small library at tmp_path/library/ with 3 indexed files."""
    rt = migration_runtime
    src_root = tmp_path / "library"
    files = []
    files.append(_seed_real_file(rt, src_root / "song1.mp3", b"track1 bytes" * 100))
    files.append(_seed_real_file(rt, src_root / "song2.mp3", b"track2 bytes" * 50))
    files.append(_seed_real_file(rt, src_root / "Photos" / "img.jpg", b"jpeg bytes\xff" * 200))
    return rt, src_root, files


# ---------------------------------------------------------------------------
# _compute_dst_path helper
# ---------------------------------------------------------------------------


class TestComputeDstPath:
    def test_basic_subpath_preserved(self):
        out = _compute_dst_path(
            "/old/library/song.mp3",
            "/old/library",
            "/new/library",
        )
        assert Path(out) == Path("/new/library/song.mp3")

    def test_deep_subpath_preserved(self):
        out = _compute_dst_path(
            "/old/lib/Pink Floyd/Wall/01.mp3",
            "/old/lib",
            "/new/lib",
        )
        assert Path(out) == Path("/new/lib/Pink Floyd/Wall/01.mp3")

    def test_src_not_under_root_returns_none(self):
        out = _compute_dst_path(
            "/elsewhere/song.mp3",
            "/old/library",
            "/new/library",
        )
        assert out is None


# ---------------------------------------------------------------------------
# _xxhash3_128_of_file helper
# ---------------------------------------------------------------------------


class TestXxhashOfFile:
    def test_matches_in_memory_hash(self, tmp_path):
        content = b"some bytes for hashing\n" * 1000
        p = tmp_path / "f.bin"
        p.write_bytes(content)
        expected = xxhash.xxh3_128(content).hexdigest()
        assert _xxhash3_128_of_file(p) == expected

    def test_empty_file(self, tmp_path):
        p = tmp_path / "empty.bin"
        p.write_bytes(b"")
        assert _xxhash3_128_of_file(p) == xxhash.xxh3_128(b"").hexdigest()

    def test_streaming_handles_files_larger_than_chunk(self, tmp_path):
        # 200 KB file: forces multiple chunks
        content = b"x" * (200 * 1024)
        p = tmp_path / "big.bin"
        p.write_bytes(content)
        expected = xxhash.xxh3_128(content).hexdigest()
        assert _xxhash3_128_of_file(p) == expected


# ---------------------------------------------------------------------------
# MigrationPlan dataclass
# ---------------------------------------------------------------------------


class TestMigrationPlanDataclass:
    def test_count_properties_with_mixed_safety(self):
        moves = [
            MigrationMove(uuid4(), "/a", "/b", SafetyLevel.SAFE, 100, "h"),
            MigrationMove(uuid4(), "/c", "/d", SafetyLevel.SAFE, 200, "h"),
            MigrationMove(uuid4(), "/e", "/f", SafetyLevel.CAUTION, 50, "h"),
            MigrationMove(uuid4(), "/g", "/h", SafetyLevel.REFUSE, 25, "h"),
        ]
        plan = MigrationPlan("local", "/src", "local", "/dst", moves)
        assert plan.total_count == 4
        assert plan.safe_count == 2
        assert plan.caution_count == 1
        assert plan.refuse_count == 1
        assert plan.planned_bytes == 300  # only SAFE bytes counted


# ---------------------------------------------------------------------------
# MigrationService.plan()
# ---------------------------------------------------------------------------


class TestPlan:
    def test_walks_three_files(self, src_tree, migration_service, tmp_path):
        rt, src_root, files = src_tree
        dst_root = tmp_path / "library_new"
        plan = migration_service.plan(
            src_source_id="local",
            src_root=str(src_root),
            dst_root=str(dst_root),
        )
        assert plan.total_count == 3
        # All three should be SAFE (tmp_path is not under any project marker)
        assert plan.safe_count == 3
        # Destinations preserve subpaths
        moves_by_basename = {Path(m.src_path).name: m for m in plan.moves}
        assert "song1.mp3" in moves_by_basename
        assert Path(moves_by_basename["song1.mp3"].dst_path) == dst_root / "song1.mp3"
        assert Path(moves_by_basename["img.jpg"].dst_path) == dst_root / "Photos" / "img.jpg"

    def test_extension_filter_narrows(self, src_tree, migration_service, tmp_path):
        rt, src_root, files = src_tree
        dst_root = tmp_path / "library_new"
        plan = migration_service.plan(
            src_source_id="local",
            src_root=str(src_root),
            dst_root=str(dst_root),
            extensions=[".mp3"],
        )
        assert plan.total_count == 2
        assert all(m.src_path.endswith(".mp3") for m in plan.moves)

    def test_extension_filter_case_insensitive(self, src_tree, migration_service, tmp_path):
        rt, src_root, files = src_tree
        dst_root = tmp_path / "library_new"
        plan = migration_service.plan(
            src_source_id="local",
            src_root=str(src_root),
            dst_root=str(dst_root),
            extensions=["MP3", "JPG"],  # uppercase, no leading dot
        )
        assert plan.total_count == 3

    def test_empty_src_returns_empty_plan(self, migration_runtime, migration_service, tmp_path):
        empty_root = tmp_path / "empty_library"
        empty_root.mkdir()
        plan = migration_service.plan(
            src_source_id="local",
            src_root=str(empty_root),
            dst_root=str(tmp_path / "out"),
        )
        assert plan.total_count == 0

    def test_dst_inside_src_raises(self, src_tree, migration_service, tmp_path):
        rt, src_root, files = src_tree
        # Try to migrate into a sub-directory of the source: would loop.
        with pytest.raises(ValueError, match="must not be inside"):
            migration_service.plan(
                src_source_id="local",
                src_root=str(src_root),
                dst_root=str(src_root / "nested"),
            )

    def test_plan_sets_src_xxhash_from_cache(self, src_tree, migration_service, tmp_path):
        rt, src_root, files = src_tree
        dst_root = tmp_path / "out"
        plan = migration_service.plan(
            src_source_id="local",
            src_root=str(src_root),
            dst_root=str(dst_root),
        )
        # All cached hashes should be carried into the plan
        for m in plan.moves:
            assert m.src_xxhash is not None and len(m.src_xxhash) == 32

    def test_caution_files_appear_in_plan_but_marked_caution(
        self, migration_runtime, migration_service_real_safety, tmp_path
    ):
        """With REAL safety logic: tmp_path is under %LOCALAPPDATA% on
        Windows, so all files there are CAUTION (app-data verdict). They
        appear in the plan but with safety_level=CAUTION so apply()
        skips them."""
        rt = migration_runtime
        src_root = tmp_path / "project"
        # Make it a git repo so SafetyService also flags files inside as CAUTION
        # (the test passes either way -- both app-data prefix and project_file
        # markers route to CAUTION)
        (src_root / ".git").mkdir(parents=True)
        (src_root / ".git" / "config").write_text("[core]\n")
        _seed_real_file(rt, src_root / "main.py", b"print('hi')\n")

        plan = migration_service_real_safety.plan(
            src_source_id="local",
            src_root=str(src_root),
            dst_root=str(tmp_path / "out"),
        )
        # main.py should be flagged CAUTION (either by project_file or app_data)
        py_moves = [m for m in plan.moves if m.src_path.endswith("main.py")]
        assert len(py_moves) == 1
        assert py_moves[0].safety_level == SafetyLevel.CAUTION


# ---------------------------------------------------------------------------
# MigrationService.apply()
# ---------------------------------------------------------------------------


class TestApply:
    def test_moves_safe_file_with_hash_verify(self, src_tree, migration_service, tmp_path):
        rt, src_root, files = src_tree
        dst_root = tmp_path / "library_new"
        plan = migration_service.plan(
            src_source_id="local",
            src_root=str(src_root),
            dst_root=str(dst_root),
        )

        # Snapshot original curator_ids
        orig_ids = {f.curator_id for f in files}

        report = migration_service.apply(plan)

        # Headline assertions
        assert report.moved_count == 3
        assert report.skipped_count == 0
        assert report.failed_count == 0

        # All 3 dst files exist
        for m in report.moves:
            assert m.outcome == MigrationOutcome.MOVED
            assert Path(m.dst_path).exists()
            assert not Path(m.src_path).exists()  # src was trashed

        # All 3 FileEntities still exist with same curator_ids,
        # now pointing at dst paths
        for f in files:
            entity = rt.file_repo.get(f.curator_id)
            assert entity is not None
            assert entity.curator_id in orig_ids
            # Path now lives under dst_root, not src_root
            assert Path(entity.source_path).is_relative_to(dst_root)

    def test_caution_file_skipped(self, migration_runtime, migration_service_real_safety, tmp_path):
        rt = migration_runtime
        src_root = tmp_path / "project"
        (src_root / ".git").mkdir(parents=True)
        (src_root / ".git" / "config").write_text("[core]\n")
        e = _seed_real_file(rt, src_root / "main.py", b"print('hi')\n")

        plan = migration_service_real_safety.plan(
            src_source_id="local",
            src_root=str(src_root),
            dst_root=str(tmp_path / "out"),
        )
        report = migration_service_real_safety.apply(plan)

        # The CAUTION file must NOT have been moved
        assert report.moved_count == 0
        moves_for_main = [m for m in report.moves if m.src_path.endswith("main.py")]
        assert len(moves_for_main) == 1
        assert moves_for_main[0].outcome == MigrationOutcome.SKIPPED_NOT_SAFE
        # File on disk untouched
        assert Path(e.source_path).exists()

    def test_collision_skipped(self, src_tree, migration_service, tmp_path):
        rt, src_root, files = src_tree
        dst_root = tmp_path / "library_new"
        # Pre-create a colliding file at the destination
        dst_root.mkdir(parents=True)
        (dst_root / "song1.mp3").write_bytes(b"existing")

        plan = migration_service.plan(
            src_source_id="local",
            src_root=str(src_root),
            dst_root=str(dst_root),
        )
        report = migration_service.apply(plan)

        # song1.mp3 must be SKIPPED_COLLISION; the others should still move
        collisions = [m for m in report.moves
                      if m.outcome == MigrationOutcome.SKIPPED_COLLISION]
        assert len(collisions) == 1
        assert collisions[0].src_path.endswith("song1.mp3")
        # Original src untouched (we didn't trash it)
        assert (src_root / "song1.mp3").exists()
        # Pre-existing dst untouched (we didn't overwrite)
        assert (dst_root / "song1.mp3").read_bytes() == b"existing"

    def test_db_guard_skipped(self, src_tree, migration_service, tmp_path):
        rt, src_root, files = src_tree
        dst_root = tmp_path / "library_new"

        plan = migration_service.plan(
            src_source_id="local",
            src_root=str(src_root),
            dst_root=str(dst_root),
        )
        # Pretend song1.mp3 IS the curator DB
        guard_path = Path(files[0].source_path)
        report = migration_service.apply(plan, db_path_guard=guard_path)

        guarded = [m for m in report.moves
                   if m.outcome == MigrationOutcome.SKIPPED_DB_GUARD]
        assert len(guarded) == 1
        assert guarded[0].src_path == str(guard_path)
        # The "DB" file was preserved
        assert guard_path.exists()

    def test_hash_mismatch_leaves_source_intact(
        self, src_tree, migration_service, tmp_path,
    ):
        """If the dst hash doesn't match src after copy, src is preserved
        and dst is removed. FileEntity is NOT updated."""
        rt, src_root, files = src_tree
        dst_root = tmp_path / "library_new"

        plan = migration_service.plan(
            src_source_id="local",
            src_root=str(src_root),
            dst_root=str(dst_root),
        )

        # Patch _xxhash3_128_of_file to return a wrong hash for the dst
        # so verify fails. We do this by patching the function in the
        # migration module's namespace.
        original_path = files[0].source_path
        call_count = [0]

        def fake_hash(path):
            call_count[0] += 1
            # First call (src hash, if not cached) returns real hash.
            # Subsequent call (dst hash) returns a deliberately bad one.
            if call_count[0] == 1 and not files[0].xxhash3_128:
                return xxhash.xxh3_128(Path(path).read_bytes()).hexdigest()
            return "deadbeef" * 4  # Wrong hash for dst

        with patch("curator.services.migration._xxhash3_128_of_file", fake_hash):
            report = migration_service.apply(plan)

        # All three moves should be HASH_MISMATCH
        mismatches = [m for m in report.moves
                      if m.outcome == MigrationOutcome.HASH_MISMATCH]
        assert len(mismatches) == 3
        # src files all still on disk
        for f in files:
            assert Path(f.source_path).exists(), (
                f"src must be preserved on hash mismatch: {f.source_path}"
            )
        # dst files all cleaned up
        for m in report.moves:
            assert not Path(m.dst_path).exists()
        # FileEntity rows still point at original src paths
        for f in files:
            entity = rt.file_repo.get(f.curator_id)
            assert entity.source_path == original_path or Path(entity.source_path) in [
                Path(orig.source_path) for orig in files
            ]

    def test_apply_with_verify_hash_false_skips_check(
        self, src_tree, migration_service, tmp_path,
    ):
        """verify_hash=False means we don't recompute the dst hash. Used
        for trusted fast paths (and tests where we want to confirm the
        verify step isn't running)."""
        rt, src_root, files = src_tree
        dst_root = tmp_path / "library_new"

        plan = migration_service.plan(
            src_source_id="local",
            src_root=str(src_root),
            dst_root=str(dst_root),
        )
        report = migration_service.apply(plan, verify_hash=False)
        assert report.moved_count == 3
        # No verified_xxhash recorded because verify was skipped
        for m in report.moves:
            if m.outcome == MigrationOutcome.MOVED:
                assert m.verified_xxhash is None

    def test_audit_entries_written_on_success(
        self, src_tree, migration_service, tmp_path,
    ):
        rt, src_root, files = src_tree
        dst_root = tmp_path / "library_new"

        plan = migration_service.plan(
            src_source_id="local",
            src_root=str(src_root),
            dst_root=str(dst_root),
        )
        report = migration_service.apply(plan)
        assert report.moved_count == 3

        # Query audit log for migration entries
        entries = rt.audit_repo.query(action="migration.move", limit=100)
        assert len(entries) == 3
        for entry in entries:
            assert entry.actor == "curator.migrate"
            assert entry.action == "migration.move"
            assert entry.entity_type == "file"
            assert "src_path" in entry.details
            assert "dst_path" in entry.details
            assert "xxhash3_128" in entry.details

    def test_no_audit_when_repo_is_none(self, src_tree, tmp_path, migration_runtime):
        """When MigrationService is constructed without an audit repo,
        moves still succeed; no audit entries are written.

        We re-stub safety here too because this test builds its own service.
        """
        from curator.services.safety import SafetyReport, SafetyLevel as SL
        rt, src_root, files = src_tree
        dst_root = tmp_path / "library_new"
        rt.safety.check_path = lambda p, **kw: SafetyReport(path=p, level=SL.SAFE)

        # Service WITHOUT audit
        svc_no_audit = MigrationService(
            file_repo=rt.file_repo,
            safety=rt.safety,
            audit=None,
        )
        plan = svc_no_audit.plan(
            src_source_id="local",
            src_root=str(src_root),
            dst_root=str(dst_root),
        )
        report = svc_no_audit.apply(plan)
        assert report.moved_count == 3
        # No migration.move entries recorded
        entries = rt.audit_repo.query(action="migration.move", limit=10)
        assert len(entries) == 0


# ---------------------------------------------------------------------------
# Lineage + bundle preservation across migration
# ---------------------------------------------------------------------------


class TestLineagePreservation:
    def test_lineage_edges_survive_move(
        self, src_tree, migration_service, tmp_path, migration_runtime,
    ):
        """The headline curator_id-constancy guarantee: lineage edges
        between two files keep working after both files are migrated."""
        rt, src_root, files = src_tree
        dst_root = tmp_path / "library_new"

        # Add a lineage edge between song1 and song2
        edge = LineageEdge(
            from_curator_id=files[0].curator_id,
            to_curator_id=files[1].curator_id,
            edge_kind=LineageKind.NEAR_DUPLICATE,
            confidence=0.85,
            detected_by="test",
        )
        rt.lineage_repo.insert(edge)

        plan = migration_service.plan(
            src_source_id="local",
            src_root=str(src_root),
            dst_root=str(dst_root),
        )
        report = migration_service.apply(plan)
        assert report.moved_count == 3

        # The lineage edge should still exist between the same two
        # curator_ids, regardless of their new paths
        edges = rt.lineage_repo.get_edges_for(files[0].curator_id)
        assert len(edges) == 1
        assert edges[0].from_curator_id == files[0].curator_id
        assert edges[0].to_curator_id == files[1].curator_id
        assert edges[0].edge_kind == LineageKind.NEAR_DUPLICATE

    def test_bundle_membership_survives_move(
        self, src_tree, migration_service, tmp_path, migration_runtime,
    ):
        rt, src_root, files = src_tree
        dst_root = tmp_path / "library_new"

        # Create a bundle with two of our files
        bundle = rt.bundle.create_manual(
            name="Test Album",
            member_ids=[files[0].curator_id, files[1].curator_id],
            primary_id=files[0].curator_id,
        )
        bid = bundle.bundle_id

        plan = migration_service.plan(
            src_source_id="local",
            src_root=str(src_root),
            dst_root=str(dst_root),
        )
        report = migration_service.apply(plan)
        assert report.moved_count == 3

        # Bundle still exists with both members
        members = rt.bundle.raw_memberships(bid)
        assert len(members) == 2
        assert {m.curator_id for m in members} == {
            files[0].curator_id, files[1].curator_id,
        }


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    def test_copy_failure_marks_failed_preserves_src(
        self, src_tree, migration_service, tmp_path,
    ):
        rt, src_root, files = src_tree
        dst_root = tmp_path / "library_new"

        plan = migration_service.plan(
            src_source_id="local",
            src_root=str(src_root),
            dst_root=str(dst_root),
        )

        # Patch shutil.copy2 to raise OSError
        def bad_copy(src, dst):
            raise OSError("disk full simulation")

        with patch("curator.services.migration.shutil.copy2", bad_copy):
            report = migration_service.apply(plan)

        assert report.failed_count == 3
        for m in report.moves:
            assert m.outcome == MigrationOutcome.FAILED
            assert "disk full simulation" in (m.error or "")
        # All src files preserved
        for f in files:
            assert Path(f.source_path).exists()
        # FileEntity rows still point at original src
        for f in files:
            entity = rt.file_repo.get(f.curator_id)
            assert Path(entity.source_path) == Path(f.source_path)
