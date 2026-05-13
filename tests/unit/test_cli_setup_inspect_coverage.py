"""Coverage closure for `cli/main.py` top-level setup + helpers + `inspect` (v1.7.155).

First Tier 3 sub-ship of the CLI Coverage Arc.

Targets:
- Lines 119-120: `_version_callback`
- Lines 220: `_emit_json` (incidentally covered via `--json` tests)
- Lines 225-226: `_err_exit` (incidentally covered via no-match test)
- Lines 251-255: `_check_csv_dialect` invalid-dialect raise
- Lines 263-338: `inspect` command (UUID/path/no-match × JSON/human × full
  feature surface: flex attrs, lineage edges both directions, bundles)

Lines 187-215 (first `_resolve_file` definition) are now annotated
`# pragma: no cover` per v1.7.155 — dead code shadowed by the second
definition at line 3711+. See release notes.
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

import pytest
from typer.testing import CliRunner

from curator._compat.datetime import utcnow_naive
from curator.cli.main import _check_csv_dialect, app
from curator.cli.runtime import CuratorRuntime
from curator.models import FileEntity, LineageEdge, LineageKind, SourceConfig


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def isolated_cli_db(tmp_path):
    """A real CuratorDB at tmp_path, returned with all repositories ready."""
    from curator.storage import CuratorDB
    from curator.storage.repositories import (
        FileRepository, LineageRepository, BundleRepository, SourceRepository,
    )
    db_path = tmp_path / "cli_inspect.db"
    db = CuratorDB(db_path)
    db.init()
    return {
        "db": db,
        "db_path": db_path,
        "files": FileRepository(db),
        "sources": SourceRepository(db),
        "lineage": LineageRepository(db),
        "bundles": BundleRepository(db),
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
# _version_callback (lines 119-120)
# ---------------------------------------------------------------------------


class TestVersionFlag:
    def test_version_flag_prints_and_exits(self, runner):
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "curator" in combined.lower()


# ---------------------------------------------------------------------------
# _check_csv_dialect (lines 251-255)
# ---------------------------------------------------------------------------


class TestCheckCsvDialect:
    def test_invalid_dialect_raises(self):
        """Line 251-255: invalid dialect raises typer.Exit via _err_exit."""
        # Build a minimal runtime stub (only needs no_color attr for the
        # err console)
        from types import SimpleNamespace
        from curator.cli.main import _err_exit
        rt = SimpleNamespace(no_color=True)
        import typer
        with pytest.raises(typer.Exit):
            _check_csv_dialect(rt, "xyz")

    def test_csv_dialect_accepted(self):
        """csv and tsv don't raise."""
        from types import SimpleNamespace
        rt = SimpleNamespace(no_color=True)
        _check_csv_dialect(rt, "csv")
        _check_csv_dialect(rt, "tsv")


# ---------------------------------------------------------------------------
# inspect command (lines 263-338) + incidental _emit_json + _err_exit
# ---------------------------------------------------------------------------


class TestInspect:
    def test_inspect_no_match_returns_error(self, runner, isolated_cli_db):
        """Line 268-271 + _err_exit: when _resolve_file returns None."""
        # Empty DB; inspect a non-existent identifier
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "inspect",
             "no-such-file.txt"],
        )
        assert result.exit_code == 1
        combined = result.stdout + (result.stderr or "")
        assert "No file matches" in combined

    def test_inspect_by_uuid_json_output(self, runner, isolated_cli_db):
        """JSON output path (lines 277-303)."""
        repos = isolated_cli_db
        repos["sources"].insert(SourceConfig(
            source_id="local", source_type="local", display_name="Local",
        ))
        f = _add_file(repos, "local", "/x.txt", xxhash3_128="hash_xx",
                     md5="md5_x", fuzzy_hash="fz_x", extension=".txt",
                     file_type="text/plain", file_type_confidence=0.95)

        result = runner.invoke(
            app,
            ["--json", "--db", str(isolated_cli_db["db_path"]),
             "inspect", str(f.curator_id)],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert '"curator_id"' in combined
        assert '"source_id": "local"' in combined
        assert '"source_path": "/x.txt"' in combined
        assert '"size": 10' in combined
        assert '"xxhash3_128": "hash_xx"' in combined
        assert '"md5": "md5_x"' in combined
        assert '"fuzzy_hash": "fz_x"' in combined

    def test_inspect_by_path_human_output(self, runner, isolated_cli_db):
        """Path-based resolution + human output (lines 306-338)."""
        repos = isolated_cli_db
        repos["sources"].insert(SourceConfig(
            source_id="local", source_type="local", display_name="Local",
        ))
        f = _add_file(repos, "local", "/foo/bar.pdf", xxhash3_128="hx",
                     md5="hm", fuzzy_hash="hf", extension=".pdf",
                     file_type="application/pdf",
                     file_type_confidence=0.99)

        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "inspect", "/foo/bar.pdf"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        # Source path is the title
        assert "/foo/bar.pdf" in combined
        # Field labels
        assert "curator_id" in combined
        assert "source" in combined and "local" in combined
        assert "size" in combined and "10" in combined
        # Hash values present
        assert "hx" in combined
        assert "hm" in combined
        assert "hf" in combined

    def test_inspect_with_deleted_at_and_flex_attrs(
        self, runner, isolated_cli_db,
    ):
        """Human output exercises optional sections: deleted_at, flex attrs."""
        repos = isolated_cli_db
        repos["sources"].insert(SourceConfig(
            source_id="local", source_type="local", display_name="Local",
        ))
        f = _add_file(repos, "local", "/deleted.txt",
                     deleted_at=datetime(2026, 1, 1))
        f.set_flex("category", "test")
        f.set_flex("priority", 5)
        repos["files"].update(f)

        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "inspect", str(f.curator_id)],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "deleted_at" in combined
        assert "flex" in combined
        assert "category" in combined
        assert "'test'" in combined  # repr-style
        assert "priority" in combined

    def test_inspect_with_lineage_edges_both_directions(
        self, runner, isolated_cli_db,
    ):
        """Human output exercises lineage rendering with from + to directions."""
        repos = isolated_cli_db
        repos["sources"].insert(SourceConfig(
            source_id="local", source_type="local", display_name="Local",
        ))
        target = _add_file(repos, "local", "/target.txt")
        related_a = _add_file(repos, "local", "/related_a.txt")
        related_b = _add_file(repos, "local", "/related_b.txt")

        # Edge FROM target TO related_a
        repos["lineage"].insert(LineageEdge(
            from_curator_id=target.curator_id,
            to_curator_id=related_a.curator_id,
            edge_kind=LineageKind.DUPLICATE,
            confidence=1.0,
            detected_by="test",
        ))
        # Edge FROM related_b TO target (reverse direction)
        repos["lineage"].insert(LineageEdge(
            from_curator_id=related_b.curator_id,
            to_curator_id=target.curator_id,
            edge_kind=LineageKind.NEAR_DUPLICATE,
            confidence=0.85,
            detected_by="test",
        ))

        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "inspect", str(target.curator_id)],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        # Both edge kinds rendered
        assert "duplicate" in combined
        assert "near_duplicate" in combined
        assert "lineage edges" in combined
        assert "(2)" in combined  # edge count

    def test_inspect_with_bundle_memberships(self, runner, isolated_cli_db):
        """Human output exercises bundle rendering."""
        from curator.models import BundleEntity, BundleMembership
        repos = isolated_cli_db
        repos["sources"].insert(SourceConfig(
            source_id="local", source_type="local", display_name="Local",
        ))
        f = _add_file(repos, "local", "/in_bundle.txt")
        b = BundleEntity(name="test-bundle", bundle_type="manual")
        repos["bundles"].insert(b)
        repos["bundles"].add_membership(BundleMembership(
            bundle_id=b.bundle_id,
            curator_id=f.curator_id,
            role="member",
        ))

        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "inspect", str(f.curator_id)],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "bundles" in combined
        assert "test-bundle" in combined
