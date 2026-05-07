"""Plugin framework for Curator.

DESIGN.md §5.

Curator's plugin system is built on `pluggy` (D11). External plugins live
under the ``curatorplug.*`` namespace and register via setuptools entry
points; built-in plugins under ``curator.plugins.core`` are registered
directly by :func:`curator.plugins.core.register_core_plugins`.

Public API:
    * :func:`get_plugin_manager` — the lazy-initialized singleton.
    * :func:`reset_plugin_manager` — for tests; recreates the singleton.
    * :func:`hookimpl` — decorator for plugin implementations (re-export).

Hook contracts live in :mod:`curator.plugins.hookspecs`.
"""

from curator.plugins.hookspecs import hookimpl, hookspec
from curator.plugins.manager import get_plugin_manager, reset_plugin_manager

__all__ = [
    "get_plugin_manager",
    "reset_plugin_manager",
    "hookimpl",
    "hookspec",
]
