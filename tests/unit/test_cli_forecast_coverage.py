"""Coverage closure for cli/main.py `forecast` command (v1.7.168).

Tier 3 sub-ship 14 of the CLI Coverage Arc.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from typer.testing import CliRunner

from curator.cli.main import app
from curator.services.forecast import DiskForecast, MonthlyBucket


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def isolated_cli_db(tmp_path):
    from curator.storage import CuratorDB
    db_path = tmp_path / "cli_forecast.db"
    db = CuratorDB(db_path)
    db.init()
    return {"db_path": db_path}


def _forecast(
    *, drive: str = "C:\\", status: str = "fit_ok",
    used_gb: float = 500.0, total_gb: float = 1000.0,
    slope: float | None = 0.5, r2: float | None = 0.95,
    days_95: int | None = 90, days_99: int | None = 180,
    eta_95: datetime | None = datetime(2026, 6, 1),
    eta_99: datetime | None = datetime(2026, 9, 1),
    history: list[MonthlyBucket] | None = None,
) -> DiskForecast:
    return DiskForecast(
        drive_path=drive,
        current_used_gb=used_gb,
        current_total_gb=total_gb,
        current_pct=(used_gb / total_gb) * 100,
        current_free_gb=total_gb - used_gb,
        slope_gb_per_day=slope,
        fit_r_squared=r2,
        days_to_95pct=days_95,
        days_to_99pct=days_99,
        eta_95pct=eta_95,
        eta_99pct=eta_99,
        status=status,
        status_message=f"forecast {status}",
        monthly_history=history or [],
    )


class TestForecast:
    def test_no_drives_found(self, runner, isolated_cli_db, monkeypatch):
        """No-drives case: empty list -> message."""
        from curator.services.forecast import ForecastService
        monkeypatch.setattr(ForecastService, "compute_all_drives",
                             lambda self: [])
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "forecast"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "No fixed drives" in combined

    def test_single_drive_argument(
        self, runner, isolated_cli_db, monkeypatch,
    ):
        """Drive argument triggers compute_disk_forecast for that drive."""
        from curator.services.forecast import ForecastService
        captured = {}

        def _stub(self, drive):
            captured["drive"] = drive
            return _forecast(drive=drive)

        monkeypatch.setattr(ForecastService, "compute_disk_forecast", _stub)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "forecast", "D:\\"],
        )
        assert result.exit_code == 0
        assert captured["drive"] == "D:\\"

    def test_human_output_all_status_colors(
        self, runner, isolated_cli_db, monkeypatch,
    ):
        """All 5 status color branches: fit_ok, past_95pct, past_99pct,
        insufficient_data, no_growth."""
        from curator.services.forecast import ForecastService
        forecasts = [
            _forecast(drive="C:\\", status="fit_ok"),
            _forecast(drive="D:\\", status="past_95pct"),
            _forecast(drive="E:\\", status="past_99pct"),
            _forecast(drive="F:\\", status="insufficient_data",
                      slope=None, r2=None),
            _forecast(drive="G:\\", status="no_growth"),
        ]
        monkeypatch.setattr(ForecastService, "compute_all_drives",
                             lambda self: forecasts)
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "forecast"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        # All 5 drive letters rendered (trailing backslash gets eaten by
        # Rich tag formatting in output capture; assert on the letter)
        for d in ("C:", "D:", "E:", "F:", "G:"):
            assert d in combined

    def test_human_output_with_history(
        self, runner, isolated_cli_db, monkeypatch,
    ):
        """History section renders monthly buckets."""
        from curator.services.forecast import ForecastService
        history = [
            MonthlyBucket(month=f"2026-0{i+1}", file_count=100*i,
                          bytes_added=1024**3 * i)
            for i in range(1, 9)  # 8 months — will show last 6
        ]
        f = _forecast(history=history)
        monkeypatch.setattr(ForecastService, "compute_all_drives",
                             lambda self: [f])
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "forecast"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "History" in combined
        # Last bucket rendered
        assert "2026-08" in combined

    def test_human_output_without_slope_skips_rate(
        self, runner, isolated_cli_db, monkeypatch,
    ):
        """slope=None -> skip Rate line."""
        from curator.services.forecast import ForecastService
        f = _forecast(slope=None, r2=None)
        monkeypatch.setattr(ForecastService, "compute_all_drives",
                             lambda self: [f])
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]), "forecast"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        # Has Used/Free, but no Rate
        assert "Used:" in combined
        assert "Rate:" not in combined

    def test_json_output(self, runner, isolated_cli_db, monkeypatch):
        """JSON path renders all forecast fields."""
        from curator.services.forecast import ForecastService
        monkeypatch.setattr(ForecastService, "compute_all_drives",
                             lambda self: [_forecast()])
        result = runner.invoke(
            app,
            ["--json", "--db", str(isolated_cli_db["db_path"]), "forecast"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert '"drive_path"' in combined
        assert '"status": "fit_ok"' in combined
        assert '"slope_gb_per_day": 0.5' in combined

    def test_json_with_none_slope(self, runner, isolated_cli_db, monkeypatch):
        """JSON: slope is None -> emit null."""
        from curator.services.forecast import ForecastService
        monkeypatch.setattr(
            ForecastService, "compute_all_drives",
            lambda self: [_forecast(slope=None, r2=None, eta_95=None, eta_99=None)],
        )
        result = runner.invoke(
            app,
            ["--json", "--db", str(isolated_cli_db["db_path"]), "forecast"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert '"slope_gb_per_day": null' in combined
        assert '"eta_95pct": null' in combined

    def test_csv_with_header(self, runner, isolated_cli_db, monkeypatch):
        from curator.services.forecast import ForecastService
        monkeypatch.setattr(ForecastService, "compute_all_drives",
                             lambda self: [_forecast()])
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "forecast", "--csv"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "drive_path,current_used_gb" in combined

    def test_csv_no_header(self, runner, isolated_cli_db, monkeypatch):
        from curator.services.forecast import ForecastService
        monkeypatch.setattr(ForecastService, "compute_all_drives",
                             lambda self: [_forecast()])
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "forecast", "--csv", "--no-header"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "drive_path,current_used_gb" not in combined
        # But the data row is there
        assert "fit_ok" in combined

    def test_csv_tsv_dialect(self, runner, isolated_cli_db, monkeypatch):
        from curator.services.forecast import ForecastService
        monkeypatch.setattr(ForecastService, "compute_all_drives",
                             lambda self: [_forecast()])
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "forecast", "--csv", "--csv-dialect", "tsv"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert "drive_path\tcurrent_used_gb" in combined

    def test_csv_with_none_values(self, runner, isolated_cli_db, monkeypatch):
        """CSV: slope/r2/days/eta all None -> empty strings."""
        from curator.services.forecast import ForecastService
        f = _forecast(
            slope=None, r2=None,
            days_95=None, days_99=None,
            eta_95=None, eta_99=None,
        )
        monkeypatch.setattr(ForecastService, "compute_all_drives",
                             lambda self: [f])
        result = runner.invoke(
            app,
            ["--db", str(isolated_cli_db["db_path"]),
             "forecast", "--csv"],
        )
        assert result.exit_code == 0
