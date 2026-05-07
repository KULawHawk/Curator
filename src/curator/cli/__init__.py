"""CLI subpackage.

The Typer app and all commands live in :mod:`curator.cli.main`. The
runtime container that wires up DB + plugin manager + repositories +
services lives in :mod:`curator.cli.runtime`.

External entry point is set in pyproject.toml as::

    [project.scripts]
    curator = "curator.cli.main:app"

NOTE: This module deliberately does NOT eagerly import ``main`` at
package import time. Doing so triggers a Python ``RuntimeWarning``
when users invoke ``python -m curator.cli.main`` (the package is
imported, then the same module is re-loaded as ``__main__``). Import
``curator.cli.main:app`` directly when you need the entry point.
"""

__all__: list[str] = []
