"""Tests for v1.7.39 GUI parity for v1.7.29 + v1.7.35.

Verifies that:
  1. SourceAddDialog has a share_visibility dropdown.
  2. The dropdown's three options match the CLI's --share-visibility set.
  3. Selecting 'public' and submitting writes a SourceConfig with
     share_visibility='public' to the DB.
  4. The default selection is 'private' (back-compat).
  5. TierDialog has a 'Keep metadata' checkbox.
  6. The checkbox is unchecked by default (preserves v1.7.29 auto-strip).
  7. The checkbox state is threaded through to migration.apply()'s
     no_autostrip kwarg.

Strategy:
  * Use pytest-qt's qtbot fixture for Qt event handling.
  * Build a real CuratorRuntime against a temp DB.
  * For SourceAddDialog: programmatically populate the dialog, set the
    dropdown, click OK, then verify the inserted SourceConfig.
  * For TierDialog: spy on migration.apply() to capture the kwargs.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Skip if PySide6 unavailable (CI environments without GUI deps)
pyside6 = pytest.importorskip("PySide6")


@pytest.fixture
def gui_runtime(tmp_path):
    """A real CuratorRuntime against a temp DB, with a 'local' source."""
    from curator.cli.runtime import build_runtime
    from curator.config import Config
    from curator.models.source import SourceConfig

    db_path = tmp_path / "v1739.db"
    rt = build_runtime(
        config=Config.load(),
        db_path_override=db_path,
        json_output=False, no_color=True, verbosity=0,
    )
    try:
        rt.source_repo.insert(SourceConfig(
            source_id="local", source_type="local", display_name="Local",
        ))
    except Exception:
        pass
    return rt


# ---------------------------------------------------------------------------
# SourceAddDialog: share_visibility dropdown
# ---------------------------------------------------------------------------


def test_source_add_dialog_has_share_visibility_dropdown(qtbot, gui_runtime):
    """v1.7.39: SourceAddDialog has a share_visibility QComboBox."""
    from curator.gui.dialogs import SourceAddDialog

    dlg = SourceAddDialog(gui_runtime)
    qtbot.addWidget(dlg)
    assert hasattr(dlg, "_cb_share_visibility"), (
        "SourceAddDialog should have _cb_share_visibility attribute"
    )


def test_source_add_dialog_share_visibility_options(qtbot, gui_runtime):
    """v1.7.39: dropdown has exactly 3 options matching the CLI set."""
    from curator.gui.dialogs import SourceAddDialog

    dlg = SourceAddDialog(gui_runtime)
    qtbot.addWidget(dlg)
    cb = dlg._cb_share_visibility
    items = [cb.itemData(i) for i in range(cb.count())]
    assert items == ["private", "team", "public"], (
        f"Expected ['private', 'team', 'public']; got {items}"
    )


def test_source_add_dialog_default_is_private(qtbot, gui_runtime):
    """v1.7.39: default selection is 'private' (back-compat)."""
    from curator.gui.dialogs import SourceAddDialog

    dlg = SourceAddDialog(gui_runtime)
    qtbot.addWidget(dlg)
    assert dlg._cb_share_visibility.currentData() == "private"


def _select_local_type(dlg) -> None:
    """Helper: pick the 'local' source type + fill its required 'roots' field.

    The dropdown defaults to the alphabetically-first registered type
    ('gdrive'), but 'local' is the simpler plugin for unit testing
    (only 'roots' is required, no auth needed).
    """
    idx = dlg._cb_source_type.findData("local")
    if idx >= 0:
        dlg._cb_source_type.setCurrentIndex(idx)
    # The 'roots' field is a QPlainTextEdit (array type)
    roots_widget = dlg._config_widgets.get("roots")
    if roots_widget is not None and hasattr(roots_widget, "setPlainText"):
        roots_widget.setPlainText("C:/dummy/path")


def test_source_add_dialog_writes_share_visibility_to_db(qtbot, gui_runtime):
    """v1.7.39: selecting 'public' writes share_visibility='public' on insert.

    This is the end-to-end behavior test: the dropdown value flows into
    SourceConfig, then to source_repo.insert(), then is readable back
    from source_repo.get().
    """
    from curator.gui.dialogs import SourceAddDialog

    dlg = SourceAddDialog(gui_runtime)
    qtbot.addWidget(dlg)

    # Populate required fields for the local plugin
    dlg._le_source_id.setText("test_public_src")
    _select_local_type(dlg)

    # Set share_visibility to 'public' (index 2)
    idx = dlg._cb_share_visibility.findData("public")
    assert idx >= 0, "Expected 'public' to be findable in dropdown"
    dlg._cb_share_visibility.setCurrentIndex(idx)

    # Trigger the submit
    dlg._on_ok_clicked()

    # If _on_ok_clicked exited early on validation, the status label
    # will tell us why. Surface that in the assertion message.
    status = dlg._lbl_status.text()

    # Verify the source was inserted with share_visibility='public'
    src = gui_runtime.source_repo.get("test_public_src")
    assert src is not None, (
        f"Source should have been inserted; status label: {status!r}"
    )
    assert src.share_visibility == "public", (
        f"Expected share_visibility='public'; got {src.share_visibility!r}"
    )


def test_source_add_dialog_private_default_writes_private(qtbot, gui_runtime):
    """v1.7.39: not changing the dropdown -> share_visibility='private' written.

    Verifies the back-compat path: a user who doesn't touch the new
    dropdown sees no behavior change.
    """
    from curator.gui.dialogs import SourceAddDialog

    dlg = SourceAddDialog(gui_runtime)
    qtbot.addWidget(dlg)

    dlg._le_source_id.setText("test_default_src")
    _select_local_type(dlg)
    # Don't touch the share_visibility dropdown -- default is 'private'
    dlg._on_ok_clicked()

    status = dlg._lbl_status.text()
    src = gui_runtime.source_repo.get("test_default_src")
    assert src is not None, (
        f"Source should have been inserted; status label: {status!r}"
    )
    assert src.share_visibility == "private", (
        f"Default should be 'private'; got {src.share_visibility!r}"
    )


# ---------------------------------------------------------------------------
# TierDialog: Keep metadata (--no-autostrip) checkbox
# ---------------------------------------------------------------------------


def test_tier_dialog_has_keep_metadata_checkbox(qtbot, gui_runtime):
    """v1.7.39: TierDialog has a Keep-metadata QCheckBox."""
    from curator.gui.dialogs import TierDialog

    dlg = TierDialog(gui_runtime)
    qtbot.addWidget(dlg)
    assert hasattr(dlg, "_cb_no_autostrip"), (
        "TierDialog should have _cb_no_autostrip attribute"
    )


def test_tier_dialog_keep_metadata_unchecked_by_default(qtbot, gui_runtime):
    """v1.7.39: Keep-metadata is OFF by default (preserves v1.7.29 strip default).

    The v1.7.29 default is to strip metadata on public-dst migrations.
    The GUI checkbox is the opt-OUT, so it must be UNCHECKED by default
    to preserve that behavior.
    """
    from curator.gui.dialogs import TierDialog

    dlg = TierDialog(gui_runtime)
    qtbot.addWidget(dlg)
    assert not dlg._cb_no_autostrip.isChecked(), (
        "Keep metadata should be unchecked by default (preserves "
        "v1.7.29 strip behavior). If checked by default, the GUI would "
        "silently disable privacy protection."
    )


def test_tier_dialog_keep_metadata_threads_to_apply(qtbot, gui_runtime, tmp_path):
    """v1.7.39: Keep-metadata checkbox state flows to migration.apply().

    Set the checkbox, trigger _action_bulk_migrate, and verify that
    migration.apply was called with no_autostrip=True. Uses patch to
    intercept the apply() call and capture its kwargs.
    """
    from curator.gui.dialogs import TierDialog
    from curator.models.file import FileEntity
    from datetime import datetime
    from uuid import uuid4

    dlg = TierDialog(gui_runtime)
    qtbot.addWidget(dlg)

    # Set up state for _action_bulk_migrate:
    dlg._le_root.setText(str(tmp_path))
    dlg._cb_no_autostrip.setChecked(True)  # Opt out of auto-strip

    # Mock file entity to migrate (must look like FileEntity for size attr)
    dummy = FileEntity(
        curator_id=uuid4(),
        source_id="local",
        source_path=str(tmp_path / "fake.txt"),
        size=42,
        mtime=datetime.now(),
        extension=".txt",
    )

    # Patch QFileDialog so it returns a non-empty path (skip the GUI picker)
    # Patch QMessageBox.question so it returns Yes (skip the confirm)
    # Patch migration.plan + migration.apply
    captured_kwargs = {}
    def fake_apply(plan, **kwargs):
        captured_kwargs.update(kwargs)
        # Return a minimal report shape
        report = MagicMock()
        report.moves = []
        return report

    fake_plan = MagicMock()
    fake_plan.moves = [MagicMock(curator_id=dummy.curator_id)]

    target_dir = str(tmp_path / "target")
    Path(target_dir).mkdir(exist_ok=True)

    # v1.7.39 test-fix: also patch QMessageBox.exec so the final
    # result-summary modal at the end of _action_bulk_migrate doesn't
    # block the test thread. Returns Ok immediately.
    from PySide6.QtWidgets import QFileDialog, QMessageBox
    with patch.object(QFileDialog, "getExistingDirectory", return_value=target_dir), \
         patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes), \
         patch.object(QMessageBox, "exec", return_value=QMessageBox.StandardButton.Ok), \
         patch.object(gui_runtime.migration, "plan", return_value=fake_plan), \
         patch.object(gui_runtime.migration, "apply", side_effect=fake_apply):
        dlg._action_bulk_migrate([dummy])

    assert "no_autostrip" in captured_kwargs, (
        f"migration.apply should have been called with no_autostrip kwarg; "
        f"got kwargs: {list(captured_kwargs.keys())}"
    )
    assert captured_kwargs["no_autostrip"] is True, (
        f"Checkbox was checked, so no_autostrip should be True; "
        f"got {captured_kwargs['no_autostrip']!r}"
    )


def test_tier_dialog_unchecked_threads_false_to_apply(qtbot, gui_runtime, tmp_path):
    """v1.7.39: unchecked checkbox threads no_autostrip=False (v1.7.29 default)."""
    from curator.gui.dialogs import TierDialog
    from curator.models.file import FileEntity
    from datetime import datetime
    from uuid import uuid4

    dlg = TierDialog(gui_runtime)
    qtbot.addWidget(dlg)

    dlg._le_root.setText(str(tmp_path))
    # Don't check the checkbox -- default is unchecked

    dummy = FileEntity(
        curator_id=uuid4(),
        source_id="local",
        source_path=str(tmp_path / "fake.txt"),
        size=42,
        mtime=datetime.now(),
        extension=".txt",
    )

    captured_kwargs = {}
    def fake_apply(plan, **kwargs):
        captured_kwargs.update(kwargs)
        report = MagicMock()
        report.moves = []
        return report

    fake_plan = MagicMock()
    fake_plan.moves = [MagicMock(curator_id=dummy.curator_id)]

    target_dir = str(tmp_path / "target")
    Path(target_dir).mkdir(exist_ok=True)

    # Same QMessageBox.exec patch as the checked variant test
    from PySide6.QtWidgets import QFileDialog, QMessageBox
    with patch.object(QFileDialog, "getExistingDirectory", return_value=target_dir), \
         patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes), \
         patch.object(QMessageBox, "exec", return_value=QMessageBox.StandardButton.Ok), \
         patch.object(gui_runtime.migration, "plan", return_value=fake_plan), \
         patch.object(gui_runtime.migration, "apply", side_effect=fake_apply):
        dlg._action_bulk_migrate([dummy])

    assert captured_kwargs.get("no_autostrip") is False, (
        f"Unchecked checkbox -> no_autostrip should be False; "
        f"got {captured_kwargs.get('no_autostrip')!r}"
    )
