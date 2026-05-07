"""Tests for SafetyService and supporting primitives (Phase Gamma F1).

Coverage matrix:

* ``find_project_root`` \u2014 file inside / outside a project, varying depth,
  handles non-existent paths.
* App-data + OS-managed prefix matching \u2014 platform-aware.
* ``find_handle_holders`` \u2014 detects ourselves holding a temp file open;
  skips silently when psutil is missing (mock the availability check).
* SafetyService.check_path \u2014 each individual concern, plus aggregation
  semantics (REFUSE wins over CAUTION which wins over SAFE).
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from curator.services.safety import (
    PROJECT_MARKERS,
    SafetyConcern,
    SafetyLevel,
    SafetyReport,
    SafetyService,
    _is_under,
    _psutil_available,
    find_handle_holders,
    find_project_root,
    get_default_app_data_paths,
    get_default_os_managed_paths,
)


# ===========================================================================
# find_project_root
# ===========================================================================


class TestFindProjectRoot:
    def test_returns_none_when_no_marker_above(self, tmp_path):
        target = tmp_path / "deep" / "nested" / "file.txt"
        target.parent.mkdir(parents=True)
        target.write_text("x")
        # tmp_path itself is NOT a project (no markers) — but parent
        # directories outside might be (e.g., the test is running inside
        # the Curator repo). So we narrow the search with max_depth=2.
        assert find_project_root(target, max_depth=2) is None

    def test_finds_git_marker(self, tmp_path):
        proj = tmp_path / "myproj"
        (proj / ".git").mkdir(parents=True)
        target = proj / "src" / "main.py"
        target.parent.mkdir(parents=True)
        target.write_text("print()")
        assert find_project_root(target) == proj

    def test_finds_pyproject_marker(self, tmp_path):
        proj = tmp_path / "pyproj"
        proj.mkdir()
        (proj / "pyproject.toml").write_text("[project]")
        target = proj / "module" / "x.py"
        target.parent.mkdir()
        target.write_text("")
        assert find_project_root(target) == proj

    def test_finds_package_json_marker(self, tmp_path):
        proj = tmp_path / "node_app"
        proj.mkdir()
        (proj / "package.json").write_text("{}")
        target = proj / "src" / "index.js"
        target.parent.mkdir()
        target.write_text("")
        assert find_project_root(target) == proj

    def test_finds_sln_pattern(self, tmp_path):
        proj = tmp_path / "vsproj"
        proj.mkdir()
        (proj / "MyApp.sln").write_text("Microsoft Visual Studio Solution File")
        target = proj / "Source.cs"
        target.write_text("")
        assert find_project_root(target) == proj

    def test_walks_up_multiple_levels(self, tmp_path):
        proj = tmp_path / "outer"
        proj.mkdir()
        (proj / ".git").mkdir()
        target = proj / "a" / "b" / "c" / "d" / "leaf.txt"
        target.parent.mkdir(parents=True)
        target.write_text("")
        assert find_project_root(target) == proj

    def test_max_depth_cap_respected(self, tmp_path):
        proj = tmp_path / "outer"
        proj.mkdir()
        (proj / ".git").mkdir()
        target = proj / "a" / "b" / "c" / "d" / "leaf.txt"
        target.parent.mkdir(parents=True)
        target.write_text("")
        # max_depth=2 means we look at the file's dir and one parent up,
        # which is c/d \u2192 c. The git is 3 above c. Should not find it.
        assert find_project_root(target, max_depth=2) is None

    def test_handles_non_existent_path(self, tmp_path):
        ghost = tmp_path / "does" / "not" / "exist.txt"
        # Should return None without raising.
        result = find_project_root(ghost, max_depth=3)
        assert result is None

    def test_directory_input_works(self, tmp_path):
        # Pass a directory rather than a file.
        proj = tmp_path / "p"
        proj.mkdir()
        (proj / "Cargo.toml").write_text("[package]")
        sub = proj / "src"
        sub.mkdir()
        assert find_project_root(sub) == proj


# ===========================================================================
# Default app-data / OS-managed path lists
# ===========================================================================


class TestDefaultPaths:
    def test_app_data_paths_nonempty(self):
        paths = get_default_app_data_paths()
        assert len(paths) > 0
        assert all(isinstance(p, Path) for p in paths)

    def test_os_managed_paths_nonempty(self):
        paths = get_default_os_managed_paths()
        assert len(paths) > 0
        assert all(isinstance(p, Path) for p in paths)

    def test_windows_includes_appdata(self):
        if sys.platform != "win32":
            pytest.skip("Windows-only check")
        import os
        paths = get_default_app_data_paths()
        appdata = os.environ.get("APPDATA")
        if appdata:
            assert any(str(p) == appdata for p in paths)

    def test_windows_includes_systemroot_in_os_managed(self):
        if sys.platform != "win32":
            pytest.skip("Windows-only check")
        paths = get_default_os_managed_paths()
        # SystemRoot may be "C:\WINDOWS" (uppercase) on some installs.
        # Case-insensitive substring check covers both.
        assert any("windows" in str(p).lower() for p in paths)


# ===========================================================================
# _is_under prefix matching
# ===========================================================================


class TestIsUnder:
    def test_path_under_root(self, tmp_path):
        root = tmp_path / "a"
        root.mkdir()
        child = root / "b" / "c.txt"
        child.parent.mkdir()
        child.write_text("x")
        assert _is_under(child, root) is True

    def test_path_not_under_unrelated_root(self, tmp_path):
        a = tmp_path / "a"
        b = tmp_path / "b"
        a.mkdir(); b.mkdir()
        target = b / "x.txt"
        target.write_text("")
        assert _is_under(target, a) is False

    def test_root_itself_is_under_itself(self, tmp_path):
        # _is_under is inclusive: root counts as "under" root.
        root = tmp_path / "selfcheck"
        root.mkdir()
        assert _is_under(root, root) is True

    def test_handles_nonexistent_paths(self, tmp_path):
        ghost = tmp_path / "ghost.txt"
        result = _is_under(ghost, tmp_path)
        # resolve(strict=False) returns the (canonical) path even when
        # nothing exists, so the prefix match still works.
        assert result is True


# ===========================================================================
# Open-handle detection (psutil-based)
# ===========================================================================


class TestFindHandleHolders:
    def test_returns_empty_list_when_psutil_unavailable(self, monkeypatch, tmp_path):
        target = tmp_path / "x.txt"
        target.write_text("hi")
        monkeypatch.setattr(
            "curator.services.safety._psutil_available",
            lambda: False,
        )
        assert find_handle_holders(target) == []

    def test_returns_empty_list_for_nonexistent_path(self, tmp_path):
        ghost = tmp_path / "no_such_file.txt"
        assert find_handle_holders(ghost) == []

    @pytest.mark.slow
    def test_detects_self_holding_file(self, tmp_path):
        if not _psutil_available():
            pytest.skip("psutil not installed")
        target = tmp_path / "held.txt"
        target.write_text("content")
        with open(target, "rb") as fh:
            holders = find_handle_holders(target)
            # On most platforms, our own python process should appear.
            # On some restrictive Windows setups proc.open_files() may
            # require admin even for own process \u2014 in that case we'll
            # get an empty list. Accept either: the contract is "doesn't
            # raise" and "returns a list".
            assert isinstance(holders, list)
            # If we got holders, our own process name is one of them.
            if holders:
                assert any(
                    "python" in h.lower() or "pytest" in h.lower()
                    for h in holders
                )


# ===========================================================================
# SafetyReport aggregate logic
# ===========================================================================


class TestSafetyReport:
    def test_default_level_is_safe(self):
        r = SafetyReport(path="/x")
        assert r.level == SafetyLevel.SAFE
        assert r.is_safe is True
        assert r.is_refused is False

    def test_caution_concern_lifts_level(self):
        r = SafetyReport(path="/x")
        r.add_concern(SafetyConcern.PROJECT_FILE, "in project")
        assert r.level == SafetyLevel.CAUTION
        assert r.is_safe is False

    def test_refuse_concern_lifts_level(self):
        r = SafetyReport(path="/x")
        r.add_concern(SafetyConcern.OS_MANAGED, "under /System")
        assert r.level == SafetyLevel.REFUSE
        assert r.is_refused is True

    def test_caution_then_refuse_yields_refuse(self):
        r = SafetyReport(path="/x")
        r.add_concern(SafetyConcern.SYMLINK, "is symlink")
        r.add_concern(SafetyConcern.OS_MANAGED, "in /System")
        assert r.level == SafetyLevel.REFUSE

    def test_refuse_then_caution_stays_refuse(self):
        # Adding a less-severe concern after a more-severe one shouldn't
        # downgrade the verdict.
        r = SafetyReport(path="/x")
        r.add_concern(SafetyConcern.OS_MANAGED, "in /System")
        r.add_concern(SafetyConcern.SYMLINK, "is symlink")
        assert r.level == SafetyLevel.REFUSE

    def test_concerns_list_records_each_addition(self):
        r = SafetyReport(path="/x")
        r.add_concern(SafetyConcern.PROJECT_FILE, "in proj")
        r.add_concern(SafetyConcern.SYMLINK, "is link")
        assert len(r.concerns) == 2
        assert r.concerns[0][0] == SafetyConcern.PROJECT_FILE


# ===========================================================================
# SafetyService.check_path
# ===========================================================================


class TestSafetyServiceCheckPath:
    def test_safe_for_unremarkable_file(self, tmp_path):
        target = tmp_path / "plain.txt"
        target.write_text("ordinary file")
        # Override to empty path lists so we don't hit the user's
        # actual app-data / OS paths during the test.
        svc = SafetyService(
            app_data_paths=[],
            os_managed_paths=[],
        )
        report = svc.check_path(target)
        assert report.level == SafetyLevel.SAFE
        assert report.concerns == []

    def test_caution_for_app_data_path(self, tmp_path):
        target = tmp_path / "appdata" / "some_app" / "file.dat"
        target.parent.mkdir(parents=True)
        target.write_text("")
        svc = SafetyService(
            app_data_paths=[tmp_path / "appdata"],
            os_managed_paths=[],
        )
        report = svc.check_path(target)
        assert report.level == SafetyLevel.CAUTION
        assert any(c[0] == SafetyConcern.APP_DATA for c in report.concerns)

    def test_refuse_for_os_managed_path(self, tmp_path):
        target = tmp_path / "system" / "important.dll"
        target.parent.mkdir(parents=True)
        target.write_text("")
        svc = SafetyService(
            app_data_paths=[],
            os_managed_paths=[tmp_path / "system"],
        )
        report = svc.check_path(target)
        assert report.level == SafetyLevel.REFUSE
        assert any(c[0] == SafetyConcern.OS_MANAGED for c in report.concerns)

    def test_os_managed_short_circuits_other_checks(self, tmp_path):
        # File is in BOTH a project AND under an OS-managed path.
        # Once we detect OS-managed, we return immediately and don't
        # even check for project membership.
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / ".git").mkdir()
        target = proj / "src" / "x.py"
        target.parent.mkdir()
        target.write_text("")

        svc = SafetyService(
            app_data_paths=[],
            os_managed_paths=[tmp_path],   # everything under tmp_path is OS-managed
        )
        report = svc.check_path(target)
        assert report.level == SafetyLevel.REFUSE
        # Only the OS_MANAGED concern was recorded, no project_file.
        kinds = [c[0] for c in report.concerns]
        assert SafetyConcern.OS_MANAGED in kinds
        assert SafetyConcern.PROJECT_FILE not in kinds

    def test_caution_for_project_file(self, tmp_path):
        proj = tmp_path / "myproj"
        proj.mkdir()
        (proj / ".git").mkdir()
        target = proj / "main.py"
        target.write_text("print()")

        svc = SafetyService(app_data_paths=[], os_managed_paths=[])
        report = svc.check_path(target)
        assert report.level == SafetyLevel.CAUTION
        assert any(c[0] == SafetyConcern.PROJECT_FILE for c in report.concerns)
        assert report.project_root == str(proj)

    def test_caution_for_symlink(self, tmp_path):
        if sys.platform == "win32":
            # Creating symlinks on Windows requires admin rights or
            # developer mode; skip rather than fail in CI.
            real = tmp_path / "real.txt"
            real.write_text("real")
            link = tmp_path / "link.txt"
            try:
                link.symlink_to(real)
            except (OSError, NotImplementedError):
                pytest.skip("symlink creation requires admin/dev mode on Windows")
        else:
            real = tmp_path / "real.txt"
            real.write_text("real")
            link = tmp_path / "link.txt"
            link.symlink_to(real)

        svc = SafetyService(app_data_paths=[], os_managed_paths=[])
        report = svc.check_path(link)
        assert report.level == SafetyLevel.CAUTION
        assert any(c[0] == SafetyConcern.SYMLINK for c in report.concerns)

    def test_handles_nonexistent_path(self, tmp_path):
        ghost = tmp_path / "no_such.txt"
        svc = SafetyService(app_data_paths=[], os_managed_paths=[])
        # Should not raise, even though the file doesn't exist.
        report = svc.check_path(ghost)
        # Level is whatever the analyses produce \u2014 typically SAFE since
        # the only checks that can fire need the path to exist or be under
        # something.
        assert isinstance(report.level, SafetyLevel)

    def test_check_handles_flag_off_by_default(self, tmp_path):
        target = tmp_path / "no_handles.txt"
        target.write_text("")
        svc = SafetyService(app_data_paths=[], os_managed_paths=[])
        report = svc.check_path(target)
        # With check_handles=False (default), holders is always empty.
        assert report.holders == []

    @pytest.mark.slow
    def test_check_handles_with_self_holding_file(self, tmp_path):
        if not _psutil_available():
            pytest.skip("psutil not installed")
        target = tmp_path / "held.txt"
        target.write_text("content")
        svc = SafetyService(app_data_paths=[], os_managed_paths=[])
        with open(target, "rb"):
            report = svc.check_path(target, check_handles=True)
            # If our process is detected as a holder, the concern is recorded
            # AND level becomes CAUTION. If detection failed (Windows admin
            # restrictions), report stays SAFE and holders is empty. Both are
            # valid \u2014 we just assert internal consistency.
            if report.holders:
                assert any(
                    c[0] == SafetyConcern.OPEN_HANDLE for c in report.concerns
                )
                assert report.level == SafetyLevel.CAUTION
            else:
                assert all(
                    c[0] != SafetyConcern.OPEN_HANDLE for c in report.concerns
                )

    def test_extra_app_data_paths_extend_defaults(self, tmp_path):
        # Confirm that extra_app_data adds to (not replaces) the defaults.
        custom = tmp_path / "my_custom_app"
        custom.mkdir()
        target = custom / "file.dat"
        target.write_text("")
        svc = SafetyService(extra_app_data=[custom])
        report = svc.check_path(target)
        assert any(c[0] == SafetyConcern.APP_DATA for c in report.concerns)


# ===========================================================================
# SafetyService.check_paths (batch)
# ===========================================================================


class TestSafetyServiceCheckPaths:
    def test_returns_one_report_per_input(self, tmp_path):
        a = tmp_path / "a.txt"; a.write_text("")
        b = tmp_path / "b.txt"; b.write_text("")
        c = tmp_path / "c.txt"; c.write_text("")
        svc = SafetyService(app_data_paths=[], os_managed_paths=[])
        reports = svc.check_paths([a, b, c])
        assert len(reports) == 3
        assert reports[0].path == str(a)
        assert reports[1].path == str(b)
        assert reports[2].path == str(c)

    def test_empty_input_returns_empty_list(self):
        svc = SafetyService(app_data_paths=[], os_managed_paths=[])
        assert svc.check_paths([]) == []


# ===========================================================================
# Helper accessors
# ===========================================================================


class TestServiceHelpers:
    def test_is_app_data_predicate(self, tmp_path):
        root = tmp_path / "fake_appdata"
        root.mkdir()
        child = root / "thing" / "x.dat"
        child.parent.mkdir()
        child.write_text("")
        svc = SafetyService(app_data_paths=[root], os_managed_paths=[])
        assert svc.is_app_data(child) is True
        assert svc.is_app_data(tmp_path / "outside.txt") is False

    def test_is_os_managed_predicate(self, tmp_path):
        root = tmp_path / "fake_system"
        root.mkdir()
        child = root / "kernel32.dll"
        child.write_text("")
        svc = SafetyService(app_data_paths=[], os_managed_paths=[root])
        assert svc.is_os_managed(child) is True
        assert svc.is_os_managed(tmp_path / "outside.txt") is False
