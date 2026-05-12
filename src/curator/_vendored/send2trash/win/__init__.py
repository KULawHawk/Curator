"""Windows entry point — vendored from send2trash/win/__init__.py (BSD-3-Clause).

Curator-local modifications: skips the ``modern`` (pywin32-based) path and
goes straight to the ctypes-based ``legacy`` implementation. This removes
the optional pywin32 dependency.

v1.7.59: the legacy import is now gated on ``sys.platform == 'win32'``
so that sibling modules in this package (notably ``recycle_bin``, which
is a pure-Python ``$I`` file parser with no Windows API calls) can be
imported on Linux/macOS for testing without triggering the
``ctypes.wintypes`` import chain in ``legacy.py``. Before this gate,
importing ``from curator._vendored.send2trash.win.recycle_bin import ...``
on POSIX raised ``ImportError: ctypes.wintypes`` at package init time,
which broke CI test collection on Ubuntu and macOS runners.
"""

from __future__ import annotations

import sys

# We always use the legacy ctypes path. The original send2trash tries
# pywin32-based "modern" first; we don't ship that to keep deps minimal.
# Gate on Windows: on POSIX, importing ctypes.wintypes from legacy.py
# raises ImportError, which breaks unrelated imports from this package
# (e.g. the cross-platform recycle_bin parser used in tests).
if sys.platform == "win32":
    from curator._vendored.send2trash.win.legacy import send2trash  # noqa: F401
