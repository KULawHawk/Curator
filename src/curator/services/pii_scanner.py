"""PII (Personally Identifiable Information) regex scanner.

T-B04 from ``docs/FEATURE_TODO.md``. The detector covers the high-value
patterns that have unambiguous, low-false-positive regex signatures:

  * **SSN** (US Social Security Number) — ``XXX-XX-XXXX``
  * **Credit card** — 13–16 digit groups (no Luhn validation yet)
  * **Phone** (US, common formats) — ``(XXX) XXX-XXXX`` and variants
  * **Email** — ``user@host.tld``

Each match has a ``severity`` (HIGH / MEDIUM) so callers can apply
graduated policy. SSN and credit card are HIGH (clearly sensitive in
almost any context). Phone and email are MEDIUM (also PII, but
appear so often in legitimate files that surfacing them as HIGH
would drown the signal in noise).

This is the **regex baseline**. The FEATURE_TODO calls out a future
``T-D02`` Conclave hookspec for semantic detection (e.g. names in
context, medical record patterns that vary by institution). The
hookspec is **deliberately not yet defined** — ship the easy 90% first;
let the data tell us what the next 10% needs to look like.

Design constraints:

  * **Pure-Python regex.** No external deps. Compiled once at service
    init for speed across batch scans.
  * **Size-capped reads.** Scanning a 10 GB CSV byte-by-byte is fine in
    principle but blows up memory in practice. We cap at 2 MB (text
    content; binary content is skipped). Configurable via
    ``head_bytes`` kwarg.
  * **No file modification.** This service is detect-only. Quarantine /
    redaction / refusal-to-migrate is a downstream consumer's
    responsibility (a future ``T-B07``-style export pipeline OR a
    ``curator_validate_file`` plugin).
  * **Best-effort decoding.** Files that aren't valid UTF-8 are decoded
    with errors='replace'; we'd rather find PII in a partially-decoded
    file than miss it because of one bad byte.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------


class PIISeverity(str, Enum):
    """Severity buckets for PII patterns."""

    HIGH = "high"      # SSN, credit card
    MEDIUM = "medium"  # phone, email


@dataclass(frozen=True)
class PIIPattern:
    """A named regex pattern with severity + a redaction helper.

    ``redact`` returns a string suitable for display logs (e.g.
    "XXX-XX-1234" instead of the full SSN). Useful for surfacing a
    finding without re-leaking the PII in a log file.
    """

    name: str
    pattern: re.Pattern[str]
    severity: PIISeverity
    description: str

    def redact(self, value: str) -> str:
        """Return a redacted version of ``value`` for safe logging.

        Default: keep only the last 4 visible characters. Subclasses
        could override for category-specific redaction (e.g. mask the
        domain portion of an email while keeping the user part).
        """
        if len(value) <= 4:
            return "*" * len(value)
        return ("*" * (len(value) - 4)) + value[-4:]


@dataclass
class PIIMatch:
    """A single PII detection result."""

    pattern_name: str
    severity: PIISeverity
    matched_text: str  # The raw match. CALLER decides whether to log this.
    redacted: str      # Safe to log: e.g. "XXX-XX-1234"
    offset: int        # Byte offset (in the source text) of the match
    line: int          # 1-based line number


@dataclass
class PIIScanReport:
    """Result of scanning a single file (or text blob)."""

    source: str  # path or label
    bytes_scanned: int
    truncated: bool  # True if the file was larger than head_bytes
    matches: list[PIIMatch] = field(default_factory=list)
    error: str | None = None

    @property
    def has_high_severity(self) -> bool:
        return any(m.severity == PIISeverity.HIGH for m in self.matches)

    @property
    def match_count(self) -> int:
        return len(self.matches)

    def by_pattern(self) -> dict[str, int]:
        """Count matches grouped by pattern name."""
        out: dict[str, int] = {}
        for m in self.matches:
            out[m.pattern_name] = out.get(m.pattern_name, 0) + 1
        return out


# ---------------------------------------------------------------------------
# Default pattern set
# ---------------------------------------------------------------------------
#
# These are the v1.7.6 baseline. Adding more is a one-line append to
# DEFAULT_PATTERNS in a future version (just be sure to write a test
# that exercises both true positives and a known false-positive case
# for the new pattern).


def _build_default_patterns() -> list[PIIPattern]:
    """Construct the default pattern list. Called once at module import."""
    return [
        PIIPattern(
            name="ssn",
            # US SSN: XXX-XX-XXXX. Word boundaries on both sides to avoid
            # matching inside longer numeric runs (e.g. order IDs).
            # Negative-lookahead `(?!0{3})` etc. could exclude impossible
            # SSN ranges; intentionally not added yet -- too easy to
            # introduce false negatives, and the goal here is recall
            # over precision (a human will eyeball matches before any
            # destructive action).
            pattern=re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
            severity=PIISeverity.HIGH,
            description="US Social Security Number (XXX-XX-XXXX format)",
        ),
        PIIPattern(
            name="credit_card",
            # 13-16 digit cards, optionally separated by spaces or dashes.
            # No Luhn validation yet; FEATURE_TODO says regex baseline.
            # Adding Luhn would cut false positives ~10x but is its own
            # mini-feature; defer.
            pattern=re.compile(
                r"\b(?:\d[ -]?){13,16}\b"
            ),
            severity=PIISeverity.HIGH,
            description="Credit card number (13-16 digits, no Luhn check)",
        ),
        PIIPattern(
            name="phone_us",
            # Common US phone formats:
            #   (XXX) XXX-XXXX  XXX-XXX-XXXX  XXX.XXX.XXXX  XXXXXXXXXX
            # Word boundaries to avoid grabbing parts of longer numeric
            # runs. Optional leading +1.
            pattern=re.compile(
                r"(?:\+?1[-.\s]?)?\(?\b\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"
            ),
            severity=PIISeverity.MEDIUM,
            description="US phone number (various formats)",
        ),
        PIIPattern(
            name="email",
            # Standard email regex. Intentionally permissive on the
            # local part; restrictive on the domain (require a dot).
            pattern=re.compile(
                r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b"
            ),
            severity=PIISeverity.MEDIUM,
            description="Email address",
        ),
    ]


DEFAULT_PATTERNS: list[PIIPattern] = _build_default_patterns()


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class PIIScanner:
    """Stateless PII detector.

    Construct once; call :meth:`scan_text` or :meth:`scan_file` many
    times. Reuses compiled regex patterns across calls. Safe to share
    between threads (no instance mutation after __init__).

    Args:
        patterns: optional override of the default pattern set. Pass
                  a subset to scope detection (e.g. SSN-only).
        head_bytes: max bytes to read per file. Files larger than this
                    are truncated to the first N bytes and the report's
                    ``truncated`` field is set. Default: 2 MB.
    """

    DEFAULT_HEAD_BYTES: int = 2 * 1024 * 1024  # 2 MB

    def __init__(
        self,
        patterns: list[PIIPattern] | None = None,
        *,
        head_bytes: int = DEFAULT_HEAD_BYTES,
    ) -> None:
        self.patterns: list[PIIPattern] = (
            list(patterns) if patterns is not None else list(DEFAULT_PATTERNS)
        )
        self.head_bytes: int = head_bytes

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def scan_text(self, text: str, *, source: str = "<text>") -> PIIScanReport:
        """Scan a string for PII matches.

        Returns a fully-populated :class:`PIIScanReport`. The ``source``
        kwarg is a label for the report (e.g. a path); doesn't affect
        scanning logic.
        """
        matches: list[PIIMatch] = []
        for pat in self.patterns:
            for m in pat.pattern.finditer(text):
                matched = m.group(0)
                offset = m.start()
                # Compute 1-based line number cheaply via slice count
                line = text.count("\n", 0, offset) + 1
                matches.append(PIIMatch(
                    pattern_name=pat.name,
                    severity=pat.severity,
                    matched_text=matched,
                    redacted=pat.redact(matched),
                    offset=offset,
                    line=line,
                ))
        # Stable sort: by offset
        matches.sort(key=lambda m: m.offset)

        return PIIScanReport(
            source=source,
            bytes_scanned=len(text.encode("utf-8", errors="ignore")),
            truncated=False,
            matches=matches,
        )

    def scan_file(self, path: str | Path) -> PIIScanReport:
        """Scan a file's first ``head_bytes`` for PII matches.

        Returns a :class:`PIIScanReport`. On read error, returns a
        report with ``error`` set and ``matches=[]``.

        Files that decode cleanly as UTF-8 are scanned in full (up to
        head_bytes). Files with invalid bytes are decoded with
        ``errors='replace'`` so we don't miss PII because of one
        encoding quirk; the replacement chars don't match any pattern
        so this doesn't introduce false positives.
        """
        p = Path(path)
        if not p.is_file():
            return PIIScanReport(
                source=str(p),
                bytes_scanned=0, truncated=False,
                error=f"Not a file (or doesn't exist): {p}",
            )

        try:
            file_size = p.stat().st_size
            with open(p, "rb") as f:
                raw = f.read(self.head_bytes)
            truncated = file_size > self.head_bytes
        except Exception as e:  # noqa: BLE001 -- best-effort
            return PIIScanReport(
                source=str(p),
                bytes_scanned=0, truncated=False,
                error=f"{type(e).__name__}: {e}",
            )

        try:
            text = raw.decode("utf-8", errors="replace")
        except Exception as e:  # noqa: BLE001 -- should be impossible w/ errors=replace
            return PIIScanReport(
                source=str(p),
                bytes_scanned=len(raw), truncated=truncated,
                error=f"Decode failed: {type(e).__name__}: {e}",
            )

        # Reuse scan_text but override source label and truncation flag
        rpt = self.scan_text(text, source=str(p))
        rpt.bytes_scanned = len(raw)
        rpt.truncated = truncated
        return rpt

    def scan_directory(
        self,
        directory: str | Path,
        *,
        recursive: bool = True,
        extensions: list[str] | None = None,
    ) -> list[PIIScanReport]:
        """Scan every file under ``directory``. Returns one report per file.

        Args:
            directory: root path.
            recursive: walk subdirectories (default True).
            extensions: optional whitelist of file extensions (with leading
                        dot, lowercase, e.g. ['.txt', '.csv']). If None,
                        scans every file.
        """
        d = Path(directory)
        if not d.is_dir():
            return [PIIScanReport(
                source=str(d),
                bytes_scanned=0, truncated=False,
                error=f"Not a directory: {d}",
            )]

        ext_set: set[str] | None = (
            {e.lower() for e in extensions} if extensions else None
        )

        reports: list[PIIScanReport] = []
        iterator = d.rglob("*") if recursive else d.glob("*")
        for child in iterator:
            if not child.is_file():
                continue
            if ext_set is not None and child.suffix.lower() not in ext_set:
                continue
            reports.append(self.scan_file(child))
        return reports
