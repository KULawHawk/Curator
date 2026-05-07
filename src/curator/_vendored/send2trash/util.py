"""Vendored from send2trash/util.py (BSD-3-Clause).

Curator-local modifications: none.
"""

from __future__ import annotations

import collections.abc
import os


def preprocess_paths(paths):
    """Normalize a path-or-iterable into a list of fspath strings."""
    if isinstance(paths, collections.abc.Iterable) and not isinstance(
        paths, (str, bytes)
    ):
        paths = list(paths)
    else:
        paths = [paths]
    # Convert items such as pathlib paths to strings.
    return [os.fspath(path) for path in paths]
