"""Coverage closure for ``curator.storage.repositories.file_repo`` (v1.7.139).

The biggest single ship in Round 2 (96 lines uncovered). Targets all
the unread methods: upsert(update branch), mark_deleted, delete (hard),
find_by_md5, find_by_fuzzy_hash, find_candidates_by_size, query (flex
filter), iter_all, count(source_id), update_status, count_by_status,
query_by_status, find_expiring_before.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

import pytest

from curator._compat.datetime import utcnow_naive
from curator.models import FileEntity
from curator.storage.queries import FileQuery


def _mk_file(repos, path: str, **overrides) -> FileEntity:
    base = dict(
        source_id="local", source_path=path,
        size=10, mtime=utcnow_naive(),
    )
    base.update(overrides)
    f = FileEntity(**base)
    repos.files.insert(f)
    return f


class TestUpsertUpdateBranch:
    def test_upsert_existing_calls_update(self, repos, local_source):
        """Line 124: upsert hits the update branch when curator_id exists."""
        f = _mk_file(repos, "/upsert", xxhash3_128="initial")
        # Re-fetch and mutate, then upsert
        f.xxhash3_128 = "updated"
        repos.files.upsert(f)
        fetched = repos.files.get(f.curator_id)
        assert fetched is not None
        assert fetched.xxhash3_128 == "updated"


class TestMarkDeleted:
    def test_mark_deleted_default_when_is_now(self, repos, local_source):
        f = _mk_file(repos, "/md1")
        repos.files.mark_deleted(f.curator_id)
        fetched = repos.files.get(f.curator_id)
        assert fetched is not None
        assert fetched.deleted_at is not None

    def test_mark_deleted_with_explicit_when(self, repos, local_source):
        f = _mk_file(repos, "/md2")
        ts = datetime(2026, 1, 15, 12, 0, 0)
        repos.files.mark_deleted(f.curator_id, when=ts)
        fetched = repos.files.get(f.curator_id)
        assert fetched is not None
        assert fetched.deleted_at == ts


class TestUndelete:
    def test_undelete_clears_deleted_at(self, repos, local_source):
        """Lines 138-140: undelete sets deleted_at to NULL."""
        f = _mk_file(repos, "/ud1")
        repos.files.mark_deleted(f.curator_id)
        assert repos.files.get(f.curator_id).deleted_at is not None
        repos.files.undelete(f.curator_id)
        assert repos.files.get(f.curator_id).deleted_at is None


class TestHardDelete:
    def test_delete_removes_row(self, repos, local_source):
        f = _mk_file(repos, "/hd1")
        assert repos.files.get(f.curator_id) is not None
        repos.files.delete(f.curator_id)
        assert repos.files.get(f.curator_id) is None


class TestFindByHashVariants:
    def test_find_by_md5(self, repos, local_source):
        a = _mk_file(repos, "/m1", md5="aabb")
        b = _mk_file(repos, "/m2", md5="aabb")
        _ = _mk_file(repos, "/m3", md5="ccdd")
        results = repos.files.find_by_md5("aabb")
        assert {r.curator_id for r in results} == {a.curator_id, b.curator_id}

    def test_find_by_md5_excludes_deleted(self, repos, local_source):
        a = _mk_file(repos, "/md_a", md5="dead")
        b = _mk_file(repos, "/md_b", md5="dead")
        repos.files.mark_deleted(b.curator_id)
        # Default include_deleted=False
        results = repos.files.find_by_md5("dead")
        assert {r.curator_id for r in results} == {a.curator_id}
        # include_deleted=True returns both
        with_deleted = repos.files.find_by_md5("dead", include_deleted=True)
        assert {r.curator_id for r in with_deleted} == {a.curator_id, b.curator_id}

    def test_find_by_fuzzy_hash(self, repos, local_source):
        a = _mk_file(repos, "/fz1", fuzzy_hash="fz")
        _ = _mk_file(repos, "/fz2", fuzzy_hash="fy")
        results = repos.files.find_by_fuzzy_hash("fz")
        assert len(results) == 1 and results[0].curator_id == a.curator_id

    def test_find_by_fuzzy_hash_include_deleted(self, repos, local_source):
        a = _mk_file(repos, "/fzd1", fuzzy_hash="abc")
        b = _mk_file(repos, "/fzd2", fuzzy_hash="abc")
        repos.files.mark_deleted(b.curator_id)
        excluding = repos.files.find_by_fuzzy_hash("abc")
        assert {r.curator_id for r in excluding} == {a.curator_id}
        all_ = repos.files.find_by_fuzzy_hash("abc", include_deleted=True)
        assert {r.curator_id for r in all_} == {a.curator_id, b.curator_id}


class TestFindCandidatesBySize:
    def test_basic_size_match(self, repos, local_source):
        a = _mk_file(repos, "/sz_a", size=100)
        b = _mk_file(repos, "/sz_b", size=100)
        c = _mk_file(repos, "/sz_c", size=200)
        results = repos.files.find_candidates_by_size(100)
        assert {r.curator_id for r in results} == {a.curator_id, b.curator_id}

    def test_excludes_curator_id(self, repos, local_source):
        a = _mk_file(repos, "/sz_ex_a", size=300)
        b = _mk_file(repos, "/sz_ex_b", size=300)
        results = repos.files.find_candidates_by_size(
            300, exclude_curator_id=a.curator_id,
        )
        assert len(results) == 1 and results[0].curator_id == b.curator_id

    def test_includes_deleted_when_flagged(self, repos, local_source):
        a = _mk_file(repos, "/sz_d_a", size=400)
        b = _mk_file(repos, "/sz_d_b", size=400)
        repos.files.mark_deleted(b.curator_id)
        excluding = repos.files.find_candidates_by_size(400)
        assert {r.curator_id for r in excluding} == {a.curator_id}
        all_ = repos.files.find_candidates_by_size(400, include_deleted=True)
        assert {r.curator_id for r in all_} == {a.curator_id, b.curator_id}


class TestQueryFlexAttrs:
    def test_query_with_flex_attr_filter(self, repos, local_source):
        f1 = _mk_file(repos, "/q1")
        f1.set_flex("category", "vital")
        repos.files.update(f1)

        f2 = _mk_file(repos, "/q2")
        f2.set_flex("category", "other")
        repos.files.update(f2)

        q = FileQuery(source_ids=["local"], flex_attrs={"category": "vital"})
        results = repos.files.query(q)
        assert len(results) == 1
        assert results[0].curator_id == f1.curator_id


class TestIterAll:
    def test_iter_all_basic(self, repos, local_source):
        for i in range(5):
            _mk_file(repos, f"/ia{i}")
        all_files = list(repos.files.iter_all())
        assert len(all_files) == 5

    def test_iter_all_filters_by_source(self, repos, local_source):
        from curator.models import SourceConfig
        repos.sources.insert(SourceConfig(
            source_id="local:b", source_type="local", display_name="b",
        ))
        _mk_file(repos, "/ia_s1", source_id="local")
        _mk_file(repos, "/ia_s2", source_id="local:b")

        results = list(repos.files.iter_all(source_id="local"))
        assert len(results) == 1
        assert results[0].source_id == "local"

    def test_iter_all_excludes_deleted_by_default(self, repos, local_source):
        a = _mk_file(repos, "/ia_d_a")
        b = _mk_file(repos, "/ia_d_b")
        repos.files.mark_deleted(b.curator_id)
        results = list(repos.files.iter_all())
        ids = {r.curator_id for r in results}
        assert a.curator_id in ids
        assert b.curator_id not in ids

    def test_iter_all_includes_deleted_when_flagged(self, repos, local_source):
        _mk_file(repos, "/ia_id_a")
        b = _mk_file(repos, "/ia_id_b")
        repos.files.mark_deleted(b.curator_id)
        results = list(repos.files.iter_all(include_deleted=True))
        assert len(results) == 2

    def test_iter_all_empty_db_returns_immediately(self, repos, local_source):
        """Line 274: when first fetch is empty, `return` fires."""
        results = list(repos.files.iter_all())
        assert results == []

    def test_iter_all_batches(self, repos, local_source):
        """batch_size triggers multi-batch loop (covers lines 277-279)."""
        for i in range(7):
            _mk_file(repos, f"/ia_bat{i}")
        # batch_size=3 -> 3 + 3 + 1 batches
        all_files = list(repos.files.iter_all(batch_size=3))
        assert len(all_files) == 7


class TestCountWithFilters:
    def test_count_by_source(self, repos, local_source):
        from curator.models import SourceConfig
        repos.sources.insert(SourceConfig(
            source_id="local:other", source_type="local", display_name="other",
        ))
        _mk_file(repos, "/c1", source_id="local")
        _mk_file(repos, "/c2", source_id="local")
        _mk_file(repos, "/c3", source_id="local:other")
        assert repos.files.count(source_id="local") == 2
        assert repos.files.count(source_id="local:other") == 1


class TestUpdateStatus:
    def test_invalid_status_raises_value_error(self, repos, local_source):
        f = _mk_file(repos, "/us_bad")
        with pytest.raises(ValueError, match="Invalid status"):
            repos.files.update_status(f.curator_id, "invalid_value")

    def test_basic_status_update(self, repos, local_source):
        f = _mk_file(repos, "/us1")
        repos.files.update_status(f.curator_id, "vital")
        fetched = repos.files.get(f.curator_id)
        assert fetched is not None
        assert fetched.status == "vital"

    def test_supersedes_id_set(self, repos, local_source):
        f1 = _mk_file(repos, "/us_s1")
        f2 = _mk_file(repos, "/us_s2")
        repos.files.update_status(
            f1.curator_id, "junk", supersedes_id=f2.curator_id,
        )
        fetched = repos.files.get(f1.curator_id)
        assert fetched is not None
        assert fetched.supersedes_id == f2.curator_id

    def test_clear_supersedes(self, repos, local_source):
        f1 = _mk_file(repos, "/us_cs1")
        f2 = _mk_file(repos, "/us_cs2")
        repos.files.update_status(f1.curator_id, "junk", supersedes_id=f2.curator_id)
        repos.files.update_status(f1.curator_id, "active", clear_supersedes=True)
        fetched = repos.files.get(f1.curator_id)
        assert fetched is not None
        assert fetched.supersedes_id is None

    def test_expires_at_set(self, repos, local_source):
        f = _mk_file(repos, "/us_e1")
        ts = datetime(2026, 12, 31)
        repos.files.update_status(f.curator_id, "provisional", expires_at=ts)
        fetched = repos.files.get(f.curator_id)
        assert fetched is not None
        assert fetched.expires_at == ts

    def test_clear_expires(self, repos, local_source):
        f = _mk_file(repos, "/us_ce1")
        repos.files.update_status(
            f.curator_id, "provisional", expires_at=datetime(2026, 1, 1),
        )
        repos.files.update_status(f.curator_id, "active", clear_expires=True)
        fetched = repos.files.get(f.curator_id)
        assert fetched is not None
        assert fetched.expires_at is None


class TestCountByStatus:
    def test_returns_dict_with_all_buckets(self, repos, local_source):
        f1 = _mk_file(repos, "/cs1")
        f2 = _mk_file(repos, "/cs2")
        repos.files.update_status(f1.curator_id, "vital")
        repos.files.update_status(f2.curator_id, "junk")

        out = repos.files.count_by_status()
        assert out["vital"] == 1
        assert out["junk"] == 1
        # All 4 buckets present, including zeros
        assert "active" in out
        assert "provisional" in out

    def test_count_by_status_include_deleted(self, repos, local_source):
        """Branch 363->365: include_deleted=True skips the deleted_at filter."""
        f1 = _mk_file(repos, "/cbs_id_1")
        f2 = _mk_file(repos, "/cbs_id_2")
        repos.files.update_status(f1.curator_id, "vital")
        repos.files.update_status(f2.curator_id, "vital")
        repos.files.mark_deleted(f2.curator_id)
        out = repos.files.count_by_status(include_deleted=True)
        assert out["vital"] == 2  # both included

    def test_count_by_status_with_source_filter(self, repos, local_source):
        from curator.models import SourceConfig
        repos.sources.insert(SourceConfig(
            source_id="local:cbs", source_type="local", display_name="cbs",
        ))
        _mk_file(repos, "/cbs1", source_id="local")
        f2 = _mk_file(repos, "/cbs2", source_id="local:cbs")
        repos.files.update_status(f2.curator_id, "vital")
        out = repos.files.count_by_status(source_id="local:cbs")
        assert out["vital"] == 1
        assert out["active"] == 0


class TestQueryByStatus:
    def test_returns_matching_files(self, repos, local_source):
        f1 = _mk_file(repos, "/qbs1")
        f2 = _mk_file(repos, "/qbs2")
        repos.files.update_status(f1.curator_id, "vital")
        repos.files.update_status(f2.curator_id, "vital")
        results = repos.files.query_by_status("vital")
        assert len(results) == 2

    def test_limit_clauses(self, repos, local_source):
        for i in range(5):
            f = _mk_file(repos, f"/qbs_l{i}")
            repos.files.update_status(f.curator_id, "junk")
        result = repos.files.query_by_status("junk", limit=2)
        assert len(result) == 2

    def test_query_by_status_include_deleted(self, repos, local_source):
        """Branch 387->389: include_deleted=True skips the deleted_at filter."""
        f1 = _mk_file(repos, "/qbs_id_1")
        f2 = _mk_file(repos, "/qbs_id_2")
        repos.files.update_status(f1.curator_id, "junk")
        repos.files.update_status(f2.curator_id, "junk")
        repos.files.mark_deleted(f2.curator_id)
        results = repos.files.query_by_status("junk", include_deleted=True)
        assert len(results) == 2

    def test_query_by_status_with_source_filter(self, repos, local_source):
        from curator.models import SourceConfig
        repos.sources.insert(SourceConfig(
            source_id="local:qbss", source_type="local", display_name="qbss",
        ))
        f1 = _mk_file(repos, "/qbs_s1", source_id="local")
        f2 = _mk_file(repos, "/qbs_s2", source_id="local:qbss")
        for f in (f1, f2):
            repos.files.update_status(f.curator_id, "vital")
        result = repos.files.query_by_status("vital", source_id="local:qbss")
        assert len(result) == 1
        assert result[0].curator_id == f2.curator_id


class TestFindExpiringBefore:
    def test_returns_files_with_expires_at_before_when(self, repos, local_source):
        f1 = _mk_file(repos, "/fe1")
        f2 = _mk_file(repos, "/fe2")
        f3 = _mk_file(repos, "/fe3")
        repos.files.update_status(
            f1.curator_id, "provisional", expires_at=datetime(2026, 1, 1),
        )
        repos.files.update_status(
            f2.curator_id, "provisional", expires_at=datetime(2026, 6, 1),
        )
        # f3 has no expires_at
        results = repos.files.find_expiring_before(datetime(2026, 3, 1))
        ids = {r.curator_id for r in results}
        assert f1.curator_id in ids
        assert f2.curator_id not in ids
        assert f3.curator_id not in ids

    def test_find_expiring_with_source_filter(self, repos, local_source):
        from curator.models import SourceConfig
        repos.sources.insert(SourceConfig(
            source_id="local:fe", source_type="local", display_name="fe",
        ))
        f1 = _mk_file(repos, "/fe_s1", source_id="local")
        f2 = _mk_file(repos, "/fe_s2", source_id="local:fe")
        for f in (f1, f2):
            repos.files.update_status(
                f.curator_id, "provisional", expires_at=datetime(2026, 1, 1),
            )
        result = repos.files.find_expiring_before(
            datetime(2026, 6, 1), source_id="local:fe",
        )
        assert len(result) == 1
        assert result[0].curator_id == f2.curator_id

    def test_find_expiring_includes_deleted_when_flagged(self, repos, local_source):
        f1 = _mk_file(repos, "/fe_d1")
        f2 = _mk_file(repos, "/fe_d2")
        for f in (f1, f2):
            repos.files.update_status(
                f.curator_id, "provisional", expires_at=datetime(2026, 1, 1),
            )
        repos.files.mark_deleted(f2.curator_id)
        excluding = repos.files.find_expiring_before(datetime(2026, 6, 1))
        all_ = repos.files.find_expiring_before(
            datetime(2026, 6, 1), include_deleted=True,
        )
        assert {r.curator_id for r in excluding} == {f1.curator_id}
        assert {r.curator_id for r in all_} == {f1.curator_id, f2.curator_id}
