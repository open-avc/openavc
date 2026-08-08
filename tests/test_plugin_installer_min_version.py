"""Tests for plugin install min_openavc_version enforcement (A32).

Before A32, the installer downloaded the plugin, installed pip deps,
and even ran _register_installed_plugin before any version check ran.
Mismatch surfaced only when the user tried to enable the plugin, by
which point we'd already mutated the environment.

The fix: parse the plugin's PLUGIN_INFO via AST after download, BEFORE
installing pip/native deps, and raise if the running OpenAVC is older
than the plugin's min_openavc_version.
"""

from unittest.mock import patch

import pytest

from openavc.core.plugin_installer import (
    _check_min_openavc_version,
    _extract_min_openavc_version,
)


def _write_plugin(tmp_path, body: str):
    plugin_dir = tmp_path / "fake_plugin"
    plugin_dir.mkdir()
    (plugin_dir / "fake_plugin.py").write_text(body, encoding="utf-8")
    return plugin_dir


def test_extracts_min_version_from_plugin_info(tmp_path):
    plugin_dir = _write_plugin(tmp_path, '''
class FakePlugin:
    PLUGIN_INFO = {
        "id": "fake",
        "min_openavc_version": "0.10.0",
    }
''')
    assert _extract_min_openavc_version(plugin_dir) == "0.10.0"


def test_returns_none_when_plugin_omits_min_version(tmp_path):
    plugin_dir = _write_plugin(tmp_path, '''
class FakePlugin:
    PLUGIN_INFO = {"id": "fake"}
''')
    assert _extract_min_openavc_version(plugin_dir) is None


def test_check_raises_when_plugin_requires_newer_openavc(tmp_path):
    plugin_dir = _write_plugin(tmp_path, '''
class FakePlugin:
    PLUGIN_INFO = {"id": "fake", "min_openavc_version": "99.0.0"}
''')
    with pytest.raises(ValueError, match=r"requires OpenAVC v99\.0\.0"):
        _check_min_openavc_version("fake", plugin_dir)


def test_check_passes_when_running_version_is_newer(tmp_path):
    plugin_dir = _write_plugin(tmp_path, '''
class FakePlugin:
    PLUGIN_INFO = {"id": "fake", "min_openavc_version": "0.0.1"}
''')
    # Must not raise.
    _check_min_openavc_version("fake", plugin_dir)


def test_check_passes_for_matching_version(tmp_path):
    plugin_dir = _write_plugin(tmp_path, '''
class FakePlugin:
    PLUGIN_INFO = {"id": "fake", "min_openavc_version": "1.2.3"}
''')
    with patch("openavc.version.__version__", "1.2.3"):
        # Same version → "less than" is False → must not raise.
        _check_min_openavc_version("fake", plugin_dir)


def test_check_no_op_when_plugin_omits_min_version(tmp_path):
    plugin_dir = _write_plugin(tmp_path, '''
class FakePlugin:
    PLUGIN_INFO = {"id": "fake", "version": "0.1.0"}
''')
    # Must not raise.
    _check_min_openavc_version("fake", plugin_dir)


def test_check_tolerates_malformed_version_string(tmp_path):
    """A bad min_openavc_version string is a plugin authoring bug — install
    must not block on it (validate_manifest will surface it on enable).
    """
    plugin_dir = _write_plugin(tmp_path, '''
class FakePlugin:
    PLUGIN_INFO = {"id": "fake", "min_openavc_version": "not-a-version"}
''')
    _check_min_openavc_version("fake", plugin_dir)


def test_check_skips_files_without_the_field(tmp_path):
    """Don't waste AST parsing on files that don't even mention the field —
    the substring guard keeps this cheap for large plugin directories.
    """
    plugin_dir = _write_plugin(tmp_path, "# nothing useful here\n")
    assert _extract_min_openavc_version(plugin_dir) is None
