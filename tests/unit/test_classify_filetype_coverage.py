"""Focused coverage tests for plugins/core/classify_filetype.py.

Sub-ship v1.7.117 of Round 2 Tier 2.

Closes lines 35, 42-43, 47, 51-52, 59, 68 + 4 partial branches.
"""

from __future__ import annotations

import sys
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from curator.models.file import FileEntity
from curator.plugins.core.classify_filetype import Plugin


NOW = datetime(2026, 5, 13, 12, 0, 0)


def _make_entity(*, source_id: str = "local", source_path: str = "/x.txt") -> FileEntity:
    return FileEntity(
        source_id=source_id,
        source_path=source_path,
        size=10,
        mtime=NOW,
    )


def test_non_local_source_returns_none():
    # Line 35: source_id doesn't start with "local" → return None.
    plugin = Plugin()
    entity = _make_entity(source_id="gdrive", source_path="/some/id")
    assert plugin.curator_classify_file(file=entity) is None


def test_filetype_not_installed_returns_none(monkeypatch):
    # Lines 41-43: filetype library import fails → return None.
    monkeypatch.setitem(sys.modules, "filetype", None)
    plugin = Plugin()
    entity = _make_entity(source_path="/nonexistent.txt")
    assert plugin.curator_classify_file(file=entity) is None


def test_path_does_not_exist_returns_none(tmp_path):
    # Line 47: path doesn't exist on disk → return None.
    plugin = Plugin()
    entity = _make_entity(source_path=str(tmp_path / "missing.txt"))
    assert plugin.curator_classify_file(file=entity) is None


def test_filetype_guess_raises_returns_none(tmp_path, monkeypatch):
    # Lines 50-52: filetype.guess raises any Exception → caught,
    # return None.
    target = tmp_path / "f.bin"
    target.write_bytes(b"x" * 300)  # need >261 bytes for filetype check
    plugin = Plugin()
    entity = _make_entity(source_path=str(target))

    import filetype

    def boom_guess(p):
        raise RuntimeError("filetype crashed")
    monkeypatch.setattr(filetype, "guess", boom_guess)

    assert plugin.curator_classify_file(file=entity) is None


def test_magic_byte_hit_returns_classification(tmp_path, monkeypatch):
    # Lines 68-73: filetype.guess returns a Kind → return
    # FileClassification with file_type=mime, extension=".ext",
    # confidence=0.95.
    target = tmp_path / "f.bin"
    target.write_bytes(b"x" * 300)
    plugin = Plugin()
    entity = _make_entity(source_path=str(target))

    import filetype
    fake_kind = SimpleNamespace(mime="image/jpeg", extension="jpg")
    monkeypatch.setattr(filetype, "guess", lambda p: fake_kind)

    result = plugin.curator_classify_file(file=entity)
    assert result is not None
    assert result.file_type == "image/jpeg"
    assert result.extension == ".jpg"
    assert result.confidence == 0.95
    assert "filetype" in result.classifier


def test_magic_byte_hit_with_no_extension(tmp_path, monkeypatch):
    # Branch coverage on `f".{kind.extension}" if kind.extension else None`.
    target = tmp_path / "f.bin"
    target.write_bytes(b"x" * 300)
    plugin = Plugin()
    entity = _make_entity(source_path=str(target))

    import filetype
    fake_kind = SimpleNamespace(mime="application/octet-stream", extension=None)
    monkeypatch.setattr(filetype, "guess", lambda p: fake_kind)

    result = plugin.curator_classify_file(file=entity)
    assert result is not None
    assert result.extension is None


def test_no_signature_match_with_text_extension_falls_back(tmp_path, monkeypatch):
    # Lines 58-65: filetype.guess returns None + path has a known
    # text extension → return FileClassification with file_type=text/plain,
    # confidence=0.6, notes mentioning extension fallback.
    target = tmp_path / "script.py"
    target.write_text("print('hello')\n" * 50)  # >261 bytes
    plugin = Plugin()
    entity = _make_entity(source_path=str(target))

    import filetype
    monkeypatch.setattr(filetype, "guess", lambda p: None)

    result = plugin.curator_classify_file(file=entity)
    assert result is not None
    assert result.file_type == "text/plain"
    assert result.extension == ".py"
    assert result.confidence == 0.6


def test_no_signature_match_with_unknown_extension_returns_none(
    tmp_path, monkeypatch,
):
    # Line 66: filetype.guess returns None + extension not in text-set →
    # return None.
    target = tmp_path / "file.xyz"
    target.write_bytes(b"x" * 300)
    plugin = Plugin()
    entity = _make_entity(source_path=str(target))

    import filetype
    monkeypatch.setattr(filetype, "guess", lambda p: None)

    assert plugin.curator_classify_file(file=entity) is None
