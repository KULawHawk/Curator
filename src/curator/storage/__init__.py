"""SQLite storage layer for Curator.

DESIGN.md §4.

Phase Alpha uses stdlib ``sqlite3`` with handwritten queries (D17). This
package provides the connection manager, migration runner, query helpers,
and a Repository per entity type.

External users typically interact with this layer via :class:`CuratorDB`
and the repositories under ``curator.storage.repositories``.
"""

from curator.storage.connection import CuratorDB
from curator.storage.migrations import MIGRATIONS, applied_migrations, apply_migrations
from curator.storage.queries import FileQuery

__all__ = [
    "CuratorDB",
    "FileQuery",
    "MIGRATIONS",
    "apply_migrations",
    "applied_migrations",
]
