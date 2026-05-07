"""End-to-end integration test for scan \u2192 organize flow (Phase Gamma F1).

Builds a real temp directory tree, scans it with ScanService into the
real DB, then runs OrganizeService.plan() and asserts the buckets
reflect the SafetyService verdicts the files would actually receive.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from curator.models import SourceConfig
from curator.services.safety import SafetyConcern, SafetyLevel


pytestmark = pytest.mark.integration


@pytest.fixture
def populated_tree(tmp_path):
    """Build a tree with a mix of safe / project / OS-managed files.

    Returns a dict with the paths so tests can reference them by role.
    """
    # SAFE: an unremarkable file outside any project
    safe_dir = tmp_path / "loose"
    safe_dir.mkdir()
    safe_file = safe_dir / "ordinary.txt"
    safe_file.write_text("nothing special here")

    # CAUTION (project_file): inside a .git-marked project
    proj_dir = tmp_path / "myproj"
    (proj_dir / ".git").mkdir(parents=True)
    proj_file = proj_dir / "src" / "main.py"
    proj_file.parent.mkdir(parents=True)
    proj_file.write_text("print('hello')")

    # CAUTION (app_data): inside a path we'll register as app-data
    appdata_dir = tmp_path / "fake_appdata" / "myapp"
    appdata_dir.mkdir(parents=True)
    appdata_file = appdata_dir / "settings.json"
    appdata_file.write_text("{}")

    # REFUSE (os_managed): inside a path we'll register as OS-managed
    osmgr_dir = tmp_path / "fake_system"
    osmgr_dir.mkdir()
    osmgr_file = osmgr_dir / "important.dll"
    osmgr_file.write_text("binary")

    return {
        "tree_root": tmp_path,
        "safe": safe_file,
        "project": proj_file,
        "appdata": appdata_file,
        "osmgr": osmgr_file,
        "fake_appdata_root": tmp_path / "fake_appdata",
        "fake_system_root": tmp_path / "fake_system",
    }


@pytest.fixture
def organize_runtime(services, repos, populated_tree):
    """Wire SafetyService with our test app-data + os-managed paths.

    The default ``services.organize`` uses platform-default registries,
    which in tests would treat the temp dir as APP_DATA (because tmp is
    usually under %LOCALAPPDATA%). We want to control that explicitly.
    """
    from curator.services.safety import SafetyService
    from curator.services.organize import OrganizeService

    safety = SafetyService(
        app_data_paths=[populated_tree["fake_appdata_root"]],
        os_managed_paths=[populated_tree["fake_system_root"]],
    )
    organize = OrganizeService(repos.files, safety)
    return organize


# ---------------------------------------------------------------------------
# scan \u2192 organize round trip
# ---------------------------------------------------------------------------


class TestScanOrganizeFlow:
    def test_organize_buckets_reflect_safety_verdicts(
        self, services, repos, populated_tree, organize_runtime
    ):
        # 1. Register the source.
        repos.sources.insert(SourceConfig(
            source_id="local",
            source_type="local",
            display_name="organize test",
            enabled=True,
            config={"root": str(populated_tree["tree_root"])},
        ))

        # 2. Scan the tree.
        report = services.scan.scan(
            source_id="local",
            root=str(populated_tree["tree_root"]),
        )
        assert report.files_seen >= 4

        # 3. Plan the organize.
        plan = organize_runtime.plan(source_id="local")

        # 4. Each file should land in its expected bucket.
        safe_paths = {f.source_path for f in plan.safe.files}
        caution_paths = {f.source_path for f in plan.caution.files}
        refuse_paths = {f.source_path for f in plan.refuse.files}

        assert str(populated_tree["safe"]) in safe_paths
        assert str(populated_tree["project"]) in caution_paths
        assert str(populated_tree["appdata"]) in caution_paths
        assert str(populated_tree["osmgr"]) in refuse_paths

    def test_root_prefix_filter_narrows_to_subtree(
        self, services, repos, populated_tree, organize_runtime
    ):
        repos.sources.insert(SourceConfig(
            source_id="local",
            source_type="local",
            display_name="prefix test",
            enabled=True,
            config={"root": str(populated_tree["tree_root"])},
        ))
        services.scan.scan(
            source_id="local",
            root=str(populated_tree["tree_root"]),
        )

        # Plan only the OS-managed subtree.
        plan = organize_runtime.plan(
            source_id="local",
            root_prefix=str(populated_tree["fake_system_root"]),
        )
        # Should only see the one OS-managed file.
        assert plan.total_files == 1
        assert plan.refuse.count == 1
        assert plan.safe.count == 0
        assert plan.caution.count == 0

    def test_concern_breakdown_for_caution_bucket(
        self, services, repos, populated_tree, organize_runtime
    ):
        repos.sources.insert(SourceConfig(
            source_id="local",
            source_type="local",
            display_name="caution test",
            enabled=True,
            config={"root": str(populated_tree["tree_root"])},
        ))
        services.scan.scan(
            source_id="local",
            root=str(populated_tree["tree_root"]),
        )
        plan = organize_runtime.plan(source_id="local")

        # The CAUTION bucket should report both project_file and app_data
        # as concerns (one file each).
        counts = plan.caution.concern_counts()
        assert counts.get(SafetyConcern.PROJECT_FILE, 0) >= 1
        assert counts.get(SafetyConcern.APP_DATA, 0) >= 1

    def test_unknown_source_returns_empty_plan(self, organize_runtime):
        plan = organize_runtime.plan(source_id="nonexistent_source")
        assert plan.total_files == 0
        assert plan.completed_at is not None

    def test_limit_caps_files_evaluated(
        self, services, repos, populated_tree, organize_runtime
    ):
        repos.sources.insert(SourceConfig(
            source_id="local",
            source_type="local",
            display_name="limit test",
            enabled=True,
            config={"root": str(populated_tree["tree_root"])},
        ))
        services.scan.scan(
            source_id="local",
            root=str(populated_tree["tree_root"]),
        )
        plan = organize_runtime.plan(source_id="local", limit=2)
        assert plan.total_files <= 2
