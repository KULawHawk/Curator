"""Tests for ``curator mcp keys`` CLI commands (v1.5.0 P2).

Covers DESIGN \u00a75.2 acceptance criteria:

* ``generate <name>`` creates the file if absent.
* ``generate`` prints the full key once, never again.
* Duplicate name on generate: exits 1 with clear error.
* ``list`` shows registered keys without secrets.
* ``revoke`` removes the entry; revoke-not-found returns 1.
* ``revoke --yes`` skips confirmation.
* ``show`` prints metadata without secrets.

Tests use Typer's CliRunner against the CLI app. CURATOR_HOME is
overridden via tmp_path + monkeypatch so each test gets a fresh
keys file with no cross-test contamination.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from curator.cli.main import app
from curator.mcp.auth import KEY_PREFIX, KEYS_FILE_NAME, load_keys


@pytest.fixture
def runner():
    """Typer CliRunner.

    Note: newer Click/Typer versions don't accept ``mix_stderr=False``.
    The runner combines stdout + stderr by default; tests check
    ``result.stdout + (result.stderr or '')`` to be channel-agnostic.
    """
    return CliRunner()


@pytest.fixture
def isolated_keys_dir(tmp_path, monkeypatch):
    """Force ~/.curator/mcp to point at a fresh tmp dir for the test.

    The CURATOR_HOME env var is honored by curator.mcp.auth's path
    helpers, so setting it redirects all MCP key file I/O to the
    tmp dir.
    """
    monkeypatch.setenv("CURATOR_HOME", str(tmp_path))
    return tmp_path / "mcp" / KEYS_FILE_NAME


# ---------------------------------------------------------------------------
# generate
# ---------------------------------------------------------------------------


class TestKeysGenerate:
    def test_generate_creates_file_if_absent(self, runner, isolated_keys_dir):
        assert not isolated_keys_dir.exists()
        result = runner.invoke(app, ["mcp", "keys", "generate", "test-key"])
        assert result.exit_code == 0, result.stdout + result.stderr
        assert isolated_keys_dir.exists()

    def test_generate_prints_plaintext_key(self, runner, isolated_keys_dir):
        result = runner.invoke(app, ["mcp", "keys", "generate", "my-key"])
        assert result.exit_code == 0
        # The plaintext key starts with curm_ and is shown in stdout.
        assert KEY_PREFIX in result.stdout

    def test_generate_persists_with_correct_name(self, runner, isolated_keys_dir):
        result = runner.invoke(app, ["mcp", "keys", "generate", "my-key"])
        assert result.exit_code == 0
        loaded = load_keys(isolated_keys_dir)
        assert len(loaded) == 1
        assert loaded[0].name == "my-key"

    def test_generate_with_description(self, runner, isolated_keys_dir):
        result = runner.invoke(app, [
            "mcp", "keys", "generate", "my-key",
            "--description", "Laptop integration",
        ])
        assert result.exit_code == 0
        loaded = load_keys(isolated_keys_dir)
        assert loaded[0].description == "Laptop integration"

    def test_generate_duplicate_name_exits_1(self, runner, isolated_keys_dir):
        runner.invoke(app, ["mcp", "keys", "generate", "my-key"])
        result = runner.invoke(app, ["mcp", "keys", "generate", "my-key"])
        assert result.exit_code == 1
        # Error message in combined output (stdout + stderr)
        combined = result.stdout + (result.stderr or "")
        assert "already exists" in combined.lower()

    def test_generate_duplicate_does_not_corrupt_existing(
        self, runner, isolated_keys_dir,
    ):
        result1 = runner.invoke(app, ["mcp", "keys", "generate", "my-key"])
        first_keys = load_keys(isolated_keys_dir)

        result2 = runner.invoke(app, ["mcp", "keys", "generate", "my-key"])
        assert result2.exit_code == 1

        # First key still exists with original hash
        after_keys = load_keys(isolated_keys_dir)
        assert len(after_keys) == 1
        assert after_keys[0].key_hash == first_keys[0].key_hash

    def test_generate_json_output(self, runner, isolated_keys_dir):
        result = runner.invoke(app, [
            "--json", "mcp", "keys", "generate", "my-key",
        ])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["ok"] is True
        assert payload["name"] == "my-key"
        assert payload["key"].startswith(KEY_PREFIX)


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


class TestKeysList:
    def test_list_empty(self, runner, isolated_keys_dir):
        result = runner.invoke(app, ["mcp", "keys", "list"])
        assert result.exit_code == 0
        assert "No API keys configured" in result.stdout

    def test_list_after_generate(self, runner, isolated_keys_dir):
        runner.invoke(app, ["mcp", "keys", "generate", "key1"])
        runner.invoke(app, ["mcp", "keys", "generate", "key2"])

        result = runner.invoke(app, ["mcp", "keys", "list"])
        assert result.exit_code == 0
        assert "key1" in result.stdout
        assert "key2" in result.stdout

    def test_list_does_not_show_key_hashes(self, runner, isolated_keys_dir):
        runner.invoke(app, ["mcp", "keys", "generate", "my-key"])
        result = runner.invoke(app, ["mcp", "keys", "list"])
        assert result.exit_code == 0
        # Key hash is 64 hex chars; ensure no such substring is in output.
        # Imperfect check, but if key_hash leaked we'd see it as a long
        # hex string in the table.
        loaded = load_keys(isolated_keys_dir)
        assert loaded[0].key_hash not in result.stdout

    def test_list_does_not_show_plaintext_keys(self, runner, isolated_keys_dir):
        gen_result = runner.invoke(app, ["mcp", "keys", "generate", "my-key"])
        # Extract the plaintext from generate output (it's printed to stdout)
        # Then verify it doesn't appear in list output.
        plaintext_lines = [
            line for line in gen_result.stdout.splitlines()
            if KEY_PREFIX in line
        ]
        assert plaintext_lines, "expected to see plaintext key in generate output"
        # Find the actual key by scanning for the curm_ prefix
        plaintext = None
        for line in plaintext_lines:
            for tok in line.split():
                if KEY_PREFIX in tok:
                    plaintext = tok.strip()
                    break
        assert plaintext is not None and plaintext.startswith(KEY_PREFIX)

        list_result = runner.invoke(app, ["mcp", "keys", "list"])
        # Strip ANSI/markup before comparing -- Rich may color differently
        assert plaintext not in list_result.stdout

    def test_list_json_output(self, runner, isolated_keys_dir):
        runner.invoke(app, [
            "mcp", "keys", "generate", "my-key",
            "--description", "test",
        ])
        result = runner.invoke(app, ["--json", "mcp", "keys", "list"])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["ok"] is True
        assert len(payload["keys"]) == 1
        assert payload["keys"][0]["name"] == "my-key"
        assert payload["keys"][0]["description"] == "test"
        # Hash + plaintext not in JSON output
        assert "key_hash" not in payload["keys"][0]
        assert "key" not in payload["keys"][0]


# ---------------------------------------------------------------------------
# revoke
# ---------------------------------------------------------------------------


class TestKeysRevoke:
    def test_revoke_yes_removes_entry(self, runner, isolated_keys_dir):
        runner.invoke(app, ["mcp", "keys", "generate", "my-key"])
        result = runner.invoke(app, ["mcp", "keys", "revoke", "my-key", "--yes"])
        assert result.exit_code == 0
        assert load_keys(isolated_keys_dir) == []

    def test_revoke_nonexistent_exits_1(self, runner, isolated_keys_dir):
        # No keys file exists; revoke should still exit 1 (not crash)
        result = runner.invoke(app, [
            "mcp", "keys", "revoke", "does-not-exist", "--yes",
        ])
        assert result.exit_code == 1

    def test_revoke_preserves_other_keys(self, runner, isolated_keys_dir):
        runner.invoke(app, ["mcp", "keys", "generate", "k1"])
        runner.invoke(app, ["mcp", "keys", "generate", "k2"])
        runner.invoke(app, ["mcp", "keys", "generate", "k3"])

        result = runner.invoke(app, ["mcp", "keys", "revoke", "k2", "--yes"])
        assert result.exit_code == 0

        remaining = sorted(k.name for k in load_keys(isolated_keys_dir))
        assert remaining == ["k1", "k3"]

    def test_revoke_without_yes_prompts_n(self, runner, isolated_keys_dir):
        runner.invoke(app, ["mcp", "keys", "generate", "my-key"])
        # Send "n\n" to the prompt
        result = runner.invoke(
            app, ["mcp", "keys", "revoke", "my-key"], input="n\n",
        )
        assert result.exit_code == 0
        # Key still exists -- user said no
        loaded = load_keys(isolated_keys_dir)
        assert len(loaded) == 1

    def test_revoke_without_yes_prompts_y(self, runner, isolated_keys_dir):
        runner.invoke(app, ["mcp", "keys", "generate", "my-key"])
        result = runner.invoke(
            app, ["mcp", "keys", "revoke", "my-key"], input="y\n",
        )
        assert result.exit_code == 0
        # Key removed -- user said yes
        assert load_keys(isolated_keys_dir) == []

    def test_revoke_json_output(self, runner, isolated_keys_dir):
        runner.invoke(app, ["mcp", "keys", "generate", "my-key"])
        result = runner.invoke(app, [
            "--json", "mcp", "keys", "revoke", "my-key", "--yes",
        ])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["ok"] is True
        assert payload["removed"] is True


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------


class TestKeysShow:
    def test_show_existing_key(self, runner, isolated_keys_dir):
        runner.invoke(app, [
            "mcp", "keys", "generate", "my-key",
            "--description", "test description",
        ])
        result = runner.invoke(app, ["mcp", "keys", "show", "my-key"])
        assert result.exit_code == 0
        assert "my-key" in result.stdout
        assert "test description" in result.stdout

    def test_show_nonexistent_exits_1(self, runner, isolated_keys_dir):
        result = runner.invoke(app, ["mcp", "keys", "show", "does-not-exist"])
        assert result.exit_code == 1

    def test_show_does_not_print_hash(self, runner, isolated_keys_dir):
        runner.invoke(app, ["mcp", "keys", "generate", "my-key"])
        result = runner.invoke(app, ["mcp", "keys", "show", "my-key"])
        loaded = load_keys(isolated_keys_dir)
        assert loaded[0].key_hash not in result.stdout

    def test_show_json_output(self, runner, isolated_keys_dir):
        runner.invoke(app, [
            "mcp", "keys", "generate", "my-key",
            "--description", "test",
        ])
        result = runner.invoke(app, ["--json", "mcp", "keys", "show", "my-key"])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["ok"] is True
        assert payload["name"] == "my-key"
        assert payload["description"] == "test"
        # Hash never in JSON output
        assert "key_hash" not in payload


# ---------------------------------------------------------------------------
# Help text
# ---------------------------------------------------------------------------


class TestHelp:
    def test_curator_mcp_help_lists_keys_subgroup(self, runner):
        result = runner.invoke(app, ["mcp", "--help"])
        assert result.exit_code == 0
        assert "keys" in result.stdout

    def test_curator_mcp_keys_help_lists_subcommands(self, runner):
        result = runner.invoke(app, ["mcp", "keys", "--help"])
        assert result.exit_code == 0
        assert "generate" in result.stdout
        assert "list" in result.stdout
        assert "revoke" in result.stdout
        assert "show" in result.stdout
