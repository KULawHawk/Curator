"""Coverage closure for ``curator.storage.repositories.audit_repo`` (v1.7.128).

Targets the 3 missing lines (84-88): the ``AuditRepository.get`` method.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from curator.models.audit import AuditEntry
from curator.storage.connection import CuratorDB
from curator.storage.repositories.audit_repo import AuditRepository


@pytest.fixture
def db(tmp_path):
    """A real CuratorDB with migrations applied."""
    db_path = tmp_path / "audit.db"
    db = CuratorDB(db_path)
    db.init()
    return db


@pytest.fixture
def repo(db):
    return AuditRepository(db)


class TestGetById:
    def test_get_existing_entry_returns_full_audit_entry(self, repo):
        """Line 84-88: ``get()`` for a known audit_id returns an AuditEntry."""
        audit_id = repo.log(
            actor="alice",
            action="scan",
            entity_type="file",
            entity_id="file_xyz",
            details={"reason": "scheduled"},
            when=datetime(2026, 1, 15, 12, 0, 0),
        )
        fetched = repo.get(audit_id)
        assert fetched is not None
        assert isinstance(fetched, AuditEntry)
        assert fetched.audit_id == audit_id
        assert fetched.actor == "alice"
        assert fetched.action == "scan"
        assert fetched.entity_type == "file"
        assert fetched.entity_id == "file_xyz"
        assert fetched.details == {"reason": "scheduled"}

    def test_get_missing_id_returns_none(self, repo):
        """``get()`` for an unknown audit_id returns None (no row -> falsy)."""
        # Insert one entry just to confirm the table exists
        repo.log(actor="bob", action="trash")
        assert repo.get(99999) is None
