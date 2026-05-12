"""Tests for v1.7.40 GUI source-edit dialog (SourceAddDialog edit mode).

Verifies that SourceAddDialog supports both creation and editing of
SourceConfig records via an ``editing_source`` constructor parameter.

In edit mode:
  * source_id is read-only, source_type is disabled (immutable identity)
  * All other fields prefill from the existing source
  * share_visibility prefills from the existing source's value
  * Plugin config widgets prefill from existing source.config
  * Submitting calls source_repo.update() (not insert())
  * created_at is preserved (not reset to datetime.now())
  * The window title shows the source_id being edited

In add mode (back-compat):
  * Everything still works as before v1.7.40

Strategy:
  * Build a real CuratorRuntime against a temp DB
  * Insert a test source via source_repo.insert()
  * Open SourceAddDialog with editing_source=existing
  * Manipulate widgets, submit, verify DB state
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

pyside6 = pytest.importorskip("PySide6")


@pytest.fixture
def gui_runtime(tmp_path):
    """A real CuratorRuntime against a temp DB."""
    from curator.cli.runtime import build_runtime
    from curator.config import Config

    db_path = tmp_path / "v1740.db"
    rt = build_runtime(
        config=Config.load(),
        db_path_override=db_path,
        json_output=False, no_color=True, verbosity=0,
    )
    return rt


@pytest.fixture
def seeded_source(gui_runtime):
    """A SourceConfig already inserted in the test DB, ready to be edited."""
    from curator.models.source import SourceConfig
    src = SourceConfig(
        source_id="my_archive",
        source_type="local",
        display_name="My Archive",
        config={"roots": ["C:/old/path"]},
        enabled=True,
        created_at=datetime(2025, 1, 15, 10, 30, 0),
        share_visibility="team",
    )
    gui_runtime.source_repo.insert(src)
    return src


# ---------------------------------------------------------------------------
# Edit mode: detection + window title
# ---------------------------------------------------------------------------


def test_edit_mode_flag_set(qtbot, gui_runtime, seeded_source):
    """v1.7.40: dialog with editing_source has is_edit_mode=True."""
    from curator.gui.dialogs import SourceAddDialog

    existing = gui_runtime.source_repo.get(seeded_source.source_id)
    dlg = SourceAddDialog(gui_runtime, None, editing_source=existing)
    qtbot.addWidget(dlg)
    assert dlg.is_edit_mode is True


def test_add_mode_flag_false(qtbot, gui_runtime):
    """v1.7.40: dialog without editing_source has is_edit_mode=False (back-compat)."""
    from curator.gui.dialogs import SourceAddDialog

    dlg = SourceAddDialog(gui_runtime)
    qtbot.addWidget(dlg)
    assert dlg.is_edit_mode is False


def test_edit_mode_window_title_shows_source_id(qtbot, gui_runtime, seeded_source):
    """v1.7.40: window title includes the source_id being edited."""
    from curator.gui.dialogs import SourceAddDialog

    existing = gui_runtime.source_repo.get(seeded_source.source_id)
    dlg = SourceAddDialog(gui_runtime, None, editing_source=existing)
    qtbot.addWidget(dlg)
    assert "my_archive" in dlg.windowTitle(), (
        f"Expected 'my_archive' in title; got {dlg.windowTitle()!r}"
    )
    assert "Edit" in dlg.windowTitle(), (
        f"Expected 'Edit' in title; got {dlg.windowTitle()!r}"
    )


# ---------------------------------------------------------------------------
# Edit mode: prefill
# ---------------------------------------------------------------------------


def test_edit_mode_prefills_source_id(qtbot, gui_runtime, seeded_source):
    """v1.7.40: source_id is prefilled and read-only in edit mode."""
    from curator.gui.dialogs import SourceAddDialog

    existing = gui_runtime.source_repo.get(seeded_source.source_id)
    dlg = SourceAddDialog(gui_runtime, None, editing_source=existing)
    qtbot.addWidget(dlg)
    assert dlg._le_source_id.text() == "my_archive"
    assert dlg._le_source_id.isReadOnly(), (
        "source_id should be read-only in edit mode (immutable primary key)"
    )


def test_edit_mode_prefills_source_type_and_disables(qtbot, gui_runtime, seeded_source):
    """v1.7.40: source_type is prefilled and disabled in edit mode."""
    from curator.gui.dialogs import SourceAddDialog

    existing = gui_runtime.source_repo.get(seeded_source.source_id)
    dlg = SourceAddDialog(gui_runtime, None, editing_source=existing)
    qtbot.addWidget(dlg)
    assert dlg._cb_source_type.currentData() == "local"
    assert not dlg._cb_source_type.isEnabled(), (
        "source_type dropdown should be disabled in edit mode"
    )


def test_edit_mode_prefills_display_name(qtbot, gui_runtime, seeded_source):
    """v1.7.40: display_name prefills from existing source."""
    from curator.gui.dialogs import SourceAddDialog

    existing = gui_runtime.source_repo.get(seeded_source.source_id)
    dlg = SourceAddDialog(gui_runtime, None, editing_source=existing)
    qtbot.addWidget(dlg)
    assert dlg._le_display_name.text() == "My Archive"


def test_edit_mode_prefills_enabled_checkbox(qtbot, gui_runtime, seeded_source):
    """v1.7.40: enabled checkbox reflects existing source.enabled."""
    from curator.gui.dialogs import SourceAddDialog

    existing = gui_runtime.source_repo.get(seeded_source.source_id)
    dlg = SourceAddDialog(gui_runtime, None, editing_source=existing)
    qtbot.addWidget(dlg)
    assert dlg._cb_enabled.isChecked() is True


def test_edit_mode_prefills_share_visibility(qtbot, gui_runtime, seeded_source):
    """v1.7.40: share_visibility prefills from existing source.

    seeded_source has share_visibility='team'; dropdown should reflect that.
    """
    from curator.gui.dialogs import SourceAddDialog

    existing = gui_runtime.source_repo.get(seeded_source.source_id)
    dlg = SourceAddDialog(gui_runtime, None, editing_source=existing)
    qtbot.addWidget(dlg)
    assert dlg._cb_share_visibility.currentData() == "team"


def test_edit_mode_prefills_config_array_field(qtbot, gui_runtime, seeded_source):
    """v1.7.40: plugin config fields prefill from existing source.config.

    The 'roots' field is an array type (QPlainTextEdit). Test it
    prefills with the existing value joined by newlines.
    """
    from curator.gui.dialogs import SourceAddDialog

    existing = gui_runtime.source_repo.get(seeded_source.source_id)
    dlg = SourceAddDialog(gui_runtime, None, editing_source=existing)
    qtbot.addWidget(dlg)
    roots_widget = dlg._config_widgets.get("roots")
    assert roots_widget is not None, "roots widget should exist for local plugin"
    assert roots_widget.toPlainText() == "C:/old/path"


# ---------------------------------------------------------------------------
# Edit mode: submit -> update()
# ---------------------------------------------------------------------------


def test_edit_mode_save_calls_update(qtbot, gui_runtime, seeded_source):
    """v1.7.40: changing share_visibility + saving writes to DB via update().

    End-to-end behavior test:
      1. Open dialog in edit mode (share_visibility='team')
      2. Change dropdown to 'public'
      3. Click OK
      4. Verify DB row has share_visibility='public'
    """
    from curator.gui.dialogs import SourceAddDialog

    existing = gui_runtime.source_repo.get(seeded_source.source_id)
    dlg = SourceAddDialog(gui_runtime, None, editing_source=existing)
    qtbot.addWidget(dlg)

    # Change share_visibility from 'team' to 'public'
    idx = dlg._cb_share_visibility.findData("public")
    assert idx >= 0
    dlg._cb_share_visibility.setCurrentIndex(idx)

    # Submit
    dlg._on_ok_clicked()

    # Verify DB
    updated = gui_runtime.source_repo.get("my_archive")
    assert updated is not None
    assert updated.share_visibility == "public", (
        f"Expected 'public' after save; got {updated.share_visibility!r}"
    )


def test_edit_mode_preserves_created_at(qtbot, gui_runtime, seeded_source):
    """v1.7.40: edit does NOT reset created_at to now().

    Critical: the original creation timestamp must survive edits, so
    sorting / filtering by creation date stays accurate.
    """
    from curator.gui.dialogs import SourceAddDialog

    existing = gui_runtime.source_repo.get(seeded_source.source_id)
    original_created_at = existing.created_at
    dlg = SourceAddDialog(gui_runtime, None, editing_source=existing)
    qtbot.addWidget(dlg)

    # Change something benign + save
    dlg._le_display_name.setText("Renamed Archive")
    dlg._on_ok_clicked()

    updated = gui_runtime.source_repo.get("my_archive")
    assert updated.created_at == original_created_at, (
        f"created_at should be preserved across edit; "
        f"original={original_created_at!r} after_edit={updated.created_at!r}"
    )
    # And the display_name change should have stuck
    assert updated.display_name == "Renamed Archive"


def test_edit_mode_saved_source_id_property(qtbot, gui_runtime, seeded_source):
    """v1.7.40: saved_source_id property mirrors created_source_id (alias)."""
    from curator.gui.dialogs import SourceAddDialog

    existing = gui_runtime.source_repo.get(seeded_source.source_id)
    dlg = SourceAddDialog(gui_runtime, None, editing_source=existing)
    qtbot.addWidget(dlg)
    # Before save -- both None
    assert dlg.saved_source_id is None
    assert dlg.created_source_id is None
    # After save -- both reflect the same source_id
    dlg._on_ok_clicked()
    assert dlg.saved_source_id == "my_archive"
    assert dlg.created_source_id == "my_archive"
    assert dlg.saved_source_id == dlg.created_source_id


def test_edit_mode_disabled_checkbox_persists(qtbot, gui_runtime, seeded_source):
    """v1.7.40: unchecking 'enabled' in edit mode persists to DB."""
    from curator.gui.dialogs import SourceAddDialog

    existing = gui_runtime.source_repo.get(seeded_source.source_id)
    dlg = SourceAddDialog(gui_runtime, None, editing_source=existing)
    qtbot.addWidget(dlg)
    dlg._cb_enabled.setChecked(False)
    dlg._on_ok_clicked()

    updated = gui_runtime.source_repo.get("my_archive")
    assert updated.enabled is False, (
        f"Expected enabled=False after save; got {updated.enabled!r}"
    )


def test_edit_mode_config_field_change_persists(qtbot, gui_runtime, seeded_source):
    """v1.7.40: changing a plugin config field persists to DB."""
    from curator.gui.dialogs import SourceAddDialog

    existing = gui_runtime.source_repo.get(seeded_source.source_id)
    dlg = SourceAddDialog(gui_runtime, None, editing_source=existing)
    qtbot.addWidget(dlg)

    # Change roots from 'C:/old/path' to a new value
    roots_widget = dlg._config_widgets["roots"]
    roots_widget.setPlainText("C:/new/path\nC:/another/path")
    dlg._on_ok_clicked()

    updated = gui_runtime.source_repo.get("my_archive")
    assert updated.config["roots"] == ["C:/new/path", "C:/another/path"], (
        f"Expected updated roots; got {updated.config.get('roots')!r}"
    )


# ---------------------------------------------------------------------------
# Add mode back-compat (regression protection)
# ---------------------------------------------------------------------------


def test_add_mode_still_works(qtbot, gui_runtime):
    """v1.7.40 regression: add mode (no editing_source) still works.

    The v1.7.39 SourceAddDialog tests already cover this, but a small
    re-test here guards against the edit-mode plumbing accidentally
    breaking the add path.
    """
    from curator.gui.dialogs import SourceAddDialog

    dlg = SourceAddDialog(gui_runtime)
    qtbot.addWidget(dlg)

    # Pick local plugin, fill required fields
    idx = dlg._cb_source_type.findData("local")
    dlg._cb_source_type.setCurrentIndex(idx)
    dlg._le_source_id.setText("new_src")
    roots_widget = dlg._config_widgets["roots"]
    roots_widget.setPlainText("C:/somewhere")
    # Choose 'public' to verify share_visibility wiring still works
    dlg._cb_share_visibility.setCurrentIndex(
        dlg._cb_share_visibility.findData("public")
    )
    dlg._on_ok_clicked()

    src = gui_runtime.source_repo.get("new_src")
    assert src is not None, (
        f"Add mode failed; status={dlg._lbl_status.text()!r}"
    )
    assert src.share_visibility == "public"
    # source_id should NOT be read-only in add mode
    # (We need a fresh dialog since the previous one was accepted)
    dlg2 = SourceAddDialog(gui_runtime)
    qtbot.addWidget(dlg2)
    assert not dlg2._le_source_id.isReadOnly(), (
        "source_id should be editable in add mode"
    )
    assert dlg2._cb_source_type.isEnabled(), (
        "source_type should be enabled in add mode"
    )
