"""Focused coverage tests for plugins/manager.py.

Sub-ship v1.7.111 of Round 2 Tier 1.

Closes lines 48-49 (reset_plugin_manager body) plus branches
37->39 (get_plugin_manager cached path) and 68->85 (no external
plugins loaded path in _create_plugin_manager).
"""

from __future__ import annotations

import pluggy
import pytest

import curator.plugins.manager as pm_mod
from curator.plugins.manager import (
    get_plugin_manager,
    reset_plugin_manager,
)


@pytest.fixture(autouse=True)
def restore_singleton():
    """Save/restore the module-level _pm singleton so these tests
    don't leak state to other tests."""
    saved = pm_mod._pm
    try:
        yield
    finally:
        pm_mod._pm = saved


def test_get_plugin_manager_returns_cached_singleton_on_repeat_calls():
    # Branch 37->39: second call sees _pm already set → skip lock entry
    # and return cached instance directly.
    reset_plugin_manager()  # ensure first call rebuilds
    first = get_plugin_manager()
    second = get_plugin_manager()
    assert first is second
    assert isinstance(first, pluggy.PluginManager)


def test_reset_plugin_manager_clears_singleton():
    # Lines 48-49: reset clears _pm under lock.
    # First populate
    get_plugin_manager()
    assert pm_mod._pm is not None
    # Reset and observe cleared
    reset_plugin_manager()
    assert pm_mod._pm is None
    # Next get rebuilds a fresh manager
    fresh = get_plugin_manager()
    assert fresh is not None


def test_create_plugin_manager_swallows_plugin_init_exception(monkeypatch):
    # Lines 87-91 (87 is the `try:` already covered; 88-95 are the
    # except body): when one plugin's curator_plugin_init raises,
    # the catch block logs a warning and lets startup continue.
    #
    # Inject a misbehaving plugin via a patched register_core_plugins
    # so it's registered BEFORE pm.hook.curator_plugin_init is called.
    import curator.plugins.core as core_mod

    hookimpl = pluggy.HookimplMarker("curator")

    class _BadInitPlugin:
        @hookimpl
        def curator_plugin_init(self, pm):
            raise RuntimeError("init boom")

    orig_register = core_mod.register_core_plugins

    def patched_register(pm):
        orig_register(pm)
        pm.register(_BadInitPlugin(), name="bad_init_plugin")

    monkeypatch.setattr(core_mod, "register_core_plugins", patched_register)
    reset_plugin_manager()
    # Must not raise despite the misbehaving plugin.
    pm = get_plugin_manager()
    assert pm is not None


def test_create_plugin_manager_when_no_external_plugins_loaded(monkeypatch):
    # Branch 68->85 False arm: load_setuptools_entrypoints returns 0 →
    # skip the debug-log block, fall through to the plugin_init try.
    # Patch PluginManager.load_setuptools_entrypoints to return 0
    # before invocation.
    orig_load = pluggy.PluginManager.load_setuptools_entrypoints

    def patched_load(self, group, name=None):
        return 0  # pretend no external plugins installed

    monkeypatch.setattr(
        pluggy.PluginManager,
        "load_setuptools_entrypoints",
        patched_load,
    )
    reset_plugin_manager()
    pm = get_plugin_manager()
    assert pm is not None
    # Core plugins are still registered (they don't go through
    # load_setuptools_entrypoints), so the manager is usable.
    assert isinstance(pm, pluggy.PluginManager)
