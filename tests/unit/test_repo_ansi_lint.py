"""Lint: no inline ANSI-strip regex patterns in tests/ outside conftest.py.

v1.7.73: codifies the v1.7.68 strip_ansi fixture hoist at the pytest level.
Any future test that introduces an inline ``re.sub(r"\\x1b\\[...", "", text)``
pattern instead of using the shared ``strip_ansi`` fixture will fail this
test with a pointer to the v1.7.68 refactor.

Background:
  v1.7.62 rush-fixed Rich/Typer help-output assertions by inlining the
  same ANSI-strip regex in 3 test files. v1.7.68 hoisted the pattern
  into a single ``strip_ansi`` pytest fixture in ``tests/conftest.py``.
  Without this lint, the next contributor could re-introduce an inline
  ``re.sub`` and reset the duplication clock.

The lint scans every ``.py`` file under ``tests/`` (excluding
``tests/conftest.py`` itself, which legitimately defines the
``_ANSI_ESCAPE_PATTERN`` and the ``strip_ansi`` fixture). For each line,
it flags occurrences of inline ANSI-escape regex patterns:

  - ``re.sub(r"\\x1b\\[...", ...)``
  - ``re.compile(r"\\x1b\\[...")``
  - Bare string literals containing the ``\\x1b[`` escape used in regex
    contexts (e.g. raw string ``r"\\x1b\\[[0-9;]*m"``)

Exemptions:
  - ``tests/conftest.py`` itself (defines the fixture)
  - This test file itself (contains the pattern in documentation)
  - Inline ``# ansi-lint: <reason>`` comment for legitimate exceptions

Fix message points to the ``strip_ansi`` fixture as the canonical approach.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


# The lint flags ANY line that contains an unescaped ANSI escape regex
# pattern: backslash-x1b-backslash-[. This is the exact pattern v1.7.62
# inlined and v1.7.68 hoisted.
ANSI_REGEX_PATTERN = re.compile(r"\\x1b\\\[")


# Files that legitimately contain the pattern (and thus are exempt):
#   - tests/conftest.py: defines the canonical strip_ansi fixture +
#     _ANSI_ESCAPE_PATTERN module constant
#   - tests/unit/test_repo_ansi_lint.py: this file, contains the
#     pattern in documentation and the regex constant above
EXEMPT_FILES: set[str] = {
    "conftest.py",
    "test_repo_ansi_lint.py",
}


def test_no_inline_ansi_strip_regex_outside_conftest() -> None:
    """Lint: no inline ANSI-strip regex patterns in tests/ outside conftest.py.

    Fails if any test file contains the ``\\x1b\\[`` regex pattern. The
    canonical approach (v1.7.68) is to use the ``strip_ansi`` pytest
    fixture defined in ``tests/conftest.py``.

    Exemptions:
      - ``tests/conftest.py`` itself
      - This test file
      - Inline ``# ansi-lint: <reason>`` comment on the violating line
    """
    # Locate tests/ relative to this file.
    tests_dir = Path(__file__).resolve().parent.parent
    assert tests_dir.is_dir() and tests_dir.name == "tests", (
        f"tests/ directory not found at {tests_dir}"
    )

    violations: list[tuple[str, int, str]] = []

    for py_path in tests_dir.rglob("*.py"):
        if py_path.name in EXEMPT_FILES:
            continue

        content = py_path.read_text(encoding="utf-8")
        lines = content.split("\n")

        for line_num, line in enumerate(lines, start=1):
            if not ANSI_REGEX_PATTERN.search(line):
                continue

            # Skip lines with an inline exemption.
            if "ansi-lint:" in line:
                continue

            rel = py_path.relative_to(tests_dir.parent)
            violations.append((
                str(rel),
                line_num,
                line.strip()[:120],
            ))

    if violations:
        msg_lines = [
            f"Found {len(violations)} inline ANSI-strip regex pattern(s) "
            "in tests/ outside conftest.py:",
            "",
        ]
        for rel, line_num, snippet in violations:
            msg_lines.append(f"  {rel}:L{line_num}: {snippet}")
        msg_lines.extend([
            "",
            "Fix: use the shared `strip_ansi` pytest fixture defined in",
            "tests/conftest.py instead of inlining the regex:",
            "",
            "  # Before",
            "  import re",
            "  output = re.sub(r\"\\x1b\\[[0-9;]*m\", \"\", result.output)",
            "",
            "  # After",
            "  def test_help(self, runner, strip_ansi):",
            "      result = runner.invoke(app, [\"--help\"])",
            "      output = strip_ansi(result.output)",
            "",
            "Why: v1.7.62 inlined this regex in 3 test files; v1.7.68",
            "hoisted it into a shared fixture (DRY refactor, ~9 lines",
            "saved per regression). The fixture pattern compiles the",
            "regex once at conftest import time and returns a callable.",
            "Inline regex re-compiles on every test call and duplicates",
            "the pattern across files. See v1.7.68 release notes.",
            "",
            "For legitimate exceptions (e.g. a non-color escape sequence",
            "you need to strip with different semantics), add an inline",
            "exemption comment on the same line:",
            "",
            "  output = re.sub(r\"\\x1b\\[2J\", \"\", text)  # ansi-lint: clear-screen sequence, not color",
        ])
        pytest.fail("\n".join(msg_lines))
