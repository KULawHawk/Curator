"""Tests for curator.cli.util — TTY+UTF-8 detection and glyph fallbacks.

v1.7.30: codifies the lesson-#50 fix into a reusable helper. Tests verify
both the detection logic and the fallback substitution.
"""
from __future__ import annotations

import importlib
import io
import sys
from unittest.mock import patch

import pytest

import curator.cli.util as util_module


# ---------------------------------------------------------------------------
# Module-level constant integrity
# ---------------------------------------------------------------------------

def test_all_8_constants_exported() -> None:
    """The 8 glyph constants must exist as module attributes."""
    for name in ("CHECK", "CROSS", "ARROW", "LARROW", "ELLIPSIS",
                "BLOCK", "TIMES", "WARN"):
        assert hasattr(util_module, name), f"missing constant: {name}"
        value = getattr(util_module, name)
        assert isinstance(value, str), f"{name} must be str; got {type(value)}"
        assert len(value) >= 1, f"{name} must be non-empty"


def test_fallback_table_covers_all_constants() -> None:
    """Every glyph constant must have a fallback entry in _GLYPH_FALLBACKS.

    Reasoning: a constant without a fallback would crash cp1252 the moment
    a non-TTY consumer hits it, defeating the entire point of this module.
    """
    glyph_to_const_name = {
        "\u2713": "CHECK",
        "\u2717": "CROSS",
        "\u2192": "ARROW",
        "\u2190": "LARROW",
        "\u2026": "ELLIPSIS",
        "\u2588": "BLOCK",
        "\u00d7": "TIMES",
        "\u26a0": "WARN",
    }
    for glyph, const_name in glyph_to_const_name.items():
        assert glyph in util_module._GLYPH_FALLBACKS, \
            f"{const_name} ({glyph!r}) missing from _GLYPH_FALLBACKS"


# ---------------------------------------------------------------------------
# Detection logic — the TRUTH TABLE that lesson-#50 codified
# ---------------------------------------------------------------------------

class FakeStdout:
    """A minimal sys.stdout substitute for testing _stdout_supports_unicode."""
    def __init__(self, *, isatty: bool, encoding: str | None):
        self._isatty = isatty
        self.encoding = encoding

    def isatty(self) -> bool:
        return self._isatty


def _reload_util() -> None:
    """Reload the module so the lru_cache on _stdout_supports_unicode resets."""
    importlib.reload(util_module)


@pytest.mark.parametrize(
    "isatty,encoding,expected",
    [
        # NOT a tty -> always False (subprocess pipe, file redirect)
        (False, "utf-8",   False),
        (False, "cp1252",  False),
        (False, None,      False),
        # TTY + UTF-* encoding -> True (modern terminal)
        (True,  "utf-8",   True),
        (True,  "UTF-8",   True),
        (True,  "utf8",    True),    # no dash form
        (True,  "utf-16",  True),    # any UTF flavor
        # TTY + non-UTF encoding -> False (legacy cmd.exe, mostly)
        (True,  "cp1252",  False),
        (True,  "latin-1", False),
        (True,  "ascii",   False),
        # TTY + None encoding -> False (rare edge case)
        (True,  None,      False),
    ],
)
def test_detection_truth_table(
    isatty: bool, encoding: str | None, expected: bool,
) -> None:
    fake = FakeStdout(isatty=isatty, encoding=encoding)
    with patch.object(sys, "stdout", fake):
        _reload_util()
        assert util_module._stdout_supports_unicode() is expected, (
            f"isatty={isatty} encoding={encoding!r} -> expected {expected}"
        )


# ---------------------------------------------------------------------------
# Constant resolution under each branch
# ---------------------------------------------------------------------------

def test_constants_are_unicode_in_utf8_tty() -> None:
    """In a real UTF-8 TTY, constants are the original glyphs."""
    fake = FakeStdout(isatty=True, encoding="utf-8")
    with patch.object(sys, "stdout", fake):
        _reload_util()
        assert util_module.CHECK == "\u2713"
        assert util_module.CROSS == "\u2717"
        assert util_module.ARROW == "\u2192"
        assert util_module.LARROW == "\u2190"
        assert util_module.ELLIPSIS == "\u2026"
        assert util_module.BLOCK == "\u2588"
        assert util_module.TIMES == "\u00d7"
        assert util_module.WARN == "\u26a0"


def test_constants_are_ascii_under_subprocess() -> None:
    """When stdout is captured (isatty=False), constants are ASCII fallbacks."""
    fake = FakeStdout(isatty=False, encoding="utf-8")
    with patch.object(sys, "stdout", fake):
        _reload_util()
        assert util_module.CHECK == "[OK]"
        assert util_module.CROSS == "[X]"
        assert util_module.ARROW == "->"
        assert util_module.LARROW == "<-"
        assert util_module.ELLIPSIS == "..."
        assert util_module.BLOCK == "#"
        assert util_module.TIMES == "x"
        assert util_module.WARN == "!"


def test_constants_are_ascii_under_cp1252_console() -> None:
    """Legacy Windows cmd.exe (TTY but cp1252) gets ASCII fallbacks.

    This is the exact case lesson-#50 strikes #1, #3, and #5 hit.
    """
    fake = FakeStdout(isatty=True, encoding="cp1252")
    with patch.object(sys, "stdout", fake):
        _reload_util()
        assert util_module.CHECK == "[OK]"
        assert util_module.CROSS == "[X]"
        assert util_module.ARROW == "->"
        assert util_module.WARN == "!"


# ---------------------------------------------------------------------------
# safe_glyphs() — arbitrary text substitution
# ---------------------------------------------------------------------------

def test_safe_glyphs_passthrough_when_utf8() -> None:
    """In a UTF-8 TTY, text passes through unchanged."""
    fake = FakeStdout(isatty=True, encoding="utf-8")
    with patch.object(sys, "stdout", fake):
        _reload_util()
        text = "Result: \u2713 success, \u2717 failure, src \u2192 dst"
        assert util_module.safe_glyphs(text) == text


def test_safe_glyphs_substitutes_under_subprocess() -> None:
    """Under non-TTY, all glyphs in the table get replaced."""
    fake = FakeStdout(isatty=False, encoding="utf-8")
    with patch.object(sys, "stdout", fake):
        _reload_util()
        text = "src \u2192 dst, score \u2713 (\u00d72)"
        out = util_module.safe_glyphs(text)
        assert "\u2192" not in out
        assert "\u2713" not in out
        assert "\u00d7" not in out
        # The ASCII forms should be present
        assert "->" in out
        assert "[OK]" in out
        assert "x" in out


def test_safe_glyphs_leaves_unknown_glyphs_alone() -> None:
    """Glyphs not in the fallback table pass through (they're not crash-risks
    we've identified yet)."""
    fake = FakeStdout(isatty=False, encoding="utf-8")
    with patch.object(sys, "stdout", fake):
        _reload_util()
        # An unrelated glyph that isn't in our table
        text = "alpha \u03b1 stays"
        out = util_module.safe_glyphs(text)
        assert "\u03b1" in out


def test_safe_glyphs_handles_empty_string() -> None:
    fake = FakeStdout(isatty=False, encoding="utf-8")
    with patch.object(sys, "stdout", fake):
        _reload_util()
        assert util_module.safe_glyphs("") == ""


# ---------------------------------------------------------------------------
# Crash-resistance — the original lesson-#50 failure mode
# ---------------------------------------------------------------------------

def test_constants_safe_to_encode_as_cp1252_under_subprocess() -> None:
    """The point of the whole module: these constants must be safe to encode
    as cp1252 when the runtime decides they're in fallback mode.

    Before v1.7.30, code like ``console.print(f"[green]\\u2713[/]")`` would
    crash with ``UnicodeEncodeError: 'charmap' codec can't encode character
    '\\u2713' in position N: character maps to <undefined>``. The fix:
    when fallback mode is active, the constant value IS ascii.
    """
    fake = FakeStdout(isatty=False, encoding="utf-8")
    with patch.object(sys, "stdout", fake):
        _reload_util()
        for name in ("CHECK", "CROSS", "ARROW", "LARROW", "ELLIPSIS",
                     "BLOCK", "TIMES", "WARN"):
            value = getattr(util_module, name)
            # This MUST NOT raise UnicodeEncodeError
            value.encode("cp1252")  # raises if any non-cp1252 glyph survives


def test_safe_glyphs_output_safe_to_encode_as_cp1252() -> None:
    """Whatever safe_glyphs returns under fallback mode must encode as cp1252."""
    fake = FakeStdout(isatty=False, encoding="utf-8")
    with patch.object(sys, "stdout", fake):
        _reload_util()
        # The constants AND realistic text built from them
        text = "all glyphs: \u2713 \u2717 \u2192 \u2190 \u2026 \u2588 \u00d7 \u26a0"
        out = util_module.safe_glyphs(text)
        out.encode("cp1252")  # must not raise


# ---------------------------------------------------------------------------
# Module reload at import resets the lru_cache (regression guard)
# ---------------------------------------------------------------------------

def test_reload_invalidates_cache() -> None:
    """importlib.reload() must produce a fresh _stdout_supports_unicode
    (used by the test infrastructure above; verify the assumption is real)."""
    fake1 = FakeStdout(isatty=False, encoding="utf-8")
    with patch.object(sys, "stdout", fake1):
        _reload_util()
        assert util_module._stdout_supports_unicode() is False
        assert util_module.CHECK == "[OK]"

    fake2 = FakeStdout(isatty=True, encoding="utf-8")
    with patch.object(sys, "stdout", fake2):
        _reload_util()
        assert util_module._stdout_supports_unicode() is True
        assert util_module.CHECK == "\u2713"
