"""Focused coverage tests for services/classification.py.

Sub-ship v1.7.103 of the Coverage Sweep arc.

Closes line 86 + 2 partial branches:

* Line 86: `specificity()` returns 1 for a non-fallback file_type
  (anything that isn't `application/octet-stream` or `text/plain`).
* Branch 57->61: `apply()` skips the extension assignment when
  `chosen.extension is None`.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from curator.models.file import FileEntity
from curator.models.results import FileClassification
from curator.services.classification import ClassificationService


def _make_entity() -> FileEntity:
    return FileEntity(
        source_id="local",
        source_path="/x/y.bin",
        size=10,
        mtime=datetime.now(),
        extension=".bin",
    )


def test_apply_with_extension_none_keeps_existing_extension():
    # Branch 57->61: chosen.extension is None → skip the extension
    # assignment; file.extension stays as whatever the source plugin
    # had set ("\.bin" here).
    pm = MagicMock()
    classification = FileClassification(
        classifier="t", file_type="application/x-test",
        confidence=0.9, extension=None,
    )
    pm.hook.curator_classify_file.return_value = [classification]

    svc = ClassificationService(pm)
    entity = _make_entity()
    chosen = svc.apply(entity)

    assert chosen is classification
    assert entity.file_type == "application/x-test"
    # Extension untouched — chosen.extension was None.
    assert entity.extension == ".bin"


def test_apply_with_no_classifications_returns_none():
    # Line 54: chosen is None → early return None, file untouched.
    pm = MagicMock()
    pm.hook.curator_classify_file.return_value = []  # no classifiers volunteered

    svc = ClassificationService(pm)
    entity = _make_entity()
    result = svc.apply(entity)
    assert result is None


def test_apply_with_extension_set_overrides_file_extension():
    # Line 60 (True arm of branch 57->61): chosen.extension is not None
    # → file.extension is overwritten.
    pm = MagicMock()
    classification = FileClassification(
        classifier="t", file_type="image/jpeg",
        confidence=0.9, extension=".jpg",
    )
    pm.hook.curator_classify_file.return_value = [classification]

    svc = ClassificationService(pm)
    entity = _make_entity()  # starts with .bin
    chosen = svc.apply(entity)

    assert chosen is classification
    assert entity.file_type == "image/jpeg"
    # Extension overridden by classifier's choice.
    assert entity.extension == ".jpg"


def test_select_best_specificity_returns_one_for_concrete_mime():
    # Line 86: `return 1` for a file_type that ISN'T in the
    # fallback set. Test via _select_best directly with one
    # concrete-mime candidate; specificity is called during sort.
    pm = MagicMock()
    svc = ClassificationService(pm)
    concrete = FileClassification(
        classifier="b", file_type="image/jpeg",
        confidence=0.5, extension=".jpg",
    )
    fallback = FileClassification(
        classifier="a", file_type="application/octet-stream",
        confidence=0.5, extension=None,
    )

    # Equal confidence → specificity tiebreaker → concrete wins.
    best = svc._select_best([fallback, concrete])
    assert best is concrete
