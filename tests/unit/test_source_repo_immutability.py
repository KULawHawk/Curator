"""Tests for v1.7.49 source_type repository-level immutability.

The :meth:`SourceRepository.update` method now raises ``ValueError`` if
the caller tries to change a source's ``source_type``. This closes the
gap from v1.7.40 where the GUI's :class:`SourceAddDialog` enforced
immutability via a disabled combobox, but a direct repository call
could silently change the type and invalidate the existing config_json
against a different plugin's schema.

Tests cover:

  * Updating with the SAME source_type works (back-compat)
  * Updating with a DIFFERENT source_type raises ValueError
  * The error message includes both source_types and the source_id
  * Other fields (display_name, config, enabled, share_visibility) can
    still be changed freely on update()
  * Calling update() on a non-existent source_id is a silent SQL no-op
    (preserves pre-v1.7.49 behavior)
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from curator.cli.runtime import build_runtime
from curator.config import Config
from curator.models.source import SourceConfig


@pytest.fixture
def runtime(tmp_path):
    """A real CuratorRuntime against a temp DB."""
    db = tmp_path / "v1749.db"
    return build_runtime(
        config=Config.load(),
        db_path_override=db,
        json_output=False, no_color=True, verbosity=0,
    )


@pytest.fixture
def seeded_source(runtime):
    """A SourceConfig inserted in the test DB, ready for update tests."""
    src = SourceConfig(
        source_id="my_archive",
        source_type="local",
        display_name="My Archive",
        config={"roots": ["C:/old/path"]},
        enabled=True,
        created_at=datetime(2025, 1, 15, 10, 30, 0),
        share_visibility="private",
    )
    runtime.source_repo.insert(src)
    return src


# ---------------------------------------------------------------------------
# Same source_type: back-compat (no behavior change)
# ---------------------------------------------------------------------------


class TestSameTypeUpdateAllowed:
    """v1.7.49: updates that preserve source_type continue to work."""

    def test_update_same_type_succeeds(self, runtime, seeded_source):
        """Same source_type, different display_name -- the v1.7.40 path."""
        modified = seeded_source.model_copy(update={
            "display_name": "Renamed Archive",
        })
        runtime.source_repo.update(modified)
        got = runtime.source_repo.get("my_archive")
        assert got.display_name == "Renamed Archive"
        assert got.source_type == "local"

    def test_update_same_type_can_change_share_visibility(
        self, runtime, seeded_source,
    ):
        """v1.7.40 use case: change share_visibility via update()."""
        modified = seeded_source.model_copy(update={
            "share_visibility": "public",
        })
        runtime.source_repo.update(modified)
        got = runtime.source_repo.get("my_archive")
        assert got.share_visibility == "public"
        assert got.source_type == "local"

    def test_update_same_type_can_change_config(self, runtime, seeded_source):
        """Plugin config edits work on update()."""
        modified = seeded_source.model_copy(update={
            "config": {"roots": ["C:/new/path", "C:/another/path"]},
        })
        runtime.source_repo.update(modified)
        got = runtime.source_repo.get("my_archive")
        assert got.config["roots"] == ["C:/new/path", "C:/another/path"]

    def test_update_same_type_can_disable(self, runtime, seeded_source):
        """Enabled flag is mutable via update()."""
        modified = seeded_source.model_copy(update={"enabled": False})
        runtime.source_repo.update(modified)
        got = runtime.source_repo.get("my_archive")
        assert got.enabled is False


# ---------------------------------------------------------------------------
# Different source_type: raises ValueError
# ---------------------------------------------------------------------------


class TestDifferentTypeRejected:
    """v1.7.49: updates that change source_type raise ValueError."""

    def test_update_different_type_raises_value_error(
        self, runtime, seeded_source,
    ):
        """Changing local -> gdrive must raise ValueError."""
        bad = seeded_source.model_copy(update={
            "source_type": "gdrive",
            # gdrive needs different config, but the type check should
            # fire BEFORE any config validation
            "config": {"credentials_path": "C:/cred.json"},
        })
        with pytest.raises(ValueError):
            runtime.source_repo.update(bad)

    def test_error_message_mentions_both_types(
        self, runtime, seeded_source,
    ):
        """The ValueError message should include both source_types."""
        bad = seeded_source.model_copy(update={"source_type": "gdrive"})
        with pytest.raises(ValueError) as exc_info:
            runtime.source_repo.update(bad)
        msg = str(exc_info.value)
        assert "local" in msg, (
            f"Error message should mention old source_type 'local'; "
            f"got: {msg!r}"
        )
        assert "gdrive" in msg, (
            f"Error message should mention new source_type 'gdrive'; "
            f"got: {msg!r}"
        )

    def test_error_message_mentions_source_id(
        self, runtime, seeded_source,
    ):
        """The ValueError message should include the source_id."""
        bad = seeded_source.model_copy(update={"source_type": "gdrive"})
        with pytest.raises(ValueError) as exc_info:
            runtime.source_repo.update(bad)
        msg = str(exc_info.value)
        assert "my_archive" in msg, (
            f"Error message should mention source_id 'my_archive'; "
            f"got: {msg!r}"
        )

    def test_error_message_mentions_immutability(
        self, runtime, seeded_source,
    ):
        """The error message should explain WHY (immutability)."""
        bad = seeded_source.model_copy(update={"source_type": "gdrive"})
        with pytest.raises(ValueError) as exc_info:
            runtime.source_repo.update(bad)
        msg = str(exc_info.value).lower()
        assert "immutable" in msg, (
            f"Error message should mention immutability; got: {msg!r}"
        )

    def test_failed_update_does_not_write(self, runtime, seeded_source):
        """A failed type-change update must NOT have changed the row.

        Critical: the guard fires BEFORE the SQL UPDATE, so the existing
        row's source_type, display_name, etc. should be untouched.
        """
        bad = seeded_source.model_copy(update={
            "source_type": "gdrive",
            "display_name": "Should Not Be Written",
        })
        with pytest.raises(ValueError):
            runtime.source_repo.update(bad)

        # Row is unchanged
        got = runtime.source_repo.get("my_archive")
        assert got.source_type == "local", "source_type was changed despite the raise"
        assert got.display_name == "My Archive", "display_name was changed despite the raise"


# ---------------------------------------------------------------------------
# Non-existent source_id: silent no-op (preserves pre-v1.7.49 behavior)
# ---------------------------------------------------------------------------


class TestNonExistentSource:
    """v1.7.49: update() on a non-existent source_id is still a silent no-op.

    This is intentional behavior preservation: changing it to raise
    would be a behavior change that could break existing callers.
    Adding a "not found" error is a separate, larger-scope ship.
    """

    def test_update_unknown_id_no_op(self, runtime):
        """Calling update() with an unknown source_id silently does nothing."""
        ghost = SourceConfig(
            source_id="ghost_archive",
            source_type="local",
            display_name="Ghost",
            config={"roots": ["C:/somewhere"]},
            enabled=True,
            created_at=datetime(2025, 1, 1),
            share_visibility="private",
        )
        # Should NOT raise (even though row doesn't exist)
        runtime.source_repo.update(ghost)
        # Row was not inserted by the UPDATE
        assert runtime.source_repo.get("ghost_archive") is None

    def test_update_unknown_id_with_arbitrary_type_no_op(self, runtime):
        """Even with a 'different' source_type, no row means no comparison.

        Since there's no existing row to compare against, the immutability
        check can't fire. The SQL UPDATE affects 0 rows. No error.
        """
        ghost = SourceConfig(
            source_id="ghost_archive_2",
            source_type="gdrive",  # arbitrary type; no existing row to compare
            display_name="Ghost",
            config={"credentials_path": "C:/cred.json"},
            enabled=True,
            created_at=datetime(2025, 1, 1),
            share_visibility="private",
        )
        # Should NOT raise
        runtime.source_repo.update(ghost)
        # Row was not inserted by the UPDATE
        assert runtime.source_repo.get("ghost_archive_2") is None
