"""get_driver_definition answers for both driver formats, not just YAML.

A declarative driver is a file; a Python driver is a class. The tool used to
look only at the files, so a Python driver that was installed, loaded and
serving devices came back as "not found" -- indistinguishable from "not
installed", which is what sends a caller off to reinstall or author a
duplicate for hardware that already works.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from openavc.cloud.ai_tool_handler import AIToolHandler
from openavc.drivers.base import BaseDriver
from openavc.drivers.registry import register_driver, unregister_driver


class _AcmeWidget(BaseDriver):
    """A Python-format driver: a class, with no .avcdriver file anywhere."""

    DRIVER_INFO: dict[str, Any] = {
        "id": "acme_widget",
        "name": "Acme Widget",
        "manufacturer": "Acme",
        "category": "switcher",
        "transport": "tcp",
        "commands": {"power_on": {"help": "Turn the widget on"}},
        "state_variables": {"power": {"type": "boolean"}},
    }

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        return None


def _handler() -> AIToolHandler:
    return AIToolHandler(MagicMock(), MagicMock(), MagicMock())


def _driver_dirs(tmp_path, monkeypatch):
    builtin = tmp_path / "definitions"
    repo = tmp_path / "driver_repo"
    builtin.mkdir()
    repo.mkdir()
    monkeypatch.setattr("openavc.system_config.DRIVER_DEFINITIONS_DIR", builtin)
    monkeypatch.setattr("openavc.system_config.DRIVER_REPO_DIR", repo)
    return builtin, repo


async def test_yaml_driver_is_marked_declarative_and_editable(tmp_path, monkeypatch) -> None:
    """The YAML path still returns the raw definition, now labelled."""
    _, repo = _driver_dirs(tmp_path, monkeypatch)
    (repo / "acme_yaml.avcdriver").write_text(
        "id: acme_yaml\nname: Acme YAML\ntransport: tcp\n", encoding="utf-8"
    )

    result = await _handler()._get_driver_definition({"driver_id": "acme_yaml"})

    assert "error" not in result
    assert result["id"] == "acme_yaml"
    assert result["format"] == "avcdriver"
    assert result["editable"] is True
    # The file-parse artifacts a declarative caller comes here for.
    assert "_comment_lines" in result
    assert "_source_file" not in result


async def test_python_driver_returns_its_loaded_surface(tmp_path, monkeypatch) -> None:
    """The bug: a Python driver has no file, so the file lookup misses. It now
    falls back to the loaded class instead of claiming the driver is absent."""
    _driver_dirs(tmp_path, monkeypatch)
    register_driver(_AcmeWidget)
    try:
        result = await _handler()._get_driver_definition({"driver_id": "acme_widget"})
    finally:
        unregister_driver("acme_widget")

    assert "error" not in result
    assert result["id"] == "acme_widget"
    assert result["format"] == "python"
    assert result["editable"] is False
    # The surface the caller actually wanted, same shape list_drivers returns.
    assert "power_on" in result["commands"]
    assert "power" in result["state_variables"]
    # And it says why there is no definition to edit.
    assert "update_driver_definition" in result["note"]


async def test_unknown_driver_says_not_installed(tmp_path, monkeypatch) -> None:
    """The only remaining error names the real condition and the next call."""
    _driver_dirs(tmp_path, monkeypatch)

    result = await _handler()._get_driver_definition({"driver_id": "nothing_here"})

    error = result["error"]
    assert error.startswith("No driver 'nothing_here' is installed")
    assert "get_installed_drivers" in error
    # Not the old wording, which read as "this driver does not exist".
    assert "not found" not in error


async def test_create_definition_will_not_shadow_a_python_driver(tmp_path, monkeypatch) -> None:
    """Reading a Python driver's introspected surface must not lead to saving
    it back as YAML: a definition sharing the id would load over the top of
    the working driver."""
    _, repo = _driver_dirs(tmp_path, monkeypatch)
    register_driver(_AcmeWidget)
    try:
        result = await _handler()._create_driver_definition(
            {"definition": {"id": "acme_widget", "name": "Acme Widget", "transport": "tcp"}}
        )
    finally:
        unregister_driver("acme_widget")

    assert "shadow" in result.get("error", "")
    assert list(repo.glob("*.avcdriver")) == []
