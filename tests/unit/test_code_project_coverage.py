"""Focused coverage tests for services/code_project.py.

Sub-ship v1.7.105 of the Coverage Sweep arc.

Closes 17 uncovered lines + 4 partial branches, all defensive OSError
boundaries plus a few specific filter arms:

* Lines 201-202: `is_project_root`'s `except OSError`.
* Lines 229-230: `find_projects`'s outer-loop `except OSError`.
* Lines 242-243: `find_projects`'s `except OSError` around analyze.
* Lines 252-253: `find_projects`'s iterdir `except OSError`.
* Line 256: `if not child.is_dir(): continue` True arm (skip non-dir).
* Lines 296-297: `analyze_project`'s `except OSError` on stat.
* Line 307: `analyze_project`'s `continue` when ext is unrecognized.
* Lines 345-346: `_iter_project_files` iterdir `except OSError`.
* Branch 353->347: `child.is_file()` False (skip non-file non-dir).
* Lines 355-356: `_iter_project_files` is_dir/is_file `except OSError`.
* Line 371: `_pick_primary_language` returns "Unknown" when top_count
  is 0 (counter has entries with value 0 only).
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from curator.services.code_project import (
    CodeProject,
    CodeProjectService,
    LANGUAGE_BY_EXTENSION,
)


# ---------------------------------------------------------------------------
# is_project_root OSError on marker check (201-202)
# ---------------------------------------------------------------------------


def test_is_project_root_swallows_oserror_on_marker_check(
    tmp_path, monkeypatch,
):
    # Lines 201-202: `(p / marker).is_dir()` raises OSError → continue
    # → try next marker → return None.
    proj = tmp_path / "proj"
    proj.mkdir()
    orig_is_dir = Path.is_dir

    def selective_is_dir(self, *args, **kwargs):
        if self.name.startswith("."):
            raise OSError("simulated permission denied")
        return orig_is_dir(self, *args, **kwargs)
    monkeypatch.setattr(Path, "is_dir", selective_is_dir)

    svc = CodeProjectService()
    # All 5 markers raise → return None (no VCS detected).
    assert svc.is_project_root(proj) is None


# ---------------------------------------------------------------------------
# find_projects OSError boundaries (229-230, 242-243, 252-253) + line 256
# ---------------------------------------------------------------------------


def test_find_projects_outer_is_project_root_raises(tmp_path, monkeypatch):
    # Lines 229-230: is_project_root raises OSError → continue (skip
    # this dir entirely).
    svc = CodeProjectService()
    monkeypatch.setattr(
        svc, "is_project_root",
        lambda *a, **kw: (_ for _ in ()).throw(OSError("boom")),
    )
    # Function returns empty list without raising.
    assert svc.find_projects(tmp_path) == []


def test_find_projects_analyze_raises_logs_and_continues(
    tmp_path, monkeypatch,
):
    # Lines 242-246: analyze_project raises OSError → logger.debug,
    # don't append; loop continues. Set up a real .git dir so the
    # first scan finds a project, but make analyze raise.
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".git").mkdir()

    svc = CodeProjectService()
    monkeypatch.setattr(
        svc, "analyze_project",
        lambda *a, **kw: (_ for _ in ()).throw(OSError("analyze fail")),
    )
    result = svc.find_projects(tmp_path)
    # No projects appended despite VCS marker present.
    assert result == []


def test_find_projects_iterdir_raises_continues(tmp_path, monkeypatch):
    # Lines 252-253: current.iterdir() raises OSError → continue.
    # tmp_path has no .git so iterdir is reached; selectively raise.
    orig_iterdir = Path.iterdir

    def boom_iterdir(self):
        if self == tmp_path:
            raise OSError("permission denied")
        return orig_iterdir(self)
    monkeypatch.setattr(Path, "iterdir", boom_iterdir)

    svc = CodeProjectService()
    assert svc.find_projects(tmp_path) == []


def test_find_projects_skips_non_directory_children(tmp_path):
    # Line 256: `if not child.is_dir(): continue` True arm — children
    # that are files are skipped during the descent walk.
    (tmp_path / "loose_file.txt").write_text("x")
    sub = tmp_path / "subdir"
    sub.mkdir()
    # No .git anywhere → no projects found. The file gets is_dir-checked.
    svc = CodeProjectService()
    assert svc.find_projects(tmp_path) == []


# ---------------------------------------------------------------------------
# analyze_project per-file defensives (296-297, 307)
# ---------------------------------------------------------------------------


def test_analyze_project_stat_oserror_skips_file(tmp_path, monkeypatch):
    # Lines 295-297: path.stat() raises OSError inside `analyze_project`'s
    # per-file loop → continue. The file_count was already incremented
    # before stat, but size/mtime/language are NOT updated for this file.
    #
    # Monkeypatching `Path.stat` globally also breaks `Path.is_file()`
    # (which calls stat internally) and would prevent the file from
    # being yielded by `_iter_project_files` in the first place.
    # Instead: monkeypatch `_iter_project_files` to yield a Path whose
    # `stat()` raises selectively.
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".git").mkdir()
    bad_file = proj / "boom.py"
    bad_file.write_text("x")

    svc = CodeProjectService()
    monkeypatch.setattr(svc, "_iter_project_files", lambda root: iter([bad_file]))

    # Now monkeypatch Path.stat to raise specifically for bad_file.
    orig_stat = Path.stat

    def boom_stat(self, *args, **kwargs):
        if self == bad_file:
            raise OSError("stat blocked")
        return orig_stat(self, *args, **kwargs)
    monkeypatch.setattr(Path, "stat", boom_stat)

    project = svc.analyze_project(proj)
    # File was counted (file_count += 1 happens BEFORE the stat call)
    # but stat failed → no size, no language.
    assert project.file_count == 1
    assert project.total_size == 0
    assert "Python" not in project.language_breakdown


def test_analyze_project_unrecognized_extension_continues(tmp_path):
    # Line 307: ext in LANGUAGE_BY_EXTENSION returns None → continue,
    # the file doesn't contribute to language_counter.
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".git").mkdir()
    (proj / "weird.unknown_ext_xyz").write_text("x")

    svc = CodeProjectService()
    project = svc.analyze_project(proj)
    # No language was recorded; primary stays "Unknown".
    assert project.primary_language == "Unknown"


# ---------------------------------------------------------------------------
# _iter_project_files defensives (345-346, 353->347, 355-356)
# ---------------------------------------------------------------------------


def test_iter_project_files_iterdir_oserror_skips(tmp_path, monkeypatch):
    # Lines 345-346: current.iterdir() raises OSError → continue.
    # Empty directory walk completes without yielding.
    bad_dir = tmp_path / "blocked"
    bad_dir.mkdir()

    orig_iterdir = Path.iterdir

    def boom_iterdir(self):
        if self == bad_dir:
            raise OSError("iter blocked")
        return orig_iterdir(self)
    monkeypatch.setattr(Path, "iterdir", boom_iterdir)

    svc = CodeProjectService()
    files = list(svc._iter_project_files(bad_dir))
    assert files == []


def test_iter_project_files_skips_non_file_non_dir(tmp_path, monkeypatch):
    # Branch 353->347: child is neither dir nor file (e.g. a broken
    # symlink that is_dir()=False AND is_file()=False) → fall through
    # without yielding, continue to next child.
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "real_file.py").write_text("x")

    # Force one child to look like neither file nor dir.
    orig_is_dir = Path.is_dir
    orig_is_file = Path.is_file
    weird = proj / "weird_entity"
    weird.write_text("data")  # actually a file on disk

    def fake_is_dir(self):
        if self == weird:
            return False
        return orig_is_dir(self)

    def fake_is_file(self):
        if self == weird:
            return False
        return orig_is_file(self)
    monkeypatch.setattr(Path, "is_dir", fake_is_dir)
    monkeypatch.setattr(Path, "is_file", fake_is_file)

    svc = CodeProjectService()
    files = list(svc._iter_project_files(proj))
    # Only the real .py file came through; the spoofed entity was skipped.
    names = [f.name for f in files]
    assert "real_file.py" in names
    assert "weird_entity" not in names


def test_iter_project_files_is_dir_oserror_skips(tmp_path, monkeypatch):
    # Lines 355-356: child.is_dir() raises OSError → continue.
    proj = tmp_path / "proj"
    proj.mkdir()
    bad = proj / "thing"
    bad.write_text("x")

    orig_is_dir = Path.is_dir

    def boom_is_dir(self):
        if self == bad:
            raise OSError("blocked")
        return orig_is_dir(self)
    monkeypatch.setattr(Path, "is_dir", boom_is_dir)

    svc = CodeProjectService()
    files = list(svc._iter_project_files(proj))
    # The blocked entity was skipped.
    assert [f.name for f in files] == []


# ---------------------------------------------------------------------------
# _pick_primary_language: counter with all-zero values (371)
# ---------------------------------------------------------------------------


def test_pick_primary_language_all_zero_counts_returns_unknown():
    # Line 371: counter has entries but every value is 0 → ranked[0]
    # has count 0 → return "Unknown". The non-zero case is well-
    # covered by other tests.
    counter: Counter = Counter()
    counter["Markdown"] = 0  # registered-but-not-weighted (lines 310-311)
    counter["Documentation"] = 0
    assert CodeProjectService._pick_primary_language(counter) == "Unknown"
