#!/usr/bin/env python3
"""scripts/setup_dev_env.py  (v1.7.41)

One-shot developer environment setup for Curator. Run this from a fresh
clone to go from zero to a working dev install + smoke-tested baseline.

Usage::

    python scripts/setup_dev_env.py [options]

Options::

    --profile {minimal,standard,full}
        Which dependency profile to install. Defaults to 'full'.
        - minimal:  [dev]                              ~6 packages
        - standard: [dev,beta,organize]                ~20 packages
        - full:     [all] = [dev,beta,cloud,organize,
                             windows,gui,mcp]          ~40+ packages

    --force
        Recreate .venv even if it exists. Otherwise the script reuses the
        existing venv (idempotent).

    --no-smoke
        Skip the post-install smoke test (collect-only on tests/unit/).
        Speeds up the script by ~2 seconds; useful for CI where pytest
        gets run later anyway.

    --dry-run
        Print every step but don't execute. Useful for inspecting what
        would happen before running for real.

What this script does
---------------------

1. **Sanity checks** — Python >= 3.11, running from a clean repo root
   (pyproject.toml present), git working tree state reported but not
   blocking.
2. **Venv** — creates ``.venv/`` via ``python -m venv``. Skips if it
   already exists (unless ``--force``). Always uses the system Python
   that ran *this* script as the venv's base, so version consistency
   is preserved.
3. **Install** — ``pip install -e ".[<profile>]"`` against the venv's
   pip. Profile selection happens via the standard
   ``project.optional-dependencies`` groups in ``pyproject.toml``.
4. **Smoke test** — collects (does NOT run) ``tests/unit/`` to verify
   the install can at least find all the imports. Skipped on
   ``--no-smoke``.
5. **Report** — clear final summary with the venv path, Python version
   inside the venv, and the next-step command to actually run pytest.

Cross-platform notes
--------------------

Detects POSIX vs Windows and chooses the correct venv binary path
(``.venv/Scripts/python.exe`` on Windows, ``.venv/bin/python`` elsewhere).
Curator is Windows-first today, but the script doesn't gate on platform.

Why this exists
---------------

The manual setup steps (check Python, create venv, activate, pip install
with the right extras, run pytest to confirm) are common knowledge but
easy to fumble. New contributors -- or future-me on a fresh machine --
should get a green smoke test in one command, with clear errors if
something's wrong with the environment.

Acceptable failure modes
------------------------

* Python too old -> clear error, exit 2.
* pyproject.toml missing -> clear error, exit 3.
* Venv create fails (disk full, permissions) -> surface stderr from
  ``python -m venv``, exit 4.
* pip install fails -> surface stderr, exit 5.
* Smoke test fails -> surface stderr but don't exit non-zero; the user
  has a working install, just one with a broken test (rare; usually a
  signal of an in-progress branch).
"""

from __future__ import annotations

import argparse
import platform
import subprocess
import sys
from pathlib import Path

MIN_PYTHON = (3, 11)
PROFILES = {
    "minimal":  "dev",
    "standard": "dev,beta,organize",
    "full":     "all",
}


# ---------------------------------------------------------------------------
# Sanity checks (pure functions, importable, testable)
# ---------------------------------------------------------------------------


def check_python_version() -> tuple[bool, str]:
    """Verify the running Python is >= MIN_PYTHON.

    Returns (ok, message). Does not raise so callers can decide whether
    to exit or just warn.
    """
    cur = sys.version_info[:2]
    if cur < MIN_PYTHON:
        return False, (
            f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ required; "
            f"running {cur[0]}.{cur[1]}.{sys.version_info[2]} "
            f"at {sys.executable!r}"
        )
    return True, f"Python {cur[0]}.{cur[1]}.{sys.version_info[2]} at {sys.executable}"


def find_project_root(start: Path | None = None) -> Path | None:
    """Walk up from ``start`` (default: this script's dir) looking for pyproject.toml.

    Returns the directory containing pyproject.toml, or None if not found
    within 4 levels up. The 4-level cap protects against runaway walks
    on weird filesystem layouts.
    """
    cur = (start or Path(__file__).resolve().parent)
    for _ in range(5):  # script dir + up to 4 parents
        if (cur / "pyproject.toml").exists():
            return cur
        if cur.parent == cur:  # filesystem root
            return None
        cur = cur.parent
    return None


def venv_python_path(root: Path) -> Path:
    """Return the path to the venv's python interpreter.

    Cross-platform: Windows uses Scripts\\python.exe, POSIX uses bin/python.
    """
    if platform.system() == "Windows":
        return root / ".venv" / "Scripts" / "python.exe"
    return root / ".venv" / "bin" / "python"


def venv_exists(root: Path) -> bool:
    """True iff a venv with a python binary exists at the expected location."""
    return venv_python_path(root).is_file()


# ---------------------------------------------------------------------------
# Side-effectful steps (each takes a `dry_run` flag for inspection mode)
# ---------------------------------------------------------------------------


def create_venv(root: Path, *, dry_run: bool = False) -> int:
    """Create .venv/ at the project root. Returns subprocess exit code."""
    cmd = [sys.executable, "-m", "venv", str(root / ".venv")]
    print(f"  $ {' '.join(cmd)}")
    if dry_run:
        return 0
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  stderr: {result.stderr.strip()}", file=sys.stderr)
    return result.returncode


def install_curator(
    venv_python: Path,
    profile: str,
    *,
    dry_run: bool = False,
) -> int:
    """pip install -e ".[<extras>]" using the venv's pip. Returns exit code.

    The profile -> extras mapping comes from :data:`PROFILES`.
    """
    extras = PROFILES.get(profile, profile)  # allow raw extras strings too
    spec = f".[{extras}]"
    cmd = [str(venv_python), "-m", "pip", "install", "-e", spec]
    print(f"  $ {' '.join(cmd)}")
    if dry_run:
        return 0
    # Stream pip's output so the user sees progress on slow nets
    result = subprocess.run(cmd)
    return result.returncode


def run_smoke_test(venv_python: Path, *, dry_run: bool = False) -> int:
    """Run a fast smoke test (collect-only on tests/unit/) using the venv's python.

    Collect-only verifies all test imports resolve -- catches missing
    deps without spending the seconds it takes to actually execute the
    tests. Full pytest is the user's next step.
    """
    cmd = [
        str(venv_python), "-m", "pytest",
        "tests/unit/", "--collect-only", "-q",
    ]
    print(f"  $ {' '.join(cmd)}")
    if dry_run:
        return 0
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  pytest stdout (last 500 chars):", file=sys.stderr)
        print(f"  {result.stdout[-500:]}", file=sys.stderr)
        print(f"  pytest stderr:", file=sys.stderr)
        print(f"  {result.stderr.strip()[:500]}", file=sys.stderr)
    else:
        # Show the trailing "N tests collected" line for confidence
        last_line = result.stdout.strip().split("\n")[-1]
        print(f"  {last_line}")
    return result.returncode


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Set up a Curator development environment "
            "(venv + editable install + smoke test)."
        ),
    )
    parser.add_argument(
        "--profile", choices=list(PROFILES.keys()), default="full",
        help="Dependency profile (default: full).",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Recreate .venv even if it already exists.",
    )
    parser.add_argument(
        "--no-smoke", action="store_true",
        help="Skip the post-install smoke test.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print every step but don't execute.",
    )
    args = parser.parse_args(argv)

    print("=" * 70)
    print("Curator dev environment setup")
    print("=" * 70)

    # Step 1: Python version
    print("\n[1/5] Python version check")
    ok, msg = check_python_version()
    print(f"  {msg}")
    if not ok:
        print("  FAIL -- upgrade Python and re-run.", file=sys.stderr)
        return 2

    # Step 2: Project root
    print("\n[2/5] Project root")
    root = find_project_root()
    if root is None:
        print(
            "  FAIL -- could not find pyproject.toml from script location. "
            "Are you running this from a Curator clone?",
            file=sys.stderr,
        )
        return 3
    print(f"  Found project root: {root}")

    # Step 3: Venv
    print("\n[3/5] Virtual environment")
    if venv_exists(root) and not args.force:
        print(f"  Reusing existing .venv at {root / '.venv'}")
    else:
        if venv_exists(root) and args.force:
            print(f"  --force given; recreating .venv at {root / '.venv'}")
            # Note: we don't delete the old venv (could be in use); we just
            # let `python -m venv` overwrite scripts. The user can manually
            # rm -rf .venv if they want a truly fresh start.
        rc = create_venv(root, dry_run=args.dry_run)
        if rc != 0:
            print(f"  FAIL -- venv creation exited {rc}", file=sys.stderr)
            return 4

    venv_py = venv_python_path(root)
    if not args.dry_run and not venv_py.exists():
        print(
            f"  FAIL -- expected venv python at {venv_py} but not found",
            file=sys.stderr,
        )
        return 4

    # Step 4: Install
    print(f"\n[4/5] Install (profile: {args.profile} -> extras: [{PROFILES[args.profile]}])")
    rc = install_curator(venv_py, args.profile, dry_run=args.dry_run)
    if rc != 0:
        print(f"  FAIL -- pip install exited {rc}", file=sys.stderr)
        return 5

    # Step 5: Smoke test
    if args.no_smoke:
        print("\n[5/5] Smoke test  --  SKIPPED (--no-smoke)")
    else:
        print("\n[5/5] Smoke test (collect-only on tests/unit/)")
        rc = run_smoke_test(venv_py, dry_run=args.dry_run)
        if rc != 0:
            # Don't return non-zero -- install is good, smoke test is a sniff
            print(
                "  WARNING -- smoke test failed but install succeeded. "
                "Investigate before relying on the venv.",
                file=sys.stderr,
            )

    # Final report
    print("\n" + "=" * 70)
    print("Setup complete.")
    print("=" * 70)
    print(f"  Venv:        {root / '.venv'}")
    print(f"  Activate:    .venv\\Scripts\\Activate.ps1   (PowerShell)")
    print(f"               source .venv/bin/activate     (POSIX)")
    print(f"  Run pytest:  {venv_py} -m pytest tests/ -q")
    print(f"  Run baseline (detached, avoids MCP wedge on long runs):")
    print(f"               scripts\\run_pytest_detached.ps1 -LogPath <log>")
    print(f"                   -PytestArgs @('tests/', '-q', '--tb=line')")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
