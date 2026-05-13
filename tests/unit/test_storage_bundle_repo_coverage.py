"""Coverage closure for ``curator.storage.repositories.bundle_repo`` (v1.7.137).

The Tier 3 FINAL ship. Closes 28 uncovered lines across all the
non-insert methods (update / delete / get / list_all / find_by_name /
remove_membership / _row_to_bundle).
"""

from __future__ import annotations

from uuid import uuid4

from curator._compat.datetime import utcnow_naive
from curator.models import BundleEntity, BundleMembership, FileEntity


def _mk_bundle(**overrides) -> BundleEntity:
    base = dict(bundle_type="manual", name="b", confidence=1.0)
    base.update(overrides)
    return BundleEntity(**base)


def _mk_file(repos, path: str) -> FileEntity:
    f = FileEntity(
        source_id="local", source_path=path, size=1, mtime=utcnow_naive(),
    )
    repos.files.insert(f)
    return f


class TestUpdate:
    def test_update_overwrites_fields(self, repos):
        b = _mk_bundle(name="orig")
        repos.bundles.insert(b)

        b.name = "renamed"
        b.description = "added later"
        b.confidence = 0.7
        b.set_flex("k", "v")
        repos.bundles.update(b)

        fetched = repos.bundles.get(b.bundle_id)
        assert fetched is not None
        assert fetched.name == "renamed"
        assert fetched.description == "added later"
        assert fetched.confidence == 0.7
        assert fetched.flex.get("k") == "v"


class TestDelete:
    def test_delete_removes_bundle(self, repos):
        b = _mk_bundle()
        repos.bundles.insert(b)
        assert repos.bundles.get(b.bundle_id) is not None
        repos.bundles.delete(b.bundle_id)
        assert repos.bundles.get(b.bundle_id) is None


class TestGet:
    def test_get_returns_bundle_with_flex_attrs(self, repos):
        b = _mk_bundle(name="with-flex")
        b.set_flex("nested", {"k": "v"})
        repos.bundles.insert(b)

        fetched = repos.bundles.get(b.bundle_id)
        assert fetched is not None
        assert fetched.name == "with-flex"
        assert fetched.flex.get("nested") == {"k": "v"}

    def test_get_missing_returns_none(self, repos):
        from uuid import uuid4 as _u
        assert repos.bundles.get(_u()) is None


class TestListAll:
    def test_list_all_returns_all_bundles(self, repos):
        for i in range(3):
            repos.bundles.insert(_mk_bundle(name=f"b{i}"))
        assert len(repos.bundles.list_all()) == 3

    def test_list_all_filters_by_bundle_type(self, repos):
        repos.bundles.insert(_mk_bundle(bundle_type="manual", name="m"))
        repos.bundles.insert(_mk_bundle(bundle_type="auto", name="a"))
        manuals = repos.bundles.list_all(bundle_type="manual")
        autos = repos.bundles.list_all(bundle_type="auto")
        assert len(manuals) == 1 and manuals[0].name == "m"
        assert len(autos) == 1 and autos[0].name == "a"


class TestFindByName:
    def test_find_by_name_returns_matches(self, repos):
        b1 = _mk_bundle(name="alpha")
        b2 = _mk_bundle(name="alpha")  # same name
        b3 = _mk_bundle(name="beta")
        for b in (b1, b2, b3):
            repos.bundles.insert(b)
        results = repos.bundles.find_by_name("alpha")
        assert len(results) == 2
        assert {r.bundle_id for r in results} == {b1.bundle_id, b2.bundle_id}

    def test_find_by_name_empty_when_no_match(self, repos):
        repos.bundles.insert(_mk_bundle(name="something"))
        assert repos.bundles.find_by_name("nothing") == []


class TestRemoveMembership:
    def test_remove_membership_drops_row(self, repos, local_source):
        b = _mk_bundle()
        repos.bundles.insert(b)
        f = _mk_file(repos, "/m.txt")
        m = BundleMembership(bundle_id=b.bundle_id, curator_id=f.curator_id)
        repos.bundles.add_membership(m)
        assert repos.bundles.member_count(b.bundle_id) == 1

        repos.bundles.remove_membership(b.bundle_id, f.curator_id)
        assert repos.bundles.member_count(b.bundle_id) == 0
