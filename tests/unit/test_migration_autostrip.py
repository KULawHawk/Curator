"""Focused unit tests for MigrationService auto-strip metadata + small
defensive boundaries left over after Clusters 1-5.

Sub-ship 4 of the Migration Phase Gamma arc (v1.7.92).
Scope plan: docs/MIGRATION_PHASE_GAMMA_SCOPE.md

This sub-ship is smaller than originally scoped because the
`_cross_source_rename_with_suffix` body (1397-1465) turned out to
already be covered by integration tests. v1.7.92 closes:

* `_auto_strip_metadata` body (lines 873-955) — needs MetadataStripper stub
* `_update_index` entity-vanished path (line 1470) — same-source variant
* `_trash_source` exception path (lines 1486-1491) — send2trash error
* `_audit_conflict` exception path (lines 1892-1893) — audit.log error
* apply() autostrip dispatch line (830) — apply() integration that
  exercises the `_auto_strip_metadata(move)` call after a successful move
* `_auto_strip_metadata` audit-None-with-STRIPPED branch (896→exit)
* `_auto_strip_metadata` SKIPPED-with-no-tmp branch (912→917)
* `_audit_move` / `_audit_copy` source-IDs-None branches (2131→2135,
  2175→2179) and `_audit_copy` defensive except (2187-2188)

Stubs from v1.7.89/90/91 reused (Lesson #84 still paying).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

import pytest

from curator.models.file import FileEntity
from curator.models.source import SourceConfig
from curator.services.metadata_stripper import StripOutcome, StripResult
from curator.services.migration import (
    MigrationMove,
    MigrationOutcome,
    MigrationPlan,
)
from curator.services.safety import SafetyLevel

from tests.unit.test_migration_plan_apply import (
    NOW,
    StubAuditRepository,
    StubFileRepository,
    StubSourceRepository,
    make_service,
)


# ===========================================================================
# StubMetadataStripper
# ===========================================================================


@dataclass
class StubMetadataStripper:
    """Configurable stripper. Tests set `result` or `raise_exc` to control
    `strip_file` behavior. If `create_tmp` is True, also writes the tmp_path
    file so the auto-strip atomic-replace branch fires."""

    result: StripResult | None = None
    raise_exc: Exception | None = None
    create_tmp: bool = True
    calls: list[tuple[Path, Path]] = field(default_factory=list)

    def strip_file(self, src: Path, dst: Path) -> StripResult:
        self.calls.append((src, dst))
        if self.raise_exc is not None:
            raise self.raise_exc
        if self.create_tmp:
            # Simulate the stripper writing the tmp file
            dst.write_bytes(b"stripped content")
        return self.result or StripResult(
            source=str(src), destination=str(dst),
            outcome=StripOutcome.STRIPPED,
            bytes_in=100, bytes_out=80,
            metadata_fields_removed=["EXIF:Make", "EXIF:Model"],
        )


# ===========================================================================
# Helpers
# ===========================================================================


def _make_move_with_file(tmp_path: Path) -> MigrationMove:
    """Build a move whose dst_path is a real existing file."""
    dst = tmp_path / "subdir" / "out.txt"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(b"destination content")
    return MigrationMove(
        curator_id=uuid4(),
        src_path="/src/file.txt",
        dst_path=str(dst),
        safety_level=SafetyLevel.SAFE,
        size=100,
        src_xxhash="src_hash",
        outcome=MigrationOutcome.MOVED,  # already-set, since this is post-move
    )


# ===========================================================================
# _auto_strip_metadata
# ===========================================================================


class TestAutoStripMetadata:
    def test_no_stripper_early_returns(self, tmp_path):
        # Lines 873-874: metadata_stripper is None → early return, no audit.
        audit = StubAuditRepository()
        svc = make_service(audit=audit)
        svc.metadata_stripper = None
        move = _make_move_with_file(tmp_path)

        svc._auto_strip_metadata(move)
        # No audit entries
        assert len(audit.entries) == 0

    def test_dst_not_exists_early_returns(self, tmp_path):
        # Lines 880-881: dst_path doesn't exist → defensive early return.
        audit = StubAuditRepository()
        svc = make_service(audit=audit)
        stripper = StubMetadataStripper()
        svc.metadata_stripper = stripper

        # Move points to a non-existent file
        move = MigrationMove(
            curator_id=uuid4(),
            src_path="/src/file.txt",
            dst_path=str(tmp_path / "does_not_exist.txt"),
            safety_level=SafetyLevel.SAFE,
            size=100, src_xxhash="src_hash",
            outcome=MigrationOutcome.MOVED,
        )
        svc._auto_strip_metadata(move)
        # Stripper not called
        assert len(stripper.calls) == 0
        assert len(audit.entries) == 0

    def test_stripped_outcome_replaces_dst_and_audits(self, tmp_path):
        # Lines 891-909: STRIPPED outcome → atomic replace + audit_stripped.
        audit = StubAuditRepository()
        svc = make_service(audit=audit)
        stripper = StubMetadataStripper()  # default: STRIPPED with tmp created
        svc.metadata_stripper = stripper
        move = _make_move_with_file(tmp_path)
        original_dst_bytes = Path(move.dst_path).read_bytes()

        svc._auto_strip_metadata(move)

        # dst was replaced with stripped content
        assert Path(move.dst_path).read_bytes() == b"stripped content"
        assert Path(move.dst_path).read_bytes() != original_dst_bytes
        # Audit emitted
        strip_audits = [
            e for e in audit.entries
            if e["action"] == "migration.metadata_stripped"
        ]
        assert len(strip_audits) == 1
        assert strip_audits[0]["details"]["bytes_in"] == 100
        assert strip_audits[0]["details"]["bytes_out"] == 80

    def test_passthrough_outcome_replaces_dst_and_audits(self, tmp_path):
        # Lines 891-909: PASSTHROUGH outcome (file type not handled,
        # byte-copied as-is) → same atomic replace + audit path.
        audit = StubAuditRepository()
        svc = make_service(audit=audit)
        stripper = StubMetadataStripper(
            result=StripResult(
                source="/x", destination="/y",
                outcome=StripOutcome.PASSTHROUGH,
                bytes_in=50, bytes_out=50,
                metadata_fields_removed=[],
            ),
        )
        svc.metadata_stripper = stripper
        move = _make_move_with_file(tmp_path)

        svc._auto_strip_metadata(move)

        strip_audits = [
            e for e in audit.entries
            if e["action"] == "migration.metadata_stripped"
        ]
        assert len(strip_audits) == 1
        assert strip_audits[0]["details"]["outcome"] == "passthrough"

    def test_stripped_but_tmp_missing_skips_replace(self, tmp_path):
        # Lines 892-895: STRIPPED outcome but tmp_path doesn't exist
        # (stripper claims success but produced no file) → skip the
        # replace, still emit audit.
        audit = StubAuditRepository()
        svc = make_service(audit=audit)
        stripper = StubMetadataStripper(create_tmp=False)
        svc.metadata_stripper = stripper
        move = _make_move_with_file(tmp_path)
        original_dst_bytes = Path(move.dst_path).read_bytes()

        svc._auto_strip_metadata(move)

        # dst NOT replaced (no tmp file existed)
        assert Path(move.dst_path).read_bytes() == original_dst_bytes
        # Audit still emitted
        strip_audits = [
            e for e in audit.entries
            if e["action"] == "migration.metadata_stripped"
        ]
        assert len(strip_audits) == 1

    def test_skipped_outcome_cleans_tmp_no_audit(self, tmp_path):
        # Lines 910-928: SKIPPED outcome → cleanup tmp, no audit
        # (audit only for STRIPPED/PASSTHROUGH/FAILED).
        audit = StubAuditRepository()
        svc = make_service(audit=audit)
        stripper = StubMetadataStripper(
            result=StripResult(
                source="/x", destination=None,
                outcome=StripOutcome.SKIPPED,
            ),
            create_tmp=True,
        )
        svc.metadata_stripper = stripper
        move = _make_move_with_file(tmp_path)

        svc._auto_strip_metadata(move)

        # Tmp file cleaned up
        tmp_path_for_strip = Path(move.dst_path).with_suffix(
            Path(move.dst_path).suffix + ".curator_autostrip"
        )
        assert not tmp_path_for_strip.exists()
        # No strip audit for SKIPPED outcome
        strip_audits = [
            e for e in audit.entries
            if e["action"] in (
                "migration.metadata_stripped",
                "migration.metadata_strip_failed",
            )
        ]
        assert len(strip_audits) == 0

    def test_failed_outcome_cleans_tmp_and_audits_failure(self, tmp_path):
        # Lines 910-928: FAILED outcome → cleanup tmp + audit_failed.
        audit = StubAuditRepository()
        svc = make_service(audit=audit)
        stripper = StubMetadataStripper(
            result=StripResult(
                source="/x", destination=None,
                outcome=StripOutcome.FAILED,
                error="corrupt file format",
            ),
            create_tmp=True,
        )
        svc.metadata_stripper = stripper
        move = _make_move_with_file(tmp_path)

        svc._auto_strip_metadata(move)

        # Tmp file cleaned up
        tmp_path_for_strip = Path(move.dst_path).with_suffix(
            Path(move.dst_path).suffix + ".curator_autostrip"
        )
        assert not tmp_path_for_strip.exists()
        # Failure audit emitted
        fail_audits = [
            e for e in audit.entries
            if e["action"] == "migration.metadata_strip_failed"
        ]
        assert len(fail_audits) == 1
        assert "corrupt file format" in fail_audits[0]["details"]["error"]

    def test_failed_outcome_tmp_unlink_oserror_swallowed(
        self, monkeypatch, tmp_path,
    ):
        # Lines 912-916: cleanup unlink raises OSError → swallowed,
        # outcome still proceeds (audit still emitted).
        audit = StubAuditRepository()
        svc = make_service(audit=audit)
        stripper = StubMetadataStripper(
            result=StripResult(
                source="/x", destination=None,
                outcome=StripOutcome.FAILED,
                error="boom",
            ),
            create_tmp=True,
        )
        svc.metadata_stripper = stripper
        move = _make_move_with_file(tmp_path)

        # Force unlink to raise OSError selectively for the tmp file
        orig_unlink = Path.unlink

        def selective_unlink(self, *args, **kwargs):
            if ".curator_autostrip" in str(self):
                raise OSError("simulated cleanup failure")
            return orig_unlink(self, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", selective_unlink)
        svc._auto_strip_metadata(move)  # should not raise

        # Failure audit still emitted despite cleanup error
        fail_audits = [
            e for e in audit.entries
            if e["action"] == "migration.metadata_strip_failed"
        ]
        assert len(fail_audits) == 1

    def test_strip_raises_defensive_cleanup_and_audit(
        self, tmp_path,
    ):
        # Lines 929-948: strip_file raises any exception → cleanup tmp,
        # audit failure (if audit available), don't propagate.
        audit = StubAuditRepository()
        svc = make_service(audit=audit)
        stripper = StubMetadataStripper(
            raise_exc=RuntimeError("stripper crashed"),
        )
        svc.metadata_stripper = stripper
        move = _make_move_with_file(tmp_path)

        # Pre-create the tmp file so cleanup branch fires
        tmp_p = Path(move.dst_path).with_suffix(
            Path(move.dst_path).suffix + ".curator_autostrip"
        )
        tmp_p.write_bytes(b"partial")

        svc._auto_strip_metadata(move)  # should not raise

        # Tmp cleaned up
        assert not tmp_p.exists()
        # Failure audit emitted
        fail_audits = [
            e for e in audit.entries
            if e["action"] == "migration.metadata_strip_failed"
        ]
        assert len(fail_audits) == 1
        assert "RuntimeError" in fail_audits[0]["details"]["error"]
        assert "stripper crashed" in fail_audits[0]["details"]["error"]

    def test_strip_raises_defensive_cleanup_unlink_oserror_swallowed(
        self, monkeypatch, tmp_path,
    ):
        # Lines 932-936: defensive cleanup unlink raises OSError →
        # swallowed, exception path completes.
        audit = StubAuditRepository()
        svc = make_service(audit=audit)
        stripper = StubMetadataStripper(
            raise_exc=RuntimeError("stripper crashed"),
        )
        svc.metadata_stripper = stripper
        move = _make_move_with_file(tmp_path)

        tmp_p = Path(move.dst_path).with_suffix(
            Path(move.dst_path).suffix + ".curator_autostrip"
        )
        tmp_p.write_bytes(b"partial")

        orig_unlink = Path.unlink

        def selective_unlink(self, *args, **kwargs):
            if ".curator_autostrip" in str(self):
                raise OSError("cleanup boom")
            return orig_unlink(self, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", selective_unlink)
        svc._auto_strip_metadata(move)  # should not raise

        # Failure audit still emitted
        fail_audits = [
            e for e in audit.entries
            if e["action"] == "migration.metadata_strip_failed"
        ]
        assert len(fail_audits) == 1

    def test_strip_raises_no_audit_when_audit_is_none(self, tmp_path):
        # Lines 937-948: when self.audit is None, the defensive audit
        # emission is skipped silently.
        svc = make_service(audit=None)  # explicitly no audit
        stripper = StubMetadataStripper(
            raise_exc=RuntimeError("boom"),
        )
        svc.metadata_stripper = stripper
        move = _make_move_with_file(tmp_path)

        # Should not raise even with no audit available
        svc._auto_strip_metadata(move)

    def test_stripped_outcome_with_no_audit_skips_audit_block(self, tmp_path):
        # Branch 896->exit: STRIPPED outcome + tmp replaced + audit is None
        # → skip the audit.log block entirely, fall through to method end.
        # Distinct from `test_strip_raises_no_audit_when_audit_is_none`
        # which exercises the OUTER defensive except (947-948); this one
        # covers the inline `if self.audit is not None` guard at 896.
        svc = make_service(audit=None)
        stripper = StubMetadataStripper()  # default: STRIPPED with tmp
        svc.metadata_stripper = stripper
        move = _make_move_with_file(tmp_path)

        svc._auto_strip_metadata(move)  # must not raise

        # The tmp replace happened (stripped content written to dst)
        assert Path(move.dst_path).read_bytes() == b"stripped content"
        # Stripper was invoked exactly once
        assert len(stripper.calls) == 1

    def test_skipped_outcome_with_no_tmp_skips_unlink_block(self, tmp_path):
        # Branch 912->917: SKIPPED outcome + tmp_path doesn't exist (e.g.
        # stripper claimed SKIPPED without ever writing the tmp) →
        # bypass the unlink try/except, fall through to the audit check
        # at 917, which is False for SKIPPED (audit only emits on FAILED).
        # Mirrors the `test_skipped_outcome_cleans_tmp_no_audit` test
        # but with create_tmp=False, exercising the False arm of `if
        # tmp_path.exists()` at line 912.
        audit = StubAuditRepository()
        svc = make_service(audit=audit)
        stripper = StubMetadataStripper(
            result=StripResult(
                source="/x", destination=None,
                outcome=StripOutcome.SKIPPED,
            ),
            create_tmp=False,  # ← no tmp file produced
        )
        svc.metadata_stripper = stripper
        move = _make_move_with_file(tmp_path)
        original_dst_bytes = Path(move.dst_path).read_bytes()

        svc._auto_strip_metadata(move)  # must not raise

        # dst unchanged (no replace happened — outcome was SKIPPED)
        assert Path(move.dst_path).read_bytes() == original_dst_bytes
        # No tmp file was created or left behind
        tmp_p = Path(move.dst_path).with_suffix(
            Path(move.dst_path).suffix + ".curator_autostrip"
        )
        assert not tmp_p.exists()
        # No audit emitted (SKIPPED is the audit-suppressing outcome)
        strip_audits = [
            e for e in audit.entries
            if e["action"] in (
                "migration.metadata_stripped",
                "migration.metadata_strip_failed",
            )
        ]
        assert len(strip_audits) == 0


# ===========================================================================
# apply() auto-strip dispatch (line 830)
# ===========================================================================


class TestApplyAutoStripDispatch:
    """Covers line 830: `self._auto_strip_metadata(move)` inside apply()'s
    per-move loop. The v1.7.92 helper-body tests above call
    `_auto_strip_metadata` directly; this class exercises the dispatch
    path via apply() so the dispatch line is covered too.

    Per Lesson #90 (data-flow tracing): the move handed to apply() goes
    through gates 1-3 before reaching _execute_one. We use a fake
    _execute_one that creates dst on disk + sets outcome=MOVED so the
    auto-strip block (lines 824-830) fires."""

    def test_apply_invokes_auto_strip_when_dst_source_is_public(
        self, tmp_path, monkeypatch,
    ):
        # Setup: dst_source has share_visibility="public", metadata_stripper
        # wired, no_autostrip=False (default) → auto_strip becomes True
        # on lines 712-732. After fake _execute_one sets outcome=MOVED,
        # line 830 dispatches to _auto_strip_metadata.
        dst_source = SourceConfig(
            source_id="public_drive",
            source_type="local",
            display_name="Public",
            share_visibility="public",
        )
        source_repo = StubSourceRepository(sources={"public_drive": dst_source})
        stripper = StubMetadataStripper()  # default: STRIPPED + create_tmp
        audit = StubAuditRepository()
        svc = make_service(
            source_repo=source_repo,
            metadata_stripper=stripper,
            audit=audit,
        )

        src = tmp_path / "src.txt"
        src.write_bytes(b"source bytes")
        dst = tmp_path / "dst.txt"  # not pre-created → no Gate-3 collision

        move = MigrationMove(
            curator_id=uuid4(),
            src_path=str(src),
            dst_path=str(dst),
            safety_level=SafetyLevel.SAFE,
            size=src.stat().st_size,
            src_xxhash=None,
        )
        plan = MigrationPlan(
            src_source_id="local", src_root=str(tmp_path),
            dst_source_id="public_drive", dst_root=str(tmp_path),
            moves=[move],
        )

        def fake_execute_one(move, *, verify_hash, keep_source,
                              src_source_id, dst_source_id):
            # Stand in for the real move: write dst on disk + set MOVED.
            Path(move.dst_path).write_bytes(b"destination bytes")
            move.outcome = MigrationOutcome.MOVED

        monkeypatch.setattr(svc, "_execute_one", fake_execute_one)

        report = svc.apply(plan, verify_hash=False)

        # _auto_strip_metadata was called via the dispatch on line 830
        assert len(stripper.calls) == 1, (
            "stripper.strip_file should have been called once via the "
            "apply() → _auto_strip_metadata dispatch path"
        )
        # Outcome preserved and the strip-stripped audit fired
        assert report.moves[0].outcome == MigrationOutcome.MOVED
        strip_audits = [
            e for e in audit.entries
            if e["action"] == "migration.metadata_stripped"
        ]
        assert len(strip_audits) == 1


# ===========================================================================
# _audit_move / _audit_copy minor defensives
# (branches 2131->2135, 2175->2179, lines 2187-2188)
# ===========================================================================


class TestAuditMoveCopyDefensives:
    """The audit helpers `_audit_move` (line 2107+) and `_audit_copy`
    (line 2149+) have two parallel uncovered branches each:

    * The `if src_source_id is not None and dst_source_id is not None:`
      cross-source-detail injector (2131/2175) — the False branch is
      hit when either ID is None (legacy callers, defensive defaults).
    * The `except Exception` defensive boundary around `audit.insert()`
      — `_audit_move`'s sibling at 2143-2147 is covered (via
      StubAuditRepository missing the `insert` attr → AttributeError),
      but `_audit_copy`'s parallel except at 2187-2188 has no callers
      in the existing test suite. Direct-invoke fixes both.
    """

    def test_audit_move_without_source_ids_skips_cross_source_details(self):
        # Branch 2131->2135 False: src_source_id=None bypasses the
        # cross-source details block, jumps straight to AuditEntry build.
        audit = StubAuditRepository()  # no `insert` attr → except branch
        svc = make_service(audit=audit)
        move = MigrationMove(
            curator_id=uuid4(),
            src_path="/data/x.txt", dst_path="/archive/x.txt",
            safety_level=SafetyLevel.SAFE,
            size=10, src_xxhash="hash",
        )

        # Both source IDs default to None → False branch of the if at 2131.
        # `audit.insert` raises AttributeError (handled by 2143-2147).
        svc._audit_move(move)  # must not raise

    def test_audit_copy_without_source_ids_skips_cross_source_details(self):
        # Branch 2175->2179 False: parallel pattern in _audit_copy.
        audit = StubAuditRepository()
        svc = make_service(audit=audit)
        move = MigrationMove(
            curator_id=uuid4(),
            src_path="/data/x.txt", dst_path="/copy/x.txt",
            safety_level=SafetyLevel.SAFE,
            size=10, src_xxhash="hash",
        )

        svc._audit_copy(move)  # must not raise

    def test_audit_copy_insert_exception_swallowed(self):
        # Lines 2187-2188: defensive `except Exception` around
        # `self.audit.insert(entry)`. _audit_move's parallel except at
        # 2143-2147 is covered by cross-source tests; this test covers
        # the _audit_copy sibling. Use a custom audit whose insert()
        # raises, so we exercise the exception path explicitly rather
        # than the AttributeError-from-missing-attr fallback.
        class BoomAudit:
            def insert(self, entry):
                raise RuntimeError("audit db locked")
        svc = make_service(audit=BoomAudit())
        move = MigrationMove(
            curator_id=uuid4(),
            src_path="/data/x.txt", dst_path="/copy/x.txt",
            safety_level=SafetyLevel.SAFE,
            size=10, src_xxhash="hash",
        )

        # Should not propagate the RuntimeError.
        svc._audit_copy(
            move,
            src_source_id="local",
            dst_source_id="gdrive",
        )


# ===========================================================================
# _update_index entity vanished (same-source path, line 1470)
# ===========================================================================


class TestUpdateIndexVanished:
    def test_same_source_entity_vanished_raises_runtime_error(
        self, monkeypatch, tmp_path,
    ):
        # Line 1470: _update_index raises RuntimeError when files.get
        # returns None. This is the SAME-SOURCE variant (the cross-source
        # variant was tested in v1.7.91).
        # Reaches via _execute_one_same_source step 6.
        src = tmp_path / "src.txt"
        src.write_bytes(b"data")
        dst = tmp_path / "dst" / "src.txt"

        # File repo with NO entries → files.get(curator_id) returns None
        # → _update_index raises RuntimeError → caught by the OSError-
        # and-defensive-Exception clauses at lines 1079-1081.
        file_repo = StubFileRepository(files=[])
        svc = make_service(file_repo=file_repo)
        move = MigrationMove(
            curator_id=uuid4(),
            src_path=str(src), dst_path=str(dst),
            safety_level=SafetyLevel.SAFE,
            size=src.stat().st_size,
            src_xxhash=None,
        )

        svc._execute_one_same_source(
            move, verify_hash=False, source_id="local",
        )

        assert move.outcome == MigrationOutcome.FAILED
        assert "vanished during migration" in (move.error or "")


# ===========================================================================
# _trash_source exception (lines 1486-1491)
# ===========================================================================


class TestTrashSourceException:
    def test_send2trash_raises_appends_error_outcome_unchanged(
        self, monkeypatch, tmp_path,
    ):
        # Lines 1486-1491: send2trash raises → caught, error appended,
        # move.outcome stays whatever it was (best-effort discipline).
        svc = make_service()
        src = tmp_path / "src.txt"
        src.write_bytes(b"data")
        move = MigrationMove(
            curator_id=uuid4(),
            src_path=str(src), dst_path="/dst/file.txt",
            safety_level=SafetyLevel.SAFE,
            size=src.stat().st_size,
            src_xxhash="src_hash",
            outcome=MigrationOutcome.MOVED,  # already set
        )

        # Monkeypatch the send2trash module reference inside the import
        # within _trash_source. The function does a local import, so
        # we need to patch the vendored module's send2trash function.
        from curator._vendored import send2trash as send2trash_module

        def boom_send2trash(path):
            raise RuntimeError("trash bin full")

        monkeypatch.setattr(send2trash_module, "send2trash", boom_send2trash)
        svc._trash_source(src, move)

        # Outcome unchanged
        assert move.outcome == MigrationOutcome.MOVED
        # Error appended
        assert "trash failed" in (move.error or "")
        assert "RuntimeError" in (move.error or "")
        assert "trash bin full" in (move.error or "")


# ===========================================================================
# _audit_conflict exception (lines 1892-1893)
# ===========================================================================


class TestAuditConflictException:
    def test_audit_log_raises_logs_warning_but_does_not_propagate(
        self, monkeypatch,
    ):
        # Lines 1892-1893: defensive `except Exception` around audit.log
        # call inside _audit_conflict. Audit failures are best-effort.
        audit = StubAuditRepository()

        def boom_log(**kw):
            raise RuntimeError("audit DB locked")

        audit.log = boom_log
        svc = make_service(audit=audit)
        move = MigrationMove(
            curator_id=uuid4(),
            src_path="/src/x", dst_path="/dst/x",
            safety_level=SafetyLevel.SAFE,
            size=10, src_xxhash=None,
        )

        # Should not raise despite audit.log raising
        svc._audit_conflict(move, mode="fail")
        # If we got here without exception, the defensive boundary works.
