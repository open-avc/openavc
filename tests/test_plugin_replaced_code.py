"""Replaced plugin source must win over timestamp caches and helper modules."""

import importlib.util
import os
from pathlib import Path
import sys
from unittest.mock import AsyncMock

import pytest

from openavc.core import plugin_installer as installer
from openavc.core.plugin_loader import _purge_plugin_modules
from tests.test_plugin_installer_hardening import _plugin_src


@pytest.fixture
def installed(tmp_path, monkeypatch):
    previous = dict(installer._PLUGIN_CLASS_REGISTRY)
    monkeypatch.setattr(installer, "PLUGIN_REPO_DIR", tmp_path)
    directory = tmp_path / "cache_unit"
    directory.mkdir()
    yield directory
    _purge_plugin_modules("plugin_cache_unit")
    installer._PLUGIN_CLASS_REGISTRY.clear()
    installer._PLUGIN_CLASS_REGISTRY.update(previous)


def replace(directory, version, value, lazy=False, entry="cache_unit_plugin.py"):
    (directory / "helper.py").write_text(f"VALUE = '{value}'\n")
    source = _plugin_src("cache_unit", version) + "\n"
    source += ("def read():\n    from .helper import VALUE\n    return VALUE\n" if lazy
               else "from .helper import VALUE\ndef read():\n    return VALUE\n")
    source += "ThePlugin.read = staticmethod(read)\n"
    (directory / entry).write_text(source)
    # Make the collision deterministic: equal sizes and the same integer mtime.
    for path in directory.glob("*.py"):
        os.utime(path, (1700000000, 1700000000))


@pytest.mark.parametrize("external_cache", [False, True])
@pytest.mark.parametrize("lazy", [False, True])
@pytest.mark.parametrize("entry", ["cache_unit_plugin.py", "__init__.py"])
def test_replacement_refreshes_entry_and_helpers(installed, tmp_path, monkeypatch, external_cache, lazy, entry):
    monkeypatch.setattr(sys, "pycache_prefix", str(tmp_path / "external-cache") if external_cache else None)
    replace(installed, "1.0.0", "old", lazy, entry)
    assert installer._register_installed_plugin("cache_unit", installed) is None
    previous = installer._PLUGIN_CLASS_REGISTRY["cache_unit"]
    assert previous.read() == "old"
    cache = Path(importlib.util.cache_from_source(str(installed / "helper.py")))
    assert cache.exists(), "The test must actually populate the cache it challenges"
    replace(installed, "2.0.0", "new", lazy, entry)
    assert installer._register_installed_plugin("cache_unit", installed) is None
    current = installer._PLUGIN_CLASS_REGISTRY["cache_unit"]
    assert current.PLUGIN_INFO["version"] == "2.0.0"
    assert current.read() == "new"


async def test_cache_permission_failure_keeps_working_installation(installed, tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "pycache_prefix", str(tmp_path / "external-cache"))
    replace(installed, "1.0.0", "old")
    assert installer._register_installed_plugin("cache_unit", installed) is None
    previous = installer._PLUGIN_CLASS_REGISTRY["cache_unit"]
    source = (installed / "cache_unit_plugin.py").read_bytes()
    original_unlink = Path.unlink

    def refuse_cache(path, *args, **kwargs):
        if "external-cache" in path.parts:
            raise PermissionError("cache is read-only")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", refuse_cache)
    download = AsyncMock(return_value={"status": "installed"})
    monkeypatch.setattr(installer, "_do_install", download)
    with pytest.raises(ValueError, match="cache directory"):
        await installer.update_plugin("cache_unit", "https://example.invalid/unused.zip")
    download.assert_not_awaited()
    assert installer._PLUGIN_CLASS_REGISTRY["cache_unit"] is previous
    assert (installed / "cache_unit_plugin.py").read_bytes() == source
    assert not (installed.parent / ".cache_unit.update-bak").exists()
