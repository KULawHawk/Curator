"""Focused unit tests for MigrationService.plan() + apply() control flow.

Sub-ship 1 of the Migration Phase Gamma arc (v1.7.89).
Scope plan: docs/MIGRATION_PHASE_GAMMA_SCOPE.md

Targets:
* MigrationReport.duration_seconds None branch (line 230)
* plan() defensive branches: dst-inside-src refusal (527-528), query failure
  (547-554), include/exclude relative_to (569-572), safety exception (585-591),
  dst computation None (599)
* apply() autostrip logging: opted-out (719-730), enabled (731-743)
* apply() FAILED_DUE_TO_CONFLICT + on_conflict=fail raise (839-840)
* _execute_one dst_source_id default-to-src branch (line 977)

Stubs introduced here will be reused by v1.7.90-93 (the rest of the
migration arc).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

from curator.models.file import FileEntity
from curator.models.source import SourceConfig
from curator.services.migration import (
    MigrationConflictError,
    MigrationMove,
    MigrationOutcome,
    MigrationPlan,
    MigrationReport,
    MigrationService,
)
from curator.services.safety import SafetyLevel, SafetyReport
from curator.storage.queries import FileQuery


NOW = datetime(2026, 5, 12, 12, 0, 0)


# ===========================================================================
# Stubs (will be reused by v1.7.90-93)
# ===========================================================================


class StubFileRepository:
    """Minimal FileRepository for migration tests.

    Supports query(file_query) returning a configurable list of entities,
    plus an injectable exception for testing the query-failure branch.
    """

    def __init__(self, files: list[FileEntity] | None = None):
        self._files: dict[UUID, FileEntity] = {
            f.curator_id: f for f in (files or [])
        }
        self._query_results: list[FileEntity] = list(files or [])
        self.query_raises: Exception | None = None
        self.updates: list[FileEntity] = []

    def query(self, query: FileQuery) -> list[FileEntity]:
        if self.query_raises is not None:
            raise self.query_raises
        return list(self._query_results)

    def get(self, curator_id: UUID) -> FileEntity | None:
        return self._files.get(curator_id)

    def update(self, entity: FileEntity) -> None:
        self._files[entity.curator_id] = entity
        self.updates.append(entity)

    # Some MigrationService paths call update_source_path or similar.
    # Add as needed in later sub-ships.


@dataclass
class StubSafetyService:
    """Stub SafetyService that returns a configurable SafetyLevel.

    Set per_path_overrides to map specific paths to specific levels.
    Set check_path_raises to make every check_path call raise.
    """

    default_level: SafetyLevel = SafetyLevel.SAFE
    per_path_overrides: dict[str, SafetyLevel] = field(default_factory=dict)
    check_path_raises: Exception | None = None
    calls: list[Path] = field(default_factory=list)

    def check_path(self, path: Path) -> SafetyReport:
        if self.check_path_raises is not None:
            raise self.check_path_raises
        self.calls.append(path)
        level = self.per_path_overrides.get(str(path), self.default_level)
        return SafetyReport(path=path, level=level, concerns=[])


@dataclass
class StubAuditRepository:
    """Captures audit log calls for assertion."""

    entries: list[dict[str, Any]] = field(default_factory=list)

    def log(self, *, actor: str, action: str, entity_type: str | None = None,
            entity_id: str | None = None, details: dict[str, Any] | None = None) -> None:
        self.entries.append({
            "actor": actor,
            "action": action,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "details": details or {},
        })


class StubSourceRepository:
    """Source repo whose get() returns a configurable SourceConfig (or None)."""

    def __init__(self, sources: dict[str, SourceConfig] | None = None):
        self._sources = sources or {}

    def get(self, source_id: str) -> SourceConfig | None:
        return self._sources.get(source_id)


class StubMetadataStripper:
    """Placeholder presence-only stub for tests that need the field set
    but don't actually invoke .strip_file(). Full stub lives in v1.7.92."""

    def __init__(self):
        self.calls: list[Any] = []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_service(
    *,
    file_repo: StubFileRepository | None = None,
    safety: StubSafetyService | None = None,
    audit: StubAuditRepository | None = None,
    source_repo: StubSourceRepository | None = None,
    metadata_stripper: StubMetadataStripper | None = None,
) -> MigrationService:
    return MigrationService(
        file_repo=file_repo or StubFileRepository(),
        safety=safety or StubSafetyService(),
        audit=audit,
        source_repo=source_repo,
        metadata_stripper=metadata_stripper,
    )


def make_file_entity(
    *,
    source_id: str = "local",
    source_path: str = "/data/file.txt",
    size: int = 100,
    extension: str | None = ".txt",
    xxhash: str | None = None,
) -> FileEntity:
    return FileEntity(
        source_id=source_id,
        source_path=source_path,
        size=size,
        mtime=NOW,
        extension=extension,
        xxhash3_128=xxhash,
    )


def make_move(
    *,
    safety_level: SafetyLevel = SafetyLevel.SAFE,
    src_path: str = "/data/x.txt",
    dst_path: str = "/archive/x.txt",
    size: int = 100,
) -> MigrationMove:
    return MigrationMove(
        curator_id=uuid4(),
        src_path=src_path,
        dst_path=dst_path,
        safety_level=safety_level,
        size=size,
        src_xxhash=None,
    )


# ===========================================================================
# MigrationReport.duration_seconds (line 230)
# ===========================================================================


class TestMigrationReportDuration:
    def test_duration_none_when_not_completed(self):
        # Line 230: MigrationReport.duration_seconds returns None
        # before completed_at is set.
        plan = MigrationPlan(
            src_source_id="local", src_root="/a",
            dst_source_id="local", dst_root="/b",
        )
        report = MigrationReport(plan=plan, started_at=NOW)
        assert report.duration_seconds is None

    def test_duration_computed_after_completion(self):
        from datetime import timedelta
        plan = MigrationPlan(
            src_source_id="local", src_root="/a",
            dst_source_id="local", dst_root="/b",
        )
        report = MigrationReport(
            plan=plan,
            started_at=NOW,
            completed_at=NOW + timedelta(seconds=3.5),
        )
        assert report.duration_seconds == 3.5


# ===========================================================================
# plan() defensive branches
# ===========================================================================


class TestPlanRefusesNesting:
    def test_raises_when_dst_root_is_inside_src_root(self, tmp_path):
        # Lines 522-528: dst inside src raises ValueError with the
        # specific "must not be inside" message; the except block at
        # 526-528 re-raises it.
        src = tmp_path / "src"
        src.mkdir()
        dst = src / "inside"  # dst literally inside src
        svc = make_service()
        with pytest.raises(ValueError, match="dst_root must not be inside src_root"):
            svc.plan(
                src_source_id="local",
                src_root=str(src),
                dst_root=str(dst),
            )

    def test_does_not_raise_for_unrelated_resolve_errors(self, tmp_path):
        # The except block swallows resolve failures (e.g. paths that
        # don't exist) — only re-raises the "must not be inside" case.
        # Use paths that don't exist and are not nested; plan should
        # proceed past the safety check without raising.
        svc = make_service()
        plan = svc.plan(
            src_source_id="local",
            src_root="/nonexistent/src/path",
            dst_root="/different/dst/path",
        )
        # Empty result is fine; we just verify no exception.
        assert plan.total_count == 0

    def test_silently_swallows_non_nesting_value_errors(self):
        # Branch 527->533: the except block catches a ValueError that
        # is NOT the "must not be inside" message and silently proceeds.
        # Trigger: monkeypatch Path.resolve on the first call (dst_root)
        # to raise OSError. Without nesting-check failure, the except
        # block catches and proceeds with an empty plan.
        #
        # Note: a NUL-character path string is silently sanitized by
        # pathlib on Windows rather than raising, so we use monkeypatch
        # to guarantee the except branch is exercised. Per Lesson #78
        # this is monkeypatching stdlib (high blast radius), so we narrow
        # to the first resolve() call only.
        from pathlib import Path as PathClass
        orig_resolve = PathClass.resolve
        call_count = [0]

        def first_call_raises(self, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise OSError("simulated resolve failure")
            return orig_resolve(self, *args, **kwargs)

        import pytest
        monkeypatch = pytest.MonkeyPatch()
        try:
            monkeypatch.setattr(PathClass, "resolve", first_call_raises)
            svc = make_service()
            plan = svc.plan(
                src_source_id="local",
                src_root="/data",
                dst_root="/archive",
            )
            # No exception was raised; plan is empty because file_repo
            # has no entries.
            assert plan.total_count == 0
        finally:
            monkeypatch.undo()


class TestPlanQueryFailure:
    def test_query_exception_returns_empty_plan(self):
        # Lines 547-554: when files.query() raises, the plan() method
        # logs the error and returns an empty MigrationPlan rather than
        # propagating.
        file_repo = StubFileRepository()
        file_repo.query_raises = RuntimeError("simulated query failure")
        svc = make_service(file_repo=file_repo)
        plan = svc.plan(
            src_source_id="local",
            src_root="/data",
            dst_root="/archive",
        )
        assert plan.total_count == 0
        assert plan.src_source_id == "local"
        assert plan.src_root == "/data"
        assert plan.dst_root == "/archive"


class TestPlanIncludeExcludeRelativeError:
    def test_skips_file_when_relative_to_fails_with_include_filter(self):
        # Lines 569-572: when include/exclude filtering is on, and a
        # candidate's source_path is not actually under src_root (so
        # Path.relative_to raises), the file is silently skipped.
        # Trigger: a stub file_repo that returns a file whose path is
        # genuinely not under src_root.
        wayward = make_file_entity(
            source_path="/totally/elsewhere/file.mp3",
            extension=".mp3",
        )
        file_repo = StubFileRepository(files=[wayward])
        svc = make_service(file_repo=file_repo)
        plan = svc.plan(
            src_source_id="local",
            src_root="/data",
            dst_root="/archive",
            includes=["**/*.mp3"],  # forces the relative_to path
        )
        # The wayward file was filtered out before becoming a move.
        assert plan.total_count == 0


class TestPlanSafetyException:
    def test_safety_check_exception_routes_file_to_refuse(self):
        # Lines 585-591: when safety.check_path raises, the file is
        # conservatively routed to REFUSE (the file appears in the plan
        # but with the most-restrictive safety level).
        f = make_file_entity(source_path="/data/x.txt")
        file_repo = StubFileRepository(files=[f])
        safety = StubSafetyService()
        safety.check_path_raises = RuntimeError("safety check crashed")
        svc = make_service(file_repo=file_repo, safety=safety)
        plan = svc.plan(
            src_source_id="local",
            src_root="/data",
            dst_root="/archive",
        )
        assert plan.total_count == 1
        assert plan.moves[0].safety_level == SafetyLevel.REFUSE


class TestPlanDstComputationNone:
    def test_file_not_under_src_root_is_skipped_when_no_glob_filter(self):
        # Line 599: _compute_dst_path returns None when source_path
        # isn't under src_root; plan() skips the file. This second check
        # is independent of the glob filter's relative_to check.
        wayward = make_file_entity(source_path="/totally/elsewhere/x.txt")
        file_repo = StubFileRepository(files=[wayward])
        svc = make_service(file_repo=file_repo)
        # NO glob filter, so we bypass lines 569-572 and reach line 599.
        plan = svc.plan(
            src_source_id="local",
            src_root="/data",
            dst_root="/archive",
        )
        assert plan.total_count == 0


# ===========================================================================
# apply() autostrip enable/opt-out branches (lines 712-743)
# ===========================================================================


class TestApplyAutoStripBranches:
    def _make_setup_with_public_dst(self, no_autostrip_audit: bool = True):
        # Common setup: source_repo returns a public-share SourceConfig,
        # metadata_stripper present, audit captured.
        dst_source = SourceConfig(
            source_id="public_drive",
            source_type="local",
            display_name="Public",
            share_visibility="public",
        )
        source_repo = StubSourceRepository(sources={"public_drive": dst_source})
        stripper = StubMetadataStripper()
        audit = StubAuditRepository() if no_autostrip_audit else None
        svc = make_service(
            source_repo=source_repo,
            metadata_stripper=stripper,
            audit=audit,
        )
        plan = MigrationPlan(
            src_source_id="local",
            src_root="/data",
            dst_source_id="public_drive",
            dst_root="/public",
            moves=[],  # empty plan; we only care about the pre-loop autostrip logic
        )
        return svc, plan, audit

    def test_autostrip_enabled_logs_when_dst_is_public(self):
        # Lines 731-743: dst_source.share_visibility == "public" and
        # no_autostrip=False → audit.log "migration.autostrip.enabled".
        svc, plan, audit = self._make_setup_with_public_dst()
        svc.apply(plan, verify_hash=False)
        actions = [e["action"] for e in audit.entries]
        assert "migration.autostrip.enabled" in actions

    def test_autostrip_opted_out_logs_when_no_autostrip_true(self):
        # Lines 717-730: no_autostrip=True → audit.log
        # "migration.autostrip.opted_out".
        svc, plan, audit = self._make_setup_with_public_dst()
        svc.apply(plan, verify_hash=False, no_autostrip=True)
        actions = [e["action"] for e in audit.entries]
        assert "migration.autostrip.opted_out" in actions

    def test_autostrip_enabled_without_audit_does_not_crash(self):
        # The `if self.audit is not None` guard on lines 719 and 733
        # protects callers that didn't supply an audit repo.
        dst_source = SourceConfig(
            source_id="public_drive",
            source_type="local",
            display_name="Public",
            share_visibility="public",
        )
        source_repo = StubSourceRepository(sources={"public_drive": dst_source})
        svc = make_service(
            source_repo=source_repo,
            metadata_stripper=StubMetadataStripper(),
            # audit=None
        )
        plan = MigrationPlan(
            src_source_id="local", src_root="/data",
            dst_source_id="public_drive", dst_root="/public",
        )
        # Should not raise even though audit is None.
        report = svc.apply(plan, verify_hash=False)
        assert report.plan.total_count == 0

    def test_no_autostrip_path_does_not_log_when_dst_is_private(self):
        # If dst_source.share_visibility == "private" (default), no
        # autostrip logging fires at all (the outer `if` at line 716
        # is False).
        dst_source = SourceConfig(
            source_id="private_dest",
            source_type="local",
            display_name="Private",
            share_visibility="private",
        )
        source_repo = StubSourceRepository(sources={"private_dest": dst_source})
        audit = StubAuditRepository()
        svc = make_service(
            source_repo=source_repo,
            metadata_stripper=StubMetadataStripper(),
            audit=audit,
        )
        plan = MigrationPlan(
            src_source_id="local", src_root="/data",
            dst_source_id="private_dest", dst_root="/p",
        )
        svc.apply(plan, verify_hash=False)
        # No autostrip-related log entries
        autostrip_actions = [
            e["action"] for e in audit.entries
            if "autostrip" in e["action"]
        ]
        assert autostrip_actions == []

    def test_no_autostrip_path_with_no_audit_does_not_crash(self):
        # Branch 719->745: no_autostrip=True AND audit is None.
        # The opt-out log call is gated by `if self.audit is not None`;
        # this branch exercises the False path where we skip the log
        # call and fall through to the move loop without error.
        dst_source = SourceConfig(
            source_id="public_drive",
            source_type="local",
            display_name="Public",
            share_visibility="public",
        )
        source_repo = StubSourceRepository(sources={"public_drive": dst_source})
        svc = make_service(
            source_repo=source_repo,
            metadata_stripper=StubMetadataStripper(),
            # audit=None
        )
        plan = MigrationPlan(
            src_source_id="local", src_root="/data",
            dst_source_id="public_drive", dst_root="/public",
        )
        # no_autostrip=True with audit=None: should not raise
        report = svc.apply(plan, verify_hash=False, no_autostrip=True)
        assert report.plan.total_count == 0


# ===========================================================================
# apply() MigrationConflictError raise after _execute_one (lines 837-842)
# ===========================================================================


class TestApplyConflictFailRaise:
    def test_raises_when_execute_one_sets_failed_due_to_conflict_and_mode_is_fail(
        self, monkeypatch,
    ):
        # Lines 837-842: if _execute_one sets outcome to
        # FAILED_DUE_TO_CONFLICT (cross-source FileExistsError caught
        # inside _execute_one_cross_source) AND on_conflict=fail, apply()
        # raises MigrationConflictError after appending the move to the
        # report.
        f = make_file_entity(source_path="/data/x.txt")
        file_repo = StubFileRepository(files=[f])
        svc = make_service(file_repo=file_repo)
        svc.set_on_conflict_mode("fail")

        # Force a plan with a single SAFE move whose dst doesn't exist
        # on disk (so Gate 3 collision check at line 783 doesn't fire).
        move = make_move(safety_level=SafetyLevel.SAFE)
        plan = MigrationPlan(
            src_source_id="local", src_root="/data",
            dst_source_id="local", dst_root="/archive",
            moves=[move],
        )

        # Monkeypatch _execute_one to simulate "I encountered a cross-
        # source FileExistsError and converted it to FAILED_DUE_TO_CONFLICT
        # without raising".
        def fake_execute(m, **kw):
            m.outcome = MigrationOutcome.FAILED_DUE_TO_CONFLICT

        monkeypatch.setattr(svc, "_execute_one", fake_execute)

        with pytest.raises(MigrationConflictError) as exc_info:
            svc.apply(plan, verify_hash=False)
        # The error carries the dst path
        assert exc_info.value.dst_path == move.dst_path


# ===========================================================================
# _execute_one dst_source_id default-to-src branch (line 977)
# ===========================================================================


class TestExecuteOneDstDefaulting:
    def test_dst_source_id_defaults_to_src_when_omitted(self, monkeypatch):
        # Line 977: if dst_source_id is None, it's set to src_source_id
        # (backward-compat with pre-Session-B callers calling _execute_one
        # directly). Verify by monkeypatching _execute_one_same_source to
        # capture the dst_source_id it received via the cross_source
        # check; if defaulting worked, src == dst, so cross_source=False
        # and the same-source path is taken.
        svc = make_service()

        called_with: dict[str, Any] = {}

        def fake_same_source(m, **kw):
            called_with.update(kw)
            m.outcome = MigrationOutcome.MOVED  # don't error out

        monkeypatch.setattr(svc, "_execute_one_same_source", fake_same_source)

        move = make_move()
        # Call _execute_one WITHOUT dst_source_id → exercises line 977.
        svc._execute_one(
            move,
            verify_hash=False,
            src_source_id="local",
            # dst_source_id intentionally omitted (defaults to None)
        )
        # Same-source path was taken (cross_source=False because the
        # defaulting made src == dst).
        assert "source_id" in called_with
        assert called_with["source_id"] == "local"
