"""Pytest fixtures shared across all Curator test modules.

These fixtures replace the per-step smoke scripts that drove development.
The fixture set covers:

  * **DB fixtures**: temp-path :class:`CuratorDB` with migrations applied.
  * **Plugin fixtures**: a fresh plugin manager per test, so tests don't
    contaminate each other through the singleton.
  * **Repository / service bundles**: ``Bunch``-style namespaces so tests
    can write ``repos.files.insert(...)`` and ``services.scan.scan(...)``.
  * **Filesystem fixtures**: ``tmp_tree`` (a SIBLING dir to db_path inside
    pytest's tmp_path) plus ``make_file`` for creating files there.

Conventions:
  * Tests use temp paths only — no per-user data leaks.
  * Each test gets its own DB; no cross-test contamination.
  * The plugin manager singleton is reset at the start of every test
    (some tests register custom plugins).
  * ``tmp_tree`` and ``db_path`` are SIBLINGS, never the same directory.
    This matters because tests scan ``tmp_tree`` and would otherwise
    pick up the SQLite DB + WAL/SHM as "files in the tree".
"""

from __future__ import annotations

import dataclasses
from datetime import datetime
from pathlib import Path
from typing import Iterator

import pluggy
import pytest
from typer.testing import CliRunner

from curator.plugins import get_plugin_manager, reset_plugin_manager
from curator.services import (
    AuditService,
    BundleService,
    ClassificationService,
    HashPipeline,
    LineageService,
    ScanService,
    TrashService,
)
from curator.storage import CuratorDB
from curator.storage.repositories import (
    AuditRepository,
    BundleRepository,
    FileRepository,
    HashCacheRepository,
    LineageRepository,
    ScanJobRepository,
    SourceRepository,
    TrashRepository,
)


# ---------------------------------------------------------------------------
# Bundles (typed namespaces) — keep test code uncluttered
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class Repos:
    """Bundle of all 8 repositories so tests can pass one fixture around."""

    files: FileRepository
    bundles: BundleRepository
    lineage: LineageRepository
    trash: TrashRepository
    audit: AuditRepository
    sources: SourceRepository
    jobs: ScanJobRepository
    cache: HashCacheRepository


@dataclasses.dataclass
class Services:
    """Bundle of all 7 services + plugin manager."""

    pm: pluggy.PluginManager
    audit: AuditService
    classification: ClassificationService
    hash_pipeline: HashPipeline
    lineage: LineageService
    bundle: BundleService
    trash: TrashService
    scan: ScanService


# ---------------------------------------------------------------------------
# Filesystem fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_tree(tmp_path: Path) -> Path:
    """Per-test directory for files we'll scan/hash/trash.

    Lives at ``<tmp_path>/tree`` (sibling of ``db_path``) so scans that
    walk this directory don't pick up the SQLite DB.
    """
    p = tmp_path / "tree"
    p.mkdir(parents=True, exist_ok=True)
    return p


@pytest.fixture
def make_file(tmp_tree: Path):
    """Factory: ``make_file('a.py', 'print("hi")')`` returns a Path.

    Files are placed inside ``tmp_tree`` (which is per-test) so tests
    don't see each other's files. Subdirectories in the path are
    auto-created.
    """
    def _make(rel_path: str, content: str | bytes = b"", mtime: datetime | None = None) -> Path:
        p = tmp_tree / rel_path
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, str):
            p.write_text(content, encoding="utf-8")
        else:
            p.write_bytes(content)
        if mtime is not None:
            ts = mtime.timestamp()
            import os as _os
            _os.utime(str(p), (ts, ts))
        return p
    return _make


# ---------------------------------------------------------------------------
# DB fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Per-test SQLite DB path.

    Lives at ``<tmp_path>/db/curator_test.db`` (sibling of tmp_tree).
    """
    db_dir = tmp_path / "db"
    db_dir.mkdir(parents=True, exist_ok=True)
    return db_dir / "curator_test.db"


@pytest.fixture
def db(db_path: Path) -> Iterator[CuratorDB]:
    """Per-test :class:`CuratorDB` with migrations applied."""
    database = CuratorDB(db_path)
    database.init()
    try:
        yield database
    finally:
        database.close_thread_connection()


@pytest.fixture
def repos(db: CuratorDB) -> Repos:
    """All 8 repositories wired to the test DB."""
    return Repos(
        files=FileRepository(db),
        bundles=BundleRepository(db),
        lineage=LineageRepository(db),
        trash=TrashRepository(db),
        audit=AuditRepository(db),
        sources=SourceRepository(db),
        jobs=ScanJobRepository(db),
        cache=HashCacheRepository(db),
    )


# ---------------------------------------------------------------------------
# Plugin fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def plugin_manager() -> Iterator[pluggy.PluginManager]:
    """Fresh plugin manager per test.

    Resets the singleton before AND after the test so:
      - tests can register custom plugins without affecting each other,
      - the singleton state doesn't bleed back into production code paths.
    """
    reset_plugin_manager()
    pm = get_plugin_manager()
    try:
        yield pm
    finally:
        reset_plugin_manager()


@pytest.fixture
def services(db: CuratorDB, repos: Repos, plugin_manager: pluggy.PluginManager) -> Services:
    """All 7 services wired together."""
    audit = AuditService(repos.audit)
    classification = ClassificationService(plugin_manager)
    hash_pipeline = HashPipeline(plugin_manager, repos.cache)
    lineage = LineageService(plugin_manager, repos.files, repos.lineage)
    bundle = BundleService(plugin_manager, repos.bundles, repos.files)
    trash = TrashService(
        plugin_manager, repos.files, repos.trash, repos.bundles, repos.audit,
    )
    scan = ScanService(
        plugin_manager, repos.files, repos.sources, repos.jobs,
        hash_pipeline, classification, lineage, audit,
    )
    return Services(
        pm=plugin_manager,
        audit=audit,
        classification=classification,
        hash_pipeline=hash_pipeline,
        lineage=lineage,
        bundle=bundle,
        trash=trash,
        scan=scan,
    )


# ---------------------------------------------------------------------------
# Convenience: registered "local" source bootstrap
# ---------------------------------------------------------------------------

@pytest.fixture
def local_source(repos: Repos):
    """Insert a 'local' SourceConfig and return it.

    Tests that exercise FileEntity insertion directly (without going
    through ScanService) need this for the FK constraint to be satisfied.
    """
    from curator.models import SourceConfig
    src = SourceConfig(
        source_id="local",
        source_type="local",
        display_name="Local FS (test)",
    )
    repos.sources.insert(src)
    return src


# ---------------------------------------------------------------------------
# CLI-test fixtures (shared by every tests/integration/test_cli_*.py file)
# ---------------------------------------------------------------------------

@pytest.fixture
def runner() -> CliRunner:
    """A Typer CliRunner. Click 8.2+ separates stdout/stderr automatically."""
    return CliRunner()


@pytest.fixture
def cli_db(tmp_path: Path) -> Path:
    """Per-test DB path for CLI invocations.

    Lives in a sibling dir of any ``tmp_tree`` use so scans don't pick
    up the SQLite DB itself. (Same convention as the ``db_path`` fixture
    used by non-CLI tests.)
    """
    db_dir = tmp_path / "cli_db"
    db_dir.mkdir(parents=True, exist_ok=True)
    return db_dir / "cli_test.db"
