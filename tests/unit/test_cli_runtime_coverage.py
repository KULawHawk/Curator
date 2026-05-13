"""Focused coverage tests for cli/runtime.py.

Sub-ship v1.7.113 of Round 2 Tier 1.

Closes:
* Line 134: `if config is None: config = Config.load()` default-config path
* Branches 163->174, 175->185, 186->190: the three "plugin not registered" defensives
* Lines 292-293: `_build_fuzzy_index_if_available` ImportError fallback
* Lines 296-299: FuzzyIndexUnavailableError fallback
* Branch 304->303 / Lines 308-311: malformed-hash ValueError swallow
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

import pluggy
import pytest

import curator.cli.runtime as runtime_mod
from curator.cli.runtime import build_runtime, _build_fuzzy_index_if_available
from curator.config import Config
from curator.models.file import FileEntity
from curator.storage import CuratorDB
from curator.storage.repositories import FileRepository


NOW = datetime(2026, 5, 13, 12, 0, 0)


@pytest.fixture
def isolated_db(tmp_path):
    """A real CuratorDB at an isolated path, with migrations applied."""
    db = CuratorDB(tmp_path / "runtime.db")
    db.init()
    return db


# ---------------------------------------------------------------------------
# Line 134: build_runtime(config=None) default-loads config
# ---------------------------------------------------------------------------


def test_build_runtime_with_config_none_loads_default_config(tmp_path, monkeypatch):
    # Line 134: when config arg is None, Config.load() is called.
    # Verify by spying on Config.load.
    cfg = Config.load()
    load_calls = []
    orig_load = Config.load

    def spying_load(*args, **kw):
        load_calls.append((args, kw))
        return cfg

    monkeypatch.setattr(Config, "load", staticmethod(spying_load))

    rt = build_runtime(
        config=None,
        db_path_override=tmp_path / "rt.db",
        verbosity=0,
    )
    assert rt is not None
    assert len(load_calls) >= 1


# ---------------------------------------------------------------------------
# Branches 163->174, 175->185, 186->190: missing plugin defensives
# ---------------------------------------------------------------------------


def test_build_runtime_when_optional_plugins_not_registered(tmp_path, monkeypatch):
    # Branches 163->174, 175->185, 186->190: when pm.get_plugin returns
    # None for the named plugins (audit_writer / gdrive_source /
    # local_source), the three injection blocks are skipped.
    # Patch the plugin manager's get_plugin to always return None.

    # We need to intercept the pm produced by get_plugin_manager INSIDE
    # build_runtime. Easiest: monkeypatch pluggy.PluginManager.get_plugin
    # to always return None.
    monkeypatch.setattr(
        pluggy.PluginManager,
        "get_plugin",
        lambda self, name: None,
    )

    rt = build_runtime(
        config=Config.load(),
        db_path_override=tmp_path / "rt.db",
        verbosity=0,
    )
    # build_runtime returned cleanly despite no injectable plugins.
    assert rt is not None
    assert rt.db is not None


# ---------------------------------------------------------------------------
# _build_fuzzy_index_if_available defensives (292-293, 296-299, 304-311)
# ---------------------------------------------------------------------------


def test_build_fuzzy_index_returns_none_on_import_error(
    isolated_db, monkeypatch,
):
    # Lines 292-293: `from curator.services.fuzzy_index import FuzzyIndex`
    # raises ImportError → return None.
    monkeypatch.setitem(sys.modules, "curator.services.fuzzy_index", None)
    file_repo = FileRepository(isolated_db)
    result = _build_fuzzy_index_if_available(file_repo)
    assert result is None


def test_build_fuzzy_index_returns_none_on_unavailable_error(
    isolated_db, monkeypatch,
):
    # Lines 295-299: FuzzyIndex() raises FuzzyIndexUnavailableError
    # (datasketch not installed at runtime) → return None.
    from curator.services import fuzzy_index as fi_mod

    def boom_init(*args, **kwargs):
        raise fi_mod.FuzzyIndexUnavailableError("datasketch missing")

    monkeypatch.setattr(fi_mod, "FuzzyIndex", boom_init)
    file_repo = FileRepository(isolated_db)
    result = _build_fuzzy_index_if_available(file_repo)
    assert result is None


def test_build_fuzzy_index_swallows_malformed_hash_value_error(
    isolated_db, monkeypatch,
):
    # Lines 308-311: idx.add raises ValueError (malformed hash) →
    # caught, `skipped += 1`, continue. Branch 304->303 covers the
    # `if f.fuzzy_hash:` True branch path that leads into the try.
    #
    # Easier than inserting a real FileEntity through the source FK
    # constraint: stub `file_repo.find_with_fuzzy_hash` to return a
    # synthetic entity. The function calls only `.curator_id` and
    # `.fuzzy_hash` on it.
    from curator.services import fuzzy_index as fi_mod

    fake_entity = FileEntity(
        source_id="local",
        source_path="/x.py",
        size=10,
        mtime=NOW,
        fuzzy_hash="3:abcd:efgh",
    )

    fake_file_repo = MagicMock()
    fake_file_repo.find_with_fuzzy_hash.return_value = [fake_entity]

    # Replace FuzzyIndex with a stub that raises ValueError on add.
    class _BadIndex:
        def add(self, *args, **kwargs):
            raise ValueError("malformed hash")

    monkeypatch.setattr(fi_mod, "FuzzyIndex", lambda *a, **kw: _BadIndex())

    result = _build_fuzzy_index_if_available(fake_file_repo)
    # Returned the index instance; the add ValueError was swallowed
    # and `skipped` was incremented to 1.
    assert result is not None


def test_build_fuzzy_index_skips_entries_without_fuzzy_hash(monkeypatch):
    # Branch 304->303 False arm: `if f.fuzzy_hash:` is False → loop
    # continues without entering the try. Defensive in case
    # find_with_fuzzy_hash ever returns an entity with empty hash.
    from curator.services import fuzzy_index as fi_mod

    no_hash = FileEntity(
        source_id="local",
        source_path="/z.py",
        size=10,
        mtime=NOW,
        fuzzy_hash=None,  # falsy → if branch False
    )
    fake_file_repo = MagicMock()
    fake_file_repo.find_with_fuzzy_hash.return_value = [no_hash]

    class _GoodIndex:
        def __init__(self):
            self.calls = []
        def add(self, curator_id, fuzzy_hash):
            self.calls.append((curator_id, fuzzy_hash))

    monkeypatch.setattr(fi_mod, "FuzzyIndex", lambda *a, **kw: _GoodIndex())

    result = _build_fuzzy_index_if_available(fake_file_repo)
    assert result is not None
    # add was never called (entry skipped).
    assert result.calls == []


def test_build_fuzzy_index_populates_and_logs_when_entries_succeed(
    monkeypatch,
):
    # Lines 312-316 + branch 304->303 True arm: populated > 0 → logger.debug.
    # Plus the happy path of `idx.add` succeeding without raising.
    from curator.services import fuzzy_index as fi_mod

    fake_entity = FileEntity(
        source_id="local",
        source_path="/y.py",
        size=10,
        mtime=NOW,
        fuzzy_hash="3:abcd:efgh",
    )

    fake_file_repo = MagicMock()
    fake_file_repo.find_with_fuzzy_hash.return_value = [fake_entity]

    class _GoodIndex:
        def __init__(self):
            self.calls = []
        def add(self, curator_id, fuzzy_hash):
            self.calls.append((curator_id, fuzzy_hash))

    monkeypatch.setattr(fi_mod, "FuzzyIndex", lambda *a, **kw: _GoodIndex())

    result = _build_fuzzy_index_if_available(fake_file_repo)
    assert result is not None
    assert len(result.calls) == 1
