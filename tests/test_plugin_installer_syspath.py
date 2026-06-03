"""Tests for plugin import isolation (A36 + A388).

`_register_installed_plugin` imports a plugin through `_exec_plugin_in_package`,
which loads each plugin inside its own ``plugin_<dir>`` package namespace
instead of dropping the plugin's directory onto the bare ``sys.path``. That
gives two properties:

  - A36: a failed import leaves nothing behind — no stray ``sys.path`` entry,
    no half-loaded modules in ``sys.modules``.
  - A388: a multi-file plugin's helper modules land under ``plugin_<dir>.<name>``
    via relative imports, so two plugins that each ship a same-named helper
    (``shared.py``) can't shadow each other under a bare ``sys.modules`` key.
"""

import sys

import pytest

from server.core.plugin_installer import _register_installed_plugin
from server.core.plugin_loader import unregister_plugin_class


def _make_plugin(tmp_path, name: str, content: str, extra: dict | None = None):
    plugin_dir = tmp_path / name
    plugin_dir.mkdir()
    (plugin_dir / f"{name}_plugin.py").write_text(content, encoding="utf-8")
    for fname, fcontent in (extra or {}).items():
        (plugin_dir / fname).write_text(fcontent, encoding="utf-8")
    return plugin_dir


@pytest.fixture(autouse=True)
def _restore_import_state():
    """Snapshot sys.path + plugin sys.modules before each test, restore after."""
    path_snapshot = list(sys.path)
    module_snapshot = set(sys.modules)
    yield
    sys.path[:] = path_snapshot
    for name in set(sys.modules) - module_snapshot:
        if name.startswith("plugin_"):
            sys.modules.pop(name, None)


def test_failed_exec_module_leaves_no_trace(tmp_path):
    """A plugin whose top-level code raises during import must leave nothing
    behind — no sys.path entry and no half-loaded package in sys.modules.
    """
    plugin_dir = _make_plugin(
        tmp_path, "broken",
        "raise RuntimeError('boom at module load')\n",
    )

    result = _register_installed_plugin("broken", plugin_dir)

    # A60 return contract: None on success, error-message str on failure.
    assert result is not None
    assert "RuntimeError" in result
    assert str(plugin_dir) not in sys.path
    assert "plugin_broken" not in sys.modules
    assert not any(m.startswith("plugin_broken.") for m in sys.modules)


def test_no_plugin_info_match_cleans_up(tmp_path):
    """The 'no PLUGIN_INFO class found' exit path purges the package it
    loaded so a later candidate/install starts clean.
    """
    plugin_dir = _make_plugin(
        tmp_path, "nothing",
        "# valid module, no plugin class\nVALUE = 1\n",
    )

    result = _register_installed_plugin("nothing", plugin_dir)

    assert result is not None
    assert "PLUGIN_INFO" in result
    assert str(plugin_dir) not in sys.path
    assert "plugin_nothing" not in sys.modules


def test_successful_registration_does_not_pollute_sys_path(tmp_path):
    """A successful load registers the plugin under its package namespace and
    leaves the bare directory off sys.path (the isolation guarantee).
    """
    plugin_dir = _make_plugin(
        tmp_path, "good",
        '''
class GoodPlugin:
    PLUGIN_INFO = {"id": "good"}
    async def start(self, api): pass
    async def stop(self): pass
''',
    )

    try:
        result = _register_installed_plugin("good", plugin_dir)
        assert result is None
        # The bare dir is NOT added to sys.path — siblings resolve via the
        # plugin_good package instead.
        assert str(plugin_dir) not in sys.path
        assert "plugin_good.good_plugin" in sys.modules
    finally:
        unregister_plugin_class("good")
        for name in [m for m in sys.modules if m.startswith("plugin_good")]:
            sys.modules.pop(name, None)


def test_multi_file_plugin_loads_via_relative_import(tmp_path):
    """A plugin split across files importing a sibling with a relative import
    loads, and the sibling lands under the plugin's package namespace.
    """
    plugin_dir = _make_plugin(
        tmp_path, "multi",
        '''
from . import shared

class MultiPlugin:
    PLUGIN_INFO = {"id": "multi"}
    MARKER = shared.MARKER
    async def start(self, api): pass
    async def stop(self): pass
''',
        extra={"shared.py": "MARKER = 'multi-shared'\n"},
    )

    try:
        result = _register_installed_plugin("multi", plugin_dir)
        assert result is None
        assert "plugin_multi.shared" in sys.modules
        # The sibling is namespaced, not bare.
        assert sys.modules.get("shared") is None
    finally:
        unregister_plugin_class("multi")
        for name in [m for m in sys.modules if m.startswith("plugin_multi")]:
            sys.modules.pop(name, None)


def test_same_named_helpers_do_not_collide(tmp_path):
    """Two plugins that each ship their own ``shared.py`` must each see their
    own helper — not whichever loaded first (A388).
    """
    alpha_dir = _make_plugin(
        tmp_path, "alpha",
        '''
from . import shared

class AlphaPlugin:
    PLUGIN_INFO = {"id": "alpha"}
    VALUE = shared.VALUE
    async def start(self, api): pass
    async def stop(self): pass
''',
        extra={"shared.py": "VALUE = 'alpha'\n"},
    )
    beta_dir = _make_plugin(
        tmp_path, "beta",
        '''
from . import shared

class BetaPlugin:
    PLUGIN_INFO = {"id": "beta"}
    VALUE = shared.VALUE
    async def start(self, api): pass
    async def stop(self): pass
''',
        extra={"shared.py": "VALUE = 'beta'\n"},
    )

    try:
        assert _register_installed_plugin("alpha", alpha_dir) is None
        assert _register_installed_plugin("beta", beta_dir) is None

        from server.core.plugin_loader import _PLUGIN_CLASS_REGISTRY

        # Each plugin captured its OWN helper's value — no cross-contamination.
        assert _PLUGIN_CLASS_REGISTRY["alpha"].VALUE == "alpha"
        assert _PLUGIN_CLASS_REGISTRY["beta"].VALUE == "beta"
        assert sys.modules["plugin_alpha.shared"].VALUE == "alpha"
        assert sys.modules["plugin_beta.shared"].VALUE == "beta"
    finally:
        for pid in ("alpha", "beta"):
            unregister_plugin_class(pid)
            for name in [m for m in sys.modules if m.startswith(f"plugin_{pid}")]:
                sys.modules.pop(name, None)


def test_does_not_touch_preexisting_sys_path_entry(tmp_path):
    """If the plugin dir is already on sys.path (rare), the loader neither
    removes it nor adds a duplicate — it manages its own package namespace,
    not the bare path.
    """
    plugin_dir = _make_plugin(
        tmp_path, "preadded",
        "raise RuntimeError('boom')\n",
    )

    sys.path.insert(0, str(plugin_dir))
    assert sys.path.count(str(plugin_dir)) == 1

    _register_installed_plugin("preadded", plugin_dir)

    # The pre-existing entry we didn't add is left exactly as it was.
    assert sys.path.count(str(plugin_dir)) == 1
