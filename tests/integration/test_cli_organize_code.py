"""CLI integration tests for `curator organize --type code` (Phase Gamma F5)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from curator.cli.main import app
from curator.services.organize import OrganizeService
from curator.services.safety import SafetyService


pytestmark = pytest.mark.integration


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "code_organize.db"


def _isolated_safety(monkeypatch) -> None:
    real_init = OrganizeService.__init__
    def patched_init(self, file_repo, safety, *args, **kwargs):
        loose = SafetyService(app_data_paths=[], os_managed_paths=[])
        real_init(self, file_repo, loose, *args, **kwargs)
    monkeypatch.setattr(OrganizeService, "__init__", patched_init)


def _make_project(root: Path, vcs: str = ".git") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / vcs).mkdir()
    return root


def _add_files(root: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestCodeTypeValidation:
    def test_help_lists_code_type(self, runner, db_path):
        result = runner.invoke(
            app, ["--db", str(db_path), "organize", "--help"],
        )
        assert result.exit_code == 0
        # The --type help text mentions all four pipelines.
        assert "code" in result.stdout
        assert "VCS" in result.stdout or "project" in result.stdout.lower()

    def test_invalid_type_still_rejected(self, runner, db_path, tmp_path):
        result = runner.invoke(
            app,
            ["--db", str(db_path), "organize", "local",
             "--type", "bogus", "--target", str(tmp_path / "out")],
        )
        assert result.exit_code == 2
        assert "Unknown --type" in result.stderr or "Unknown --type" in result.stdout

    def test_code_type_requires_target(self, runner, db_path):
        result = runner.invoke(
            app,
            ["--db", str(db_path), "organize", "local", "--type", "code"],
        )
        assert result.exit_code == 2


# ---------------------------------------------------------------------------
# Plan-mode end-to-end with a real scan
# ---------------------------------------------------------------------------


class TestCodeOrganizePlan:
    def test_plan_proposes_destinations_for_project_files(
        self, runner, db_path, tmp_path, monkeypatch
    ):
        _isolated_safety(monkeypatch)

        # Build a code library: two real projects + some loose files.
        lib = tmp_path / "code_lib"
        py_proj = _make_project(lib / "myapp")
        _add_files(py_proj, {
            "main.py": "print('hi')",
            "lib/util.py": "x = 1",
            "tests/test_main.py": "pass",
            "README.md": "docs",
        })
        rust_proj = _make_project(lib / "rusty_thing")
        _add_files(rust_proj, {
            "src/main.rs": "fn main() {}",
            "Cargo.toml": "[package]",  # ignored ext
            "src/lib.rs": "// lib",
        })
        # Loose file outside any project.
        (lib / "scratch_note.txt").write_text("just a note")

        # Scan the library.
        scan = runner.invoke(
            app, ["--db", str(db_path), "scan", "local", str(lib)],
        )
        assert scan.exit_code == 0, scan.stdout

        # Plan with --type code + --show-files to get individual file proposals.
        target = tmp_path / "Code"
        result = runner.invoke(
            app,
            ["--json", "--db", str(db_path),
             "organize", "local",
             "--root", str(lib),
             "--type", "code",
             "--target", str(target),
             "--show-files"],
        )
        assert result.exit_code == 0, result.stdout + (result.stderr or "")
        payload = json.loads(result.stdout)

        # The proposals come from CAUTION bucket for code mode (project
        # files trigger the project-root safety concern, so they're
        # CAUTION even though we propose destinations for them).
        caution_files = payload.get("caution", {}).get("files", [])
        proposal_paths = [
            f["proposed_destination"]
            for f in caution_files
            if f.get("proposed_destination")
        ]

        # Files inside myapp/ should be proposed under Code/Python/myapp/...
        py_proposals = [p for p in proposal_paths if "Python" in p and "myapp" in p]
        assert len(py_proposals) >= 1, f"expected Python/myapp proposals, got {proposal_paths}"
        # Files inside rusty_thing/ should be proposed under Code/Rust/rusty_thing/...
        rs_proposals = [p for p in proposal_paths if "Rust" in p and "rusty_thing" in p]
        assert len(rs_proposals) >= 1, f"expected Rust/rusty_thing proposals, got {proposal_paths}"

    def test_loose_file_outside_project_not_proposed(
        self, runner, db_path, tmp_path, monkeypatch
    ):
        _isolated_safety(monkeypatch)

        lib = tmp_path / "code_lib"
        proj = _make_project(lib / "alpha")
        _add_files(proj, {"main.py": "code"})
        # Loose .py file at the lib root, NOT inside the project.
        (lib / "scratch.py").write_text("loose")

        runner.invoke(app, ["--db", str(db_path), "scan", "local", str(lib)])

        target = tmp_path / "Code"
        result = runner.invoke(
            app,
            ["--json", "--db", str(db_path),
             "organize", "local",
             "--root", str(lib),
             "--type", "code",
             "--target", str(target),
             "--show-files"],
        )
        payload = json.loads(result.stdout)
        # In code mode, project files are CAUTION; loose files are SAFE.
        # We check both buckets to confirm scratch.py wasn't proposed.
        all_files = (
            payload.get("safe", {}).get("files", [])
            + payload.get("caution", {}).get("files", [])
        )
        proposal_paths = [
            f["proposed_destination"]
            for f in all_files
            if f.get("proposed_destination")
        ]

        # No proposal should contain "scratch.py" (it's not in any project).
        scratch_proposed = [p for p in proposal_paths if "scratch.py" in p]
        assert scratch_proposed == [], (
            f"scratch.py should not be proposed; got {scratch_proposed}"
        )
