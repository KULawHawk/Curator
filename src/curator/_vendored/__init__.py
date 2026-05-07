"""Vendored third-party dependencies.

These packages are vendored (copied) into Curator rather than declared
as PyPI dependencies. The reasons are documented in
``Github/CURATOR_RESEARCH_NOTES.md`` (Round 1 decisions D1-D26):

  * **ppdeep** (Apache-2.0) — pure-Python ssdeep fuzzy hashing. Single
    file, stable algorithm, no reason to track upstream releases.
    "Take and modify" per Round 1.

  * **send2trash** (BSD-3-Clause) — Windows Recycle Bin / macOS Trash /
    Linux freedesktop trash. Vendored so Curator's trash path is
    deterministic across systems where ``send2trash`` isn't installed.
    Phase Alpha vendors only the Windows path (``win/legacy.py`` —
    ctypes-based, no pywin32 dependency); Phase Beta expands to other
    platforms.

License files for each vendored package are alongside this file:

  * ``LICENSE-PPDEEP.txt``     — Apache-2.0 © Marcin Ulikowski
  * ``LICENSE-SEND2TRASH.txt`` — BSD-3-Clause © Hardcoded Software
                                    & Virgil Dupras

Vendored modules are imported via the ``curator._vendored.*`` namespace.
Consumers use the existing graceful-degradation pattern::

    try:
        from curator._vendored.ppdeep import compare
    except ImportError:
        try:
            from ppdeep import compare  # PyPI fallback
        except ImportError:
            compare = None

so the package still works during development before vendoring lands,
or in environments where vendored modules can't load (unlikely).
"""

# Bundle versions track the upstream commit/tag we forked from. Update
# these when re-syncing to a newer upstream.
__bundle_versions__ = {
    "ppdeep": "20260221",
    "send2trash": "1.8.3 (Windows-only subset)",
}
