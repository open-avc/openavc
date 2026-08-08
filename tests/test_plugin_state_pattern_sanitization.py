"""A71 — PluginLoader._sanitize_state_pattern keeps plugins inside their
``plugin.<id>.*`` namespace so a plugin sidebar can't scrape unrelated state.
"""

from openavc.core.plugin_loader import PluginLoader


def _san(pattern, plugin_id="dante"):
    return PluginLoader._sanitize_state_pattern(
        pattern, plugin_id, f"plugin.{plugin_id}."
    )


def test_accepts_basic_namespace_pattern():
    assert _san("plugin.dante.*") == "plugin.dante.*"


def test_accepts_nested_pattern():
    assert _san("plugin.dante.device.*") == "plugin.dante.device.*"


def test_accepts_pattern_with_placeholder():
    assert (
        _san("plugin.dante.device.{device_id}.*") == "plugin.dante.device.{device_id}.*"
    )


def test_rewrites_global_wildcard():
    assert _san("*") == "plugin.dante.*"


def test_rewrites_other_namespace():
    assert _san("device.*") == "plugin.dante.*"


def test_rewrites_neighbor_namespace():
    # plugin.dant* would match plugin.dante.* AND plugin.dantelite.* — a
    # neighbor plugin's state should not leak.
    assert _san("plugin.dant.*") == "plugin.dante.*"


def test_rewrites_empty_string():
    assert _san("") == "plugin.dante.*"


def test_rewrites_whitespace_only():
    assert _san("   ") == "plugin.dante.*"


def test_rewrites_non_string():
    assert _san(None) == "plugin.dante.*"
    assert _san(42) == "plugin.dante.*"
    assert _san(["plugin.dante.*"]) == "plugin.dante.*"
