"""Unit tests for AuditWriterPlugin and the curator_audit_event hookspec.

Covers:
- The hookimpl persists a valid event to the repo
- The hookimpl swallows DB errors and logs (DM-4 best-effort semantics)
- The hookspec is reachable via pm.hook.curator_audit_event(...) after build_runtime
- Existing core audit writes (MigrationService direct path) still work (DM-3 invariant)
- The placeholder behavior: events fired before set_audit_repo are logged-and-dropped

Cross-references docs/CURATOR_AUDIT_EVENT_HOOKSPEC_DESIGN.md v0.2.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from curator.cli.runtime import build_runtime
from curator.config import Config
from curator.models.audit import AuditEntry
from curator.plugins.core.audit_writer import AuditWriterPlugin
from curator.storage import CuratorDB
from curator.storage.repositories import AuditRepository


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def real_audit_repo(tmp_path):
    """A real AuditRepository backed by a tmp SQLite DB."""
    db_path = tmp_path / "audit_test.db"
    db = CuratorDB(db_path)
    db.init()
    return AuditRepository(db)


@pytest.fixture
def runtime(tmp_path):
    """A real build_runtime with an isolated DB."""
    db_path = tmp_path / "runtime.db"
    cfg = Config.load()
    return build_runtime(
        config=cfg,
        db_path_override=db_path,
        json_output=False,
        no_color=True,
        verbosity=0,
    )


# ---------------------------------------------------------------------------
# Direct unit tests on AuditWriterPlugin (no runtime)
# ---------------------------------------------------------------------------


class TestAuditWriterPluginDirect:
    """Tests that exercise AuditWriterPlugin's hookimpl directly, with
    a hand-constructed instance. No real Curator runtime needed."""

    def test_hookimpl_persists_valid_entry(self, real_audit_repo):
        """Construct AuditWriterPlugin with a real repo, call the
        hookimpl directly, verify the entry actually lands in the DB."""
        plugin = AuditWriterPlugin(audit_repo=real_audit_repo)

        plugin.curator_audit_event(
            actor="test.actor",
            action="test.action",
            entity_type="file",
            entity_id="abc-123",
            details={"key": "value", "n": 42},
        )

        # Verify it actually persisted
        results = real_audit_repo.query(actor="test.actor")
        assert len(results) == 1
        entry = results[0]
        assert entry.actor == "test.actor"
        assert entry.action == "test.action"
        assert entry.entity_type == "file"
        assert entry.entity_id == "abc-123"
        assert entry.details == {"key": "value", "n": 42}

    def test_hookimpl_swallows_db_errors(self, caplog):
        """If audit_repo.insert raises, the hookimpl logs and swallows;
        does NOT propagate the exception. Per DM-4 best-effort."""
        mock_repo = MagicMock(spec=AuditRepository)
        mock_repo.insert.side_effect = RuntimeError("DB locked")

        plugin = AuditWriterPlugin(audit_repo=mock_repo)

        # Should NOT raise
        plugin.curator_audit_event(
            actor="test.actor",
            action="test.action",
            entity_type="file",
            entity_id="x",
            details={},
        )

        # The repo was called (so the hookimpl ran)
        mock_repo.insert.assert_called_once()

    def test_hookimpl_drops_events_when_repo_not_set(self):
        """Before set_audit_repo is called, the hookimpl logs at debug
        and returns without crashing. This is the placeholder pattern
        for events fired during curator_plugin_init."""
        plugin = AuditWriterPlugin()  # no repo
        assert plugin.audit_repo is None

        # Should NOT raise
        plugin.curator_audit_event(
            actor="early.actor",
            action="early.action",
            entity_type="file",
            entity_id="x",
            details={},
        )
        # No way to verify the log without inspecting loguru's sink;
        # the contract is "doesn't raise + doesn't persist", which is
        # confirmed by the absence of an exception above.

    def test_set_audit_repo_enables_persistence(self, real_audit_repo):
        """After set_audit_repo, subsequent events DO persist."""
        plugin = AuditWriterPlugin()  # placeholder
        # Event 1 dropped (no repo)
        plugin.curator_audit_event(
            actor="x", action="dropped", entity_type=None,
            entity_id=None, details={},
        )
        # Inject repo
        plugin.set_audit_repo(real_audit_repo)
        # Event 2 persisted
        plugin.curator_audit_event(
            actor="x", action="persisted", entity_type=None,
            entity_id=None, details={},
        )

        # Only the second event landed in the repo
        results = real_audit_repo.query(actor="x")
        assert len(results) == 1
        assert results[0].action == "persisted"


# ---------------------------------------------------------------------------
# Integration tests through the real plugin manager
# ---------------------------------------------------------------------------


class TestAuditEventHookspecAfterBuildRuntime:
    """Tests that exercise the hookspec via the real plugin manager
    after build_runtime has wired everything together."""

    def test_hookspec_reachable_via_pm_after_build_runtime(self, runtime):
        """After build_runtime returns, pm.hook.curator_audit_event(...)
        is a valid call that doesn't raise."""
        # The hookspec must exist on pm.hook
        assert hasattr(runtime.pm.hook, "curator_audit_event")
        # And must be callable
        runtime.pm.hook.curator_audit_event(
            actor="test.runtime",
            action="test.runtime.action",
            entity_type="file",
            entity_id="abc",
            details={"runtime": True},
        )

    def test_audit_writer_plugin_is_registered_and_wired(self, runtime):
        """The AuditWriterPlugin core plugin must be registered AND
        have its audit_repo injected by build_runtime."""
        plugin = runtime.pm.get_plugin("curator.core.audit_writer")
        assert plugin is not None
        assert isinstance(plugin, AuditWriterPlugin)
        assert plugin.audit_repo is not None
        # Should be the same repo as runtime.audit_repo
        assert plugin.audit_repo is runtime.audit_repo

    def test_event_via_pm_persists_to_runtime_audit_repo(self, runtime):
        """Firing curator_audit_event via pm.hook lands in runtime.audit_repo."""
        runtime.pm.hook.curator_audit_event(
            actor="curatorplug.test",
            action="compliance.test",
            entity_type="file",
            entity_id="aaa-bbb-ccc",
            details={"src_xxhash": "deadbeef", "mode": "strict"},
        )

        results = runtime.audit_repo.query(actor="curatorplug.test")
        assert len(results) == 1
        entry = results[0]
        assert entry.action == "compliance.test"
        assert entry.entity_id == "aaa-bbb-ccc"
        assert entry.details == {"src_xxhash": "deadbeef", "mode": "strict"}


class TestExistingDirectAuditWritesStillWork:
    """Regression guard for DM-3: keep direct-to-repo writes from
    MigrationService working. The new hookspec is purely for
    plugin-driven events; it does NOT replace the existing path."""

    def test_direct_audit_repo_insert_still_works(self, runtime):
        """audit_repo.insert(entry) -- the path MigrationService uses --
        must continue to work unchanged."""
        entry = AuditEntry(
            actor="curator.migrate",
            action="migration.move",
            entity_type="file",
            entity_id="file-xyz",
            details={"src": "/a", "dst": "/b"},
        )

        # Direct insert, NOT via hookspec
        audit_id = runtime.audit_repo.insert(entry)
        assert audit_id is not None
        assert audit_id > 0

        # Verify it's queryable
        results = runtime.audit_repo.query(actor="curator.migrate")
        assert len(results) == 1
        assert results[0].entity_id == "file-xyz"

    def test_hook_and_direct_paths_coexist(self, runtime):
        """Both write paths (direct insert + via hookspec) write to the
        same table; the events from both paths are queryable together."""
        # Path 1: direct insert (MigrationService-style)
        runtime.audit_repo.insert(AuditEntry(
            actor="curator.migrate", action="migration.move",
            entity_type="file", entity_id="f1", details={"path": 1},
        ))
        # Path 2: via hookspec (plugin-style)
        runtime.pm.hook.curator_audit_event(
            actor="curatorplug.example", action="plugin.action",
            entity_type="file", entity_id="f2", details={"path": 2},
        )

        # Both should be queryable
        all_entries = runtime.audit_repo.query()
        actors = {e.actor for e in all_entries}
        assert "curator.migrate" in actors
        assert "curatorplug.example" in actors
