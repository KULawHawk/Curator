"""Coverage closure for ``curator.storage.repositories.trash_repo`` (v1.7.131).

Targets:
- Lines 63-64: ``delete(curator_id)``
- Lines 91-92: ``list(since=...)`` filter clause
- Branch 97->100: ``list(limit=None)`` skips the LIMIT clause
- Lines 104-105: ``count()``

Uses the shared ``repos`` + ``local_source`` fixtures from
``tests/conftest.py`` to satisfy the trash_registry → files → sources
foreign-key chain.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from curator._compat.datetime import utcnow_naive
from curator.models import FileEntity, TrashRecord


def _trash_for(file_entity, *, when: datetime, actor: str = "alice") -> TrashRecord:
    return TrashRecord(
        curator_id=file_entity.curator_id,
        original_source_id=file_entity.source_id,
        original_path=file_entity.source_path,
        file_hash=file_entity.xxhash3_128,
        trashed_at=when,
        trashed_by=actor,
        reason="testing",
        bundle_memberships_snapshot=[],
        file_attrs_snapshot={},
        os_trash_location=None,
        restore_path_override=None,
    )


def _make_file(repos, source_id: str, source_path: str) -> FileEntity:
    f = FileEntity(
        source_id=source_id, source_path=source_path,
        size=1, mtime=utcnow_naive(),
    )
    repos.files.insert(f)
    return f


class TestDelete:
    def test_delete_removes_record(self, repos, local_source):
        f = _make_file(repos, "local", "/a.txt")
        rec = _trash_for(f, when=datetime(2026, 1, 15, 12, 0, 0))
        repos.trash.insert(rec)
        assert repos.trash.get(f.curator_id) is not None

        repos.trash.delete(f.curator_id)
        assert repos.trash.get(f.curator_id) is None


class TestListFilters:
    def test_since_filter_excludes_older_records(self, repos, local_source):
        f_old = _make_file(repos, "local", "/old.txt")
        f_new = _make_file(repos, "local", "/new.txt")
        repos.trash.insert(_trash_for(f_old, when=datetime(2026, 1, 1)))
        repos.trash.insert(_trash_for(f_new, when=datetime(2026, 2, 1)))

        cutoff = datetime(2026, 1, 15)
        results = repos.trash.list(since=cutoff)
        ids = {r.curator_id for r in results}
        assert f_new.curator_id in ids
        assert f_old.curator_id not in ids

    def test_list_without_limit_returns_all_matching(self, repos, local_source):
        """Branch 97->100: limit=None skips the LIMIT clause."""
        for i in range(5):
            f = _make_file(repos, "local", f"/x{i}.txt")
            repos.trash.insert(
                _trash_for(f, when=datetime(2026, 1, 1) + timedelta(days=i)),
            )
        results = repos.trash.list()  # limit=None
        assert len(results) == 5


class TestCount:
    def test_count_returns_row_total(self, repos, local_source):
        assert repos.trash.count() == 0
        f1 = _make_file(repos, "local", "/c1.txt")
        f2 = _make_file(repos, "local", "/c2.txt")
        repos.trash.insert(_trash_for(f1, when=datetime(2026, 1, 1)))
        repos.trash.insert(_trash_for(f2, when=datetime(2026, 1, 2)))
        assert repos.trash.count() == 2
