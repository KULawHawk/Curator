"""Tests for v0.36 FileInspectDialog (per-file inspect modal).

The dialog is read-only and constructed eagerly (queries DB at __init__
time), so most tests just instantiate it and assert the resulting
table contents. We never call .exec() (which would block on a modal
event loop); .show() is enough to verify rendering.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import patch
from uuid import uuid4

import pytest

pyside6 = pytest.importorskip("PySide6")  # noqa: F841

from PySide6.QtCore import QModelIndex
from PySide6.QtWidgets import QApplication

from curator.cli.runtime import build_runtime
from curator.config import Config
from curator.gui.dialogs import FileInspectDialog
from curator.gui.main_window import CuratorMainWindow
from curator.models.bundle import BundleEntity, BundleMembership
from curator.models.file import FileEntity
from curator.models.lineage import LineageEdge, LineageKind
from curator.models.source import SourceConfig


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def runtime_with_rich_data(tmp_path):
    """Real runtime + a file with: real metadata, a flex attr, lineage edges to two
    other files, and membership in two bundles."""
    db_path = tmp_path / "inspect_test.db"
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

    # Subject file: has real values for most fields.
    subject = FileEntity(
        curator_id=uuid4(), source_id="local",
        source_path="/Music/Pink Floyd/The Wall/06 - Comfortably Numb.mp3",
        size=5_900_000,
        mtime=datetime(2024, 6, 1, 14, 30),
        ctime=datetime(2024, 5, 30, 10, 0),
        inode=12345,
        xxhash3_128="a" * 32,
        md5="b" * 32,
        fuzzy_hash="3:fuzzy:hash",
        file_type="audio/mpeg",
        extension=".mp3",
        file_type_confidence=0.99,
    )
    # Flex attr.
    subject.flex["custom_tag"] = "test_value"
    rt.file_repo.upsert(subject)

    # Two related files for lineage edges.
    other_a = FileEntity(
        curator_id=uuid4(), source_id="local",
        source_path="/Music/.../05 - Mother.mp3", size=5_200_000,
        mtime=datetime(2024, 6, 1), extension=".mp3",
    )
    other_b = FileEntity(
        curator_id=uuid4(), source_id="local",
        source_path="/Backup/comfortably_numb.mp3", size=5_900_000,
        mtime=datetime(2024, 6, 1), extension=".mp3",
        xxhash3_128="a" * 32,  # identical to subject -> duplicate
    )
    rt.file_repo.upsert(other_a)
    rt.file_repo.upsert(other_b)

    # Lineage edges:
    # subject -> other_b: DUPLICATE (high confidence)
    rt.lineage_repo.insert(LineageEdge(
        from_curator_id=subject.curator_id,
        to_curator_id=other_b.curator_id,
        edge_kind=LineageKind.DUPLICATE,
        confidence=1.0,
        detected_by="curator.core.lineage_hash_dup",
        notes="exact xxhash3 match",
    ))
    # other_a -> subject: NEAR_DUPLICATE (moderate)
    rt.lineage_repo.insert(LineageEdge(
        from_curator_id=other_a.curator_id,
        to_curator_id=subject.curator_id,
        edge_kind=LineageKind.NEAR_DUPLICATE,
        confidence=0.78,
        detected_by="curator.core.lineage_fuzzy_dup",
        notes="ssdeep similarity 78",
    ))

    # Two bundles, subject in both.
    b1 = BundleEntity(name="The Wall album", bundle_type="manual", confidence=1.0)
    rt.bundle_repo.insert(b1)
    rt.bundle_repo.add_membership(BundleMembership(
        bundle_id=b1.bundle_id, curator_id=subject.curator_id, role="primary",
    ))

    b2 = BundleEntity(name="Pink Floyd discography", bundle_type="auto", confidence=0.92)
    rt.bundle_repo.insert(b2)
    rt.bundle_repo.add_membership(BundleMembership(
        bundle_id=b2.bundle_id, curator_id=subject.curator_id,
        role="member", confidence=0.92,
    ))

    yield rt, subject, [other_a, other_b], [b1, b2]


# ===========================================================================
# Construction
# ===========================================================================


class TestDialogConstruction:
    def test_dialog_constructs_without_crash(self, qapp, runtime_with_rich_data):
        rt, subject, _others, _bundles = runtime_with_rich_data
        dlg = FileInspectDialog(subject, rt)
        try:
            assert "Comfortably Numb" in dlg.windowTitle()
            assert dlg._tabs.count() == 3
        finally:
            dlg.deleteLater()

    def test_header_includes_path_and_size(self, qapp, runtime_with_rich_data):
        rt, subject, _, _ = runtime_with_rich_data
        dlg = FileInspectDialog(subject, rt)
        try:
            header_text = dlg._header_text()
            assert "Comfortably Numb" in header_text
            assert "5.6 MB" in header_text  # 5_900_000 bytes -> 5.6 MB
            assert "local" in header_text
        finally:
            dlg.deleteLater()

    def test_deleted_file_header_marks_deleted(self, qapp, runtime_with_rich_data):
        rt, subject, _, _ = runtime_with_rich_data
        # Mark deleted in DB and re-fetch.
        rt.file_repo.mark_deleted(subject.curator_id)
        deleted_subject = rt.file_repo.get(subject.curator_id)
        dlg = FileInspectDialog(deleted_subject, rt)
        try:
            assert "DELETED" in dlg._header_text()
        finally:
            dlg.deleteLater()


# ===========================================================================
# Metadata tab
# ===========================================================================


class TestMetadataTab:
    def test_metadata_includes_curator_id(self, qapp, runtime_with_rich_data):
        rt, subject, _, _ = runtime_with_rich_data
        dlg = FileInspectDialog(subject, rt)
        try:
            metadata_widget = dlg._tabs.widget(0)
            # It's the QTableWidget from _make_kv_table.
            from PySide6.QtWidgets import QTableWidget
            assert isinstance(metadata_widget, QTableWidget)
            # Find the Curator ID row.
            found = False
            for row in range(metadata_widget.rowCount()):
                if metadata_widget.item(row, 0).text() == "Curator ID":
                    assert metadata_widget.item(row, 1).text() == str(subject.curator_id)
                    found = True
                    break
            assert found
        finally:
            dlg.deleteLater()

    def test_metadata_shows_human_size(self, qapp, runtime_with_rich_data):
        rt, subject, _, _ = runtime_with_rich_data
        dlg = FileInspectDialog(subject, rt)
        try:
            tw = dlg._tabs.widget(0)
            for row in range(tw.rowCount()):
                if tw.item(row, 0).text() == "Size (human)":
                    assert "MB" in tw.item(row, 1).text()
                    return
            pytest.fail("Size (human) row missing")
        finally:
            dlg.deleteLater()

    def test_metadata_shows_flex_attrs(self, qapp, runtime_with_rich_data):
        rt, subject, _, _ = runtime_with_rich_data
        dlg = FileInspectDialog(subject, rt)
        try:
            tw = dlg._tabs.widget(0)
            flex_keys = [tw.item(r, 0).text() for r in range(tw.rowCount())
                         if tw.item(r, 0).text().startswith("flex:")]
            assert any("custom_tag" in k for k in flex_keys)
        finally:
            dlg.deleteLater()

    def test_metadata_shows_xxhash(self, qapp, runtime_with_rich_data):
        rt, subject, _, _ = runtime_with_rich_data
        dlg = FileInspectDialog(subject, rt)
        try:
            tw = dlg._tabs.widget(0)
            for row in range(tw.rowCount()):
                if tw.item(row, 0).text() == "xxhash3_128":
                    assert tw.item(row, 1).text() == "a" * 32
                    return
            pytest.fail("xxhash row missing")
        finally:
            dlg.deleteLater()


# ===========================================================================
# Lineage tab
# ===========================================================================


class TestLineageTab:
    def test_lineage_shows_both_edges(self, qapp, runtime_with_rich_data):
        rt, subject, _others, _ = runtime_with_rich_data
        dlg = FileInspectDialog(subject, rt)
        try:
            tw = dlg._tabs.widget(1)
            assert tw.rowCount() == 2
            # Collect kind / direction / other-path triples.
            triples = []
            for row in range(tw.rowCount()):
                triples.append((
                    tw.item(row, 0).text(),  # kind
                    tw.item(row, 1).text(),  # direction
                    tw.item(row, 2).text(),  # other path
                ))
            # One DUPLICATE outbound (subject -> other_b)
            assert any(t[0] == "duplicate" and t[1] == "->" and "Backup" in t[2]
                       for t in triples)
            # One NEAR_DUPLICATE inbound (other_a -> subject)
            assert any(t[0] == "near_duplicate" and t[1] == "<-" and "Mother" in t[2]
                       for t in triples)
        finally:
            dlg.deleteLater()

    def test_lineage_confidence_formatted(self, qapp, runtime_with_rich_data):
        rt, subject, _, _ = runtime_with_rich_data
        dlg = FileInspectDialog(subject, rt)
        try:
            tw = dlg._tabs.widget(1)
            confidences = [tw.item(r, 3).text() for r in range(tw.rowCount())]
            assert "1.00" in confidences
            assert "0.78" in confidences
        finally:
            dlg.deleteLater()

    def test_lineage_empty_when_no_edges(self, qapp, runtime_with_rich_data):
        rt, _subject, _others, _ = runtime_with_rich_data
        # Use one of the other files, which has no edges from itself
        # except being on the receiving end of subject's DUPLICATE edge
        # (lineage other_a -> subject, but other_a has no edges OUT).
        # Wait, other_a IS the source of an edge. Let me create a fresh
        # isolated file.
        from uuid import uuid4
        from datetime import datetime
        from curator.models.file import FileEntity
        isolated = FileEntity(
            curator_id=uuid4(), source_id="local",
            source_path="/isolated/file.txt", size=1,
            mtime=datetime(2024, 1, 1),
        )
        rt.file_repo.upsert(isolated)
        dlg = FileInspectDialog(isolated, rt)
        try:
            tw = dlg._tabs.widget(1)
            assert tw.rowCount() == 0
        finally:
            dlg.deleteLater()


# ===========================================================================
# Bundle Memberships tab
# ===========================================================================


class TestBundlesTab:
    def test_bundles_show_both_memberships(self, qapp, runtime_with_rich_data):
        rt, subject, _, _bundles = runtime_with_rich_data
        dlg = FileInspectDialog(subject, rt)
        try:
            tw = dlg._tabs.widget(2)
            assert tw.rowCount() == 2
            names = [tw.item(r, 0).text() for r in range(tw.rowCount())]
            assert "The Wall album" in names
            assert "Pink Floyd discography" in names
        finally:
            dlg.deleteLater()

    def test_bundles_role_column_correct(self, qapp, runtime_with_rich_data):
        rt, subject, _, _ = runtime_with_rich_data
        dlg = FileInspectDialog(subject, rt)
        try:
            tw = dlg._tabs.widget(2)
            # Find the row where Bundle == "The Wall album"; role should be "primary".
            for row in range(tw.rowCount()):
                if tw.item(row, 0).text() == "The Wall album":
                    assert tw.item(row, 2).text() == "primary"
                    return
            pytest.fail("The Wall album row missing")
        finally:
            dlg.deleteLater()


# ===========================================================================
# Wiring (double-click + context menu Inspect both open the dialog)
# ===========================================================================


class TestInspectWiring:
    def test_double_click_opens_inspect_dialog(self, qapp, runtime_with_rich_data):
        rt, subject, _, _ = runtime_with_rich_data
        window = CuratorMainWindow(rt)
        try:
            # Patch _open_inspect_dialog to capture the call (avoid modal exec).
            captured = []
            window._open_inspect_dialog = lambda f: captured.append(f)
            # Force a refresh so the new files are in the model.
            window._files_model.refresh()
            # Find the index for the subject row.
            subject_row = None
            for r in range(window._files_model.rowCount()):
                if window._files_model.file_at(r).curator_id == subject.curator_id:
                    subject_row = r
                    break
            assert subject_row is not None
            # Trigger double-click slot directly (avoids QTest dependencies).
            idx = window._files_model.index(subject_row, 0)
            window._slot_inspect_at_index(idx)
            # Captured the right file.
            assert len(captured) == 1
            assert captured[0].curator_id == subject.curator_id
        finally:
            window.deleteLater()

    def test_inspect_with_invalid_index_no_op(self, qapp, runtime_with_rich_data):
        rt, _subject, _, _ = runtime_with_rich_data
        window = CuratorMainWindow(rt)
        try:
            captured = []
            window._open_inspect_dialog = lambda f: captured.append(f)
            window._slot_inspect_at_index(QModelIndex())  # invalid
            assert captured == []
        finally:
            window.deleteLater()
