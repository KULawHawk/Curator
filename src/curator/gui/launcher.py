"""Entry point that boots the QApplication and shows the main window.

This module is the seam between the CLI (which has a ``CuratorRuntime``
fully wired up) and the Qt event loop. It's intentionally thin so that
unit tests can construct a :class:`CuratorMainWindow` directly with a
test runtime and never touch :func:`run_gui`.

Usage from CLI::

    from curator.gui.launcher import run_gui
    exit_code = run_gui(runtime)
    raise typer.Exit(code=exit_code)
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from curator.cli.runtime import CuratorRuntime


def run_gui(runtime: "CuratorRuntime") -> int:
    """Boot Qt, show the main window, and run the event loop.

    Returns the QApplication exit code (0 on clean close).

    Raises:
        ImportError: PySide6 isn't installed. Caller (CLI) should
            print an actionable install hint.
    """
    # Imported here so that `import curator.gui.launcher` in a headless
    # environment doesn't crash before the caller had a chance to print
    # a friendly message.
    from PySide6.QtWidgets import QApplication

    from curator.gui.main_window import CuratorMainWindow

    app = QApplication.instance() or QApplication(sys.argv)
    window = CuratorMainWindow(runtime)
    window.show()
    return app.exec()


def is_pyside6_available() -> bool:
    """Cheap probe: True if PySide6 is importable.

    Used by the CLI ``gui`` command to print an actionable message when
    the [gui] extra hasn't been installed, rather than raising a raw
    ImportError on the user.
    """
    try:
        import PySide6  # noqa: F401
    except ImportError:
        return False
    return True


__all__ = ["run_gui", "is_pyside6_available"]
