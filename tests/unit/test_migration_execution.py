"""Focused unit tests for MigrationService same-source execution +
collision resolution (4 on-conflict modes).

Sub-ship 2 of the Migration Phase Gamma arc (v1.7.90).
Scope plan: docs/MIGRATION_PHASE_GAMMA_SCOPE.md

Targets:
* `_execute_one_same_source` happy-path xxhash compute (line 1020)
* hash-mismatch cleanup with unlink OSError swallow (lines 1036-1037)
* OSError/shutil.Error path with dst cleanup (lines 1073-1076)
* defensive Exception fallback (lines 1079-1081)
* `_resolve_collision` 4 modes (lines 1928-1992):
  - skip (1928-1930)
  - fail (1932-1939)
  - overwrite-with-backup success + OSError failure (1941-1961)
  - rename-with-suffix success + RuntimeError failure (1963-1984)
  - unknown mode defensive fallback (1986-1992)

Stubs reused from v1.7.89 via import; no redesign per Lesson #84.

KEY TEST PATTERN: `apply()` creates a FRESH copy of each move into
`report.moves`. Always assert against `report.moves[0]`, not against
the original move handed in via `plan.moves[0]`.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from curator.models.file import FileEntity
from curator.services.migration import (
    MigrationConflictError,
    MigrationMove,
    MigrationOutcome,
    MigrationPlan,
)
from curator.services.safety import SafetyLevel

# Reuse stubs and helpers from v1.7.89 (Lesson #84 — stub patterns mature
# and compound across sub-ships).
from tests.unit.test_migration_plan_apply import (
    NOW,
    StubAuditRepository,
    StubFileRepository,
    make_service,
)


# ===========================================================================
# Helpers
# ===========================================================================


def _build_setup(tmp_path, *, mode: str | None = None,
                 pre_existing_dst: bool = False,
                 src_content: bytes = b"source bytes",
                 dst_content: bytes = b"pre-existing dst bytes",
                 audit: bool = False):
    """Build service + plan + move + (optional audit) pointing at real files.

    Returns: (svc, plan, src_path, dst_path, audit_repo_or_None)
    """
    src = tmp_path / "src.txt"
    src.write_bytes(src_content)
    dst = tmp_path / "dst" / "src.txt"
    if pre_existing_dst:
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(dst_content)

    entity = FileEntity(
        source_id="local",
        source_path=str(src),
        size=src.stat().st_size,
        mtime=NOW,
    )
    file_repo = StubFileRepository(files=[entity])
    audit_repo = StubAuditRepository() if audit else None
    svc = make_service(file_repo=file_repo, audit=audit_repo)
    if mode is not None:
        svc.set_on_conflict_mode(mode)

    move = MigrationMove(
        curator_id=entity.curator_id,
        src_path=str(src),
        dst_path=str(dst),
        safety_level=SafetyLevel.SAFE,
        size=entity.size,
        src_xxhash=None,
    )
    plan = MigrationPlan(
        src_source_id="local", src_root=str(src.parent),
        dst_source_id="local", dst_root=str(dst.parent),
        moves=[move],
    )
    return svc, plan, src, dst, audit_repo


# ===========================================================================
# _execute_one_same_source: xxhash compute + error paths
# ===========================================================================


class TestSameSourceExecution:
    def test_xxhash_computed_when_verify_hash_true_and_no_cache(
        self, tmp_path,
    ):
        # Line 1020: src_xxhash is None and verify_hash=True
        # → compute hash on demand.
        svc, plan, src, dst, _ = _build_setup(tmp_path)
        report = svc.apply(plan, verify_hash=True)
        result_move = report.moves[0]
        # Hash was computed and dst was moved successfully
        assert result_move.src_xxhash is not None
        assert result_move.outcome == MigrationOutcome.MOVED
        assert dst.exists()

    def test_hash_mismatch_cleans_up_dst_and_marks_failed(
        self, monkeypatch, tmp_path,
    ):
        # Lines 1032-1043: when dst hash != src hash, unlink dst,
        # mark HASH_MISMATCH, leave src untouched.
        svc, plan, src, dst, _ = _build_setup(tmp_path)

        # Force a hash mismatch: monkeypatch the hash function to
        # return one value for src and a different value for dst.
        from curator.services import migration as migration_module
        call_count = [0]

        def fake_hash(path):
            call_count[0] += 1
            return "src_hash_aaaa" if call_count[0] == 1 else "dst_hash_bbbb"

        monkeypatch.setattr(migration_module, "_xxhash3_128_of_file", fake_hash)
        report = svc.apply(plan, verify_hash=True)
        result_move = report.moves[0]

        assert result_move.outcome == MigrationOutcome.HASH_MISMATCH
        assert "hash mismatch" in (result_move.error or "")
        # dst was cleaned up
        assert not dst.exists()
        # src was preserved
        assert src.exists()

    def test_hash_mismatch_unlink_oserror_is_swallowed(
        self, monkeypatch, tmp_path,
    ):
        # Lines 1034-1037: hash-mismatch cleanup catches OSError from
        # unlink (e.g. file already gone, permission issue) and
        # continues to set outcome+error.
        svc, plan, src, dst, _ = _build_setup(tmp_path)

        from curator.services import migration as migration_module
        call_count = [0]

        def fake_hash(path):
            call_count[0] += 1
            return "src_aaa" if call_count[0] == 1 else "dst_bbb"

        monkeypatch.setattr(migration_module, "_xxhash3_128_of_file", fake_hash)

        # Patch Path.unlink to raise OSError when called on dst.
        orig_unlink = Path.unlink

        def selective_unlink_raise(self, *args, **kwargs):
            if str(self) == str(dst):
                raise OSError("simulated unlink failure")
            return orig_unlink(self, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", selective_unlink_raise)
        report = svc.apply(plan, verify_hash=True)
        result_move = report.moves[0]

        # Outcome still set despite unlink failure; src preserved.
        assert result_move.outcome == MigrationOutcome.HASH_MISMATCH
        assert src.exists()

    def test_oserror_during_copy_cleans_dst_and_marks_failed(
        self, monkeypatch, tmp_path,
    ):
        # Lines 1070-1078: shutil.Error / OSError during copy →
        # dst.unlink() if it exists, then outcome=FAILED + error.
        svc, plan, src, dst, _ = _build_setup(tmp_path)
        # NOTE: do NOT pre-create dst -- that would trigger Gate 3
        # collision resolution before _execute_one. Instead, the fake
        # copy creates a partial dst then raises (simulating a copy that
        # fails mid-write).

        from curator.services import migration as migration_module

        def boom_copy(src_p, dst_p, *args, **kwargs):
            # Write a partial dst so the cleanup branch fires.
            Path(dst_p).parent.mkdir(parents=True, exist_ok=True)
            Path(dst_p).write_bytes(b"partial")
            raise OSError("simulated copy failure mid-write")

        monkeypatch.setattr(migration_module.shutil, "copy2", boom_copy)
        report = svc.apply(plan, verify_hash=False)
        result_move = report.moves[0]

        assert result_move.outcome == MigrationOutcome.FAILED
        assert "OSError" in (result_move.error or "")
        # dst was cleaned up
        assert not dst.exists()
        # src preserved
        assert src.exists()

    def test_oserror_during_copy_unlink_oserror_swallowed(
        self, monkeypatch, tmp_path,
    ):
        # Lines 1072-1076: nested try around the dst cleanup unlink also
        # catches OSError. Triggers when copy fails AND dst cleanup
        # also fails.
        svc, plan, src, dst, _ = _build_setup(tmp_path)
        # Same approach as above: partial-write-then-raise to bypass
        # Gate 3 collision.

        from curator.services import migration as migration_module

        def boom_copy(src_p, dst_p, *args, **kwargs):
            Path(dst_p).parent.mkdir(parents=True, exist_ok=True)
            Path(dst_p).write_bytes(b"partial")
            raise OSError("copy boom")

        orig_unlink = Path.unlink

        def selective_unlink(self, *args, **kwargs):
            if str(self) == str(dst):
                raise OSError("unlink boom")
            return orig_unlink(self, *args, **kwargs)

        monkeypatch.setattr(migration_module.shutil, "copy2", boom_copy)
        monkeypatch.setattr(Path, "unlink", selective_unlink)
        report = svc.apply(plan, verify_hash=False)
        result_move = report.moves[0]

        # Outcome still set despite both failures
        assert result_move.outcome == MigrationOutcome.FAILED
        assert src.exists()

    def test_defensive_exception_fallback(self, monkeypatch, tmp_path):
        # Lines 1079-1081: any non-OSError/non-shutil.Error exception
        # also marks FAILED. Defensive boundary.
        svc, plan, src, dst, _ = _build_setup(tmp_path)

        from curator.services import migration as migration_module

        def boom_copy(*a, **kw):
            raise RuntimeError("unexpected non-OS error")

        monkeypatch.setattr(migration_module.shutil, "copy2", boom_copy)
        report = svc.apply(plan, verify_hash=False)
        result_move = report.moves[0]

        assert result_move.outcome == MigrationOutcome.FAILED
        assert "RuntimeError" in (result_move.error or "")


# ===========================================================================
# _resolve_collision: 4 on-conflict modes via apply()
# ===========================================================================


class TestCollisionResolveModes:
    """Each on-conflict mode is exercised via apply() with a pre-existing
    dst file (which triggers _resolve_collision before _execute_one)."""

    def test_skip_mode_marks_collision_and_preserves_dst(self, tmp_path):
        # Lines 1928-1930: skip → SKIPPED_COLLISION, dst untouched.
        svc, plan, src, dst, _ = _build_setup(
            tmp_path, mode="skip", pre_existing_dst=True,
        )
        original_dst_bytes = dst.read_bytes()
        report = svc.apply(plan, verify_hash=False)
        result_move = report.moves[0]

        assert result_move.outcome == MigrationOutcome.SKIPPED_COLLISION
        assert dst.read_bytes() == original_dst_bytes
        assert src.exists()

    def test_fail_mode_raises_migration_conflict_error(self, tmp_path):
        # Lines 1932-1939: fail → FAILED_DUE_TO_CONFLICT + audit;
        # then apply() raises MigrationConflictError per Gate-3 logic.
        svc, plan, _, _, audit = _build_setup(
            tmp_path, mode="fail", pre_existing_dst=True, audit=True,
        )
        with pytest.raises(MigrationConflictError):
            svc.apply(plan, verify_hash=False)
        # Conflict audit emitted
        actions = [e["action"] for e in audit.entries]
        assert any("conflict" in a for a in actions)

    def test_overwrite_with_backup_success(self, tmp_path):
        # Lines 1941-1961: overwrite-with-backup renames dst → backup,
        # then proceeds with the move; outcome becomes
        # MOVED_OVERWROTE_WITH_BACKUP.
        svc, plan, src, dst, _ = _build_setup(
            tmp_path, mode="overwrite-with-backup", pre_existing_dst=True,
        )
        report = svc.apply(plan, verify_hash=False)
        result_move = report.moves[0]

        assert result_move.outcome == MigrationOutcome.MOVED_OVERWROTE_WITH_BACKUP
        # dst exists at the new location with src content
        assert dst.exists()
        assert dst.read_bytes() == b"source bytes"
        # A backup file exists in the same dir
        backups = [p for p in dst.parent.iterdir()
                   if "backup" in p.name or "curator-backup" in p.name]
        assert len(backups) >= 1

    def test_overwrite_with_backup_rename_oserror_fails_gracefully(
        self, monkeypatch, tmp_path,
    ):
        # Lines 1945-1956: dst.rename(backup_path) raises OSError →
        # FAILED_DUE_TO_CONFLICT + error message + audit.
        svc, plan, src, dst, audit = _build_setup(
            tmp_path, mode="overwrite-with-backup",
            pre_existing_dst=True, audit=True,
        )

        # Force the rename to fail when target name suggests a backup.
        orig_rename = Path.rename

        def selective_rename(self, target):
            target_str = str(target)
            if "backup" in target_str:
                raise OSError("simulated cross-volume rename failure")
            return orig_rename(self, target)

        monkeypatch.setattr(Path, "rename", selective_rename)
        report = svc.apply(plan, verify_hash=False)
        result_move = report.moves[0]

        assert result_move.outcome == MigrationOutcome.FAILED_DUE_TO_CONFLICT
        assert "backup rename failed" in (result_move.error or "")

    def test_rename_with_suffix_success_picks_free_name(self, tmp_path):
        # Lines 1963-1984: rename-with-suffix mutates move.dst_path to
        # a .curator-N name and proceeds; outcome becomes
        # MOVED_RENAMED_WITH_SUFFIX.
        svc, plan, src, original_dst, _ = _build_setup(
            tmp_path, mode="rename-with-suffix", pre_existing_dst=True,
        )
        original_dst_path_str = str(original_dst)
        report = svc.apply(plan, verify_hash=False)
        result_move = report.moves[0]

        assert result_move.outcome == MigrationOutcome.MOVED_RENAMED_WITH_SUFFIX
        # dst_path was mutated to a .curator-N name
        assert result_move.dst_path != original_dst_path_str
        assert ".curator-" in result_move.dst_path
        # New dst exists; original dst preserved
        assert Path(result_move.dst_path).exists()
        assert original_dst.exists()

    def test_rename_with_suffix_exhaustion_fails(
        self, monkeypatch, tmp_path,
    ):
        # Lines 1966-1973: _find_available_suffix raises RuntimeError
        # (exhaustion) → FAILED_DUE_TO_CONFLICT + audit.
        svc, plan, _, _, audit = _build_setup(
            tmp_path, mode="rename-with-suffix",
            pre_existing_dst=True, audit=True,
        )

        def boom_find(*a, **kw):
            raise RuntimeError("rename-with-suffix exhausted: 9999 attempts")

        monkeypatch.setattr(svc, "_find_available_suffix", boom_find)
        report = svc.apply(plan, verify_hash=False)
        result_move = report.moves[0]

        assert result_move.outcome == MigrationOutcome.FAILED_DUE_TO_CONFLICT
        assert "exhausted" in (result_move.error or "")

    def test_unknown_mode_falls_back_to_skip(self, tmp_path):
        # Lines 1986-1992: unknown on_conflict_mode (bypassing the
        # setter validation) logs a warning and falls back to skip.
        svc, plan, _, dst, _ = _build_setup(
            tmp_path, mode="skip", pre_existing_dst=True,
        )
        # Bypass the setter validation: directly set the private attribute.
        svc._on_conflict_mode = "totally-bogus-mode"
        original_dst_bytes = dst.read_bytes()
        report = svc.apply(plan, verify_hash=False)
        result_move = report.moves[0]

        # Defensive fallback: outcome is SKIPPED_COLLISION
        assert result_move.outcome == MigrationOutcome.SKIPPED_COLLISION
        # dst preserved
        assert dst.read_bytes() == original_dst_bytes
