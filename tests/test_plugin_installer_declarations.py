"""What the installer reads from a plugin before it installs anything for it.

After the download and before any dependency is installed, the installer
reads the plugin's declarations from source (``core/plugin_source.py``), once,
from the file the loader would import (``plugin_import._candidate_files``):
its ``PLUGIN_INFO``'s ``min_openavc_version``, ``dependencies`` and
``native_dependencies``, and nothing else in the folder. A value the source
computes when the plugin runs cannot be read; nothing is installed or checked
for it, and the log says which value.

These tests run a whole install with the download replaced by files written
into the plugin's folder, and record what reaches the two dependency
installers.
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openavc.core import plugin_installer as pi

CATALOG = "https://raw.githubusercontent.com/open-avc/openavc-plugins/main"

PLUGIN_ID = "acme_widget"
MAIN = f"{PLUGIN_ID}_plugin.py"


def _plugin(info_extra: str = "", *, before: str = "", body: str = "") -> str:
    """A plugin module: ``before`` at module level, then the class, whose
    PLUGIN_INFO gets ``info_extra`` (dict entries) and whose body gets
    ``body``."""
    return f'''{before}
class AcmeWidgetPlugin:
    PLUGIN_INFO = {{
        "id": "{PLUGIN_ID}",
        "name": "Acme Widget",
        "version": "1.0.0",
        "author": "Acme",
        "description": "Acme widget bridge.",
        "category": "utility",
        "license": "MIT",
        {info_extra}
    }}
{body}
    async def start(self, api):
        pass

    async def stop(self):
        pass
'''


class _Installed:
    """What one install handed to the dependency installers."""

    def __init__(self, result, pip: AsyncMock, native: AsyncMock):
        self.result = result
        self._pip = pip
        self._native = native

    @property
    def requirements(self) -> list[str] | None:
        if not self._pip.await_count:
            return None
        return list(self._pip.await_args.args[0])

    @property
    def native_deps(self) -> list[dict] | None:
        if not self._native.await_count:
            return None
        return list(self._native.await_args.args[1])


async def _install(tmp_path, files: dict[str, str]) -> _Installed:
    async def download(_client, _repo_path, dest_dir, **_kwargs):
        for name, text in files.items():
            (dest_dir / name).write_text(text, encoding="utf-8")

    catalog = MagicMock()
    pip = AsyncMock()
    native = AsyncMock()
    with (
        patch.object(pi, "PLUGIN_REPO_DIR", tmp_path),
        patch.object(pi, "_plugin_catalog_check", AsyncMock(return_value=catalog)),
        patch.object(pi, "_download_github_directory", download),
        patch.object(pi, "install_requirements", pip),
        patch.object(pi, "install_native_deps", native),
        patch.object(pi, "_register_installed_plugin", return_value=None),
    ):
        result = await pi.install_plugin(PLUGIN_ID, f"{CATALOG}/plugins/{PLUGIN_ID}")
    return _Installed(result, pip, native)


NATIVE_DEP = {
    "id": "acme_sdk",
    "name": "Acme SDK",
    "license": "MIT",
    "required": True,
    "platforms": {"linux_x64": {"type": "command", "command": "true"}},
}


# --- only PLUGIN_INFO, only in the plugin's own file ---------------------------


async def test_a_settings_field_with_a_dependencies_key_installs_nothing(tmp_path):
    installed = await _install(tmp_path, {
        MAIN: _plugin(body='''
    CONFIG_SCHEMA = {
        "zones": {"type": "string", "label": "Zones", "dependencies": ["mode"]},
    }
'''),
    })
    assert installed.result["status"] == "installed"
    assert installed.requirements is None


async def test_a_helper_modules_dependencies_install_nothing(tmp_path):
    installed = await _install(tmp_path, {
        MAIN: _plugin('"dependencies": ["acme-lib>=1.0"],'),
        "recipes.py": 'RECIPE = {"dependencies": ["not-a-plugin-dependency"]}\n',
        "_tables.py": 'TABLE = {"dependencies": ["nor-this-one"]}\n',
    })
    assert installed.requirements == ["acme-lib>=1.0"]


async def test_a_min_version_outside_plugin_info_does_not_refuse_the_install(tmp_path):
    installed = await _install(tmp_path, {
        MAIN: _plugin(),
        "compat.py": 'NEWER_FEATURES = {"min_openavc_version": "99.0.0"}\n',
    })
    assert installed.result["status"] == "installed"


async def test_native_dependencies_in_a_helper_module_install_nothing(tmp_path):
    installed = await _install(tmp_path, {
        MAIN: _plugin(),
        "catalog.py": f"KNOWN = {{'native_dependencies': [{NATIVE_DEP!r}]}}\n",
    })
    assert installed.native_deps is None


# --- the values PLUGIN_INFO holds, as the loader would find them ---------------


async def test_dependencies_bound_through_a_name_install(tmp_path):
    installed = await _install(tmp_path, {
        MAIN: _plugin('"dependencies": _REQUIREMENTS,', before='_REQUIREMENTS = ["acme-lib>=1.0"]'),
    })
    assert installed.requirements == ["acme-lib>=1.0"]


async def test_a_min_version_bound_through_a_name_refuses_the_install(tmp_path):
    with pytest.raises(ValueError, match=r"requires OpenAVC v99\.0\.0"):
        await _install(tmp_path, {
            MAIN: _plugin('"min_openavc_version": _FLOOR,', before='_FLOOR = "99.0.0"'),
        })
    assert not (tmp_path / PLUGIN_ID).exists()


async def test_native_dependencies_bound_through_a_name_install(tmp_path):
    installed = await _install(tmp_path, {
        MAIN: _plugin('"native_dependencies": [_SDK],', before=f"_SDK = {NATIVE_DEP!r}"),
    })
    assert installed.native_deps == [NATIVE_DEP]


async def test_a_module_level_plugin_info_bound_into_the_class_is_read(tmp_path):
    source = f'''
PLUGIN_INFO = {{
    "id": "{PLUGIN_ID}",
    "name": "Acme Widget",
    "version": "1.0.0",
    "author": "Acme",
    "description": "Acme widget bridge.",
    "category": "utility",
    "license": "MIT",
    "dependencies": ["acme-lib>=1.0"],
    "native_dependencies": [{NATIVE_DEP!r}],
}}


class AcmeWidgetPlugin:
    PLUGIN_INFO = PLUGIN_INFO
'''
    installed = await _install(tmp_path, {MAIN: source})
    assert installed.requirements == ["acme-lib>=1.0"]
    assert installed.native_deps == [NATIVE_DEP]


async def test_a_module_level_plugin_info_with_no_class_installs_nothing(tmp_path):
    """The loader starts a class, so a dict with no class is no plugin's."""
    installed = await _install(tmp_path, {
        MAIN: 'PLUGIN_INFO = {"id": "acme_widget", "dependencies": ["acme-lib"]}\n',
    })
    assert installed.requirements is None


# --- what cannot be read from the text ----------------------------------------


async def test_computed_dependencies_install_nothing_and_say_so(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger="openavc.core.plugin_installer"):
        installed = await _install(tmp_path, {
            MAIN: _plugin('"dependencies": _requirements(),', before="def _requirements():\n    return ['acme-lib']"),
        })
    assert installed.result["status"] == "installed"
    assert installed.requirements is None
    assert "PLUGIN_INFO['dependencies']" in caplog.text


async def test_computed_keys_leave_the_dependencies_unknown_and_say_so(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger="openavc.core.plugin_installer"):
        installed = await _install(tmp_path, {
            MAIN: _plugin("**_extra(),", before="def _extra():\n    return {'dependencies': ['acme-lib']}"),
        })
    assert installed.requirements is None
    assert installed.native_deps is None
    assert "PLUGIN_INFO['dependencies']" in caplog.text


async def test_a_computed_min_version_does_not_refuse_the_install(tmp_path):
    installed = await _install(tmp_path, {
        MAIN: _plugin('"min_openavc_version": ".".join(["99", "0", "0"]),'),
    })
    assert installed.result["status"] == "installed"


async def test_dependencies_written_as_one_string_are_not_split_into_letters(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger="openavc.core.plugin_installer"):
        installed = await _install(tmp_path, {MAIN: _plugin('"dependencies": "acme-lib",')})
    assert installed.requirements is None
    assert "PLUGIN_INFO['dependencies']" in caplog.text


async def test_a_dependency_that_is_not_text_is_left_out_and_said(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger="openavc.core.plugin_installer"):
        installed = await _install(tmp_path, {MAIN: _plugin('"dependencies": ["acme-lib", 7],')})
    assert installed.requirements == ["acme-lib"]
    assert "PLUGIN_INFO['dependencies']" in caplog.text


async def test_a_native_dependency_that_is_not_a_dict_is_left_out_and_said(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger="openavc.core.plugin_installer"):
        installed = await _install(tmp_path, {
            MAIN: _plugin(f'"native_dependencies": [{NATIVE_DEP!r}, "acme_sdk"],'),
        })
    assert installed.native_deps == [NATIVE_DEP]
    assert "PLUGIN_INFO['native_dependencies']" in caplog.text


# --- one read, of the loader's file ------------------------------------------------


async def test_the_plugin_is_read_once(tmp_path):
    reads = []
    real = pi.read_source

    def counting(text):
        reads.append(text)
        return real(text)

    with patch.object(pi, "read_source", counting):
        installed = await _install(tmp_path, {
            MAIN: _plugin('"dependencies": ["acme-lib"], "min_openavc_version": "0.1.0",'),
            "helper.py": "VALUE = 1\n",
        })
    assert installed.requirements == ["acme-lib"]
    assert len(reads) == 1


async def test_a_file_that_does_not_parse_is_passed_over_as_the_loader_does(tmp_path):
    installed = await _install(tmp_path, {
        "__init__.py": "def broken(\n",
        MAIN: _plugin('"dependencies": ["acme-lib"],'),
    })
    assert installed.requirements == ["acme-lib"]


async def test_the_loaders_first_file_wins(tmp_path):
    """``__init__.py`` comes before ``<folder>_plugin.py``, as at start."""
    installed = await _install(tmp_path, {
        "__init__.py": _plugin('"dependencies": ["acme-lib"],'),
        MAIN: _plugin('"dependencies": ["not-the-loaders-file"],'),
    })
    assert installed.requirements == ["acme-lib"]


def test_registration_imports_each_file_once(tmp_path):
    """The file the loader names first is not imported a second time when it fails."""
    plugin_dir = tmp_path / PLUGIN_ID
    plugin_dir.mkdir()
    runs = tmp_path / "runs.txt"
    (plugin_dir / MAIN).write_text(
        f"open({str(runs)!r}, 'a').write('x')\nraise RuntimeError('acme broke')\n",
        encoding="utf-8",
    )
    with patch.object(pi, "PLUGIN_REPO_DIR", tmp_path):
        error = pi._register_installed_plugin(PLUGIN_ID, plugin_dir)
    assert "acme broke" in error
    assert runs.read_text() == "x"
