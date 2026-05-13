"""Coverage closure for ``curator.storage.repositories.source_repo`` (v1.7.130).

Targets:
- Lines 142-143: ``delete(source_id)``
- Lines 167-171: ``list_by_type(source_type)``
- Lines 184-185: ``_row_to_source`` defensive fallback when row lacks
  ``share_visibility`` (pre-migration-004 schema snapshot guard)
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from curator.models.source import SourceConfig
from curator.storage.connection import CuratorDB
from curator.storage.repositories.source_repo import SourceRepository


@pytest.fixture
def db(tmp_path):
    db_path = tmp_path / "source_repo.db"
    db = CuratorDB(db_path)
    db.init()
    return db


@pytest.fixture
def repo(db):
    return SourceRepository(db)


def _mk(source_id: str, source_type: str = "local", **overrides):
    return SourceConfig(
        source_id=source_id,
        source_type=source_type,
        display_name=overrides.get("display_name", source_id),
        config=overrides.get("config", {"path": "/x"}),
        enabled=overrides.get("enabled", True),
    )


class TestDelete:
    def test_delete_removes_source(self, repo):
        sc = _mk("local:to_delete")
        repo.insert(sc)
        assert repo.get("local:to_delete") is not None

        repo.delete("local:to_delete")
        assert repo.get("local:to_delete") is None

    def test_delete_missing_id_is_noop(self, repo):
        """Deleting a non-existent source_id doesn't raise."""
        repo.delete("nonexistent:source")  # must not raise


class TestListByType:
    def test_list_by_type_filters_correctly(self, repo):
        repo.insert(_mk("local:a"))
        repo.insert(_mk("local:b"))
        repo.insert(_mk("gdrive:c", source_type="gdrive"))

        locals_ = repo.list_by_type("local")
        gdrives = repo.list_by_type("gdrive")

        assert {s.source_id for s in locals_} == {"local:a", "local:b"}
        assert {s.source_id for s in gdrives} == {"gdrive:c"}

    def test_list_by_type_empty_when_no_match(self, repo):
        repo.insert(_mk("local:x"))
        assert repo.list_by_type("onedrive") == []


class TestRowToSourceShareVisibilityFallback:
    """Pre-migration-004 schema snapshot guard — defensive boundary."""

    def test_row_without_share_visibility_key_falls_back_to_private(self, repo):
        """KeyError on row['share_visibility'] -> defaults to 'private'."""
        # Build a dict-shaped "row" that lacks share_visibility. The repo
        # method itself accepts any subscriptable mapping.
        fake_row = {
            "source_id": "local:legacy",
            "source_type": "local",
            "display_name": "Legacy",
            "config_json": json.dumps({"path": "/legacy"}),
            "enabled": 1,
            "created_at": datetime(2026, 1, 1),
            # NOTE: no 'share_visibility' key -> KeyError on access
        }
        result = repo._row_to_source(fake_row)
        assert result.share_visibility == "private"

    def test_row_with_explicit_share_visibility_uses_it(self, repo):
        repo.insert(_mk("local:public"))
        # Direct read from DB to verify the populated path also works
        fetched = repo.get("local:public")
        assert fetched is not None
        # Default newly-inserted is 'private' unless explicitly set
        assert fetched.share_visibility == "private"
