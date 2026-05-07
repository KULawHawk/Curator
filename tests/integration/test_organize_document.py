"""Integration tests for `curator organize --type document` (Phase Gamma F4)."""

from __future__ import annotations

import json
import os
import zipfile
from datetime import datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from curator.cli.main import app
from curator.services.organize import OrganizeService
from curator.services.safety import SafetyService


pytestmark = pytest.mark.integration


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "curator_doc_cli.db"


def _isolated_safety_env(monkeypatch) -> None:
    real_init = OrganizeService.__init__
    def patched_init(self, file_repo, safety, *args, **kwargs):
        loose = SafetyService(app_data_paths=[], os_managed_paths=[])
        real_init(self, file_repo, loose, *args, **kwargs)
    monkeypatch.setattr(OrganizeService, "__init__", patched_init)


def _make_pdf(path: Path, *, creation_date: str) -> None:
    from pypdf import PdfWriter
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.add_metadata({"/CreationDate": creation_date})
    with open(path, "wb") as f:
        writer.write(f)


def _make_docx(path: Path, *, created: str) -> None:
    core_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<cp:coreProperties '
        'xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dcterms="http://purl.org/dc/terms/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        f'<dcterms:created xsi:type="dcterms:W3CDTF">{created}</dcterms:created>'
        '</cp:coreProperties>'
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("docProps/core.xml", core_xml)
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestOrganizeDocumentCli:
    def test_help_mentions_document(self, runner, db_path):
        result = runner.invoke(
            app, ["--db", str(db_path), "organize", "--help"],
        )
        assert "document" in result.stdout

    def test_unknown_type_lists_all_three(self, runner, db_path, tmp_path):
        result = runner.invoke(
            app,
            ["--db", str(db_path), "organize", "local",
             "--type", "video", "--target", str(tmp_path / "lib")],
        )
        assert result.exit_code != 0
        combined = (result.stdout or "") + (result.stderr or "")
        assert "music" in combined and "photo" in combined and "document" in combined

    def test_plan_with_pdf_and_docx(
        self, runner, db_path, tmp_path, monkeypatch
    ):
        _isolated_safety_env(monkeypatch)

        media = tmp_path / "docs_in"
        media.mkdir()
        _make_pdf(media / "Invoice.pdf", creation_date="D:20240315000000")
        _make_docx(media / "Report.docx", created="2023-06-15T00:00:00Z")
        # Plain text with date in filename:
        (media / "notes_2024-01-01.txt").write_text("hello")

        scan = runner.invoke(
            app, ["--db", str(db_path), "scan", "local", str(media)],
        )
        assert scan.exit_code == 0

        target = tmp_path / "library"
        result = runner.invoke(
            app,
            ["--json", "--db", str(db_path), "organize", "local",
             "--type", "document", "--target", str(target),
             "--root", str(media), "--show-files"],
        )
        assert result.exit_code == 0, result.stdout + (result.stderr or "")
        payload = json.loads(result.stdout)

        # All three should have proposed destinations.
        safe_files = payload["safe"].get("files", [])
        proposals_by_name = {
            Path(f["path"]).name: Path(f["proposed_destination"])
            for f in safe_files if f.get("proposed_destination")
        }
        assert "Invoice.pdf" in proposals_by_name
        assert "Report.docx" in proposals_by_name
        assert "notes_2024-01-01.txt" in proposals_by_name

        # Invoice.pdf -> 2024/2024-03/Invoice.pdf
        assert "2024" in proposals_by_name["Invoice.pdf"].parts
        assert "2024-03" in proposals_by_name["Invoice.pdf"].parts
        # Report.docx -> 2023/2023-06/Report.docx
        assert "2023" in proposals_by_name["Report.docx"].parts
        assert "2023-06" in proposals_by_name["Report.docx"].parts
        # notes_2024-01-01.txt -> 2024/2024-01/...
        assert "2024-01" in proposals_by_name["notes_2024-01-01.txt"].parts

    def test_apply_round_trip_for_documents(
        self, runner, db_path, tmp_path, monkeypatch
    ):
        _isolated_safety_env(monkeypatch)

        media = tmp_path / "in"
        media.mkdir()
        pdf = media / "April Report.pdf"
        _make_pdf(pdf, creation_date="D:20240415120000")
        runner.invoke(app, ["--db", str(db_path), "scan", "local", str(media)])

        target = tmp_path / "lib"
        result = runner.invoke(
            app,
            ["--json", "--db", str(db_path), "organize", "local",
             "--type", "document", "--target", str(target),
             "--apply", "--root", str(media)],
        )
        assert result.exit_code == 0, result.stdout
        payload = json.loads(result.stdout)
        assert payload["stage"]["mode"] == "apply"
        assert payload["stage"]["moved_count"] == 1

        final = target / "2024" / "2024-04" / "April Report.pdf"
        assert final.exists()
        assert not pdf.exists()

        revert = runner.invoke(
            app, ["--db", str(db_path), "organize-revert", str(target)],
        )
        assert revert.exit_code == 0
        assert pdf.exists()

    def test_text_file_with_no_date_uses_mtime(
        self, runner, db_path, tmp_path, monkeypatch
    ):
        _isolated_safety_env(monkeypatch)
        media = tmp_path / "in"
        media.mkdir()
        anon = media / "anonymous.txt"
        anon.write_text("hello")
        ts = datetime(2017, 11, 11).timestamp()
        os.utime(anon, (ts, ts))

        runner.invoke(app, ["--db", str(db_path), "scan", "local", str(media)])

        target = tmp_path / "lib"
        result = runner.invoke(
            app,
            ["--json", "--db", str(db_path), "organize", "local",
             "--type", "document", "--target", str(target),
             "--root", str(media), "--show-files"],
        )
        assert result.exit_code == 0, result.stdout
        payload = json.loads(result.stdout)
        proposed = next(
            (f["proposed_destination"]
             for f in payload["safe"].get("files", [])
             if f["path"].endswith("anonymous.txt")),
            None,
        )
        assert proposed is not None
        dest = Path(proposed)
        assert "2017" in dest.parts
        assert "2017-11" in dest.parts
