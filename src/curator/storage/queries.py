"""Composable query objects.

DESIGN.md §4.6.

Repositories expose narrow, named query methods (``find_by_hash``,
``find_by_path``, etc.) for common cases. For broader / dynamic queries,
build a :class:`FileQuery` and pass it to ``FileRepository.query``.

The query object generates ``(sql, params)`` for execution; the repository
adds ``SELECT * FROM files`` (or its equivalent) and runs it. Flex-attr
filtering is intentionally NOT in the SQL — it's applied in Python after
fetch — because flex attrs live in a join table and the filter shape
varies. For Phase Alpha this is fine; if it becomes a hot path we can
push it into SQL with EXISTS subqueries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class FileQuery:
    """Composable query for the ``files`` table.

    All conditions are AND-combined. To express OR, run two separate queries
    and union the results in Python. Empty/None fields are no-ops.
    """

    # Equality / set membership
    source_ids: list[str] | None = None
    extensions: list[str] | None = None
    file_types: list[str] | None = None

    # Path matching
    source_path_starts_with: str | None = None

    # Numeric ranges
    min_size: int | None = None
    max_size: int | None = None

    # Hash presence
    has_xxhash: bool = False
    has_md5: bool = False
    has_fuzzy_hash: bool = False

    # Specific hash matching (for finding a file by content)
    xxhash3_128: str | None = None
    md5: str | None = None
    fuzzy_hash: str | None = None

    # Time ranges
    seen_after: datetime | None = None
    seen_before: datetime | None = None
    mtime_after: datetime | None = None
    mtime_before: datetime | None = None

    # Soft-delete filter: None = any, True = only trashed, False = only active
    deleted: bool | None = False

    # Flex attribute filter — applied in Python post-fetch
    flex_attrs: dict[str, Any] = field(default_factory=dict)

    # Output controls
    order_by: str = "seen_at DESC"
    limit: int | None = None
    offset: int = 0

    # ------------------------------------------------------------------
    def build_where(self) -> tuple[str, list[Any]]:
        """Return the WHERE clause body and the parameter list.

        The returned WHERE body does NOT include the leading ``WHERE``;
        if there are no conditions it returns ``("1", [])``.
        """
        clauses: list[str] = []
        params: list[Any] = []

        if self.source_ids:
            clauses.append(f"source_id IN ({','.join('?' * len(self.source_ids))})")
            params.extend(self.source_ids)

        if self.extensions:
            clauses.append(f"extension IN ({','.join('?' * len(self.extensions))})")
            params.extend(self.extensions)

        if self.file_types:
            clauses.append(f"file_type IN ({','.join('?' * len(self.file_types))})")
            params.extend(self.file_types)

        if self.source_path_starts_with:
            clauses.append("source_path LIKE ? ESCAPE '\\'")
            # Escape SQL LIKE wildcards in the prefix.
            prefix = self.source_path_starts_with.replace("\\", "\\\\")
            prefix = prefix.replace("%", r"\%").replace("_", r"\_")
            params.append(prefix + "%")

        if self.min_size is not None:
            clauses.append("size >= ?")
            params.append(self.min_size)
        if self.max_size is not None:
            clauses.append("size <= ?")
            params.append(self.max_size)

        if self.has_xxhash:
            clauses.append("xxhash3_128 IS NOT NULL")
        if self.has_md5:
            clauses.append("md5 IS NOT NULL")
        if self.has_fuzzy_hash:
            clauses.append("fuzzy_hash IS NOT NULL")

        if self.xxhash3_128 is not None:
            clauses.append("xxhash3_128 = ?")
            params.append(self.xxhash3_128)
        if self.md5 is not None:
            clauses.append("md5 = ?")
            params.append(self.md5)
        if self.fuzzy_hash is not None:
            clauses.append("fuzzy_hash = ?")
            params.append(self.fuzzy_hash)

        if self.seen_after is not None:
            clauses.append("seen_at >= ?")
            params.append(self.seen_after)
        if self.seen_before is not None:
            clauses.append("seen_at < ?")
            params.append(self.seen_before)
        if self.mtime_after is not None:
            clauses.append("mtime >= ?")
            params.append(self.mtime_after)
        if self.mtime_before is not None:
            clauses.append("mtime < ?")
            params.append(self.mtime_before)

        if self.deleted is True:
            clauses.append("deleted_at IS NOT NULL")
        elif self.deleted is False:
            clauses.append("deleted_at IS NULL")
        # if self.deleted is None: no filter

        where = " AND ".join(clauses) if clauses else "1"
        return where, params

    def build_sql(self, *, base: str = "SELECT * FROM files") -> tuple[str, list[Any]]:
        """Return the full SQL and parameter list."""
        where, params = self.build_where()
        sql = f"{base} WHERE {where}"
        if self.order_by:
            sql += f" ORDER BY {self.order_by}"
        if self.limit is not None:
            sql += " LIMIT ?"
            params.append(self.limit)
            if self.offset:
                sql += " OFFSET ?"
                params.append(self.offset)
        return sql, params
