"""Coverage closure for ``curator.services.migration`` dispatcher arms (v1.7.146).

The Round 2 measurement discrepancy was a real coverage gap: the
existing unit tests use direct-call patterns that bypass the
``_execute_one`` dispatcher, and the ``plan()`` False-branch for the
``dst_source_id is None`` check was unreached because tests always
defaulted dst_source_id to None or omitted it.

Integration tests (``tests/integration/test_cli_migrate.py``) DO cover
these lines because they exercise the full dispatch. To make
``pytest tests/unit/`` standalone show 100% on migration.py, these
focused unit tests target:

- Line 505 False branch: ``plan(dst_source_id="gdrive:x")`` skips the
  default-to-src assignment at line 506.
- Lines 984-988: ``_execute_one`` cross-source dispatch arm calls
  ``_execute_one_cross_source`` and returns.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from curator.services.migration import MigrationMove, MigrationOutcome
from curator.services.safety import SafetyLevel
from tests.unit.test_migration_plan_apply import (
    NOW,
    StubFileRepository,
    StubSafetyService,
    StubSourceRepository,
    make_move,
    make_service,
)


class TestPlanDstSourceIdExplicit:
    def test_plan_with_explicit_dst_source_id_skips_default(self):
        """Line 505 False branch: explicit dst_source_id != None skips
        the dst_source_id = src_source_id assignment on line 506."""
        files = StubFileRepository(files=[])
        sources = StubSourceRepository()
        # Register both source IDs so the cross-source path is valid
        from curator.models.source import SourceConfig
        sources.sources = {
            "local": SourceConfig(
                source_id="local", source_type="local",
                display_name="Local", config={"path": "/tmp"},
            ),
            "gdrive:x": SourceConfig(
                source_id="gdrive:x", source_type="gdrive",
                display_name="GDrive X", config={},
            ),
        }
        safety = StubSafetyService(default_level=SafetyLevel.SAFE)
        svc = make_service(file_repo=files, safety=safety, source_repo=sources)
        # plan() with explicit dst_source_id != None
        plan = svc.plan(
            src_source_id="local",
            src_root="/tmp",
            dst_root="/dst",
            dst_source_id="gdrive:x",
        )
        # Plan succeeded; empty file repo so no moves
        assert plan is not None
        assert plan.moves == []


class TestExecuteOneCrossSourceDispatch:
    def test_dispatcher_routes_to_cross_source_helper(self):
        """Lines 984-988: when src != dst source_ids (and both non-None),
        ``_execute_one`` dispatches to ``_execute_one_cross_source`` and
        returns. Stub the helper so we don't need a full plugin setup."""
        svc = make_service()
        move = make_move(src_path="/src/a", dst_path="/dst/a", size=10)
        # Stub the cross-source helper so we just verify dispatch
        cross_source_calls = []

        def _cross_stub(move, *, verify_hash, keep_source,
                        src_source_id, dst_source_id):
            cross_source_calls.append({
                "verify_hash": verify_hash,
                "keep_source": keep_source,
                "src_source_id": src_source_id,
                "dst_source_id": dst_source_id,
            })

        svc._execute_one_cross_source = _cross_stub  # type: ignore[method-assign]
        # Also stub same-source so if dispatch routes wrong we'd notice
        svc._execute_one_same_source = MagicMock()  # type: ignore[method-assign]

        # Call with cross-source IDs
        svc._execute_one(
            move, verify_hash=True, keep_source=False,
            src_source_id="local", dst_source_id="gdrive:x",
        )
        # Cross-source helper was called once with the right args
        assert len(cross_source_calls) == 1
        assert cross_source_calls[0]["src_source_id"] == "local"
        assert cross_source_calls[0]["dst_source_id"] == "gdrive:x"
        # Same-source helper was NOT called
        svc._execute_one_same_source.assert_not_called()
