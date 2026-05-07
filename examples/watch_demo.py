"""Minimal end-to-end demo of the Phase Beta v0.16+v0.17 file watcher.

Watches a directory you pass on the command line, prints every event,
and (optionally) runs an incremental scan_paths on each event so the
Curator index stays live.

Run::

    python examples/watch_demo.py /path/to/watch
    python examples/watch_demo.py /path/to/watch --apply
    python examples/watch_demo.py /path/to/watch --apply --db /tmp/demo.db

Press Ctrl+C to stop.

This script uses the same wiring as ``curator watch`` from the CLI; it
exists as a copy-pasteable starting point for users who want to embed
reactive scanning into their own scripts.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running from a checkout without ``pip install -e .``
HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "src"
if SRC.exists() and str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from curator.cli.runtime import build_runtime
from curator.config import Config
from curator.models import SourceConfig
from curator.services.watch import (
    NoLocalSourcesError,
    WatchService,
    WatchUnavailableError,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "root",
        type=Path,
        help="Directory to watch (will be added as a 'local' source).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Run scan_paths on each event, keeping the index live.",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=Path.home() / ".curator-watch-demo.db",
        help="DB path (default: ~/.curator-watch-demo.db).",
    )
    parser.add_argument(
        "--source-id",
        default="local:watch_demo",
        help="Source id for the watched directory.",
    )
    args = parser.parse_args()

    if not args.root.is_dir():
        parser.error(f"{args.root} is not a directory")

    # Build a runtime against the demo DB.
    config = Config.load()
    runtime = build_runtime(
        config=config,
        db_path_override=args.db,
        verbosity=0,
    )

    # Register the source if it doesn't exist yet.
    existing = runtime.source_repo.get(args.source_id)
    if existing is None:
        runtime.source_repo.insert(SourceConfig(
            source_id=args.source_id,
            source_type="local",
            display_name=f"watch_demo: {args.root}",
            enabled=True,
            config={"root": str(args.root.resolve())},
        ))
        print(f"[demo] registered source {args.source_id} -> {args.root}")
    else:
        print(f"[demo] using existing source {args.source_id}")

    # Build the watcher and run.
    service = WatchService(runtime.source_repo)
    print(f"[demo] watching {args.root} (apply={args.apply}). Press Ctrl+C to stop.")

    try:
        for change in service.watch(source_ids=[args.source_id]):
            print(f"  {change.kind.value:<8} {change.path}")
            if args.apply:
                report = runtime.scan.scan_paths(
                    source_id=change.source_id,
                    paths=[str(change.path)],
                )
                bits = []
                if report.files_new:
                    bits.append(f"new={report.files_new}")
                if report.files_updated:
                    bits.append(f"updated={report.files_updated}")
                if report.files_deleted:
                    bits.append(f"deleted={report.files_deleted}")
                if report.lineage_edges_created:
                    bits.append(f"edges={report.lineage_edges_created}")
                if bits:
                    print(f"    -> {' '.join(bits)}")
    except WatchUnavailableError as e:
        print(f"[demo] watchfiles isn't installed: {e}", file=sys.stderr)
        return 2
    except NoLocalSourcesError as e:
        print(f"[demo] no sources to watch: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n[demo] stopped (Ctrl+C)")
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
