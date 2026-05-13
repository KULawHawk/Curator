"""Focused unit tests for MigrationService persistent-path progress-side helpers.

Sub-ship 5a of the Migration Phase Gamma arc (v1.7.93a).
Scope plan: docs/MIGRATION_PHASE_GAMMA_SCOPE.md

Group A of the v1.7.93 split. Targets the *_for_progress family of
methods (persistent-path sisters of the apply-time methods covered in
v1.7.89-91) plus the cross-source bytes-transfer infrastructure that
all persistent-path cross-source code depends on:

* `_emit_progress_audit_conflict` (1672-1692) — audit emitter for
  conflict-resolution events with job_id cross-reference
* `_resolve_collision_for_progress` (2022-2105) — sister of
  `_resolve_collision`, 4-tuple return instead of 2-tuple
* `_cross_source_overwrite_with_backup_for_progress` (1713-1786)
* `_cross_source_rename_with_suffix_for_progress` (1805-1857)
* `_cross_source_transfer` body (2324-2408) — the actual bytes-transfer
  hook orchestration that all cross-source persistent code uses
* `_can_write_to_source` defensives (2218-2233)
* `_hook_first_result` defensives (2246-2261)
* `_read_bytes_via_hook` defensives (2281-2289)

v1.7.93b will cover the persistent-job lifecycle (`run_job`, `_worker_loop`,
`_execute_one_persistent_*`, `create_job`, worker pool).

Stubs reused from v1.7.89/90/91 (Lesson #84 still paying — pattern
dividends per Lesson #87).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable
from uuid import UUID, uuid4

import pytest

from curator.models.migration import MigrationProgress
from curator.services.migration import MigrationOutcome

from tests.unit.test_migration_plan_apply import (
    StubAuditRepository,
    make_service,
)
from tests.unit.test_migration_cross_source import (
    StubMigrationHooks,
    StubMigrationPluginManager,
)


# ===========================================================================
# Helpers
# ===========================================================================


def make_progress(
    *,
    job_id: UUID | None = None,
    curator_id: UUID | None = None,
    src_path: str = "/src/file.txt",
    dst_path: str = "/dst/file.txt",
    size: int = 100,
    src_xxhash: str | None = "src_hash",
    safety_level: str = "safe",
    status: str = "pending",
) -> MigrationProgress:
    return MigrationProgress(
        job_id=job_id or uuid4(),
        curator_id=curator_id or uuid4(),
        src_path=src_path,
        dst_path=dst_path,
        size=size,
        src_xxhash=src_xxhash,
        safety_level=safety_level,
        status=status,
    )


def _set_transfer_result(
    monkeypatch, svc,
    outcome: MigrationOutcome,
    actual_file_id: str = "/dst/file.txt",
    verified_hash: str | None = "src_hash",
):
    """Monkeypatch _cross_source_transfer to return canned result."""
    def fake_transfer(*, src_source_id, src_file_id, src_xxhash,
                      dst_source_id, dst_path, verify_hash):
        return (outcome, actual_file_id, verified_hash)
    monkeypatch.setattr(svc, "_cross_source_transfer", fake_transfer)


def _set_transfer_sequence(monkeypatch, svc, results: list[tuple]):
    """Monkeypatch _cross_source_transfer to yield a sequence of canned
    (outcome, file_id, hash) tuples — one per call. Useful for tests
    that walk through suffix-rename retries."""
    iterator = iter(results)

    def fake_transfer(**kwargs):
        try:
            return next(iterator)
        except StopIteration:
            return (MigrationOutcome.MOVED, "/dst/file.txt", "src_hash")
    monkeypatch.setattr(svc, "_cross_source_transfer", fake_transfer)


# ===========================================================================
# _emit_progress_audit_conflict (lines 1672-1692)
# ===========================================================================


class TestEmitProgressAuditConflict:
    def test_no_audit_early_returns(self):
        # Line 1672-1673: audit=None → no-op.
        svc = make_service(audit=None)
        progress = make_progress()
        svc._emit_progress_audit_conflict(progress, mode="skip")
        # Nothing to assert; just verify no exception.

    def test_emits_base_details_without_extra(self):
        # Lines 1674-1690: details_extra=None → base details only.
        audit = StubAuditRepository()
        svc = make_service(audit=audit)
        progress = make_progress(
            src_path="/x/a.txt", dst_path="/y/a.txt", size=42,
        )
        svc._emit_progress_audit_conflict(progress, mode="skip")
        assert len(audit.entries) == 1
        entry = audit.entries[0]
        assert entry["action"] == "migration.conflict_resolved"
        assert entry["details"]["src_path"] == "/x/a.txt"
        assert entry["details"]["dst_path"] == "/y/a.txt"
        assert entry["details"]["mode"] == "skip"
        assert entry["details"]["size"] == 42
        assert entry["details"]["job_id"] == str(progress.job_id)
        # No extra fields
        assert "cross_source" not in entry["details"]

    def test_emits_with_details_extra_merged(self):
        # Lines 1681-1682: details_extra=dict → merged on top of base.
        audit = StubAuditRepository()
        svc = make_service(audit=audit)
        progress = make_progress()
        svc._emit_progress_audit_conflict(
            progress,
            mode="overwrite-with-backup",
            details_extra={"cross_source": True, "backup_name": "x.curator-bak"},
        )
        details = audit.entries[0]["details"]
        assert details["mode"] == "overwrite-with-backup"
        assert details["cross_source"] is True
        assert details["backup_name"] == "x.curator-bak"

    def test_audit_log_exception_is_swallowed(self):
        # Lines 1691-1695: audit.log raises → warning, doesn't propagate.
        audit = StubAuditRepository()

        def boom(**kw):
            raise RuntimeError("audit DB locked")
        audit.log = boom
        svc = make_service(audit=audit)
        progress = make_progress()
        # Must not raise.
        svc._emit_progress_audit_conflict(progress, mode="skip")


# ===========================================================================
# _resolve_collision_for_progress (lines 2022-2105)
# ===========================================================================


class TestResolveCollisionForProgress:
    def test_skip_mode_returns_short_circuit_no_audit(self, tmp_path):
        # Lines 2050-2051: mode=skip → (True, None, None, None), no audit.
        audit = StubAuditRepository()
        svc = make_service(audit=audit)
        svc.set_on_conflict_mode("skip")
        progress = make_progress()
        dst_p = tmp_path / "x.txt"

        result = svc._resolve_collision_for_progress(progress, dst_p)
        assert result == (True, None, None, None)
        # No audit emitted for skip
        assert len(audit.entries) == 0

    def test_fail_mode_returns_error_and_audits(self, tmp_path):
        # Lines 2053-2059: mode=fail → (True, None, None, err) + audit.
        audit = StubAuditRepository()
        svc = make_service(audit=audit)
        svc.set_on_conflict_mode("fail")
        progress = make_progress(dst_path=str(tmp_path / "x.txt"))
        dst_p = tmp_path / "x.txt"

        sc, outcome, new_dst, err = svc._resolve_collision_for_progress(
            progress, dst_p,
        )
        assert sc is True
        assert outcome is None
        assert new_dst is None
        assert err is not None
        assert "destination already exists" in err
        # Audit emitted with mode=fail
        modes = [e["details"]["mode"] for e in audit.entries]
        assert "fail" in modes

    def test_overwrite_with_backup_success(self, tmp_path):
        # Lines 2061-2079: mode=overwrite-with-backup, rename succeeds
        # → (False, MOVED_OVERWROTE_WITH_BACKUP, None, None) + audit.
        audit = StubAuditRepository()
        svc = make_service(audit=audit)
        svc.set_on_conflict_mode("overwrite-with-backup")
        dst_p = tmp_path / "x.txt"
        dst_p.write_bytes(b"existing")  # backup rename has a real target
        progress = make_progress(dst_path=str(dst_p))

        sc, outcome, new_dst, err = svc._resolve_collision_for_progress(
            progress, dst_p,
        )
        assert sc is False
        assert outcome == MigrationOutcome.MOVED_OVERWROTE_WITH_BACKUP
        assert new_dst is None
        assert err is None
        # dst_p was renamed to backup_path; original gone
        assert not dst_p.exists()
        # Audit emitted
        modes = [e["details"]["mode"] for e in audit.entries]
        assert "overwrite-with-backup" in modes

    def test_overwrite_with_backup_oserror_returns_error_and_audits(
        self, tmp_path, monkeypatch,
    ):
        # Lines 2065-2074: dst_p.rename raises OSError → (True, None, None, err)
        # + audit "overwrite-with-backup-failed".
        audit = StubAuditRepository()
        svc = make_service(audit=audit)
        svc.set_on_conflict_mode("overwrite-with-backup")
        dst_p = tmp_path / "x.txt"
        dst_p.write_bytes(b"existing")
        progress = make_progress(dst_path=str(dst_p))

        orig_rename = Path.rename

        def boom_rename(self, target, *args, **kwargs):
            if str(self) == str(dst_p):
                raise OSError("rename blocked")
            return orig_rename(self, target, *args, **kwargs)
        monkeypatch.setattr(Path, "rename", boom_rename)

        sc, outcome, new_dst, err = svc._resolve_collision_for_progress(
            progress, dst_p,
        )
        assert sc is True
        assert outcome is None
        assert new_dst is None
        assert err is not None
        assert "backup rename failed" in err
        # Audit emitted with "overwrite-with-backup-failed"
        modes = [e["details"]["mode"] for e in audit.entries]
        assert "overwrite-with-backup-failed" in modes

    def test_rename_with_suffix_success(self, tmp_path):
        # Lines 2081-2098: mode=rename-with-suffix, find succeeds
        # → (False, MOVED_RENAMED_WITH_SUFFIX, new_path, None) + audit.
        audit = StubAuditRepository()
        svc = make_service(audit=audit)
        svc.set_on_conflict_mode("rename-with-suffix")
        dst_p = tmp_path / "x.txt"
        dst_p.write_bytes(b"existing")
        progress = make_progress(dst_path=str(dst_p))

        sc, outcome, new_dst, err = svc._resolve_collision_for_progress(
            progress, dst_p,
        )
        assert sc is False
        assert outcome == MigrationOutcome.MOVED_RENAMED_WITH_SUFFIX
        assert new_dst is not None
        assert str(new_dst).endswith(".txt")
        assert "curator-1" in str(new_dst)
        assert err is None
        # Audit emitted
        modes = [e["details"]["mode"] for e in audit.entries]
        assert "rename-with-suffix" in modes

    def test_rename_with_suffix_exhaustion_returns_error_and_audits(
        self, tmp_path, monkeypatch,
    ):
        # Lines 2082-2089: _find_available_suffix raises RuntimeError (9999
        # exhausted) → (True, None, None, str(e)) + audit
        # "rename-with-suffix-failed".
        audit = StubAuditRepository()
        svc = make_service(audit=audit)
        svc.set_on_conflict_mode("rename-with-suffix")
        dst_p = tmp_path / "x.txt"
        progress = make_progress(dst_path=str(dst_p))

        def boom_find(self_dst):
            raise RuntimeError("suffix exhaustion at [1, 9999]")
        monkeypatch.setattr(svc, "_find_available_suffix", boom_find)

        sc, outcome, new_dst, err = svc._resolve_collision_for_progress(
            progress, dst_p,
        )
        assert sc is True
        assert outcome is None
        assert new_dst is None
        assert err is not None
        assert "suffix exhaustion" in err
        modes = [e["details"]["mode"] for e in audit.entries]
        assert "rename-with-suffix-failed" in modes

    def test_unknown_mode_returns_skip_default(self, tmp_path):
        # Lines 2100-2105: unknown mode (defensive) → (True, None, None, None)
        # + warning. set_on_conflict_mode validates, but the defensive
        # arm is exercised by bypassing the setter.
        audit = StubAuditRepository()
        svc = make_service(audit=audit)
        # Bypass the setter's validation to inject a bogus mode.
        svc._on_conflict_mode = "BOGUS_MODE_XYZ"
        progress = make_progress()
        dst_p = tmp_path / "x.txt"

        result = svc._resolve_collision_for_progress(progress, dst_p)
        assert result == (True, None, None, None)

    def test_emit_audit_inner_skips_when_audit_none(self, tmp_path):
        # The nested _emit_audit's `if self.audit is None: return` is the
        # path taken when service has no audit at all but a non-skip mode
        # still tries to emit. Covers branch 2025-2026.
        svc = make_service(audit=None)
        svc.set_on_conflict_mode("fail")
        progress = make_progress(dst_path=str(tmp_path / "x.txt"))
        dst_p = tmp_path / "x.txt"

        sc, outcome, new_dst, err = svc._resolve_collision_for_progress(
            progress, dst_p,
        )
        # Same return shape as the audit-emitting case
        assert sc is True
        assert err is not None and "destination already exists" in err

    def test_emit_audit_inner_swallows_log_exception(self, tmp_path):
        # The nested _emit_audit's `except Exception` (2044-2048) — exercised
        # by making audit.log raise during a fail-mode resolution.
        audit = StubAuditRepository()

        def boom(**kw):
            raise RuntimeError("audit boom")
        audit.log = boom
        svc = make_service(audit=audit)
        svc.set_on_conflict_mode("fail")
        progress = make_progress(dst_path=str(tmp_path / "x.txt"))
        dst_p = tmp_path / "x.txt"

        # Must not raise.
        sc, outcome, new_dst, err = svc._resolve_collision_for_progress(
            progress, dst_p,
        )
        assert sc is True
        assert err is not None


# ===========================================================================
# _cross_source_overwrite_with_backup_for_progress (lines 1713-1786)
# ===========================================================================


class TestCrossSourceOverwriteBackupForProgress:
    def test_existing_file_id_none_degrades_to_skip(self, monkeypatch):
        # Lines 1716-1731: _find_existing_dst_file_id_for_overwrite
        # returns None → emit degraded audit + return None.
        audit = StubAuditRepository()
        svc = make_service(audit=audit)
        progress = make_progress()
        monkeypatch.setattr(
            svc, "_find_existing_dst_file_id_for_overwrite",
            lambda *a, **kw: None,
        )

        result = svc._cross_source_overwrite_with_backup_for_progress(
            progress, verify_hash=False,
            src_source_id="local", dst_source_id="gdrive",
        )
        assert result is None
        modes = [e["details"]["mode"] for e in audit.entries]
        assert "overwrite-with-backup-degraded-cross-source" in modes
        details = next(
            e["details"] for e in audit.entries
            if e["details"]["mode"] == "overwrite-with-backup-degraded-cross-source"
        )
        assert "could not resolve existing dst file_id" in details["reason"]

    def test_backup_rename_fails_degrades_to_skip(self, monkeypatch):
        # Lines 1737-1752: _attempt_cross_source_backup_rename returns
        # (False, error) → emit degraded audit + return None.
        audit = StubAuditRepository()
        svc = make_service(audit=audit)
        progress = make_progress()
        monkeypatch.setattr(
            svc, "_find_existing_dst_file_id_for_overwrite",
            lambda *a, **kw: "existing_file_id_123",
        )
        monkeypatch.setattr(
            svc, "_attempt_cross_source_backup_rename",
            lambda *a, **kw: (False, "plugin does not implement rename"),
        )

        result = svc._cross_source_overwrite_with_backup_for_progress(
            progress, verify_hash=False,
            src_source_id="local", dst_source_id="gdrive",
        )
        assert result is None
        modes = [e["details"]["mode"] for e in audit.entries]
        assert "overwrite-with-backup-degraded-cross-source" in modes

    def test_retry_returns_moved_yields_success(self, monkeypatch):
        # Lines 1754-1786: rename succeeds → audit + retry transfer
        # returns MOVED → return success tuple.
        audit = StubAuditRepository()
        svc = make_service(audit=audit)
        progress = make_progress()
        monkeypatch.setattr(
            svc, "_find_existing_dst_file_id_for_overwrite",
            lambda *a, **kw: "existing_id",
        )
        monkeypatch.setattr(
            svc, "_attempt_cross_source_backup_rename",
            lambda *a, **kw: (True, None),
        )
        _set_transfer_result(
            monkeypatch, svc,
            MigrationOutcome.MOVED,
            actual_file_id="new_dst_id", verified_hash="hash_v",
        )

        result = svc._cross_source_overwrite_with_backup_for_progress(
            progress, verify_hash=True,
            src_source_id="local", dst_source_id="gdrive",
        )
        assert result == (MigrationOutcome.MOVED, "new_dst_id", "hash_v")
        # Audit shows the rename happened
        modes = [e["details"]["mode"] for e in audit.entries]
        assert "overwrite-with-backup" in modes

    def test_retry_returns_hash_mismatch_surfaces(self, monkeypatch):
        # Lines 1782-1785: retry returns HASH_MISMATCH → propagate.
        audit = StubAuditRepository()
        svc = make_service(audit=audit)
        progress = make_progress()
        monkeypatch.setattr(
            svc, "_find_existing_dst_file_id_for_overwrite",
            lambda *a, **kw: "existing_id",
        )
        monkeypatch.setattr(
            svc, "_attempt_cross_source_backup_rename",
            lambda *a, **kw: (True, None),
        )
        _set_transfer_result(
            monkeypatch, svc,
            MigrationOutcome.HASH_MISMATCH,
            actual_file_id="dst_id", verified_hash="bad_hash",
        )

        result = svc._cross_source_overwrite_with_backup_for_progress(
            progress, verify_hash=True,
            src_source_id="local", dst_source_id="gdrive",
        )
        assert result == (MigrationOutcome.HASH_MISMATCH, "dst_id", "bad_hash")

    def test_retry_returns_skipped_collision_surfaces(self, monkeypatch):
        # Lines 1782-1785: retry returns SKIPPED_COLLISION (race condition)
        # → propagate.
        audit = StubAuditRepository()
        svc = make_service(audit=audit)
        progress = make_progress()
        monkeypatch.setattr(
            svc, "_find_existing_dst_file_id_for_overwrite",
            lambda *a, **kw: "existing_id",
        )
        monkeypatch.setattr(
            svc, "_attempt_cross_source_backup_rename",
            lambda *a, **kw: (True, None),
        )
        _set_transfer_result(
            monkeypatch, svc,
            MigrationOutcome.SKIPPED_COLLISION,
            actual_file_id="dst_id", verified_hash=None,
        )

        result = svc._cross_source_overwrite_with_backup_for_progress(
            progress, verify_hash=True,
            src_source_id="local", dst_source_id="gdrive",
        )
        assert result == (MigrationOutcome.SKIPPED_COLLISION, "dst_id", None)


# ===========================================================================
# _cross_source_rename_with_suffix_for_progress (lines 1805-1857)
# ===========================================================================


class TestCrossSourceRenameWithSuffixForProgress:
    def test_first_suffix_succeeds(self, monkeypatch):
        # Lines 1805-1840: first candidate (n=1) succeeds → return MOVED.
        audit = StubAuditRepository()
        svc = make_service(audit=audit)
        progress = make_progress(dst_path="/dst/file.txt")
        _set_transfer_result(
            monkeypatch, svc,
            MigrationOutcome.MOVED,
            actual_file_id="/dst/file.curator-1.txt", verified_hash="h",
        )

        result = svc._cross_source_rename_with_suffix_for_progress(
            progress, verify_hash=True,
            src_source_id="local", dst_source_id="gdrive",
        )
        assert result is not None
        outcome, file_id, vhash = result
        assert outcome == MigrationOutcome.MOVED
        assert file_id == "/dst/file.curator-1.txt"
        # Audit emitted with suffix_n=1
        suffix_audits = [
            e for e in audit.entries
            if e["details"]["mode"] == "rename-with-suffix"
        ]
        assert len(suffix_audits) == 1
        assert suffix_audits[0]["details"]["suffix_n"] == 1

    def test_third_suffix_succeeds_after_two_collisions(self, monkeypatch):
        # Line 1820-1821: first two candidates return SKIPPED_COLLISION →
        # `continue` loop; third succeeds.
        audit = StubAuditRepository()
        svc = make_service(audit=audit)
        progress = make_progress(dst_path="/dst/file.txt")
        _set_transfer_sequence(monkeypatch, svc, [
            (MigrationOutcome.SKIPPED_COLLISION, "/dst/c1", None),
            (MigrationOutcome.SKIPPED_COLLISION, "/dst/c2", None),
            (MigrationOutcome.MOVED, "/dst/file.curator-3.txt", "h"),
        ])

        result = svc._cross_source_rename_with_suffix_for_progress(
            progress, verify_hash=True,
            src_source_id="local", dst_source_id="gdrive",
        )
        assert result is not None
        outcome, file_id, _ = result
        assert outcome == MigrationOutcome.MOVED
        # Audit suffix_n=3
        suffix_audits = [
            e for e in audit.entries
            if e["details"]["mode"] == "rename-with-suffix"
        ]
        assert suffix_audits[0]["details"]["suffix_n"] == 3

    def test_hash_mismatch_on_retry_surfaces(self, monkeypatch):
        # Lines 1822-1825: HASH_MISMATCH on a suffix retry → propagate
        # (no point retrying the same src bytes).
        audit = StubAuditRepository()
        svc = make_service(audit=audit)
        progress = make_progress(dst_path="/dst/file.txt")
        _set_transfer_result(
            monkeypatch, svc,
            MigrationOutcome.HASH_MISMATCH,
            actual_file_id="/dst/file.curator-1.txt", verified_hash="bad",
        )

        result = svc._cross_source_rename_with_suffix_for_progress(
            progress, verify_hash=True,
            src_source_id="local", dst_source_id="gdrive",
        )
        assert result is not None
        outcome, _, vhash = result
        assert outcome == MigrationOutcome.HASH_MISMATCH
        assert vhash == "bad"

    def test_9999_exhaustion_degrades_to_none(self, monkeypatch):
        # Lines 1842-1857: 9999 candidates all return SKIPPED_COLLISION →
        # emit degraded audit + return None.
        audit = StubAuditRepository()
        svc = make_service(audit=audit)
        progress = make_progress(dst_path="/dst/file.txt")

        def always_collide(**kwargs):
            return (MigrationOutcome.SKIPPED_COLLISION, "/dst/c", None)
        monkeypatch.setattr(svc, "_cross_source_transfer", always_collide)

        result = svc._cross_source_rename_with_suffix_for_progress(
            progress, verify_hash=False,
            src_source_id="local", dst_source_id="gdrive",
        )
        assert result is None
        modes = [e["details"]["mode"] for e in audit.entries]
        assert "rename-with-suffix-degraded-cross-source" in modes


# ===========================================================================
# _cross_source_transfer (lines 2293-2408)
# ===========================================================================


def _setup_pm_for_transfer(
    read_bytes_chunks: list[bytes | None] | None = None,
    write_result: Any | object = ...,
    write_raises: Exception | None = None,
    read_back_chunks: list[bytes | None] | None = None,
    delete_result: Any = SimpleNamespace(),
):
    """Build a StubMigrationPluginManager wired for cross-source transfer.

    `read_bytes_chunks` is consumed by repeated `curator_source_read_bytes`
    calls (src first, then dst-readback if verify_hash=True). Use None to
    signal EOF on the first chunk (no plugin handles this source).
    """
    pm = StubMigrationPluginManager()

    # The read hook is called twice in the happy path: once for src,
    # once for dst-verify. We supply BOTH sequences via a single FIFO
    # queue (src chunks first, then read-back chunks).
    chunks: list[bytes | None] = []
    if read_bytes_chunks is not None:
        chunks.extend(read_bytes_chunks)
    if read_back_chunks is not None:
        chunks.extend(read_back_chunks)
    chunk_iter = iter(chunks)

    def read_bytes(**kwargs):
        try:
            chunk = next(chunk_iter)
        except StopIteration:
            return [None]
        return [chunk] if chunk is not None else [None]
    pm.set_hook("curator_source_read_bytes", read_bytes)

    def write(**kwargs):
        if write_raises is not None:
            raise write_raises
        if write_result is ...:
            return [SimpleNamespace(file_id=kwargs.get("name", "dst_id"))]
        return [write_result] if write_result is not None else [None]
    pm.set_hook("curator_source_write", write)

    def delete(**kwargs):
        return [delete_result]
    pm.set_hook("curator_source_delete", delete)

    def post(**kwargs):
        return [None]
    pm.set_hook("curator_source_write_post", post)

    return pm


class TestCrossSourceTransfer:
    def test_happy_path_returns_moved_with_verified_hash(self):
        # Lines 2323-2408: happy path through read → write → verify.
        # Same bytes on src and dst → hash matches → MOVED.
        # Short data → loop exits on short-read; no b"" terminator needed.
        src_data = b"hello world"
        pm = _setup_pm_for_transfer(
            read_bytes_chunks=[src_data],
            read_back_chunks=[src_data],
        )
        svc = make_service()
        svc.pm = pm

        outcome, file_id, vhash = svc._cross_source_transfer(
            src_source_id="local", src_file_id="/src/a.txt",
            src_xxhash=None,
            dst_source_id="gdrive", dst_path="/dst/a.txt",
            verify_hash=True,
        )
        assert outcome == MigrationOutcome.MOVED
        assert vhash is not None  # computed from dst bytes

    def test_src_bytes_none_raises_runtime_error(self):
        # Lines 2325-2329: src read returns None → no plugin handles → raise.
        pm = _setup_pm_for_transfer(read_bytes_chunks=[None])
        svc = make_service()
        svc.pm = pm

        with pytest.raises(RuntimeError, match="no plugin handled curator_source_read_bytes"):
            svc._cross_source_transfer(
                src_source_id="local", src_file_id="/src/a.txt",
                src_xxhash=None,
                dst_source_id="gdrive", dst_path="/dst/a.txt",
                verify_hash=False,
            )

    def test_write_file_exists_returns_skipped_collision(self):
        # Lines 2352-2353: write raises FileExistsError → SKIPPED_COLLISION.
        pm = _setup_pm_for_transfer(
            read_bytes_chunks=[b"data"],
            write_raises=FileExistsError("dst exists"),
        )
        svc = make_service()
        svc.pm = pm

        outcome, file_id, vhash = svc._cross_source_transfer(
            src_source_id="local", src_file_id="/src/a.txt",
            src_xxhash=None,
            dst_source_id="gdrive", dst_path="/dst/a.txt",
            verify_hash=False,
        )
        assert outcome == MigrationOutcome.SKIPPED_COLLISION
        assert file_id == "/dst/a.txt"
        assert vhash is None

    def test_write_returns_none_raises_runtime_error(self):
        # Lines 2354-2358: write hook returns None → no plugin handles → raise.
        pm = _setup_pm_for_transfer(
            read_bytes_chunks=[b"data"],
            write_result=None,
        )
        svc = make_service()
        svc.pm = pm

        with pytest.raises(RuntimeError, match="no plugin handled curator_source_write"):
            svc._cross_source_transfer(
                src_source_id="local", src_file_id="/src/a.txt",
                src_xxhash=None,
                dst_source_id="gdrive", dst_path="/dst/a.txt",
                verify_hash=False,
            )

    def test_verify_dst_unreadable_raises_runtime_error(self):
        # Lines 2367-2378: verify=True but dst read-back returns None →
        # delete dst (best-effort) + raise "couldn't re-read dst".
        pm = _setup_pm_for_transfer(
            read_bytes_chunks=[b"data"],
            read_back_chunks=[None],  # dst read-back fails
        )
        svc = make_service()
        svc.pm = pm

        with pytest.raises(RuntimeError, match="couldn't re-read dst"):
            svc._cross_source_transfer(
                src_source_id="local", src_file_id="/src/a.txt",
                src_xxhash=None,
                dst_source_id="gdrive", dst_path="/dst/a.txt",
                verify_hash=True,
            )

    def test_verify_hash_mismatch_deletes_dst_returns_mismatch(self):
        # Lines 2380-2391: src bytes ≠ dst bytes (different hashes) →
        # delete dst + return HASH_MISMATCH.
        pm = _setup_pm_for_transfer(
            read_bytes_chunks=[b"source bytes"],
            read_back_chunks=[b"different bytes"],
        )
        svc = make_service()
        svc.pm = pm

        outcome, file_id, vhash = svc._cross_source_transfer(
            src_source_id="local", src_file_id="/src/a.txt",
            src_xxhash=None,
            dst_source_id="gdrive", dst_path="/dst/a.txt",
            verify_hash=True,
        )
        assert outcome == MigrationOutcome.HASH_MISMATCH
        assert vhash is not None  # computed from the bad dst bytes

    def test_verify_hash_false_skips_verify(self):
        # Lines 2362-2391 skipped: verify_hash=False → no read-back, no
        # mismatch check, returns MOVED with vhash=None.
        pm = _setup_pm_for_transfer(
            read_bytes_chunks=[b"data"],
        )
        svc = make_service()
        svc.pm = pm

        outcome, file_id, vhash = svc._cross_source_transfer(
            src_source_id="local", src_file_id="/src/a.txt",
            src_xxhash=None,
            dst_source_id="gdrive", dst_path="/dst/a.txt",
            verify_hash=False,
        )
        assert outcome == MigrationOutcome.MOVED
        assert vhash is None

    def test_src_xxhash_provided_skips_compute(self):
        # Lines 2332-2336: src_xxhash given → use cached, skip xxh3_128.
        # Re-read of dst must compute its hash to compare against the
        # cached src_xxhash; we use bytes that happen to match the cached
        # value so the test asserts the cache was actually used.
        import xxhash
        src_data = b"some payload bytes"
        cached_hash = xxhash.xxh3_128(src_data).hexdigest()
        pm = _setup_pm_for_transfer(
            read_bytes_chunks=[src_data],
            read_back_chunks=[src_data],
        )
        svc = make_service()
        svc.pm = pm

        outcome, file_id, vhash = svc._cross_source_transfer(
            src_source_id="local", src_file_id="/src/a.txt",
            src_xxhash=cached_hash,
            dst_source_id="gdrive", dst_path="/dst/a.txt",
            verify_hash=True,
        )
        assert outcome == MigrationOutcome.MOVED
        assert vhash == cached_hash


# ===========================================================================
# _invoke_post_write_hook (lines 2438-2449)
# ===========================================================================


class TestInvokePostWriteHook:
    def test_pm_none_returns_silently(self):
        # Line 2438-2439: pm=None → silent no-op.
        svc = make_service()
        svc.pm = None
        # Must not raise.
        svc._invoke_post_write_hook(
            source_id="x", file_id="y",
            src_xxhash=None, written_bytes_len=0,
        )

    def test_missing_hookspec_returns_silently(self):
        # Lines 2442-2443: getattr raises AttributeError when the
        # hookspec isn't registered → graceful no-op.
        pm = StubMigrationPluginManager()
        pm.remove_hook("curator_source_write_post")
        svc = make_service()
        svc.pm = pm
        # Must not raise.
        svc._invoke_post_write_hook(
            source_id="x", file_id="y",
            src_xxhash=None, written_bytes_len=0,
        )


# ===========================================================================
# _can_write_to_source (lines 2210-2233)
# ===========================================================================


class TestCanWriteToSource:
    def test_pm_none_returns_false(self):
        svc = make_service()
        svc.pm = None
        assert svc._can_write_to_source("local") is False

    def test_register_exception_returns_false(self):
        # Lines 2222-2223: defensive against plugin raising during
        # register.
        pm = StubMigrationPluginManager()

        def boom(**kw):
            raise RuntimeError("bad plugin")
        pm.set_hook("curator_source_register", boom)
        svc = make_service()
        svc.pm = pm
        assert svc._can_write_to_source("local") is False

    def test_infos_not_list_is_wrapped(self):
        # Line 2225: single non-list info → wrapped to [info].
        pm = StubMigrationPluginManager()
        info = SimpleNamespace(source_type="local", supports_write=True)

        def reg(**kw):
            return info  # NOT a list — pluggy collapse to single
        pm.set_hook("curator_source_register", reg)
        svc = make_service()
        svc.pm = pm
        assert svc._can_write_to_source("local") is True

    def test_none_in_infos_list_is_skipped(self):
        # Lines 2227-2228: info is None → skip.
        pm = StubMigrationPluginManager()
        good = SimpleNamespace(source_type="local", supports_write=True)

        def reg(**kw):
            return [None, good]
        pm.set_hook("curator_source_register", reg)
        svc = make_service()
        svc.pm = pm
        assert svc._can_write_to_source("local") is True

    def test_source_type_exact_match(self):
        pm = StubMigrationPluginManager()
        info = SimpleNamespace(source_type="local", supports_write=True)
        pm.set_hook("curator_source_register", lambda **kw: [info])
        svc = make_service()
        svc.pm = pm
        assert svc._can_write_to_source("local") is True

    def test_source_type_prefix_match(self):
        # "local:my_drive" starts with "local:" → matches source_type "local".
        pm = StubMigrationPluginManager()
        info = SimpleNamespace(source_type="local", supports_write=True)
        pm.set_hook("curator_source_register", lambda **kw: [info])
        svc = make_service()
        svc.pm = pm
        assert svc._can_write_to_source("local:my_drive") is True

    def test_supports_write_false_returns_false(self):
        # Line 2232: getattr(info, supports_write, False) is False.
        pm = StubMigrationPluginManager()
        info = SimpleNamespace(source_type="local", supports_write=False)
        pm.set_hook("curator_source_register", lambda **kw: [info])
        svc = make_service()
        svc.pm = pm
        assert svc._can_write_to_source("local") is False

    def test_no_match_returns_false(self):
        # Line 2233: loop exhausts without matching source_id → False.
        pm = StubMigrationPluginManager()
        info = SimpleNamespace(source_type="gdrive", supports_write=True)
        pm.set_hook("curator_source_register", lambda **kw: [info])
        svc = make_service()
        svc.pm = pm
        assert svc._can_write_to_source("local") is False


# ===========================================================================
# _hook_first_result (lines 2235-2261)
# ===========================================================================


class TestHookFirstResult:
    def test_pm_none_returns_none(self):
        svc = make_service()
        svc.pm = None
        assert svc._hook_first_result("any_hook") is None

    def test_missing_hook_returns_none(self):
        # Lines 2245-2247: getattr(pm.hook, hook_name) raises
        # AttributeError → return None.
        pm = StubMigrationPluginManager()
        pm.remove_hook("ghost_hook")
        svc = make_service()
        svc.pm = pm
        assert svc._hook_first_result("ghost_hook") is None

    def test_file_exists_error_propagates(self):
        # Lines 2250-2253: FileExistsError is significant; re-raise.
        pm = StubMigrationPluginManager()

        def boom(**kw):
            raise FileExistsError("dst exists")
        pm.set_hook("curator_source_write", boom)
        svc = make_service()
        svc.pm = pm
        with pytest.raises(FileExistsError):
            svc._hook_first_result(
                "curator_source_write",
                source_id="x", parent_id="/", name="y",
                data=b"", mtime=None, overwrite=False,
            )

    def test_other_exception_returns_none(self):
        # Lines 2254-2258: any other exception → warning + None.
        pm = StubMigrationPluginManager()

        def boom(**kw):
            raise RuntimeError("plugin crashed")
        pm.set_hook("curator_source_read_bytes", boom)
        svc = make_service()
        svc.pm = pm
        result = svc._hook_first_result(
            "curator_source_read_bytes",
            source_id="x", file_id="y", offset=0, length=10,
        )
        assert result is None

    def test_results_not_list_returns_value(self):
        # Lines 2259-2260: results is single (non-list) value → return it.
        pm = StubMigrationPluginManager()

        def single(**kw):
            return "single_value"
        pm.set_hook("custom_hook", single)
        svc = make_service()
        svc.pm = pm
        assert svc._hook_first_result("custom_hook") == "single_value"

    def test_results_list_returns_first_non_none(self):
        # Line 2261: results is a list → first non-None.
        pm = StubMigrationPluginManager()

        def hook(**kw):
            return [None, None, "found", "later"]
        pm.set_hook("custom_hook", hook)
        svc = make_service()
        svc.pm = pm
        assert svc._hook_first_result("custom_hook") == "found"

    def test_results_list_all_none_returns_none(self):
        # Line 2261: all None → next() returns the default None.
        pm = StubMigrationPluginManager()

        def hook(**kw):
            return [None, None]
        pm.set_hook("custom_hook", hook)
        svc = make_service()
        svc.pm = pm
        assert svc._hook_first_result("custom_hook") is None


# ===========================================================================
# _read_bytes_via_hook (lines 2263-2290)
# ===========================================================================


class TestReadBytesViaHook:
    def test_first_chunk_none_returns_none(self):
        # Lines 2280-2283: first chunk (offset=0) is None → no plugin
        # handles this source → return None.
        pm = _setup_pm_for_transfer(read_bytes_chunks=[None])
        svc = make_service()
        svc.pm = pm
        result = svc._read_bytes_via_hook("unknown_source", "/x/y")
        assert result is None

    def test_short_read_terminates_loop(self):
        # Lines 2287-2288: a chunk shorter than _HASH_CHUNK_SIZE signals
        # last chunk; loop exits with bytes joined.
        pm = _setup_pm_for_transfer(read_bytes_chunks=[b"short"])
        svc = make_service()
        svc.pm = pm
        result = svc._read_bytes_via_hook("local", "/x/y")
        assert result == b"short"

    def test_empty_bytes_signals_eof(self):
        # Lines 2284-2285: empty bytes mid-loop = EOF.
        from curator.services.migration import _HASH_CHUNK_SIZE
        # First chunk is exactly _HASH_CHUNK_SIZE (does NOT exit via
        # short-read); second chunk is empty (EOF).
        chunk = b"a" * _HASH_CHUNK_SIZE
        pm = _setup_pm_for_transfer(read_bytes_chunks=[chunk, b""])
        svc = make_service()
        svc.pm = pm
        result = svc._read_bytes_via_hook("local", "/x/y")
        assert result == chunk

    def test_none_mid_loop_is_eof_returns_joined(self):
        # Lines 2280-2283: chunk is None mid-loop (offset > 0) →
        # `break` to return joined bytes.
        from curator.services.migration import _HASH_CHUNK_SIZE
        chunk = b"b" * _HASH_CHUNK_SIZE
        pm = _setup_pm_for_transfer(read_bytes_chunks=[chunk, None])
        svc = make_service()
        svc.pm = pm
        result = svc._read_bytes_via_hook("local", "/x/y")
        assert result == chunk
