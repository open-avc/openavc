"""Tests for plugin install sys.path cleanup on failure (A36).

`_register_installed_plugin` inserts the plugin's directory into sys.path
so importlib can resolve the plugin's submodules. Before A36, that
insert was only cleaned up on the "no PLUGIN_INFO class found" exit
path — not on exec_module() exceptions. A failed install would leave
the plugin directory permanently on sys.path, and a later install
with a colliding submodule name could silently pick up files from the
failed plugin.
"""

import sys

import pytest

from server.core.plugin_installer import _register_installed_plugin


def _make_plugin(tmp_path, name: str, content: str):
    plugin_dir = tmp_path / name
    plugin_dir.mkdir()
    (plugin_dir / f"{name}_plugin.py").write_text(content, encoding="utf-8")
    return plugin_dir


@pytest.fixture(autouse=True)
def _restore_sys_path():
    """Snapshot sys.path before each test, restore after."""
    snapshot = list(sys.path)
    yield
    sys.path[:] = snapshot


def test_failed_exec_module_does_not_leave_dir_on_sys_path(tmp_path):
    """Regression for A36: a plugin whose top-level code raises during
    exec_module must not leave its directory on sys.path. Without the
    try/finally, the entry persisted forever.
    """
    plugin_dir = _make_plugin(
        tmp_path, "broken",
        "raise RuntimeError('boom at module load')\n",
    )

    assert str(plugin_dir) not in sys.path
    result = _register_installed_plugin("broken", plugin_dir)

    assert result is False
    assert str(plugin_dir) not in sys.path, (
        "_register_installed_plugin left the plugin directory on sys.path "
        "after exec_module raised — subsequent plugin installs with "
        "colliding submodule names will pick up files from this failed plugin."
    )


def test_no_plugin_info_match_cleans_up_sys_path(tmp_path):
    """The 'no PLUGIN_INFO class found' exit path must also clean up
    (this worked before A36, must keep working after).
    """
    plugin_dir = _make_plugin(
        tmp_path, "nothing",
        "# valid module, no plugin class\nVALUE = 1\n",
    )

    assert str(plugin_dir) not in sys.path
    result = _register_installed_plugin("nothing", plugin_dir)

    assert result is False
    assert str(plugin_dir) not in sys.path


def test_successful_registration_keeps_dir_on_sys_path(tmp_path):
    """On successful registration, the plugin's dir must stay on sys.path
    so subsequent imports of the plugin's submodules work. This is the
    one case where we intentionally don't clean up.
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

    assert str(plugin_dir) not in sys.path
    try:
        result = _register_installed_plugin("good", plugin_dir)
        assert result is True
        assert str(plugin_dir) in sys.path, (
            "Successful registration should leave plugin dir on sys.path "
            "so the plugin's submodule imports continue to work."
        )
    finally:
        # Unregister so the test doesn't leak global state.
        from server.core.plugin_loader import unregister_plugin_class
        unregister_plugin_class("good")


def test_does_not_double_insert_when_dir_already_on_path(tmp_path):
    """If the plugin dir is already on sys.path (rare but possible),
    don't add a duplicate, and on cleanup don't remove the pre-existing
    entry that wasn't ours to add.
    """
    plugin_dir = _make_plugin(
        tmp_path, "preadded",
        "raise RuntimeError('boom')\n",
    )

    sys.path.insert(0, str(plugin_dir))
    initial_count = sys.path.count(str(plugin_dir))
    assert initial_count == 1

    _register_installed_plugin("preadded", plugin_dir)

    # Still present (we didn't add it, so finally shouldn't remove it),
    # and only once.
    assert sys.path.count(str(plugin_dir)) == 1
