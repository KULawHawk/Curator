"""send2trash — vendored cross-platform subset.

Original (BSD-3-Clause) Copyright 2013 Hardcoded Software, 2017+ Virgil Dupras.
Upstream: https://github.com/arsenetar/send2trash

Curator-internal vendor with platform-specific backends:

  * Windows  → ``win`` submodule (ctypes-based ``SHFileOperationW``)
  * macOS    → ``mac`` submodule (AppleScript via ``osascript``)
  * Linux/BSD → ``plat_freedesktop`` module (XDG Trash spec home trash)
  * Other    → import-time stub raising :class:`RuntimeError`

Curator-local modifications versus upstream:

  1. **Imports**: ``from send2trash.X`` → ``from curator._vendored.send2trash.X``.
  2. **Modern Windows path skipped**: only the ctypes-based ``legacy``
     Windows path is included (no optional pywin32 dependency).
  3. **mac backend uses AppleScript**: avoids pulling pyobjc as a
     dependency. Same Finder behavior, slightly slower per-file.
  4. **Linux scope**: home trash only. Cross-filesystem (top-dir trash
     under ``<mount>/.Trash-<uid>``) is deferred to Phase Gamma.

Public API: ``send2trash(paths)`` — matches upstream signature.
"""

from __future__ import annotations

import sys

from curator._vendored.send2trash.exceptions import TrashPermissionError  # noqa: F401


if sys.platform == "win32":
    from curator._vendored.send2trash.win import send2trash  # noqa: F401
elif sys.platform == "darwin":
    from curator._vendored.send2trash.mac import send2trash  # noqa: F401
elif sys.platform.startswith(("linux", "freebsd", "openbsd", "netbsd", "dragonfly")):
    from curator._vendored.send2trash.plat_freedesktop import send2trash  # noqa: F401
else:
    def send2trash(paths):  # type: ignore[no-redef]
        """Stub for unsupported platforms."""
        raise RuntimeError(
            f"Curator's vendored send2trash doesn't support sys.platform={sys.platform!r}. "
            "Install the PyPI 'send2trash' package, or file an issue if you need "
            "this platform supported."
        )


__all__ = ["send2trash", "TrashPermissionError"]
