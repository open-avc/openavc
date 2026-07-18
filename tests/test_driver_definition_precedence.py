"""Duplicate driver-id precedence through the definition API (routes/drivers.py).

A user copy in driver_repo overrides a same-id built-in — the loader, the
listing, and the editing endpoints all agree on that. These tests pin the
API-facing consequences:

- The listing and PATCH operate on the user copy (the one that actually
  serves the id at runtime), never the shadowed built-in.
- Deleting an overriding user copy re-registers the shipped built-in in the
  same call, so the id keeps working with its original behavior.
- Renaming an overriding copy away restores the built-in for the old id;
  renaming a plain user driver drops the old id's stale registration.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import server.system_config as system_config
from server.api.models import DriverDefinitionRequest
from server.api.routes import drivers as drivers_routes
from server.api.routes.drivers import (
    delete_driver_definition_endpoint,
    list_driver_definitions as list_definitions_endpoint,
    patch_driver_definition,
    update_driver_definition,
)
from server.core.device_manager import (
    get_driver_registry,
    is_driver_registered,
    register_driver,
    unregister_driver,
)
from server.drivers.configurable import create_configurable_driver_class
from server.drivers.driver_loader import save_driver_definition


BUILTIN_DEF = {
    "id": "acme_widget",
    "name": "Built-in",
    "transport": "tcp",
    "commands": {"power_on": {"send": "BUILTIN ON\\r"}},
}

USER_DEF = {
    "id": "acme_widget",
    "name": "User Copy",
    "transport": "tcp",
    "commands": {"power_on": {"send": "USER ON\\r"}},
}


def _registry_name(driver_id: str) -> str | None:
    for info in get_driver_registry():
        if info["id"] == driver_id:
            return info["name"]
    return None


@pytest.fixture()
def driver_dirs(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    builtin_dir = tmp_path / "definitions"
    repo_dir = tmp_path / "driver_repo"
    builtin_dir.mkdir()
    repo_dir.mkdir()
    monkeypatch.setattr(
        drivers_routes, "_get_driver_dirs", lambda: [builtin_dir, repo_dir]
    )
    # Built-ins are recognized by living under the bundled definitions tree;
    # treat this test's definitions dir as that tree.
    monkeypatch.setattr(system_config, "DRIVER_DEFINITIONS_DIR", builtin_dir)

    async def _reload_driver(driver_id: str) -> int:
        return 0

    monkeypatch.setattr(
        drivers_routes,
        "_get_engine",
        lambda: SimpleNamespace(devices=SimpleNamespace(reload_driver=_reload_driver)),
    )
    return builtin_dir, repo_dir


@pytest.fixture()
def override_pair(driver_dirs) -> tuple[Path, Path]:
    """A built-in definition shadowed by a same-id user copy, user copy live."""
    builtin_dir, repo_dir = driver_dirs
    save_driver_definition(dict(BUILTIN_DEF), builtin_dir)
    save_driver_definition(dict(USER_DEF), repo_dir)
    # As after startup: the user copy won the load and serves the id.
    register_driver(create_configurable_driver_class(dict(USER_DEF)))
    yield builtin_dir, repo_dir
    unregister_driver("acme_widget")
    unregister_driver("acme_custom")


async def test_listing_shows_the_user_copy_as_user_source(override_pair):
    definitions = await list_definitions_endpoint()
    entries = [d for d in definitions if d["id"] == "acme_widget"]
    assert len(entries) == 1
    assert entries[0]["name"] == "User Copy"
    assert entries[0]["source"] == "user"


async def test_patch_merges_onto_the_user_copy(override_pair):
    _, repo_dir = override_pair
    await patch_driver_definition("acme_widget", {"name": "Patched"})

    saved = yaml.safe_load((repo_dir / "acme_widget.avcdriver").read_text())
    assert saved["name"] == "Patched"
    # The merge base was the user copy, not the shadowed built-in.
    assert saved["commands"]["power_on"] == {"send": "USER ON\\r"}


async def test_delete_override_restores_the_builtin(override_pair):
    builtin_dir, repo_dir = override_pair

    result = await delete_driver_definition_endpoint("acme_widget")

    assert result["status"] == "deleted"
    assert result["builtin_restored"] is True
    assert not (repo_dir / "acme_widget.avcdriver").exists()
    assert (builtin_dir / "acme_widget.avcdriver").exists()
    # The registry now serves the shipped built-in again.
    assert _registry_name("acme_widget") == "Built-in"


async def test_delete_plain_user_driver_unregisters(driver_dirs):
    _, repo_dir = driver_dirs
    save_driver_definition(dict(USER_DEF), repo_dir)
    register_driver(create_configurable_driver_class(dict(USER_DEF)))

    result = await delete_driver_definition_endpoint("acme_widget")

    assert result["builtin_restored"] is False
    assert not is_driver_registered("acme_widget")


async def test_rename_restores_the_builtin_for_the_old_id(override_pair):
    builtin_dir, repo_dir = override_pair

    body = DriverDefinitionRequest(
        id="acme_custom",
        name="Custom Widget",
        transport="tcp",
        commands={"power_on": {"send": "CUSTOM ON\\r"}},
    )
    result = await update_driver_definition("acme_widget", body)

    assert result["id"] == "acme_custom"
    assert (repo_dir / "acme_custom.avcdriver").exists()
    assert not (repo_dir / "acme_widget.avcdriver").exists()
    # The new id is live and the old id fell back to the shipped built-in.
    assert _registry_name("acme_custom") == "Custom Widget"
    assert _registry_name("acme_widget") == "Built-in"


async def test_rename_plain_user_driver_drops_the_old_id(driver_dirs):
    _, repo_dir = driver_dirs
    save_driver_definition(dict(USER_DEF), repo_dir)
    register_driver(create_configurable_driver_class(dict(USER_DEF)))

    body = DriverDefinitionRequest(
        id="acme_custom",
        name="Custom Widget",
        transport="tcp",
        commands={"power_on": {"send": "CUSTOM ON\\r"}},
    )
    try:
        await update_driver_definition("acme_widget", body)
        assert is_driver_registered("acme_custom")
        assert not is_driver_registered("acme_widget")
    finally:
        unregister_driver("acme_custom")
        unregister_driver("acme_widget")
