"""macOS send2trash backend (Phase Beta gate #2).

Originally based on send2trash (BSD-3-Clause). Curator-local approach:
delegate to AppleScript via ``osascript`` so we don't pull pyobjc as a
dependency. This works on every modern macOS (10.10+).

The AppleScript path is slightly slower than the Foundation API
(``NSFileManager.trashItemAtURL:resultingItemURL:error:``) because of
process startup, but it has zero install footprint and matches Finder
behavior exactly \u2014 including routing files on external volumes to that
volume's ``.Trashes`` folder, which is the right thing to do.

Not yet implemented:
  * Reading back the resulting trash location (mac equivalent of the
    Windows ``recycle_bin.py`` reader). Mac stores the original path in
    extended attributes (``com.apple.metadata:kMDItemPhysicalPath``)
    and an ``com.apple.metadata:kMDItemFinderComment`` for "Put Back".
    Phase Beta+ may add a reader if Curator's restore needs it.
"""

from __future__ import annotations

import os
import subprocess
import sys

from curator._vendored.send2trash.exceptions import TrashPermissionError
from curator._vendored.send2trash.util import preprocess_paths


def send2trash(paths):
    """Send one or more paths to the macOS Trash via AppleScript.

    Args:
        paths: a single path (str/bytes/PathLike) or an iterable of paths.

    Raises:
        FileNotFoundError: a path doesn't exist.
        TrashPermissionError: macOS refused to move the file.
        OSError: ``osascript`` itself failed unexpectedly.
        RuntimeError: not running on macOS.
    """
    if sys.platform != "darwin":
        raise RuntimeError(
            f"send2trash.mac is for macOS; current platform is {sys.platform}"
        )

    paths = preprocess_paths(paths)

    # Existence check up front so we get a clean FileNotFoundError
    # rather than an opaque AppleScript failure.
    for p in paths:
        if not os.path.exists(p):
            raise FileNotFoundError(p)

    # Build the AppleScript: "tell application Finder to delete (POSIX file "/abs/path")"
    # We emit one delete call per file rather than batching, so that an
    # error on one file doesn't poison the whole batch.
    for p in paths:
        # AppleScript string escape: " \"  \\
        escaped = p.replace("\\", "\\\\").replace('"', '\\"')
        script = (
            'tell application "Finder" to delete '
            f'(POSIX file "{escaped}")'
        )
        try:
            subprocess.run(
                ["osascript", "-e", script],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as e:
            stderr = (e.stderr or "").lower()
            if "not allowed" in stderr or "permission" in stderr:
                raise TrashPermissionError(p) from e
            raise OSError(
                f"osascript failed for {p}: {e.stderr.strip() if e.stderr else e}"
            ) from e
        except FileNotFoundError as e:
            # ``osascript`` itself missing (extremely unusual on macOS).
            raise RuntimeError(
                "osascript not found on PATH; cannot trash files via AppleScript"
            ) from e
