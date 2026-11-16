"""Tests for plugin install min_openavc_version enforcement (A32).

Before A32, the installer downloaded the plugin, installed pip deps,
and even ran _register_installed_plugin before any version check ran.
Mismatch surfaced only when the user tried to enable the plugin, by
which point we'd already mutated the environment.

The fix: read the plugin's PLUGIN_INFO from its source after download
(core/plugin_source.py), BEFORE installing pip/native deps, and raise if
the running OpenAVC is older than the plugin's min_openavc_version.
"""

from unittest.mock import patch

import pytest

from openavc.core.plugin_installer import (
    _check_min_openavc_version,
    _read_declarations,
)


def _write_plugin(tmp_path, body: str):
    plugin_dir = tmp_path / "fake_plugin"
    plugin_dir.mkdir()
    (plugin_dir / "fake_plugin.py").write_text(body, encoding="utf-8")
    return plugin_dir


def _check(tmp_path, body: str) -> None:
    _check_min_openavc_version("fake", _read_declarations(_write_plugin(tmp_path, body)))


def test_reads_min_version_from_plugin_info(tmp_path):
    plugin_dir = _write_plugin(tmp_path, '''
class FakePlugin:
    PLUGIN_INFO = {
        "id": "fake",
        "min_openavc_version": "0.10.0",
    }
''')
    assert _read_declarations(plugin_dir).info["min_openavc_version"] == "0.10.0"


def test_check_raises_when_plugin_requires_newer_openavc(tmp_path):
    with pytest.raises(ValueError, match=r"requires OpenAVC v99\.0\.0"):
        _check(tmp_path, '''
class FakePlugin:
    PLUGIN_INFO = {"id": "fake", "min_openavc_version": "99.0.0"}
''')


def test_check_passes_when_running_version_is_newer(tmp_path):
    # Must not raise.
    _check(tmp_path, '''
class FakePlugin:
    PLUGIN_INFO = {"id": "fake", "min_openavc_version": "0.0.1"}
''')


def test_check_passes_for_matching_version(tmp_path):
    with patch("openavc.version.__version__", "1.2.3"):
        # Same version → "less than" is False → must not raise.
        _check(tmp_path, '''
class FakePlugin:
    PLUGIN_INFO = {"id": "fake", "min_openavc_version": "1.2.3"}
''')


def test_check_no_op_when_plugin_omits_min_version(tmp_path):
    # Must not raise.
    _check(tmp_path, '''
class FakePlugin:
    PLUGIN_INFO = {"id": "fake", "version": "0.1.0"}
''')


def test_check_tolerates_malformed_version_string(tmp_path):
    """A bad min_openavc_version string is a plugin authoring bug — install
    must not block on it (validate_manifest will surface it on enable).
    """
    _check(tmp_path, '''
class FakePlugin:
    PLUGIN_INFO = {"id": "fake", "min_openavc_version": "not-a-version"}
''')


def test_check_tolerates_a_version_that_is_not_text(tmp_path):
    _check(tmp_path, '''
class FakePlugin:
    PLUGIN_INFO = {"id": "fake", "min_openavc_version": 99}
''')


def test_a_folder_with_no_plugin_class_declares_nothing(tmp_path):
    plugin_dir = _write_plugin(tmp_path, "# nothing useful here\n")
    assert _read_declarations(plugin_dir) is None
    _check_min_openavc_version("fake", None)
