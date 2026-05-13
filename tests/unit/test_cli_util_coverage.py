"""Focused coverage tests for cli/util.py.

Sub-ship v1.7.112 of Round 2 Tier 1.

Closes lines 184-191 — the entire `build_csv_writer` function body
(csv dialect, tsv dialect, unknown-dialect ValueError).
"""

from __future__ import annotations

import csv
import io

import pytest

from curator.cli.util import build_csv_writer


def test_build_csv_writer_default_csv_dialect():
    buf = io.StringIO()
    writer = build_csv_writer(buf)
    writer.writerow(["a", "b", "c"])
    assert buf.getvalue() == "a,b,c\n"


def test_build_csv_writer_tsv_dialect():
    buf = io.StringIO()
    writer = build_csv_writer(buf, dialect="tsv")
    writer.writerow(["x", "y", "z"])
    assert buf.getvalue() == "x\ty\tz\n"


def test_build_csv_writer_unknown_dialect_raises_value_error():
    with pytest.raises(ValueError, match="unknown csv dialect"):
        build_csv_writer(io.StringIO(), dialect="bogus")


def test_build_csv_writer_uses_lf_lineterminator():
    # The lineterminator='\n' setting is the v1.7.36 Windows blank-line
    # fix; verify it stays in place.
    buf = io.StringIO()
    writer = build_csv_writer(buf)
    writer.writerow(["a"])
    writer.writerow(["b"])
    assert buf.getvalue() == "a\nb\n"
