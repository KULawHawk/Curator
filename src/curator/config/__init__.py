"""Curator configuration loading.

DESIGN.md §16.

Configuration lives in ``curator.toml`` (TOML). Resolution order:
    1. Explicit ``--config`` path on the CLI (highest priority).
    2. ``$CURATOR_CONFIG`` environment variable.
    3. ``./curator.toml`` (current directory).
    4. ``<platformdirs.user_config_dir('curator')>/curator.toml``.
    5. Built-in defaults (always applied as a base layer).

The ``Config`` class is a thin wrapper over a nested dict with helpers
for: TOML load/save, dotted-path access (``config.get("hash.prefix_bytes")``),
and resolution of the special ``"auto"`` values for ``db_path`` /
``log_path`` (which expand to platformdirs paths at load time).
"""

from __future__ import annotations

import copy
import os
import sys
from pathlib import Path
from typing import Any

# tomllib (read) is stdlib in Python 3.11+. tomli_w handles writes.
if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover — Phase Alpha targets 3.11+
    import tomli as tomllib  # type: ignore[import-not-found,no-redef]

from curator.config.defaults import DEFAULT_CONFIG


CONFIG_FILENAME = "curator.toml"
ENV_VAR = "CURATOR_CONFIG"


class Config:
    """In-memory configuration with TOML load/save.

    Internally stores a nested dict (the merge of defaults + user TOML).
    Use dotted-path access for ergonomics::

        config.get("hash.prefix_bytes")          # 4096
        config.get("trash.purge_older_than_days", default=30)
        config["curator"]["db_path"]             # also works
    """

    def __init__(self, data: dict[str, Any] | None = None):
        # Always start from a deep copy of defaults so callers can't
        # mutate them through the live config.
        self._data: dict[str, Any] = copy.deepcopy(DEFAULT_CONFIG)
        if data:
            self._merge(data)
        self._source_path: Path | None = None

    # ------------------------------------------------------------------
    # Class methods (preferred construction)
    # ------------------------------------------------------------------

    @classmethod
    def load(cls, explicit_path: str | os.PathLike[str] | None = None) -> "Config":
        """Load config from disk using the resolution order above.

        Args:
            explicit_path: highest-priority path. None means "search
                           for one in standard locations".

        Returns:
            A populated :class:`Config`. ``source_path`` reflects which
            file was actually loaded (None if pure defaults).
        """
        config = cls()
        path = cls._resolve_path(explicit_path)
        if path is not None and path.exists():
            with path.open("rb") as f:
                user_data = tomllib.load(f)
            config._merge(user_data)
            config._source_path = path
        config._resolve_auto_paths()
        return config

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Config":
        """Construct from an explicit dict (testing convenience)."""
        config = cls(data)
        config._resolve_auto_paths()
        return config

    # ------------------------------------------------------------------
    # Access
    # ------------------------------------------------------------------

    def get(self, dotted_key: str, default: Any = None) -> Any:
        """Get a value via dotted path: ``config.get("hash.prefix_bytes")``."""
        node: Any = self._data
        for part in dotted_key.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, dotted_key: str, value: Any) -> None:
        """Set a value via dotted path. Creates intermediate dicts as needed."""
        parts = dotted_key.split(".")
        node = self._data
        for part in parts[:-1]:
            if part not in node or not isinstance(node[part], dict):
                node[part] = {}
            node = node[part]
        node[parts[-1]] = value

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __contains__(self, key: str) -> bool:
        return key in self._data

    def as_dict(self) -> dict[str, Any]:
        """Return a deep copy of the underlying config dict."""
        return copy.deepcopy(self._data)

    @property
    def source_path(self) -> Path | None:
        """The TOML file this config was loaded from (if any)."""
        return self._source_path

    # ------------------------------------------------------------------
    # Convenience accessors for common settings
    # ------------------------------------------------------------------

    @property
    def db_path(self) -> Path:
        """Resolved DB path (``"auto"`` is expanded at load time)."""
        return Path(self.get("curator.db_path"))

    @property
    def log_path(self) -> Path:
        """Resolved log path (``"auto"`` is expanded at load time)."""
        return Path(self.get("curator.log_path"))

    @property
    def log_level(self) -> str:
        return str(self.get("curator.log_level", "INFO")).upper()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, path: str | os.PathLike[str] | None = None) -> Path:
        """Write this config to TOML.

        Args:
            path: destination. If None, use ``self.source_path`` (if set).

        Returns:
            The path actually written.

        Raises:
            ValueError: if no destination is available.
            ImportError: if ``tomli_w`` isn't installed (Phase Alpha
                         dependency; should always be present).
        """
        try:
            import tomli_w  # type: ignore[import-not-found]
        except ImportError as e:
            raise ImportError(
                "Saving config requires 'tomli_w'. Install with `pip install tomli-w`."
            ) from e

        target = Path(path) if path is not None else self._source_path
        if target is None:
            raise ValueError("No path given and config has no source_path")
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("wb") as f:
            tomli_w.dump(self._data, f)
        return target

    # ------------------------------------------------------------------
    # Internal: merge + path resolution
    # ------------------------------------------------------------------

    def _merge(self, user_data: dict[str, Any]) -> None:
        """Shallow-per-section merge: user sections fully replace defaults sections."""
        for section, value in user_data.items():
            if isinstance(value, dict) and isinstance(self._data.get(section), dict):
                # Within a section, deep-merge so users can override
                # individual keys without redefining the whole section.
                self._deep_update(self._data[section], value)
            else:
                # Non-section keys (rare) just replace.
                self._data[section] = copy.deepcopy(value)

    @staticmethod
    def _deep_update(target: dict[str, Any], source: dict[str, Any]) -> None:
        """Recursively merge ``source`` into ``target`` in place."""
        for key, value in source.items():
            if (
                key in target
                and isinstance(target[key], dict)
                and isinstance(value, dict)
            ):
                Config._deep_update(target[key], value)
            else:
                target[key] = copy.deepcopy(value)

    @staticmethod
    def _resolve_path(explicit: str | os.PathLike[str] | None) -> Path | None:
        """Find the config file using resolution order."""
        if explicit is not None:
            return Path(explicit)
        env = os.environ.get(ENV_VAR)
        if env:
            return Path(env)
        cwd_path = Path.cwd() / CONFIG_FILENAME
        if cwd_path.exists():
            return cwd_path
        try:
            from platformdirs import user_config_dir
            user_dir = Path(user_config_dir("curator"))
            user_path = user_dir / CONFIG_FILENAME
            if user_path.exists():
                return user_path
        except ImportError:
            pass  # platformdirs is in our deps; this branch is paranoid.
        return None

    def _resolve_auto_paths(self) -> None:
        """Expand ``"auto"`` for db_path and log_path."""
        try:
            from platformdirs import user_data_dir, user_log_dir
        except ImportError:  # pragma: no cover — platformdirs is required
            return

        if self.get("curator.db_path") == "auto":
            data_dir = Path(user_data_dir("curator"))
            self.set("curator.db_path", str(data_dir / "curator.db"))
        if self.get("curator.log_path") == "auto":
            log_dir = Path(user_log_dir("curator"))
            self.set("curator.log_path", str(log_dir / "curator.log"))


__all__ = ["Config", "DEFAULT_CONFIG", "CONFIG_FILENAME", "ENV_VAR"]
