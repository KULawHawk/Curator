"""
tests/integration/test_infrastructure_audit.py   (v1.7.80)

Self-verifying snapshot of Curator's development infrastructure. If any piece
drifts (a script gets deleted, a hook loses its shebang, the dependabot
schedule changes, the workflow uses a different action version), exactly the
relevant assertion in this file fails with a clear message.

This is the doctrine made executable. See docs/ENGINEERING_DOCTRINE.md for the
human-readable version.

Why this test exists:
  - Principle 9 (bug-class sweeps + regression lints): the infrastructure
    *itself* is a bug-prone surface. Without a snapshot test, silent drift
    (e.g. someone accidentally deletes scripts/ci_diag.sh) would only be
    discovered when the missing piece is needed.
  - Principle 8 (lints turn invariants into laws): each assertion below is
    an invariant that we have committed to. Codifying them in pytest gives
    them the same force as a pre-commit lint.

Scope:
  - Tooling scripts (5 expected as of v1.7.80)
  - Git hooks (2 expected)
  - Pre-commit lint files (3 expected)
  - Workflow YAML structure (action versions, matrix shape)
  - Dependabot configuration (ecosystem, cadence)
  - Top-level docs (README sections, doctrine, audit)

What this test deliberately does NOT do:
  - Run the scripts (each has its own validation; pre-commit + pre-push run
    in CI for free)
  - Check script CONTENTS line-by-line (too brittle; would break on every
    cosmetic edit)
  - Validate the doctrine prose (text-content validation belongs in human
    review, not pytest)
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Repo root (this test lives at tests/integration/test_infrastructure_audit.py)
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


# ===========================================================================
# Part I -- Tooling scripts (Principle 16: one-command setup)
# ===========================================================================

EXPECTED_SCRIPTS = {
    "scripts/run_pytest_detached.ps1": {
        "ship": "v1.7.39",
        "platform": "Windows",
        "purpose": "MCP-safe pytest invocation (PowerShell)",
    },
    "scripts/run_pytest_detached.cmd": {
        "ship": "v1.7.39",
        "platform": "Windows (cmd.exe)",
        "purpose": "MCP-safe pytest invocation (cmd shim)",
    },
    "scripts/ci_diag.ps1": {
        "ship": "v1.7.65",
        "platform": "Windows (+ pwsh on POSIX)",
        "purpose": "CI diagnostic loop (status/logs/summary)",
    },
    "scripts/ci_diag.sh": {
        "ship": "v1.7.78",
        "platform": "macOS / Linux / WSL / Git Bash",
        "purpose": "CI diagnostic loop (bash variant)",
    },
    "scripts/setup_dev_hooks.ps1": {
        "ship": "v1.7.74",
        "platform": "Windows",
        "purpose": "One-command dev environment installer (hooks + PAT)",
    },
    "scripts/setup_dev_hooks.sh": {
        "ship": "v1.7.76",
        "platform": "macOS / Linux / WSL / Git Bash",
        "purpose": "One-command dev environment installer (bash)",
    },
    "scripts/setup_dev_env.py": {
        "ship": "early Phase Beta",
        "platform": "Cross-platform (Python)",
        "purpose": "Bootstrap full dev environment (venv + extras)",
    },
    "scripts/setup_gdrive_source.py": {
        "ship": "Phase Beta",
        "platform": "Cross-platform (Python)",
        "purpose": "OAuth flow + Google Drive source registration",
    },
}


@pytest.mark.parametrize("script_path", sorted(EXPECTED_SCRIPTS.keys()))
def test_tooling_script_exists(script_path: str) -> None:
    """Each expected tooling script must exist at its canonical path."""
    full_path = REPO_ROOT / script_path
    assert full_path.is_file(), (
        f"Expected tooling script {script_path!r} is missing. "
        f"This script was shipped in {EXPECTED_SCRIPTS[script_path]['ship']} "
        f"for {EXPECTED_SCRIPTS[script_path]['purpose']}. "
        f"See docs/ENGINEERING_DOCTRINE.md Appendix B."
    )


def test_no_unexpected_tooling_scripts() -> None:
    """The scripts/ directory should contain exactly the documented set.

    If a new script appears, either (a) document it in EXPECTED_SCRIPTS above
    and in the doctrine, or (b) delete it. Undocumented infrastructure is a
    smell (Principle 12: documentation follows tooling).
    """
    scripts_dir = REPO_ROOT / "scripts"
    if not scripts_dir.is_dir():
        pytest.skip("scripts/ directory not present")

    found = {f"scripts/{p.name}" for p in scripts_dir.iterdir() if p.is_file()}
    expected = set(EXPECTED_SCRIPTS.keys())
    unexpected = found - expected
    assert not unexpected, (
        f"Found undocumented script(s) in scripts/: {sorted(unexpected)}. "
        f"Add them to EXPECTED_SCRIPTS in this test and "
        f"docs/ENGINEERING_DOCTRINE.md, or remove them."
    )


# ===========================================================================
# Part II -- Git hooks (Principle 10: signals not gates; Principle 8: lints)
# ===========================================================================

EXPECTED_HOOKS = {
    ".githooks/pre-commit": {
        "ship": "v1.7.34 (initial), v1.7.72/73 (lints added)",
        "behavior": "Runs 3 project-invariant lints (~0.5s)",
        "blocks": True,
    },
    ".githooks/pre-push": {
        "ship": "v1.7.70",
        "behavior": "Queries CI API; warns on red; never blocks",
        "blocks": False,
    },
}


@pytest.mark.parametrize("hook_path", sorted(EXPECTED_HOOKS.keys()))
def test_git_hook_exists(hook_path: str) -> None:
    full_path = REPO_ROOT / hook_path
    assert full_path.is_file(), (
        f"Expected git hook {hook_path!r} is missing. "
        f"Ship: {EXPECTED_HOOKS[hook_path]['ship']}. "
        f"Behavior: {EXPECTED_HOOKS[hook_path]['behavior']}."
    )


@pytest.mark.parametrize("hook_path", sorted(EXPECTED_HOOKS.keys()))
def test_git_hook_has_shebang(hook_path: str) -> None:
    """Hooks must start with a POSIX shebang so they work on Linux/macOS."""
    full_path = REPO_ROOT / hook_path
    if not full_path.is_file():
        pytest.skip(f"{hook_path} not present (other test reports this)")
    first_line = full_path.read_text(encoding="utf-8").splitlines()[0]
    assert first_line.startswith("#!"), (
        f"{hook_path} must start with a POSIX shebang line. "
        f"Got: {first_line!r}"
    )


# ===========================================================================
# Part III -- Project-invariant lints (Principle 8)
# ===========================================================================

EXPECTED_LINTS = {
    "tests/unit/test_cli_util.py": {
        "ship": "v1.7.32/34",
        "rule": "No literal Unicode glyphs in src/curator/cli/ outside util.py",
        "lesson": "#50 (cp1252 capture crashes)",
    },
    "tests/unit/test_repo_order_by_lint.py": {
        "ship": "v1.7.72",
        "rule": "ORDER BY clauses need deterministic tie-breaker",
        "lesson": "#67 (20-ship silent CI-red arc)",
    },
    "tests/unit/test_repo_ansi_lint.py": {
        "ship": "v1.7.73",
        "rule": "No inline ANSI-strip regex outside conftest.py",
        "lesson": "Reinforces v1.7.68 fixture hoist",
    },
}


@pytest.mark.parametrize("lint_path", sorted(EXPECTED_LINTS.keys()))
def test_project_invariant_lint_exists(lint_path: str) -> None:
    full_path = REPO_ROOT / lint_path
    assert full_path.is_file(), (
        f"Expected lint file {lint_path!r} is missing. "
        f"Ship: {EXPECTED_LINTS[lint_path]['ship']}. "
        f"Rule: {EXPECTED_LINTS[lint_path]['rule']}. "
        f"Removing a lint requires updating EXPECTED_LINTS and explaining the "
        f"removal in release notes."
    )


# ===========================================================================
# Part IV -- CI workflow (Principle 13: decision history in comments)
# ===========================================================================

EXPECTED_ACTION_VERSIONS = {
    "actions/checkout@v6": "v1.7.77 (was v5 from v1.7.67)",
    "actions/setup-python@v6": "v1.7.67",
    "actions/upload-artifact@v7": "v1.7.77 (was v6 from v1.7.67)",
}


def test_ci_workflow_exists() -> None:
    workflow = REPO_ROOT / ".github" / "workflows" / "test.yml"
    assert workflow.is_file(), (
        ".github/workflows/test.yml is the canonical CI workflow. "
        "If it's missing, CI doesn't run and the project's quality story is "
        "broken. See Part V of the doctrine for the 9-cell matrix decision."
    )


def test_ci_workflow_uses_expected_action_versions() -> None:
    """Workflow must use exactly the action versions ratified in the doctrine.

    If a bump is needed, update this test and the workflow simultaneously, and
    cite the new versions in release notes (Principle 13: decision history in
    comments).
    """
    workflow = REPO_ROOT / ".github" / "workflows" / "test.yml"
    content = workflow.read_text(encoding="utf-8")
    for version, ship in EXPECTED_ACTION_VERSIONS.items():
        assert version in content, (
            f"Expected {version!r} in .github/workflows/test.yml "
            f"(established by {ship}, ratified in doctrine Part V). "
            f"If this assertion fails, either roll back the workflow or "
            f"update EXPECTED_ACTION_VERSIONS and the doctrine."
        )


def test_ci_workflow_has_full_matrix() -> None:
    """The 9-cell matrix is a Part V standing decision (v1.7.54)."""
    workflow = REPO_ROOT / ".github" / "workflows" / "test.yml"
    content = workflow.read_text(encoding="utf-8")
    for os_name in ("windows-latest", "ubuntu-latest", "macos-latest"):
        assert os_name in content, (
            f"Expected OS {os_name!r} in CI matrix. The 9-cell matrix "
            f"({{windows, ubuntu, macos}} x {{3.11, 3.12, 3.13}}) is "
            f"ratified in doctrine Part V."
        )
    for py_version in ('"3.11"', '"3.12"', '"3.13"'):
        assert py_version in content, (
            f"Expected Python {py_version} in CI matrix. The 9-cell matrix "
            f"is ratified in doctrine Part V."
        )


# ===========================================================================
# Part V -- Dependabot (Principle 17: detector not acceptor)
# ===========================================================================

def test_dependabot_config_exists() -> None:
    config = REPO_ROOT / ".github" / "dependabot.yml"
    assert config.is_file(), (
        ".github/dependabot.yml is the source of truth for automated "
        "dependency tracking (Principle 17). Removing it disables Dependabot."
    )


def test_dependabot_watches_github_actions() -> None:
    """github-actions ecosystem watching is a standing decision (v1.7.71)."""
    config = REPO_ROOT / ".github" / "dependabot.yml"
    content = config.read_text(encoding="utf-8")
    assert "github-actions" in content, (
        "Dependabot must watch the github-actions ecosystem. This is a "
        "Part V standing decision (v1.7.71)."
    )


# ===========================================================================
# Part VI -- Top-level documentation (Principle 12)
# ===========================================================================

EXPECTED_DOCS = {
    "README.md": {
        "required_sections": [
            "## Contributing",
            "### What the hooks do",
            "### CI diagnostic loop",
            "### Automated dependency tracking",
        ],
        "purpose": "Single source of truth for contributor onboarding (v1.7.75/76)",
    },
    "docs/ENGINEERING_DOCTRINE.md": {
        "required_sections": [
            "## Principle 1:",
            "## Principle 8:",
            "## Principle 16:",
            "## Principle 17:",
            "## Part V",
            "## Document history",
        ],
        "purpose": "This document's authoritative version (v1.7.80)",
    },
    "docs/AD_ASTRA_CI_AUDIT.md": {
        "required_sections": [
            "## Findings",
            "## Conclusion",
        ],
        "purpose": "Closes the sibling-repo audit (v1.7.79)",
    },
}


@pytest.mark.parametrize("doc_path", sorted(EXPECTED_DOCS.keys()))
def test_documentation_file_exists(doc_path: str) -> None:
    full_path = REPO_ROOT / doc_path
    assert full_path.is_file(), (
        f"Expected documentation {doc_path!r} is missing. "
        f"Purpose: {EXPECTED_DOCS[doc_path]['purpose']}."
    )


@pytest.mark.parametrize("doc_path", sorted(EXPECTED_DOCS.keys()))
def test_documentation_has_required_sections(doc_path: str) -> None:
    full_path = REPO_ROOT / doc_path
    if not full_path.is_file():
        pytest.skip(f"{doc_path} missing (other test reports this)")
    content = full_path.read_text(encoding="utf-8")
    missing = [
        s for s in EXPECTED_DOCS[doc_path]["required_sections"]
        if s not in content
    ]
    assert not missing, (
        f"{doc_path} is missing required sections: {missing}. "
        f"These were established by the ship that created the doc; removing "
        f"them requires updating EXPECTED_DOCS and explaining in release notes."
    )


# ===========================================================================
# Part VII -- Cross-platform parity (Principle 3)
# ===========================================================================

def test_cross_platform_parity_setup_hooks() -> None:
    """Both PowerShell and bash variants of setup_dev_hooks must exist together."""
    ps = REPO_ROOT / "scripts" / "setup_dev_hooks.ps1"
    sh = REPO_ROOT / "scripts" / "setup_dev_hooks.sh"
    assert ps.is_file() and sh.is_file(), (
        "Principle 3 (functional parity > code parity) requires BOTH "
        "setup_dev_hooks.ps1 (v1.7.74) AND setup_dev_hooks.sh (v1.7.76) "
        "to exist. Having only one breaks cross-platform contributor support."
    )


def test_cross_platform_parity_ci_diag() -> None:
    """Both PowerShell and bash variants of ci_diag must exist together."""
    ps = REPO_ROOT / "scripts" / "ci_diag.ps1"
    sh = REPO_ROOT / "scripts" / "ci_diag.sh"
    assert ps.is_file() and sh.is_file(), (
        "Principle 3 requires BOTH ci_diag.ps1 (v1.7.65) AND ci_diag.sh "
        "(v1.7.78) to exist. Having only one breaks cross-platform CI "
        "diagnostic access."
    )


# ===========================================================================
# Part VIII -- Counts (sanity check; these will drift over time)
# ===========================================================================

def test_changelog_mentions_doctrine() -> None:
    """The doctrine ship should be discoverable from CHANGELOG."""
    changelog = REPO_ROOT / "CHANGELOG.md"
    if not changelog.is_file():
        pytest.skip("CHANGELOG.md not present")
    content = changelog.read_text(encoding="utf-8")
    # Just check that we have a v1.7.80 entry; precise content is human-reviewed.
    assert re.search(r"\[1\.7\.80\]", content) or "v1.7.80" in content, (
        "CHANGELOG.md should have an entry for v1.7.80 (the doctrine ship)."
    )
