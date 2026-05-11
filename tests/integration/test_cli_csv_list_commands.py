"""Tests for v1.7.36 CSV-completeness ship.

Verifies that the four list-output CLI commands gained --csv / --no-header
flags and emit well-formed CSV when invoked. Uses subprocess for end-to-
end verification (the same way the user would actually run them).

Strategy:
  * Spin up a real CuratorRuntime against a temp DB.
  * Seed minimal data so each command has something to list.
  * Invoke the CLI via subprocess with --csv (and again with --csv --no-header)
  * Parse the output with the stdlib csv module and verify shape.

These tests intentionally use a separate temp DB per test to avoid
crosstalk and to keep the v1.7.36 CSV semantics test-isolated.
"""

from __future__ import annotations

import csv
import io
import subprocess
import sys
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_curator(args: list[str], db_path: Path, env_extra: dict | None = None) -> tuple[int, str, str]:
    """Run `python -m curator.cli.main ...` with --db pointing at tmp DB.

    Returns (returncode, stdout, stderr).
    """
    import os
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONIOENCODING"] = "utf-8"
    if env_extra:
        env.update(env_extra)
    cmd = [sys.executable, "-m", "curator.cli.main", "--db", str(db_path)] + args
    result = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", env=env,
    )
    return result.returncode, result.stdout, result.stderr


def _parse_csv(text: str) -> list[list[str]]:
    """Parse CSV text into a list of rows."""
    return list(csv.reader(io.StringIO(text)))


def _expect_no_blank_lines(text: str) -> None:
    """Verify the CSV output uses single-newline terminators.

    v1.7.36 added lineterminator='\\n' to all 7 stdout csv.writers to
    prevent the Windows \\r\\n + extra-newline blank-row issue.
    """
    assert "\n\n" not in text, (
        f"Found blank line in CSV output: {text[:200]!r}"
    )


# ---------------------------------------------------------------------------
# audit --csv
# ---------------------------------------------------------------------------


def test_audit_csv_header_and_rows(tmp_path):
    """audit --csv on a fresh DB produces a header + zero rows (no audit yet)."""
    db_path = tmp_path / "v1736_audit.db"
    code, stdout, _ = _run_curator(["audit", "--limit", "5", "--csv"], db_path)
    assert code == 0, f"Expected exit 0, got {code}"
    _expect_no_blank_lines(stdout)
    rows = _parse_csv(stdout)
    # Header always present; data rows depend on prior activity
    assert rows[0] == [
        "audit_id", "occurred_at", "actor", "action",
        "entity_type", "entity_id", "details",
    ], f"Header mismatch: {rows[0]!r}"


def test_audit_csv_no_header(tmp_path):
    """audit --csv --no-header omits the header row.

    For a freshly-created DB the audit table is empty, so stdout is also
    empty -- which is exactly the contract for --no-header on a no-rows
    dataset.
    """
    db_path = tmp_path / "v1736_audit_nh.db"
    code, stdout, _ = _run_curator(
        ["audit", "--limit", "5", "--csv", "--no-header"], db_path,
    )
    assert code == 0
    _expect_no_blank_lines(stdout)
    # No header AND no rows on a fresh DB
    rows = _parse_csv(stdout)
    if rows:
        # If there happened to be entries (e.g. from runtime init), header
        # must not be present
        assert rows[0][0] != "audit_id", (
            f"--no-header still emitted header row: {rows[0]!r}"
        )


# ---------------------------------------------------------------------------
# bundles list --csv
# ---------------------------------------------------------------------------


def test_bundles_list_csv_empty(tmp_path):
    """bundles list --csv on empty DB emits header only."""
    db_path = tmp_path / "v1736_bundles.db"
    code, stdout, _ = _run_curator(["bundles", "list", "--csv"], db_path)
    assert code == 0
    _expect_no_blank_lines(stdout)
    rows = _parse_csv(stdout)
    assert rows == [["bundle_id", "name", "type", "members", "confidence"]], (
        f"Expected header-only output; got: {rows!r}"
    )


def test_bundles_list_csv_no_header_empty(tmp_path):
    """bundles list --csv --no-header on empty DB emits nothing."""
    db_path = tmp_path / "v1736_bundles_nh.db"
    code, stdout, _ = _run_curator(
        ["bundles", "list", "--csv", "--no-header"], db_path,
    )
    assert code == 0
    _expect_no_blank_lines(stdout)
    # No header, no data -> empty output
    assert stdout == "" or stdout == "\n", f"Expected empty; got: {stdout!r}"


# ---------------------------------------------------------------------------
# sources list --csv
# ---------------------------------------------------------------------------


def test_sources_list_csv_includes_share_visibility(tmp_path):
    """sources list --csv: header has share_visibility column (v1.7.29)."""
    db_path = tmp_path / "v1736_sources.db"
    # Adding a source first so we have at least one row
    code, _, _ = _run_curator(
        ["sources", "add", "test_src", "--type", "local"], db_path,
    )
    assert code == 0
    code, stdout, _ = _run_curator(["sources", "list", "--csv"], db_path)
    assert code == 0
    _expect_no_blank_lines(stdout)
    rows = _parse_csv(stdout)
    assert rows[0] == [
        "source_id", "source_type", "display_name", "enabled",
        "files", "share_visibility", "config",
    ], f"Header mismatch: {rows[0]!r}"
    # Should have at least one source row
    assert len(rows) >= 2, f"Expected header + 1 source; got {len(rows)} row(s)"
    # share_visibility column should be a valid value
    sv_idx = rows[0].index("share_visibility")
    for r in rows[1:]:
        assert r[sv_idx] in ("private", "team", "public"), (
            f"Unexpected share_visibility value: {r[sv_idx]!r}"
        )


# ---------------------------------------------------------------------------
# lineage --csv
# ---------------------------------------------------------------------------


def test_lineage_csv_no_file(tmp_path):
    """lineage --csv with a nonexistent file should fail cleanly (exit 1).

    The --csv flag is wired but lineage requires resolving a real file
    first. On an empty DB any identifier fails to resolve. The flag
    parsing itself shouldn't break.
    """
    db_path = tmp_path / "v1736_lineage.db"
    code, stdout, stderr = _run_curator(
        ["lineage", "nonexistent-identifier", "--csv"], db_path,
    )
    # Exit 1 (file not found) is the expected behavior; the CSV flag
    # is parsed but never reached
    assert code == 1, f"Expected exit 1, got {code}; stderr: {stderr[:200]}"
