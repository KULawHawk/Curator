"""Coverage closure for ``curator.cli.mcp_keys`` (v1.7.153).

Existing ``tests/unit/test_mcp_keys_cli.py`` covers the happy paths +
human-output error messages. The remaining uncovered surface is:
- JSON-output mode for error paths (lines 112, 286, 369)
- ``KeyFileError`` handling across all four commands (lines 128-140 in
  generate, 182-194 in list, 270-282 in revoke, 352-364 in show)
- Defensive ``removed=False`` branch in revoke (327-328)
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from curator.cli.main import app
from curator.mcp.auth import KEYS_FILE_NAME, KeyFileError


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def isolated_keys_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("CURATOR_HOME", str(tmp_path))
    return tmp_path / "mcp" / KEYS_FILE_NAME


# ---------------------------------------------------------------------------
# JSON output mode for error paths
# ---------------------------------------------------------------------------


class TestGenerateJsonErrors:
    def test_duplicate_name_json_output(self, runner, isolated_keys_dir):
        """Line 112-120: generate's DuplicateNameError handler with --json."""
        # Generate first key
        result1 = runner.invoke(app, ["--json", "mcp", "keys", "generate", "dup"])
        assert result1.exit_code == 0
        # Same name again -> DuplicateNameError + JSON output
        result = runner.invoke(app, ["--json", "mcp", "keys", "generate", "dup"])
        assert result.exit_code == 1
        # JSON payload should be parseable
        combined = result.stdout + (result.stderr or "")
        # Find the JSON object (may have stderr/stdout mixed)
        assert '"ok": false' in combined
        assert '"error": "duplicate_name"' in combined
        assert '"name": "dup"' in combined


class TestRevokeJsonErrors:
    def test_revoke_not_found_json_output(self, runner, isolated_keys_dir):
        """Line 286-291: revoke not_found handler with --json."""
        # Generate a key so the file exists with non-empty contents
        runner.invoke(app, ["mcp", "keys", "generate", "existing"])
        result = runner.invoke(app, ["--json", "mcp", "keys", "revoke", "missing"])
        assert result.exit_code == 1
        combined = result.stdout + (result.stderr or "")
        assert '"ok": false' in combined
        assert '"error": "not_found"' in combined
        assert '"name": "missing"' in combined

    def test_revoke_existing_json_output(self, runner, isolated_keys_dir):
        """JSON output for the happy revoke path (covers JSON return branch)."""
        runner.invoke(app, ["mcp", "keys", "generate", "tobedeleted"])
        result = runner.invoke(
            app, ["--json", "mcp", "keys", "revoke", "tobedeleted", "--yes"],
        )
        assert result.exit_code == 0
        combined = result.stdout + (result.stderr or "")
        assert '"ok": true' in combined
        assert '"removed": true' in combined


class TestShowJsonErrors:
    def test_show_not_found_json_output(self, runner, isolated_keys_dir):
        """Line 369-375: show not_found handler with --json."""
        # Empty key file
        runner.invoke(app, ["mcp", "keys", "generate", "alpha"])
        result = runner.invoke(app, ["--json", "mcp", "keys", "show", "ghost"])
        assert result.exit_code == 1
        combined = result.stdout + (result.stderr or "")
        assert '"ok": false' in combined
        assert '"error": "not_found"' in combined


# ---------------------------------------------------------------------------
# KeyFileError handlers across all four commands
# ---------------------------------------------------------------------------


class TestGenerateKeyFileError:
    def test_keyfile_error_human_output(self, runner, isolated_keys_dir, monkeypatch):
        """Lines 128-140: generate's KeyFileError handler (human path)."""
        import curator.cli.mcp_keys as mcp_keys_mod

        def _raise(*args, **kwargs):
            raise KeyFileError("permission denied (simulated)")

        monkeypatch.setattr(mcp_keys_mod, "add_key", _raise)
        result = runner.invoke(app, ["mcp", "keys", "generate", "wontwork"])
        assert result.exit_code == 2
        combined = result.stdout + (result.stderr or "")
        assert "Error reading keys file" in combined
        assert "permission denied" in combined

    def test_keyfile_error_json_output(self, runner, isolated_keys_dir, monkeypatch):
        """Lines 128-140: generate's KeyFileError handler (JSON path)."""
        import curator.cli.mcp_keys as mcp_keys_mod

        def _raise(*args, **kwargs):
            raise KeyFileError("io error (simulated)")

        monkeypatch.setattr(mcp_keys_mod, "add_key", _raise)
        result = runner.invoke(app, ["--json", "mcp", "keys", "generate", "wontwork"])
        assert result.exit_code == 2
        combined = result.stdout + (result.stderr or "")
        assert '"error": "key_file_error"' in combined


class TestListKeyFileError:
    def test_keyfile_error_human_output(self, runner, isolated_keys_dir, monkeypatch):
        """Lines 182-194: list's KeyFileError handler (human path)."""
        import curator.cli.mcp_keys as mcp_keys_mod

        def _raise(_path):
            raise KeyFileError("file corrupt (simulated)")

        monkeypatch.setattr(mcp_keys_mod, "load_keys", _raise)
        result = runner.invoke(app, ["mcp", "keys", "list"])
        assert result.exit_code == 2
        combined = result.stdout + (result.stderr or "")
        assert "Error reading keys file" in combined

    def test_keyfile_error_json_output(self, runner, isolated_keys_dir, monkeypatch):
        """Lines 182-194: list's KeyFileError handler (JSON path)."""
        import curator.cli.mcp_keys as mcp_keys_mod

        def _raise(_path):
            raise KeyFileError("file corrupt (simulated)")

        monkeypatch.setattr(mcp_keys_mod, "load_keys", _raise)
        result = runner.invoke(app, ["--json", "mcp", "keys", "list"])
        assert result.exit_code == 2
        combined = result.stdout + (result.stderr or "")
        assert '"error": "key_file_error"' in combined


class TestRevokeKeyFileError:
    def test_keyfile_error_human_output(self, runner, isolated_keys_dir, monkeypatch):
        """Lines 270-282: revoke's KeyFileError handler (human path)."""
        import curator.cli.mcp_keys as mcp_keys_mod

        def _raise(_path):
            raise KeyFileError("io error (simulated)")

        monkeypatch.setattr(mcp_keys_mod, "load_keys", _raise)
        result = runner.invoke(app, ["mcp", "keys", "revoke", "anything", "--yes"])
        assert result.exit_code == 2
        combined = result.stdout + (result.stderr or "")
        assert "Error reading keys file" in combined

    def test_keyfile_error_json_output(self, runner, isolated_keys_dir, monkeypatch):
        """Lines 270-282: revoke's KeyFileError handler (JSON path)."""
        import curator.cli.mcp_keys as mcp_keys_mod

        def _raise(_path):
            raise KeyFileError("io error (simulated)")

        monkeypatch.setattr(mcp_keys_mod, "load_keys", _raise)
        result = runner.invoke(
            app, ["--json", "mcp", "keys", "revoke", "anything", "--yes"],
        )
        assert result.exit_code == 2
        combined = result.stdout + (result.stderr or "")
        assert '"error": "key_file_error"' in combined


class TestShowKeyFileError:
    def test_keyfile_error_human_output(self, runner, isolated_keys_dir, monkeypatch):
        """Lines 352-364: show's KeyFileError handler (human path)."""
        import curator.cli.mcp_keys as mcp_keys_mod

        def _raise(_path):
            raise KeyFileError("io error (simulated)")

        monkeypatch.setattr(mcp_keys_mod, "load_keys", _raise)
        result = runner.invoke(app, ["mcp", "keys", "show", "anything"])
        assert result.exit_code == 2
        combined = result.stdout + (result.stderr or "")
        assert "Error reading keys file" in combined

    def test_keyfile_error_json_output(self, runner, isolated_keys_dir, monkeypatch):
        """Lines 352-364: show's KeyFileError handler (JSON path)."""
        import curator.cli.mcp_keys as mcp_keys_mod

        def _raise(_path):
            raise KeyFileError("io error (simulated)")

        monkeypatch.setattr(mcp_keys_mod, "load_keys", _raise)
        result = runner.invoke(app, ["--json", "mcp", "keys", "show", "anything"])
        assert result.exit_code == 2
        combined = result.stdout + (result.stderr or "")
        assert '"error": "key_file_error"' in combined


# ---------------------------------------------------------------------------
# Defensive removed=False branch (revoke)
# ---------------------------------------------------------------------------


class TestRevokeRemovedFalseDefensive:
    def test_removed_false_after_existing_check_is_human_branch(
        self, runner, isolated_keys_dir, monkeypatch,
    ):
        """Lines 327-328: revoke's defensive "removed=False after passing
        existing check" branch — should be unreachable in normal flow,
        but tested via monkeypatching remove_key to return False."""
        # Setup a real key first so existing check passes
        runner.invoke(app, ["mcp", "keys", "generate", "racy"])

        import curator.cli.mcp_keys as mcp_keys_mod
        monkeypatch.setattr(mcp_keys_mod, "remove_key", lambda name, path: False)

        result = runner.invoke(app, ["mcp", "keys", "revoke", "racy", "--yes"])
        assert result.exit_code == 1
        combined = result.stdout + (result.stderr or "")
        assert "was not found" in combined
