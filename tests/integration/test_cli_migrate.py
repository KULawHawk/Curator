"""CLI integration tests for v1.0.0a1 migrate command.

Tests the `curator migrate` Typer subcommand via CliRunner with real
filesystem ops. Mirrors the pattern of test_cli_organize.py.

Each test seeds real files on disk, indexes them in a real CuratorDB,
then invokes the CLI via CliRunner.

NB: pytest's tmp_path lives under %LOCALAPPDATA% on Windows, which the
SafetyService correctly flags as CAUTION. So either the tests stub
safety to SAFE (most tests, since they're testing CLI mechanics) or
they explicitly verify the CAUTION-skip behavior.
"""

from __future__ import annotations

import importlib.util
import json
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import pytest
import xxhash
from typer.testing import CliRunner

from curator.cli.main import app
from curator.cli.runtime import build_runtime
from curator.config import Config
from curator.models.file import FileEntity
from curator.models.source import SourceConfig


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def seeded_db(tmp_path, monkeypatch):
    """Build a curator DB seeded with 3 real files at tmp_path/library/.

    Patches SafetyService.check_path globally for this test to return
    SAFE so the CLI exercises the full move pipeline (tmp_path is under
    %LOCALAPPDATA% on Windows so real safety would return CAUTION).

    Returns (db_path, src_root, files).
    """
    db_path = tmp_path / "migration.db"

    # Force runtime to use our DB path via env override (Curator reads
    # CURATOR_DB or via --db CLI flag).
    monkeypatch.setenv("CURATOR_DB", str(db_path))

    # Globally patch SafetyService.check_path so files under tmp_path
    # are SAFE for migration mechanics tests.
    from curator.services.safety import SafetyReport, SafetyService, SafetyLevel as SL

    def _safe_check(self, path, **kw):
        return SafetyReport(path=path, level=SL.SAFE)

    monkeypatch.setattr(SafetyService, "check_path", _safe_check)

    # Build a runtime + seed the DB.
    cfg = Config.load()
    rt = build_runtime(
        config=cfg, db_path_override=db_path,
        json_output=False, no_color=True, verbosity=0,
    )
    try:
        rt.source_repo.insert(SourceConfig(
            source_id="local", source_type="local", display_name="Local",
        ))
    except Exception:
        pass

    src_root = tmp_path / "library"
    files = []
    for name, content in [
        ("song1.mp3", b"track1 bytes" * 100),
        ("song2.mp3", b"track2 bytes" * 50),
        ("Photos/img.jpg", b"jpeg bytes\xff" * 200),
    ]:
        p = src_root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
        h = xxhash.xxh3_128(content).hexdigest()
        e = FileEntity(
            curator_id=uuid4(), source_id="local",
            source_path=str(p), size=len(content),
            mtime=datetime.fromtimestamp(p.stat().st_mtime),
            extension=p.suffix.lower(),
            xxhash3_128=h,
        )
        rt.file_repo.upsert(e)
        files.append(e)

    return db_path, src_root, files


def test_migrate_command_listed_in_help(runner):
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "migrate" in result.stdout


def test_migrate_help_shows_phase_1_note(runner):
    result = runner.invoke(app, ["migrate", "--help"])
    assert result.exit_code == 0
    # v1.7.62: on POSIX CI, Rich/Typer writes help output via a Console
    # that goes to a stream CliRunner doesn't capture in `result.stdout`
    # (likely stderr or a separate file descriptor). Use `result.output`
    # (combined) and strip ANSI codes so the substring assertion is
    # robust against Rich's formatting choices.
    import re
    output = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
    # Phase 1 docstring + the "--apply" gate note appear
    assert "Phase 1" in output or "phase 1" in output.lower()
    assert "--apply" in output
    assert "--ext" in output


def test_plan_only_does_not_move(runner, seeded_db, tmp_path):
    db_path, src_root, files = seeded_db
    dst_root = tmp_path / "library_new"
    result = runner.invoke(app, [
        "--db", str(db_path),
        "migrate", "local", str(src_root), str(dst_root),
    ])
    assert result.exit_code == 0
    # All 3 sources still on disk
    for f in files:
        assert Path(f.source_path).exists()
    # No dst created
    assert not dst_root.exists()


def test_plan_json_output_shape(runner, seeded_db, tmp_path):
    db_path, src_root, files = seeded_db
    dst_root = tmp_path / "library_new"
    result = runner.invoke(app, [
        "--json", "--db", str(db_path),
        "migrate", "local", str(src_root), str(dst_root),
    ])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["action"] == "migrate.plan"
    assert payload["src_source_id"] == "local"
    assert payload["total"] == 3
    assert payload["safe"] == 3
    assert payload["caution"] == 0
    assert payload["refuse"] == 0
    assert len(payload["moves"]) == 3
    # Each move has the expected fields
    m = payload["moves"][0]
    assert {"curator_id", "src_path", "dst_path", "safety_level", "size"} <= m.keys()


def test_apply_moves_files_with_hash_verify(runner, seeded_db, tmp_path):
    db_path, src_root, files = seeded_db
    dst_root = tmp_path / "library_new"
    result = runner.invoke(app, [
        "--json", "--db", str(db_path),
        "migrate", "local", str(src_root), str(dst_root), "--apply",
    ])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["action"] == "migrate.apply"
    assert payload["moved"] == 3
    assert payload["failed"] == 0
    # All 3 dst files exist
    for orig in files:
        rel = Path(orig.source_path).relative_to(src_root)
        assert (dst_root / rel).exists()


def test_apply_no_safe_files_returns_zero_moved(runner, seeded_db, tmp_path):
    """When extension filter excludes everything, the apply path
    short-circuits with moved=0."""
    db_path, src_root, files = seeded_db
    dst_root = tmp_path / "library_new"
    result = runner.invoke(app, [
        "--json", "--db", str(db_path),
        "migrate", "local", str(src_root), str(dst_root),
        "--apply", "--ext", ".nonexistent",
    ])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["action"] == "migrate.apply"
    assert payload["moved"] == 0
    # Phase 2 changed wording: "no SAFE files" -> "no eligible files"
    # because --include-caution widens eligibility beyond SAFE.
    assert "no eligible files" in payload.get("reason", "")


def test_dst_inside_src_exits_2(runner, seeded_db, tmp_path):
    db_path, src_root, files = seeded_db
    bad_dst = src_root / "nested"
    result = runner.invoke(app, [
        "--db", str(db_path),
        "migrate", "local", str(src_root), str(bad_dst),
    ])
    assert result.exit_code == 2
    # All sources untouched
    for f in files:
        assert Path(f.source_path).exists()


def test_extension_filter_narrows_plan(runner, seeded_db, tmp_path):
    db_path, src_root, files = seeded_db
    dst_root = tmp_path / "library_new"
    result = runner.invoke(app, [
        "--json", "--db", str(db_path),
        "migrate", "local", str(src_root), str(dst_root),
        "--ext", ".mp3",
    ])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["total"] == 2
    for m in payload["moves"]:
        assert m["src_path"].endswith(".mp3")


# ---------------------------------------------------------------------------
# A3: CLI extensions for Phase 2 (lifecycle + flags)
# ---------------------------------------------------------------------------


class TestLifecycleList:
    def test_list_empty_returns_dim_message(self, runner, seeded_db):
        db_path, _, _ = seeded_db
        result = runner.invoke(app, [
            "--db", str(db_path), "migrate", "--list",
        ])
        assert result.exit_code == 0
        assert "No migration jobs" in result.stdout

    def test_list_after_phase2_apply_shows_job(
        self, runner, seeded_db, tmp_path,
    ):
        db_path, src_root, files = seeded_db
        dst_root = tmp_path / "library_new"
        # Create a Phase 2 job by using --workers 2 --apply
        applied = runner.invoke(app, [
            "--db", str(db_path),
            "migrate", "local", str(src_root), str(dst_root),
            "--apply", "--workers", "2",
        ])
        assert applied.exit_code == 0
        # Now list should show 1 job
        listed = runner.invoke(app, [
            "--json", "--db", str(db_path), "migrate", "--list",
        ])
        assert listed.exit_code == 0
        payload = json.loads(listed.stdout)
        assert isinstance(payload, list)
        assert len(payload) == 1
        assert payload[0]["status"] == "completed"
        assert payload[0]["files_copied"] == 3  # all 3 files moved

    def test_list_status_filter_narrows(
        self, runner, seeded_db, tmp_path,
    ):
        db_path, src_root, files = seeded_db
        # Create one Phase 2 job (will end as 'completed')
        runner.invoke(app, [
            "--db", str(db_path),
            "migrate", "local", str(src_root), str(tmp_path / "out"),
            "--apply", "--workers", "2",
        ])
        # Filter for 'running' (none should match)
        result = runner.invoke(app, [
            "--json", "--db", str(db_path),
            "migrate", "--list", "--status-filter", "running",
        ])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload == []


class TestLifecycleStatus:
    def test_status_bad_uuid_exits_2(self, runner, seeded_db):
        db_path, _, _ = seeded_db
        result = runner.invoke(app, [
            "--db", str(db_path), "migrate", "--status", "not-a-uuid",
        ])
        assert result.exit_code == 2
        assert "job_id" in result.output.lower() or "uuid" in result.output.lower()

    def test_status_unknown_uuid_exits_1(self, runner, seeded_db):
        db_path, _, _ = seeded_db
        result = runner.invoke(app, [
            "--db", str(db_path),
            "migrate", "--status", "00000000-0000-0000-0000-000000000000",
        ])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_status_known_id_full_dict(
        self, runner, seeded_db, tmp_path,
    ):
        db_path, src_root, files = seeded_db
        # Create a Phase 2 job
        applied = runner.invoke(app, [
            "--json", "--db", str(db_path),
            "migrate", "local", str(src_root), str(tmp_path / "out"),
            "--apply", "--workers", "2",
        ])
        assert applied.exit_code == 0
        applied_payload = json.loads(applied.stdout)
        job_id = applied_payload["job_id"]
        # Query its status
        status = runner.invoke(app, [
            "--json", "--db", str(db_path),
            "migrate", "--status", job_id,
        ])
        assert status.exit_code == 0
        payload = json.loads(status.stdout)
        assert payload["job_id"] == job_id
        assert payload["status"] == "completed"
        assert payload["files_total"] == 3
        assert payload["files_copied"] == 3
        assert payload["files_failed"] == 0
        assert payload["bytes_copied"] > 0
        assert "progress_histogram" in payload
        assert "options" in payload


class TestLifecycleAbort:
    def test_abort_bad_uuid_exits_2(self, runner, seeded_db):
        db_path, _, _ = seeded_db
        result = runner.invoke(app, [
            "--db", str(db_path), "migrate", "--abort", "garbage",
        ])
        assert result.exit_code == 2

    def test_abort_unknown_job_is_noop(self, runner, seeded_db):
        """Sending abort to a non-running job is a quiet no-op (the
        signal is dropped if no thread is listening)."""
        db_path, _, _ = seeded_db
        result = runner.invoke(app, [
            "--db", str(db_path),
            "migrate", "--abort", "00000000-0000-0000-0000-000000000000",
        ])
        assert result.exit_code == 0
        assert "Abort signal sent" in result.stdout

    def test_abort_json_output_shape(self, runner, seeded_db):
        db_path, _, _ = seeded_db
        result = runner.invoke(app, [
            "--json", "--db", str(db_path),
            "migrate", "--abort", "00000000-0000-0000-0000-000000000000",
        ])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["action"] == "migrate.abort"
        assert payload["sent"] is True


class TestLifecycleResume:
    def test_resume_unknown_id_exits_1(self, runner, seeded_db):
        db_path, _, _ = seeded_db
        result = runner.invoke(app, [
            "--db", str(db_path),
            "migrate", "--resume", "00000000-0000-0000-0000-000000000000",
        ])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_resume_completed_job_returns_report(
        self, runner, seeded_db, tmp_path,
    ):
        """Resuming an already-completed job is a no-op that returns the
        existing report (no re-execution)."""
        db_path, src_root, files = seeded_db
        # Create a Phase 2 job
        applied = runner.invoke(app, [
            "--json", "--db", str(db_path),
            "migrate", "local", str(src_root), str(tmp_path / "out"),
            "--apply", "--workers", "2",
        ])
        assert applied.exit_code == 0
        job_id = json.loads(applied.stdout)["job_id"]
        # Resume it (should be no-op)
        resumed = runner.invoke(app, [
            "--json", "--db", str(db_path),
            "migrate", "--resume", job_id,
        ])
        assert resumed.exit_code == 0
        resumed_payload = json.loads(resumed.stdout)
        assert resumed_payload["job_id"] == job_id
        assert resumed_payload["moved"] == 3


class TestRoutingWorkers:
    def test_workers_gt_1_routes_to_phase2(
        self, runner, seeded_db, tmp_path,
    ):
        """--workers > 1 creates a persisted migration_jobs row."""
        db_path, src_root, files = seeded_db
        result = runner.invoke(app, [
            "--json", "--db", str(db_path),
            "migrate", "local", str(src_root), str(tmp_path / "out"),
            "--apply", "--workers", "4",
        ])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        # Phase 2 path -> JSON includes job_id
        assert "job_id" in payload
        assert payload["moved"] == 3
        # And a job exists in the DB now
        listed = runner.invoke(app, [
            "--json", "--db", str(db_path), "migrate", "--list",
        ])
        jobs = json.loads(listed.stdout)
        assert len(jobs) == 1

    def test_workers_eq_1_stays_phase1(
        self, runner, seeded_db, tmp_path,
    ):
        """--workers 1 (default) does NOT create a migration_jobs row."""
        db_path, src_root, files = seeded_db
        result = runner.invoke(app, [
            "--json", "--db", str(db_path),
            "migrate", "local", str(src_root), str(tmp_path / "out"),
            "--apply",  # no --workers, defaults to 1
        ])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        # Phase 1 path -> NO job_id in payload
        assert "job_id" not in payload
        # And no jobs in --list
        listed = runner.invoke(app, [
            "--json", "--db", str(db_path), "migrate", "--list",
        ])
        jobs = json.loads(listed.stdout)
        assert jobs == []


class TestKeepSourceFlag:
    def test_keep_source_preserves_files(
        self, runner, seeded_db, tmp_path,
    ):
        db_path, src_root, files = seeded_db
        dst_root = tmp_path / "out"
        result = runner.invoke(app, [
            "--db", str(db_path),
            "migrate", "local", str(src_root), str(dst_root),
            "--apply", "--keep-source",
        ])
        assert result.exit_code == 0
        # All 3 sources still on disk
        for f in files:
            assert Path(f.source_path).exists()
        # All 3 dsts created
        for f in files:
            rel = Path(f.source_path).relative_to(src_root)
            assert (dst_root / rel).exists()

    def test_keep_source_json_reflects_flag(
        self, runner, seeded_db, tmp_path,
    ):
        db_path, src_root, files = seeded_db
        result = runner.invoke(app, [
            "--json", "--db", str(db_path),
            "migrate", "local", str(src_root), str(tmp_path / "out"),
            "--apply", "--keep-source",
        ])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["keep_source"] is True
        assert payload["moved"] == 3  # COPIED counts as moved in headline
        # All per-file outcomes are 'copied' not 'moved'
        for r in payload["results"]:
            if r["outcome"]:
                assert r["outcome"] == "copied"


class TestPlanFilters:
    def test_include_glob_narrows(self, runner, seeded_db, tmp_path):
        db_path, src_root, files = seeded_db
        result = runner.invoke(app, [
            "--json", "--db", str(db_path),
            "migrate", "local", str(src_root), str(tmp_path / "out"),
            "--include", "*.mp3",
        ])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        # Only the 2 mp3 files (excludes the Photos/img.jpg)
        assert payload["total"] == 2
        for m in payload["moves"]:
            assert m["src_path"].endswith(".mp3")

    def test_exclude_glob_narrows(self, runner, seeded_db, tmp_path):
        db_path, src_root, files = seeded_db
        result = runner.invoke(app, [
            "--json", "--db", str(db_path),
            "migrate", "local", str(src_root), str(tmp_path / "out"),
            "--exclude", "Photos/*",
        ])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        # Excludes the photo, keeps the 2 mp3s
        assert payload["total"] == 2
        for m in payload["moves"]:
            assert "Photos" not in m["src_path"]

    def test_path_prefix_narrows_to_subdir(
        self, runner, seeded_db, tmp_path,
    ):
        db_path, src_root, files = seeded_db
        result = runner.invoke(app, [
            "--json", "--db", str(db_path),
            "migrate", "local", str(src_root), str(tmp_path / "out"),
            "--path-prefix", "Photos",
        ])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        # Only the Photos/img.jpg
        assert payload["total"] == 1
        assert "Photos" in payload["moves"][0]["src_path"]


class TestGuardsAndErrors:
    @pytest.mark.skipif(
        importlib.util.find_spec("pydrive2") is not None,
        reason=(
            "This test asserts that the gdrive plugin cannot dispatch a "
            "cross-source migration when PyDrive2 is missing (exit code 2 "
            "+ 'cross-source' message). When PyDrive2 IS installed (e.g. via "
            "the [cloud] extra, or transitively via Curator's own deps), the "
            "capability check legitimately succeeds and the migration runs. "
            "Skipping to avoid false negatives in environments where Drive "
            "functionality is intentionally available."
        ),
    )
    def test_dst_source_id_different_exits_2(
        self, runner, seeded_db, tmp_path,
    ):
        """Cross-source migration to a dst whose plugin doesn't
        advertise supports_write should error cleanly. In the test env
        gdrive plugin can't load PyDrive2 so it doesn't claim 'gdrive'
        sources, so the capability check refuses."""
        db_path, src_root, files = seeded_db
        result = runner.invoke(app, [
            "--db", str(db_path),
            "migrate", "local", str(src_root), str(tmp_path / "out"),
            "--dst-source-id", "gdrive",
        ])
        assert result.exit_code == 2
        assert "cross-source" in result.output.lower()

    def test_missing_positional_no_lifecycle_exits_2(
        self, runner, seeded_db,
    ):
        """Bare `migrate` with no positional args + no lifecycle flag
        produces a clean error."""
        db_path, _, _ = seeded_db
        result = runner.invoke(app, [
            "--db", str(db_path), "migrate",
        ])
        assert result.exit_code == 2
        assert "required" in result.output.lower()


# ---------------------------------------------------------------------------
# Session B: Cross-source migration via CLI (--dst-source-id)
# ---------------------------------------------------------------------------


@pytest.fixture
def cross_source_seeded_db(tmp_path, monkeypatch):
    """Like ``seeded_db`` but registers TWO local source IDs.

    Both ``local`` and ``local:vault`` are owned by ``LocalPlugin`` via
    its ``_owns()`` prefix matching, so the cross-source CLI path is
    fully exercised through real bytes/files (no PyDrive2 or mocks).

    Returns ``(db_path, src_root, files)`` -- 3 files seeded under
    ``source_id='local'`` at ``tmp_path/library/``.
    """
    db_path = tmp_path / "migration_cs.db"
    monkeypatch.setenv("CURATOR_DB", str(db_path))

    # Stub safety -> SAFE for everything under tmp_path
    from curator.services.safety import SafetyReport, SafetyService, SafetyLevel as SL

    def _safe_check(self, path, **kw):
        return SafetyReport(path=path, level=SL.SAFE)

    monkeypatch.setattr(SafetyService, "check_path", _safe_check)

    cfg = Config.load()
    rt = build_runtime(
        config=cfg, db_path_override=db_path,
        json_output=False, no_color=True, verbosity=0,
    )
    for sid, name in [("local", "Local Primary"), ("local:vault", "Local Vault")]:
        try:
            rt.source_repo.insert(SourceConfig(
                source_id=sid, source_type="local", display_name=name,
            ))
        except Exception:
            pass

    src_root = tmp_path / "library"
    files = []
    for name, content in [
        ("song1.mp3", b"track1 bytes" * 100),
        ("song2.mp3", b"track2 bytes" * 50),
        ("Photos/img.jpg", b"jpeg bytes\xff" * 200),
    ]:
        p = src_root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
        h = xxhash.xxh3_128(content).hexdigest()
        e = FileEntity(
            curator_id=uuid4(), source_id="local",
            source_path=str(p), size=len(content),
            mtime=datetime.fromtimestamp(p.stat().st_mtime),
            extension=p.suffix.lower(),
            xxhash3_128=h,
        )
        rt.file_repo.upsert(e)
        files.append(e)

    return db_path, src_root, files


class TestCrossSourceCLI:
    def test_cross_source_apply_moves_files_via_cli(
        self, runner, cross_source_seeded_db, tmp_path,
    ):
        """--dst-source-id local:vault --apply moves files end-to-end
        (Phase 1 path, default workers=1)."""
        db_path, src_root, files = cross_source_seeded_db
        dst_root = tmp_path / "vault"
        result = runner.invoke(app, [
            "--db", str(db_path),
            "migrate", "local", str(src_root), str(dst_root),
            "--apply", "--dst-source-id", "local:vault",
        ])
        assert result.exit_code == 0
        # Srcs trashed, dsts present
        for f in files:
            rel = Path(f.source_path).relative_to(src_root)
            assert (dst_root / rel).exists()

    def test_cross_source_workers_routes_to_phase2(
        self, runner, cross_source_seeded_db, tmp_path,
    ):
        """--dst-source-id + --workers > 1 creates a persistent job."""
        db_path, src_root, files = cross_source_seeded_db
        result = runner.invoke(app, [
            "--json", "--db", str(db_path),
            "migrate", "local", str(src_root), str(tmp_path / "vault"),
            "--apply", "--dst-source-id", "local:vault",
            "--workers", "4",
        ])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        # Phase 2 path -> JSON includes job_id
        assert "job_id" in payload
        assert payload["moved"] == 3
        # And the job exists in --list with cross-source src/dst
        listed = runner.invoke(app, [
            "--json", "--db", str(db_path), "migrate", "--list",
        ])
        jobs = json.loads(listed.stdout)
        assert len(jobs) == 1
        assert jobs[0]["src_source_id"] == "local"
        assert jobs[0]["dst_source_id"] == "local:vault"
        assert jobs[0]["files_copied"] == 3

    def test_cross_source_keep_source_via_cli(
        self, runner, cross_source_seeded_db, tmp_path,
    ):
        """--dst-source-id + --keep-source preserves srcs and reports
        COPIED outcomes in JSON."""
        db_path, src_root, files = cross_source_seeded_db
        dst_root = tmp_path / "vault"
        result = runner.invoke(app, [
            "--json", "--db", str(db_path),
            "migrate", "local", str(src_root), str(dst_root),
            "--apply", "--dst-source-id", "local:vault",
            "--keep-source",
        ])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["keep_source"] is True
        assert payload["moved"] == 3
        # All srcs still on disk
        for f in files:
            assert Path(f.source_path).exists()
        # All dsts created
        for f in files:
            rel = Path(f.source_path).relative_to(src_root)
            assert (dst_root / rel).exists()
        # All per-file outcomes are 'copied'
        for r in payload["results"]:
            if r["outcome"]:
                assert r["outcome"] == "copied"

    def test_cross_source_unsupported_dst_exits_2(
        self, runner, cross_source_seeded_db, tmp_path,
    ):
        """--dst-source-id pointing at a source no plugin supports for
        write produces a capability-check error (exit 2)."""
        db_path, src_root, _ = cross_source_seeded_db
        result = runner.invoke(app, [
            "--db", str(db_path),
            "migrate", "local", str(src_root), str(tmp_path / "out"),
            "--dst-source-id", "never_registered_source",
        ])
        assert result.exit_code == 2
        # Error mentions the failed source + the supports_write capability
        out = result.output.lower()
        assert "never_registered_source" in out
        assert "supports_write" in out or "supports write" in out

    def test_cross_source_plan_only_shows_both_source_ids(
        self, runner, cross_source_seeded_db, tmp_path,
    ):
        """Plan-only mode (no --apply) for cross-source should still
        succeed (capability check passes) and JSON output reflects the
        cross-source src/dst pair."""
        db_path, src_root, _ = cross_source_seeded_db
        result = runner.invoke(app, [
            "--json", "--db", str(db_path),
            "migrate", "local", str(src_root), str(tmp_path / "vault"),
            "--dst-source-id", "local:vault",
        ])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["action"] == "migrate.plan"
        assert payload["src_source_id"] == "local"
        assert payload["dst_source_id"] == "local:vault"
        assert payload["total"] == 3
