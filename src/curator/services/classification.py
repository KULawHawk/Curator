"""Classification service — orchestrates ``curator_classify_file`` plugins.

DESIGN.md §2.2.1, §5.

Multiple plugins may volunteer classifications for the same file (e.g.
filetype.py-based detector + a Phase Beta python-magic plugin + a
domain-specific clinical-file detector). This service collects all
non-None results and selects the best by confidence, with a tiebreaker
that prefers the plugin Curator considers most authoritative.

The selected classification is written back to the FileEntity:
``file_type``, ``extension`` (when the classifier provided one),
``file_type_confidence``. Persistence is the caller's responsibility
(typically the ScanService).
"""

from __future__ import annotations

from typing import Optional

import pluggy

from curator.models.file import FileEntity
from curator.models.results import FileClassification


class ClassificationService:
    """Run classifier plugins and apply the best result to a FileEntity."""

    def __init__(self, plugin_manager: pluggy.PluginManager):
        self.pm = plugin_manager

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def classify(self, file: FileEntity) -> Optional[FileClassification]:
        """Run all classifiers and return the best candidate (or None).

        Does NOT mutate ``file``. Use :meth:`apply` for the in-place
        version typically called by the scan orchestrator.
        """
        candidates = self._collect(file)
        return self._select_best(candidates) if candidates else None

    def apply(self, file: FileEntity) -> Optional[FileClassification]:
        """Run all classifiers and write the best result onto ``file``.

        Returns the chosen :class:`FileClassification` (or None if no
        plugin had an opinion). The caller persists ``file`` afterward.
        """
        chosen = self.classify(file)
        if chosen is None:
            return None

        file.file_type = chosen.file_type
        if chosen.extension is not None:
            # Only override extension if the classifier proposed one;
            # otherwise we keep whatever the source plugin reported.
            file.extension = chosen.extension
        file.file_type_confidence = chosen.confidence
        return chosen

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _collect(self, file: FileEntity) -> list[FileClassification]:
        """Call the hook and return all non-None classifications."""
        results = self.pm.hook.curator_classify_file(file=file)
        return [r for r in results if r is not None]

    def _select_best(
        self, candidates: list[FileClassification]
    ) -> FileClassification:
        """Choose the highest-confidence classification.

        Tiebreaker: more specific MIME types win over ``application/octet-stream``
        and ``text/plain`` (those are the "I have no better idea" fallbacks).
        Final tiebreaker: lexical order on ``classifier`` name (deterministic).
        """
        def specificity(c: FileClassification) -> int:
            t = (c.file_type or "").lower()
            if t in {"application/octet-stream", "text/plain"}:
                return 0
            return 1

        # Sort by (confidence desc, specificity desc, classifier asc).
        candidates.sort(key=lambda c: (-c.confidence, -specificity(c), c.classifier))
        return candidates[0]
