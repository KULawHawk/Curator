"""Coverage closure for cli/main.py `trash` + `restore` commands (v1.7.159).

Tier 3 sub-ship 5 of the CLI Coverage Arc.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from typer.testing import CliRunner

from curator._compat.datetime import utcnow_naive
from curator.cli.main import app
from curator.models import FileEntity, SourceConfig
from curator.models.trash import TrashRecord


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def isolated_cli_db(tmp_path):
    from curator.storage import CuratorDB
    from curator.storage.repositories import (
        FileRepository, SourceRepository, TrashRepository,
    )
    db_path = tmp_path / "cli_trash.db"
    db = CuratorDB(db_path)
    db.init()
    return {
        "db": db,
        "db_path": db_path,
        "files": FileRepository(db),
        "sources": SourceRepository(db),
        "trash": TrashRepository(db),
    }


def _setup(repos, path: str = "/file.txt"):
    repos["sources"].insert(SourceConfig(
        source_id="local", source_type="local", display_name="Local",
    ))
    f = FileEntity(
        source_id="local", source_path=path,
        size=10, mtime=utcnow_naive(),
    )
    repos["files"].insert(f)
    return f


# ---------------------------------------------------------------------------
# trash
# ---------------------------------------------------------------------------


class TestTrashCmd:
    def test_no_match_errors(self, runner, isolated_cli_db):
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "trash", "nonexistent"],
        )
        assert result.exit_code == 1
        combined = result.stdout + (result.stderr or "")
        assert "No file matches" in combined

    def test_dry_run_default(self, runner, isolated_cli_db):
        f = _setup(isolated_cli_db, "/dry.txt")
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "trash", str(f.curator_id)],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "would trash" in combined
        assert "/dry.txt" in combined

    def test_apply_happy_path(self, runner, isolated_cli_db, monkeypatch):
        f = _setup(isolated_cli_db, "/apply.txt")
        from curator.services.trash import TrashService

        def _stub_send(self, curator_id, *, reason, actor):
            return TrashRecord(
                curator_id=curator_id,
                original_source_id="local",
                original_path="/apply.txt",
                file_hash="h",
                trashed_at=datetime(2026, 1, 1),
                trashed_by=actor,
                reason=reason,
                bundle_memberships_snapshot=[],
                file_attrs_snapshot={},
            )

        monkeypatch.setattr(TrashService, "send_to_trash", _stub_send)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "trash", str(f.curator_id), "--apply",
             "--reason", "test-cleanup"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "Trashed" in combined

    def test_apply_json_output(self, runner, isolated_cli_db, monkeypatch):
        f = _setup(isolated_cli_db, "/json.txt")
        from curator.services.trash import TrashService

        def _stub_send(self, curator_id, *, reason, actor):
            return TrashRecord(
                curator_id=curator_id,
                original_source_id="local",
                original_path="/json.txt",
                file_hash="h",
                trashed_at=datetime(2026, 1, 1),
                trashed_by=actor,
                reason=reason,
                bundle_memberships_snapshot=[],
                file_attrs_snapshot={},
            )

        monkeypatch.setattr(TrashService, "send_to_trash", _stub_send)
        result = runner.invoke(
            app,
            ["--json", "--db", str(isolated_cli_db["db_path"]),
             "trash", str(f.curator_id), "--apply"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert '"trashed"' in combined
        assert '/json.txt' in combined

    def test_send2trash_unavailable_errors(
        self, runner, isolated_cli_db, monkeypatch,
    ):
        f = _setup(isolated_cli_db, "/unavail.txt")
        from curator.services.trash import TrashService, Send2TrashUnavailableError

        def _stub_raise(self, curator_id, *, reason, actor):
            raise Send2TrashUnavailableError("send2trash not installed")

        monkeypatch.setattr(TrashService, "send_to_trash", _stub_raise)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "trash", str(f.curator_id), "--apply"],
        )
        assert result.exit_code == 1
        combined = result.stdout + (result.stderr or "")
        assert "send2trash" in combined.lower()

    def test_trash_vetoed_errors(self, runner, isolated_cli_db, monkeypatch):
        f = _setup(isolated_cli_db, "/veto.txt")
        from curator.services.trash import TrashService, TrashVetoed

        def _stub_raise(self, curator_id, *, reason, actor):
            raise TrashVetoed("plugin vetoed trash")

        monkeypatch.setattr(TrashService, "send_to_trash", _stub_raise)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "trash", str(f.curator_id), "--apply"],
        )
        assert result.exit_code == 1
        combined = result.stdout + (result.stderr or "")
        assert "vetoed" in combined

    def test_generic_trash_error(self, runner, isolated_cli_db, monkeypatch):
        f = _setup(isolated_cli_db, "/err.txt")
        from curator.services.trash import TrashService, TrashError

        def _stub_raise(self, curator_id, *, reason, actor):
            raise TrashError("generic trash failure")

        monkeypatch.setattr(TrashService, "send_to_trash", _stub_raise)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "trash", str(f.curator_id), "--apply"],
        )
        assert result.exit_code == 1
        combined = result.stdout + (result.stderr or "")
        assert "generic trash failure" in combined


# ---------------------------------------------------------------------------
# restore
# ---------------------------------------------------------------------------


def _add_trash_record(repos, *, path: str = "/orig.txt",
                       restore_override: str | None = None) -> tuple[FileEntity, TrashRecord]:
    f = _setup(repos, path)
    rec = TrashRecord(
        curator_id=f.curator_id,
        original_source_id="local",
        original_path=path,
        file_hash="h",
        trashed_at=datetime(2026, 1, 1),
        trashed_by="cli.test",
        reason="test",
        bundle_memberships_snapshot=[],
        file_attrs_snapshot={},
        restore_path_override=restore_override,
    )
    repos["trash"].insert(rec)
    return f, rec


class TestRestoreCmd:
    def test_non_uuid_identifier_errors(self, runner, isolated_cli_db):
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "restore", "not-a-uuid"],
        )
        assert result.exit_code == 1
        combined = result.stdout + (result.stderr or "")
        assert "curator_id UUID" in combined

    def test_no_trash_record_errors(self, runner, isolated_cli_db):
        random_uuid = uuid4()
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "restore", str(random_uuid)],
        )
        assert result.exit_code == 1
        combined = result.stdout + (result.stderr or "")
        assert "No trash record" in combined

    def test_dry_run_default(self, runner, isolated_cli_db):
        f, _ = _add_trash_record(isolated_cli_db)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "restore", str(f.curator_id)],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "would restore" in combined
        assert "/orig.txt" in combined

    def test_dry_run_with_explicit_target(self, runner, isolated_cli_db):
        f, _ = _add_trash_record(isolated_cli_db)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "restore", str(f.curator_id), "--to", "/new/location.txt"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        # Path on Windows gets normalized to \new\location.txt
        assert "location.txt" in combined

    def test_dry_run_uses_restore_path_override(self, runner, isolated_cli_db):
        """Dry-run target falls back to restore_path_override when --to not given."""
        f, _ = _add_trash_record(isolated_cli_db,
                                  restore_override="/override.txt")
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "restore", str(f.curator_id)],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "/override.txt" in combined

    def test_apply_happy_path(self, runner, isolated_cli_db, monkeypatch):
        f, _ = _add_trash_record(isolated_cli_db, path="/restore_me.txt")
        from curator.services.trash import TrashService

        def _stub_restore(self, curator_id, *, target_path, actor):
            return FileEntity(
                curator_id=curator_id,
                source_id="local",
                source_path=target_path or "/restore_me.txt",
                size=10, mtime=utcnow_naive(),
            )

        monkeypatch.setattr(TrashService, "restore", _stub_restore)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "restore", str(f.curator_id), "--apply"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "Restored" in combined

    def test_apply_with_target_json(self, runner, isolated_cli_db, monkeypatch):
        f, _ = _add_trash_record(isolated_cli_db)
        from curator.services.trash import TrashService

        def _stub_restore(self, curator_id, *, target_path, actor):
            return FileEntity(
                curator_id=curator_id,
                source_id="local",
                source_path=target_path,
                size=10, mtime=utcnow_naive(),
            )

        monkeypatch.setattr(TrashService, "restore", _stub_restore)
        result = runner.invoke(
            app,
            ["--json", "--db", str(isolated_cli_db["db_path"]),
             "restore", str(f.curator_id), "--apply",
             "--to", "/the/destination.txt"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert '"restored"' in combined
        # Path may be normalized to \the\destination.txt on Windows
        assert 'destination.txt' in combined

    def test_not_in_trash_error(self, runner, isolated_cli_db, monkeypatch):
        f, _ = _add_trash_record(isolated_cli_db)
        from curator.services.trash import (
            TrashService, NotInTrashError,
        )

        def _stub_raise(self, curator_id, *, target_path, actor):
            raise NotInTrashError("not in trash")

        monkeypatch.setattr(TrashService, "restore", _stub_raise)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "restore", str(f.curator_id), "--apply"],
        )
        assert result.exit_code == 1
        combined = result.stdout + (result.stderr or "")
        assert "not in trash" in combined

    def test_restore_impossible_exits_2(self, runner, isolated_cli_db, monkeypatch):
        """RestoreImpossibleError uses exit code 2 (not 1) — 'not user error'."""
        f, _ = _add_trash_record(isolated_cli_db)
        from curator.services.trash import (
            TrashService, RestoreImpossibleError,
        )

        def _stub_raise(self, curator_id, *, target_path, actor):
            raise RestoreImpossibleError("os trash gone")

        monkeypatch.setattr(TrashService, "restore", _stub_raise)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "restore", str(f.curator_id), "--apply"],
        )
        assert result.exit_code == 2
        combined = result.stdout + (result.stderr or "")
        assert "os trash gone" in combined

    def test_generic_trash_error(self, runner, isolated_cli_db, monkeypatch):
        f, _ = _add_trash_record(isolated_cli_db)
        from curator.services.trash import TrashService, TrashError

        def _stub_raise(self, curator_id, *, target_path, actor):
            raise TrashError("generic restore failure")

        monkeypatch.setattr(TrashService, "restore", _stub_raise)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "restore", str(f.curator_id), "--apply"],
        )
        assert result.exit_code == 1
        combined = result.stdout + (result.stderr or "")
        assert "generic restore failure" in combined
