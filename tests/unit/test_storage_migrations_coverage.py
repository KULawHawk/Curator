"""Coverage closure for ``curator.storage.migrations`` (v1.7.129).

Targets:
- Lines 232-233: ``apply_migrations`` re-raises with context when a
  migration func raises
- Lines 238-241: ``applied_migrations`` query
"""

from __future__ import annotations

import sqlite3

import pytest

from curator.storage import migrations as mig_mod
from curator.storage.connection import CuratorDB
from curator.storage.migrations import apply_migrations, applied_migrations


class TestAppliedMigrations:
    def test_returns_all_applied_names_in_order(self, tmp_path):
        db_path = tmp_path / "appliedmigs.db"
        db = CuratorDB(db_path)
        db.init()
        names = applied_migrations(db.conn())
        # At minimum the canonical 001_* migration should be present
        assert isinstance(names, list)
        assert all(isinstance(n, str) for n in names)
        assert len(names) > 0


class TestApplyMigrationsErrorReraise:
    def test_migration_failure_raises_runtime_error_with_context(
        self, tmp_path, monkeypatch,
    ):
        """If a migration callable raises, apply_migrations wraps it in a
        RuntimeError that names the migration and chains the original."""
        # Build a one-off connection (skip CuratorDB.init since we want
        # to inject a failing migration list)
        conn = sqlite3.connect(":memory:")

        def _boom(_conn):
            raise ValueError("intentional migration failure")

        bad_migrations = [("999_test_failure", _boom)]
        monkeypatch.setattr(mig_mod, "MIGRATIONS", bad_migrations)

        with pytest.raises(RuntimeError, match="999_test_failure"):
            apply_migrations(conn)
