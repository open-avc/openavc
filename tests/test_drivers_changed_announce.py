"""The server says when its drivers change, at every door that writes one.

The Programmer keeps its driver lists in a store that reloaded only when that
store did the installing, so a driver installed from Discovery, a device page,
the assistant, an upload or a project that brought it stayed missing from
Drivers > Installed until the page was reloaded. Each door now ends with one
``drivers.changed`` push to Programmer clients, which reload their lists.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from openavc.api import _engine as engine_slot
from openavc.api.models import DriverDefinitionRequest
from openavc.api.routes import drivers as drivers_routes
from openavc.api.routes.drivers import (
    announce_drivers_changed,
    create_driver_definition,
    delete_driver_definition_endpoint,
    patch_driver_definition,
)


class _Hub:
    def __init__(self) -> None:
        self.sent: list[tuple[dict, str | None]] = []

    async def broadcast(self, message: dict, client_type: str | None = None) -> None:
        self.sent.append((message, client_type))


@pytest.fixture()
def hub(monkeypatch) -> _Hub:
    hub = _Hub()

    async def _reload_driver(driver_id: str) -> int:
        return 0

    fake = SimpleNamespace(
        ws=hub, devices=SimpleNamespace(reload_driver=_reload_driver),
    )
    monkeypatch.setattr(engine_slot, "get_engine_optional", lambda: fake)
    monkeypatch.setattr(drivers_routes, "_get_engine", lambda: fake)
    return hub


@pytest.fixture()
def driver_dirs(tmp_path: Path, monkeypatch) -> Path:
    builtin_dir = tmp_path / "definitions"
    repo_dir = tmp_path / "driver_repo"
    builtin_dir.mkdir()
    repo_dir.mkdir()
    monkeypatch.setattr(
        drivers_routes, "_get_driver_dirs", lambda: (builtin_dir, repo_dir)
    )
    return repo_dir


DEFINITION = {
    "id": "acme_widget",
    "name": "Acme Widget",
    "transport": "tcp",
    "commands": {"power_on": {"send": "PWR ON\\r"}},
}


async def test_the_push_goes_to_programmers_only(hub):
    await announce_drivers_changed("acme_widget")
    assert hub.sent == [({"type": "drivers.changed", "driver_id": "acme_widget"}, "programmer")]


async def test_a_door_that_wrote_several_names_none(hub):
    await announce_drivers_changed()
    assert hub.sent == [({"type": "drivers.changed"}, "programmer")]


async def test_nothing_is_sent_before_the_engine_starts(monkeypatch):
    monkeypatch.setattr(engine_slot, "get_engine_optional", lambda: None)
    await announce_drivers_changed("acme_widget")  # no engine, no error


async def test_a_builder_save_edit_and_delete_each_announce(hub, driver_dirs):
    await create_driver_definition(DriverDefinitionRequest(**DEFINITION))
    await patch_driver_definition("acme_widget", {"name": "Acme Widget II"})
    await delete_driver_definition_endpoint("acme_widget")
    assert [m for m, _ in hub.sent] == [
        {"type": "drivers.changed", "driver_id": "acme_widget"},
    ] * 3
