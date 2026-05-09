"""One-time setup script for registering a gdrive source in Curator.

Works around a v1.5.0 CLI gap: ``curator sources add --type gdrive``
registers the source's metadata but doesn't expose a way to set the
plugin-specific config (client_secrets_path, credentials_path,
root_folder_id). This script bridges the gap by constructing the full
SourceConfig and calling ``source_repo.upsert()`` directly.

Usage::

    python scripts/setup_gdrive_source.py [alias] [--folder-id FOLDER_ID]

Where ``alias`` defaults to ``src_drive`` (matching the convention used
in TRACER_SESSION_B_RUNBOOK.md). ``--folder-id`` defaults to ``"root"``
which means "the user's My Drive root"; pass a specific folder ID to
restrict the scope to a sub-folder (recommended for testing so test
files don't litter the root).

To find a folder ID: open the folder in https://drive.google.com,
look at the URL — the last segment after ``/folders/`` is the folder
ID.

Idempotent: if the source already exists, this script updates its
config rather than failing.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from curator.cli.runtime import build_runtime
from curator.config import Config
from curator.models.source import SourceConfig
from curator.services.gdrive_auth import (
    auth_status,
    source_config_for_alias,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Register or update a gdrive source in Curator's index, "
            "wiring it to an existing PyDrive2 auth alias."
        ),
    )
    parser.add_argument(
        "alias",
        nargs="?",
        default="src_drive",
        help=(
            "PyDrive2 alias to wire (must already have client_secrets.json + "
            "credentials.json under ~/.curator/gdrive/<alias>/). "
            "Default: src_drive."
        ),
    )
    parser.add_argument(
        "--folder-id",
        default="root",
        help=(
            "Drive folder ID to scope the source to. 'root' = user's My "
            "Drive root (default). For testing, create a dedicated folder "
            "in Drive and pass its ID so test files stay scoped."
        ),
    )
    parser.add_argument(
        "--source-id",
        default=None,
        help=(
            "Curator source_id to register. Defaults to 'gdrive:<alias>' "
            "(e.g. 'gdrive:src_drive'). The 'gdrive:' prefix is REQUIRED "
            "for the gdrive_source plugin to claim ownership of the source "
            "-- without the prefix, migration to this source will fail with "
            "'no registered plugin advertises supports_write'. Override "
            "only if you have a strong reason and the prefix is preserved."
        ),
    )
    parser.add_argument(
        "--display-name",
        default=None,
        help="Friendly name for the source. Defaults to 'Google Drive (<alias>)'.",
    )
    args = parser.parse_args()

    # Verify the alias has been authenticated (otherwise the source
    # would be registered but unusable).
    status = auth_status(args.alias)
    if status.state != "credentials_present":
        print(
            f"ERROR: alias {args.alias!r} is not authenticated.\n"
            f"  state: {status.state}\n"
            f"  detail: {status.detail}\n"
            f"\nRun 'curator gdrive auth {args.alias}' first.",
            file=sys.stderr,
        )
        return 2

    # Plugin ownership requires source_id to be 'gdrive' or start with
    # 'gdrive:' (see Plugin._owns in plugins/core/gdrive_source.py).
    # Default to 'gdrive:<alias>' so migrations work out of the box.
    source_id = args.source_id or f"gdrive:{args.alias}"
    if source_id != "gdrive" and not source_id.startswith("gdrive:"):
        print(
            f"ERROR: source_id {source_id!r} does not match the gdrive "
            f"plugin's ownership pattern. The plugin only claims source_ids "
            f"that are 'gdrive' or start with 'gdrive:'. Without ownership, "
            f"the source advertises no capabilities and migration to it "
            f"fails with 'no registered plugin advertises supports_write'. "
            f"Re-run with --source-id 'gdrive:{args.alias}' (or omit "
            f"--source-id to use the default).",
            file=sys.stderr,
        )
        return 2
    display_name = args.display_name or f"Google Drive ({args.alias})"

    # Build the config dict that the gdrive_source plugin expects.
    config = source_config_for_alias(args.alias)
    config["root_folder_id"] = args.folder_id

    # Build the runtime so we can talk to the index.
    runtime = build_runtime(
        config=Config.load(),
        json_output=False,
        no_color=False,
        verbosity=0,
    )

    src = SourceConfig(
        source_id=source_id,
        source_type="gdrive",
        display_name=display_name,
        config=config,
        enabled=True,
    )

    existing = runtime.source_repo.get(source_id)
    if existing is None:
        runtime.source_repo.insert(src)
        action = "registered"
    else:
        runtime.source_repo.upsert(src)
        action = "updated"

    runtime.audit.log(
        actor="scripts.setup_gdrive_source",
        action=f"source.{action}",
        entity_type="source",
        entity_id=source_id,
        details={
            "type": "gdrive",
            "alias": args.alias,
            "folder_id": args.folder_id,
        },
    )

    print(f"OK: {action} source {source_id!r}")
    print(f"  type: gdrive")
    print(f"  display_name: {display_name}")
    print(f"  alias: {args.alias}")
    print(f"  folder_id: {args.folder_id}")
    print(f"  client_secrets: {config['client_secrets_path']}")
    print(f"  credentials:    {config['credentials_path']}")
    print()
    print(f"Verify with: curator sources show {source_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
