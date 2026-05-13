"""Coverage closure for cli/main.py `export-clean` command (v1.7.171).

Tier 3 sub-ship 17 of the CLI Coverage Arc.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from curator.cli.main import app
from curator.services.metadata_stripper import (
    StripOutcome, StripReport, StripResult,
)


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def isolated_cli_db(tmp_path):
    from curator.storage import CuratorDB
    db_path = tmp_path / "cli_export.db"
    db = CuratorDB(db_path)
    db.init()
    return {"db_path": db_path, "tmp_path": tmp_path}


def _result(*, source: str, outcome: StripOutcome,
             destination: str | None = None,
             bytes_in: int = 100, bytes_out: int = 90,
             fields: list[str] | None = None,
             error: str | None = None) -> StripResult:
    return StripResult(
        source=source, destination=destination or source.replace("src", "dst"),
        outcome=outcome,
        bytes_in=bytes_in, bytes_out=bytes_out,
        metadata_fields_removed=fields or [],
        error=error,
    )


def _report(results: list[StripResult] | None = None) -> StripReport:
    return StripReport(
        started_at=datetime(2026, 1, 1, 12, 0, 0),
        completed_at=datetime(2026, 1, 1, 12, 0, 5),
        results=results or [],
    )


class TestExportClean:
    def test_source_not_found(self, runner, isolated_cli_db, tmp_path):
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "export-clean",
             str(tmp_path / "ghost.txt"), str(tmp_path / "dst.txt")],
        )
        assert result.exit_code == 1
        combined = result.stdout + (result.stderr or "")
        assert "Source not found" in combined

    def test_single_file_happy_path(
        self, runner, isolated_cli_db, tmp_path, monkeypatch,
    ):
        from curator.services.metadata_stripper import MetadataStripper
        src = tmp_path / "a.jpg"
        src.write_bytes(b"fake image")
        dst = tmp_path / "out" / "a.jpg"
        captured = {}

        def _stub_strip(self, s, d):
            captured["src"] = s
            captured["dst"] = d
            return _result(source=str(s), outcome=StripOutcome.STRIPPED,
                            destination=str(d),
                            fields=["EXIF", "GPS"])

        monkeypatch.setattr(MetadataStripper, "strip_file", _stub_strip)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "export-clean",
             str(src), str(dst)],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "Metadata export-clean" in combined
        assert "Stripped" in combined
        assert dst.parent.exists()

    def test_directory_recursive_with_extensions(
        self, runner, isolated_cli_db, tmp_path, monkeypatch,
    ):
        from curator.services.metadata_stripper import MetadataStripper
        src = tmp_path / "tree"
        src.mkdir()
        dst = tmp_path / "out"
        captured = {}

        def _stub_strip_dir(self, s, d, *, recursive, extensions):
            captured["recursive"] = recursive
            captured["extensions"] = extensions
            return _report([
                _result(source="/a.jpg", outcome=StripOutcome.STRIPPED),
                _result(source="/b.txt", outcome=StripOutcome.PASSTHROUGH),
                _result(source="/c.tmp", outcome=StripOutcome.SKIPPED),
            ])

        monkeypatch.setattr(MetadataStripper, "strip_directory", _stub_strip_dir)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "export-clean",
             str(src), str(dst), "--no-recursive",
             "--ext", ".jpg", "--ext", ".png"],
        )
        assert result.exit_code == 0
        assert captured["recursive"] is False
        assert captured["extensions"] == [".jpg", ".png"]
        combined = result.stdout + (result.stderr or "")
        assert "Stripped" in combined
        assert "Passthrough" in combined
        assert "Skipped" in combined

    def test_failures_render_with_exit_1(
        self, runner, isolated_cli_db, tmp_path, monkeypatch,
    ):
        from curator.services.metadata_stripper import MetadataStripper
        src = tmp_path / "tree"
        src.mkdir()
        dst = tmp_path / "out"
        report = _report([
            _result(source="/a.jpg", outcome=StripOutcome.STRIPPED),
            _result(source="/bad.jpg", outcome=StripOutcome.FAILED,
                     error="permission denied"),
        ])
        monkeypatch.setattr(MetadataStripper, "strip_directory",
                             lambda self, *a, **kw: report)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "export-clean",
             str(src), str(dst)],
        )
        assert result.exit_code == 1
        combined = result.stdout + (result.stderr or "")
        assert "Failures" in combined
        assert "permission denied" in combined

    def test_failures_more_than_20_caps(
        self, runner, isolated_cli_db, tmp_path, monkeypatch,
    ):
        from curator.services.metadata_stripper import MetadataStripper
        src = tmp_path / "tree"
        src.mkdir()
        dst = tmp_path / "out"
        # 25 failures -> cap at 20 + "and 5 more"
        results = [
            _result(source=f"/fail_{i}.jpg",
                     outcome=StripOutcome.FAILED,
                     error=f"err_{i}")
            for i in range(25)
        ]
        monkeypatch.setattr(MetadataStripper, "strip_directory",
                             lambda self, *a, **kw: _report(results))
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "export-clean",
             str(src), str(dst)],
        )
        assert result.exit_code == 1
        combined = result.stdout + (result.stderr or "")
        assert "and 5 more" in combined

    def test_show_files_renders_per_file_outcomes(
        self, runner, isolated_cli_db, tmp_path, monkeypatch,
    ):
        """--show-files renders per-file outcomes (excluding failures)."""
        from curator.services.metadata_stripper import MetadataStripper
        src = tmp_path / "tree"
        src.mkdir()
        dst = tmp_path / "out"
        report = _report([
            _result(source="/a.jpg", outcome=StripOutcome.STRIPPED,
                     fields=["EXIF"]),
            _result(source="/b.txt", outcome=StripOutcome.PASSTHROUGH),
        ])
        monkeypatch.setattr(MetadataStripper, "strip_directory",
                             lambda self, *a, **kw: report)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "export-clean",
             str(src), str(dst), "--show-files"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "Per-file outcomes" in combined
        assert "stripped" in combined
        assert "passthrough" in combined
        assert "EXIF" in combined  # fields rendered

    def test_drop_icc_rebuilds_stripper(
        self, runner, isolated_cli_db, tmp_path, monkeypatch,
    ):
        """--drop-icc constructs a fresh MetadataStripper(keep_icc_profile=False)."""
        import curator.services.metadata_stripper as ms_mod
        src = tmp_path / "icc.jpg"
        src.write_bytes(b"data")
        dst = tmp_path / "out" / "icc.jpg"
        captured = {}

        class _FakeStripper:
            def __init__(self, *, keep_icc_profile):
                captured["keep_icc_profile"] = keep_icc_profile

            def strip_file(self, s, d):
                return _result(source=str(s), destination=str(d),
                                outcome=StripOutcome.STRIPPED)

            def strip_directory(self, *a, **kw):
                return _report([_result(source="/x", outcome=StripOutcome.STRIPPED)])

        monkeypatch.setattr(ms_mod, "MetadataStripper", _FakeStripper)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "export-clean",
             str(src), str(dst), "--drop-icc"],
        )
        assert result.exit_code == 0
        assert captured["keep_icc_profile"] is False

    def test_json_output(
        self, runner, isolated_cli_db, tmp_path, monkeypatch,
    ):
        from curator.services.metadata_stripper import MetadataStripper
        src = tmp_path / "j.jpg"
        src.write_bytes(b"data")
        dst = tmp_path / "out" / "j.jpg"
        monkeypatch.setattr(
            MetadataStripper, "strip_file",
            lambda self, s, d: _result(
                source=str(s), destination=str(d),
                outcome=StripOutcome.STRIPPED, fields=["EXIF"],
            ),
        )
        result = runner.invoke(
            app,
            ["--json", "--db", str(isolated_cli_db["db_path"]),
             "export-clean", str(src), str(dst)],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert '"stripped_count": 1' in combined
        assert '"metadata_fields_removed"' in combined

    def test_csv_with_header(
        self, runner, isolated_cli_db, tmp_path, monkeypatch,
    ):
        from curator.services.metadata_stripper import MetadataStripper
        src = tmp_path / "tree"
        src.mkdir()
        dst = tmp_path / "out"
        monkeypatch.setattr(
            MetadataStripper, "strip_directory",
            lambda self, *a, **kw: _report([
                _result(source="/a.jpg", outcome=StripOutcome.STRIPPED,
                         fields=["EXIF", "XMP"]),
            ]),
        )
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "export-clean",
             str(src), str(dst), "--csv"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "source,destination,outcome" in combined
        # Fields pipe-delimited
        assert "EXIF|XMP" in combined

    def test_csv_with_failures_exits_1(
        self, runner, isolated_cli_db, tmp_path, monkeypatch,
    ):
        from curator.services.metadata_stripper import MetadataStripper
        src = tmp_path / "tree"
        src.mkdir()
        dst = tmp_path / "out"
        monkeypatch.setattr(
            MetadataStripper, "strip_directory",
            lambda self, *a, **kw: _report([
                _result(source="/bad", outcome=StripOutcome.FAILED,
                         error="boom"),
            ]),
        )
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "export-clean",
             str(src), str(dst), "--csv"],
        )
        assert result.exit_code == 1

    def test_csv_no_header(
        self, runner, isolated_cli_db, tmp_path, monkeypatch,
    ):
        from curator.services.metadata_stripper import MetadataStripper
        src = tmp_path / "tree"
        src.mkdir()
        dst = tmp_path / "out"
        monkeypatch.setattr(
            MetadataStripper, "strip_directory",
            lambda self, *a, **kw: _report([
                _result(source="/a", outcome=StripOutcome.STRIPPED),
            ]),
        )
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "export-clean",
             str(src), str(dst), "--csv", "--no-header"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "source,destination,outcome" not in combined

    def test_csv_tsv_dialect(
        self, runner, isolated_cli_db, tmp_path, monkeypatch,
    ):
        from curator.services.metadata_stripper import MetadataStripper
        src = tmp_path / "tree"
        src.mkdir()
        dst = tmp_path / "out"
        monkeypatch.setattr(
            MetadataStripper, "strip_directory",
            lambda self, *a, **kw: _report([
                _result(source="/a", outcome=StripOutcome.STRIPPED),
            ]),
        )
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "export-clean",
             str(src), str(dst), "--csv", "--csv-dialect", "tsv"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "source\tdestination" in combined
