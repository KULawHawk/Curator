"""Unit tests for the curator_plugin_init hookspec (v1.1.2+).

Tests the ``_create_plugin_manager`` lifecycle: the one-shot init hook
fires after all plugins are registered, and a plugin's init raising
doesn't crash startup. See ``docs/PLUGIN_INIT_HOOKSPEC_DESIGN.md`` v0.2
for the design + four ratified DMs.

These tests don't go through ``build_runtime`` -- they exercise
``_create_plugin_manager`` directly by registering a fake
hookimpl-bearing plugin via the same path entry-point discovery would
take. Keeps the unit tests fast and isolated.
"""

from __future__ import annotations

from unittest.mock import patch

import pluggy

from curator.plugins import hookimpl, hookspecs
from curator.plugins.manager import _create_plugin_manager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_init_recorder():
    """Build a plugin instance whose curator_plugin_init records every call.

    Returns ``(plugin_instance, calls_list)``. Each invocation of the
    init hook appends a dict {pm, all_plugin_names, all_plugin_count}
    to the calls list, so tests can assert the timing-related
    invariants (per DM-2: hook fires AFTER all plugins are registered).
    """
    calls: list[dict] = []

    class _InitRecorder:
        @hookimpl
        def curator_plugin_init(self, pm):
            # Snapshot the pm state at the moment init fires
            calls.append({
                "pm": pm,
                "all_plugin_names": [n for n, _ in pm.list_name_plugin()],
                "all_plugin_count": len(pm.list_name_plugin()),
            })

    return _InitRecorder(), calls


def _make_init_raiser(message="simulated init failure"):
    """Build a plugin whose curator_plugin_init raises an exception.

    Used to verify DM-3: an init failure is caught and logged but does
    not abort startup or de-register the misbehaving plugin.
    """
    class _InitRaiser:
        @hookimpl
        def curator_plugin_init(self, pm):
            raise RuntimeError(message)

    return _InitRaiser()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPluginInitFiresOnce:
    """Per DM-4: init hook fires exactly once per pm at startup.
    Plugins registered dynamically afterwards do NOT receive the hook.
    """

    def test_init_fires_once_via_create_plugin_manager(self):
        """When _create_plugin_manager runs and discovers a plugin via
        load_setuptools_entrypoints, the plugin's init hookimpl fires
        exactly once."""
        recorder, calls = _make_init_recorder()

        # Patch load_setuptools_entrypoints so the recorder is "discovered"
        # as if it had a setuptools entry point. This avoids needing to
        # actually pip-install anything for the test.
        original_load = pluggy.PluginManager.load_setuptools_entrypoints

        def fake_load(self, group, name=None):
            if group == "curator":
                self.register(recorder, name="init_recorder")
                return 1
            return original_load(self, group, name)

        with patch.object(
            pluggy.PluginManager, "load_setuptools_entrypoints",
            fake_load,
        ):
            pm = _create_plugin_manager()

        assert len(calls) == 1
        assert calls[0]["pm"] is pm

    def test_dynamic_register_after_startup_does_not_fire_init(self):
        """Per DM-4: plugins registered via pm.register() AFTER
        _create_plugin_manager has returned do NOT get the init hook
        fired again. They have to use other mechanisms (e.g. constructor
        args) to get pm access if they need it."""
        # Build the pm normally (no recorder discovered)
        pm = _create_plugin_manager()

        # Now dynamically register a recorder plugin
        recorder, calls = _make_init_recorder()
        pm.register(recorder, name="late_recorder")

        # Init hook should NOT have fired for the late recorder
        assert calls == []


class TestPluginInitTiming:
    """Per DM-2: init hook fires AFTER all plugins are registered, so
    init hookimpls can see all siblings via pm.list_name_plugin()."""

    def test_init_sees_all_core_plugins_already_registered(self):
        """When a plugin's init hookimpl runs, the pm should already
        have all core plugins registered. The init can list them and
        do setup work that depends on them being present."""
        recorder, calls = _make_init_recorder()

        original_load = pluggy.PluginManager.load_setuptools_entrypoints

        def fake_load(self, group, name=None):
            if group == "curator":
                self.register(recorder, name="timing_recorder")
                return 1
            return original_load(self, group, name)

        with patch.object(
            pluggy.PluginManager, "load_setuptools_entrypoints",
            fake_load,
        ):
            pm = _create_plugin_manager()

        # The recorder's init saw at least the recorder itself + Curator's
        # core plugins (LocalPlugin, etc.). Exact count varies as core
        # plugins evolve; assert structurally rather than numerically.
        assert calls[0]["all_plugin_count"] >= 2  # at least recorder + 1 core
        assert "timing_recorder" in calls[0]["all_plugin_names"]
        # And whatever pm reference came in is the SAME pm instance
        assert calls[0]["pm"] is pm


class TestPluginInitFailureIsolation:
    """Per DM-3: a plugin's init hookimpl raising is caught and logged
    but does NOT abort _create_plugin_manager. The misbehaving plugin
    remains registered; subsequent hookimpls of theirs may behave oddly,
    but other plugins are unaffected."""

    def test_init_raise_does_not_crash_create(self):
        """A plugin's init raising should not propagate up out of
        _create_plugin_manager."""
        raiser = _make_init_raiser()

        original_load = pluggy.PluginManager.load_setuptools_entrypoints

        def fake_load(self, group, name=None):
            if group == "curator":
                self.register(raiser, name="raiser")
                return 1
            return original_load(self, group, name)

        with patch.object(
            pluggy.PluginManager, "load_setuptools_entrypoints",
            fake_load,
        ):
            # This MUST NOT raise.
            pm = _create_plugin_manager()

        # The misbehaving plugin is STILL registered (DM-3: log + continue,
        # don't de-register).
        assert "raiser" in [n for n, _ in pm.list_name_plugin()]

    def test_init_raise_does_not_block_other_plugins_init(self):
        """If one plugin's init raises and another plugin's init succeeds,
        the successful one should still have run."""
        raiser = _make_init_raiser()
        recorder, calls = _make_init_recorder()

        original_load = pluggy.PluginManager.load_setuptools_entrypoints

        def fake_load(self, group, name=None):
            if group == "curator":
                self.register(raiser, name="raiser")
                self.register(recorder, name="success_recorder")
                return 2
            return original_load(self, group, name)

        with patch.object(
            pluggy.PluginManager, "load_setuptools_entrypoints",
            fake_load,
        ):
            pm = _create_plugin_manager()

        # The successful recorder's init MUST have fired exactly once
        # despite the raiser failing alongside it. Pluggy invokes all
        # hookimpls; a single raise in one of them doesn't stop the
        # others (this is pluggy's default ``firstresult=False`` behavior
        # for hooks without a result-collapse policy).
        assert len(calls) == 1
        # Both plugins are registered
        names = [n for n, _ in pm.list_name_plugin()]
        assert "raiser" in names
        assert "success_recorder" in names


class TestPluginInitNoOpForSilentPlugins:
    """Strictly additive: plugins that don't implement curator_plugin_init
    are unaffected. Curator's existing core plugins (LocalPlugin etc.)
    don't implement this hookspec; they should continue to work and the
    pm should be fully functional after _create_plugin_manager returns."""

    def test_create_works_when_no_plugin_implements_init(self):
        """Without ANY plugin implementing the new hookspec, the
        _create_plugin_manager call should succeed and produce a working
        pm just like before v1.1.2."""
        # Don't patch anything -- just let _create_plugin_manager run
        # with whatever plugins are currently discoverable in the env.
        pm = _create_plugin_manager()

        # Sanity: pm has the new hookspec known
        hook_names = list(vars(pm.hook).keys())
        assert "curator_plugin_init" in hook_names
        # Sanity: pm is usable -- can list plugins without error
        plugins = pm.list_name_plugin()
        assert len(plugins) >= 1  # at least one core plugin
