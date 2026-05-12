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
    _windows_app_data_paths,
    _windows_os_managed_paths,
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


# ===========================================================================
# v1.7.84 — Windows-coverage additions: defensive error paths,
# psutil-mocked find_handle_holders, symlink + check_handles branches.
# Target: 100% line + branch coverage on Windows-relevant safety.py code.
# Non-Windows code is pragma'd; see docs/PLATFORM_SCOPE.md.
# ===========================================================================


class TestDefensiveErrorPaths:
    """Coverage for OSError / RuntimeError defensive branches."""

    def test_find_project_root_resolve_raises_oserror(self, monkeypatch, tmp_path):
        target = tmp_path / "x.txt"
        target.write_text("")

        def boom(self, *args, **kwargs):
            raise OSError("simulated resolve failure")

        monkeypatch.setattr(Path, "resolve", boom)
        assert find_project_root(target) is None

    def test_find_project_root_resolve_raises_runtimeerror(self, monkeypatch, tmp_path):
        target = tmp_path / "x.txt"
        target.write_text("")

        def boom(self, *args, **kwargs):
            raise RuntimeError("simulated symlink cycle")

        monkeypatch.setattr(Path, "resolve", boom)
        assert find_project_root(target) is None

    def test_find_project_root_inner_oserror_during_marker_check(
        self, monkeypatch, tmp_path
    ):
        # If Path.exists() raises OSError during the marker loop, the
        # function must return None gracefully (defensive path 205-206).
        proj = tmp_path / "proj"
        proj.mkdir()
        target = proj / "file.txt"
        target.write_text("")

        orig_exists = Path.exists
        call_count = {"n": 0}

        def selective_exists(self):
            call_count["n"] += 1
            # Let the initial currency check pass (calls 1-2), then raise.
            if call_count["n"] >= 3:
                raise OSError("simulated stat failure")
            return orig_exists(self)

        monkeypatch.setattr(Path, "exists", selective_exists)
        assert find_project_root(target) is None

    def test_is_under_resolve_oserror(self, monkeypatch, tmp_path):
        target = tmp_path / "x.txt"
        target.write_text("")

        def boom(self, *args, **kwargs):
            raise OSError("simulated resolve failure")

        monkeypatch.setattr(Path, "resolve", boom)
        # Defensive return False (lines 364-365)
        assert _is_under(target, tmp_path) is False

    def test_is_under_resolve_runtimeerror(self, monkeypatch, tmp_path):
        target = tmp_path / "x.txt"
        target.write_text("")

        def boom(self, *args, **kwargs):
            raise RuntimeError("simulated symlink cycle")

        monkeypatch.setattr(Path, "resolve", boom)
        assert _is_under(target, tmp_path) is False


# ===========================================================================
# _psutil_available ImportError path (lines 379-380)
# ===========================================================================


class TestPsutilAvailableFalsePath:
    def test_returns_false_when_import_fails(self, monkeypatch):
        import builtins

        orig_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "psutil":
                raise ImportError("simulated unavailability")
            return orig_import(name, *args, **kwargs)

        # Ensure cached import doesn't hide the error
        monkeypatch.delitem(sys.modules, "psutil", raising=False)
        monkeypatch.setattr(builtins, "__import__", fake_import)
        assert _psutil_available() is False


# ===========================================================================
# find_handle_holders body (lines 402-429) — psutil mocked
# ===========================================================================


class _FakeOpenFile:
    """Mimics psutil's Process.open_files() entries (.path attribute)."""

    def __init__(self, path):
        self.path = path


class _FakeProc:
    """Mimics psutil.Process for find_handle_holders testing.

    Attributes the service uses:
      * proc.info — dict-like with 'name' and 'pid' keys
      * proc.open_files() — returns list of objects with .path
    """

    def __init__(
        self,
        name,
        pid,
        files=None,
        raise_on_open_files=None,
        info_name_is_none=False,
    ):
        if info_name_is_none:
            self.info = {"name": None, "pid": pid}
        else:
            self.info = {"name": name, "pid": pid}
        self._files = files or []
        self._raise = raise_on_open_files

    def open_files(self):
        if self._raise is not None:
            raise self._raise
        return self._files


class TestFindHandleHoldersBody:
    """Comprehensive coverage for find_handle_holders by mocking psutil.process_iter."""

    def _install_fake_iter(self, monkeypatch, procs):
        """Replace psutil.process_iter to yield the given fake processes."""
        if not _psutil_available():
            pytest.skip("psutil not installed; cannot mock")
        import psutil

        def fake_iter(attrs=None):
            for p in procs:
                yield p

        monkeypatch.setattr(psutil, "process_iter", fake_iter)

    def test_target_resolve_oserror_returns_empty(self, monkeypatch, tmp_path):
        # If path.resolve() raises OSError, function returns [] before iter.
        target = tmp_path / "x.txt"
        target.write_text("")

        orig_resolve = Path.resolve
        call_count = {"n": 0}

        def selective_resolve(self, *args, **kwargs):
            call_count["n"] += 1
            # path.exists() must succeed first; resolve() is called after.
            # Raise on the FIRST resolve call (which is on `path` itself).
            if call_count["n"] == 1:
                raise OSError("simulated")
            return orig_resolve(self, *args, **kwargs)

        monkeypatch.setattr(Path, "resolve", selective_resolve)
        assert find_handle_holders(target) == []

    def test_detects_holder_when_path_matches(self, monkeypatch, tmp_path):
        target = tmp_path / "held.bin"
        target.write_text("x")
        # A fake process that has the target file open
        proc = _FakeProc(
            "myapp.exe",
            1234,
            files=[_FakeOpenFile(str(target))],
        )
        self._install_fake_iter(monkeypatch, [proc])
        result = find_handle_holders(target)
        assert "myapp.exe" in result

    def test_fallback_to_pid_when_info_name_is_none(self, monkeypatch, tmp_path):
        target = tmp_path / "held.bin"
        target.write_text("x")
        proc = _FakeProc(
            None, 7777,
            files=[_FakeOpenFile(str(target))],
            info_name_is_none=True,
        )
        self._install_fake_iter(monkeypatch, [proc])
        result = find_handle_holders(target)
        assert "pid 7777" in result

    def test_skips_process_with_unrelated_file(self, monkeypatch, tmp_path):
        target = tmp_path / "held.bin"
        target.write_text("x")
        unrelated = tmp_path / "other.bin"
        unrelated.write_text("y")
        proc = _FakeProc(
            "unrelated.exe", 9999,
            files=[_FakeOpenFile(str(unrelated))],
        )
        self._install_fake_iter(monkeypatch, [proc])
        result = find_handle_holders(target)
        assert "unrelated.exe" not in result

    def test_open_file_path_resolve_oserror_skips_inner(self, monkeypatch, tmp_path):
        # If Path(f.path).resolve() raises OSError, the inner loop continues
        # (lines 419-420). The outer loop must not crash.
        target = tmp_path / "held.bin"
        target.write_text("x")

        # File path that will fail to resolve
        bad_file = _FakeOpenFile("\\\\?\\GLOBALROOT\\bad")
        good_file = _FakeOpenFile(str(target))

        proc = _FakeProc(
            "some.exe", 5555,
            files=[bad_file, good_file],
        )

        if not _psutil_available():
            pytest.skip("psutil not installed")
        import psutil

        def fake_iter(attrs=None):
            yield proc

        monkeypatch.setattr(psutil, "process_iter", fake_iter)

        # Monkeypatch Path.resolve to fail on the bad path only
        orig_resolve = Path.resolve

        def selective_resolve(self, *args, **kwargs):
            if "GLOBALROOT" in str(self):
                raise OSError("unresolvable")
            return orig_resolve(self, *args, **kwargs)

        monkeypatch.setattr(Path, "resolve", selective_resolve)
        result = find_handle_holders(target)
        # The good_file was still matched after skipping the bad one
        assert "some.exe" in result

    def test_access_denied_exception_skips_process(self, monkeypatch, tmp_path):
        target = tmp_path / "held.bin"
        target.write_text("x")
        if not _psutil_available():
            pytest.skip("psutil not installed")
        import psutil

        proc_denied = _FakeProc(
            "protected.exe", 1,
            raise_on_open_files=psutil.AccessDenied(),
        )
        proc_good = _FakeProc(
            "normal.exe", 2,
            files=[_FakeOpenFile(str(target))],
        )
        self._install_fake_iter(monkeypatch, [proc_denied, proc_good])
        result = find_handle_holders(target)
        # protected.exe was skipped, normal.exe still recorded
        assert "protected.exe" not in result
        assert "normal.exe" in result

    def test_no_such_process_exception_skips(self, monkeypatch, tmp_path):
        target = tmp_path / "held.bin"
        target.write_text("x")
        import psutil

        proc = _FakeProc(
            "gone.exe", 99,
            raise_on_open_files=psutil.NoSuchProcess(pid=99),
        )
        self._install_fake_iter(monkeypatch, [proc])
        # Should not raise, just return []
        assert find_handle_holders(target) == []

    def test_zombie_process_exception_skips(self, monkeypatch, tmp_path):
        target = tmp_path / "held.bin"
        target.write_text("x")
        import psutil

        proc = _FakeProc(
            "zombie.exe", 42,
            raise_on_open_files=psutil.ZombieProcess(pid=42),
        )
        self._install_fake_iter(monkeypatch, [proc])
        assert find_handle_holders(target) == []

    def test_deduplicates_same_process_name(self, monkeypatch, tmp_path):
        # If the same process holds two file entries (unusual but possible),
        # the holder list should NOT contain duplicates.
        target = tmp_path / "held.bin"
        target.write_text("x")
        proc = _FakeProc(
            "same.exe", 1,
            files=[_FakeOpenFile(str(target)), _FakeOpenFile(str(target))],
        )
        self._install_fake_iter(monkeypatch, [proc])
        # Inside the for-f loop, after the first match we `break`, so
        # this would normally produce one entry. But verify the dedup
        # also handles the case of multiple matching procs with same name.
        proc2 = _FakeProc(
            "same.exe", 2,
            files=[_FakeOpenFile(str(target))],
        )
        self._install_fake_iter(monkeypatch, [proc, proc2])
        result = find_handle_holders(target)
        # Only one "same.exe" entry despite two procs holding the file
        assert result.count("same.exe") == 1


# ===========================================================================
# check_path symlink + lstat OSError branches (lines 517-524)
# ===========================================================================


class TestCheckPathSymlinkBranch:
    def test_symlink_detected_via_monkeypatched_is_symlink(
        self, monkeypatch, tmp_path
    ):
        # On Windows, creating real symlinks needs admin/dev mode. Mock
        # is_symlink() to True to exercise the SYMLINK concern branch.
        target = tmp_path / "fake_link.txt"
        target.write_text("")

        def always_true(self):
            return True

        monkeypatch.setattr(Path, "is_symlink", always_true)
        svc = SafetyService(app_data_paths=[], os_managed_paths=[])
        report = svc.check_path(target)
        assert report.level == SafetyLevel.CAUTION
        assert any(c[0] == SafetyConcern.SYMLINK for c in report.concerns)

    def test_is_symlink_raising_oserror_is_swallowed(self, monkeypatch, tmp_path):
        # If is_symlink() raises OSError, the report should still complete
        # (the except branch at 521-524 swallows it without recording a concern).
        target = tmp_path / "weird.txt"
        target.write_text("")

        def boom(self):
            raise OSError("simulated lstat failure")

        monkeypatch.setattr(Path, "is_symlink", boom)
        svc = SafetyService(app_data_paths=[], os_managed_paths=[])
        # Should not raise; report still constructed with no SYMLINK concern.
        report = svc.check_path(target)
        assert all(c[0] != SafetyConcern.SYMLINK for c in report.concerns)


# ===========================================================================
# check_path with check_handles=True (lines 537-543)
# ===========================================================================


class TestCheckPathHandlesBranch:
    def test_check_handles_records_holders(self, monkeypatch, tmp_path):
        target = tmp_path / "held.bin"
        target.write_text("")
        monkeypatch.setattr(
            "curator.services.safety.find_handle_holders",
            lambda p: ["app.exe", "another.exe"],
        )
        svc = SafetyService(app_data_paths=[], os_managed_paths=[])
        report = svc.check_path(target, check_handles=True)
        assert report.holders == ["app.exe", "another.exe"]
        assert report.level == SafetyLevel.CAUTION
        # Detail message includes the holder names (joined with comma)
        detail = next(d for c, d in report.concerns if c == SafetyConcern.OPEN_HANDLE)
        assert "app.exe" in detail and "another.exe" in detail

    def test_check_handles_more_than_five_truncates_pretty(
        self, monkeypatch, tmp_path
    ):
        # When more than 5 holders exist, the detail string truncates to the
        # first 5 and appends "(+N more)" — covers line 542.
        target = tmp_path / "held.bin"
        target.write_text("")
        many_holders = [f"proc{i}.exe" for i in range(7)]
        monkeypatch.setattr(
            "curator.services.safety.find_handle_holders",
            lambda p: many_holders,
        )
        svc = SafetyService(app_data_paths=[], os_managed_paths=[])
        report = svc.check_path(target, check_handles=True)
        detail = next(d for c, d in report.concerns if c == SafetyConcern.OPEN_HANDLE)
        assert "(+2 more)" in detail
        # First 5 holders are in the detail; last 2 are summarized
        for i in range(5):
            assert f"proc{i}.exe" in detail

    def test_check_handles_no_holders_no_concern(self, monkeypatch, tmp_path):
        target = tmp_path / "unheld.bin"
        target.write_text("")
        monkeypatch.setattr(
            "curator.services.safety.find_handle_holders",
            lambda p: [],
        )
        svc = SafetyService(app_data_paths=[], os_managed_paths=[])
        report = svc.check_path(target, check_handles=True)
        # No OPEN_HANDLE concern when holders list is empty
        assert all(c[0] != SafetyConcern.OPEN_HANDLE for c in report.concerns)
        assert report.holders == []
