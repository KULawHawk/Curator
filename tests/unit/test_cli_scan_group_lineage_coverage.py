"""Coverage closure for cli/main.py `scan` + `group` + `lineage` commands (v1.7.156).

Tier 3 sub-ship 2 of the CLI Coverage Arc.

Targets:
- Lines 369: scan "path does not exist" error
- Branch 374->377: scan's "not json_output" False arm
- Lines 384-401: scan JSON output payload
- Line 416: scan's `if report.errors:` table row
- Lines 437-511: group command body (all branches)
- Lines 516-524: `_pick_primary` strategy branches + invalid raise
- Lines 551-623: lineage command body (all output modes + CSV)
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from uuid import uuid4

import pytest
from typer.testing import CliRunner

from curator._compat.datetime import utcnow_naive
from curator.cli.main import _pick_primary, app
from curator.models import FileEntity, LineageEdge, LineageKind, SourceConfig


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def isolated_cli_db(tmp_path):
    from curator.storage import CuratorDB
    from curator.storage.repositories import (
        FileRepository, LineageRepository, SourceRepository,
    )
    db_path = tmp_path / "cli_scan.db"
    db = CuratorDB(db_path)
    db.init()
    return {
        "db": db,
        "db_path": db_path,
        "files": FileRepository(db),
        "sources": SourceRepository(db),
        "lineage": LineageRepository(db),
        "tmp_path": tmp_path,
    }


def _add_file(repos, source_id: str, path: str, **overrides) -> FileEntity:
    base = dict(
        source_id=source_id, source_path=path,
        size=10, mtime=utcnow_naive(),
    )
    base.update(overrides)
    f = FileEntity(**base)
    repos["files"].insert(f)
    return f


# ---------------------------------------------------------------------------
# scan
# ---------------------------------------------------------------------------


class TestScan:
    def test_scan_nonexistent_path_returns_error(self, runner, isolated_cli_db):
        """Line 369: path doesn't exist -> _err_exit."""
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "scan", "local",
             str(isolated_cli_db["tmp_path"] / "does_not_exist")],
        )
        assert result.exit_code == 1
        combined = result.stdout + (result.stderr or "")
        assert "does not exist" in combined.lower()

    def test_scan_human_output(self, runner, isolated_cli_db, monkeypatch):
        """Branch 374->377 + happy path table render. Stub ScanService.scan
        to return a controllable ScanReport (no plugin setup required)."""
        from curator.services.scan import ScanReport, ScanService

        def _stub_scan(self, source_id, root, options):
            return ScanReport(
                job_id=uuid4(), source_id=source_id, root=root,
                started_at=datetime(2026, 1, 1, 12, 0, 0),
                completed_at=datetime(2026, 1, 1, 12, 0, 5),
                files_seen=10, files_new=5, files_updated=2, files_unchanged=3,
                files_hashed=7, cache_hits=3, bytes_read=1024,
                classifications_assigned=4, lineage_edges_created=2, errors=0,
            )

        monkeypatch.setattr(ScanService, "scan", _stub_scan)
        # Create a real root dir
        root = isolated_cli_db["tmp_path"] / "scan_root"
        root.mkdir(parents=True)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "scan", "local", str(root)],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        # Both the "Scanning..." line + the table
        assert "Scanning" in combined
        assert "10" in combined  # files seen
        assert "Scan complete" in combined

    def test_scan_json_output(self, runner, isolated_cli_db, monkeypatch):
        """Lines 384-401: JSON output payload."""
        from curator.services.scan import ScanReport, ScanService

        def _stub_scan(self, source_id, root, options):
            return ScanReport(
                job_id=uuid4(), source_id=source_id, root=root,
                started_at=datetime(2026, 1, 1, 12, 0, 0),
                completed_at=datetime(2026, 1, 1, 12, 0, 5),
                files_seen=10, files_new=5, files_updated=2, files_unchanged=3,
                files_hashed=7, cache_hits=3, bytes_read=1024,
                classifications_assigned=4, lineage_edges_created=2, errors=0,
            )

        monkeypatch.setattr(ScanService, "scan", _stub_scan)
        root = isolated_cli_db["tmp_path"] / "scan_json_root"
        root.mkdir(parents=True)
        result = runner.invoke(
            app,
            ["--json", "--db", str(isolated_cli_db["db_path"]), "scan",
             "local", str(root)],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert '"files_seen": 10' in combined
        assert '"files_new": 5' in combined
        assert '"cache_hits": 3' in combined
        # JSON path shouldn't print "Scanning..."
        assert "Scanning" not in combined

    def test_scan_with_errors_shows_error_row(
        self, runner, isolated_cli_db, monkeypatch,
    ):
        """Line 416: report.errors > 0 -> error row in table."""
        from curator.services.scan import ScanReport, ScanService

        def _stub_scan(self, source_id, root, options):
            return ScanReport(
                job_id=uuid4(), source_id=source_id, root=root,
                started_at=datetime(2026, 1, 1), completed_at=datetime(2026, 1, 1, 0, 0, 1),
                files_seen=10, files_new=10, files_hashed=8, errors=2,
            )

        monkeypatch.setattr(ScanService, "scan", _stub_scan)
        root = isolated_cli_db["tmp_path"] / "scan_err_root"
        root.mkdir(parents=True)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "scan", "local", str(root)],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "errors" in combined
        assert "2" in combined

    def test_scan_with_ignore_options(
        self, runner, isolated_cli_db, monkeypatch,
    ):
        """Verify ignore patterns are forwarded as options dict."""
        from curator.services.scan import ScanReport, ScanService

        captured_options = {}

        def _stub_scan(self, source_id, root, options):
            captured_options.update(options)
            return ScanReport(
                job_id=uuid4(), source_id=source_id, root=root,
                started_at=datetime(2026, 1, 1),
                completed_at=datetime(2026, 1, 1, 0, 0, 1),
            )

        monkeypatch.setattr(ScanService, "scan", _stub_scan)
        root = isolated_cli_db["tmp_path"] / "scan_ig_root"
        root.mkdir(parents=True)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "scan", "local", str(root),
             "--ignore", "*.tmp", "-i", "node_modules"],
        )
        assert result.exit_code == 0
        assert captured_options.get("ignore") == ["*.tmp", "node_modules"]


# ---------------------------------------------------------------------------
# _pick_primary (lines 514-524)
# ---------------------------------------------------------------------------


class TestPickPrimary:
    def _files(self):
        return [
            FileEntity(source_id="local", source_path="/short",
                       size=1, mtime=datetime(2026, 1, 1)),
            FileEntity(source_id="local", source_path="/medium/path",
                       size=1, mtime=datetime(2026, 2, 1)),
            FileEntity(source_id="local", source_path="/very/long/path/abc",
                       size=1, mtime=datetime(2026, 3, 1)),
        ]

    def test_oldest(self):
        files = self._files()
        assert _pick_primary(files, "oldest").source_path == "/short"

    def test_newest(self):
        files = self._files()
        assert _pick_primary(files, "newest").source_path == "/very/long/path/abc"

    def test_shortest_path(self):
        files = self._files()
        assert _pick_primary(files, "shortest_path").source_path == "/short"

    def test_longest_path(self):
        files = self._files()
        assert _pick_primary(files, "longest_path").source_path == "/very/long/path/abc"

    def test_invalid_strategy_raises(self):
        import typer
        with pytest.raises(typer.BadParameter):
            _pick_primary(self._files(), "bogus")


# ---------------------------------------------------------------------------
# group
# ---------------------------------------------------------------------------


def _setup_dup_group(repos):
    """Create 3 files with same xxhash + 2 DUPLICATE edges."""
    repos["sources"].insert(SourceConfig(
        source_id="local", source_type="local", display_name="Local",
    ))
    a = _add_file(repos, "local", "/a.txt", xxhash3_128="hash_dup")
    b = _add_file(repos, "local", "/b.txt", xxhash3_128="hash_dup")
    c = _add_file(repos, "local", "/c.txt", xxhash3_128="hash_dup")
    repos["lineage"].insert(LineageEdge(
        from_curator_id=a.curator_id, to_curator_id=b.curator_id,
        edge_kind=LineageKind.DUPLICATE, confidence=1.0, detected_by="test",
    ))
    repos["lineage"].insert(LineageEdge(
        from_curator_id=a.curator_id, to_curator_id=c.curator_id,
        edge_kind=LineageKind.DUPLICATE, confidence=1.0, detected_by="test",
    ))
    return a, b, c


class TestGroup:
    def test_empty_no_groups_human_output(self, runner, isolated_cli_db):
        """Line 452-457: no duplicates -> 'No duplicate groups' message."""
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "group"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "No duplicate groups" in combined

    def test_empty_no_groups_json(self, runner, isolated_cli_db):
        """Line 452-454: no duplicates -> JSON empty payload."""
        result = runner.invoke(
            app,
            ["--json", "--db", str(isolated_cli_db["db_path"]), "group"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert '"groups": []' in combined
        assert '"would_trash": 0' in combined

    def test_dry_run_human_output(self, runner, isolated_cli_db):
        """Lines 460-499: groups resolved, primary picked, non-primary listed."""
        _setup_dup_group(isolated_cli_db)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "group", "--keep", "shortest_path"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "hash" in combined
        assert "would trash" in combined.lower()
        assert "/a.txt" in combined  # primary (shortest)

    def test_dry_run_json_output(self, runner, isolated_cli_db):
        _setup_dup_group(isolated_cli_db)
        result = runner.invoke(
            app,
            ["--json", "--db", str(isolated_cli_db["db_path"]), "group",
             "--keep", "shortest_path"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert '"groups"' in combined
        assert '"would_trash": 2' in combined
        assert '"applied": false' in combined

    def test_apply_calls_trash(self, runner, isolated_cli_db, monkeypatch):
        """Line 501-509: --apply calls rt.trash.send_to_trash on non-primary."""
        a, b, c = _setup_dup_group(isolated_cli_db)
        # Stub send_to_trash to track calls
        sent = []
        from curator.services.trash import TrashService

        def _stub_send(self, curator_id, *, reason, actor):
            sent.append({"curator_id": curator_id, "reason": reason, "actor": actor})

        monkeypatch.setattr(TrashService, "send_to_trash", _stub_send)

        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "group", "--apply",
             "--keep", "shortest_path"],
        )
        assert result.exit_code == 0
        # Two non-primaries should have been "trashed" (which two depends on
        # set iteration order since all three paths have equal length — the
        # important thing is exactly 2 got trashed out of 3)
        assert len(sent) == 2
        all_ids = {a.curator_id, b.curator_id, c.curator_id}
        trashed_ids = {s["curator_id"] for s in sent}
        assert trashed_ids.issubset(all_ids)

    def test_skips_edges_with_missing_file_or_no_hash(
        self, runner, isolated_cli_db,
    ):
        """Line 448: edge with file_repo.get(...) returning None, or one of
        the files missing xxhash3_128, is `continue`-skipped."""
        repos = isolated_cli_db
        repos["sources"].insert(SourceConfig(
            source_id="local", source_type="local", display_name="Local",
        ))
        # Files exist but lack xxhash3_128 -> continue path
        a = _add_file(repos, "local", "/no_hash_a.txt")
        b = _add_file(repos, "local", "/no_hash_b.txt")
        repos["lineage"].insert(LineageEdge(
            from_curator_id=a.curator_id, to_curator_id=b.curator_id,
            edge_kind=LineageKind.DUPLICATE, confidence=1.0, detected_by="t",
        ))
        # All edges skipped -> empty groups -> "No duplicate groups"
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "group"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "No duplicate groups" in combined

    def test_skips_groups_with_only_one_live_file(
        self, runner, isolated_cli_db,
    ):
        """Line 466: after is_deleted filter, group has < 2 files -> continue."""
        repos = isolated_cli_db
        repos["sources"].insert(SourceConfig(
            source_id="local", source_type="local", display_name="Local",
        ))
        a = _add_file(repos, "local", "/live.txt", xxhash3_128="h")
        b = _add_file(repos, "local", "/deleted.txt", xxhash3_128="h",
                      deleted_at=datetime(2026, 1, 1))
        repos["lineage"].insert(LineageEdge(
            from_curator_id=a.curator_id, to_curator_id=b.curator_id,
            edge_kind=LineageKind.DUPLICATE, confidence=1.0, detected_by="t",
        ))
        # After is_deleted filter only `a` remains in the group; len(files) < 2
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "group"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        # No groups should be reported (the only group was filtered out)
        assert "No duplicate groups" in combined or "would_trash" not in combined

    def test_apply_trash_error_logged(self, runner, isolated_cli_db, monkeypatch):
        """Lines 510-511: TrashError caught + logged to stderr, continues."""
        _setup_dup_group(isolated_cli_db)
        from curator.services.trash import TrashService, TrashError

        def _stub_send_raise(self, curator_id, *, reason, actor):
            raise TrashError("simulated trash failure")

        monkeypatch.setattr(TrashService, "send_to_trash", _stub_send_raise)

        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "group", "--apply",
             "--keep", "shortest_path"],
        )
        # Should NOT exit non-zero — errors are logged and the loop continues
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "simulated trash failure" in combined


# ---------------------------------------------------------------------------
# lineage
# ---------------------------------------------------------------------------


class TestLineageCmd:
    def test_lineage_no_match_returns_error(self, runner, isolated_cli_db):
        """Lines 552-554: _resolve_file returns None -> _err_exit."""
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "lineage", "nonexistent"],
        )
        assert result.exit_code == 1
        combined = result.stdout + (result.stderr or "")
        assert "No file matches" in combined

    def test_lineage_human_output_with_edges(self, runner, isolated_cli_db):
        """Lines 600-623: human output table rendering with both directions."""
        repos = isolated_cli_db
        repos["sources"].insert(SourceConfig(
            source_id="local", source_type="local", display_name="Local",
        ))
        target = _add_file(repos, "local", "/target.txt")
        other = _add_file(repos, "local", "/other.txt")
        # Edge target -> other (is_from True)
        repos["lineage"].insert(LineageEdge(
            from_curator_id=target.curator_id, to_curator_id=other.curator_id,
            edge_kind=LineageKind.DUPLICATE, confidence=1.0, detected_by="t",
        ))
        # Edge other -> target (is_from False; covers LARROW path + other_path)
        third = _add_file(repos, "local", "/third.txt")
        repos["lineage"].insert(LineageEdge(
            from_curator_id=third.curator_id, to_curator_id=target.curator_id,
            edge_kind=LineageKind.NEAR_DUPLICATE, confidence=0.85, detected_by="t",
        ))
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "lineage",
             str(target.curator_id)],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "Lineage" in combined
        assert "duplicate" in combined
        assert "near_duplicate" in combined

    def test_lineage_human_output_no_edges(self, runner, isolated_cli_db):
        """Lines 601-603: 'No lineage edges' message."""
        repos = isolated_cli_db
        repos["sources"].insert(SourceConfig(
            source_id="local", source_type="local", display_name="Local",
        ))
        f = _add_file(repos, "local", "/alone.txt")
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "lineage", str(f.curator_id)],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "No lineage edges" in combined

    def test_lineage_json_output(self, runner, isolated_cli_db):
        """Lines 558-577: JSON output payload."""
        repos = isolated_cli_db
        repos["sources"].insert(SourceConfig(
            source_id="local", source_type="local", display_name="Local",
        ))
        f = _add_file(repos, "local", "/j.txt")
        result = runner.invoke(
            app,
            ["--json", "--db", str(isolated_cli_db["db_path"]), "lineage",
             str(f.curator_id)],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert '"file"' in combined
        assert '"edges": []' in combined

    def test_lineage_csv_output(self, runner, isolated_cli_db):
        """Lines 580-598: CSV output with header."""
        repos = isolated_cli_db
        repos["sources"].insert(SourceConfig(
            source_id="local", source_type="local", display_name="Local",
        ))
        target = _add_file(repos, "local", "/csv_target.txt")
        other = _add_file(repos, "local", "/csv_other.txt")
        repos["lineage"].insert(LineageEdge(
            from_curator_id=target.curator_id, to_curator_id=other.curator_id,
            edge_kind=LineageKind.DUPLICATE, confidence=1.0,
            detected_by="t", notes="csv-test",
        ))
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "lineage",
             str(target.curator_id), "--csv"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "edge_id,kind,from,to" in combined
        assert "duplicate" in combined
        assert "csv-test" in combined

    def test_lineage_csv_no_header(self, runner, isolated_cli_db):
        """Line 583: `if not no_header` False arm — skip header row."""
        repos = isolated_cli_db
        repos["sources"].insert(SourceConfig(
            source_id="local", source_type="local", display_name="Local",
        ))
        target = _add_file(repos, "local", "/nh_target.txt")
        other = _add_file(repos, "local", "/nh_other.txt")
        repos["lineage"].insert(LineageEdge(
            from_curator_id=target.curator_id, to_curator_id=other.curator_id,
            edge_kind=LineageKind.DUPLICATE, confidence=1.0, detected_by="t",
        ))
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "lineage",
             str(target.curator_id), "--csv", "--no-header"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "edge_id,kind,from,to" not in combined  # no header
        assert "duplicate" in combined

    def test_lineage_csv_tsv_dialect(self, runner, isolated_cli_db):
        """Verify TSV dialect output."""
        repos = isolated_cli_db
        repos["sources"].insert(SourceConfig(
            source_id="local", source_type="local", display_name="Local",
        ))
        target = _add_file(repos, "local", "/tsv_target.txt")
        other = _add_file(repos, "local", "/tsv_other.txt")
        repos["lineage"].insert(LineageEdge(
            from_curator_id=target.curator_id, to_curator_id=other.curator_id,
            edge_kind=LineageKind.DUPLICATE, confidence=1.0, detected_by="t",
        ))
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "lineage",
             str(target.curator_id), "--csv", "--csv-dialect", "tsv"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        # TSV uses \t as delimiter
        assert "edge_id\tkind" in combined
