"""Built-in (core) plugins shipped with Curator.

These plugins are loaded automatically by the plugin manager and provide
the baseline functionality required for Phase Alpha to work end-to-end.
"""

from __future__ import annotations

import pluggy
from loguru import logger


def register_core_plugins(pm: pluggy.PluginManager) -> None:
    """Register all built-in plugins on the given plugin manager.

    Called once during plugin-manager initialization. Each plugin module
    exposes a ``Plugin`` class instance whose ``@hookimpl`` methods
    implement the relevant hookspecs.

    Order: registered top-to-bottom. Pluggy doesn't guarantee call order
    across plugins for the same hook unless ``tryfirst`` / ``trylast``
    is used; for our hooks the order doesn't matter (results are
    aggregated).
    """
    from curator.plugins.core import (
        classify_filetype,
        gdrive_source,
        lineage_filename,
        lineage_fuzzy_dup,
        lineage_hash_dup,
        local_source,
    )

    plugins: list[tuple[str, type]] = [
        ("local_source", local_source.Plugin),
        ("gdrive_source", gdrive_source.Plugin),
        ("classify_filetype", classify_filetype.Plugin),
        ("lineage_hash_dup", lineage_hash_dup.Plugin),
        ("lineage_filename", lineage_filename.Plugin),
        ("lineage_fuzzy_dup", lineage_fuzzy_dup.Plugin),
    ]

    for name, plugin_cls in plugins:
        try:
            pm.register(plugin_cls(), name=f"curator.core.{name}")
            logger.debug("Registered core plugin: curator.core.{name}", name=name)
        except Exception as e:  # pragma: no cover — defensive
            # A malformed core plugin must not crash the whole app — log
            # and continue. Phase Alpha is forgiving here; Phase Beta
            # will likely tighten this to a hard fail in dev mode.
            logger.error(
                "Failed to register core plugin {name}: {err}",
                name=name, err=e,
            )
