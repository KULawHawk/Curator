"""MCP API key authentication (v1.5.0).

Implements the key-management primitives for ``curator-mcp`` HTTP
authentication per ``docs/CURATOR_MCP_HTTP_AUTH_DESIGN.md`` v0.2
RATIFIED.

Per-user layout (under ``~/.curator/mcp/``):

    ~/.curator/mcp/
      api-keys.json         (managed by this module; stores key_hash, never plaintext)

Honors ``CURATOR_HOME`` env var for tests + non-default deployments,
mirroring :mod:`curator.services.gdrive_auth`'s convention.

This module is the data layer; CLI commands live in
:mod:`curator.cli.mcp_keys` (P2) and the FastMCP middleware lives in
:mod:`curator.mcp.server` (P3). All three reference the same primitives
defined here.

Constitutional alignment
------------------------

* **Aim 1 (Accuracy):** Auth state is persisted to disk and re-read on
  every validation. There's no in-memory cache that could fall out of
  sync with a revoked key.
* **Aim 8 (Auditability):** This module doesn't emit audit events
  itself (the middleware does), but it provides the data the audit
  events reference (key name, key prefix).
* **Article II Principle 4 (No Silent Failures):** Invalid or missing
  keys raise typed exceptions or return ``None`` explicitly; nothing
  is swallowed. The ONE exception is :func:`update_last_used` which
  silently no-ops if the named key was concurrently revoked, since
  that's the documented best-effort semantics.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger


# ---------------------------------------------------------------------------
# Constants (DM-3 RATIFIED: curm_ prefix + 40 chars URL-safe base64)
# ---------------------------------------------------------------------------


KEY_PREFIX = "curm_"
"""Format prefix for Curator MCP API keys per DM-3 RATIFIED.

Follows the GitHub PAT (``ghp_``), Stripe (``sk_``), Anthropic
(``sk-ant-``) convention so secret-scanners can detect accidentally
committed Curator keys.
"""

RANDOM_TOKEN_BYTES = 30
"""Bytes of entropy in the random portion. ``secrets.token_urlsafe(30)``
yields ~40 URL-safe-base64 chars (with no padding); plus the 5-char
prefix gives a 44- to 46-char total key.

30 bytes = 240 bits of entropy, well above any plausible brute-force
threshold for the lifetime of a v1.5.0 deployment.
"""

KEYS_FILE_NAME = "api-keys.json"
"""Filename within ``~/.curator/mcp/`` per DM-2 RATIFIED."""

SCHEMA_VERSION = 1
"""Version field at the top of the keys file. Bumped on incompatible
schema changes; v1.5.0 ships at v1."""


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def default_mcp_dir() -> Path:
    """Return Curator's per-user MCP config directory.

    Defaults to ``~/.curator/mcp``. Honors ``CURATOR_HOME`` if set
    (matching :func:`curator.services.gdrive_auth.default_gdrive_dir`'s
    convention so tests + non-default deployments can redirect both
    subsystems with one env var).
    """
    base = os.environ.get("CURATOR_HOME")
    if base:
        return Path(base) / "mcp"
    return Path.home() / ".curator" / "mcp"


def default_keys_file() -> Path:
    """Return the path to the MCP API keys JSON file."""
    return default_mcp_dir() / KEYS_FILE_NAME


# ---------------------------------------------------------------------------
# Key generation + hashing
# ---------------------------------------------------------------------------


def generate_key() -> str:
    """Generate a new API key in the ``curm_<random>`` format.

    Returns:
        The full plaintext key, 44\u201346 chars, suitable to give to the
        user. This is the ONLY time the plaintext key is materialized;
        callers that persist it MUST call :func:`hash_key` and store
        the hash, not the plaintext.

    Uses :func:`secrets.token_urlsafe` (cryptographically secure RNG
    sourced from the OS).
    """
    random_part = secrets.token_urlsafe(RANDOM_TOKEN_BYTES)
    return f"{KEY_PREFIX}{random_part}"


def hash_key(key: str) -> str:
    """Compute the storage hash for a key (SHA-256 hex digest, 64 chars).

    SHA-256 is the same digest used elsewhere in Curator (Drive's MD5
    is not used for security; xxhash3_128 is used for content
    addressing). For credential hashing, SHA-256 is industry standard
    and matches GitHub PAT / similar tooling.

    Note: this is not a password-hashing function (no salt, no work
    factor). That's correct because API keys are 240-bit random tokens
    \u2014 brute-force is computationally infeasible regardless of hash
    function. Salting is unnecessary because the key itself is already
    256 bits of randomness; rainbow-table attacks against random keys
    of that size aren't viable.
    """
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class StoredKey:
    """One entry in the keys file. Per DM-4 RATIFIED.

    Attributes:
        name: Unique per-user identifier (e.g. ``claude-desktop-home``).
        key_hash: SHA-256 hex digest of the plaintext key. Plaintext
            never leaves the generation call site.
        created_at: ISO 8601 UTC timestamp ending in ``Z``.
        last_used_at: ISO 8601 UTC timestamp of the most recent
            successful validation, or ``None`` if the key has never
            been used. Updated by :func:`update_last_used`.
        description: Optional human-readable note (e.g. ``Home laptop
            Claude Desktop``).
    """

    name: str
    key_hash: str
    created_at: str
    last_used_at: str | None
    description: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "StoredKey":
        return cls(
            name=d["name"],
            key_hash=d["key_hash"],
            created_at=d["created_at"],
            last_used_at=d.get("last_used_at"),
            description=d.get("description"),
        )


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class KeyFileError(Exception):
    """Raised when the keys file is corrupt, unreadable, or has an
    unsupported schema version."""


class DuplicateNameError(Exception):
    """Raised when :func:`add_key` is called with a name that already
    exists in the keys file."""


# ---------------------------------------------------------------------------
# Time helper
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    """Current UTC time in compact ISO 8601 with ``Z`` suffix.

    Format: ``2026-05-08T12:34:56Z`` (no fractional seconds, no offset
    other than Z). Matches the format other Curator timestamps use
    in audit log details.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------


def _set_secure_permissions(path: Path) -> None:
    """Restrict the file to the current user.

    On Unix, sets mode ``0o600`` (owner read/write only). On Windows,
    relies on the parent directory's ACL inheritance (the home
    directory is typically restricted to the current user's principal
    by default). The Windows behavior is documented as a v1.5.0
    limitation; explicit ACL tightening via ``icacls`` could be added
    in a future release if multi-user Windows hardening becomes a
    requirement.

    Errors are logged but not raised \u2014 a permissions failure shouldn't
    block the rest of the workflow, which is more important.
    """
    if sys.platform == "win32":
        return
    try:
        os.chmod(path, 0o600)
    except OSError as e:
        logger.warning(
            "MCP auth: failed to set 0600 on {p}: {e}", p=path, e=e,
        )


# ---------------------------------------------------------------------------
# File I/O (atomic via write-temp-then-rename)
# ---------------------------------------------------------------------------


def load_keys(path: Path | None = None) -> list[StoredKey]:
    """Load all keys from the JSON file.

    Args:
        path: Override the keys-file path. Defaults to
            :func:`default_keys_file`.

    Returns:
        List of :class:`StoredKey`. Empty list if the file doesn't
        exist (this is the normal first-time-startup case, not an
        error).

    Raises:
        :class:`KeyFileError`: if the file exists but is corrupt,
            non-JSON, or has an unsupported schema version.
    """
    if path is None:
        path = default_keys_file()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        raise KeyFileError(f"failed to read {path}: {e}") from e
    if not isinstance(data, dict):
        raise KeyFileError(f"{path} is not a JSON object")
    if data.get("version") != SCHEMA_VERSION:
        raise KeyFileError(
            f"{path} has unsupported schema version: "
            f"{data.get('version')!r} (expected {SCHEMA_VERSION})"
        )
    keys_list = data.get("keys", [])
    if not isinstance(keys_list, list):
        raise KeyFileError(f"{path} 'keys' field is not a list")
    return [StoredKey.from_dict(k) for k in keys_list]


def save_keys(keys: list[StoredKey], path: Path | None = None) -> None:
    """Persist all keys to the JSON file atomically.

    Uses write-to-temp-then-rename so a crash mid-write doesn't
    corrupt the file. Permissions are tightened (Unix ``0o600``) on
    the temp file BEFORE the rename so the target is never briefly
    world-readable.

    Args:
        keys: Full list of keys to persist (replaces any existing file).
        path: Override the keys-file path. Defaults to
            :func:`default_keys_file`.

    Raises:
        :class:`OSError`: on unrecoverable filesystem errors (parent
            dir not writable, disk full, etc.). The original target
            file is NOT modified in that case.
    """
    if path is None:
        path = default_keys_file()
    path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "version": SCHEMA_VERSION,
        "keys": [k.to_dict() for k in keys],
    }
    body = json.dumps(payload, indent=2)

    fd, tmp_path = tempfile.mkstemp(
        prefix=f".{KEYS_FILE_NAME}.tmp.",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(body)
        # Set permissions BEFORE the rename so the target is never
        # briefly readable by other users (window between rename and
        # chmod could otherwise leak the file).
        _set_secure_permissions(Path(tmp_path))
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# High-level operations (used by CLI + middleware)
# ---------------------------------------------------------------------------


def add_key(
    name: str,
    *,
    description: str | None = None,
    path: Path | None = None,
) -> str:
    """Generate and persist a new key.

    Args:
        name: Unique identifier for the new key. Must not collide
            with any existing key's name.
        description: Optional human-readable note.
        path: Override the keys-file path. Defaults to
            :func:`default_keys_file`.

    Returns:
        The plaintext key. This is the ONLY time the user can see the
        key; subsequent :func:`load_keys` calls return only the
        ``key_hash``. Caller (CLI ``generate`` command) must show this
        to the user immediately.

    Raises:
        :class:`DuplicateNameError`: if ``name`` already exists.
    """
    keys = load_keys(path)
    if any(k.name == name for k in keys):
        raise DuplicateNameError(
            f"key with name {name!r} already exists; "
            f"revoke the old one first or pick a different name"
        )
    plaintext = generate_key()
    stored = StoredKey(
        name=name,
        key_hash=hash_key(plaintext),
        created_at=_now_iso(),
        last_used_at=None,
        description=description,
    )
    keys.append(stored)
    save_keys(keys, path)
    return plaintext


def remove_key(name: str, *, path: Path | None = None) -> bool:
    """Remove a key by name.

    Args:
        name: Identifier of the key to remove.
        path: Override the keys-file path. Defaults to
            :func:`default_keys_file`.

    Returns:
        ``True`` if a key with that name was found and removed,
        ``False`` otherwise. Idempotent: removing a non-existent name
        is not an error.
    """
    keys = load_keys(path)
    new_keys = [k for k in keys if k.name != name]
    if len(new_keys) == len(keys):
        return False
    save_keys(new_keys, path)
    return True


def validate_key(
    presented: str, *, path: Path | None = None,
) -> StoredKey | None:
    """Check whether ``presented`` matches any stored key.

    Does NOT update ``last_used_at`` \u2014 callers that want to track
    usage call :func:`update_last_used` separately. This separation
    lets the auth middleware decide when to track (e.g., only on
    successful tool dispatch, not just on header validation).

    Args:
        presented: The plaintext key from the HTTP Authorization
            header.
        path: Override the keys-file path. Defaults to
            :func:`default_keys_file`.

    Returns:
        The matching :class:`StoredKey` if validation succeeds,
        ``None`` otherwise. Reasons for ``None``:
        * Empty / whitespace-only presented value.
        * Missing the ``curm_`` prefix.
        * Hash doesn't match any stored key.
        * Keys file doesn't exist (no keys configured).
    """
    if not presented or not presented.startswith(KEY_PREFIX):
        return None
    presented_hash = hash_key(presented)
    for k in load_keys(path):
        if k.key_hash == presented_hash:
            return k
    return None


def update_last_used(name: str, *, path: Path | None = None) -> None:
    """Update ``last_used_at`` for the named key. Atomic.

    Best-effort: if the key was concurrently revoked between the
    validation and this call, the update silently no-ops. Race-window
    is small but non-zero in multi-process scenarios.

    Args:
        name: Identifier of the key whose timestamp to update.
        path: Override the keys-file path. Defaults to
            :func:`default_keys_file`.
    """
    keys = load_keys(path)
    found = False
    for k in keys:
        if k.name == name:
            k.last_used_at = _now_iso()
            found = True
            break
    if found:
        save_keys(keys, path)
