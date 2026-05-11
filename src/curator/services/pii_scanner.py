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
from typing import Callable


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------


class PIISeverity(str, Enum):
    """Severity buckets for PII patterns."""

    HIGH = "high"      # SSN, credit card, API keys
    MEDIUM = "medium"  # phone, email
    LOW = "low"        # IP addresses (informational)


# ---------------------------------------------------------------------------
# Validation helpers (v1.7.10)
# ---------------------------------------------------------------------------


def _luhn_valid(value: str) -> bool:
    """Luhn checksum: standard credit card / IMEI validation.

    Extracts digits from ``value`` (separators ignored), then runs the
    Luhn algorithm: double every second digit from the right, sum
    digit-by-digit, return True iff the total mod 10 is zero.

    Rejects strings with fewer than 13 or more than 19 digits to
    avoid spurious matches on order numbers, tracking codes, etc.
    """
    digits = [int(c) for c in value if c.isdigit()]
    if len(digits) < 13 or len(digits) > 19:
        return False
    checksum = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        checksum += d
    return checksum % 10 == 0


def _ipv4_valid(value: str) -> bool:
    """Octet-range check on a dotted-quad string.

    The regex matches 1-3 digits in each of 4 positions; this validator
    rejects strings with any octet > 255. Catches false positives like
    ``999.888.777.666`` and ``800.123.456.789`` (which look IP-shaped
    but aren't valid). Also rejects leading-zero octets except for
    a literal ``0`` (so ``192.168.001.001`` is rejected since it's
    typically a typo, not a real IP).
    """
    parts = value.split(".")
    if len(parts) != 4:
        return False
    for p in parts:
        if not p.isdigit():
            return False
        # Reject leading zeros (except plain "0")
        if len(p) > 1 and p[0] == "0":
            return False
        if int(p) > 255:
            return False
    return True


@dataclass(frozen=True)
class PIIPattern:
    """A named regex pattern with severity + a redaction helper.

    ``redact`` returns a string suitable for display logs (e.g.
    "XXX-XX-1234" instead of the full SSN). Useful for surfacing a
    finding without re-leaking the PII in a log file.

    v1.7.10: optional ``validator`` callable. When set, matches that
    fail validation are dropped — used to enforce Luhn on credit cards
    and octet-range on IPv4 addresses. Cuts false positives
    substantially (Luhn alone eliminates ~90% of bogus credit card
    matches on random numeric strings).
    """

    name: str
    pattern: re.Pattern[str]
    severity: PIISeverity
    description: str
    validator: Callable[[str], bool] | None = None

    def is_valid(self, value: str) -> bool:
        """Run the optional validator. True if no validator is set."""
        if self.validator is None:
            return True
        try:
            return bool(self.validator(value))
        except Exception:  # noqa: BLE001 -- never let a validator crash a scan
            return False

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
            # v1.7.10: Luhn validator wired so matches like "1234-5678-9012-3456"
            # (regex-valid but checksum-invalid) get filtered out. Cuts ~10x
            # of false positives on random numeric strings.
            #
            # NOTE: the final `\d` (not `\d[ -]?`) prevents the inner optional
            # separator from greedily consuming a trailing space, which would
            # extend the match past the last digit and break last-4 redaction.
            # Pattern: 12-15 reps of "digit + optional separator" + final digit
            # = total of 13-16 digits.
            pattern=re.compile(
                r"\b(?:\d[ -]?){12,15}\d\b"
            ),
            severity=PIISeverity.HIGH,
            description="Credit card number (Luhn-validated, 13-16 digits)",
            validator=_luhn_valid,
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
        # ----- v1.7.10 additions ------------------------------------------
        PIIPattern(
            name="ipv4",
            # Dotted-quad regex; octet ranges enforced by _ipv4_valid.
            # Word boundaries via \b on each end so we don't match
            # partial matches inside longer numeric runs.
            pattern=re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
            severity=PIISeverity.LOW,
            description="IPv4 address (octet-range validated)",
            validator=_ipv4_valid,
        ),
        PIIPattern(
            name="github_pat",
            # Personal Access Tokens: ghp_ (classic), gho_ (OAuth user-to-server),
            # ghu_ (user-to-server), ghs_ (server-to-server), ghr_ (refresh).
            # Token body is 36 chars of [A-Za-z0-9] for ghp_ and 40 chars
            # for the newer fine-grained variants (github_pat_).
            pattern=re.compile(
                r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}\b"
            ),
            severity=PIISeverity.HIGH,
            description="GitHub Personal Access Token",
        ),
        PIIPattern(
            name="aws_access_key_id",
            # AKIA followed by 16 uppercase alphanumeric (IAM user keys).
            # ASIA prefix is also used for temporary STS credentials; cover both.
            pattern=re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
            severity=PIISeverity.HIGH,
            description="AWS Access Key ID",
        ),
        PIIPattern(
            name="slack_token",
            # Slack tokens: xoxb (bot), xoxp (user), xoxa (workspace),
            # xoxr (refresh). Format: prefix-numeric-numeric-alphanumeric.
            pattern=re.compile(
                r"\bxox[abprs]-\d{10,}-\d{10,}-[A-Za-z0-9]{20,}\b"
            ),
            severity=PIISeverity.HIGH,
            description="Slack API token",
        ),
        # ----- v1.7.11 additions: 3 high-value API key patterns -----------
        PIIPattern(
            name="google_api_key",
            # Google Cloud API keys: AIza prefix + 35 chars of
            # [A-Za-z0-9_-]. Used for Maps, Firebase, YouTube, GCP REST APIs.
            # The prefix is unique and the 39-char total length is fixed.
            pattern=re.compile(r"\bAIza[0-9A-Za-z\-_]{35}\b"),
            severity=PIISeverity.HIGH,
            description="Google API key (Maps/Firebase/YouTube/GCP)",
        ),
        PIIPattern(
            name="stripe_secret_key",
            # Stripe secret keys: sk_live_ (production), sk_test_ (test mode).
            # Followed by 24+ chars of [A-Za-z0-9]. Distinct from Stripe
            # publishable keys (pk_live_/pk_test_) which are intentionally
            # public; we only flag SECRET keys here.
            pattern=re.compile(
                r"\bsk_(?:live|test)_[A-Za-z0-9]{24,}\b"
            ),
            severity=PIISeverity.HIGH,
            description="Stripe secret key (live or test mode)",
        ),
        PIIPattern(
            name="openai_api_key",
            # OpenAI keys: legacy sk- prefix (48 char total) + new
            # sk-proj- project-scoped keys. Distinct from Stripe's sk_
            # (underscore not dash) so the patterns don't collide.
            # Pattern requires DASH after sk to avoid matching
            # Stripe's sk_live_/sk_test_ prefix.
            pattern=re.compile(
                r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{20,}\b"
            ),
            severity=PIISeverity.HIGH,
            description="OpenAI API key (sk- or sk-proj- prefix)",
        ),
        # ----- v1.7.12 additions: 3 more API key patterns -----------------
        PIIPattern(
            name="twilio_account_sid",
            # Twilio Account SID: AC prefix + exactly 32 lowercase hex chars.
            # Length is fixed by Twilio (34 chars total). The lowercase
            # hex constraint distinguishes from generic AC-prefixed strings.
            pattern=re.compile(r"\bAC[a-f0-9]{32}\b"),
            severity=PIISeverity.HIGH,
            description="Twilio Account SID (AC prefix + 32 hex chars)",
        ),
        PIIPattern(
            name="mailgun_api_key",
            # Mailgun keys: classic 'key-' prefix (deprecated but still in
            # use) + newer 'private-' / 'pubkey-' formats. All followed
            # by 32 chars. Hex-only for the new format; alphanumeric for
            # the older 'key-' format (matches Mailgun's docs).
            pattern=re.compile(
                r"\b(?:key|private|pubkey)-[a-zA-Z0-9]{32}\b"
            ),
            severity=PIISeverity.HIGH,
            description="Mailgun API key (key-/private-/pubkey- prefix)",
        ),
        PIIPattern(
            name="discord_bot_token",
            # Discord bot tokens: 3 dot-separated segments. Start with
            # 'M' (newer) or 'N' (older) for bot tokens. Format:
            #   [MN][A-Za-z0-9_-]{23-30}.[A-Za-z0-9_-]{6-7}.[A-Za-z0-9_-]{27,}
            # The starting M/N constraint cuts FPs on random base64-shaped
            # 3-segment strings.
            pattern=re.compile(
                r"\b[MN][A-Za-z0-9_\-]{23,30}"
                r"\.[A-Za-z0-9_\-]{6,7}"
                r"\.[A-Za-z0-9_\-]{27,}\b"
            ),
            severity=PIISeverity.HIGH,
            description="Discord bot token (3-segment dot format, M/N prefix)",
        ),
        # ----- v1.7.15 additions: JWT + GitLab + Atlassian ----------------
        PIIPattern(
            name="jwt",
            # JSON Web Token: 3 base64url-encoded segments separated by dots.
            # The first two segments ALWAYS start with 'eyJ' (which is the
            # base64 encoding of '{"'). This dual-eyJ constraint is what
            # distinguishes JWTs from arbitrary 3-segment base64 strings
            # (like Discord bot tokens, which don't have this property).
            # Header: eyJ + 10+ chars; Payload: eyJ + 10+ chars; Signature: 20+ chars.
            pattern=re.compile(
                r"\beyJ[A-Za-z0-9_\-]{10,}"
                r"\.eyJ[A-Za-z0-9_\-]{10,}"
                r"\.[A-Za-z0-9_\-]{20,}\b"
            ),
            severity=PIISeverity.HIGH,
            description="JSON Web Token (header.payload.signature, dual eyJ prefix)",
        ),
        PIIPattern(
            name="gitlab_pat",
            # GitLab Personal Access Token: distinctive 'glpat-' prefix +
            # 20+ chars of [A-Za-z0-9_-]. GitLab docs document this format
            # as the standard PAT shape; CI tokens use glcbt-, deploy use
            # glcdt-, etc. We match the most common (PAT) variant.
            pattern=re.compile(r"\bglpat-[A-Za-z0-9_\-]{20,}\b"),
            severity=PIISeverity.HIGH,
            description="GitLab Personal Access Token (glpat- prefix)",
        ),
        PIIPattern(
            name="atlassian_api_token",
            # Atlassian API tokens (Jira, Confluence, Bitbucket Cloud):
            # ATATT3xFfGF0 is the documented prefix for cloud API tokens,
            # followed by base64url-style chars including '=' padding.
            # Length varies; we require at least 20 body chars to avoid
            # false matches on truncated test data.
            pattern=re.compile(
                r"\bATATT3xFfGF0[A-Za-z0-9_\-=]{20,}\b"
            ),
            severity=PIISeverity.HIGH,
            description="Atlassian API token (Jira/Confluence/Bitbucket Cloud)",
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

        v1.7.10: per-pattern validator hook is consulted; matches
        that fail validation (e.g. credit cards failing Luhn,
        IPv4 strings with octets > 255) are silently dropped.
        """
        matches: list[PIIMatch] = []
        for pat in self.patterns:
            for m in pat.pattern.finditer(text):
                matched = m.group(0)
                # v1.7.10: drop matches that fail the optional validator
                if not pat.is_valid(matched):
                    continue
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
