"""Default configuration values.

DESIGN.md §16.1.

These are merged with whatever the user has in ``curator.toml`` so that
omitted keys fall back to sensible defaults. The merge is shallow per
top-level section (i.e. ``[hash]`` in user TOML replaces the entire
``hash`` dict, not just the keys the user set). For Phase Alpha this
shallow merge is intentional — we want users to be able to override a
whole section cleanly.
"""

from __future__ import annotations

from typing import Any


DEFAULT_CONFIG: dict[str, Any] = {
    "curator": {
        # ``"auto"`` is replaced with platformdirs.user_data_dir(...) at load time.
        "db_path": "auto",
        "log_path": "auto",
        "log_level": "INFO",
    },
    "hash": {
        "primary": "xxh3_128",
        "secondary": "md5",
        "fuzzy_for": [
            ".py", ".bas", ".vb", ".md", ".txt", ".rst", ".json", ".yaml",
            ".yml", ".toml", ".ini", ".cfg", ".html", ".css", ".js", ".ts",
            ".sql", ".csv", ".tsv", ".log", ".xml",
        ],
        "prefix_bytes": 4096,
        "suffix_bytes": 4096,
    },
    "trash": {
        # Phase Alpha: 'os_recycle_bin' is the only supported provider.
        # Phase Beta+ may add 'curator_managed' (Curator owns the trash dir).
        "provider": "os_recycle_bin",
        "restore_metadata": True,
        # ``None`` = never auto-purge from Curator's trash registry.
        "purge_older_than_days": None,
    },
    "detection": {
        "temp_files": {
            "patterns": [
                "*.tmp", "~$*", ".DS_Store", "Thumbs.db", "desktop.ini",
            ],
            "auto_trash": False,
        },
    },
    "lineage": {
        # Below this fuzzy similarity (0-100), don't store the edge.
        "fuzzy_threshold": 70,
        "auto_confirm_threshold": 0.95,
        "escalate_threshold": 0.70,
    },
    "source": {
        # No sources by default — user must add them via `curator source add`
        # or by editing curator.toml. The `local` source becomes implicit
        # the first time `curator scan local <root>` is invoked (auto-created).
    },
    "group": {
        # When --apply on `curator group`, which file in each duplicate
        # group should we KEEP? Choices: oldest | newest | shortest_path | longest_path.
        "default_keep_strategy": "oldest",
    },
    "plugins": {
        # List of plugin names to skip loading (matches the name passed to
        # ``pm.register(..., name=NAME)``).
        "disabled": [],
    },
}
