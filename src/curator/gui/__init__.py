"""Curator GUI (Phase Beta gate 4, v0.34).

Native PySide6 / Qt desktop UI. Read-only first ship — three views over
the existing Curator runtime:

  * **Browser** — every indexed file in a sortable table.
  * **Bundles** — every bundle with its membership count.
  * **Trash** — every trashed file with its trash record metadata.

The GUI shares the exact same :class:`curator.cli.runtime.CuratorRuntime`
the CLI uses; everything below the runtime (DB, repos, services) is
unchanged. Phase γ may add a service-mode dispatch (talk to a remote
Curator API) but that's a separate concern from this layer.

The GUI launcher entrypoint is :func:`curator.gui.launcher.run_gui`.
The CLI exposes it as ``curator gui``.

Mutations (trashing files from Browser, dissolving bundles, restoring
from trash) are deferred to v0.35 — this ship is intentionally
read-only so the visual layer can ship + soak before destructive
operations are wired through the GUI's HITL escalation paths.
"""

from __future__ import annotations

# We avoid importing PySide6 at module load: importing curator.gui in a
# headless environment (CI without Qt platform plugins) would crash
# even when no Qt code is exercised. Sub-modules import what they need
# at call time.

__all__ = ["__module_purpose__"]

__module_purpose__ = (
    "Curator GUI package. Import curator.gui.launcher to run."
)
