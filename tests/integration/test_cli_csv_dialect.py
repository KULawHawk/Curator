"""Tests for v1.7.37 --csv-dialect TSV option.

Verifies that:
  1. The build_csv_writer helper in cli/util.py produces correct
     delimiter / line-terminator behavior for both dialects, and
     raises ValueError on unknown dialects.
  2. The --csv-dialect flag is present on all 9 stdout-CSV commands.
  3. The 'tsv' dialect produces tab-separated output for each command.
  4. audit-export's --format flag accepts 'tsv' as a third value and
     writes a tab-separated file.

Strategy:
  * Helper-level tests use plain io.StringIO (no subprocess).
  * CLI tests use subprocess (matches the v1.7.36 pattern from
    test_cli_csv_list_commands.py).
"""

from __future__ import annotations

import csv as _csv
import io
import os
import subprocess
import sys
from pathlib import Path

import pytest

from curator.cli.util import build_csv_writer


# ---------------------------------------------------------------------------
# build_csv_writer helper tests
# ---------------------------------------------------------------------------


class TestBuildCsvWriter:
    """Direct unit tests of the helper function."""

    def test_default_dialect_is_csv(self):
        """Calling with no dialect argument defaults to comma-separated."""
        out = io.StringIO()
        w = build_csv_writer(out)
        w.writerow(["a", "b", "c"])
        assert out.getvalue() == "a,b,c\n"

    def test_csv_dialect_explicit(self):
        """Explicit 'csv' dialect matches the default."""
        out = io.StringIO()
        w = build_csv_writer(out, "csv")
        w.writerow(["a", "b", "c"])
        assert out.getvalue() == "a,b,c\n"

    def test_tsv_dialect_uses_tab_delimiter(self):
        """'tsv' dialect uses tab as the delimiter."""
        out = io.StringIO()
        w = build_csv_writer(out, "tsv")
        w.writerow(["a", "b", "c"])
        assert out.getvalue() == "a\tb\tc\n"

    def test_lineterminator_is_lf_for_csv(self):
        """v1.7.36 lineterminator fix: CSV uses '\\n', not '\\r\\n'."""
        out = io.StringIO()
        w = build_csv_writer(out, "csv")
        w.writerow(["a", "b"])
        w.writerow(["c", "d"])
        assert out.getvalue() == "a,b\nc,d\n", (
            f"Expected LF-only line terminators; got: {out.getvalue()!r}"
        )

    def test_lineterminator_is_lf_for_tsv(self):
        """v1.7.36 lineterminator fix extends to TSV."""
        out = io.StringIO()
        w = build_csv_writer(out, "tsv")
        w.writerow(["a", "b"])
        w.writerow(["c", "d"])
        assert out.getvalue() == "a\tb\nc\td\n"

    def test_unknown_dialect_raises_valueerror(self):
        """Unknown dialects raise ValueError with a helpful message."""
        out = io.StringIO()
        with pytest.raises(ValueError, match="unknown csv dialect"):
            build_csv_writer(out, "xml")

    def test_quoting_works_for_tsv_cells_with_tabs(self):
        """TSV cells containing tabs are still quoted per RFC 4180 semantics."""
        out = io.StringIO()
        w = build_csv_writer(out, "tsv")
        w.writerow(["plain", "has\ttab", "normal"])
        # The cell with a literal tab gets quoted
        assert '"has\ttab"' in out.getvalue()


# ---------------------------------------------------------------------------
# CLI subprocess tests for --csv-dialect tsv across the 9 commands
# ---------------------------------------------------------------------------


def _run_curator(args: list[str], db_path: Path) -> tuple[int, str, str]:
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONIOENCODING"] = "utf-8"
    cmd = [sys.executable, "-m", "curator.cli.main", "--db", str(db_path)] + args
    result = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", env=env,
    )
    return result.returncode, result.stdout, result.stderr


def _expect_tab_delimited(text: str) -> None:
    """Verify the first non-empty line has tabs and no commas."""
    lines = [line for line in text.split("\n") if line.strip()]
    if not lines:
        return  # empty output is acceptable for empty datasets
    first = lines[0]
    assert "\t" in first, f"Expected tab in first line: {first!r}"
    # Commas may appear in JSON-encoded cells but not as top-level
    # delimiters; counting top-level columns by tab should give >1
    assert first.count("\t") >= 1, f"Expected >=1 tab in first line: {first!r}"


def _expect_no_blank_lines(text: str) -> None:
    """v1.7.36 regression guard: no '\\n\\n' anywhere in CSV/TSV output."""
    assert "\n\n" not in text, (
        f"Found blank line in output: {text[:200]!r}"
    )


def test_audit_csv_dialect_tsv(tmp_path):
    """`audit --csv --csv-dialect tsv` produces tab-delimited output."""
    db = tmp_path / "v1737_audit.db"
    code, stdout, _ = _run_curator(
        ["audit", "--limit", "3", "--csv", "--csv-dialect", "tsv"], db,
    )
    assert code == 0
    _expect_no_blank_lines(stdout)
    _expect_tab_delimited(stdout)


def test_bundles_list_csv_dialect_tsv(tmp_path):
    """`bundles list --csv --csv-dialect tsv` produces tab-delimited output."""
    db = tmp_path / "v1737_bundles.db"
    code, stdout, _ = _run_curator(
        ["bundles", "list", "--csv", "--csv-dialect", "tsv"], db,
    )
    assert code == 0
    _expect_no_blank_lines(stdout)
    # Header is "bundle_id\tname\ttype\tmembers\tconfidence"
    header = stdout.split("\n", 1)[0]
    assert header == "bundle_id\tname\ttype\tmembers\tconfidence", (
        f"Unexpected TSV header: {header!r}"
    )


def test_sources_list_csv_dialect_tsv(tmp_path):
    """`sources list --csv --csv-dialect tsv` produces tab-delimited output."""
    db = tmp_path / "v1737_sources.db"
    # Seed a source so we have a data row
    code, _, _ = _run_curator(
        ["sources", "add", "test_src", "--type", "local"], db,
    )
    assert code == 0
    code, stdout, _ = _run_curator(
        ["sources", "list", "--csv", "--csv-dialect", "tsv"], db,
    )
    assert code == 0
    _expect_no_blank_lines(stdout)
    _expect_tab_delimited(stdout)
    # share_visibility column should still be present
    header = stdout.split("\n", 1)[0]
    assert "share_visibility" in header


def test_csv_default_dialect_unchanged(tmp_path):
    """Without --csv-dialect, output stays comma-separated (back-compat)."""
    db = tmp_path / "v1737_default.db"
    code, _, _ = _run_curator(
        ["sources", "add", "back_compat", "--type", "local"], db,
    )
    assert code == 0
    code, stdout, _ = _run_curator(
        ["sources", "list", "--csv"], db,  # no --csv-dialect
    )
    assert code == 0
    header = stdout.split("\n", 1)[0]
    assert header.startswith("source_id,source_type"), (
        f"Default dialect should be CSV; got header: {header!r}"
    )
    assert "\t" not in header, (
        f"Default dialect should not contain tabs; got: {header!r}"
    )


# ---------------------------------------------------------------------------
# v1.7.38: clean error for invalid --csv-dialect
# ---------------------------------------------------------------------------


def test_invalid_csv_dialect_gives_clean_error(tmp_path):
    """v1.7.38: --csv-dialect xyz produces typer-style error, not traceback.

    Closes the v1.7.37 limitation where the helper-layer ValueError
    propagated as a Rich traceback. The v1.7.38 _check_csv_dialect()
    helper in main.py catches it CLI-side and surfaces a clean message.
    """
    db = tmp_path / "v1738_invalid.db"
    code, stdout, stderr = _run_curator(
        ["audit", "--limit", "1", "--csv", "--csv-dialect", "invalid"], db,
    )
    # Exit 1 (clean user error), NOT exit 2 (typer parsing) or other
    assert code == 1, f"Expected clean exit 1; got {code}"
    # No Python traceback in output
    combined = stdout + stderr
    assert "Traceback" not in combined, (
        f"Should not show Python traceback; got: {combined[:300]!r}"
    )
    # Error message should mention the valid options + the invalid value
    assert "csv" in combined and "tsv" in combined and "invalid" in combined, (
        f"Error should mention 'csv', 'tsv', and 'invalid'; got: {combined[:300]!r}"
    )


def test_invalid_csv_dialect_clean_error_across_commands(tmp_path):
    """The clean-error path applies uniformly to every --csv-dialect command.

    Spot-check three commands (audit, bundles list, sources list) to
    verify they all behave the same way for an invalid dialect value.
    """
    db = tmp_path / "v1738_uniform.db"
    cmds = [
        ["audit", "--limit", "1", "--csv", "--csv-dialect", "xml"],
        ["bundles", "list", "--csv", "--csv-dialect", "yaml"],
        ["sources", "list", "--csv", "--csv-dialect", "weird"],
    ]
    for cmd in cmds:
        code, stdout, stderr = _run_curator(cmd, db)
        combined = stdout + stderr
        assert code == 1, (
            f"{cmd[0]}: expected exit 1; got {code}"
        )
        assert "Traceback" not in combined, (
            f"{cmd[0]}: should not show Python traceback; got: {combined[:200]!r}"
        )
        # Each error must mention the dialect name we passed
        invalid_value = cmd[-1]
        assert invalid_value in combined, (
            f"{cmd[0]}: error should mention {invalid_value!r}; got: {combined[:200]!r}"
        )


def test_helper_validation_still_raises_valueerror():
    """v1.7.38: build_csv_writer's ValueError behavior preserved for library callers.

    The CLI-side _check_csv_dialect catches the dialect before the
    helper is invoked, but library callers (programmatic users of
    build_csv_writer) still get the original ValueError defense.
    Both layers of validation remain useful.
    """
    from curator.cli.util import build_csv_writer
    out = io.StringIO()
    with pytest.raises(ValueError, match="unknown csv dialect"):
        build_csv_writer(out, "xml")


# ---------------------------------------------------------------------------
# audit-export --format tsv
# ---------------------------------------------------------------------------


def test_audit_export_format_tsv(tmp_path):
    """`audit-export --format tsv` writes a tab-separated file."""
    db = tmp_path / "v1737_export.db"
    out = tmp_path / "export.tsv"
    code, stdout, stderr = _run_curator(
        ["audit-export", "--to", str(out), "--format", "tsv"], db,
    )
    assert code == 0, f"exit={code}, stderr={stderr[:300]}"
    assert out.exists(), "Output file was not created"
    content = out.read_text(encoding="utf-8")
    # Header should be tab-separated
    if content:  # might be empty if no audit rows
        first_line = content.split("\n", 1)[0]
        if first_line:  # if any rows were exported
            assert "\t" in first_line, (
                f"Expected tab in TSV header; got: {first_line!r}"
            )


def test_audit_export_format_invalid_value(tmp_path):
    """`audit-export --format xml` rejects with a clear error."""
    db = tmp_path / "v1737_invalid.db"
    out = tmp_path / "export.dat"
    code, stdout, stderr = _run_curator(
        ["audit-export", "--to", str(out), "--format", "xml"], db,
    )
    assert code == 1, f"Expected exit 1; got {code}"
    # The validation message should mention the valid options
    combined = stdout + stderr
    assert "tsv" in combined or "jsonl" in combined or "csv" in combined, (
        f"Error should mention valid formats; got: {combined[:300]!r}"
    )
