"""Focused coverage tests for mcp/auth.py.

Sub-ship v1.7.120 of Round 2 Tier 2.

Closes lines 237-240, 267, 306, 327-332:
* 237-240: `_set_secure_permissions` os.chmod OSError swallow
* 267: `load_keys` with `path=None` → default_keys_file()
* 306: `save_keys` with `path=None` → default_keys_file()
* 327-332: `save_keys` mid-write exception → tmp file cleanup
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

import curator.mcp.auth as auth_mod
from curator.mcp.auth import (
    KeyFileError,
    StoredKey,
    _set_secure_permissions,
    load_keys,
    save_keys,
)


# ---------------------------------------------------------------------------
# _set_secure_permissions chmod failure (237-240)
# ---------------------------------------------------------------------------


def test_set_secure_permissions_swallows_chmod_oserror(tmp_path, monkeypatch):
    # Lines 237-240: on non-Windows, os.chmod can raise OSError →
    # caught with logger.warning, don't propagate.
    target = tmp_path / "test.json"
    target.write_text("{}")

    monkeypatch.setattr(sys, "platform", "linux")

    def boom_chmod(path, mode):
        raise OSError("simulated permission denied")
    monkeypatch.setattr(os, "chmod", boom_chmod)

    # Must not raise.
    _set_secure_permissions(target)


# ---------------------------------------------------------------------------
# load_keys default path (267)
# ---------------------------------------------------------------------------


def test_load_keys_uses_default_path_when_none(monkeypatch, tmp_path):
    # Line 267: path is None → default_keys_file().
    fake_default = tmp_path / "nonexistent.json"
    monkeypatch.setattr(auth_mod, "default_keys_file", lambda: fake_default)
    # File doesn't exist → returns empty list.
    assert load_keys(path=None) == []


# ---------------------------------------------------------------------------
# save_keys default path (306)
# ---------------------------------------------------------------------------


def test_save_keys_uses_default_path_when_none(monkeypatch, tmp_path):
    # Line 306: path is None → default_keys_file().
    fake_default = tmp_path / "out.json"
    monkeypatch.setattr(auth_mod, "default_keys_file", lambda: fake_default)
    save_keys([], path=None)
    assert fake_default.exists()


# ---------------------------------------------------------------------------
# save_keys cleanup on exception (327-332)
# ---------------------------------------------------------------------------


def test_save_keys_cleans_up_temp_file_on_write_exception(tmp_path, monkeypatch):
    # Lines 327-332: an exception during the write/rename block →
    # tmp file is cleaned up, exception propagates.
    target = tmp_path / "out.json"

    # Force os.replace to raise so we hit the except block.
    def boom_replace(src, dst):
        raise OSError("simulated rename failure")
    monkeypatch.setattr(os, "replace", boom_replace)

    with pytest.raises(OSError, match="simulated rename failure"):
        save_keys([], path=target)

    # Verify no leftover tmp files in the parent directory.
    leftover = [p for p in tmp_path.iterdir() if p.name.startswith(".")]
    assert leftover == []


def test_save_keys_swallows_unlink_oserror_during_cleanup(tmp_path, monkeypatch):
    # Lines 329-331: cleanup unlink raises OSError → swallowed (the
    # original exception still propagates).
    target = tmp_path / "out.json"

    # Force os.replace to raise.
    def boom_replace(src, dst):
        raise OSError("simulated rename failure")
    monkeypatch.setattr(os, "replace", boom_replace)

    # Also force os.unlink to raise.
    orig_unlink = os.unlink

    def boom_unlink(path):
        raise OSError("simulated unlink failure")
    monkeypatch.setattr(os, "unlink", boom_unlink)

    # Original exception propagates; unlink failure is swallowed.
    with pytest.raises(OSError, match="simulated rename failure"):
        save_keys([], path=target)
