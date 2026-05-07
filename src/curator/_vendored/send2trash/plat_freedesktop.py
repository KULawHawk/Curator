"""Freedesktop (Linux + BSD) send2trash backend (Phase Beta gate #2).

Implements the freedesktop.org Trash specification 1.0:
https://specifications.freedesktop.org/trash-spec/trashspec-1.0.html

Layout (home trash):

    $XDG_DATA_HOME/Trash/files/<name>          ← actual content
    $XDG_DATA_HOME/Trash/info/<name>.trashinfo ← INI-style metadata

The ``.trashinfo`` file looks like::

    [Trash Info]
    Path=/home/jake/Documents/old.txt
    DeletionDate=2026-05-06T17:30:45

Curator-local scope choices:

  * Phase Beta v0.18: home trash only. Files on different mount points
    that don't share the home filesystem raise :class:`TrashPermissionError`.
    Top-dir trash (``<mount>/.Trash-<uid>``) is a Phase Gamma concern.
  * No URL-encoding of the ``Path=`` field (the spec requires it for
    paths with reserved characters; ours just stores the raw path).
    Most desktop environments (GNOME, KDE) tolerate this. Phase Gamma
    can add full RFC 3986 encoding for stricter compliance.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

from curator._vendored.send2trash.exceptions import TrashPermissionError
from curator._vendored.send2trash.util import preprocess_paths


def _xdg_data_home() -> Path:
    """Return $XDG_DATA_HOME or its default ($HOME/.local/share)."""
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg)
    return Path.home() / ".local" / "share"


def _home_trash() -> Path:
    """Return the home trash directory (creating it if missing)."""
    return _xdg_data_home() / "Trash"


def _on_same_filesystem(a: Path, b: Path) -> bool:
    """True iff both paths resolve to the same filesystem (same st_dev)."""
    try:
        return a.stat().st_dev == b.stat().st_dev
    except OSError:
        return False


def _unique_trash_name(files_dir: Path, original_name: str) -> str:
    """Return a name that doesn't collide with anything in ``files_dir``.

    The freedesktop spec doesn't dictate the disambiguation scheme; we
    follow the GNOME convention of appending ``.N`` before the extension.
    """
    candidate = original_name
    counter = 1
    while (files_dir / candidate).exists():
        stem, dot, suffix = original_name.rpartition(".")
        if dot and stem:
            candidate = f"{stem}.{counter}.{suffix}"
        else:
            candidate = f"{original_name}.{counter}"
        counter += 1
    return candidate


def _write_trashinfo(info_path: Path, original_path: str, deleted_at: datetime) -> None:
    """Write the ``.trashinfo`` metadata file."""
    iso_date = deleted_at.strftime("%Y-%m-%dT%H:%M:%S")
    content = (
        "[Trash Info]\n"
        f"Path={original_path}\n"
        f"DeletionDate={iso_date}\n"
    )
    info_path.write_text(content, encoding="utf-8")


def send2trash(paths):
    """Send one or more paths to the freedesktop home trash.

    Args:
        paths: a single path (str/bytes/PathLike) or an iterable of paths.

    Raises:
        FileNotFoundError: a path doesn't exist.
        TrashPermissionError: the path is on a different filesystem than
                              ``$HOME`` (Phase Beta scope), or the trash
                              directory isn't writable.
        OSError: filesystem-level failure during the move.
        RuntimeError: running on Windows or macOS (use the right backend).
    """
    if sys.platform == "win32" or sys.platform == "darwin":
        raise RuntimeError(
            "send2trash.plat_freedesktop is for Linux/BSD; "
            f"current platform is {sys.platform}"
        )

    paths = preprocess_paths(paths)
    for p in paths:
        if not os.path.lexists(p):
            raise FileNotFoundError(p)

    trash_root = _home_trash()
    files_dir = trash_root / "files"
    info_dir = trash_root / "info"

    # Ensure the trash directories exist.
    try:
        files_dir.mkdir(parents=True, exist_ok=True)
        info_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise TrashPermissionError(str(trash_root)) from e

    home = Path.home()
    for p_str in paths:
        p = Path(p_str).resolve()

        # Phase Beta scope: home trash only. Cross-filesystem trash is
        # a Phase Gamma feature.
        if not _on_same_filesystem(p, home):
            raise TrashPermissionError(str(p))

        original_name = p.name
        trash_name = _unique_trash_name(files_dir, original_name)
        target = files_dir / trash_name
        info_path = info_dir / f"{trash_name}.trashinfo"

        # Write the .trashinfo BEFORE moving (per spec: if move fails
        # we may leave a stranded .trashinfo, which is recoverable;
        # the inverse loses the metadata entirely).
        try:
            _write_trashinfo(info_path, str(p), datetime.now())
        except OSError as e:
            raise TrashPermissionError(str(info_path)) from e

        try:
            os.rename(str(p), str(target))
        except OSError as e:
            # Rollback the .trashinfo so we don't leave junk.
            try:
                info_path.unlink()
            except OSError:
                pass
            if isinstance(e, PermissionError):
                raise TrashPermissionError(str(p)) from e
            raise


__all__ = ["send2trash"]
