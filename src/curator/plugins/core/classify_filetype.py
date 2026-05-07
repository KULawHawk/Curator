"""filetype.py-backed file classifier.

DESIGN.md §5.4 + D18.

Uses :mod:`filetype` (pure-Python, zero-install on Windows) to identify
files by reading the first 261 bytes for magic-byte detection. This is
Curator's primary classifier; richer descriptions via python-magic are
deferred to Phase Beta as an optional plugin.

The classifier opens the file directly (not via the source plugin) for
Phase Alpha simplicity. When we add cloud sources, this will switch to
using ``curator_source_read_bytes`` so it works across sources.
"""

from __future__ import annotations

from pathlib import Path

from curator.models.file import FileEntity
from curator.models.results import FileClassification
from curator.plugins.hookspecs import hookimpl


CLASSIFIER_NAME = "curator.core.classify_filetype"


class Plugin:
    """File classifier using the ``filetype`` library."""

    @hookimpl
    def curator_classify_file(self, file: FileEntity) -> FileClassification | None:
        # Phase Alpha: only handle local files. Cloud sources will route
        # through curator_source_read_bytes once that's wired.
        if not file.source_id.startswith("local"):
            return None

        # Lazy import — keeps plugin discovery fast and avoids hard
        # dependency at import time. ``filetype`` is in our base deps so
        # this should never fail in normal installs.
        try:
            import filetype  # type: ignore[import-not-found]
        except ImportError:
            return None

        path = Path(file.source_path)
        if not path.exists() or not path.is_file():
            return None

        try:
            kind = filetype.guess(str(path))
        except Exception:
            return None

        if kind is None:
            # ``filetype`` couldn't identify it. Fall back to extension
            # heuristic for plain text-ish files (these aren't covered by
            # filetype's magic-byte signatures).
            if path.suffix.lower() in _TEXT_EXTENSIONS:
                return FileClassification(
                    file_type="text/plain",
                    extension=path.suffix.lower(),
                    confidence=0.6,  # extension-only, lower confidence
                    classifier=CLASSIFIER_NAME,
                    notes="extension fallback (filetype.py had no signature match)",
                )
            return None

        return FileClassification(
            file_type=kind.mime,
            extension=f".{kind.extension}" if kind.extension else None,
            confidence=0.95,  # magic-byte match — high confidence
            classifier=CLASSIFIER_NAME,
        )


# Extension fallback set. The hash pipeline keeps the authoritative list
# in :mod:`curator.services.hash_pipeline`; this is a hint subset that
# covers the common cases filetype.py doesn't have signatures for.
_TEXT_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".py", ".bas", ".vb", ".md", ".txt", ".rst", ".json", ".yaml", ".yml",
        ".toml", ".ini", ".cfg", ".html", ".css", ".js", ".ts", ".sql",
        ".csv", ".tsv", ".log", ".xml",
    }
)
