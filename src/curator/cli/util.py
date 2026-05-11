"""User-facing CLI output utilities.

Codifies the lesson #50 fix that recurred FIVE times across the
v1.7.21 -> v1.7.29 arc: Unicode glyphs in user-facing strings crash
the Windows cp1252 encoder when stdout is piped (non-TTY). Without
TTY+UTF-8 detection, every glyph (check, arrow, ellipsis, block)
is a latent crash bug.

The strikes (for institutional memory):

  * v1.7.21 - histogram U+2588 (FULL BLOCK)
  * v1.7.24 - TTY-aware bar fallback codified (but only for one glyph)
  * v1.7.25 - tier --apply U+2192 (right arrow) and U+2026 (ellipsis)
  * v1.7.28 - U+2713 (check) in test scaffolding
  * v1.7.29 - PRE-EXISTING U+2713 in sources_config CLI -- undetected
              until a subprocess test exercised the path

The pre-existing strike is the urgent signal: the bug had been in
the codebase for an unknown duration, undetected, because no
existing test exercised that code path via subprocess. This module
exists so future contributors don't repeat the mistake -- the
constants (:data:`CHECK`, :data:`ARROW`, :data:`ELLIPSIS`,
:data:`BLOCK`) automatically substitute ASCII fallbacks when stdout
isn't a UTF-8-capable TTY.

Usage in user-facing strings::

    from curator.cli.util import CHECK, ARROW, ELLIPSIS

    console.print(f"[green]{CHECK}[/] Updated config.")
    console.print(f"  [dim]{ELLIPSIS} and {n} more[/]")
    console.print(f"Migrating {src} {ARROW} {dst}")

Renders as the Unicode glyph in an interactive Windows Terminal /
VS Code terminal / macOS / Linux TTY (all of which report
``isatty() and encoding == 'utf-8'``). Renders as the ASCII
fallback (``[OK]``, ``->``, ``...``, ``#``) in subprocess pipes,
file redirects, and legacy cp1252 consoles.

For ad-hoc substitution of arbitrary text containing dangerous
glyphs, use :func:`safe_glyphs`.

This module is intentionally tiny -- one function and a constants
table. No external dependencies.
"""

from __future__ import annotations

import sys
from functools import lru_cache


@lru_cache(maxsize=1)
def _stdout_supports_unicode() -> bool:
    """True iff ``sys.stdout`` is an interactive TTY with a UTF-* encoding.

    Cached because the answer doesn't change during a process
    lifetime (``sys.stdout`` is fixed at startup and replacement
    via monkeypatching isn't a supported runtime path), and we
    may call this many times per render.

    Reasoning matrix:

      * isatty=False (subprocess pipe, file redirect)    -> False
      * isatty=True + encoding='cp1252' (legacy cmd.exe)  -> False
      * isatty=True + encoding='utf-8' (modern terminal)  -> True
      * isatty=True + encoding=None (rare edge case)     -> False
    """
    if not sys.stdout.isatty():
        return False
    enc = (sys.stdout.encoding or "").lower().replace("-", "")
    return enc.startswith("utf")


# ASCII fallbacks for the glyphs that have hit cp1252 in the v1.7.x arc.
# Adding a new fallback is a one-line table addition.
_GLYPH_FALLBACKS: dict[str, str] = {
    "\u2588": "#",     # FULL BLOCK -- audit-summary histograms (v1.7.21)
    "\u2713": "[OK]",  # CHECK MARK -- sources/bundle/trash success lines
    "\u2717": "[X]",   # BALLOT X -- error/failure lines (v1.7.30 audit)
    "\u2192": "->",    # RIGHTWARDS ARROW -- migration/tier "src -> dst"
    "\u2190": "<-",    # LEFTWARDS ARROW -- lineage display "a <- b" (v1.7.30)
    "\u2026": "...",   # HORIZONTAL ELLIPSIS -- "and N more" tails
    "\u00d7": "x",     # MULTIPLICATION SIGN -- dimensions/multiplication
    "\u26a0": "!",     # WARNING SIGN -- inline warnings (v1.7.30 audit)
    "\u00b2": "^2",    # SUPERSCRIPT TWO -- forecast R^2 (v1.7.33 audit, 6th strike)
}


def safe_glyphs(text: str) -> str:
    """Substitute TTY-unsafe Unicode codepoints with ASCII fallbacks.

    When stdout is an interactive UTF-8 TTY, returns ``text`` unchanged.
    Otherwise, each glyph in :data:`_GLYPH_FALLBACKS` is replaced with
    its ASCII fallback so subprocess-captured output won't crash the
    cp1252 codec on Windows.

    Use this for arbitrary text (e.g. text from a database row or
    user input). For literal glyphs in your own code, prefer the
    convenience constants :data:`CHECK`, :data:`ARROW`, etc. -- they
    do the same substitution but read more naturally.

    Returns:
        The input string with unsafe glyphs replaced when needed.
    """
    if _stdout_supports_unicode():
        return text
    for unicode_char, ascii_fallback in _GLYPH_FALLBACKS.items():
        if unicode_char in text:
            text = text.replace(unicode_char, ascii_fallback)
    return text


def _const(unicode_char: str) -> str:
    """Return the unicode char if stdout supports it; else its ASCII fallback."""
    if _stdout_supports_unicode():
        return unicode_char
    return _GLYPH_FALLBACKS.get(unicode_char, unicode_char)


# Convenience constants. Computed once at import time (the underlying
# _stdout_supports_unicode is cached). Use these in f-strings instead
# of literal Unicode characters:
#
#     console.print(f"[green]{CHECK}[/] success!")
#     console.print(f"  {ELLIPSIS} and {n} more")
#     console.print(f"{src} {ARROW} {dst}")
#
# In an interactive UTF-8 terminal, they render as the pretty glyph.
# In a subprocess pipe or cp1252 console, they render as the ASCII
# fallback, avoiding the latent encoder crash.

CHECK = _const("\u2713")      # "\u2713" or "[OK]"
CROSS = _const("\u2717")      # "\u2717" or "[X]"   (v1.7.30)
ARROW = _const("\u2192")      # "\u2192" or "->"
LARROW = _const("\u2190")     # "\u2190" or "<-"   (v1.7.30)
ELLIPSIS = _const("\u2026")   # "\u2026" or "..."
BLOCK = _const("\u2588")      # "\u2588" or "#"
TIMES = _const("\u00d7")      # "\u00d7" or "x"
WARN = _const("\u26a0")       # "\u26a0" or "!"   (v1.7.30)
SUPER2 = _const("\u00b2")     # "\u00b2" or "^2"  (v1.7.33, R-squared)
