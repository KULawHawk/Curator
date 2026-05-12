"""Focused unit tests for FileQuery (storage/queries.py).

Covers every conditional branch in build_where() and build_sql() to satisfy
the v1.7.84 doctrine standard (100% line + branch on Windows scope, or
documented pragma).

The module is pure SQL+params construction with no I/O — no stubs required.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from curator.storage.queries import FileQuery


# ===========================================================================
# build_where() — individual filter clauses
# ===========================================================================


class TestBuildWhereEmpty:
    def test_empty_query_returns_one_and_empty_params(self):
        q = FileQuery(deleted=None)  # disable default deleted filter too
        where, params = q.build_where()
        assert where == "1"
        assert params == []

    def test_default_query_has_deleted_filter(self):
        # FileQuery() defaults to deleted=False (only active).
        q = FileQuery()
        where, params = q.build_where()
        assert "deleted_at IS NULL" in where
        assert params == []


class TestBuildWhereSourceIds:
    def test_single_source_id(self):
        q = FileQuery(source_ids=["local"], deleted=None)
        where, params = q.build_where()
        assert "source_id IN (?)" in where
        assert params == ["local"]

    def test_multiple_source_ids(self):
        q = FileQuery(source_ids=["local", "gdrive", "onedrive"], deleted=None)
        where, params = q.build_where()
        assert "source_id IN (?,?,?)" in where
        assert params == ["local", "gdrive", "onedrive"]

    def test_empty_source_ids_list_is_noop(self):
        # Empty list is falsy — no filter applied.
        q = FileQuery(source_ids=[], deleted=None)
        where, params = q.build_where()
        assert "source_id" not in where


class TestBuildWhereExtensions:
    def test_single_extension(self):
        q = FileQuery(extensions=[".pdf"], deleted=None)
        where, params = q.build_where()
        assert "extension IN (?)" in where
        assert params == [".pdf"]

    def test_multiple_extensions(self):
        q = FileQuery(extensions=[".pdf", ".docx"], deleted=None)
        where, params = q.build_where()
        assert "extension IN (?,?)" in where
        assert params == [".pdf", ".docx"]


class TestBuildWhereFileTypes:
    def test_single_file_type(self):
        q = FileQuery(file_types=["document"], deleted=None)
        where, params = q.build_where()
        assert "file_type IN (?)" in where
        assert params == ["document"]

    def test_multiple_file_types(self):
        q = FileQuery(file_types=["document", "image"], deleted=None)
        where, params = q.build_where()
        assert "file_type IN (?,?)" in where
        assert params == ["document", "image"]


class TestBuildWherePathPrefix:
    def test_plain_prefix(self):
        q = FileQuery(source_path_starts_with="/data/proj", deleted=None)
        where, params = q.build_where()
        assert "source_path LIKE ? ESCAPE '\\'" in where
        # No special chars to escape; just appended '%'
        assert params == ["/data/proj%"]

    def test_prefix_with_backslash_is_escaped(self):
        # Backslash must be escaped first (it's the ESCAPE char).
        q = FileQuery(source_path_starts_with="C:\\data", deleted=None)
        where, params = q.build_where()
        assert params == ["C:\\\\data%"]

    def test_prefix_with_percent_is_escaped(self):
        q = FileQuery(source_path_starts_with="100%dir", deleted=None)
        where, params = q.build_where()
        # '%' becomes '\\%'
        assert params == ["100\\%dir%"]

    def test_prefix_with_underscore_is_escaped(self):
        q = FileQuery(source_path_starts_with="my_dir", deleted=None)
        where, params = q.build_where()
        assert params == ["my\\_dir%"]


class TestBuildWhereSize:
    def test_min_size_only(self):
        q = FileQuery(min_size=1024, deleted=None)
        where, params = q.build_where()
        assert "size >= ?" in where
        assert "size <= ?" not in where
        assert params == [1024]

    def test_max_size_only(self):
        q = FileQuery(max_size=2048, deleted=None)
        where, params = q.build_where()
        assert "size <= ?" in where
        assert "size >= ?" not in where
        assert params == [2048]

    def test_both_min_and_max(self):
        q = FileQuery(min_size=100, max_size=999, deleted=None)
        where, params = q.build_where()
        assert "size >= ?" in where
        assert "size <= ?" in where
        assert params == [100, 999]

    def test_min_size_zero_is_applied(self):
        # 0 is not None, so the filter IS applied (a subtle but important
        # distinction: min_size=0 is different from min_size=None).
        q = FileQuery(min_size=0, deleted=None)
        where, params = q.build_where()
        assert "size >= ?" in where
        assert params == [0]


class TestBuildWhereHashPresence:
    def test_has_xxhash(self):
        q = FileQuery(has_xxhash=True, deleted=None)
        where, _ = q.build_where()
        assert "xxhash3_128 IS NOT NULL" in where

    def test_has_md5(self):
        q = FileQuery(has_md5=True, deleted=None)
        where, _ = q.build_where()
        assert "md5 IS NOT NULL" in where

    def test_has_fuzzy_hash(self):
        q = FileQuery(has_fuzzy_hash=True, deleted=None)
        where, _ = q.build_where()
        assert "fuzzy_hash IS NOT NULL" in where

    def test_all_hash_presence_flags_false_is_noop(self):
        q = FileQuery(deleted=None)  # all has_* default to False
        where, _ = q.build_where()
        assert "IS NOT NULL" not in where


class TestBuildWhereHashEquality:
    def test_xxhash_equality(self):
        q = FileQuery(xxhash3_128="abc123", deleted=None)
        where, params = q.build_where()
        assert "xxhash3_128 = ?" in where
        assert params == ["abc123"]

    def test_md5_equality(self):
        q = FileQuery(md5="d41d8cd98f00b204e9800998ecf8427e", deleted=None)
        where, params = q.build_where()
        assert "md5 = ?" in where
        assert params == ["d41d8cd98f00b204e9800998ecf8427e"]

    def test_fuzzy_hash_equality(self):
        q = FileQuery(fuzzy_hash="3:abc:def", deleted=None)
        where, params = q.build_where()
        assert "fuzzy_hash = ?" in where
        assert params == ["3:abc:def"]


class TestBuildWhereTimeRanges:
    def test_seen_after_only(self):
        dt = datetime(2026, 1, 1)
        q = FileQuery(seen_after=dt, deleted=None)
        where, params = q.build_where()
        assert "seen_at >= ?" in where
        assert params == [dt]

    def test_seen_before_only(self):
        dt = datetime(2026, 5, 1)
        q = FileQuery(seen_before=dt, deleted=None)
        where, params = q.build_where()
        assert "seen_at < ?" in where
        assert params == [dt]

    def test_seen_range(self):
        dt_a = datetime(2026, 1, 1)
        dt_b = datetime(2026, 5, 1)
        q = FileQuery(seen_after=dt_a, seen_before=dt_b, deleted=None)
        where, params = q.build_where()
        assert "seen_at >= ?" in where
        assert "seen_at < ?" in where
        assert params == [dt_a, dt_b]

    def test_mtime_after_only(self):
        dt = datetime(2026, 1, 1)
        q = FileQuery(mtime_after=dt, deleted=None)
        where, params = q.build_where()
        assert "mtime >= ?" in where
        assert params == [dt]

    def test_mtime_before_only(self):
        dt = datetime(2026, 5, 1)
        q = FileQuery(mtime_before=dt, deleted=None)
        where, params = q.build_where()
        assert "mtime < ?" in where
        assert params == [dt]


class TestBuildWhereDeleted:
    def test_deleted_true_filters_to_trashed(self):
        q = FileQuery(deleted=True)
        where, params = q.build_where()
        assert "deleted_at IS NOT NULL" in where
        assert params == []

    def test_deleted_false_filters_to_active(self):
        q = FileQuery(deleted=False)
        where, params = q.build_where()
        assert "deleted_at IS NULL" in where
        assert params == []

    def test_deleted_none_applies_no_filter(self):
        q = FileQuery(deleted=None)
        where, params = q.build_where()
        assert "deleted_at" not in where


# ===========================================================================
# Combined conditions
# ===========================================================================


class TestBuildWhereCombined:
    def test_multiple_conditions_joined_by_and(self):
        q = FileQuery(
            source_ids=["local"],
            min_size=100,
            has_xxhash=True,
            deleted=False,
        )
        where, params = q.build_where()
        # Each clause appears, joined by AND
        assert "source_id IN (?)" in where
        assert "size >= ?" in where
        assert "xxhash3_128 IS NOT NULL" in where
        assert "deleted_at IS NULL" in where
        assert " AND " in where
        # Parameter order matches clause order
        assert params == ["local", 100]

    def test_complex_real_world_query(self):
        # Find recent active local PDFs over 1KB with fuzzy hashes set.
        dt = datetime(2026, 5, 1)
        q = FileQuery(
            source_ids=["local"],
            extensions=[".pdf"],
            min_size=1024,
            has_fuzzy_hash=True,
            seen_after=dt,
            deleted=False,
        )
        where, params = q.build_where()
        assert "source_id IN (?)" in where
        assert "extension IN (?)" in where
        assert "size >= ?" in where
        assert "fuzzy_hash IS NOT NULL" in where
        assert "seen_at >= ?" in where
        assert "deleted_at IS NULL" in where
        assert params == ["local", ".pdf", 1024, dt]


# ===========================================================================
# build_sql() — full SQL construction
# ===========================================================================


class TestBuildSqlBase:
    def test_default_base_with_empty_query(self):
        q = FileQuery(deleted=None, order_by="")
        sql, params = q.build_sql()
        assert sql == "SELECT * FROM files WHERE 1"
        assert params == []

    def test_default_base_with_default_query_has_deleted_filter(self):
        q = FileQuery(order_by="")
        sql, params = q.build_sql()
        assert sql == "SELECT * FROM files WHERE deleted_at IS NULL"
        assert params == []

    def test_custom_base(self):
        q = FileQuery(deleted=None, order_by="")
        sql, _ = q.build_sql(base="SELECT id, source_path FROM files")
        assert sql == "SELECT id, source_path FROM files WHERE 1"


class TestBuildSqlOrderBy:
    def test_default_order_by_seen_at_desc(self):
        q = FileQuery(deleted=None)
        sql, _ = q.build_sql()
        assert sql.endswith(" ORDER BY seen_at DESC")

    def test_custom_order_by(self):
        q = FileQuery(deleted=None, order_by="size ASC")
        sql, _ = q.build_sql()
        assert " ORDER BY size ASC" in sql

    def test_empty_order_by_omits_clause(self):
        q = FileQuery(deleted=None, order_by="")
        sql, _ = q.build_sql()
        assert "ORDER BY" not in sql

    def test_none_order_by_omits_clause(self):
        # `if self.order_by:` is falsy for None too.
        q = FileQuery(deleted=None, order_by=None)  # type: ignore[arg-type]
        sql, _ = q.build_sql()
        assert "ORDER BY" not in sql


class TestBuildSqlLimitOffset:
    def test_no_limit_no_offset(self):
        q = FileQuery(deleted=None, order_by="")
        sql, params = q.build_sql()
        assert "LIMIT" not in sql
        assert "OFFSET" not in sql
        assert params == []

    def test_limit_without_offset(self):
        q = FileQuery(deleted=None, order_by="", limit=50)
        sql, params = q.build_sql()
        assert sql.endswith(" LIMIT ?")
        assert "OFFSET" not in sql
        assert params == [50]

    def test_limit_with_zero_offset_does_not_emit_offset(self):
        # offset=0 is the default and falsy, so OFFSET is NOT appended
        # even though limit is set. Subtle but important branch.
        q = FileQuery(deleted=None, order_by="", limit=25, offset=0)
        sql, params = q.build_sql()
        assert "LIMIT ?" in sql
        assert "OFFSET" not in sql
        assert params == [25]

    def test_limit_with_nonzero_offset_emits_both(self):
        q = FileQuery(deleted=None, order_by="", limit=10, offset=20)
        sql, params = q.build_sql()
        assert "LIMIT ?" in sql
        assert "OFFSET ?" in sql
        # Order: LIMIT param first, then OFFSET param
        assert params == [10, 20]

    def test_offset_without_limit_is_ignored(self):
        # The code only appends OFFSET when LIMIT is set. So offset=10 alone
        # produces neither LIMIT nor OFFSET.
        q = FileQuery(deleted=None, order_by="", offset=10)
        sql, params = q.build_sql()
        assert "LIMIT" not in sql
        assert "OFFSET" not in sql
        assert params == []


class TestBuildSqlFullPath:
    def test_realistic_complete_query(self):
        # Combines everything: filters, custom base, order_by, limit + offset.
        q = FileQuery(
            source_ids=["local"],
            min_size=100,
            order_by="size DESC",
            limit=20,
            offset=40,
            deleted=False,
        )
        sql, params = q.build_sql(base="SELECT curator_id FROM files")
        assert sql.startswith("SELECT curator_id FROM files WHERE")
        assert "source_id IN (?)" in sql
        assert "size >= ?" in sql
        assert "deleted_at IS NULL" in sql
        assert " ORDER BY size DESC" in sql
        assert sql.endswith(" OFFSET ?")
        # Param order: WHERE-clause params first, then LIMIT, then OFFSET
        assert params == ["local", 100, 20, 40]
