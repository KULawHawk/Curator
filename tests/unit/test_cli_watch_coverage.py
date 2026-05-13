"""Coverage closure for cli/main.py `watch` command (v1.7.161).

Tier 3 sub-ship 7 of the CLI Coverage Arc.
"""

from __future__ import annotations

import sys
import types
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from curator.cli.main import app


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def isolated_cli_db(tmp_path):
    from curator.storage import CuratorDB
    db_path = tmp_path / "cli_watch.db"
    db = CuratorDB(db_path)
    db.init()
    return {"db_path": db_path}


# ---------------------------------------------------------------------------
# Import-error branch (lines 1568-1570)
# ---------------------------------------------------------------------------


class TestWatchImportError:
    def test_curator_services_watch_import_error(
        self, runner, isolated_cli_db, monkeypatch,
    ):
        """Lines 1568-1570: importing curator.services.watch fails -> exit 2."""
        # Pop the cached module so the in-function import re-executes
        monkeypatch.delitem(sys.modules, "curator.services.watch", raising=False)
        monkeypatch.setitem(sys.modules, "curator.services.watch", None)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "watch"],
        )
        assert result.exit_code == 2
        combined = result.stdout + (result.stderr or "")
        assert "watch service unavailable" in combined


# ---------------------------------------------------------------------------
# Service errors (WatchUnavailableError / NoLocalSourcesError)
# ---------------------------------------------------------------------------


class TestWatchServiceErrors:
    def test_watch_unavailable_error_exits_2(
        self, runner, isolated_cli_db, monkeypatch,
    ):
        """Lines 1577-1579: WatchUnavailableError -> exit 2."""
        from curator.services.watch import (
            WatchService, WatchUnavailableError,
        )

        def _stub_watch(self, *, source_ids=None):
            raise WatchUnavailableError("watchfiles not installed")

        monkeypatch.setattr(WatchService, "watch", _stub_watch)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "watch"],
        )
        assert result.exit_code == 2
        combined = result.stdout + (result.stderr or "")
        assert "watchfiles" in combined.lower()

    def test_no_local_sources_exits_1(
        self, runner, isolated_cli_db, monkeypatch,
    ):
        """Lines 1580-1582: NoLocalSourcesError -> exit 1."""
        from curator.services.watch import (
            WatchService, NoLocalSourcesError,
        )

        def _stub_watch(self, *, source_ids=None):
            raise NoLocalSourcesError("no local sources to watch")

        monkeypatch.setattr(WatchService, "watch", _stub_watch)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "watch"],
        )
        assert result.exit_code == 1
        combined = result.stdout + (result.stderr or "")
        assert "no local sources" in combined.lower()


# ---------------------------------------------------------------------------
# Event iteration: human, JSON, --apply
# ---------------------------------------------------------------------------


def _make_event(kind: str, path: str = "/tmp/x.txt", source_id: str = "local"):
    from curator.services.watch import ChangeKind, PathChange
    return PathChange(
        kind=ChangeKind(kind),
        path=Path(path),
        source_id=source_id,
        detected_at=datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc),
    )


class TestWatchHumanOutput:
    def test_emits_lines_per_event(
        self, runner, isolated_cli_db, monkeypatch,
    ):
        from curator.services.watch import WatchService

        def _stub_watch(self, *, source_ids=None):
            yield _make_event("added", "/a.txt")
            yield _make_event("modified", "/b.txt")
            yield _make_event("deleted", "/c.txt")

        monkeypatch.setattr(WatchService, "watch", _stub_watch)
        # Make __len__ work without setting up real roots
        monkeypatch.setattr(WatchService, "__len__", lambda self: 2)

        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "watch"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "Watching" in combined
        assert "added" in combined
        assert "modified" in combined
        assert "deleted" in combined


class TestWatchJsonOutput:
    def test_emits_json_lines(
        self, runner, isolated_cli_db, monkeypatch,
    ):
        from curator.services.watch import WatchService

        def _stub_watch(self, *, source_ids=None):
            yield _make_event("added", "/j.txt")

        monkeypatch.setattr(WatchService, "watch", _stub_watch)
        monkeypatch.setattr(WatchService, "__len__", lambda self: 1)

        result = runner.invoke(
            app,
            ["--json", "--db", str(isolated_cli_db["db_path"]), "watch"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert '"kind": "added"' in combined
        # In JSON mode we don't print the "Watching..." banner
        assert "Watching" not in combined


class TestWatchWithApply:
    def test_apply_triggers_scan_paths(
        self, runner, isolated_cli_db, monkeypatch,
    ):
        from curator.services.scan import ScanReport, ScanService
        from curator.services.watch import WatchService

        def _stub_watch(self, *, source_ids=None):
            yield _make_event("added", "/x.txt")

        scan_calls = []

        def _stub_scan_paths(self, *, source_id, paths):
            scan_calls.append({"source_id": source_id, "paths": paths})
            return ScanReport(
                job_id=__import__("uuid").uuid4(),
                source_id=source_id, root=paths[0],
                started_at=datetime(2026, 1, 1),
                completed_at=datetime(2026, 1, 1, 0, 0, 1),
                files_new=1, files_updated=2, files_deleted=0,
                lineage_edges_created=3, errors=0,
            )

        monkeypatch.setattr(WatchService, "watch", _stub_watch)
        monkeypatch.setattr(WatchService, "__len__", lambda self: 1)
        monkeypatch.setattr(ScanService, "scan_paths", _stub_scan_paths)

        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "watch", "--apply"],
        )
        assert result.exit_code == 0
        assert len(scan_calls) == 1
        combined = result.stdout + (result.stderr or "")
        assert "scan_paths" in combined or "new=1" in combined

    def test_apply_scan_paths_error_logged(
        self, runner, isolated_cli_db, monkeypatch,
    ):
        """Lines 1633-1635: scan_paths exception logged + watch continues."""
        from curator.services.scan import ScanService
        from curator.services.watch import WatchService

        def _stub_watch(self, *, source_ids=None):
            yield _make_event("modified", "/fails.txt")

        def _stub_scan_raise(self, *, source_id, paths):
            raise RuntimeError("scan_paths simulated failure")

        monkeypatch.setattr(WatchService, "watch", _stub_watch)
        monkeypatch.setattr(WatchService, "__len__", lambda self: 1)
        monkeypatch.setattr(ScanService, "scan_paths", _stub_scan_raise)

        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "watch", "--apply"],
        )
        # Should NOT exit on scan_paths failure - logged + continues
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "scan_paths failed" in combined

    def test_apply_scan_with_errors_in_report(
        self, runner, isolated_cli_db, monkeypatch,
    ):
        """Line 1628 (errors suffix) when report.errors > 0."""
        from curator.services.scan import ScanReport, ScanService
        from curator.services.watch import WatchService

        def _stub_watch(self, *, source_ids=None):
            yield _make_event("added", "/err.txt")

        def _stub_scan(self, *, source_id, paths):
            return ScanReport(
                job_id=__import__("uuid").uuid4(),
                source_id=source_id, root=paths[0],
                started_at=datetime(2026, 1, 1),
                completed_at=datetime(2026, 1, 1, 0, 0, 1),
                files_new=0, files_updated=0, files_deleted=0,
                errors=2,
            )

        monkeypatch.setattr(WatchService, "watch", _stub_watch)
        monkeypatch.setattr(WatchService, "__len__", lambda self: 1)
        monkeypatch.setattr(ScanService, "scan_paths", _stub_scan)

        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "watch", "--apply"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "errors=2" in combined


# ---------------------------------------------------------------------------
# KeyboardInterrupt
# ---------------------------------------------------------------------------


class TestWatchKeyboardInterrupt:
    def test_ctrl_c_exits_cleanly(
        self, runner, isolated_cli_db, monkeypatch,
    ):
        """Lines 1636-1639: KeyboardInterrupt -> graceful exit, no error."""
        from curator.services.watch import WatchService

        def _stub_watch(self, *, source_ids=None):
            yield _make_event("added", "/x.txt")
            raise KeyboardInterrupt

        monkeypatch.setattr(WatchService, "watch", _stub_watch)
        monkeypatch.setattr(WatchService, "__len__", lambda self: 1)

        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "watch"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "watch ended" in combined.lower()

    def test_ctrl_c_in_json_mode_skips_message(
        self, runner, isolated_cli_db, monkeypatch,
    ):
        """Branch 1637->1639: JSON mode + KeyboardInterrupt skips message."""
        from curator.services.watch import WatchService

        def _stub_watch(self, *, source_ids=None):
            # Yield one event, then raise KeyboardInterrupt during next()
            # (must yield first so this is a generator that raises inside
            # the for-loop try block, not at construction)
            yield _make_event("added", "/x.txt")
            raise KeyboardInterrupt

        monkeypatch.setattr(WatchService, "watch", _stub_watch)
        monkeypatch.setattr(WatchService, "__len__", lambda self: 1)

        result = runner.invoke(
            app,
            ["--json", "--db", str(isolated_cli_db["db_path"]), "watch"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        # JSON mode: no "watch ended" message
        assert "watch ended" not in combined.lower()


class TestWatchApplyAllReportFields:
    def test_apply_with_files_deleted_and_edges(
        self, runner, isolated_cli_db, monkeypatch,
    ):
        """Line 1624 + 1626 + 1629: deleted + edges suffix branches."""
        from curator.services.scan import ScanReport, ScanService
        from curator.services.watch import WatchService

        def _stub_watch(self, *, source_ids=None):
            yield _make_event("deleted", "/d.txt")

        def _stub_scan(self, *, source_id, paths):
            return ScanReport(
                job_id=__import__("uuid").uuid4(),
                source_id=source_id, root=paths[0],
                started_at=datetime(2026, 1, 1),
                completed_at=datetime(2026, 1, 1, 0, 0, 1),
                files_new=0, files_updated=0, files_deleted=5,
                lineage_edges_created=2, errors=0,
            )

        monkeypatch.setattr(WatchService, "watch", _stub_watch)
        monkeypatch.setattr(WatchService, "__len__", lambda self: 1)
        monkeypatch.setattr(ScanService, "scan_paths", _stub_scan)

        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "watch", "--apply"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "deleted=5" in combined
        assert "edges=2" in combined

    def test_apply_json_mode_skips_suffix_print(
        self, runner, isolated_cli_db, monkeypatch,
    ):
        """Branch 1617->1592: JSON mode + --apply skips the suffix print."""
        from curator.services.scan import ScanReport, ScanService
        from curator.services.watch import WatchService

        def _stub_watch(self, *, source_ids=None):
            yield _make_event("added", "/j.txt")

        def _stub_scan(self, *, source_id, paths):
            return ScanReport(
                job_id=__import__("uuid").uuid4(),
                source_id=source_id, root=paths[0],
                started_at=datetime(2026, 1, 1),
                completed_at=datetime(2026, 1, 1, 0, 0, 1),
                files_new=1,
            )

        monkeypatch.setattr(WatchService, "watch", _stub_watch)
        monkeypatch.setattr(WatchService, "__len__", lambda self: 1)
        monkeypatch.setattr(ScanService, "scan_paths", _stub_scan)

        result = runner.invoke(
            app,
            ["--json", "--db", str(isolated_cli_db["db_path"]),
             "watch", "--apply"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        # JSON event emitted but no human-readable scan_paths suffix
        assert '"kind": "added"' in combined
        assert "scan_paths:" not in combined

    def test_apply_with_no_suffix_fields_skips_print(
        self, runner, isolated_cli_db, monkeypatch,
    ):
        """Branch 1629->1592: when suffix is empty (no counters), skip print."""
        from curator.services.scan import ScanReport, ScanService
        from curator.services.watch import WatchService

        def _stub_watch(self, *, source_ids=None):
            yield _make_event("added", "/empty.txt")

        def _stub_scan(self, *, source_id, paths):
            return ScanReport(
                job_id=__import__("uuid").uuid4(),
                source_id=source_id, root=paths[0],
                started_at=datetime(2026, 1, 1),
                completed_at=datetime(2026, 1, 1, 0, 0, 1),
                # All counters zero -> suffix empty -> no print
                files_new=0, files_updated=0, files_deleted=0,
                lineage_edges_created=0, errors=0,
            )

        monkeypatch.setattr(WatchService, "watch", _stub_watch)
        monkeypatch.setattr(WatchService, "__len__", lambda self: 1)
        monkeypatch.setattr(ScanService, "scan_paths", _stub_scan)

        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "watch", "--apply"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        # Event still printed
        assert "added" in combined
        # But no scan_paths summary
        assert "scan_paths:" not in combined
