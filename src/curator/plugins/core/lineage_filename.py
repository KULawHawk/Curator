"""VERSION_OF lineage detector — filename-based version chains.

DESIGN.md §8.3.3.

Detects sibling files that look like version-numbered variants of the
same logical file::

    Stats_II_v1.bas
    Stats_II_v2.bas
    Stats_II_v3.bas        => VERSION_OF chain

    Report.docx
    Report (1).docx        => VERSION_OF (Windows-style copy)
    Report - Copy.docx     => VERSION_OF (Windows-style copy)

The detector emits an edge from the older to the newer file when the
basenames + extensions match modulo a recognized version pattern.

Confidence: 0.85 — high enough to auto-confirm but not 1.0 because
filename heuristics can be wrong (e.g. ``v1.0.bas`` and ``v2.0.bas``
might be unrelated drafts that just happen to share a naming scheme).
"""

from __future__ import annotations

import re
from pathlib import Path

from curator.models.file import FileEntity
from curator.models.lineage import LineageEdge, LineageKind
from curator.plugins.hookspecs import hookimpl


DETECTOR_NAME = "curator.core.lineage_filename"

# Patterns are tried in order; first match wins.
# Each pattern names the same three groups: ``base``, ``version``, ``ext``.
VERSION_PATTERNS: list[re.Pattern] = [
    # "name_v2.ext", "name v2.ext", "name-v2.ext", "name v2.5.1.ext"
    re.compile(r"^(?P<base>.+?)[\s_-]?v(?P<version>\d+(?:\.\d+)*)\.(?P<ext>\w+)$", re.IGNORECASE),
    # "name (1).ext", "name (2).ext"  (Windows duplicate-paste convention)
    re.compile(r"^(?P<base>.+?)[\s_-]?\((?P<version>\d+)\)\.(?P<ext>\w+)$"),
    # "name - Copy.ext", "name - Copy (2).ext"  (Windows copy convention)
    re.compile(
        r"^(?P<base>.+?)[\s_-]Copy(?:[\s_-]?\((?P<version>\d+)\))?\.(?P<ext>\w+)$",
        re.IGNORECASE,
    ),
    # "name_2.ext"  (trailing-number-only, low specificity — last resort)
    re.compile(r"^(?P<base>.+?)[\s_-](?P<version>\d+)\.(?P<ext>\w+)$"),
]


def _parse_versioned(name: str) -> tuple[str, str, str] | None:
    """Return (base, version, ext) if ``name`` matches a version pattern.

    Note: the "Copy" pattern allows ``version`` to be missing (when the
    filename is just ``Foo - Copy.ext`` with no number); we treat that as
    version "1".
    """
    for pat in VERSION_PATTERNS:
        m = pat.match(name)
        if m is not None:
            base = m.group("base")
            version = m.group("version") or "1"
            ext = m.group("ext")
            return base, version, ext
    return None


def _version_sort_key(version: str) -> tuple:
    """Convert "1.2.10" -> (1, 2, 10) for proper numeric ordering."""
    try:
        return tuple(int(p) for p in version.split("."))
    except ValueError:
        return (version,)


class Plugin:
    """VERSION_OF detector via filename pattern matching."""

    @hookimpl
    def curator_compute_lineage(
        self,
        file_a: FileEntity,
        file_b: FileEntity,
    ) -> LineageEdge | None:
        if file_a.curator_id == file_b.curator_id:
            return None

        # Files must be in the same directory to be considered siblings.
        # (Prevents false positives across unrelated parts of a tree.)
        path_a = Path(file_a.source_path)
        path_b = Path(file_b.source_path)
        if path_a.parent != path_b.parent:
            return None

        parsed_a = _parse_versioned(path_a.name)
        parsed_b = _parse_versioned(path_b.name)
        if parsed_a is None or parsed_b is None:
            return None

        base_a, ver_a, ext_a = parsed_a
        base_b, ver_b, ext_b = parsed_b

        # Same base name and same extension required.
        if base_a.casefold() != base_b.casefold() or ext_a.casefold() != ext_b.casefold():
            return None

        # Distinct versions required (same version with different
        # filenames would be weird and is not handled here).
        if ver_a == ver_b:
            return None

        # Direct edge from older -> newer.
        key_a = _version_sort_key(ver_a)
        key_b = _version_sort_key(ver_b)
        if key_a < key_b:
            from_id, to_id = file_a.curator_id, file_b.curator_id
            from_v, to_v = ver_a, ver_b
        else:
            from_id, to_id = file_b.curator_id, file_a.curator_id
            from_v, to_v = ver_b, ver_a

        return LineageEdge(
            from_curator_id=from_id,
            to_curator_id=to_id,
            edge_kind=LineageKind.VERSION_OF,
            confidence=0.85,
            detected_by=DETECTOR_NAME,
            notes=f"version chain: {from_v} -> {to_v}",
        )
