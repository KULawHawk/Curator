"""Plugin manager singleton.

DESIGN.md §5.2.

Curator uses one ``pluggy.PluginManager`` per process. The first call to
:func:`get_plugin_manager` lazily creates it, registers all built-in
core plugins, and discovers external plugins via setuptools entry points
under the ``curator`` group.

Tests can call :func:`reset_plugin_manager` to get a fresh manager (e.g.
for plugin-isolation testing).
"""

from __future__ import annotations

import threading
from typing import Optional

import pluggy
from loguru import logger

from curator.plugins import hookspecs

_pm: Optional[pluggy.PluginManager] = None
_pm_lock = threading.Lock()


def get_plugin_manager() -> pluggy.PluginManager:
    """Return the lazy-initialized singleton plugin manager.

    Thread-safe. The first caller initializes; subsequent callers get
    the same instance.
    """
    global _pm
    if _pm is None:
        with _pm_lock:
            if _pm is None:  # double-checked locking
                _pm = _create_plugin_manager()
    return _pm


def reset_plugin_manager() -> None:
    """Clear the singleton so the next get returns a fresh manager.

    For tests only. Production code should never call this.
    """
    global _pm
    with _pm_lock:
        _pm = None


def _create_plugin_manager() -> pluggy.PluginManager:
    """Build a fresh plugin manager: hookspecs + core plugins + entry points."""
    pm = pluggy.PluginManager("curator")
    pm.add_hookspecs(hookspecs)

    # Built-in plugins (always registered).
    from curator.plugins.core import register_core_plugins

    register_core_plugins(pm)

    # External plugins via setuptools entry points.
    # Plugins declare in their pyproject.toml:
    #   [project.entry-points.curator]
    #   my_plugin = "curatorplug.my_plugin:Plugin"
    try:
        loaded = pm.load_setuptools_entrypoints("curator")
        if loaded:
            logger.debug(
                "Loaded {n} external Curator plugin(s) via entry points",
                n=loaded,
            )
    except Exception as e:  # pragma: no cover — defensive
        # Entry-point discovery failures should never crash startup;
        # log and continue with core plugins only.
        logger.warning("Entry-point plugin discovery failed: {err}", err=e)

    return pm
