"""Windows entry point — vendored from send2trash/win/__init__.py (BSD-3-Clause).

Curator-local modifications: skips the ``modern`` (pywin32-based) path and
goes straight to the ctypes-based ``legacy`` implementation. This removes
the optional pywin32 dependency.
"""

from __future__ import annotations

# We always use the legacy ctypes path. The original send2trash tries
# pywin32-based "modern" first; we don't ship that to keep deps minimal.
from curator._vendored.send2trash.win.legacy import send2trash  # noqa: F401
