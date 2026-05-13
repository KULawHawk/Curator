"""Focused coverage tests for config/__init__.py.

Sub-ship v1.7.123 of Round 2 Tier 2.

Closes 39 uncovered lines + 10 partial branches across:
* `Config.__init__` data-merge branch
* `Config.load` file-found path
* `from_dict` factory
* `get` dotted-path defensives
* `set` dotted-path intermediate creation
* `__getitem__`, `__contains__`, `as_dict`, `source_path`
* `save` (with and without `path` arg)
* `_merge` body
* `_deep_update` body
* `_resolve_path` branches (env var, cwd, platformdirs)
* `_resolve_auto_paths` for db_path/log_path auto
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import curator.config as config_mod
from curator.config import (
    CONFIG_FILENAME,
    Config,
    ENV_VAR,
)


# ---------------------------------------------------------------------------
# __init__ data-merge branch (line 55)
# ---------------------------------------------------------------------------


def test_init_with_data_merges_into_defaults():
    # Line 55: Config(data={...}) → self._merge(data) fires.
    cfg = Config(data={"curator": {"log_level": "DEBUG"}})
    assert cfg.get("curator.log_level") == "DEBUG"


# ---------------------------------------------------------------------------
# load: file-found path (77-80)
# ---------------------------------------------------------------------------


def test_load_reads_user_toml_when_file_exists(tmp_path, monkeypatch):
    # Lines 77-80: explicit path exists → open file, merge, set source_path.
    cfg_file = tmp_path / "curator.toml"
    cfg_file.write_text('[curator]\nlog_level = "WARNING"\n')
    cfg = Config.load(cfg_file)
    assert cfg.get("curator.log_level") == "WARNING"
    assert cfg.source_path == cfg_file


# ---------------------------------------------------------------------------
# from_dict factory (87-89)
# ---------------------------------------------------------------------------


def test_from_dict_constructs_and_resolves_auto():
    # Lines 87-89: from_dict creates Config + resolves auto paths.
    cfg = Config.from_dict({"curator": {"log_level": "ERROR"}})
    assert cfg.get("curator.log_level") == "ERROR"
    # auto paths got resolved (no longer "auto")
    assert cfg.get("curator.db_path") != "auto"


# ---------------------------------------------------------------------------
# get + set dotted-path (100, 110)
# ---------------------------------------------------------------------------


def test_get_returns_default_when_intermediate_not_dict():
    # Line 100: intermediate node isn't a dict → return default.
    cfg = Config(data={"curator": {"log_level": "INFO"}})
    # "log_level.foo" tries to traverse INTO the string "INFO"
    assert cfg.get("curator.log_level.foo", default="fallback") == "fallback"


def test_get_returns_default_when_key_not_in_node():
    cfg = Config()
    assert cfg.get("nonexistent.key", default="X") == "X"


def test_set_creates_intermediate_dicts():
    # Line 109-110: intermediate key missing OR not a dict → create new
    # dict. Set a deeply nested key from scratch.
    cfg = Config()
    cfg.set("newroot.newsub.leaf", 42)
    assert cfg.get("newroot.newsub.leaf") == 42


def test_set_replaces_non_dict_intermediate():
    # Line 110 specifically: intermediate exists but isn't a dict.
    cfg = Config(data={"curator": {"db_path": "string_value"}})
    # Setting "curator.db_path.nested" replaces the string with a dict.
    cfg.set("curator.db_path.nested", "ok")
    assert cfg.get("curator.db_path.nested") == "ok"


# ---------------------------------------------------------------------------
# Dunder/property accessors (115, 118, 122, 127)
# ---------------------------------------------------------------------------


def test_getitem_returns_section_dict():
    # Line 115: __getitem__ returns the section.
    cfg = Config()
    section = cfg["curator"]
    assert isinstance(section, dict)


def test_contains_returns_true_for_known_section():
    # Line 118: __contains__ checks top-level key.
    cfg = Config()
    assert "curator" in cfg
    assert "totally_made_up" not in cfg


def test_as_dict_returns_deep_copy():
    # Line 122: as_dict returns a deep copy.
    cfg = Config()
    snapshot = cfg.as_dict()
    snapshot["curator"]["log_level"] = "MUTATED"
    # Original config unchanged.
    assert cfg.get("curator.log_level") != "MUTATED"


def test_convenience_properties_return_expected_values():
    # Lines 136, 141, 145: db_path / log_path / log_level convenience
    # accessors.
    cfg = Config.from_dict({"curator": {
        "db_path": "/custom/db.sqlite",
        "log_path": "/custom/log.log",
        "log_level": "warning",  # tested for upper() normalization
    }})
    assert str(cfg.db_path) == str(Path("/custom/db.sqlite"))
    assert str(cfg.log_path) == str(Path("/custom/log.log"))
    assert cfg.log_level == "WARNING"  # normalized to upper


def test_load_with_no_path_uses_defaults_only(monkeypatch, tmp_path):
    # Branch 76->81: explicit path is None → skip file read → just
    # resolve auto paths. Force _resolve_path to return None.
    monkeypatch.setattr(Config, "_resolve_path", staticmethod(lambda x: None))
    cfg = Config.load()
    assert cfg.source_path is None


def test_source_path_property_returns_value(tmp_path):
    # Line 127: source_path property returns _source_path.
    cfg_file = tmp_path / "c.toml"
    cfg_file.write_text("[curator]\n")
    cfg = Config.load(cfg_file)
    assert cfg.source_path == cfg_file


# ---------------------------------------------------------------------------
# save() body (165-178)
# ---------------------------------------------------------------------------


def test_save_writes_toml_to_explicit_path(tmp_path):
    # Lines 172-178: explicit path → write to it. Use from_dict so auto
    # paths are resolved (defaults may contain None values that aren't
    # TOML-serializable in the raw form).
    cfg = Config.from_dict({"curator": {"log_level": "DEBUG"}})
    # Remove any None values from the dict so TOML can serialize.
    _strip_none_values(cfg._data)
    target = tmp_path / "out.toml"
    written = cfg.save(target)
    assert written == target
    assert target.exists()
    reloaded = Config.load(target)
    assert reloaded.get("curator.log_level") == "DEBUG"


def test_save_uses_source_path_when_no_arg(tmp_path):
    cfg_file = tmp_path / "src.toml"
    cfg_file.write_text("[curator]\n")
    cfg = Config.load(cfg_file)
    _strip_none_values(cfg._data)
    cfg.set("curator.log_level", "TRACE")
    written = cfg.save()
    assert written == cfg_file


def _strip_none_values(d: dict) -> None:
    """Recursively remove None-valued keys so tomli_w can serialize."""
    keys_to_drop = [k for k, v in d.items() if v is None]
    for k in keys_to_drop:
        del d[k]
    for v in d.values():
        if isinstance(v, dict):
            _strip_none_values(v)


def test_save_raises_when_no_path_available():
    # Line 173-174: no path arg and no source_path → ValueError.
    cfg = Config()
    with pytest.raises(ValueError, match="No path given"):
        cfg.save()


def test_save_raises_import_error_when_tomli_w_missing(tmp_path, monkeypatch):
    # Lines 166-170: tomli_w missing → ImportError.
    monkeypatch.setitem(sys.modules, "tomli_w", None)
    cfg = Config()
    target = tmp_path / "out.toml"
    with pytest.raises(ImportError, match="tomli_w"):
        cfg.save(target)


# ---------------------------------------------------------------------------
# _merge body (186-193)
# ---------------------------------------------------------------------------


def test_merge_replaces_non_section_keys():
    # Lines 191-193: non-dict top-level value just replaces.
    cfg = Config()
    cfg._merge({"non_dict_top": "value"})
    assert cfg._data["non_dict_top"] == "value"


def test_merge_deep_merges_section_dicts():
    # Lines 187-190: section value is a dict + existing section is a dict
    # → _deep_update.
    cfg = Config()
    # Make sure 'curator' is a dict in defaults
    assert isinstance(cfg._data.get("curator"), dict)
    cfg._merge({"curator": {"new_key": "new_val"}})
    assert cfg.get("curator.new_key") == "new_val"
    # Existing keys preserved.
    assert "log_level" in cfg._data["curator"]


# ---------------------------------------------------------------------------
# _deep_update body (198-206)
# ---------------------------------------------------------------------------


def test_deep_update_recurses_on_nested_dicts():
    # Lines 199-204: when both sides are dicts at the same key, recurse.
    target = {"a": {"b": {"c": 1}}}
    source = {"a": {"b": {"d": 2}}}
    Config._deep_update(target, source)
    assert target == {"a": {"b": {"c": 1, "d": 2}}}


def test_deep_update_replaces_non_dict_values():
    # Lines 205-206: not both dicts → replace.
    target = {"a": "old"}
    source = {"a": "new"}
    Config._deep_update(target, source)
    assert target == {"a": "new"}


# ---------------------------------------------------------------------------
# _resolve_path branches (212, 215, 218, 224-226)
# ---------------------------------------------------------------------------


def test_resolve_path_returns_explicit_when_provided():
    # Line 212: explicit not None → return Path(explicit).
    result = Config._resolve_path("/some/path.toml")
    assert result == Path("/some/path.toml")


def test_resolve_path_uses_env_var_when_set(monkeypatch):
    # Line 213-215: env var set → return that.
    monkeypatch.setenv(ENV_VAR, "/env/path.toml")
    result = Config._resolve_path(None)
    assert result == Path("/env/path.toml")


def test_resolve_path_uses_cwd_when_file_present(tmp_path, monkeypatch):
    # Lines 216-218: cwd has curator.toml → return that.
    cfg_file = tmp_path / CONFIG_FILENAME
    cfg_file.write_text("")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(ENV_VAR, raising=False)
    result = Config._resolve_path(None)
    assert result == cfg_file


def test_resolve_path_returns_none_when_nothing_found(tmp_path, monkeypatch):
    # Lines 219-227: no env, no cwd, no platformdirs file → None.
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(ENV_VAR, raising=False)
    # Force platformdirs path to nonexistent location.
    import platformdirs
    monkeypatch.setattr(
        platformdirs, "user_config_dir",
        lambda app: str(tmp_path / "nonexistent_subdir"),
    )
    result = Config._resolve_path(None)
    assert result is None


def test_resolve_path_uses_platformdirs_when_user_file_exists(
    tmp_path, monkeypatch,
):
    # Lines 219-224: platformdirs user_config_dir/curator.toml exists.
    user_dir = tmp_path / "userdir"
    user_dir.mkdir()
    cfg_file = user_dir / CONFIG_FILENAME
    cfg_file.write_text("")
    monkeypatch.chdir(tmp_path)  # cwd has no curator.toml
    monkeypatch.delenv(ENV_VAR, raising=False)
    import platformdirs
    monkeypatch.setattr(
        platformdirs, "user_config_dir",
        lambda app: str(user_dir),
    )
    result = Config._resolve_path(None)
    assert result == cfg_file


def test_resolve_path_swallows_platformdirs_import_error(
    tmp_path, monkeypatch,
):
    # Lines 225-226: platformdirs ImportError → fall through to None.
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(ENV_VAR, raising=False)
    monkeypatch.setitem(sys.modules, "platformdirs", None)
    result = Config._resolve_path(None)
    assert result is None


# ---------------------------------------------------------------------------
# _resolve_auto_paths branches (236->239, 239->exit)
# ---------------------------------------------------------------------------


def test_resolve_auto_paths_skips_when_db_path_already_explicit():
    # Branch 236->239: db_path is not "auto" → skip db_path expansion,
    # move to log_path check.
    cfg = Config(data={"curator": {"db_path": "/custom/db.sqlite"}})
    # After __init__ + from_dict-like flow, manually invoke
    cfg._resolve_auto_paths()
    # db_path stays the explicit value.
    assert cfg.get("curator.db_path") == "/custom/db.sqlite"


def test_resolve_auto_paths_skips_when_log_path_already_explicit():
    # Branch 239->exit: log_path is not "auto" → skip log_path expansion.
    cfg = Config(data={
        "curator": {
            "db_path": "/custom/db.sqlite",
            "log_path": "/custom/log.log",
        }
    })
    cfg._resolve_auto_paths()
    assert cfg.get("curator.log_path") == "/custom/log.log"
