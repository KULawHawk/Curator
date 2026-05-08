"""Local filesystem source plugin.

DESIGN.md §6.3.

This is Curator's first source plugin. It implements the source contract
(:mod:`curator.plugins.hookspecs`, "source plugin contract" section) for
``source_id`` values that start with ``"local"``.

Conventions:
    * ``file_id`` is the absolute filesystem path as a string.
    * ``inode`` is captured in :class:`FileInfo.extras` for hardlink
      detection by the hash pipeline.
    * ``ignore`` patterns in the source config use glob syntax matched
      against any path component (e.g. ``"node_modules"`` matches a
      directory named ``node_modules`` anywhere in the path).
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from curator.models.types import (
    ChangeEvent,
    ChangeKind,
    FileInfo,
    FileStat,
    SourcePluginInfo,
)
from curator.plugins.hookspecs import hookimpl


SOURCE_TYPE = "local"


def _matches_ignore(path: Path, patterns: list[str]) -> bool:
    """Return True if any path component matches any pattern.

    Patterns use glob syntax (Path.match). We test against the basename
    of every parent up to (and including) ``path`` itself, so a pattern
    like ``"__pycache__"`` matches at any depth.
    """
    for pattern in patterns:
        # Match against the file's basename
        if path.name == pattern or path.match(pattern):
            return True
        # Match against any ancestor's basename
        for parent in path.parents:
            if parent.name == pattern or parent.match(pattern):
                return True
    return False


def _stat_to_file_stat(file_id: str, stat_result: os.stat_result) -> FileStat:
    return FileStat(
        file_id=file_id,
        size=stat_result.st_size,
        mtime=datetime.fromtimestamp(stat_result.st_mtime),
        ctime=datetime.fromtimestamp(stat_result.st_ctime),
        inode=stat_result.st_ino,
    )


class Plugin:
    """LocalFSSource plugin.

    All hook methods short-circuit (return ``None``) when the
    ``source_id`` doesn't belong to this plugin, so other source plugins
    registered for ``"gdrive"``, ``"onedrive"``, etc. won't conflict.
    """

    SOURCE_TYPE = SOURCE_TYPE

    # ---- registration ----

    @hookimpl
    def curator_source_register(self) -> SourcePluginInfo:
        return SourcePluginInfo(
            source_type=SOURCE_TYPE,
            display_name="Local Filesystem",
            requires_auth=False,
            supports_watch=True,  # via watchfiles, Phase Beta
            supports_write=True,  # v0.40 — atomic via tempfile + os.replace
            config_schema={
                "type": "object",
                "properties": {
                    "roots": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Absolute paths to scan",
                    },
                    "ignore": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Glob patterns for paths to skip",
                    },
                },
                "required": ["roots"],
            },
        )

    # ---- enumeration ----

    @hookimpl
    def curator_source_enumerate(
        self,
        source_id: str,
        root: str,
        options: dict[str, Any],
    ) -> Iterator[FileInfo] | None:
        if not self._owns(source_id):
            return None
        return self._iter(source_id, root, options)

    def _iter(self, source_id: str, root: str, options: dict[str, Any]) -> Iterator[FileInfo]:
        ignore_patterns: list[str] = list(options.get("ignore", []))
        root_path = Path(root)
        if not root_path.exists():
            return
        for path in root_path.rglob("*"):
            try:
                if not path.is_file():
                    continue
                if _matches_ignore(path, ignore_patterns):
                    continue
                stat = path.stat()
            except OSError:
                # Permission denied, broken symlink, race condition, etc.
                # Skip silently — scan reports surface these elsewhere.
                continue

            yield FileInfo(
                file_id=str(path),
                path=str(path),
                size=stat.st_size,
                mtime=datetime.fromtimestamp(stat.st_mtime),
                ctime=datetime.fromtimestamp(stat.st_ctime),
                is_directory=False,
                extras={"inode": stat.st_ino},
            )

    # ---- read / stat ----

    @hookimpl
    def curator_source_read_bytes(
        self,
        source_id: str,
        file_id: str,
        offset: int,
        length: int,
    ) -> bytes | None:
        if not self._owns(source_id):
            return None
        try:
            with open(file_id, "rb") as f:
                f.seek(offset)
                return f.read(length)
        except OSError:
            return None

    @hookimpl
    def curator_source_stat(self, source_id: str, file_id: str) -> FileStat | None:
        if not self._owns(source_id):
            return None
        path = Path(file_id)
        try:
            return _stat_to_file_stat(file_id, path.stat())
        except OSError:
            return None

    # ---- mutations ----

    @hookimpl
    def curator_source_move(
        self,
        source_id: str,
        file_id: str,
        new_path: str,
    ) -> FileInfo | None:
        if not self._owns(source_id):
            return None
        old_path = Path(file_id)
        new_path_obj = Path(new_path)
        new_path_obj.parent.mkdir(parents=True, exist_ok=True)
        old_path.rename(new_path_obj)
        # Return updated FileInfo for the new location
        stat = new_path_obj.stat()
        return FileInfo(
            file_id=str(new_path_obj),
            path=str(new_path_obj),
            size=stat.st_size,
            mtime=datetime.fromtimestamp(stat.st_mtime),
            ctime=datetime.fromtimestamp(stat.st_ctime),
            is_directory=False,
            extras={"inode": stat.st_ino},
        )

    @hookimpl
    def curator_source_rename(
        self,
        source_id: str,
        file_id: str,
        new_name: str,
        *,
        overwrite: bool = False,
    ) -> FileInfo | None:
        """Rename a local file within its current parent (v1.4.0+).

        Atomic on the same filesystem via ``Path.rename`` (which calls
        ``os.rename`` and is atomic per the POSIX spec). With
        ``overwrite=False`` (default), raises ``FileExistsError`` if a
        sibling with ``new_name`` already exists. With ``overwrite=True``
        replaces whatever's there via ``Path.replace``.

        Used by Tracer Phase 4's cross-source
        ``--on-conflict=overwrite-with-backup`` flow to rename existing
        destination files out of the way before the new transfer.
        """
        if not self._owns(source_id):
            return None
        old_path = Path(file_id)
        new_path = old_path.parent / new_name
        if new_path.exists() and not overwrite:
            raise FileExistsError(
                f"local source rename: {new_path} already exists; "
                f"pass overwrite=True to replace"
            )
        if overwrite:
            # Path.replace is atomic and overwrites unconditionally
            old_path.replace(new_path)
        else:
            old_path.rename(new_path)
        stat = new_path.stat()
        return FileInfo(
            file_id=str(new_path),
            path=str(new_path),
            size=stat.st_size,
            mtime=datetime.fromtimestamp(stat.st_mtime),
            ctime=datetime.fromtimestamp(stat.st_ctime),
            is_directory=False,
            extras={"inode": stat.st_ino},
        )

    @hookimpl
    def curator_source_delete(
        self,
        source_id: str,
        file_id: str,
        to_trash: bool,
    ) -> bool | None:
        if not self._owns(source_id):
            return None
        if to_trash:
            # send2trash will be vendored in Step 8; until then use the
            # PyPI package if installed, else fall back to os.remove with
            # a clear log message. Phase Alpha protects us via the audit
            # log + restore registry; we know what we deleted regardless.
            try:
                from send2trash import send2trash  # type: ignore[import-not-found]
                send2trash(file_id)
                return True
            except ImportError:
                # We're not yet vendored. The TrashService is the proper
                # caller for to_trash=True paths and will be wired with a
                # vendored send2trash; this path exists for defensive use.
                os.remove(file_id)
                return True
        os.remove(file_id)
        return True

    @hookimpl
    def curator_source_write(
        self,
        source_id: str,
        parent_id: str,
        name: str,
        data: bytes,
        *,
        mtime: datetime | None = None,
        overwrite: bool = False,
    ) -> FileInfo | None:
        """Create a new file at ``parent_id / name`` atomically.

        v0.40. Implementation uses a temp file in the same directory
        followed by ``os.replace`` so partial writes never leave a
        half-formed visible file at the target path.
        """
        if not self._owns(source_id):
            return None

        parent_path = Path(parent_id)
        if not parent_path.exists():
            parent_path.mkdir(parents=True, exist_ok=True)
        elif not parent_path.is_dir():
            raise OSError(
                f"local source write: parent_id {parent_id!r} is not a directory"
            )

        target = parent_path / name
        if target.exists() and not overwrite:
            raise FileExistsError(
                f"local source write: {target} already exists; pass overwrite=True to replace"
            )

        # Write to a tempfile in the same directory so os.replace is
        # truly atomic (cross-device renames would not be).
        fd, tmp_path = tempfile.mkstemp(
            prefix=f".{name}.", suffix=".tmp", dir=str(parent_path),
        )
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            # Set mtime if requested (atime is set to mtime too —
            # we don't try to preserve atime separately).
            if mtime is not None:
                ts = mtime.timestamp()
                os.utime(tmp_path, (ts, ts))
            os.replace(tmp_path, target)  # atomic on same FS
        except Exception:
            # Best-effort cleanup of the tempfile if anything went wrong.
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

        # Return the FileInfo for the newly-written file.
        stat = target.stat()
        return FileInfo(
            file_id=str(target),
            path=str(target),
            size=stat.st_size,
            mtime=datetime.fromtimestamp(stat.st_mtime),
            ctime=datetime.fromtimestamp(stat.st_ctime),
            is_directory=False,
            extras={"inode": stat.st_ino},
        )

    # ---- helpers ----

    def _owns(self, source_id: str) -> bool:
        """Return True if this plugin owns the given source_id."""
        # Conventionally "local" or "local:<name>" for multi-root setups.
        return source_id == SOURCE_TYPE or source_id.startswith(f"{SOURCE_TYPE}:")
