"""Focused coverage tests for plugins/core/local_source.py.

Sub-ship v1.7.124 of Round 2 Tier 2.

Closes ~50 uncovered lines + 7 partial branches:
* `_matches_ignore` ancestor matching
* `_stat_to_file_stat` body
* `curator_source_enumerate`/`_iter` non-owned + missing-root + ignore + OSError
* `curator_source_read_bytes` non-owned + OSError
* `curator_source_stat` non-owned + OSError
* `curator_source_move` body
* `curator_source_delete` non-owned + to_trash (send2trash present + ImportError fallback)
* `curator_source_write` tmpfile cleanup unlink OSError
* `_owns` DB lookup branches (success + exception swallow)
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from curator.plugins.core.local_source import (
    Plugin,
    _matches_ignore,
    _stat_to_file_stat,
)


# ---------------------------------------------------------------------------
# _matches_ignore (51-56)
# ---------------------------------------------------------------------------


def test_matches_ignore_matches_ancestor_basename(tmp_path):
    # Lines 53-56: ancestor (parent dir) matches the pattern.
    proj = tmp_path / "proj"
    pycache = proj / "__pycache__"
    pycache.mkdir(parents=True)
    target = pycache / "x.pyc"
    target.write_bytes(b"")
    assert _matches_ignore(target, ["__pycache__"]) is True


def test_matches_ignore_glob_pattern_on_ancestor(tmp_path):
    # Line 55 alt arm: parent.match(pattern) with glob.
    proj = tmp_path / "proj"
    cache = proj / ".cache_dir"
    cache.mkdir(parents=True)
    target = cache / "x.txt"
    target.write_bytes(b"")
    assert _matches_ignore(target, [".cache_*"]) is True


def test_matches_ignore_returns_false_for_no_match(tmp_path):
    target = tmp_path / "normal.txt"
    target.write_bytes(b"")
    assert _matches_ignore(target, ["unrelated"]) is False


def test_matches_ignore_matches_basename_directly(tmp_path):
    # Line 52: file's basename matches pattern → return True before the
    # ancestor loop.
    target = tmp_path / "ignored_file.tmp"
    target.write_bytes(b"")
    assert _matches_ignore(target, ["ignored_file.tmp"]) is True


# ---------------------------------------------------------------------------
# _stat_to_file_stat (line 61)
# ---------------------------------------------------------------------------


def test_stat_to_file_stat_returns_populated_filestat(tmp_path):
    target = tmp_path / "x.txt"
    target.write_text("hello")
    result = _stat_to_file_stat(str(target), target.stat())
    assert result.file_id == str(target)
    assert result.size == 5
    assert result.inode > 0


# ---------------------------------------------------------------------------
# curator_source_enumerate / _iter (155, 162, 166, 168, 170-173)
# ---------------------------------------------------------------------------


def test_enumerate_returns_none_for_non_owned_source():
    # Line 155: source_id not owned → return None.
    plugin = Plugin()
    result = plugin.curator_source_enumerate(
        source_id="gdrive", root="/x", options={},
    )
    assert result is None


def test_iter_returns_immediately_when_root_does_not_exist(tmp_path):
    # Line 162: root_path.exists() False → return early.
    plugin = Plugin()
    missing = tmp_path / "does_not_exist"
    result = list(plugin._iter(
        source_id="local", root=str(missing), options={},
    ))
    assert result == []


def test_iter_skips_directories_and_ignored_paths(tmp_path):
    # Lines 165-168: directories skipped + ignored patterns skipped.
    plugin = Plugin()
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "subdir").mkdir()  # directory; skipped by is_file()
    (proj / "real.txt").write_text("x")
    cache = proj / "__pycache__"
    cache.mkdir()
    (cache / "skip.pyc").write_bytes(b"")

    results = list(plugin._iter(
        source_id="local", root=str(proj),
        options={"ignore": ["__pycache__"]},
    ))
    names = [Path(r.file_id).name for r in results]
    assert "real.txt" in names
    assert "skip.pyc" not in names
    # subdir is skipped because is_file() is False on the dir itself


def test_iter_swallows_oserror_during_stat(tmp_path, monkeypatch):
    # Lines 170-173: OSError during is_file/stat → continue.
    plugin = Plugin()
    proj = tmp_path / "proj"
    proj.mkdir()
    bad_file = proj / "bad.txt"
    bad_file.write_text("x")

    orig_is_file = Path.is_file

    def boom_is_file(self):
        if str(self) == str(bad_file):
            raise OSError("simulated permission denied")
        return orig_is_file(self)
    monkeypatch.setattr(Path, "is_file", boom_is_file)

    # No exception — bad_file just gets skipped.
    results = list(plugin._iter(
        source_id="local", root=str(proj), options={},
    ))
    names = [Path(r.file_id).name for r in results]
    assert "bad.txt" not in names


# ---------------------------------------------------------------------------
# curator_source_read_bytes (196, 201-202)
# ---------------------------------------------------------------------------


def test_read_bytes_returns_none_for_non_owned_source():
    plugin = Plugin()
    assert plugin.curator_source_read_bytes(
        source_id="gdrive", file_id="/x", offset=0, length=10,
    ) is None


def test_read_bytes_returns_none_on_oserror(tmp_path):
    # Lines 201-202: open() raises OSError → return None.
    plugin = Plugin()
    missing = tmp_path / "missing.bin"
    assert plugin.curator_source_read_bytes(
        source_id="local", file_id=str(missing), offset=0, length=10,
    ) is None


def test_read_bytes_reads_partial_file(tmp_path):
    plugin = Plugin()
    target = tmp_path / "data.bin"
    target.write_bytes(b"0123456789")
    result = plugin.curator_source_read_bytes(
        source_id="local", file_id=str(target), offset=2, length=4,
    )
    assert result == b"2345"


# ---------------------------------------------------------------------------
# curator_source_stat (206-212)
# ---------------------------------------------------------------------------


def test_stat_returns_none_for_non_owned_source():
    plugin = Plugin()
    assert plugin.curator_source_stat(
        source_id="gdrive", file_id="/x",
    ) is None


def test_stat_returns_none_on_oserror(tmp_path):
    plugin = Plugin()
    missing = tmp_path / "missing.bin"
    assert plugin.curator_source_stat(
        source_id="local", file_id=str(missing),
    ) is None


def test_stat_returns_filestat_for_existing_file(tmp_path):
    plugin = Plugin()
    target = tmp_path / "x.bin"
    target.write_text("hello")
    result = plugin.curator_source_stat(
        source_id="local", file_id=str(target),
    )
    assert result is not None
    assert result.size == 5


# ---------------------------------------------------------------------------
# curator_source_move (223-231)
# ---------------------------------------------------------------------------


def test_move_returns_none_for_non_owned_source():
    plugin = Plugin()
    assert plugin.curator_source_move(
        source_id="gdrive", file_id="/x", new_path="/y",
    ) is None


def test_move_relocates_file_and_returns_new_info(tmp_path):
    plugin = Plugin()
    src = tmp_path / "src.txt"
    src.write_text("data")
    dst = tmp_path / "subdir" / "dst.txt"
    result = plugin.curator_source_move(
        source_id="local", file_id=str(src), new_path=str(dst),
    )
    assert result is not None
    assert dst.exists()
    assert not src.exists()
    assert result.file_id == str(dst)


# ---------------------------------------------------------------------------
# curator_source_delete (294-312)
# ---------------------------------------------------------------------------


def test_delete_returns_none_for_non_owned_source():
    plugin = Plugin()
    assert plugin.curator_source_delete(
        source_id="gdrive", file_id="/x", to_trash=False,
    ) is None


def test_delete_with_to_trash_uses_send2trash_when_available(
    tmp_path, monkeypatch,
):
    # Lines 302-304: send2trash import succeeds, send2trash(file_id)
    # fires, return True. The send2trash PyPI package isn't a hard
    # Curator dep (the vendored copy lives at curator._vendored.send2trash);
    # the `from send2trash import send2trash` line in production code
    # is "use it IF it happens to be installed" defensive logic. Inject
    # a fake send2trash module into sys.modules so the import succeeds.
    import types
    plugin = Plugin()
    target = tmp_path / "delme.txt"
    target.write_text("x")

    calls = []
    def recording_send2trash(path):
        calls.append(path)
        Path(path).unlink()

    fake_module = types.ModuleType("send2trash")
    fake_module.send2trash = recording_send2trash
    monkeypatch.setitem(sys.modules, "send2trash", fake_module)

    result = plugin.curator_source_delete(
        source_id="local", file_id=str(target), to_trash=True,
    )
    assert result is True
    assert calls == [str(target)]
    assert not target.exists()


def test_delete_with_to_trash_falls_back_to_os_remove_on_import_error(
    tmp_path, monkeypatch,
):
    # Lines 305-310: send2trash ImportError → fall back to os.remove.
    plugin = Plugin()
    target = tmp_path / "delme.txt"
    target.write_text("x")

    monkeypatch.setitem(sys.modules, "send2trash", None)

    result = plugin.curator_source_delete(
        source_id="local", file_id=str(target), to_trash=True,
    )
    assert result is True
    assert not target.exists()


def test_delete_without_to_trash_uses_os_remove(tmp_path):
    # Lines 311-312: to_trash=False → direct os.remove.
    plugin = Plugin()
    target = tmp_path / "delme.txt"
    target.write_text("x")

    result = plugin.curator_source_delete(
        source_id="local", file_id=str(target), to_trash=False,
    )
    assert result is True
    assert not target.exists()


# ---------------------------------------------------------------------------
# curator_source_write tmpfile cleanup (366-367)
# ---------------------------------------------------------------------------


def test_write_swallows_unlink_oserror_during_cleanup(tmp_path, monkeypatch):
    # Lines 363-367: an exception during write triggers cleanup; if the
    # cleanup unlink itself fails (OSError), it's swallowed and the
    # original exception still propagates.
    plugin = Plugin()
    parent = tmp_path
    name = "out.bin"

    # Force os.replace to raise so we enter the cleanup branch.
    def boom_replace(src, dst):
        raise OSError("replace failed")
    monkeypatch.setattr(os, "replace", boom_replace)

    # Then force os.unlink to ALSO raise inside cleanup.
    orig_unlink = os.unlink

    def boom_unlink(path):
        raise OSError("unlink failed")
    monkeypatch.setattr(os, "unlink", boom_unlink)

    # The original OSError ("replace failed") still propagates.
    with pytest.raises(OSError, match="replace failed"):
        plugin.curator_source_write(
            source_id="local", parent_id=str(parent), name=name, data=b"x",
        )


# ---------------------------------------------------------------------------
# _owns DB lookup (402-411)
# ---------------------------------------------------------------------------


def test_owns_returns_true_for_db_registered_source():
    # Lines 401-405: source_repo lookup finds a matching source_type.
    plugin = Plugin()
    fake_source = MagicMock()
    fake_source.source_type = "local"
    fake_repo = MagicMock()
    fake_repo.get.return_value = fake_source
    plugin.set_source_repo(fake_repo)

    assert plugin._owns("custom_id_not_local_prefix") is True


def test_owns_returns_false_when_db_returns_none():
    # Lines 401-405: source_repo.get returns None → fall through.
    plugin = Plugin()
    fake_repo = MagicMock()
    fake_repo.get.return_value = None
    plugin.set_source_repo(fake_repo)

    assert plugin._owns("unknown_id") is False


def test_owns_returns_false_when_db_returns_different_source_type():
    # Lines 401-405: source.source_type != "local" → False.
    plugin = Plugin()
    fake_source = MagicMock()
    fake_source.source_type = "gdrive"
    fake_repo = MagicMock()
    fake_repo.get.return_value = fake_source
    plugin.set_source_repo(fake_repo)

    assert plugin._owns("some_id") is False


def test_owns_swallows_db_exception():
    # Lines 406-411: source_repo.get raises → caught, return False.
    plugin = Plugin()
    fake_repo = MagicMock()
    fake_repo.get.side_effect = RuntimeError("db unavailable")
    plugin.set_source_repo(fake_repo)

    assert plugin._owns("some_id") is False
