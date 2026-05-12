"""Tests for v1.7.41 scripts/setup_dev_env.py helper functions + orchestration.

The script's side-effectful steps (create_venv, install_curator,
run_smoke_test) shell out to real subprocesses and aren't worth
testing in a unit context -- they'd take 30+ seconds each and just be
verifying that subprocess.run() works.

Instead, the unit tests focus on:

  * Pure helper functions (check_python_version, find_project_root,
    venv_python_path, venv_exists) -- testable without side effects
  * The argparse interface + dry-run mode -- verifies the
    orchestration glues together correctly without actually installing

Integration: a real install run is verified manually by the developer
(or in CI's setup phase); pytest doesn't need to redo that work.
"""

from __future__ import annotations

import platform
import subprocess
import sys
from pathlib import Path

import pytest


# Import the script module. It's at scripts/setup_dev_env.py at repo root.
# Add scripts/ to sys.path for the import.
import importlib.util

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "setup_dev_env.py"


@pytest.fixture(scope="module")
def setup_module():
    """Load scripts/setup_dev_env.py as an importable module."""
    spec = importlib.util.spec_from_file_location(
        "setup_dev_env_under_test", _SCRIPT_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# check_python_version
# ---------------------------------------------------------------------------


def test_check_python_version_passes_on_current(setup_module):
    """v1.7.41: the running test interpreter must be new enough.

    pytest itself only runs on Python >= 3.9-ish, and Curator requires
    >= 3.11. Either way, the test interpreter is comfortably above
    the minimum, so check_python_version should always return ok=True
    when run from the test suite.
    """
    ok, msg = setup_module.check_python_version()
    assert ok is True, f"Expected ok=True; got msg={msg!r}"
    assert "Python" in msg
    assert str(sys.executable) in msg


def test_check_python_version_min_constant(setup_module):
    """v1.7.41: MIN_PYTHON sanity -- should be (3, 11) per pyproject.toml.

    This guards against accidentally raising the minimum (which would
    silently lock out 3.11 users on next contributor run).
    """
    assert setup_module.MIN_PYTHON == (3, 11), (
        f"MIN_PYTHON should match pyproject's requires-python; "
        f"got {setup_module.MIN_PYTHON!r}"
    )


# ---------------------------------------------------------------------------
# find_project_root
# ---------------------------------------------------------------------------


def test_find_project_root_from_script_dir(setup_module):
    """v1.7.41: starting from scripts/ should find the repo root."""
    root = setup_module.find_project_root(_SCRIPT_PATH.parent)
    assert root is not None
    assert (root / "pyproject.toml").exists()
    assert root == _REPO_ROOT


def test_find_project_root_from_repo_root(setup_module):
    """v1.7.41: starting from the repo root itself should still find it."""
    root = setup_module.find_project_root(_REPO_ROOT)
    assert root == _REPO_ROOT


def test_find_project_root_returns_none_outside_project(setup_module, tmp_path):
    """v1.7.41: starting from a tmp dir (no pyproject.toml) returns None."""
    root = setup_module.find_project_root(tmp_path)
    assert root is None, (
        f"Should not find pyproject.toml from tmp_path={tmp_path!r}; got {root!r}"
    )


def test_find_project_root_walks_up_at_most_4_levels(setup_module, tmp_path):
    """v1.7.41: deep nested dirs without pyproject.toml return None.

    The 4-level cap protects against runaway walks on weird filesystem
    layouts where pyproject.toml might be very high up.
    """
    deep = tmp_path / "a" / "b" / "c" / "d" / "e" / "f"
    deep.mkdir(parents=True)
    root = setup_module.find_project_root(deep)
    assert root is None


# ---------------------------------------------------------------------------
# venv_python_path
# ---------------------------------------------------------------------------


def test_venv_python_path_platform_specific(setup_module, tmp_path):
    """v1.7.41: cross-platform venv binary path resolution.

    Windows: .venv\\Scripts\\python.exe
    POSIX:   .venv/bin/python
    """
    p = setup_module.venv_python_path(tmp_path)
    if platform.system() == "Windows":
        assert p == tmp_path / ".venv" / "Scripts" / "python.exe"
    else:
        assert p == tmp_path / ".venv" / "bin" / "python"


def test_venv_exists_negative(setup_module, tmp_path):
    """v1.7.41: venv_exists returns False when .venv is missing."""
    assert setup_module.venv_exists(tmp_path) is False


def test_venv_exists_positive(setup_module, tmp_path):
    """v1.7.41: venv_exists returns True when a venv python is present."""
    # Build the right directory + create a fake python binary
    venv_py = setup_module.venv_python_path(tmp_path)
    venv_py.parent.mkdir(parents=True)
    venv_py.touch()
    assert setup_module.venv_exists(tmp_path) is True


# ---------------------------------------------------------------------------
# PROFILES mapping
# ---------------------------------------------------------------------------


def test_profiles_has_three_named_profiles(setup_module):
    """v1.7.41: minimal / standard / full are the documented profiles."""
    assert set(setup_module.PROFILES.keys()) == {"minimal", "standard", "full"}


def test_profile_full_means_all(setup_module):
    """v1.7.41: --profile full maps to [all], which the pyproject defines as
    [dev,beta,cloud,organize,windows,gui,mcp]. We test the mapping
    here; the pyproject groups themselves are pinned there."""
    assert setup_module.PROFILES["full"] == "all"


def test_profile_minimal_means_dev_only(setup_module):
    """v1.7.41: --profile minimal is just [dev] -- the smallest profile
    that lets pytest run."""
    assert setup_module.PROFILES["minimal"] == "dev"


def test_profile_standard_includes_beta_and_organize(setup_module):
    """v1.7.41: --profile standard adds [beta,organize] to [dev] because
    those plugin groups are needed for non-GUI test paths."""
    assert "dev" in setup_module.PROFILES["standard"]
    assert "beta" in setup_module.PROFILES["standard"]
    assert "organize" in setup_module.PROFILES["standard"]


# ---------------------------------------------------------------------------
# main() orchestration (via subprocess + --dry-run)
# ---------------------------------------------------------------------------


def _run_setup_script(args: list[str]) -> subprocess.CompletedProcess:
    """Helper: run scripts/setup_dev_env.py as a subprocess."""
    return subprocess.run(
        [sys.executable, str(_SCRIPT_PATH), *args],
        capture_output=True, text=True, encoding="utf-8",
    )


def test_main_help_works():
    """v1.7.41: --help exits 0 and prints the profile options."""
    result = _run_setup_script(["--help"])
    assert result.returncode == 0
    assert "--profile" in result.stdout
    assert "minimal" in result.stdout
    assert "standard" in result.stdout
    assert "full" in result.stdout
    assert "--dry-run" in result.stdout


def test_main_dry_run_minimal_exits_zero():
    """v1.7.41: --dry-run --profile minimal prints the plan, exits 0."""
    result = _run_setup_script(["--dry-run", "--profile", "minimal", "--no-smoke"])
    assert result.returncode == 0, (
        f"--dry-run should exit 0; got {result.returncode}\n"
        f"stdout: {result.stdout[-500:]!r}\n"
        f"stderr: {result.stderr[-500:]!r}"
    )
    assert "Curator dev environment setup" in result.stdout
    assert "[dev]" in result.stdout  # The minimal profile's extras
    assert "Setup complete" in result.stdout


def test_main_dry_run_full_exits_zero():
    """v1.7.41: --dry-run --profile full prints the [all] command."""
    result = _run_setup_script(["--dry-run", "--profile", "full", "--no-smoke"])
    assert result.returncode == 0
    assert "[all]" in result.stdout


def test_main_dry_run_standard_exits_zero():
    """v1.7.41: --dry-run --profile standard prints the [dev,beta,organize] command."""
    result = _run_setup_script(["--dry-run", "--profile", "standard", "--no-smoke"])
    assert result.returncode == 0
    assert "[dev,beta,organize]" in result.stdout


def test_main_default_profile_is_full():
    """v1.7.41: omitting --profile defaults to 'full'."""
    result = _run_setup_script(["--dry-run", "--no-smoke"])
    assert result.returncode == 0
    assert "profile: full" in result.stdout


def test_main_invalid_profile_rejected():
    """v1.7.41: argparse rejects unknown profile values."""
    result = _run_setup_script(["--profile", "exotic"])
    assert result.returncode != 0
    # argparse prints to stderr
    assert "invalid choice" in result.stderr or "exotic" in result.stderr


def test_main_dry_run_skips_smoke_message():
    """v1.7.41: --no-smoke surfaces 'SKIPPED' marker in the output."""
    result = _run_setup_script(["--dry-run", "--no-smoke"])
    assert result.returncode == 0
    assert "SKIPPED" in result.stdout
