"""Google Drive auth helper (Phase Beta gate #5 polish, v0.42).

Wraps PyDrive2's interactive OAuth flow with a Curator-aware filesystem
convention so users don't have to think about credential paths.

Per-alias layout (under ``~/.curator/gdrive/<alias>/``):

    ~/.curator/gdrive/
      personal/
        client_secrets.json   (user-supplied, from Google Cloud Console)
        credentials.json      (managed by PyDrive2; refresh tokens live here)
      work/
        client_secrets.json
        credentials.json
      ...

The ``alias`` is the part after ``gdrive:`` in source IDs, e.g.
``gdrive:personal`` uses the ``personal/`` directory.

This module deliberately keeps OAuth-side effects in ONE function
(:func:`run_interactive_auth`) so the rest can be unit-tested without
network or PyDrive2.

Status discovery is fully offline: :func:`auth_status` returns a dict
describing what's on disk without touching the network.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_ALIAS = "default"
"""When users don't pass an alias, default to this name."""

CLIENT_SECRETS_NAME = "client_secrets.json"
CREDENTIALS_NAME = "credentials.json"


@dataclass(frozen=True)
class AuthPaths:
    """Concrete filesystem paths for one alias's auth artifacts."""

    alias: str
    """The alias name (e.g. 'personal', 'work', 'default')."""

    dir: Path
    """Directory containing both files."""

    client_secrets: Path
    """Where ``client_secrets.json`` is expected."""

    credentials: Path
    """Where PyDrive2 stores its credentials JSON."""


def default_gdrive_dir() -> Path:
    """Return Curator's per-user gdrive auth directory.

    Defaults to ``~/.curator/gdrive``. Honors ``CURATOR_HOME`` if set
    (for tests + non-default deployments).
    """
    base = os.environ.get("CURATOR_HOME")
    if base:
        return Path(base) / "gdrive"
    return Path.home() / ".curator" / "gdrive"


def paths_for_alias(alias: str, *, base: Path | None = None) -> AuthPaths:
    """Compute the per-alias auth-file paths.

    Args:
        alias: The alias name. Cannot contain path separators.
        base: Override the gdrive base directory (defaults to
            :func:`default_gdrive_dir`). Useful for tests.

    Raises:
        ValueError: If ``alias`` is empty, contains separators, or
            collides with reserved names.
    """
    if not alias:
        raise ValueError("alias cannot be empty")
    if "/" in alias or "\\" in alias or alias in (".", ".."):
        raise ValueError(
            f"alias {alias!r} cannot contain path separators or "
            "be '.'/'..'"
        )
    base = base if base is not None else default_gdrive_dir()
    alias_dir = base / alias
    return AuthPaths(
        alias=alias,
        dir=alias_dir,
        client_secrets=alias_dir / CLIENT_SECRETS_NAME,
        credentials=alias_dir / CREDENTIALS_NAME,
    )


@dataclass(frozen=True)
class AuthStatus:
    """Snapshot of auth state for one alias (purely offline)."""

    paths: AuthPaths
    """Computed paths for this alias."""

    has_client_secrets: bool
    """True if the client_secrets.json file exists on disk."""

    has_credentials: bool
    """True if the credentials.json file exists on disk."""

    state: str
    """One of: ``no_client_secrets`` (need user upload),
    ``no_credentials`` (ready to run auth flow),
    ``credentials_present`` (auth has been run; refresh token expected
    to be valid)."""

    detail: str
    """Human-readable explanation of state."""

    def to_dict(self) -> dict[str, Any]:
        """Serializable form for ``--json`` output."""
        return {
            "alias": self.paths.alias,
            "dir": str(self.paths.dir),
            "client_secrets": str(self.paths.client_secrets),
            "credentials": str(self.paths.credentials),
            "has_client_secrets": self.has_client_secrets,
            "has_credentials": self.has_credentials,
            "state": self.state,
            "detail": self.detail,
        }


def auth_status(alias: str, *, base: Path | None = None) -> AuthStatus:
    """Compute current auth state for ``alias`` (offline; no network)."""
    paths = paths_for_alias(alias, base=base)
    has_cs = paths.client_secrets.is_file()
    has_creds = paths.credentials.is_file()
    if not has_cs:
        return AuthStatus(
            paths=paths,
            has_client_secrets=False,
            has_credentials=has_creds,
            state="no_client_secrets",
            detail=(
                f"Place client_secrets.json from Google Cloud Console at "
                f"{paths.client_secrets}"
            ),
        )
    if not has_creds:
        return AuthStatus(
            paths=paths,
            has_client_secrets=True,
            has_credentials=False,
            state="no_credentials",
            detail=(
                f"Run 'curator gdrive auth {alias}' to complete OAuth and "
                f"populate {paths.credentials}"
            ),
        )
    return AuthStatus(
        paths=paths,
        has_client_secrets=True,
        has_credentials=True,
        state="credentials_present",
        detail=(
            f"Auth complete. PyDrive2 will refresh the token automatically "
            f"using {paths.credentials}"
        ),
    )


def ensure_alias_dir(alias: str, *, base: Path | None = None) -> AuthPaths:
    """Create the alias directory if missing; return its paths."""
    paths = paths_for_alias(alias, base=base)
    paths.dir.mkdir(parents=True, exist_ok=True)
    return paths


class PyDrive2NotInstalled(RuntimeError):
    """Raised when PyDrive2 isn't installed in the current environment."""


class ClientSecretsMissing(FileNotFoundError):
    """Raised when client_secrets.json is missing for an alias."""


def run_interactive_auth(
    paths: AuthPaths,
    *,
    auth_method: str = "command_line",
) -> None:
    """Drive the PyDrive2 interactive OAuth flow + persist credentials.

    Args:
        paths: AuthPaths for the alias to authenticate.
        auth_method: Either ``"command_line"`` (default; prints URL to
            console + reads the auth code from stdin) or ``"local_webserver"``
            (opens a browser; uses a localhost callback). The latter
            requires graphical session.

    Raises:
        PyDrive2NotInstalled: PyDrive2 missing.
        ClientSecretsMissing: client_secrets.json not at expected path.
        RuntimeError: PyDrive2's auth flow failed for any other reason.

    Side effects:
        Writes ``credentials.json`` at ``paths.credentials`` on success.
    """
    try:
        from pydrive2.auth import GoogleAuth
    except ImportError as e:
        raise PyDrive2NotInstalled(
            "PyDrive2 is not installed. Install with: "
            "pip install 'curator[cloud]'"
        ) from e

    if not paths.client_secrets.is_file():
        raise ClientSecretsMissing(
            f"client_secrets.json not found at {paths.client_secrets}. "
            f"Download it from Google Cloud Console (Credentials -> "
            f"OAuth 2.0 Client ID -> Download JSON) and place it there."
        )

    paths.dir.mkdir(parents=True, exist_ok=True)

    gauth = GoogleAuth()
    gauth.LoadClientConfigFile(str(paths.client_secrets))

    try:
        if auth_method == "local_webserver":
            gauth.LocalWebserverAuth()
        elif auth_method == "command_line":
            gauth.CommandLineAuth()
        else:
            raise ValueError(
                f"Unknown auth_method {auth_method!r}; "
                "use 'command_line' or 'local_webserver'"
            )
    except Exception as e:
        raise RuntimeError(
            f"PyDrive2 auth flow failed: {e}"
        ) from e

    try:
        gauth.SaveCredentialsFile(str(paths.credentials))
    except Exception as e:
        raise RuntimeError(
            f"Failed to save credentials to {paths.credentials}: {e}"
        ) from e


def source_config_for_alias(alias: str, *, base: Path | None = None) -> dict[str, Any]:
    """Return a SourceConfig.config dict pointing at this alias's auth files.

    Convenient for ``curator sources add --type gdrive --name gdrive:<alias>``
    integrations.
    """
    paths = paths_for_alias(alias, base=base)
    return {
        "client_secrets_path": str(paths.client_secrets),
        "credentials_path": str(paths.credentials),
        "root_folder_id": "root",
    }


__all__ = [
    "DEFAULT_ALIAS",
    "CLIENT_SECRETS_NAME",
    "CREDENTIALS_NAME",
    "AuthPaths",
    "AuthStatus",
    "PyDrive2NotInstalled",
    "ClientSecretsMissing",
    "default_gdrive_dir",
    "paths_for_alias",
    "auth_status",
    "ensure_alias_dir",
    "run_interactive_auth",
    "source_config_for_alias",
]
