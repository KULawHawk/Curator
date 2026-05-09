"""Tests for ``curator.mcp.auth`` (v1.5.0 P1).

Covers DESIGN \u00a75.1 acceptance criteria:

1. Key generation produces the right prefix + length.
2. Key generation has enough entropy.
3. Key hash computation is consistent.
4. File I/O round-trip preserves all fields.
5. ``0600`` permissions on Unix.
6. Validation accepts matching key, rejects non-matching.
7. ``last_used_at`` update is atomic.

Plus broader coverage of the public API:

* CURATOR_HOME env-var override.
* Corrupt file handling.
* Schema-version mismatch handling.
* DuplicateNameError on add.
* Idempotent remove.
* Empty / prefix-less / whitespace presented keys rejected.
* Validation against empty keystore.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from curator.mcp.auth import (
    DuplicateNameError,
    KEY_PREFIX,
    KEYS_FILE_NAME,
    KeyFileError,
    SCHEMA_VERSION,
    StoredKey,
    add_key,
    default_keys_file,
    default_mcp_dir,
    generate_key,
    hash_key,
    load_keys,
    remove_key,
    save_keys,
    update_last_used,
    validate_key,
)


@pytest.fixture
def keys_file(tmp_path):
    """A keys file path under a fresh temp dir per test."""
    return tmp_path / KEYS_FILE_NAME


# ---------------------------------------------------------------------------
# 1. Key generation: prefix + length
# ---------------------------------------------------------------------------


class TestGenerateKey:
    def test_has_curm_prefix(self):
        for _ in range(20):
            assert generate_key().startswith(KEY_PREFIX)

    def test_length_in_expected_range(self):
        # secrets.token_urlsafe(30) yields 40 chars (no padding) most of
        # the time, occasionally 41 due to base64 stripping. Plus 5-char
        # prefix gives 45-46 typical. Allow a tiny window.
        for _ in range(20):
            key = generate_key()
            assert 44 <= len(key) <= 46, f"unexpected length: {len(key)}"

    def test_random_part_uses_url_safe_alphabet(self):
        valid = set(
            "abcdefghijklmnopqrstuvwxyz"
            "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
            "0123456789-_"
        )
        for _ in range(20):
            random_part = generate_key()[len(KEY_PREFIX):]
            for c in random_part:
                assert c in valid, f"bad char {c!r} in {random_part!r}"


# ---------------------------------------------------------------------------
# 2. Entropy
# ---------------------------------------------------------------------------


class TestKeyEntropy:
    def test_each_call_produces_unique_keys(self):
        # 100 calls; collisions would mean the RNG is broken (probability
        # of collision in 100 draws from 2^240 is vanishingly small).
        seen = set()
        for _ in range(100):
            seen.add(generate_key())
        assert len(seen) == 100


# ---------------------------------------------------------------------------
# 3. Hash computation
# ---------------------------------------------------------------------------


class TestHashKey:
    def test_same_key_same_hash(self):
        key = generate_key()
        assert hash_key(key) == hash_key(key)

    def test_different_keys_different_hashes(self):
        a = generate_key()
        b = generate_key()
        # Hash collisions for SHA-256 on different inputs would be a
        # cryptographic failure; this assertion is robust.
        assert hash_key(a) != hash_key(b)

    def test_hash_is_64_char_lowercase_hex(self):
        for _ in range(10):
            h = hash_key(generate_key())
            assert len(h) == 64
            assert all(c in "0123456789abcdef" for c in h), \
                f"non-hex char in {h!r}"


# ---------------------------------------------------------------------------
# 4. File I/O round-trip
# ---------------------------------------------------------------------------


class TestSaveLoadRoundTrip:
    def test_round_trip_empty_list(self, keys_file):
        save_keys([], keys_file)
        assert load_keys(keys_file) == []

    def test_round_trip_one_key_full_fields(self, keys_file):
        original = [StoredKey(
            name="test-key",
            key_hash=hash_key(generate_key()),
            created_at="2026-05-08T12:00:00Z",
            last_used_at="2026-05-08T13:30:00Z",
            description="A test key",
        )]
        save_keys(original, keys_file)
        loaded = load_keys(keys_file)
        assert len(loaded) == 1
        assert loaded[0].name == "test-key"
        assert loaded[0].key_hash == original[0].key_hash
        assert loaded[0].created_at == "2026-05-08T12:00:00Z"
        assert loaded[0].last_used_at == "2026-05-08T13:30:00Z"
        assert loaded[0].description == "A test key"

    def test_round_trip_one_key_optional_fields_none(self, keys_file):
        original = [StoredKey(
            name="minimal",
            key_hash="0" * 64,
            created_at="2026-05-08T12:00:00Z",
            last_used_at=None,
            description=None,
        )]
        save_keys(original, keys_file)
        loaded = load_keys(keys_file)
        assert loaded[0].last_used_at is None
        assert loaded[0].description is None

    def test_round_trip_multiple_keys_preserves_order(self, keys_file):
        keys = [
            StoredKey(
                name=f"k{i}",
                key_hash=hash_key(generate_key()),
                created_at="2026-05-08T12:00:00Z",
                last_used_at=None,
                description=None,
            )
            for i in range(5)
        ]
        save_keys(keys, keys_file)
        loaded = load_keys(keys_file)
        assert [k.name for k in loaded] == ["k0", "k1", "k2", "k3", "k4"]

    def test_load_nonexistent_returns_empty(self, keys_file):
        # No file at all (first-time-startup case)
        assert load_keys(keys_file) == []

    def test_save_creates_parent_directory(self, tmp_path):
        nested = tmp_path / "a" / "b" / "c" / KEYS_FILE_NAME
        save_keys([], nested)
        assert nested.exists()

    def test_load_corrupt_json_raises(self, keys_file):
        keys_file.parent.mkdir(parents=True, exist_ok=True)
        keys_file.write_text("not valid json {{{")
        with pytest.raises(KeyFileError):
            load_keys(keys_file)

    def test_load_wrong_top_level_type_raises(self, keys_file):
        keys_file.parent.mkdir(parents=True, exist_ok=True)
        keys_file.write_text(json.dumps([1, 2, 3]))  # array, not object
        with pytest.raises(KeyFileError):
            load_keys(keys_file)

    def test_load_wrong_schema_version_raises(self, keys_file):
        keys_file.parent.mkdir(parents=True, exist_ok=True)
        keys_file.write_text(json.dumps({"version": 999, "keys": []}))
        with pytest.raises(KeyFileError, match="schema version"):
            load_keys(keys_file)

    def test_load_keys_field_not_list_raises(self, keys_file):
        keys_file.parent.mkdir(parents=True, exist_ok=True)
        keys_file.write_text(json.dumps({
            "version": SCHEMA_VERSION,
            "keys": "not a list",
        }))
        with pytest.raises(KeyFileError, match="not a list"):
            load_keys(keys_file)


# ---------------------------------------------------------------------------
# 5. Permissions (Unix only)
# ---------------------------------------------------------------------------


class TestPermissions:
    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="0600 mode test is Unix-only; Windows uses ACL inheritance",
    )
    def test_save_sets_0600_on_unix(self, keys_file):
        save_keys([], keys_file)
        mode = oct(keys_file.stat().st_mode)[-3:]
        assert mode == "600", f"expected 600, got {mode}"


# ---------------------------------------------------------------------------
# 6. Validation accepts matching, rejects non-matching
# ---------------------------------------------------------------------------


class TestValidateKey:
    def test_valid_key_returns_stored_entry(self, keys_file):
        plaintext = add_key("my-key", path=keys_file)
        result = validate_key(plaintext, path=keys_file)
        assert result is not None
        assert result.name == "my-key"

    def test_wrong_key_returns_none(self, keys_file):
        add_key("my-key", path=keys_file)
        wrong = generate_key()  # different random key
        assert validate_key(wrong, path=keys_file) is None

    def test_key_without_prefix_returns_none(self, keys_file):
        add_key("my-key", path=keys_file)
        assert validate_key("not-a-curator-key", path=keys_file) is None

    def test_empty_string_returns_none(self, keys_file):
        save_keys([], keys_file)
        assert validate_key("", path=keys_file) is None

    def test_whitespace_returns_none(self, keys_file):
        save_keys([], keys_file)
        assert validate_key("   ", path=keys_file) is None

    def test_validate_against_empty_keystore_returns_none(self, keys_file):
        save_keys([], keys_file)
        assert validate_key(generate_key(), path=keys_file) is None

    def test_validate_with_no_file_at_all_returns_none(self, keys_file):
        # File doesn't exist
        assert validate_key(generate_key(), path=keys_file) is None


# ---------------------------------------------------------------------------
# 7. last_used_at update is atomic
# ---------------------------------------------------------------------------


class TestUpdateLastUsed:
    def test_update_sets_iso_timestamp(self, keys_file):
        add_key("my-key", path=keys_file)
        before = load_keys(keys_file)[0]
        assert before.last_used_at is None

        update_last_used("my-key", path=keys_file)

        after = load_keys(keys_file)[0]
        assert after.last_used_at is not None
        assert after.last_used_at.endswith("Z")
        # Roughly: 'YYYY-MM-DDTHH:MM:SSZ' = 20 chars
        assert len(after.last_used_at) == 20

    def test_update_nonexistent_silently_noops(self, keys_file):
        add_key("my-key", path=keys_file)
        # Should not raise; should not modify the existing key
        update_last_used("does-not-exist", path=keys_file)
        loaded = load_keys(keys_file)
        assert loaded[0].name == "my-key"
        assert loaded[0].last_used_at is None

    def test_repeated_updates_keep_file_parseable(self, keys_file):
        # Tests that the atomic-write flow doesn't ever leave the file
        # in a half-written state. We can't easily force a crash mid-
        # write, but we can verify post-condition consistency across a
        # tight loop.
        add_key("my-key", path=keys_file)
        for _ in range(50):
            update_last_used("my-key", path=keys_file)
        loaded = load_keys(keys_file)
        assert len(loaded) == 1
        assert loaded[0].name == "my-key"

    def test_update_preserves_other_keys(self, keys_file):
        add_key("k1", path=keys_file)
        add_key("k2", path=keys_file)
        update_last_used("k1", path=keys_file)
        loaded = load_keys(keys_file)
        # k1 should have a timestamp, k2 should still be None
        k1 = next(k for k in loaded if k.name == "k1")
        k2 = next(k for k in loaded if k.name == "k2")
        assert k1.last_used_at is not None
        assert k2.last_used_at is None


# ---------------------------------------------------------------------------
# Add / remove
# ---------------------------------------------------------------------------


class TestAddKey:
    def test_add_returns_plaintext_key(self, keys_file):
        plaintext = add_key("my-key", path=keys_file)
        assert plaintext.startswith(KEY_PREFIX)

    def test_add_persists(self, keys_file):
        plaintext = add_key("my-key", path=keys_file)
        loaded = load_keys(keys_file)
        assert len(loaded) == 1
        assert loaded[0].name == "my-key"
        assert loaded[0].key_hash == hash_key(plaintext)

    def test_add_duplicate_name_raises(self, keys_file):
        add_key("my-key", path=keys_file)
        with pytest.raises(DuplicateNameError):
            add_key("my-key", path=keys_file)

    def test_add_duplicate_does_not_corrupt_file(self, keys_file):
        original_plaintext = add_key("my-key", path=keys_file)
        with pytest.raises(DuplicateNameError):
            add_key("my-key", path=keys_file)
        # File should still hold the original key intact
        loaded = load_keys(keys_file)
        assert len(loaded) == 1
        assert loaded[0].key_hash == hash_key(original_plaintext)

    def test_add_with_description(self, keys_file):
        add_key("my-key", description="laptop integration", path=keys_file)
        loaded = load_keys(keys_file)
        assert loaded[0].description == "laptop integration"

    def test_add_two_distinct_keys(self, keys_file):
        a = add_key("k1", path=keys_file)
        b = add_key("k2", path=keys_file)
        loaded = load_keys(keys_file)
        assert len(loaded) == 2
        assert a != b


class TestRemoveKey:
    def test_remove_existing_returns_true(self, keys_file):
        add_key("my-key", path=keys_file)
        assert remove_key("my-key", path=keys_file) is True

    def test_remove_nonexistent_returns_false(self, keys_file):
        save_keys([], keys_file)
        assert remove_key("does-not-exist", path=keys_file) is False

    def test_remove_actually_deletes(self, keys_file):
        add_key("my-key", path=keys_file)
        remove_key("my-key", path=keys_file)
        assert load_keys(keys_file) == []

    def test_remove_one_of_many_preserves_others(self, keys_file):
        add_key("k1", path=keys_file)
        add_key("k2", path=keys_file)
        add_key("k3", path=keys_file)
        remove_key("k2", path=keys_file)
        loaded = load_keys(keys_file)
        names = sorted(k.name for k in loaded)
        assert names == ["k1", "k3"]


# ---------------------------------------------------------------------------
# Default paths + CURATOR_HOME
# ---------------------------------------------------------------------------


class TestDefaultPaths:
    def test_curator_home_overrides_default(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CURATOR_HOME", str(tmp_path))
        assert default_mcp_dir() == tmp_path / "mcp"
        assert default_keys_file() == tmp_path / "mcp" / KEYS_FILE_NAME

    def test_no_curator_home_uses_user_home(self, monkeypatch):
        monkeypatch.delenv("CURATOR_HOME", raising=False)
        assert default_mcp_dir() == Path.home() / ".curator" / "mcp"


# ---------------------------------------------------------------------------
# StoredKey serialization
# ---------------------------------------------------------------------------


class TestStoredKeySerialization:
    def test_to_dict_includes_all_fields(self):
        sk = StoredKey(
            name="n",
            key_hash="h",
            created_at="c",
            last_used_at="l",
            description="d",
        )
        d = sk.to_dict()
        assert d == {
            "name": "n",
            "key_hash": "h",
            "created_at": "c",
            "last_used_at": "l",
            "description": "d",
        }

    def test_from_dict_handles_missing_optional_fields(self):
        sk = StoredKey.from_dict({
            "name": "n",
            "key_hash": "h",
            "created_at": "c",
        })
        assert sk.last_used_at is None
        assert sk.description is None
