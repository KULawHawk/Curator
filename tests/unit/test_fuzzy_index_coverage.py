"""Focused coverage tests for services/fuzzy_index.py.

Sub-ship v1.7.96 of the Coverage Sweep arc.

Closes the one uncovered region: lines 166-167, the
`except ImportError` branch in `FuzzyIndex.__init__` that
fires when `datasketch` is not installed.

The test temporarily replaces `sys.modules['datasketch']` with None
so the `from datasketch import MinHashLSH` raises ImportError,
exercising the FuzzyIndexUnavailableError translation.
"""

from __future__ import annotations

import sys

import pytest

from curator.services.fuzzy_index import (
    FuzzyIndex,
    FuzzyIndexUnavailableError,
)


def test_init_raises_unavailable_when_datasketch_missing(monkeypatch):
    # Lines 164-170: simulate datasketch not installed. Setting
    # sys.modules[name] = None is the canonical pattern that makes
    # `from name import X` raise ImportError (per Python import semantics).
    monkeypatch.setitem(sys.modules, "datasketch", None)

    with pytest.raises(FuzzyIndexUnavailableError, match="datasketch is not installed"):
        FuzzyIndex()
