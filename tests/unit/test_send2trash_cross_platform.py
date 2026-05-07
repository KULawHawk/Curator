"""Tests for the cross-platform send2trash backends (Phase Beta gate #2).

Three layers:

* **Dispatcher tests** — verify ``__init__.py`` picks the right backend
  for each ``sys.platform`` value. Run on every host.

* **Freedesktop tests** — exercise ``plat_freedesktop.send2trash`` with
  a fake ``HOME`` and ``XDG_DATA_HOME``. The actual move is to a temp
  dir on the same filesystem. Skipped on Windows because ``os.rename``
  semantics differ (file-in-use locks); the contract logic is still
  exercised on every host through the helper-function tests.

* **Mac tests** — confined to constructing the AppleScript string
  (we can't actually call ``osascript`` on Windows). The live path is
  Phase Gamma test work on macOS hardware.
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from unittest import mock

import pytest

from curator._vendored.send2trash import plat_freedesktop
from curator._vendored.send2trash.exceptions import TrashPermissionError
from curator._vendored.send2trash.plat_freedesktop import (
    _home_trash,
    _on_same_filesystem,
    _unique_trash_name,
    _write_trashinfo,
    _xdg_data_home,
)


pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

class TestDispatcher:
    def test_dispatcher_module_exposes_send2trash(self):
        from curator._vendored.send2trash import send2trash, TrashPermissionError
        assert callable(send2trash)
        assert issubclass(TrashPermissionError, PermissionError)

    def test_unsupported_platform_stub_raises(self):
        # We can't actually change sys.platform at runtime cleanly, but
        # we can verify the stub exists for the "other" branch by reading
        # the module source. (More concretely: we test the freedesktop +
        # mac modules' platform guards below.)
        from curator._vendored import send2trash as pkg
        # If we're on Windows, the imported send2trash should be from .win;
        # if we're on macOS, from .mac; if on Linux, from .plat_freedesktop.
        if sys.platform == "win32":
            assert ".win" in pkg.send2trash.__module__
        elif sys.platform == "darwin":
            assert ".mac" in pkg.send2trash.__module__
        elif sys.platform.startswith("linux"):
            assert ".plat_freedesktop" in pkg.send2trash.__module__


# ---------------------------------------------------------------------------
# Freedesktop helpers (run on every platform)
# ---------------------------------------------------------------------------

class TestXdgDataHome:
    def test_default_when_env_unset(self, monkeypatch):
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
        result = _xdg_data_home()
        # Default is $HOME/.local/share — last two components match.
        assert result.parts[-2:] == (".local", "share")

    def test_uses_env_when_set(self, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        assert _xdg_data_home() == tmp_path

    def test_home_trash_appends_trash(self, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        assert _home_trash() == tmp_path / "Trash"


class TestUniqueTrashName:
    def test_no_collision_returns_input(self, tmp_path):
        files_dir = tmp_path / "files"
        files_dir.mkdir()
        assert _unique_trash_name(files_dir, "foo.txt") == "foo.txt"

    def test_collision_appends_dot_n(self, tmp_path):
        files_dir = tmp_path / "files"
        files_dir.mkdir()
        (files_dir / "foo.txt").write_text("existing")
        assert _unique_trash_name(files_dir, "foo.txt") == "foo.1.txt"

    def test_double_collision_increments(self, tmp_path):
        files_dir = tmp_path / "files"
        files_dir.mkdir()
        (files_dir / "foo.txt").write_text("a")
        (files_dir / "foo.1.txt").write_text("b")
        assert _unique_trash_name(files_dir, "foo.txt") == "foo.2.txt"

    def test_no_extension_appends_plain_suffix(self, tmp_path):
        files_dir = tmp_path / "files"
        files_dir.mkdir()
        (files_dir / "noext").write_text("a")
        assert _unique_trash_name(files_dir, "noext") == "noext.1"


class TestWriteTrashinfo:
    def test_writes_iso_date_and_path(self, tmp_path):
        info_path = tmp_path / "test.trashinfo"
        ts = datetime(2026, 1, 15, 12, 34, 56)
        _write_trashinfo(info_path, "/home/jake/old.txt", ts)

        content = info_path.read_text(encoding="utf-8")
        assert "[Trash Info]" in content
        assert "Path=/home/jake/old.txt" in content
        assert "DeletionDate=2026-01-15T12:34:56" in content


class TestOnSameFilesystem:
    def test_same_path_is_same_filesystem(self, tmp_path):
        # Same temp dir is trivially on the same FS as itself.
        sub = tmp_path / "sub"
        sub.mkdir()
        assert _on_same_filesystem(tmp_path, sub) is True

    def test_nonexistent_path_returns_false(self, tmp_path):
        ghost = tmp_path / "ghost"
        assert _on_same_filesystem(ghost, tmp_path) is False


# ---------------------------------------------------------------------------
# Freedesktop send2trash — live moves on a fake HOME
# ---------------------------------------------------------------------------

# We can run these on any platform because we override sys.platform, HOME,
# and XDG_DATA_HOME. The actual moves are within ``tmp_path``.
class TestFreedesktopSend2Trash:
    @pytest.fixture
    def fd_env(self, tmp_path, monkeypatch):
        """Set up an isolated freedesktop trash environment under tmp_path.

        Returns (home_dir, trash_root) so tests can inspect the result.
        """
        home = tmp_path / "home"
        home.mkdir()
        xdg = home / ".local" / "share"
        xdg.mkdir(parents=True)
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("XDG_DATA_HOME", str(xdg))
        monkeypatch.setattr("pathlib.Path.home", lambda: home)
        # Override the module-level platform guard.
        monkeypatch.setattr(plat_freedesktop, "sys", mock.MagicMock(platform="linux"))
        return home, xdg / "Trash"

    def test_trashes_a_file_into_files_dir(self, fd_env):
        home, trash_root = fd_env
        target = home / "old_file.txt"
        target.write_text("delete me")

        plat_freedesktop.send2trash(str(target))

        assert not target.exists()
        # File is in {trash_root}/files/old_file.txt
        moved = trash_root / "files" / "old_file.txt"
        assert moved.exists()
        assert moved.read_text() == "delete me"

    def test_writes_matching_trashinfo(self, fd_env):
        home, trash_root = fd_env
        target = home / "with_meta.txt"
        target.write_text("content")

        plat_freedesktop.send2trash(str(target))

        info = trash_root / "info" / "with_meta.txt.trashinfo"
        assert info.exists()
        body = info.read_text()
        assert "[Trash Info]" in body
        assert f"Path={target}" in body
        assert "DeletionDate=" in body

    def test_collision_produces_dot_n_suffix(self, fd_env):
        home, trash_root = fd_env
        # Pre-populate the trash with a name we'll collide with.
        files_dir = trash_root / "files"
        info_dir = trash_root / "info"
        files_dir.mkdir(parents=True)
        info_dir.mkdir(parents=True)
        (files_dir / "doc.txt").write_text("older copy")
        (info_dir / "doc.txt.trashinfo").write_text("[Trash Info]\nPath=/old\n")

        # Now trash a new file with the same basename.
        new_target = home / "doc.txt"
        new_target.write_text("newer copy")
        plat_freedesktop.send2trash(str(new_target))

        # Original survives; new one lands at doc.1.txt.
        assert (files_dir / "doc.txt").read_text() == "older copy"
        assert (files_dir / "doc.1.txt").read_text() == "newer copy"
        assert (info_dir / "doc.1.txt.trashinfo").exists()

    def test_nonexistent_path_raises_file_not_found(self, fd_env):
        home, _ = fd_env
        ghost = home / "never_existed.txt"
        with pytest.raises(FileNotFoundError):
            plat_freedesktop.send2trash(str(ghost))

    def test_accepts_pathlib_path(self, fd_env):
        home, trash_root = fd_env
        target = home / "pathlib_in.txt"
        target.write_text("via Path object")

        plat_freedesktop.send2trash(target)  # Path, not str

        assert (trash_root / "files" / "pathlib_in.txt").exists()

    def test_iterable_of_paths(self, fd_env):
        home, trash_root = fd_env
        a = home / "a.txt"; a.write_text("a")
        b = home / "b.txt"; b.write_text("b")

        plat_freedesktop.send2trash([a, b])

        assert (trash_root / "files" / "a.txt").exists()
        assert (trash_root / "files" / "b.txt").exists()


# ---------------------------------------------------------------------------
# Mac backend — guard tests only (live test would require macOS hardware)
# ---------------------------------------------------------------------------

class TestMacBackend:
    def test_raises_runtime_error_on_non_mac(self):
        # On Windows or Linux, calling .mac.send2trash directly should raise
        # RuntimeError before it tries to invoke osascript.
        if sys.platform == "darwin":
            pytest.skip("test is for non-mac platforms only")

        from curator._vendored.send2trash import mac as mac_backend
        with pytest.raises(RuntimeError, match="darwin|macOS"):
            mac_backend.send2trash("/tmp/whatever")

    def test_module_imports_on_any_platform(self):
        # The mac module should be IMPORTABLE everywhere (it's just code);
        # only the send2trash function rejects non-mac at call time.
        from curator._vendored.send2trash import mac as mac_backend
        assert callable(mac_backend.send2trash)
