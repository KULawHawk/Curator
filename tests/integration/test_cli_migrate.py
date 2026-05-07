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
    # Phase 1 docstring + the "--apply" gate note appear
    assert "Phase 1" in result.stdout or "phase 1" in result.stdout.lower()
    assert "--apply" in result.stdout
    assert "--ext" in result.stdout


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
    assert "no SAFE files" in payload.get("reason", "")


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
