"""Focused coverage tests for services/pii_scanner.py.

Sub-ship v1.7.99 of the Coverage Sweep arc.

Closes the remaining uncovered regions:

* Line 471 + partial branch — `_parse_jwt_claims` defensive return
  when the base64-decoded JSON payload (or header) is not a dict.
* Lines 753-754 — `scan_file`'s `except Exception` around file
  read/stat, triggered by an OSError during open/read.

Companion source change in `services/pii_scanner.py`: the
`try/except` around `raw.decode("utf-8", errors="replace")` (lines
762-763) was provably unreachable per Python's documented behavior
(bytes.decode with errors="replace" is total). Removed for honesty
per doctrine item 1.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from curator.services.pii_scanner import PIIScanner, _parse_jwt


def _b64url(obj) -> str:
    """JSON-encode obj and base64-urlsafe-encode without padding."""
    raw = json.dumps(obj).encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def test_parse_jwt_non_dict_payload_returns_none():
    # Line 470-471: when payload (or header) decodes to a non-dict
    # JSON value (e.g. a JSON array), `_parse_jwt` returns None.
    header = _b64url({"alg": "HS256", "typ": "JWT"})
    payload = _b64url([1, 2, 3])  # NON-DICT
    signature = "sigsig"  # opaque; not validated
    token = f"{header}.{payload}.{signature}"

    claims = _parse_jwt(token)
    assert claims is None


def test_scan_file_read_oserror_returns_report_with_error(tmp_path):
    # Lines 752-758: when open()/read() raises an exception (e.g.
    # OSError from a file vanished mid-scan, or permission denied),
    # scan_file returns a PIIScanReport with the error message
    # populated rather than letting the exception propagate.
    target = tmp_path / "file.txt"
    target.write_text("dummy")

    scanner = PIIScanner()
    # Patch builtins.open in the pii_scanner module to raise.
    with patch(
        "curator.services.pii_scanner.open",
        side_effect=OSError("simulated read failure"),
        create=True,
    ):
        report = scanner.scan_file(target)

    assert report.bytes_scanned == 0
    assert report.truncated is False
    assert report.error is not None
    assert "OSError" in report.error
    assert "simulated read failure" in report.error
