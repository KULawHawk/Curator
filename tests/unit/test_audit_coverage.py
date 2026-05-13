"""Focused coverage tests for services/audit.py.

Sub-ship v1.7.98 of the Coverage Sweep arc.

Closes the three uncovered lines 83-87 — the entire body of
`AuditService.insert(entry)`, an alternate API path for pre-built
`AuditEntry` instances (vs. the ergonomic `log()` keyword-style
caller).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from curator.models.audit import AuditEntry
from curator.services.audit import AuditService


class _StubAuditRepo:
    """Minimal AuditRepository for testing `AuditService.insert`."""

    def __init__(self):
        self.inserted: list[AuditEntry] = []
        self._next_id = 1

    def log(self, **kw) -> int:  # not used here, present for parity
        self._next_id += 1
        return self._next_id

    def insert(self, entry: AuditEntry) -> int:
        self.inserted.append(entry)
        audit_id = self._next_id
        self._next_id += 1
        return audit_id


def test_insert_passes_entry_to_repo_and_returns_audit_id():
    # Lines 81-87: insert() inserts the entry verbatim via repo.insert
    # and returns the integer audit_id. Also emits a Loguru info event
    # bound with `audit=True` plus the entry's details (not assertable
    # via the stub, but the test exercises the line).
    repo = _StubAuditRepo()
    svc = AuditService(repo)

    entry = AuditEntry(
        actor="curator.test",
        action="test.action",
        entity_type="file",
        entity_id="abc-123",
        details={"some": "data", "more": 42},
        when=datetime(2026, 5, 13, 12, 0, 0),
    )

    audit_id = svc.insert(entry)
    assert audit_id == 1
    assert repo.inserted == [entry]
