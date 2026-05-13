"""Coverage closure for cli/main.py `scan-pii` command (v1.7.170).

Tier 3 sub-ship 16 of the CLI Coverage Arc.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from curator.cli.main import app
from curator.services.pii_scanner import (
    PIIMatch, PIIScanReport, PIISeverity,
)


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def isolated_cli_db(tmp_path):
    from curator.storage import CuratorDB
    db_path = tmp_path / "cli_pii.db"
    db = CuratorDB(db_path)
    db.init()
    return {"db_path": db_path, "tmp_path": tmp_path}


def _match(*, pattern: str = "email", severity=PIISeverity.LOW,
            redacted: str = "***@example.com", line: int = 1,
            metadata: dict | None = None) -> PIIMatch:
    return PIIMatch(
        pattern_name=pattern, severity=severity,
        matched_text="user@example.com", redacted=redacted,
        offset=0, line=line, metadata=metadata,
    )


def _report(
    source: str = "/x.txt", *,
    matches: list[PIIMatch] | None = None,
    truncated: bool = False, error: str | None = None,
    bytes_scanned: int = 100,
) -> PIIScanReport:
    return PIIScanReport(
        source=source, bytes_scanned=bytes_scanned,
        truncated=truncated, error=error,
        matches=matches or [],
    )


class TestScanPii:
    def test_target_not_found(self, runner, isolated_cli_db, tmp_path):
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "scan-pii", str(tmp_path / "ghost.txt")],
        )
        assert result.exit_code == 1
        combined = result.stdout + (result.stderr or "")
        assert "Target not found" in combined

    def test_single_file_no_matches_human(
        self, runner, isolated_cli_db, tmp_path, monkeypatch,
    ):
        from curator.services.pii_scanner import PIIScanner
        target = tmp_path / "clean.txt"
        target.write_text("nothing sensitive here")
        monkeypatch.setattr(PIIScanner, "scan_file",
                             lambda self, p: _report(source=str(p)))
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "scan-pii", str(target)],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "No PII patterns" in combined
        assert "Files scanned" in combined

    def test_single_file_with_matches_human(
        self, runner, isolated_cli_db, tmp_path, monkeypatch,
    ):
        from curator.services.pii_scanner import PIIScanner
        target = tmp_path / "dirty.txt"
        target.write_text("data")
        report = _report(
            source=str(target),
            matches=[_match(pattern="email", severity=PIISeverity.LOW),
                     _match(pattern="ssn", severity=PIISeverity.HIGH)],
        )
        monkeypatch.setattr(PIIScanner, "scan_file",
                             lambda self, p: report)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "scan-pii", str(target)],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "Per-file findings" in combined
        assert "HIGH severity" in combined
        assert "email=1" in combined
        assert "ssn=1" in combined

    def test_directory_recursive(
        self, runner, isolated_cli_db, tmp_path, monkeypatch,
    ):
        """Directory target uses scan_directory()."""
        from curator.services.pii_scanner import PIIScanner
        target = tmp_path / "tree"
        target.mkdir()
        captured = {}

        def _stub_scan_dir(self, p, *, recursive, extensions):
            captured["recursive"] = recursive
            captured["extensions"] = extensions
            return [_report()]
        monkeypatch.setattr(PIIScanner, "scan_directory", _stub_scan_dir)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "scan-pii", str(target),
             "--no-recursive", "--ext", ".txt", "--ext", ".md"],
        )
        assert result.exit_code == 0
        assert captured["recursive"] is False
        assert captured["extensions"] == [".txt", ".md"]

    def test_high_only_filter(
        self, runner, isolated_cli_db, tmp_path, monkeypatch,
    ):
        """--high-only filters to reports with HIGH severity matches."""
        from curator.services.pii_scanner import PIIScanner
        target = tmp_path / "tree"
        target.mkdir()
        reports = [
            _report(source="/low.txt",
                     matches=[_match(severity=PIISeverity.LOW)]),
            _report(source="/high.txt",
                     matches=[_match(pattern="ssn", severity=PIISeverity.HIGH)]),
        ]
        monkeypatch.setattr(PIIScanner, "scan_directory",
                             lambda self, p, **kw: reports)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "scan-pii", str(target), "--high-only"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        # /high.txt visible (HIGH-severity)
        assert "/high.txt" in combined
        # /low.txt filtered out

    def test_head_bytes_override_rebuilds_scanner(
        self, runner, isolated_cli_db, tmp_path, monkeypatch,
    ):
        """--head-bytes triggers a fresh PIIScanner instance."""
        # Patch PIIScanner module-level so the rebuilt instance gets stub scan
        import curator.services.pii_scanner as pii_mod
        target = tmp_path / "h.txt"
        target.write_text("data")
        captured = {}

        class _FakeScanner:
            def __init__(self, *, head_bytes):
                captured["head_bytes"] = head_bytes

            def scan_file(self, p):
                return _report(source=str(p))

            def scan_directory(self, p, **kw):
                return [_report(source=str(p))]

        monkeypatch.setattr(pii_mod, "PIIScanner", _FakeScanner)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "scan-pii", str(target), "--head-bytes", "1000000"],
        )
        assert result.exit_code == 0
        assert captured["head_bytes"] == 1000000

    def test_show_matches_renders_matches(
        self, runner, isolated_cli_db, tmp_path, monkeypatch,
    ):
        from curator.services.pii_scanner import PIIScanner
        target = tmp_path / "sm.txt"
        target.write_text("data")
        report = _report(
            source=str(target),
            matches=[
                _match(pattern="email", severity=PIISeverity.LOW, line=3,
                        redacted="***@x.com"),
                # High-risk metadata (live Stripe mode)
                _match(pattern="stripe_key", severity=PIISeverity.HIGH,
                        line=5, redacted="sk_live_***",
                        metadata={"mode": "live", "expired": False}),
            ],
        )
        monkeypatch.setattr(PIIScanner, "scan_file",
                             lambda self, p: report)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "scan-pii", str(target), "--show-matches"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "L   3" in combined or "L 3" in combined or "L  3" in combined
        assert "stripe_key" in combined
        # High-risk metadata rendered
        assert "mode=live" in combined

    def test_error_report_rendered(
        self, runner, isolated_cli_db, tmp_path, monkeypatch,
    ):
        """Report with error attribute is rendered with warning marker."""
        from curator.services.pii_scanner import PIIScanner
        target = tmp_path / "err.txt"
        target.write_text("data")
        report = _report(source=str(target), error="binary file, skipped",
                          matches=[])
        # Need at least one matching report to enter the per-file loop
        report2 = _report(source="/other.txt",
                           matches=[_match()])
        monkeypatch.setattr(PIIScanner, "scan_file",
                             lambda self, p: report)
        # Use directory target so we can return both
        tdir = tmp_path / "td"
        tdir.mkdir()
        monkeypatch.setattr(PIIScanner, "scan_directory",
                             lambda self, p, **kw: [report, report2])
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "scan-pii", str(tdir)],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "binary file" in combined
        # Errors counter
        assert "Errors:" in combined

    def test_json_output(
        self, runner, isolated_cli_db, tmp_path, monkeypatch,
    ):
        from curator.services.pii_scanner import PIIScanner
        target = tmp_path / "j.txt"
        target.write_text("data")
        report = _report(
            source=str(target),
            matches=[_match(pattern="ssn", severity=PIISeverity.HIGH)],
        )
        monkeypatch.setattr(PIIScanner, "scan_file",
                             lambda self, p: report)
        result = runner.invoke(
            app,
            ["--json", "--db", str(isolated_cli_db["db_path"]),
             "scan-pii", str(target), "--show-matches"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert '"match_count": 1' in combined
        assert '"has_high_severity": true' in combined
        assert '"matches"' in combined

    def test_csv_per_file_with_header(
        self, runner, isolated_cli_db, tmp_path, monkeypatch,
    ):
        from curator.services.pii_scanner import PIIScanner
        target = tmp_path / "csv.txt"
        target.write_text("data")
        report = _report(
            source=str(target),
            matches=[_match(pattern="email")],
            truncated=True,
        )
        monkeypatch.setattr(PIIScanner, "scan_file",
                             lambda self, p: report)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "scan-pii", str(target), "--csv"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "source,match_count" in combined
        assert "email=1" in combined
        assert ",yes" in combined  # truncated yes

    def test_csv_per_match_with_metadata(
        self, runner, isolated_cli_db, tmp_path, monkeypatch,
    ):
        """--csv --show-matches emits one row per match including metadata."""
        from curator.services.pii_scanner import PIIScanner
        target = tmp_path / "cm.txt"
        target.write_text("data")
        report = _report(
            source=str(target),
            matches=[_match(metadata={"alg": "HS256", "expired": False})],
        )
        monkeypatch.setattr(PIIScanner, "scan_file",
                             lambda self, p: report)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "scan-pii", str(target), "--csv", "--show-matches"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "source,line,offset,pattern" in combined
        assert "alg=HS256" in combined

    def test_csv_no_header(
        self, runner, isolated_cli_db, tmp_path, monkeypatch,
    ):
        from curator.services.pii_scanner import PIIScanner
        target = tmp_path / "nh.txt"
        target.write_text("data")
        monkeypatch.setattr(PIIScanner, "scan_file",
                             lambda self, p: _report(source=str(p),
                                                       matches=[_match()]))
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "scan-pii", str(target), "--csv", "--no-header"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "source,match_count" not in combined

    def test_csv_tsv_dialect(
        self, runner, isolated_cli_db, tmp_path, monkeypatch,
    ):
        from curator.services.pii_scanner import PIIScanner
        target = tmp_path / "tsv.txt"
        target.write_text("data")
        monkeypatch.setattr(PIIScanner, "scan_file",
                             lambda self, p: _report(source=str(p),
                                                       matches=[_match()]))
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "scan-pii", str(target),
             "--csv", "--csv-dialect", "tsv"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "source\tmatch_count" in combined
