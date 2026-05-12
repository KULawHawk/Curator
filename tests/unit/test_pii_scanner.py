"""Tests for v1.7.55: pii_scanner.py coverage lift (Tier 3, post-D3).

Backstory: v1.7.51's coverage baseline showed `pii_scanner.py` at
22.34% -- the weakest covered module in the codebase. Zero tests
referenced it directly; the 22% came from incidental imports.

This module tests the full pii_scanner surface:

  * **Validators** (TestLuhn, TestIPv4Valid) -- boundary cases for the
    two pluggable validators
  * **PIIPattern** (TestPIIPattern) -- is_valid + redact helpers
  * **PIIMatch / PIIScanReport** (TestScanReport) -- has_high_severity,
    match_count, by_pattern
  * **PIIScanner** (TestScanText, TestScanFile, TestScanDirectory) --
    end-to-end scanning paths including error / truncation / encoding
  * **Default patterns** (TestDefaultPatterns) -- positive matches +
    known false positives for each of the 17 patterns
  * **Enrichment parsers** (TestParseAWSKey, TestParseStripeKey,
    TestParseSlackToken, TestParseGitHubPAT, TestParseOpenAIKey,
    TestParseMailgunKey, TestParseJWT) -- metadata extraction

Strategy: test against PUBLIC API where possible, but the validators
and parsers are private helpers (underscore-prefixed) AND a documented
extension point ("Adding more is a one-line append to DEFAULT_PATTERNS").
Tests import them by name to lock the implementation contract.
"""
from __future__ import annotations

import base64
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from curator.services.pii_scanner import (
    DEFAULT_PATTERNS,
    PIIMatch,
    PIIPattern,
    PIIScanner,
    PIIScanReport,
    PIISeverity,
    _ipv4_valid,
    _luhn_valid,
    _parse_aws_key,
    _parse_github_pat,
    _parse_jwt,
    _parse_mailgun_key,
    _parse_openai_key,
    _parse_slack_token,
    _parse_stripe_key,
)


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------


class TestLuhnValid:
    """v1.7.55: _luhn_valid implements the standard credit-card checksum."""

    def test_valid_visa_test_number(self):
        # 4242 4242 4242 4242 is Stripe's documented test card
        assert _luhn_valid("4242424242424242") is True

    def test_valid_with_dashes(self):
        # Dashes/spaces should be ignored (digit-only check)
        assert _luhn_valid("4242-4242-4242-4242") is True

    def test_valid_with_spaces(self):
        assert _luhn_valid("4242 4242 4242 4242") is True

    def test_invalid_checksum(self):
        # Same length, wrong digit at the end
        assert _luhn_valid("4242424242424241") is False

    def test_rejects_too_short(self):
        # 12 digits = below the 13-digit minimum
        assert _luhn_valid("123456789012") is False

    def test_rejects_too_long(self):
        # 20 digits = above the 19-digit maximum
        assert _luhn_valid("12345678901234567890") is False

    def test_empty_string(self):
        assert _luhn_valid("") is False

    def test_no_digits(self):
        assert _luhn_valid("abc-def-ghij") is False

    def test_mastercard_test_number(self):
        # 5555 5555 5555 4444 is Stripe's MasterCard test card
        assert _luhn_valid("5555555555554444") is True

    def test_amex_test_number(self):
        # 378282246310005 is Stripe's Amex test card (15 digits)
        assert _luhn_valid("378282246310005") is True


class TestIPv4Valid:
    """v1.7.55: _ipv4_valid rejects octets >255 and leading-zero notation."""

    def test_valid_private_ip(self):
        assert _ipv4_valid("192.168.1.1") is True

    def test_valid_public_ip(self):
        assert _ipv4_valid("8.8.8.8") is True

    def test_valid_zero_address(self):
        assert _ipv4_valid("0.0.0.0") is True

    def test_valid_broadcast_address(self):
        assert _ipv4_valid("255.255.255.255") is True

    def test_invalid_octet_over_255(self):
        assert _ipv4_valid("256.0.0.1") is False

    def test_invalid_all_octets_over_255(self):
        assert _ipv4_valid("999.888.777.666") is False

    def test_invalid_leading_zero(self):
        # 001 is rejected per v1.7.10 design (typo guard)
        assert _ipv4_valid("192.168.001.001") is False

    def test_invalid_too_few_octets(self):
        assert _ipv4_valid("192.168.1") is False

    def test_invalid_too_many_octets(self):
        assert _ipv4_valid("192.168.1.1.1") is False

    def test_invalid_non_numeric(self):
        assert _ipv4_valid("192.168.abc.1") is False

    def test_zero_alone_is_ok(self):
        # "0" without leading zero is allowed
        assert _ipv4_valid("0.0.0.0") is True


# ---------------------------------------------------------------------------
# PIIPattern helpers
# ---------------------------------------------------------------------------


class TestPIIPattern:
    """v1.7.55: PIIPattern.is_valid and .redact helpers."""

    def test_is_valid_returns_true_when_no_validator(self):
        p = PIIPattern(
            name="test",
            pattern=re.compile(r"\d+"),
            severity=PIISeverity.MEDIUM,
            description="test",
            validator=None,
        )
        assert p.is_valid("anything") is True

    def test_is_valid_calls_validator(self):
        called_with = []
        def _v(val):
            called_with.append(val)
            return False
        p = PIIPattern(
            name="test", pattern=re.compile(r".*"),
            severity=PIISeverity.MEDIUM, description="test",
            validator=_v,
        )
        assert p.is_valid("hello") is False
        assert called_with == ["hello"]

    def test_is_valid_catches_validator_exception(self):
        """v1.7.10: never let a validator crash a scan."""
        def _v(val):
            raise RuntimeError("validator broke")
        p = PIIPattern(
            name="test", pattern=re.compile(r".*"),
            severity=PIISeverity.MEDIUM, description="test",
            validator=_v,
        )
        # Exception is swallowed; treat as invalid
        assert p.is_valid("hello") is False

    def test_redact_short_value(self):
        p = PIIPattern(
            name="test", pattern=re.compile(r".*"),
            severity=PIISeverity.MEDIUM, description="test",
        )
        # Value <= 4 chars: all stars
        assert p.redact("ab") == "**"
        assert p.redact("abcd") == "****"

    def test_redact_long_value_keeps_last_4(self):
        p = PIIPattern(
            name="test", pattern=re.compile(r".*"),
            severity=PIISeverity.MEDIUM, description="test",
        )
        assert p.redact("123-45-6789") == "*******6789"
        # Length preserved
        assert len(p.redact("123-45-6789")) == len("123-45-6789")


# ---------------------------------------------------------------------------
# PIIScanReport properties
# ---------------------------------------------------------------------------


class TestScanReport:
    """v1.7.55: report property helpers."""

    def _mk_match(self, severity=PIISeverity.MEDIUM, name="email"):
        return PIIMatch(
            pattern_name=name, severity=severity,
            matched_text="x", redacted="*",
            offset=0, line=1, metadata=None,
        )

    def test_empty_report_no_high_severity(self):
        r = PIIScanReport(source="x", bytes_scanned=0, truncated=False)
        assert r.has_high_severity is False
        assert r.match_count == 0
        assert r.by_pattern() == {}

    def test_has_high_severity_true(self):
        r = PIIScanReport(source="x", bytes_scanned=0, truncated=False)
        r.matches.append(self._mk_match(severity=PIISeverity.HIGH))
        assert r.has_high_severity is True

    def test_has_high_severity_false_with_medium_only(self):
        r = PIIScanReport(source="x", bytes_scanned=0, truncated=False)
        r.matches.append(self._mk_match(severity=PIISeverity.MEDIUM))
        r.matches.append(self._mk_match(severity=PIISeverity.LOW))
        assert r.has_high_severity is False

    def test_match_count(self):
        r = PIIScanReport(source="x", bytes_scanned=0, truncated=False)
        for _ in range(5):
            r.matches.append(self._mk_match())
        assert r.match_count == 5

    def test_by_pattern_counts_grouped(self):
        r = PIIScanReport(source="x", bytes_scanned=0, truncated=False)
        r.matches.append(self._mk_match(name="email"))
        r.matches.append(self._mk_match(name="email"))
        r.matches.append(self._mk_match(name="ssn", severity=PIISeverity.HIGH))
        out = r.by_pattern()
        assert out == {"email": 2, "ssn": 1}


# ---------------------------------------------------------------------------
# Default patterns (positive + false positives)
# ---------------------------------------------------------------------------


@pytest.fixture
def scanner():
    """Default-config PIIScanner."""
    return PIIScanner()


class TestSSNPattern:
    def test_matches_ssn_format(self, scanner):
        r = scanner.scan_text("Patient SSN: 123-45-6789")
        ssn_matches = [m for m in r.matches if m.pattern_name == "ssn"]
        assert len(ssn_matches) == 1
        assert ssn_matches[0].matched_text == "123-45-6789"
        assert ssn_matches[0].severity == PIISeverity.HIGH

    def test_does_not_match_continuous_digits(self, scanner):
        # 9 digits in a row don't match SSN (lacks dashes)
        r = scanner.scan_text("Order: 123456789")
        ssn_matches = [m for m in r.matches if m.pattern_name == "ssn"]
        assert len(ssn_matches) == 0


class TestCreditCardPattern:
    def test_matches_valid_visa(self, scanner):
        r = scanner.scan_text("Card: 4242 4242 4242 4242")
        cc_matches = [m for m in r.matches if m.pattern_name == "credit_card"]
        assert len(cc_matches) == 1
        assert cc_matches[0].severity == PIISeverity.HIGH

    def test_rejects_luhn_invalid_number(self, scanner):
        # 16 digits but wrong checksum -- filter out
        r = scanner.scan_text("Card: 1234 5678 9012 3456")
        cc_matches = [m for m in r.matches if m.pattern_name == "credit_card"]
        assert len(cc_matches) == 0


class TestPhonePattern:
    def test_matches_us_phone_paren_format(self, scanner):
        r = scanner.scan_text("Call (555) 123-4567")
        ph_matches = [m for m in r.matches if m.pattern_name == "phone_us"]
        assert len(ph_matches) == 1

    def test_matches_us_phone_dash_format(self, scanner):
        r = scanner.scan_text("Call 555-123-4567")
        ph_matches = [m for m in r.matches if m.pattern_name == "phone_us"]
        assert len(ph_matches) == 1

    def test_severity_is_medium(self, scanner):
        r = scanner.scan_text("Call 555-123-4567")
        ph = [m for m in r.matches if m.pattern_name == "phone_us"][0]
        assert ph.severity == PIISeverity.MEDIUM


class TestEmailPattern:
    def test_matches_email(self, scanner):
        r = scanner.scan_text("Reach me: alice@example.com")
        em = [m for m in r.matches if m.pattern_name == "email"]
        assert len(em) == 1
        assert em[0].matched_text == "alice@example.com"
        assert em[0].severity == PIISeverity.MEDIUM

    def test_no_match_without_domain_dot(self, scanner):
        r = scanner.scan_text("alice@localhost")
        em = [m for m in r.matches if m.pattern_name == "email"]
        assert len(em) == 0


class TestIPv4Pattern:
    def test_matches_valid_ip(self, scanner):
        r = scanner.scan_text("Server at 192.168.1.1")
        ip = [m for m in r.matches if m.pattern_name == "ipv4"]
        assert len(ip) == 1
        assert ip[0].severity == PIISeverity.LOW

    def test_rejects_out_of_range_ip(self, scanner):
        # 999.888.777.666 matches the regex but fails validator
        r = scanner.scan_text("Server at 999.888.777.666")
        ip = [m for m in r.matches if m.pattern_name == "ipv4"]
        assert len(ip) == 0


class TestApiKeyPatterns:
    """v1.7.55: API-key family of patterns (HIGH severity each)."""

    def test_github_pat_ghp(self, scanner):
        r = scanner.scan_text("token=ghp_" + "A" * 36)
        m = [x for x in r.matches if x.pattern_name == "github_pat"]
        assert len(m) == 1
        assert m[0].severity == PIISeverity.HIGH

    def test_github_pat_all_prefixes(self, scanner):
        """gho_/ghu_/ghs_/ghr_ should all match."""
        for prefix in ("gho_", "ghu_", "ghs_", "ghr_"):
            r = scanner.scan_text(f"key={prefix}" + "X" * 36)
            m = [x for x in r.matches if x.pattern_name == "github_pat"]
            assert len(m) == 1, f"prefix {prefix} should match"

    def test_aws_access_key_id_akia(self, scanner):
        r = scanner.scan_text("AKIA" + "ABCDEFGHIJKLMNOP")
        m = [x for x in r.matches if x.pattern_name == "aws_access_key_id"]
        assert len(m) == 1

    def test_aws_access_key_id_asia(self, scanner):
        # STS temporary key
        r = scanner.scan_text("ASIA" + "ABCDEFGHIJKLMNOP")
        m = [x for x in r.matches if x.pattern_name == "aws_access_key_id"]
        assert len(m) == 1

    def test_slack_token_bot(self, scanner):
        r = scanner.scan_text("slack=xoxb-1234567890-1234567890-" + "A" * 24)
        m = [x for x in r.matches if x.pattern_name == "slack_token"]
        assert len(m) == 1

    def test_google_api_key(self, scanner):
        # AIza + exactly 35 chars (pattern is strict on length)
        r = scanner.scan_text("AIza" + "abcdefghijklmnopqrstuvwxyz012345678")
        m = [x for x in r.matches if x.pattern_name == "google_api_key"]
        assert len(m) == 1

    def test_stripe_live_secret_key(self, scanner):
        r = scanner.scan_text("sk_live_" + "X" * 24)
        m = [x for x in r.matches if x.pattern_name == "stripe_secret_key"]
        assert len(m) == 1

    def test_stripe_test_secret_key(self, scanner):
        r = scanner.scan_text("sk_test_" + "X" * 24)
        m = [x for x in r.matches if x.pattern_name == "stripe_secret_key"]
        assert len(m) == 1

    def test_openai_standard_key(self, scanner):
        r = scanner.scan_text("sk-" + "X" * 40)
        m = [x for x in r.matches if x.pattern_name == "openai_api_key"]
        assert len(m) == 1

    def test_openai_project_key(self, scanner):
        r = scanner.scan_text("sk-proj-" + "X" * 40)
        m = [x for x in r.matches if x.pattern_name == "openai_api_key"]
        assert len(m) == 1

    def test_twilio_account_sid(self, scanner):
        r = scanner.scan_text("AC" + "abcdef0123456789abcdef0123456789")
        m = [x for x in r.matches if x.pattern_name == "twilio_account_sid"]
        assert len(m) == 1

    def test_mailgun_key_legacy(self, scanner):
        r = scanner.scan_text("key-" + "X" * 32)
        m = [x for x in r.matches if x.pattern_name == "mailgun_api_key"]
        assert len(m) == 1

    def test_mailgun_key_private(self, scanner):
        r = scanner.scan_text("private-" + "X" * 32)
        m = [x for x in r.matches if x.pattern_name == "mailgun_api_key"]
        assert len(m) == 1

    def test_discord_bot_token(self, scanner):
        # Format: [MN] + 23-30 chars . 6-7 chars . 27+ chars
        token = "M" + "x" * 24 + "." + "yyyyyy" + "." + "z" * 30
        r = scanner.scan_text(f"discord={token}")
        m = [x for x in r.matches if x.pattern_name == "discord_bot_token"]
        assert len(m) == 1

    def test_gitlab_pat(self, scanner):
        r = scanner.scan_text("glpat-" + "X" * 25)
        m = [x for x in r.matches if x.pattern_name == "gitlab_pat"]
        assert len(m) == 1

    def test_atlassian_token(self, scanner):
        r = scanner.scan_text("ATATT3xFfGF0" + "X" * 30)
        m = [x for x in r.matches if x.pattern_name == "atlassian_api_token"]
        assert len(m) == 1


class TestJWTPattern:
    def _mk_jwt(self, header=None, payload=None):
        """Build a syntactically valid JWT (no signature verification)."""
        h = header or {"alg": "HS256", "typ": "JWT"}
        p = payload or {"sub": "1234", "iss": "test"}
        def b64u(obj):
            raw = json.dumps(obj).encode()
            return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
        return f"{b64u(h)}.{b64u(p)}.signaturepart_must_be_20chars_or_more"

    def test_matches_well_formed_jwt(self, scanner):
        tok = self._mk_jwt()
        r = scanner.scan_text(f"Authorization: Bearer {tok}")
        m = [x for x in r.matches if x.pattern_name == "jwt"]
        assert len(m) == 1
        assert m[0].severity == PIISeverity.HIGH


# ---------------------------------------------------------------------------
# Enrichment parsers
# ---------------------------------------------------------------------------


class TestParseAWSKey:
    def test_akia_classified_as_long_term(self):
        result = _parse_aws_key("AKIA" + "X" * 16)
        assert result is not None
        assert result["key_type"] == "long_term"

    def test_asia_classified_as_temporary(self):
        result = _parse_aws_key("ASIA" + "X" * 16)
        assert result is not None
        assert result["key_type"] == "temporary"

    def test_unrecognized_prefix_returns_none(self):
        assert _parse_aws_key("FAKE" + "X" * 16) is None


class TestParseStripeKey:
    def test_live_classified_as_live(self):
        result = _parse_stripe_key("sk_live_" + "X" * 24)
        assert result is not None
        assert result["mode"] == "live"

    def test_test_classified_as_test(self):
        result = _parse_stripe_key("sk_test_" + "X" * 24)
        assert result is not None
        assert result["mode"] == "test"

    def test_unrecognized_prefix_returns_none(self):
        assert _parse_stripe_key("sk_other_X") is None


class TestParseSlackToken:
    def test_xoxa_app(self):
        result = _parse_slack_token("xoxa-something")
        assert result is not None
        assert result["token_type"] == "app"

    def test_xoxb_bot(self):
        assert _parse_slack_token("xoxb-something")["token_type"] == "bot"

    def test_xoxp_user(self):
        assert _parse_slack_token("xoxp-something")["token_type"] == "user"

    def test_xoxr_refresh(self):
        assert _parse_slack_token("xoxr-something")["token_type"] == "refresh"

    def test_xoxs_workspace(self):
        assert _parse_slack_token("xoxs-something")["token_type"] == "workspace"

    def test_unknown_returns_none(self):
        assert _parse_slack_token("xoxz-something") is None

    def test_too_short_returns_none(self):
        assert _parse_slack_token("xox") is None


class TestParseGitHubPAT:
    def test_ghp_classic(self):
        result = _parse_github_pat("ghp_" + "X" * 36)
        assert result is not None
        assert result["token_type"] == "personal"

    def test_gho_oauth(self):
        assert _parse_github_pat("gho_X")["token_type"] == "oauth"

    def test_ghu_user_to_server(self):
        assert _parse_github_pat("ghu_X")["token_type"] == "user_to_server"

    def test_ghs_server_to_server(self):
        assert _parse_github_pat("ghs_X")["token_type"] == "server_to_server"

    def test_ghr_refresh(self):
        assert _parse_github_pat("ghr_X")["token_type"] == "refresh"

    def test_unknown_returns_none(self):
        assert _parse_github_pat("xxx_X") is None


class TestParseOpenAIKey:
    def test_project_key(self):
        result = _parse_openai_key("sk-proj-" + "X" * 40)
        assert result is not None
        assert result["key_type"] == "project"

    def test_standard_key(self):
        result = _parse_openai_key("sk-" + "X" * 40)
        assert result is not None
        assert result["key_type"] == "standard"

    def test_unrecognized_returns_none(self):
        assert _parse_openai_key("totally-different-X") is None


class TestParseMailgunKey:
    def test_legacy_key(self):
        result = _parse_mailgun_key("key-" + "X" * 32)
        assert result is not None
        assert result["key_type"] == "legacy_api"

    def test_private_key(self):
        result = _parse_mailgun_key("private-" + "X" * 32)
        assert result is not None
        assert result["key_type"] == "private"

    def test_public_key(self):
        result = _parse_mailgun_key("pubkey-" + "X" * 32)
        assert result is not None
        assert result["key_type"] == "public"

    def test_unknown_returns_none(self):
        assert _parse_mailgun_key("unknown-X") is None


class TestParseJWT:
    @staticmethod
    def _build_jwt(header=None, payload=None):
        """Helper to construct a base64url-encoded JWT."""
        def b64u(obj):
            raw = json.dumps(obj).encode()
            return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
        h = header or {"alg": "HS256", "typ": "JWT", "kid": "key-1"}
        p = payload or {"sub": "1234", "iss": "test-iss"}
        return f"{b64u(h)}.{b64u(p)}.fake_signature_just_for_test"

    def test_parses_valid_jwt(self):
        tok = self._build_jwt()
        result = _parse_jwt(tok)
        assert result is not None
        assert result["alg"] == "HS256"
        assert result["typ"] == "JWT"
        assert result["kid"] == "key-1"
        assert result["sub"] == "1234"
        assert result["iss"] == "test-iss"

    def test_parses_exp_and_derives_iso(self):
        # exp claim in the future -> not expired
        future_exp = int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp())
        tok = self._build_jwt(payload={"sub": "x", "exp": future_exp})
        result = _parse_jwt(tok)
        assert result is not None
        assert result["exp"] == future_exp
        assert "exp_iso" in result
        assert result["expired"] is False

    def test_expired_jwt_flagged(self):
        # exp claim in the past -> expired=True
        past_exp = int((datetime.now(timezone.utc) - timedelta(hours=1)).timestamp())
        tok = self._build_jwt(payload={"sub": "x", "exp": past_exp})
        result = _parse_jwt(tok)
        assert result is not None
        assert result["expired"] is True

    def test_malformed_jwt_returns_none(self):
        # Only 2 segments, not 3
        assert _parse_jwt("eyJabc.eyJdef") is None

    def test_invalid_base64_returns_none(self):
        # Three segments but not base64
        assert _parse_jwt("@@@.@@@.@@@") is None

    def test_invalid_json_returns_none(self):
        # Valid base64 of non-JSON
        b64_text = base64.urlsafe_b64encode(b"not-json").rstrip(b"=").decode()
        token = f"{b64_text}.{b64_text}.sig"
        assert _parse_jwt(token) is None

    def test_jwt_with_only_alg_returns_partial(self):
        """Token with no recognized claims but valid structure: returns dict with what's there."""
        result = _parse_jwt(self._build_jwt(
            header={"alg": "RS256"},
            payload={"unrelated": "claim"},
        ))
        # alg present; iss/sub/aud absent; numeric claims absent
        assert result is not None
        assert result["alg"] == "RS256"
        assert "iss" not in result

    def test_bogus_exp_does_not_crash(self):
        """v1.7.26: bogus epoch (negative, > year 9999) shouldn't crash."""
        tok = self._build_jwt(payload={"sub": "x", "exp": 99999999999999})
        # Should not raise; should return a dict (maybe without exp_iso)
        result = _parse_jwt(tok)
        assert result is not None
        # 99999999999999 is way past year 9999 -- exp_iso skipped


# ---------------------------------------------------------------------------
# PIIScanner.scan_text behavior
# ---------------------------------------------------------------------------


class TestScanText:
    def test_clean_text_no_matches(self, scanner):
        r = scanner.scan_text("Just some plain prose with no PII at all.")
        assert r.match_count == 0
        assert r.error is None

    def test_multiple_patterns_in_one_text(self, scanner):
        text = "Email: alice@example.com, SSN: 123-45-6789, phone (555) 123-4567"
        r = scanner.scan_text(text)
        names = {m.pattern_name for m in r.matches}
        assert "email" in names
        assert "ssn" in names
        assert "phone_us" in names

    def test_matches_sorted_by_offset(self, scanner):
        text = "SSN: 123-45-6789. Email: bob@test.com."
        r = scanner.scan_text(text)
        offsets = [m.offset for m in r.matches]
        assert offsets == sorted(offsets)

    def test_line_number_is_1_based(self, scanner):
        text = "line1\nline2 alice@example.com\nline3"
        r = scanner.scan_text(text)
        em = [m for m in r.matches if m.pattern_name == "email"][0]
        assert em.line == 2

    def test_source_label_in_report(self, scanner):
        r = scanner.scan_text("plain text", source="my-file.txt")
        assert r.source == "my-file.txt"

    def test_bytes_scanned_is_utf8_length(self, scanner):
        text = "café"  # 4 chars, 5 bytes in UTF-8
        r = scanner.scan_text(text)
        assert r.bytes_scanned == 5

    def test_truncated_flag_false_for_scan_text(self, scanner):
        r = scanner.scan_text("anything")
        assert r.truncated is False

    def test_redacted_field_populated(self, scanner):
        r = scanner.scan_text("Phone: 555-123-4567")
        ph = [m for m in r.matches if m.pattern_name == "phone_us"][0]
        # Redacted shows only last 4 digits visible
        assert ph.redacted != ph.matched_text
        assert ph.redacted.endswith(ph.matched_text[-4:])

    def test_metadata_populated_for_enriched_pattern(self, scanner):
        """AWS key matches should get metadata via _PATTERN_PARSERS dispatch."""
        r = scanner.scan_text("AWS_KEY=AKIA" + "X" * 16)
        aws = [m for m in r.matches if m.pattern_name == "aws_access_key_id"][0]
        assert aws.metadata is not None
        assert aws.metadata["key_type"] == "long_term"

    def test_metadata_none_for_basic_pattern(self, scanner):
        """SSN doesn't have a parser; metadata should be None."""
        r = scanner.scan_text("SSN: 123-45-6789")
        ssn = [m for m in r.matches if m.pattern_name == "ssn"][0]
        assert ssn.metadata is None


# ---------------------------------------------------------------------------
# PIIScanner.scan_file behavior
# ---------------------------------------------------------------------------


class TestScanFile:
    def test_scans_a_real_file(self, tmp_path, scanner):
        p = tmp_path / "data.txt"
        p.write_text("SSN: 123-45-6789", encoding="utf-8")
        r = scanner.scan_file(p)
        assert r.error is None
        assert r.match_count == 1

    def test_missing_file_returns_error(self, tmp_path, scanner):
        r = scanner.scan_file(tmp_path / "does-not-exist.txt")
        assert r.error is not None
        assert "Not a file" in r.error or "doesn't exist" in r.error
        assert r.matches == []

    def test_directory_is_not_a_file(self, tmp_path, scanner):
        r = scanner.scan_file(tmp_path)  # tmp_path is a directory
        assert r.error is not None
        assert r.matches == []

    def test_truncation_for_oversize_file(self, tmp_path):
        # Tiny head_bytes to force truncation
        s = PIIScanner(head_bytes=100)
        p = tmp_path / "big.txt"
        # Write 1000 bytes; only first 100 read
        p.write_text("a" * 1000, encoding="utf-8")
        r = s.scan_file(p)
        assert r.truncated is True
        assert r.bytes_scanned == 100

    def test_no_truncation_for_small_file(self, tmp_path, scanner):
        p = tmp_path / "small.txt"
        p.write_text("hello", encoding="utf-8")
        r = scanner.scan_file(p)
        assert r.truncated is False

    def test_handles_non_utf8_bytes(self, tmp_path, scanner):
        """v1.7.6: best-effort decode with errors='replace'."""
        p = tmp_path / "binary.bin"
        # Mix of bad bytes + valid PII
        p.write_bytes(b"\xff\xfe" + b"SSN: 123-45-6789" + b"\xff")
        r = scanner.scan_file(p)
        assert r.error is None  # didn't crash on bad bytes
        # The valid PII embedded in the file should still be found
        ssn = [m for m in r.matches if m.pattern_name == "ssn"]
        assert len(ssn) == 1


# ---------------------------------------------------------------------------
# PIIScanner.scan_directory behavior
# ---------------------------------------------------------------------------


class TestScanDirectory:
    def test_scans_multiple_files(self, tmp_path, scanner):
        (tmp_path / "a.txt").write_text("SSN: 111-22-3333", encoding="utf-8")
        (tmp_path / "b.txt").write_text("Email: a@b.com", encoding="utf-8")
        results = scanner.scan_directory(tmp_path, recursive=False)
        assert len(results) == 2

    def test_recursive_finds_nested_files(self, tmp_path, scanner):
        nested = tmp_path / "sub"
        nested.mkdir()
        (nested / "nested.txt").write_text("SSN: 111-22-3333", encoding="utf-8")
        results = scanner.scan_directory(tmp_path, recursive=True)
        assert len(results) >= 1

    def test_non_recursive_skips_subdirectories(self, tmp_path, scanner):
        nested = tmp_path / "sub"
        nested.mkdir()
        (nested / "nested.txt").write_text("SSN: 111-22-3333", encoding="utf-8")
        (tmp_path / "top.txt").write_text("ok", encoding="utf-8")
        results = scanner.scan_directory(tmp_path, recursive=False)
        # Should find only top.txt, not nested.txt
        sources = [Path(r.source).name for r in results]
        assert "top.txt" in sources
        assert "nested.txt" not in sources

    def test_extension_filter(self, tmp_path, scanner):
        (tmp_path / "a.txt").write_text("ok", encoding="utf-8")
        (tmp_path / "b.csv").write_text("ok", encoding="utf-8")
        (tmp_path / "c.bin").write_text("ok", encoding="utf-8")
        results = scanner.scan_directory(
            tmp_path, recursive=False, extensions=[".txt", ".csv"],
        )
        sources = [Path(r.source).suffix for r in results]
        assert ".txt" in sources
        assert ".csv" in sources
        assert ".bin" not in sources

    def test_invalid_directory_returns_error_report(self, tmp_path, scanner):
        # Pass a path to a file, not a directory
        f = tmp_path / "not-a-dir.txt"
        f.write_text("ok", encoding="utf-8")
        results = scanner.scan_directory(f)
        assert len(results) == 1
        assert results[0].error is not None


# ---------------------------------------------------------------------------
# Custom pattern set
# ---------------------------------------------------------------------------


class TestCustomPatternSet:
    def test_custom_pattern_only(self):
        """Pass a single custom pattern; default patterns are not used."""
        custom = PIIPattern(
            name="my_id",
            pattern=re.compile(r"\bID-\d{4}\b"),
            severity=PIISeverity.MEDIUM,
            description="Custom ID format",
        )
        s = PIIScanner(patterns=[custom])
        r = s.scan_text("Customer ID-1234 and SSN: 123-45-6789")
        # Only custom pattern matches; default SSN pattern is absent
        names = {m.pattern_name for m in r.matches}
        assert names == {"my_id"}

    def test_default_patterns_returns_a_list(self):
        """DEFAULT_PATTERNS is exposed for introspection."""
        assert isinstance(DEFAULT_PATTERNS, list)
        assert len(DEFAULT_PATTERNS) > 0
        # All entries are PIIPattern
        assert all(isinstance(p, PIIPattern) for p in DEFAULT_PATTERNS)

    def test_default_patterns_have_unique_names(self):
        names = [p.name for p in DEFAULT_PATTERNS]
        assert len(names) == len(set(names)), (
            f"Duplicate pattern names: {names}"
        )
