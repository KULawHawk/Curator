"""Unit tests for CodeProjectService (Phase Gamma F5, v0.31).

Covers:
    * is_project_root: detects .git, .hg, .svn; ignores non-VCS dirs
    * find_projects: discovers multiple projects, prunes at boundaries,
      handles nested submodules (parent wins), handles missing dirs,
      sorts deterministically
    * analyze_project: counts files, infers language, computes total
      size and last_modified, skips SKIP_DIR_NAMES, skips IGNORED_EXTENSIONS
    * Language inference: dominant language wins; alphabetic tiebreaker;
      Unknown for empty / unrecognized projects; Markdown doesn't
      outvote real code
    * propose_destination: builds {target}/{lang}/{name}/{rel_subpath};
      returns None for files outside the project
    * find_project_containing: most-specific (longest root_path) wins
"""

from __future__ import annotations

from pathlib import Path

import pytest

from curator.services.code_project import (
    IGNORED_EXTENSIONS,
    LANGUAGE_BY_EXTENSION,
    SKIP_DIR_NAMES,
    VCS_MARKERS,
    CodeProject,
    CodeProjectService,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_project(root: Path, vcs: str = ".git") -> Path:
    """Create a directory with a VCS marker. Returns the project root."""
    root.mkdir(parents=True, exist_ok=True)
    (root / vcs).mkdir()
    return root


def _add_files(root: Path, files: dict[str, str]) -> None:
    """Bulk-create files under ``root``. ``files`` maps relpath -> content."""
    for rel, content in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)


@pytest.fixture
def service():
    return CodeProjectService()


# ===========================================================================
# is_project_root
# ===========================================================================


class TestIsProjectRoot:
    def test_detects_git(self, service, tmp_path):
        root = _make_project(tmp_path / "myrepo", ".git")
        assert service.is_project_root(root) == "git"

    def test_detects_hg(self, service, tmp_path):
        root = _make_project(tmp_path / "myrepo", ".hg")
        assert service.is_project_root(root) == "hg"

    def test_detects_svn(self, service, tmp_path):
        root = _make_project(tmp_path / "myrepo", ".svn")
        assert service.is_project_root(root) == "svn"

    def test_no_marker_returns_none(self, service, tmp_path):
        plain = tmp_path / "plain"
        plain.mkdir()
        (plain / "README.md").write_text("hi")
        assert service.is_project_root(plain) is None

    def test_nonexistent_returns_none(self, service, tmp_path):
        assert service.is_project_root(tmp_path / "does_not_exist") is None

    def test_file_path_returns_none(self, service, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("not a dir")
        assert service.is_project_root(f) is None

    def test_marker_must_be_directory_not_file(self, service, tmp_path):
        # A file named ".git" (e.g. a submodule pointer) shouldn't count
        # because we only accept dirs as markers.
        weird = tmp_path / "weird"
        weird.mkdir()
        (weird / ".git").write_text("gitdir: ../.git/modules/weird\n")
        # The submodule pointer file is NOT a project root by itself.
        assert service.is_project_root(weird) is None


# ===========================================================================
# find_projects
# ===========================================================================


class TestFindProjects:
    def test_empty_tree_returns_empty(self, service, tmp_path):
        assert service.find_projects(tmp_path) == []

    def test_finds_single_project(self, service, tmp_path):
        proj = _make_project(tmp_path / "alpha")
        _add_files(proj, {"main.py": "print('hi')"})
        results = service.find_projects(tmp_path)
        assert len(results) == 1
        assert results[0].project_name == "alpha"
        assert results[0].vcs_type == "git"

    def test_finds_multiple_sibling_projects(self, service, tmp_path):
        _make_project(tmp_path / "alpha")
        _make_project(tmp_path / "bravo")
        _make_project(tmp_path / "charlie")
        results = service.find_projects(tmp_path)
        assert len(results) == 3
        # Sorted deterministically.
        names = [p.project_name for p in results]
        assert names == sorted(names)

    def test_prunes_at_project_boundary(self, service, tmp_path):
        # An outer project containing a nested .git/ should NOT yield
        # the nested one as a separate project.
        outer = _make_project(tmp_path / "outer")
        _make_project(outer / "vendor" / "submodule")
        results = service.find_projects(tmp_path)
        assert len(results) == 1
        assert results[0].project_name == "outer"

    def test_finds_projects_at_different_depths(self, service, tmp_path):
        # Two projects at different depths under the search root.
        _make_project(tmp_path / "alpha")
        _make_project(tmp_path / "nested" / "deep" / "bravo")
        results = service.find_projects(tmp_path)
        assert len(results) == 2
        names = sorted(p.project_name for p in results)
        assert names == ["alpha", "bravo"]

    def test_skips_skip_dir_names_during_descent(self, service, tmp_path):
        # A project hidden inside node_modules should NOT be found.
        nm = tmp_path / "node_modules" / "lodash"
        _make_project(nm)
        results = service.find_projects(tmp_path)
        assert results == []

    def test_search_root_is_a_project(self, service, tmp_path):
        # If the search root itself is a project, that's the one project found.
        _make_project(tmp_path)
        results = service.find_projects(tmp_path)
        assert len(results) == 1
        assert results[0].root_path == tmp_path

    def test_nonexistent_root_returns_empty(self, service, tmp_path):
        assert service.find_projects(tmp_path / "nope") == []


# ===========================================================================
# analyze_project
# ===========================================================================


class TestAnalyzeProject:
    def test_counts_files_and_size(self, service, tmp_path):
        proj = _make_project(tmp_path / "alpha")
        _add_files(proj, {
            "main.py": "x" * 100,
            "lib/util.py": "y" * 50,
            "README.md": "z" * 10,
        })
        result = service.analyze_project(proj)
        assert result.file_count == 3
        # 100 + 50 + 10 == 160 (excluding the .git marker which is empty)
        assert result.total_size == 160

    def test_python_project_inferred_correctly(self, service, tmp_path):
        proj = _make_project(tmp_path / "py_app")
        _add_files(proj, {
            "main.py": "code",
            "lib/a.py": "code",
            "lib/b.py": "code",
            "README.md": "docs",
        })
        result = service.analyze_project(proj)
        assert result.primary_language == "Python"
        assert result.language_breakdown["Python"] == 3

    def test_typescript_project_inferred(self, service, tmp_path):
        proj = _make_project(tmp_path / "ts_app")
        _add_files(proj, {
            "src/main.ts": "code",
            "src/util.ts": "code",
            "src/types.tsx": "code",
            "README.md": "docs",
        })
        result = service.analyze_project(proj)
        assert result.primary_language == "TypeScript"

    def test_skips_node_modules_during_analysis(self, service, tmp_path):
        # A JS project with a huge node_modules dir of Python files
        # shouldn't be classified as Python.
        proj = _make_project(tmp_path / "js_app")
        _add_files(proj, {
            "src/main.js": "code",
            "src/util.js": "code",
            # Adversarial: many .py files inside node_modules.
            "node_modules/fakepkg/install.py": "x",
            "node_modules/fakepkg/setup.py": "x",
            "node_modules/fakepkg/build.py": "x",
            "node_modules/fakepkg/test.py": "x",
        })
        result = service.analyze_project(proj)
        assert result.primary_language == "JavaScript"
        # node_modules files were not even counted in file_count.
        # 2 .js files + 0 from node_modules == 2.
        assert result.file_count == 2

    def test_skips_pycache(self, service, tmp_path):
        proj = _make_project(tmp_path / "pyapp")
        _add_files(proj, {
            "main.py": "code",
            "__pycache__/main.cpython-311.pyc": "binary",
        })
        result = service.analyze_project(proj)
        # pyc file in __pycache__ is in a SKIP dir.
        assert result.file_count == 1

    def test_unknown_language_for_empty_project(self, service, tmp_path):
        proj = _make_project(tmp_path / "empty")
        result = service.analyze_project(proj)
        assert result.primary_language == "Unknown"
        assert result.file_count == 0

    def test_unknown_language_for_only_ignored_extensions(self, service, tmp_path):
        # A "project" containing only PDFs and JPEGs and lock files.
        proj = _make_project(tmp_path / "media")
        _add_files(proj, {
            "doc.pdf": "x",
            "photo.jpg": "x",
            "package.lock": "{}",
        })
        result = service.analyze_project(proj)
        assert result.primary_language == "Unknown"

    def test_alphabetic_tiebreaker(self, service, tmp_path):
        # Equal counts of Go and Rust -> Go wins alphabetically.
        proj = _make_project(tmp_path / "tie")
        _add_files(proj, {
            "a.go": "x", "b.go": "x",
            "c.rs": "x", "d.rs": "x",
        })
        result = service.analyze_project(proj)
        assert result.primary_language == "Go"

    def test_markdown_does_not_outvote_real_code(self, service, tmp_path):
        # Many .md files + a few .py -> Python should still win.
        proj = _make_project(tmp_path / "doc_heavy")
        files = {f"docs/d{i}.md": "x" for i in range(20)}
        files["src/main.py"] = "x"
        files["src/util.py"] = "x"
        _add_files(proj, files)
        result = service.analyze_project(proj)
        assert result.primary_language == "Python"

    def test_records_last_modified(self, service, tmp_path):
        proj = _make_project(tmp_path / "dated")
        _add_files(proj, {"main.py": "x"})
        result = service.analyze_project(proj)
        assert result.last_modified is not None

    def test_records_vcs_type(self, service, tmp_path):
        proj = _make_project(tmp_path / "hgproj", vcs=".hg")
        _add_files(proj, {"main.py": "x"})
        result = service.analyze_project(proj)
        assert result.vcs_type == "hg"


# ===========================================================================
# propose_destination
# ===========================================================================


class TestProposeDestination:
    def test_basic_destination(self, service, tmp_path):
        proj = _make_project(tmp_path / "myapp")
        _add_files(proj, {"main.py": "x"})
        analysis = service.analyze_project(proj)
        dest = service.propose_destination(
            analysis,
            file_path=proj / "main.py",
            target_root="/Code",
        )
        assert dest == Path("/Code/Python/myapp/main.py")

    def test_preserves_subpath_within_project(self, service, tmp_path):
        proj = _make_project(tmp_path / "deep")
        _add_files(proj, {"src/lib/util.py": "x"})
        analysis = service.analyze_project(proj)
        dest = service.propose_destination(
            analysis,
            file_path=proj / "src" / "lib" / "util.py",
            target_root="/Code",
        )
        assert dest == Path("/Code/Python/deep/src/lib/util.py")

    def test_file_outside_project_returns_none(self, service, tmp_path):
        proj = _make_project(tmp_path / "alpha")
        _add_files(proj, {"main.py": "x"})
        other = tmp_path / "outside.txt"
        other.write_text("not in project")
        analysis = service.analyze_project(proj)
        result = service.propose_destination(
            analysis,
            file_path=other,
            target_root="/Code",
        )
        assert result is None


# ===========================================================================
# find_project_containing
# ===========================================================================


class TestFindProjectContaining:
    def test_finds_correct_project(self, service, tmp_path):
        a = _make_project(tmp_path / "alpha")
        b = _make_project(tmp_path / "bravo")
        _add_files(a, {"main.py": "x"})
        _add_files(b, {"main.py": "x"})
        projects = service.find_projects(tmp_path)

        result_a = service.find_project_containing(a / "main.py", projects)
        assert result_a is not None
        assert result_a.project_name == "alpha"

        result_b = service.find_project_containing(b / "main.py", projects)
        assert result_b is not None
        assert result_b.project_name == "bravo"

    def test_returns_none_for_unrelated_file(self, service, tmp_path):
        a = _make_project(tmp_path / "alpha")
        _add_files(a, {"main.py": "x"})
        projects = service.find_projects(tmp_path)
        unrelated = tmp_path / "elsewhere.txt"
        unrelated.write_text("x")
        assert service.find_project_containing(unrelated, projects) is None


# ===========================================================================
# Module-level constants
# ===========================================================================


class TestConstants:
    def test_vcs_markers_includes_git(self):
        assert ".git" in VCS_MARKERS

    def test_skip_dir_names_includes_node_modules(self):
        assert "node_modules" in SKIP_DIR_NAMES

    def test_skip_dir_names_includes_vcs_markers(self):
        # SKIP_DIR_NAMES should contain VCS_MARKERS so analyze_project
        # doesn't recurse INTO .git/objects/ etc.
        for marker in VCS_MARKERS:
            assert marker in SKIP_DIR_NAMES

    def test_python_extensions_mapped(self):
        assert LANGUAGE_BY_EXTENSION[".py"] == "Python"

    def test_ignored_extensions_includes_pyc(self):
        assert ".pyc" in IGNORED_EXTENSIONS
