"""Focused coverage tests for models/base.py.

Sub-ship v1.7.110 of Round 2 Tier 1.

Closes lines 56-62 (get_computed body), 68 (get_flex body),
76 (has_flex body) — utility methods on the `CuratorEntity` base
class that no existing test exercises directly.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from curator.models.base import CuratorEntity
from curator.models.file import FileEntity


NOW = datetime(2026, 5, 13, 12, 0, 0)


def _make_entity() -> FileEntity:
    return FileEntity(
        source_id="local",
        source_path="/x.txt",
        size=10,
        mtime=NOW,
    )


# ---------------------------------------------------------------------------
# get_flex / has_flex (68, 76)
# ---------------------------------------------------------------------------


def test_flex_property_returns_underlying_dict():
    # Line 43: the `flex` property returns the internal _flex dict.
    entity = _make_entity()
    # Mutate via the property and observe via get_flex.
    entity.flex["direct_key"] = "direct_value"
    assert entity.get_flex("direct_key") == "direct_value"


def test_get_flex_returns_default_when_missing():
    # Line 68: get_flex returns default when key absent.
    entity = _make_entity()
    assert entity.get_flex("nope", default=42) == 42
    assert entity.get_flex("absent") is None


def test_get_flex_returns_value_when_present():
    entity = _make_entity()
    entity.set_flex("tag", "vacation")
    assert entity.get_flex("tag") == "vacation"


def test_has_flex_reflects_set_state():
    # Line 76: has_flex returns True/False based on key presence.
    entity = _make_entity()
    assert entity.has_flex("missing") is False
    entity.set_flex("present", "value")
    assert entity.has_flex("present") is True


# ---------------------------------------------------------------------------
# get_computed (56-62)
# ---------------------------------------------------------------------------


def test_get_computed_returns_first_non_none_plugin_result(monkeypatch):
    # Lines 56-61: walk plugin results, return first non-None.
    entity = _make_entity()
    fake_pm = MagicMock()
    fake_pm.hook.curator_compute_attr.return_value = [None, "computed_value", "later"]
    monkeypatch.setattr(
        "curator.plugins.get_plugin_manager",
        lambda: fake_pm,
    )
    assert entity.get_computed("any_key") == "computed_value"


def test_get_computed_raises_key_error_when_no_plugin_handles(monkeypatch):
    # Line 62: every plugin returned None → KeyError.
    entity = _make_entity()
    fake_pm = MagicMock()
    fake_pm.hook.curator_compute_attr.return_value = [None, None]
    monkeypatch.setattr(
        "curator.plugins.get_plugin_manager",
        lambda: fake_pm,
    )
    with pytest.raises(KeyError, match="any_key"):
        entity.get_computed("any_key")
