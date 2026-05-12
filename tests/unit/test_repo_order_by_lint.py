"""Lint: every ORDER BY in src/curator/storage/repositories/ uses a
deterministic sort key (rowid or a documented-unique column).

v1.7.72: codifies the v1.7.66 ORDER BY hardening sweep at the pytest level.
Any future ORDER BY clause that lacks a deterministic tie-breaker will fail
this test with a pointer to the v1.7.64/v1.7.66 lesson:

  SQLite's CURRENT_TIMESTAMP has second-level resolution. Two rows inserted
  in the same call get identical timestamps. ORDER BY <timestamp> alone
  returns them in implementation-defined order, which varies by SQLite
  version. The OS x Python matrix exposes this divergence. Always add
  ``, rowid <DIR>`` (or use a column that is documented to be unique) as
  the final sort key.

The lint scans the repository modules' Python source for "ORDER BY"
substrings. For each match, the line (or the surrounding multi-line SQL
block) must contain ONE of:

  1. ``rowid`` (the canonical SQLite tie-breaker)
  2. A column from KNOWN_UNIQUE_COLUMNS (e.g. ``curator_id`` UUIDs,
     ``src_path`` unique-within-job)
  3. An inline lint exemption comment: ``# order-by-lint: <reason>``

Scope:
  - ONLY scans src/curator/storage/repositories/ -- that's where the
    SQLite ORDER BY tie-breaking risk lives.
  - Pytest fixtures and CLI test SQL (in tests/) are not scanned.
  - The lint test itself contains "ORDER BY" strings for documentation;
    it's skipped via filename pattern.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


# Columns that are documented-unique in their respective tables, so an
# ORDER BY <column> is already deterministic without needing rowid.
#
# This list MUST stay in sync with the v1.7.66 audit decisions:
#   - file_repo.py L269: ORDER BY curator_id -- UUID, unique
#   - migration_job_repo.py L275/L399: ORDER BY src_path -- unique within job
KNOWN_UNIQUE_COLUMNS: set[str] = {
    "curator_id",   # UUID, unique across all sources
    "src_path",     # unique within a single migration job
}


# Regex that captures ORDER BY plus the trailing clause until either:
#   - a closing triple-quote, single-quote, or double-quote
#   - the end of the line that contains "ORDER BY"
# Multi-line SQL is handled by capturing a 5-line window starting at the
# ORDER BY line. The clause must contain either rowid, a known-unique
# column, or an exemption comment within this window.
ORDER_BY_PATTERN = re.compile(r"ORDER\s+BY", re.IGNORECASE)


def test_every_order_by_has_deterministic_tie_breaker() -> None:
    """Lint: ORDER BY clauses must include a deterministic tie-breaker.

    Fails if any ORDER BY in src/curator/storage/repositories/ lacks:
      - ``rowid`` (the canonical SQLite tie-breaker)
      - One of the KNOWN_UNIQUE_COLUMNS
      - An inline ``# order-by-lint: <reason>`` exemption comment

    v1.7.66 swept 13 sites across 7 repos. This lint prevents regression.
    """
    # Locate src/curator/storage/repositories/ relative to this test file.
    repos_dir = (
        Path(__file__).resolve().parent.parent.parent
        / "src" / "curator" / "storage" / "repositories"
    )
    assert repos_dir.is_dir(), f"repositories/ directory not found at {repos_dir}"

    violations: list[tuple[str, int, str]] = []

    for py_path in repos_dir.rglob("*.py"):
        if py_path.name == "__init__.py":
            continue

        content = py_path.read_text(encoding="utf-8")
        lines = content.split("\n")

        for line_num, line in enumerate(lines, start=1):
            if not ORDER_BY_PATTERN.search(line):
                continue

            # Skip if the ORDER BY is inside a Python comment.
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue

            # Build a 5-line window (current + next 4) to capture multi-line
            # SQL strings that span until LIMIT or a closing triple-quote.
            window_end = min(line_num + 5, len(lines))
            window = "\n".join(lines[line_num - 1 : window_end])

            # Check for any acceptable deterministic-sort signal.
            has_rowid = bool(re.search(r"\browid\b", window))
            has_known_unique = any(
                re.search(rf"\b{col}\b", window) for col in KNOWN_UNIQUE_COLUMNS
            )
            has_exemption = "order-by-lint:" in window

            if not (has_rowid or has_known_unique or has_exemption):
                rel = py_path.relative_to(repos_dir.parent.parent.parent.parent)
                violations.append((
                    str(rel),
                    line_num,
                    line.strip()[:120],
                ))

    if violations:
        msg_lines = [
            f"Found {len(violations)} ORDER BY clause(s) in "
            "src/curator/storage/repositories/ without a deterministic "
            "tie-breaker:",
            "",
        ]
        for rel, line_num, snippet in violations:
            msg_lines.append(f"  {rel}:L{line_num}: {snippet}")
        msg_lines.extend([
            "",
            "Fix: append ``, rowid <DIR>`` to the ORDER BY clause (DIR",
            "matches the primary key's direction). Examples:",
            "",
            "  ORDER BY created_at DESC          ->  ORDER BY created_at DESC, rowid DESC",
            "  ORDER BY expires_at ASC           ->  ORDER BY expires_at ASC, rowid ASC",
            "  ORDER BY confidence DESC, ts DESC ->  ORDER BY confidence DESC, ts DESC, rowid DESC",
            "",
            "If the ORDER BY uses a column that is documented-unique (UUID,",
            "unique-within-scope), add it to KNOWN_UNIQUE_COLUMNS in this",
            "test, OR add an inline exemption comment on the same line:",
            "",
            "  ORDER BY curator_id  # order-by-lint: UUID is globally unique",
            "",
            "Why: SQLite's CURRENT_TIMESTAMP has second-level resolution.",
            "Two rows inserted in the same call tie on timestamp; tie-break",
            "order is implementation-defined and varies by SQLite version.",
            "v1.7.64 caught one such test failing on Windows-3.11/3.12 cells;",
            "v1.7.66 swept 13 sites across 7 repos. See v1.7.66 release notes.",
        ])
        pytest.fail("\n".join(msg_lines))
