"""Vendored from send2trash/exceptions.py (BSD-3-Clause).

Curator-local modifications: none (the original is platform-independent).
"""

from __future__ import annotations

import errno


class TrashPermissionError(PermissionError):
    """A permission error specific to a trash directory.

    Raising this error indicates that permissions prevent us from
    efficiently trashing a file, although we might still have permission
    to delete it. This is *not* used when permissions prevent removing
    the file itself: that will be raised as a regular ``PermissionError``
    (``OSError`` on Python 2; we no longer support Python 2).

    Application code that catches this may try to simply delete the file,
    or prompt the user to decide, or (on freedesktop platforms) move it
    to 'home trash' as a fallback. That last option probably involves
    copying the data between partitions, devices, or network drives, so
    we don't do it as a fallback.
    """

    def __init__(self, filename):
        super().__init__(errno.EACCES, "Permission denied", filename)
