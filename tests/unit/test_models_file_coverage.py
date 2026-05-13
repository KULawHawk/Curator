"""Focused coverage tests for models/file.py.

Sub-ship v1.7.107 of Round 2 Tier 1 (originally Tier 1 ship 2 in the
handoff; promoted to 1 because migration.py was already at 100% from
v1.7.93b per re-measurement under Lesson #93).

Closes line 99 — the `is_text_eligible` property's return statement.
"""

from __future__ import annotations

from datetime import datetime

from curator.models.file import FileEntity


NOW = datetime(2026, 5, 13, 12, 0, 0)


def _make_entity(*, extension: str | None = None) -> FileEntity:
    return FileEntity(
        source_id="local",
        source_path="/a/b.txt",
        size=10,
        mtime=NOW,
        extension=extension,
    )


def test_is_text_eligible_true_for_known_text_extension():
    # Line 99 True branch: extension in _TEXT_EXTENSIONS_HINT.
    assert _make_entity(extension=".py").is_text_eligible is True
    assert _make_entity(extension=".md").is_text_eligible is True


def test_is_text_eligible_false_for_binary_extension():
    # Line 99 False branch via `in` check: extension not in hint set.
    assert _make_entity(extension=".jpg").is_text_eligible is False


def test_is_text_eligible_false_for_none_extension():
    # Line 99 short-circuit False branch: extension is None.
    assert _make_entity(extension=None).is_text_eligible is False


def test_is_text_eligible_case_insensitive():
    # The property lowercases the extension before checking.
    assert _make_entity(extension=".PY").is_text_eligible is True
    assert _make_entity(extension=".JpG").is_text_eligible is False
