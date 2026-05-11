"""Tests for v1.7.29 auto-strip + v1.7.35 --no-autostrip opt-out (T-B07).

Strategy:
  * Reuse the ``migration_runtime`` + ``migration_service`` fixtures from
    ``test_migration.py`` for the real-DB + real-file harness.
  * Wire a *recording* fake MetadataStripper that captures strip_file
    calls without actually modifying anything -- the migration's
    hash-verify discipline already runs on real files; we just need to
    observe whether the strip phase fires.
  * Register a destination source with share_visibility='public' so the
    auto-strip gating condition is satisfied.

Behaviors verified:
  1. v1.7.29 default: dst is public + metadata_stripper wired + no flag
     -> strip_file fires once per moved file + an enabled audit event.
  2. v1.7.35 opt-out: dst is public + metadata_stripper wired + flag set
     -> strip_file does NOT fire + an opted_out audit event.
  3. v1.7.35 no-op: dst is private + flag set -> neither event fires.
  4. v1.7.35 no-op: no metadata_stripper wired + flag set -> nothing
     happens (the gating condition for auto-strip isn't met regardless).
"""

from __future__ import annotations

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
    MigrationService,
)
from curator.services.safety import SafetyLevel, SafetyReport

# Import the fixtures from test_migration so we don't duplicate harness code
from tests.unit.test_migration import (
    migration_runtime,  # noqa: F401 (re-exported pytest fixture)
    _seed_real_file,
)


# ---------------------------------------------------------------------------
# Recording fake MetadataStripper
# ---------------------------------------------------------------------------


class RecordingStripper:
    """Test double: records strip_file calls without modifying anything.

    Returns a synthetic StripResult so the MigrationService's auto_strip
    branch sees a successful outcome. We only care about whether
    strip_file was invoked and how many times.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def strip_file(self, src: Path, dst: Path) -> StripResult:
        self.calls.append((str(src), str(dst)))
        return StripResult(
            source=str(src),
            destination=str(dst),
            outcome=StripOutcome.PASSTHROUGH,
            bytes_in=0,
            bytes_out=0,
            metadata_fields_removed=[],
            error=None,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _register_public_dst(rt, source_id: str = "local") -> None:
    """Update the destination source to share_visibility='public'.

    By default, the migration_runtime fixture inserts 'local' with the
    default private share_visibility. We update it in place rather than
    registering a new source so the test stays on the same-source code
    path (cross-source needs plugin hooks the unit test isn't trying to
    exercise).
    """
    existing = rt.source_repo.get(source_id)
    if existing is None:
        rt.source_repo.insert(SourceConfig(
            source_id=source_id,
            source_type="local",
            display_name="Public Destination",
            share_visibility="public",
        ))
    else:
        updated = SourceConfig(
            source_id=existing.source_id,
            source_type=existing.source_type,
            display_name=existing.display_name,
            config=existing.config,
            enabled=existing.enabled,
            created_at=existing.created_at,
            share_visibility="public",
        )
        rt.source_repo.update(updated)


def _register_private_dst(rt, source_id: str = "local") -> None:
    """Ensure the destination source has the default share_visibility='private'.

    The default migration_runtime fixture already inserts 'local' as
    private; this helper exists for symmetry and to make the test
    intent obvious.
    """
    existing = rt.source_repo.get(source_id)
    if existing is None:
        rt.source_repo.insert(SourceConfig(
            source_id=source_id,
            source_type="local",
            display_name="Private Destination",
        ))


def _build_service_with_strip(rt, stripper: RecordingStripper) -> MigrationService:
    """Build a MigrationService with the strip wiring v1.7.29 expects."""
    # Stub safety check -- tmp_path is under CAUTION on Windows otherwise.
    rt.safety.check_path = lambda p, **kw: SafetyReport(
        path=p, level=SafetyLevel.SAFE,
    )
    return MigrationService(
        file_repo=rt.file_repo,
        safety=rt.safety,
        audit=rt.audit_repo,
        source_repo=rt.source_repo,
        metadata_stripper=stripper,
    )


def _plan_one_file(rt, src_root: Path, dst_root: Path, dst_source_id: str) -> MigrationPlan:
    """Build a one-file plan from src_root to dst_root."""
    f = _seed_real_file(rt, src_root / "data.bin", b"v1.7.35 test bytes\n" * 50)
    return MigrationPlan(
        src_source_id="local",
        src_root=str(src_root),
        dst_source_id=dst_source_id,
        dst_root=str(dst_root),
        moves=[
            MigrationMove(
                curator_id=f.curator_id,
                src_path=f.source_path,
                dst_path=str(dst_root / "data.bin"),
                safety_level=SafetyLevel.SAFE,
                size=f.size,
                src_xxhash=f.xxhash3_128,
            ),
        ],
    )


def _audit_actions(rt, action_prefix: str = "migration.autostrip") -> list[str]:
    """Return action strings of audit entries matching the prefix."""
    entries = rt.audit_repo.query(limit=100)
    return [e.action for e in entries if e.action.startswith(action_prefix)]


# ---------------------------------------------------------------------------
# v1.7.29 baseline: auto-strip fires when dst is public
# ---------------------------------------------------------------------------


def test_autostrip_fires_when_dst_is_public(migration_runtime, tmp_path):  # noqa: F811
    """v1.7.29: public dst + stripper wired -> strip fires + audit event."""
    rt = migration_runtime
    _register_public_dst(rt, "local")
    stripper = RecordingStripper()
    svc = _build_service_with_strip(rt, stripper)

    src_root = tmp_path / "src"
    dst_root = tmp_path / "dst"
    plan = _plan_one_file(rt, src_root, dst_root, dst_source_id="local")

    report = svc.apply(plan, verify_hash=False)
    # Move succeeded (default outcome MOVED)
    assert report.moved_count == 1
    # strip_file was invoked exactly once
    assert len(stripper.calls) == 1
    # Audit log records the enabled event but NOT the opted_out event
    actions = _audit_actions(rt)
    assert "migration.autostrip.enabled" in actions
    assert "migration.autostrip.opted_out" not in actions


# ---------------------------------------------------------------------------
# v1.7.35: no_autostrip=True blocks strip when dst is public
# ---------------------------------------------------------------------------


def test_no_autostrip_blocks_strip_when_dst_is_public(migration_runtime, tmp_path):  # noqa: F811
    """v1.7.35: public dst + stripper + no_autostrip=True -> strip skipped."""
    rt = migration_runtime
    _register_public_dst(rt, "local")
    stripper = RecordingStripper()
    svc = _build_service_with_strip(rt, stripper)

    src_root = tmp_path / "src"
    dst_root = tmp_path / "dst"
    plan = _plan_one_file(rt, src_root, dst_root, dst_source_id="local")

    report = svc.apply(plan, verify_hash=False, no_autostrip=True)
    # Move still succeeds
    assert report.moved_count == 1
    # strip_file was NOT invoked
    assert stripper.calls == [], (
        f"Expected zero strip_file calls; got {stripper.calls}"
    )


def test_no_autostrip_audit_event_when_dst_is_public(migration_runtime, tmp_path):  # noqa: F811
    """v1.7.35: public dst + no_autostrip=True -> opted_out audit event fires.

    The override should be discoverable in the audit trail so administrators
    can see WHY a public-dst migration did not strip. The opted_out event
    fires; the enabled event does NOT fire (auto-strip never ran).
    """
    rt = migration_runtime
    _register_public_dst(rt, "local")
    stripper = RecordingStripper()
    svc = _build_service_with_strip(rt, stripper)

    src_root = tmp_path / "src"
    dst_root = tmp_path / "dst"
    plan = _plan_one_file(rt, src_root, dst_root, dst_source_id="local")

    svc.apply(plan, verify_hash=False, no_autostrip=True)
    actions = _audit_actions(rt)
    assert "migration.autostrip.opted_out" in actions
    assert "migration.autostrip.enabled" not in actions


# ---------------------------------------------------------------------------
# v1.7.35: no_autostrip is a no-op when dst is not public
# ---------------------------------------------------------------------------


def test_no_autostrip_is_noop_when_dst_is_private(migration_runtime, tmp_path):  # noqa: F811
    """v1.7.35: private dst + no_autostrip=True -> no events, no strip.

    The default share_visibility is 'private'. Auto-strip would not have
    happened anyway; no_autostrip=True is a no-op. Neither audit event
    should fire (the opt-out is only logged when it CHANGES behavior).
    """
    rt = migration_runtime
    _register_private_dst(rt, "local")
    stripper = RecordingStripper()
    svc = _build_service_with_strip(rt, stripper)

    src_root = tmp_path / "src"
    dst_root = tmp_path / "dst"
    plan = _plan_one_file(rt, src_root, dst_root, dst_source_id="local")

    svc.apply(plan, verify_hash=False, no_autostrip=True)
    actions = _audit_actions(rt)
    assert actions == [], (
        f"Expected no autostrip events for private dst; got {actions}"
    )
    assert stripper.calls == []


# ---------------------------------------------------------------------------
# v1.7.35: no metadata_stripper wired -> still no-op even with flag
# ---------------------------------------------------------------------------


def test_no_autostrip_is_noop_when_no_stripper_wired(migration_runtime, tmp_path):  # noqa: F811
    """v1.7.35: public dst + flag + NO stripper wired -> no events, nothing.

    Even when the user passes --no-autostrip explicitly, if the service
    isn't wired with a stripper at all, the gating condition for auto-
    strip never holds, so the opt-out is meaningless and no audit event
    is recorded.
    """
    rt = migration_runtime
    _register_public_dst(rt, "local")
    # Build service WITHOUT metadata_stripper or source_repo
    rt.safety.check_path = lambda p, **kw: SafetyReport(
        path=p, level=SafetyLevel.SAFE,
    )
    svc = MigrationService(
        file_repo=rt.file_repo,
        safety=rt.safety,
        audit=rt.audit_repo,
        # source_repo + metadata_stripper intentionally omitted
    )

    src_root = tmp_path / "src"
    dst_root = tmp_path / "dst"
    plan = _plan_one_file(rt, src_root, dst_root, dst_source_id="local")

    svc.apply(plan, verify_hash=False, no_autostrip=True)
    actions = _audit_actions(rt)
    assert actions == [], (
        f"Expected no autostrip events without stripper; got {actions}"
    )
