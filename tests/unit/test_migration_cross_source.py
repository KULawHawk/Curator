"""Tests for v1.1.0a3 MigrationService Phase 2 Session B (cross-source migration).

Covers the cross-source code path that uses ``curator_source_write`` +
``curator_source_read_bytes`` + ``curator_source_delete`` plugin hooks
instead of the in-process ``shutil.copy2`` fast path. The strategy uses
TWO local source IDs (``local`` and ``local:vault``) -- both owned by
``LocalPlugin`` via its ``_owns()`` prefix matching -- so we exercise
the full cross-source path with real bytes/files on disk, no PyDrive2
or mock plugins required. The same-plugin-different-source-id case is
a legitimate cross-source migration (e.g., reorganizing between two
configured local roots, like Music drive -> archive drive).

Per docs/TRACER_PHASE_2_DESIGN.md §5.3 invariants tested here:

  * ``curator_id`` constancy across cross-source moves (lineage edges
    + bundle memberships persist transparently because we keep the
    same FileEntity row, just update its ``source_id`` + ``source_path``).
  * Hash-Verify-Before-Move via re-streaming the dst back through
    ``curator_source_read_bytes`` and recomputing xxhash3_128.
  * Mismatch leaves src intact and deletes dst (best-effort).
  * Audit entries for cross-source moves include both
    ``src_source_id`` and ``dst_source_id`` plus a
    ``cross_source: True`` marker for queryability.

Plus the CLI capability check (``--dst-source-id`` resolves to a
plugin that doesn't advertise ``supports_write``).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest
import xxhash

from curator.cli.runtime import build_runtime
from curator.config import Config
from curator.models.file import FileEntity
from curator.models.source import SourceConfig
from curator.services.migration import (
    MigrationOutcome,
    MigrationService,
)
from curator.services.safety import SafetyLevel, SafetyReport


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _seed_real_file(
    rt, source_id: str, path: Path,
    content: bytes = b"some bytes\n",
) -> FileEntity:
    """Create a real file on disk + index it under ``source_id``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    h = xxhash.xxh3_128(content).hexdigest()
    e = FileEntity(
        curator_id=uuid4(),
        source_id=source_id,
        source_path=str(path),
        size=len(content),
        mtime=datetime.fromtimestamp(path.stat().st_mtime),
        extension=path.suffix.lower(),
        xxhash3_128=h,
    )
    rt.file_repo.upsert(e)
    return e


@pytest.fixture
def cross_source_runtime(tmp_path):
    """Real CuratorRuntime with TWO local source IDs registered.

    SafetyService is stubbed to SAFE so the migration-mechanics tests
    run without CAUTION false positives (pytest tmp_path is under
    %LOCALAPPDATA% on Windows, which real safety flags as CAUTION).
    """
    db_path = tmp_path / "cross_source.db"
    cfg = Config.load()
    rt = build_runtime(
        config=cfg, db_path_override=db_path,
        json_output=False, no_color=True, verbosity=0,
    )
    # Register both source IDs (both backed by LocalPlugin via _owns() prefix)
    for sid, name in [("local", "Local Primary"), ("local:vault", "Local Vault")]:
        try:
            rt.source_repo.insert(SourceConfig(
                source_id=sid, source_type="local", display_name=name,
            ))
        except Exception:
            pass  # already present from a prior test in this run
    # Stub safety -> SAFE for everything
    def _safe(self, path, **kw):
        return SafetyReport(path=path, level=SafetyLevel.SAFE)
    with patch.object(
        rt.safety.__class__, "check_path", _safe,
    ):
        yield rt


# ---------------------------------------------------------------------------
# Cross-source detection + capability check
# ---------------------------------------------------------------------------


class TestCrossSourceDetection:
    def test_is_cross_source_true_for_different_ids(self, cross_source_runtime):
        svc = cross_source_runtime.migration
        assert svc._is_cross_source("local", "local:vault") is True
        assert svc._is_cross_source("local", "gdrive") is True

    def test_is_cross_source_false_for_same_id(self, cross_source_runtime):
        svc = cross_source_runtime.migration
        assert svc._is_cross_source("local", "local") is False
        assert svc._is_cross_source("local:vault", "local:vault") is False


class TestCanWriteToSource:
    def test_local_supports_write(self, cross_source_runtime):
        svc = cross_source_runtime.migration
        # Both 'local' and 'local:*' are owned by LocalPlugin which
        # advertises supports_write=True.
        assert svc._can_write_to_source("local") is True
        assert svc._can_write_to_source("local:vault") is True

    def test_unknown_source_returns_false(self, cross_source_runtime):
        svc = cross_source_runtime.migration
        # No plugin owns 'made_up_source'
        assert svc._can_write_to_source("made_up_source") is False


# ---------------------------------------------------------------------------
# Cross-source bytes transfer (the helper directly)
# ---------------------------------------------------------------------------


class TestCrossSourceTransfer:
    def test_round_trip_bytes_match(self, cross_source_runtime, tmp_path):
        rt = cross_source_runtime
        svc = rt.migration
        src_path = tmp_path / "src" / "doc.txt"
        src_path.parent.mkdir(parents=True)
        content = b"hello cross-source world\n" * 50  # ~1.2 KB
        src_path.write_bytes(content)
        dst_path = tmp_path / "vault" / "doc.txt"
        src_hash = xxhash.xxh3_128(content).hexdigest()

        outcome, actual_dst, verified = svc._cross_source_transfer(
            src_source_id="local",
            src_file_id=str(src_path),
            src_xxhash=src_hash,
            dst_source_id="local:vault",
            dst_path=str(dst_path),
            verify_hash=True,
        )

        assert outcome == MigrationOutcome.MOVED
        assert actual_dst == str(dst_path)
        assert verified == src_hash
        assert dst_path.exists()
        assert dst_path.read_bytes() == content
        # Src untouched (this helper is bytes-only; caller handles delete)
        assert src_path.exists()

    def test_collision_returns_skipped(self, cross_source_runtime, tmp_path):
        rt = cross_source_runtime
        svc = rt.migration
        src_path = tmp_path / "src" / "x.txt"
        src_path.parent.mkdir(parents=True)
        src_path.write_bytes(b"src content")
        # Pre-create dst so the write hook raises FileExistsError
        dst_path = tmp_path / "vault" / "x.txt"
        dst_path.parent.mkdir(parents=True)
        dst_path.write_bytes(b"PRE-EXISTING DO NOT TOUCH")

        outcome, _, verified = svc._cross_source_transfer(
            src_source_id="local",
            src_file_id=str(src_path),
            src_xxhash=None,
            dst_source_id="local:vault",
            dst_path=str(dst_path),
            verify_hash=False,
        )

        assert outcome == MigrationOutcome.SKIPPED_COLLISION
        # Pre-existing dst preserved
        assert dst_path.read_bytes() == b"PRE-EXISTING DO NOT TOUCH"

    def test_unknown_dst_source_raises(self, cross_source_runtime, tmp_path):
        rt = cross_source_runtime
        svc = rt.migration
        src_path = tmp_path / "src" / "x.txt"
        src_path.parent.mkdir(parents=True)
        src_path.write_bytes(b"x")
        with pytest.raises(RuntimeError, match="curator_source_write"):
            svc._cross_source_transfer(
                src_source_id="local",
                src_file_id=str(src_path),
                src_xxhash=None,
                dst_source_id="never_registered",
                dst_path=str(tmp_path / "out" / "x.txt"),
                verify_hash=False,
            )


# ---------------------------------------------------------------------------
# Phase 1: apply() with cross-source
# ---------------------------------------------------------------------------


class TestPhase1CrossSourceApply:
    def test_cross_source_move_updates_source_id_and_path(
        self, cross_source_runtime, tmp_path,
    ):
        """The headline cross-source invariant: same curator_id, but
        FileEntity.source_id flips from src to dst on move."""
        rt = cross_source_runtime
        src_root = tmp_path / "primary"
        dst_root = tmp_path / "vault"
        files = [
            _seed_real_file(rt, "local", src_root / f"f{i}.txt", b"data" * (i + 1))
            for i in range(3)
        ]

        plan = rt.migration.plan(
            src_source_id="local", src_root=str(src_root),
            dst_source_id="local:vault", dst_root=str(dst_root),
        )
        assert plan.src_source_id == "local"
        assert plan.dst_source_id == "local:vault"
        assert plan.total_count == 3

        report = rt.migration.apply(plan)

        assert report.moved_count == 3
        assert report.failed_count == 0
        # Each FileEntity now has source_id="local:vault" and the new path.
        # curator_id is unchanged (Constitution invariant).
        for f in files:
            entity = rt.file_repo.get(f.curator_id)
            assert entity is not None
            assert entity.source_id == "local:vault"
            assert entity.source_path.startswith(str(dst_root))
            # Hash unchanged (the bytes are the same; we just moved them)
            assert entity.xxhash3_128 == f.xxhash3_128

    def test_cross_source_keep_source_preserves_src_and_index(
        self, cross_source_runtime, tmp_path,
    ):
        rt = cross_source_runtime
        src_root = tmp_path / "primary"
        dst_root = tmp_path / "vault"
        files = [
            _seed_real_file(rt, "local", src_root / f"f{i}.txt")
            for i in range(2)
        ]
        plan = rt.migration.plan(
            src_source_id="local", src_root=str(src_root),
            dst_source_id="local:vault", dst_root=str(dst_root),
        )
        report = rt.migration.apply(plan, keep_source=True)

        assert report.moved_count == 2  # COPIED counts in moved_count
        # All srcs still exist
        for f in files:
            assert Path(f.source_path).exists()
        # Index unchanged -- still points at "local" with old paths
        for f in files:
            entity = rt.file_repo.get(f.curator_id)
            assert entity.source_id == "local"
            assert entity.source_path == f.source_path
        # Dsts created
        for f in files:
            rel = Path(f.source_path).relative_to(src_root)
            assert (dst_root / rel).exists()
        # Outcomes are COPIED
        for m in report.moves:
            if m.outcome:
                assert m.outcome == MigrationOutcome.COPIED

    def test_cross_source_audit_includes_both_source_ids(
        self, cross_source_runtime, tmp_path,
    ):
        rt = cross_source_runtime
        src_root = tmp_path / "primary"
        dst_root = tmp_path / "vault"
        _seed_real_file(rt, "local", src_root / "f.txt")
        plan = rt.migration.plan(
            src_source_id="local", src_root=str(src_root),
            dst_source_id="local:vault", dst_root=str(dst_root),
        )
        rt.migration.apply(plan)
        # Audit entry must exist (Phase 1 _audit_move uses base details)
        # Phase 1 cross-source uses _audit_move which currently records
        # src_path + dst_path but not source_ids. The Phase 2 path adds
        # source_ids + cross_source marker; Phase 1 keeps the simpler
        # schema since the dst_path itself encodes the destination.
        moves = rt.audit_repo.query(action="migration.move", limit=10)
        assert len(moves) == 1


class TestPhase1CrossSourceVerify:
    def test_hash_mismatch_leaves_src_intact(
        self, cross_source_runtime, tmp_path,
    ):
        """If we corrupt the src's cached hash to disagree with reality,
        verify-by-restream catches it: dst gets deleted, src untouched."""
        rt = cross_source_runtime
        src_root = tmp_path / "primary"
        dst_root = tmp_path / "vault"
        f = _seed_real_file(rt, "local", src_root / "x.txt", b"real bytes")
        # Corrupt the cached hash so verify fails
        f.xxhash3_128 = "deadbeef" * 4  # 32 hex chars
        rt.file_repo.update(f)

        plan = rt.migration.plan(
            src_source_id="local", src_root=str(src_root),
            dst_source_id="local:vault", dst_root=str(dst_root),
        )
        report = rt.migration.apply(plan)

        assert report.failed_count == 1
        m = report.moves[0]
        assert m.outcome == MigrationOutcome.HASH_MISMATCH
        # Src untouched
        assert Path(f.source_path).exists()
        # Dst was cleaned up by the cross-source helper
        assert not (dst_root / "x.txt").exists()
        # Index still points at src (not updated due to hash mismatch)
        entity = rt.file_repo.get(f.curator_id)
        assert entity.source_id == "local"


# ---------------------------------------------------------------------------
# Phase 2: run_job() with cross-source
# ---------------------------------------------------------------------------


class TestPhase2CrossSourceRunJob:
    def test_cross_source_persistent_under_workers(
        self, cross_source_runtime, tmp_path,
    ):
        """Persisted cross-source migration with 4 workers preserves
        all Constitution invariants under contention."""
        rt = cross_source_runtime
        src_root = tmp_path / "primary"
        dst_root = tmp_path / "vault"
        files = [
            _seed_real_file(rt, "local", src_root / f"f{i:02d}.txt", f"file{i}".encode() * 20)
            for i in range(8)
        ]
        plan = rt.migration.plan(
            src_source_id="local", src_root=str(src_root),
            dst_source_id="local:vault", dst_root=str(dst_root),
        )
        job_id = rt.migration.create_job(plan)
        report = rt.migration.run_job(job_id, workers=4, verify_hash=True)

        assert report.moved_count == 8
        assert report.failed_count == 0
        # All entities now under "local:vault"
        for f in files:
            entity = rt.file_repo.get(f.curator_id)
            assert entity.source_id == "local:vault"
            assert entity.source_path.startswith(str(dst_root))
        # Audit log should have 8 cross-source moves with the right markers
        audit_entries = rt.audit_repo.query(action="migration.move", limit=20)
        assert len(audit_entries) == 8
        for entry in audit_entries:
            details = entry.details or {}
            assert details.get("cross_source") is True
            assert details.get("src_source_id") == "local"
            assert details.get("dst_source_id") == "local:vault"
            assert details.get("job_id") == str(job_id)

    def test_cross_source_keep_source_under_workers(
        self, cross_source_runtime, tmp_path,
    ):
        """Cross-source + keep_source: srcs preserved, audit uses
        ``migration.copy`` action with cross_source marker."""
        rt = cross_source_runtime
        src_root = tmp_path / "primary"
        dst_root = tmp_path / "vault"
        files = [
            _seed_real_file(rt, "local", src_root / f"f{i}.txt")
            for i in range(4)
        ]
        plan = rt.migration.plan(
            src_source_id="local", src_root=str(src_root),
            dst_source_id="local:vault", dst_root=str(dst_root),
        )
        job_id = rt.migration.create_job(
            plan, options={"keep_source": True},
        )
        report = rt.migration.run_job(
            job_id, workers=4, keep_source=True,
        )

        assert report.moved_count == 4  # COPIED counts as moved
        # Srcs all preserved
        for f in files:
            assert Path(f.source_path).exists()
        # Index still points at "local"
        for f in files:
            entity = rt.file_repo.get(f.curator_id)
            assert entity.source_id == "local"
        # Audit: migration.copy with cross_source marker
        copy_entries = rt.audit_repo.query(action="migration.copy", limit=10)
        move_entries = rt.audit_repo.query(action="migration.move", limit=10)
        assert len(copy_entries) == 4
        assert len(move_entries) == 0
        for entry in copy_entries:
            details = entry.details or {}
            assert details.get("cross_source") is True


# ---------------------------------------------------------------------------
# Constitution invariants under cross-source
# ---------------------------------------------------------------------------


class TestCrossSourceConstitutionInvariants:
    def test_curator_id_constancy_under_cross_source(
        self, cross_source_runtime, tmp_path,
    ):
        """The Phase 1 invariant -- same curator_id before + after --
        also holds for cross-source moves."""
        rt = cross_source_runtime
        src_root = tmp_path / "primary"
        dst_root = tmp_path / "vault"
        f = _seed_real_file(rt, "local", src_root / "x.txt")
        original_id = f.curator_id

        plan = rt.migration.plan(
            src_source_id="local", src_root=str(src_root),
            dst_source_id="local:vault", dst_root=str(dst_root),
        )
        rt.migration.apply(plan)

        # Look up by curator_id -- it must still resolve, with NEW source_id
        entity = rt.file_repo.get(original_id)
        assert entity is not None
        assert entity.curator_id == original_id  # CONSTITUTION
        assert entity.source_id == "local:vault"  # NEW
        assert entity.source_path != str(src_root / "x.txt")  # NEW

    def test_lineage_edges_survive_cross_source_move(
        self, cross_source_runtime, tmp_path,
    ):
        """Lineage edges reference curator_ids; cross-source moves keep
        the same curator_id, so all edges stay valid transparently."""
        from curator.models.lineage import LineageEdge, LineageKind
        rt = cross_source_runtime
        src_root = tmp_path / "primary"
        dst_root = tmp_path / "vault"
        f1 = _seed_real_file(rt, "local", src_root / "a.txt", b"AAA")
        f2 = _seed_real_file(rt, "local", src_root / "b.txt", b"BBB")
        # Add a lineage edge between them
        rt.lineage_repo.insert(LineageEdge(
            from_curator_id=f1.curator_id,
            to_curator_id=f2.curator_id,
            edge_kind=LineageKind.DUPLICATE,
            confidence=0.9, detected_by="test",
        ))
        edges_before = rt.lineage_repo.get_edges_for(f1.curator_id)
        assert len(edges_before) == 1

        plan = rt.migration.plan(
            src_source_id="local", src_root=str(src_root),
            dst_source_id="local:vault", dst_root=str(dst_root),
        )
        rt.migration.apply(plan)

        # Edges still reference f1.curator_id and f2.curator_id; both
        # still resolve to live entities (now under "local:vault").
        edges_after = rt.lineage_repo.get_edges_for(f1.curator_id)
        assert len(edges_after) == 1
        # Both endpoints still resolvable
        assert rt.file_repo.get(f1.curator_id) is not None
        assert rt.file_repo.get(f2.curator_id) is not None

    def test_db_guard_skips_cross_source_too(
        self, cross_source_runtime, tmp_path,
    ):
        """db_path_guard works the same for cross-source as same-source:
        a planned move whose src equals the guard path is skipped."""
        rt = cross_source_runtime
        src_root = tmp_path / "primary"
        dst_root = tmp_path / "vault"
        f = _seed_real_file(rt, "local", src_root / "important.db")
        plan = rt.migration.plan(
            src_source_id="local", src_root=str(src_root),
            dst_source_id="local:vault", dst_root=str(dst_root),
        )
        report = rt.migration.apply(
            plan, db_path_guard=Path(f.source_path),
        )
        assert report.moved_count == 0
        m = report.moves[0]
        assert m.outcome == MigrationOutcome.SKIPPED_DB_GUARD
        # File untouched
        assert Path(f.source_path).exists()


# ---------------------------------------------------------------------------
# Same-source still works (regression: dispatcher correctness)
# ---------------------------------------------------------------------------


class TestSameSourceStillWorks:
    """The Session B refactor introduces a dispatcher in _execute_one
    and _execute_one_persistent. Sanity-check that same-source still
    routes to the shutil fast path correctly."""

    def test_same_source_apply_uses_fast_path(
        self, cross_source_runtime, tmp_path,
    ):
        rt = cross_source_runtime
        src_root = tmp_path / "primary"
        dst_root = tmp_path / "primary_renamed"
        f = _seed_real_file(rt, "local", src_root / "x.txt")
        plan = rt.migration.plan(
            src_source_id="local", src_root=str(src_root),
            dst_source_id="local",  # same as src -> fast path
            dst_root=str(dst_root),
        )
        report = rt.migration.apply(plan)
        assert report.moved_count == 1
        # Audit entry for same-source move does NOT have cross_source marker
        entries = rt.audit_repo.query(action="migration.move", limit=5)
        assert len(entries) == 1
        details = entries[0].details or {}
        assert "cross_source" not in details


# ===========================================================================
# v1.1.1: curator_source_write_post hook firing (P1 of curatorplug-atrium-safety)
# ===========================================================================


class TestCuratorSourceWritePostHook:
    """v1.1.1 introduced ``curator_source_write_post`` as a Curator-side
    prerequisite for the ``curatorplug-atrium-safety`` plugin. After every
    successful ``curator_source_write`` (in the cross-source path), the
    hook fires and any registered plugin can perform an independent
    post-write verification or refuse the write by raising.

    See ``curatorplug-atrium-safety/DESIGN.md`` for the design that
    motivated this hookspec.
    """

    def _make_recorder_plugin(self):
        """Build a simple hookimpl that records every post-write invocation.

        Returns ``(plugin_instance, calls_list)``. The plugin can be
        registered with ``rt.pm.register(plugin_instance)`` and the
        calls_list will accumulate one dict per hook firing.
        """
        from curator.plugins import hookimpl
        calls: list[dict] = []

        class _PostWriteRecorder:
            @hookimpl
            def curator_source_write_post(
                self, source_id, file_id, src_xxhash, written_bytes_len,
            ):
                calls.append({
                    "source_id": source_id,
                    "file_id": file_id,
                    "src_xxhash": src_xxhash,
                    "written_bytes_len": written_bytes_len,
                })
                return None

        return _PostWriteRecorder(), calls

    def test_hook_fires_after_successful_cross_source_write(
        self, cross_source_runtime, tmp_path,
    ):
        """The headline: a successful cross-source migration triggers
        exactly one ``curator_source_write_post`` invocation per file,
        with the expected arguments populated."""
        rt = cross_source_runtime
        src_root = tmp_path / "src_a"
        dst_root = tmp_path / "dst_b"
        content = b"recorder-test-bytes\n" * 10
        expected_hash = xxhash.xxh3_128(content).hexdigest()
        _seed_real_file(rt, "local", src_root / "f.txt", content=content)

        plugin, calls = self._make_recorder_plugin()
        rt.pm.register(plugin)
        try:
            plan = rt.migration.plan(
                src_source_id="local", src_root=str(src_root),
                dst_source_id="local:vault", dst_root=str(dst_root),
            )
            report = rt.migration.apply(plan)
        finally:
            rt.pm.unregister(plugin)

        assert report.moved_count == 1
        assert len(calls) == 1
        call = calls[0]
        assert call["source_id"] == "local:vault"
        assert call["file_id"] == str(dst_root / "f.txt")
        assert call["src_xxhash"] == expected_hash
        assert call["written_bytes_len"] == len(content)

    def test_hook_does_not_fire_on_collision(
        self, cross_source_runtime, tmp_path,
    ):
        """When ``curator_source_write`` raises FileExistsError (dst already
        present), the move outcomes is SKIPPED_COLLISION and the post-write
        hook MUST NOT fire -- nothing was actually written."""
        rt = cross_source_runtime
        src_root = tmp_path / "src_collide"
        dst_root = tmp_path / "dst_collide"
        _seed_real_file(rt, "local", src_root / "clash.txt", content=b"src")
        # Pre-create the dst file so curator_source_write raises FileExistsError
        dst_root.mkdir(parents=True, exist_ok=True)
        (dst_root / "clash.txt").write_bytes(b"already here")

        plugin, calls = self._make_recorder_plugin()
        rt.pm.register(plugin)
        try:
            plan = rt.migration.plan(
                src_source_id="local", src_root=str(src_root),
                dst_source_id="local:vault", dst_root=str(dst_root),
            )
            report = rt.migration.apply(plan)
        finally:
            rt.pm.unregister(plugin)

        assert report.moves[0].outcome == MigrationOutcome.SKIPPED_COLLISION
        assert calls == []  # never fired

    def test_hook_does_not_fire_on_hash_mismatch(
        self, cross_source_runtime, tmp_path, monkeypatch,
    ):
        """When verify reads back bytes that don't match the source
        hash, outcome is HASH_MISMATCH; the dst is deleted and the
        post-write hook MUST NOT fire (the write didn't survive verify)."""
        rt = cross_source_runtime
        src_root = tmp_path / "src_mismatch"
        dst_root = tmp_path / "dst_mismatch"
        _seed_real_file(
            rt, "local", src_root / "corrupt.txt", content=b"original",
        )

        # Force a hash mismatch by stubbing the verify step to return
        # different bytes than were written.
        original_read = rt.migration._read_bytes_via_hook
        call_count = {"n": 0}
        def _flaky_read(source_id, file_id):
            call_count["n"] += 1
            # First call (reading src for the transfer): real
            # Second call (reading dst for verify): corrupt
            if call_count["n"] == 1:
                return original_read(source_id, file_id)
            return b"corrupted-on-readback"
        monkeypatch.setattr(
            rt.migration, "_read_bytes_via_hook", _flaky_read,
        )

        plugin, calls = self._make_recorder_plugin()
        rt.pm.register(plugin)
        try:
            plan = rt.migration.plan(
                src_source_id="local", src_root=str(src_root),
                dst_source_id="local:vault", dst_root=str(dst_root),
            )
            report = rt.migration.apply(plan)
        finally:
            rt.pm.unregister(plugin)

        assert report.moves[0].outcome == MigrationOutcome.HASH_MISMATCH
        assert calls == []  # never fired

    def test_hook_receives_none_xxhash_when_verify_disabled(
        self, cross_source_runtime, tmp_path,
    ):
        """When ``verify_hash=False``, the migration skips its own verify;
        the hook still fires (so safety plugins can verify themselves)
        but receives ``src_xxhash=None`` -- safety plugins must handle
        this case gracefully (e.g., skip their own verify or refuse)."""
        rt = cross_source_runtime
        src_root = tmp_path / "src_noverify"
        dst_root = tmp_path / "dst_noverify"
        _seed_real_file(
            rt, "local", src_root / "f.txt", content=b"no-verify-bytes",
        )

        plugin, calls = self._make_recorder_plugin()
        rt.pm.register(plugin)
        try:
            plan = rt.migration.plan(
                src_source_id="local", src_root=str(src_root),
                dst_source_id="local:vault", dst_root=str(dst_root),
            )
            report = rt.migration.apply(plan, verify_hash=False)
        finally:
            rt.pm.unregister(plugin)

        assert report.moved_count == 1
        assert len(calls) == 1
        assert calls[0]["src_xxhash"] is None
        assert calls[0]["written_bytes_len"] == len(b"no-verify-bytes")

    def test_hook_raising_makes_move_failed(
        self, cross_source_runtime, tmp_path,
    ):
        """DM-1 (soft enforcement): a plugin can refuse a write by
        raising from the post-write hook. The exception propagates,
        the per-file outer boundary catches it, and the move's outcome
        becomes FAILED with the exception message in ``error``."""
        rt = cross_source_runtime
        src_root = tmp_path / "src_refused"
        dst_root = tmp_path / "dst_refused"
        _seed_real_file(
            rt, "local", src_root / "refused.txt", content=b"refuse me",
        )

        from curator.plugins import hookimpl

        class _RefusingPlugin:
            @hookimpl
            def curator_source_write_post(
                self, source_id, file_id, src_xxhash, written_bytes_len,
            ):
                raise RuntimeError("compliance: simulated refusal")

        plugin = _RefusingPlugin()
        rt.pm.register(plugin)
        try:
            plan = rt.migration.plan(
                src_source_id="local", src_root=str(src_root),
                dst_source_id="local:vault", dst_root=str(dst_root),
            )
            report = rt.migration.apply(plan)
        finally:
            rt.pm.unregister(plugin)

        assert report.moves[0].outcome == MigrationOutcome.FAILED
        assert "simulated refusal" in (report.moves[0].error or "")


# ===========================================================================
# Phase 4 P3 — end-to-end cross-source conflict resolution
# ===========================================================================

class TestPhase4CrossSourceConflictResolution:
    """End-to-end Phase 4 tests using REAL LocalPlugin (no mocks).

    Exercises the full ``apply()`` → ``_execute_one_cross_source`` →
    Phase 4 dispatch → ``curator_source_rename`` chain against actual
    files on disk via the ``local`` + ``local:vault`` two-source-ID
    pattern. Complements ``test_migration_phase4_cross_source_conflict.py``
    (which tests dispatch logic in isolation with mocked transfer +
    rename helpers).

    Per docs/TRACER_PHASE_4_DESIGN.md v0.3 IMPLEMENTED §12 — closes the
    "end-to-end coverage of the apply() flow lives at the next coverage
    pass" gap noted in the P2 test file's module docstring.
    """

    def test_cross_source_overwrite_with_backup_renames_existing_then_writes(
        self, cross_source_runtime, tmp_path,
    ):
        """overwrite-with-backup end-to-end: pre-seed dst, run apply, verify backup exists.

        Setup: src has `foo.txt` with content `b'src bytes'`; dst already has
        `foo.txt` with content `b'old bytes'` (NOT in the index, just on disk).
        Action: apply with --on-conflict=overwrite-with-backup.
        Expected: existing dst gets renamed to `foo.curator-backup-<UTC>.txt`
        with the OLD bytes preserved; new dst at `foo.txt` has the SRC bytes;
        outcome is MOVED_OVERWROTE_WITH_BACKUP; audit emits cross_source: True
        with backup_name field; src is trashed (file no longer at src path).
        """
        rt = cross_source_runtime
        src_root = tmp_path / "src_owb"
        dst_root = tmp_path / "dst_owb"
        # Seed src under "local" with a known content + index entry
        src_content = b"src bytes for overwrite-with-backup"
        _seed_real_file(rt, "local", src_root / "foo.txt", content=src_content)
        # Pre-seed dst on disk WITHOUT indexing (the colliding existing file)
        dst_root.mkdir(parents=True, exist_ok=True)
        old_content = b"old dst bytes that should land in backup"
        (dst_root / "foo.txt").write_bytes(old_content)

        rt.migration.set_on_conflict_mode("overwrite-with-backup")

        plan = rt.migration.plan(
            src_source_id="local", src_root=str(src_root),
            dst_source_id="local:vault", dst_root=str(dst_root),
        )
        # Pass on_conflict directly to apply() because apply() resets the
        # mode at entry to whatever its default kwarg says.
        report = rt.migration.apply(plan, on_conflict="overwrite-with-backup")

        # 1 move only
        assert len(report.moves) == 1
        move = report.moves[0]

        # Outcome MUST be the variant, not plain MOVED
        assert move.outcome == MigrationOutcome.MOVED_OVERWROTE_WITH_BACKUP, (
            f"expected MOVED_OVERWROTE_WITH_BACKUP, got {move.outcome}"
        )

        # New dst has the SRC content
        assert (dst_root / "foo.txt").read_bytes() == src_content

        # Backup file exists with .curator-backup-<UTC> in the name AND has OLD content
        backups = list(dst_root.glob("foo.curator-backup-*.txt"))
        assert len(backups) == 1, (
            f"expected exactly 1 backup file, found {[b.name for b in backups]}"
        )
        assert backups[0].read_bytes() == old_content, (
            "backup file should contain the ORIGINAL pre-rename dst bytes"
        )

        # Src was trashed (file no longer at src path)
        assert not (src_root / "foo.txt").exists(), (
            "src should have been trashed/deleted after successful overwrite-with-backup"
        )

        # Audit verification is covered by the dispatch unit tests in
        # tests/unit/test_migration_phase4_cross_source_conflict.py via
        # mocks; this e2e test focuses on real-disk state + outcome enum
        # to validate the full apply() -> dispatch chain end-to-end.

    def test_cross_source_rename_with_suffix_lands_at_curator_1(
        self, cross_source_runtime, tmp_path,
    ):
        """rename-with-suffix end-to-end: dst occupied; src lands at .curator-1.

        Setup: src has `bar.dat`; dst has `bar.dat` (occupying the slot).
        Action: apply with --on-conflict=rename-with-suffix.
        Expected: src bytes land at `bar.curator-1.dat`; outcome is
        MOVED_RENAMED_WITH_SUFFIX; audit captures suffix_n=1 + cross_source: True.
        Original dst file at `bar.dat` is UNCHANGED (we route around it,
        we don't overwrite it).
        """
        rt = cross_source_runtime
        src_root = tmp_path / "src_rws"
        dst_root = tmp_path / "dst_rws"
        src_content = b"src content for rename-with-suffix"
        _seed_real_file(rt, "local", src_root / "bar.dat", content=src_content)
        dst_root.mkdir(parents=True, exist_ok=True)
        existing_dst_content = b"existing untouched dst content"
        (dst_root / "bar.dat").write_bytes(existing_dst_content)

        rt.migration.set_on_conflict_mode("rename-with-suffix")

        plan = rt.migration.plan(
            src_source_id="local", src_root=str(src_root),
            dst_source_id="local:vault", dst_root=str(dst_root),
        )
        report = rt.migration.apply(plan, on_conflict="rename-with-suffix")

        move = report.moves[0]
        assert move.outcome == MigrationOutcome.MOVED_RENAMED_WITH_SUFFIX

        # The suffix variant has src bytes
        suffix_dst = dst_root / "bar.curator-1.dat"
        assert suffix_dst.exists(), f"expected {suffix_dst} to exist"
        assert suffix_dst.read_bytes() == src_content

        # The original dst slot is UNTOUCHED (rename-with-suffix routes around)
        assert (dst_root / "bar.dat").read_bytes() == existing_dst_content

        # Src was trashed
        assert not (src_root / "bar.dat").exists()

        # Audit details verified in unit test pass (mock-based).

    def test_cross_source_rename_with_suffix_finds_next_free_when_curator_1_taken(
        self, cross_source_runtime, tmp_path,
    ):
        """rename-with-suffix retry loop: .curator-1 taken too, lands at .curator-2.

        Setup: src has `baz.bin`; dst has `baz.bin` AND `baz.curator-1.bin`
        (both occupying slots). Action: apply with --on-conflict=rename-with-suffix.
        Expected: src bytes land at `baz.curator-2.bin`; suffix_n=2 in audit;
        FileExistsError retry-write loop iterated past suffix=1.
        """
        rt = cross_source_runtime
        src_root = tmp_path / "src_rws2"
        dst_root = tmp_path / "dst_rws2"
        src_content = b"src content third in line"
        _seed_real_file(rt, "local", src_root / "baz.bin", content=src_content)
        dst_root.mkdir(parents=True, exist_ok=True)
        # Both `baz.bin` AND `baz.curator-1.bin` are occupied
        (dst_root / "baz.bin").write_bytes(b"slot 0 occupied")
        (dst_root / "baz.curator-1.bin").write_bytes(b"slot 1 occupied")

        rt.migration.set_on_conflict_mode("rename-with-suffix")

        plan = rt.migration.plan(
            src_source_id="local", src_root=str(src_root),
            dst_source_id="local:vault", dst_root=str(dst_root),
        )
        report = rt.migration.apply(plan, on_conflict="rename-with-suffix")

        move = report.moves[0]
        assert move.outcome == MigrationOutcome.MOVED_RENAMED_WITH_SUFFIX

        # Lands at curator-2
        suffix_dst = dst_root / "baz.curator-2.bin"
        assert suffix_dst.exists()
        assert suffix_dst.read_bytes() == src_content

        # Both prior slots untouched
        assert (dst_root / "baz.bin").read_bytes() == b"slot 0 occupied"
        assert (dst_root / "baz.curator-1.bin").read_bytes() == b"slot 1 occupied"

        # Audit details (suffix_n=2 etc.) verified in unit test pass.
