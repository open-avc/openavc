"""One change at a time on each driver id: ``driver_change.holding``.

Platform test: invented drivers (``acme_widget``) on a stand-in device
manager, through the real doors.

Every door that changes a driver (an install, an update, an import, an
uninstall, the Driver Builder's saves, delete and reload, the Code view's,
the assistant's) awaits between its own steps: a catalog download, the
devices on the driver coming down and being built again one by one. A second
door on the same driver used to land in that gap, and the first door's answer
then described something that was no longer true: a save said its devices
were reconnected while a delete had taken their driver away, or half the
devices ran one version and half another. Each door now holds its driver's id
from its first check to its announcement, so the second door begins once the
first has answered, and the end state is what the later door said.
"""

from __future__ import annotations

import asyncio
import io
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
import yaml
from fastapi import HTTPException

import openavc.system_config as system_config
from openavc.api import driver_catalog
from openavc.api.models import (
    CommunityDriverInstallRequest,
    DriverDefinitionRequest,
    PythonDriverCreateRequest,
)
from openavc.api.routes import driver_definitions as BUILDER
from openavc.api.routes import drivers as LIBRARY
from openavc.api.routes import python_drivers as CODE_VIEW
from openavc.cloud.ai_tool_handler import AIToolHandler
from openavc.drivers import driver_change, refused_files, registry
from openavc.drivers.driver_change import DriverChange, DriverRefused
from openavc.drivers.registry import get_driver_class, is_driver_registered
from openavc.utils.community_integrity import ArtifactHashes

DRIVER_ID = "acme_widget"
CATALOG = "https://raw.githubusercontent.com/open-avc/openavc-drivers/main/utility/"


def _definition(name: str, driver_id: str = DRIVER_ID) -> dict:
    return {
        "id": driver_id,
        "name": name,
        "manufacturer": "Acme",
        "category": "utility",
        "version": "1.0.0",
        "transport": "tcp",
        "commands": {"power_on": {"label": "On", "send": "P\\r"}},
    }


async def _settle() -> None:
    """Let every task that can run, run, until each is waiting on something
    outside the loop."""
    for _ in range(50):
        await asyncio.sleep(0)


# --- the world ---------------------------------------------------------------


class _Devices:
    """Devices on drivers, built again one at a time as the device manager
    builds them: each comes down (an await), then is built from whatever class
    the registry holds for its driver at that moment, or waits for its driver
    when none does, and is named among those built either way. ``park`` makes
    the next rebuild wait after its first device until the event is set."""

    def __init__(self) -> None:
        self.on: dict[str, str] = {}
        self.built: dict[str, str | None] = {}
        self.parked = asyncio.Event()
        self._gate: asyncio.Event | None = None
        self.request_bringup = None

    def park(self) -> asyncio.Event:
        self._gate = asyncio.Event()
        return self._gate

    def get_devices_using_driver(self, driver_id: str) -> list[str]:
        return [device for device, on in self.on.items() if on == driver_id]

    def status_of(self, device_id: str) -> str:
        return "running" if self.built.get(device_id) else "orphaned"

    async def reload_driver(self, driver_id: str) -> list[str]:
        rebuilt: list[str] = []
        for device in self.get_devices_using_driver(driver_id):
            await asyncio.sleep(0)
            driver_class = get_driver_class(driver_id)
            self.built[device] = driver_class.DRIVER_INFO["name"] if driver_class else None
            rebuilt.append(device)
            gate, self._gate = self._gate, None
            if gate is not None:
                self.parked.set()
                await gate.wait()
        return rebuilt

    async def retry_all_orphans(self) -> list[str]:
        return []


class _Catalog:
    """The community catalog: each URL answers its bytes, an unlisted one a
    404. ``park`` makes the next fetch wait until the event is set."""

    def __init__(self) -> None:
        self.pages: dict[str, bytes] = {}
        self.parked = asyncio.Event()
        self._gate: asyncio.Event | None = None

    def park(self) -> asyncio.Event:
        self._gate = asyncio.Event()
        return self._gate

    def __call__(self, **_kwargs):  # httpx.AsyncClient(**kwargs)
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def get(self, url: str):
        gate, self._gate = self._gate, None
        if gate is not None:
            self.parked.set()
            await gate.wait()
        body = self.pages.get(url)
        request = httpx.Request("GET", url)
        if body is None:
            return httpx.Response(404, request=request)
        return httpx.Response(200, content=body, request=request)


@pytest.fixture
def world(tmp_path: Path, monkeypatch):
    builtin = tmp_path / "definitions"
    repo = tmp_path / "driver_repo"
    builtin.mkdir()
    repo.mkdir()
    monkeypatch.setattr(system_config, "DRIVER_DEFINITIONS_DIR", builtin)
    monkeypatch.setattr(system_config, "DRIVER_REPO_DIR", repo)
    monkeypatch.setattr(BUILDER, "_get_driver_dirs", lambda: [builtin, repo])
    monkeypatch.setattr(LIBRARY, "_get_driver_repo_dir", lambda: repo)
    monkeypatch.setattr(registry, "_DRIVER_REGISTRY", {})
    monkeypatch.setattr(refused_files, "_refused", {})
    monkeypatch.setattr(refused_files, "_noticed", {})

    devices = _Devices()
    announced: list = []

    async def _broadcast(message, client_type=None):
        announced.append(message.get("driver_id"))

    engine = SimpleNamespace(
        devices=devices, simulation=None, project=None, ws=SimpleNamespace(broadcast=_broadcast)
    )
    monkeypatch.setattr(driver_change, "_running_engine", lambda: engine)
    monkeypatch.setattr(LIBRARY, "_get_engine", lambda: engine)
    monkeypatch.setattr(CODE_VIEW, "_get_engine", lambda: engine)

    async def _refresh() -> None:
        return None

    monkeypatch.setattr("openavc.api.discovery.refresh_all_device_matches", _refresh)

    catalog = _Catalog()
    monkeypatch.setattr("httpx.AsyncClient", catalog)

    async def _no_hashes(driver_id, force=False):
        return ArtifactHashes(f"Driver '{driver_id}'", None, source="catalog")

    monkeypatch.setattr(driver_catalog, "_catalog_hashes", _no_hashes)

    def installed(name: str, driver_id: str = DRIVER_ID) -> None:
        """A YAML driver in the folder, registered, as a start leaves it."""
        from openavc.drivers.configurable import create_configurable_driver_class

        definition = _definition(name, driver_id)
        (repo / f"{driver_id}.avcdriver").write_text(yaml.safe_dump(definition, sort_keys=False))
        registry.register_driver(create_configurable_driver_class(definition))

    def stored() -> dict[str, str]:
        """Each YAML driver file in the folder, by id, as the name it holds."""
        return {
            path.stem: yaml.safe_load(path.read_text())["name"]
            for path in sorted(repo.glob("*.avcdriver"))
        }

    def serving(driver_id: str = DRIVER_ID) -> str | None:
        driver_class = get_driver_class(driver_id)
        return driver_class.DRIVER_INFO["name"] if driver_class else None

    yield SimpleNamespace(
        repo=repo, devices=devices, catalog=catalog, announced=announced,
        installed=installed, stored=stored, serving=serving,
    )
    for name in [n for n in sys.modules if n.startswith("openavc_driver_acme")]:
        sys.modules.pop(name, None)


def _builder_save(name: str, driver_id: str = DRIVER_ID, renamed: str | None = None):
    definition = _definition(name, renamed or driver_id)
    return BUILDER.update_driver_definition(driver_id, DriverDefinitionRequest(**definition))


async def _answer(door) -> object:
    """A door's answer: what it returned, or the refusal it raised."""
    try:
        return await door
    except HTTPException as refusal:
        return refusal


# --- two doors on one driver -------------------------------------------------


async def test_a_delete_waits_for_a_save_still_building_its_devices(world):
    world.installed("Acme Widget 1")
    world.devices.on = {"d1": DRIVER_ID, "d2": DRIVER_ID, "d3": DRIVER_ID}
    gate = world.devices.park()
    save = asyncio.create_task(_builder_save("Acme Widget 2"))
    await world.devices.parked.wait()

    delete = asyncio.create_task(BUILDER.delete_driver_definition_endpoint(DRIVER_ID))
    await _settle()
    deleted_while_saving = delete.done()
    gate.set()
    saved, deleted = await save, await delete

    # The save's answer is true: every device it named runs what it saved.
    assert saved["devices_reconnected"] == ["d1", "d2", "d3"]
    assert world.devices.built == dict.fromkeys(["d1", "d2", "d3"], "Acme Widget 2")
    # And the delete's, which came later, is the end state.
    assert deleted["status"] == "deleted"
    assert world.stored() == {}
    assert world.serving() is None
    assert not deleted_while_saving


async def test_the_assistant_s_edit_waits_for_a_save_so_every_device_runs_one_version(world):
    """The assistant read the driver before the Builder's save; its edit,
    sent while the save is building its devices, waits for the save, and is
    then refused as made from the copy the save replaced. The save is the end
    state, on every device."""
    world.installed("Acme Widget 1")
    world.devices.on = {"d1": DRIVER_ID, "d2": DRIVER_ID, "d3": DRIVER_ID}
    assistant = AIToolHandler(MagicMock(), MagicMock(), MagicMock())
    read = await assistant._get_driver_definition({"driver_id": DRIVER_ID})
    gate = world.devices.park()
    save = asyncio.create_task(_builder_save("Acme Widget 2"))
    await world.devices.parked.wait()

    edit = asyncio.create_task(
        assistant._update_driver_definition(
            {"driver_id": DRIVER_ID, "definition": {**read, "name": "Acme Widget 3"}}
        )
    )
    await _settle()
    edited_while_saving = edit.done()
    gate.set()
    saved, edited = await save, await edit

    assert saved["devices_reconnected"] == ["d1", "d2", "d3"]
    assert edited["error"].startswith("'acme_widget' was changed after you read it")
    assert world.devices.built == dict.fromkeys(["d1", "d2", "d3"], "Acme Widget 2")
    assert world.stored() == {DRIVER_ID: "Acme Widget 2"}
    assert world.serving() == "Acme Widget 2"
    assert not edited_while_saving


async def test_the_assistant_s_edit_from_a_read_after_the_save_is_the_end_state(world):
    """Read again once the save answered, the same edit saves: every device
    runs what it saved."""
    world.installed("Acme Widget 1")
    world.devices.on = {"d1": DRIVER_ID, "d2": DRIVER_ID, "d3": DRIVER_ID}
    await _builder_save("Acme Widget 2")
    assistant = AIToolHandler(MagicMock(), MagicMock(), MagicMock())
    read = await assistant._get_driver_definition({"driver_id": DRIVER_ID})

    edited = await assistant._update_driver_definition(
        {"driver_id": DRIVER_ID, "definition": {**read, "name": "Acme Widget 3"}}
    )

    assert edited["status"] == "updated"
    assert edited["devices_reconnected"] == ["d1", "d2", "d3"]
    assert world.devices.built == dict.fromkeys(["d1", "d2", "d3"], "Acme Widget 3")
    assert world.stored() == {DRIVER_ID: "Acme Widget 3"}
    assert world.serving() == "Acme Widget 3"


async def test_a_create_of_a_renamed_driver_s_old_id_waits_for_the_rename(world):
    world.installed("Acme Widget 1")
    world.devices.on = {"d1": DRIVER_ID, "d2": DRIVER_ID}
    gate = world.devices.park()
    rename = asyncio.create_task(_builder_save("Acme Widget 2", renamed="acme_widget_two"))
    await world.devices.parked.wait()

    create = asyncio.create_task(
        BUILDER.create_driver_definition(DriverDefinitionRequest(**_definition("Acme Widget New")))
    )
    await _settle()
    created_while_renaming = create.done()
    gate.set()
    renamed, created = await rename, await create

    # The rename left the devices on the old id waiting for their driver ...
    assert renamed["driver_id"] == "acme_widget_two"
    # ... and the create, which came later, then brought them up on its own.
    assert created["status"] == "created"
    assert world.devices.built == {"d1": "Acme Widget New", "d2": "Acme Widget New"}
    assert world.stored() == {"acme_widget_two": "Acme Widget 2", DRIVER_ID: "Acme Widget New"}
    assert not created_while_renaming


async def test_a_create_waits_for_an_install_of_its_id_and_is_refused_after_it(world):
    world.catalog.pages[CATALOG + f"{DRIVER_ID}.avcdriver"] = yaml.safe_dump(
        _definition("Acme Widget Catalog"), sort_keys=False
    ).encode()
    gate = world.catalog.park()
    install = asyncio.create_task(
        LIBRARY.install_community_driver(
            CommunityDriverInstallRequest(
                driver_id=DRIVER_ID, file_url=CATALOG + f"{DRIVER_ID}.avcdriver"
            )
        )
    )
    await world.catalog.parked.wait()

    create = asyncio.create_task(
        _answer(BUILDER.create_driver_definition(
            DriverDefinitionRequest(**_definition("Acme Widget Mine"))
        ))
    )
    await _settle()
    created_while_installing = create.done()
    gate.set()
    installed, created = await install, await create

    assert installed["status"] == "installed"
    # The later door's answer is the end state: the id is the install's.
    assert isinstance(created, HTTPException) and created.status_code == 409
    assert world.stored() == {DRIVER_ID: "Acme Widget Catalog"}
    assert world.serving() == "Acme Widget Catalog"
    assert not created_while_installing


# --- every door waits while its driver is held --------------------------------


class _Upload:
    """A file as a browser sends it to an import door."""

    def __init__(self, filename: str, content: bytes) -> None:
        self.filename = filename
        self._content = content

    async def read(self) -> bytes:
        return self._content


class _Request:
    """A request as the import and update doors read it."""

    def __init__(self, *, upload: _Upload | None = None, json: dict | None = None) -> None:
        self._upload = upload
        self._json = json or {}

    async def form(self) -> dict:
        return {"file": self._upload}

    async def json(self) -> dict:
        return self._json


def _yaml_file(name: str) -> bytes:
    return yaml.safe_dump(_definition(name), sort_keys=False).encode()


def _bundle(name: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(f"{DRIVER_ID}.avcdriver", _yaml_file(name))
    return buffer.getvalue()


def _python_source(driver_id: str, *, computed: bool = False) -> str:
    written = f"'acme_' + {driver_id[len('acme_'):]!r}" if computed else repr(driver_id)
    return (
        "from openavc.drivers.base import BaseDriver\n\n\n"
        "class AcmeDriver(BaseDriver):\n"
        "    DRIVER_INFO = {\n"
        f"        'id': {written},\n"
        "        'name': 'Acme Python',\n"
        "        'manufacturer': 'Acme',\n"
        "        'category': 'utility',\n"
        "        'version': '1.0.0',\n"
        "        'transport': 'tcp',\n"
        "    }\n"
    )


_INSTALL_URL = CATALOG + f"{DRIVER_ID}.avcdriver"


def _assistant() -> AIToolHandler:
    return AIToolHandler(MagicMock(), MagicMock(), MagicMock())


# Every door that writes, removes, registers or reloads a driver, each as it is
# called. A door here without the hold fails the test below; the next test
# holds this list to every route on the three driver routers that is not a
# GET, so a new route there is listed or named as no driver change. The
# assistant's tools are listed by hand.
_DOORS = {
    "community install": lambda: LIBRARY.install_community_driver(
        CommunityDriverInstallRequest(driver_id=DRIVER_ID, file_url=_INSTALL_URL)
    ),
    "single-file import": lambda: LIBRARY.upload_driver(
        _Request(upload=_Upload(f"{DRIVER_ID}.avcdriver", _yaml_file("Imported")))
    ),
    "bundle import": lambda: LIBRARY.upload_driver_bundle(
        _Request(upload=_Upload("acme.zip", _bundle("Bundled")))
    ),
    "uninstall": lambda: LIBRARY.uninstall_driver(DRIVER_ID),
    "community update": lambda: LIBRARY.update_driver(
        DRIVER_ID, _Request(json={"file_url": _INSTALL_URL})
    ),
    "Builder create": lambda: BUILDER.create_driver_definition(
        DriverDefinitionRequest(**_definition("Created"))
    ),
    "Builder save": lambda: _builder_save("Saved"),
    "Builder save that renames onto it": lambda: BUILDER.update_driver_definition(
        "acme_other", DriverDefinitionRequest(**_definition("Renamed"))
    ),
    "Builder patch": lambda: BUILDER.patch_driver_definition(DRIVER_ID, {"name": "Patched"}),
    "Builder delete": lambda: BUILDER.delete_driver_definition_endpoint(DRIVER_ID),
    "Builder reload": lambda: BUILDER.reload_driver_definition(DRIVER_ID),
    "Code view create": lambda: CODE_VIEW.create_python_driver(
        PythonDriverCreateRequest(id=DRIVER_ID, source=_python_source(DRIVER_ID))
    ),
    "Code view save": lambda: CODE_VIEW.save_python_driver_source(
        DRIVER_ID, {"source": _python_source(DRIVER_ID)}
    ),
    "Code view delete": lambda: CODE_VIEW.delete_python_driver(DRIVER_ID),
    "Code view reload": lambda: CODE_VIEW.reload_python_driver_endpoint(DRIVER_ID),
    "assistant create": lambda: _assistant()._create_driver_definition(
        {"definition": _definition("Assistant")}
    ),
    "assistant update": lambda: _assistant()._update_driver_definition(
        {"driver_id": DRIVER_ID, "definition": _definition("Assistant")}
    ),
    "assistant update that renames onto it": lambda: _assistant()._update_driver_definition(
        {"driver_id": "acme_other", "definition": _definition("Assistant")}
    ),
    "assistant install": lambda: _assistant()._install_community_driver(
        {"driver_id": DRIVER_ID, "file_url": _INSTALL_URL}
    ),
}


# The routes each entry of _DOORS calls, and the ones that change no driver.
_HELD_ROUTES = {
    "install_community_driver", "upload_driver", "upload_driver_bundle", "uninstall_driver",
    "update_driver", "create_driver_definition", "update_driver_definition",
    "patch_driver_definition", "delete_driver_definition_endpoint", "reload_driver_definition",
    "create_python_driver", "save_python_driver_source", "delete_python_driver",
    "reload_python_driver_endpoint",
}
_NO_DRIVER_CHANGE = {"validate_driver_definition_draft"}  # checks a draft, writes nothing


def test_every_route_that_can_change_a_driver_is_a_listed_door():
    routes = {
        route.endpoint.__name__
        for router in (LIBRARY.router, BUILDER.router, CODE_VIEW.router)
        for route in router.routes
        if set(getattr(route, "methods", ()) or ()) - {"GET", "HEAD"}
    }
    assert routes == _HELD_ROUTES | _NO_DRIVER_CHANGE
    called = set().union(*(door.__code__.co_names for door in _DOORS.values()))
    assert _HELD_ROUTES <= called


@pytest.mark.parametrize("door", sorted(_DOORS))
async def test_every_door_waits_while_another_change_holds_its_driver(world, door):
    world.installed("Acme Widget 1")
    world.installed("Acme Other", driver_id="acme_other")
    released = asyncio.Event()
    holding = asyncio.Event()

    async def _another_change():
        async with driver_change.holding(DRIVER_ID):
            holding.set()
            await released.wait()

    other = asyncio.create_task(_another_change())
    await holding.wait()
    task = asyncio.create_task(_answer(_DOORS[door]()))
    await _settle()
    ran_while_held = task.done()
    released.set()
    await other
    await asyncio.wait_for(task, timeout=5)
    assert not ran_while_held


# --- the hold itself ---------------------------------------------------------


async def _run_holding(order: list, name: str, *driver_ids: str, until: asyncio.Event | None = None):
    async with driver_change.holding(*driver_ids):
        order.append(f"{name} in")
        if until is not None:
            await until.wait()
        await asyncio.sleep(0)
        order.append(f"{name} out")


async def test_two_changes_on_one_driver_run_one_after_the_other():
    order: list = []
    release = asyncio.Event()
    first = asyncio.create_task(_run_holding(order, "first", DRIVER_ID, until=release))
    second = asyncio.create_task(_run_holding(order, "second", DRIVER_ID))
    await _settle()
    assert order == ["first in"]
    release.set()
    await asyncio.gather(first, second)
    assert order == ["first in", "first out", "second in", "second out"]


async def test_changes_on_other_drivers_do_not_wait():
    order: list = []
    release = asyncio.Event()
    first = asyncio.create_task(_run_holding(order, "first", DRIVER_ID, until=release))
    other = asyncio.create_task(_run_holding(order, "other", "acme_other"))
    await asyncio.wait_for(other, timeout=5)
    assert order == ["first in", "other in", "other out"]
    release.set()
    await first


async def test_changes_naming_two_drivers_in_either_order_all_finish():
    order: list = []
    release = asyncio.Event()
    first = asyncio.create_task(_run_holding(order, "first", DRIVER_ID, until=release))
    crossed = [
        asyncio.create_task(_run_holding(order, "rename one way", DRIVER_ID, "acme_other")),
        asyncio.create_task(_run_holding(order, "rename the other", "acme_other", DRIVER_ID)),
    ]
    await _settle()
    release.set()
    await asyncio.wait_for(asyncio.gather(first, *crossed), timeout=5)
    assert len(order) == 6


async def test_a_door_that_calls_another_door_on_its_driver_does_not_wait_for_itself():
    order: list = []

    async def _outer():
        async with driver_change.holding(DRIVER_ID, "acme_other"):
            async with driver_change.holding(DRIVER_ID):
                order.append("inner")
            other = asyncio.create_task(_run_holding(order, "other", DRIVER_ID))
            await _settle()
            order.append("outer still holds")
        await other

    await asyncio.wait_for(_outer(), timeout=5)
    assert order == ["inner", "outer still holds", "other in", "other out"]


async def test_a_change_that_fails_or_is_cancelled_gives_its_driver_back():
    async def _fails():
        async with driver_change.holding(DRIVER_ID):
            raise RuntimeError("the change failed")

    with pytest.raises(RuntimeError):
        await _fails()

    release = asyncio.Event()
    held = asyncio.create_task(_run_holding([], "held", DRIVER_ID, until=release))
    waiting = asyncio.create_task(_run_holding([], "waiting", DRIVER_ID))
    await _settle()
    waiting.cancel()
    held.cancel()
    await asyncio.gather(held, waiting, return_exceptions=True)

    order: list = []
    await asyncio.wait_for(_run_holding(order, "next", DRIVER_ID), timeout=5)
    assert order == ["next in", "next out"]


async def test_nothing_is_held_once_every_change_has_ended():
    for driver_id in ("acme_one", "acme_two", "acme_three"):
        await _run_holding([], driver_id, driver_id, "acme_shared")
    holds = driver_change._holds_on(asyncio.get_running_loop())
    assert holds.holders == {}
    assert holds.waiting == []


async def test_claim_takes_a_free_driver_until_the_change_ends():
    order: list = []
    release = asyncio.Event()

    async def _learns_another_id():
        async with driver_change.holding(DRIVER_ID):
            driver_change.claim("acme_other")
            order.append("claimed")
            await release.wait()
        order.append("ended")

    first = asyncio.create_task(_learns_another_id())
    await _settle()
    second = asyncio.create_task(_run_holding(order, "second", "acme_other"))
    await _settle()
    assert order == ["claimed"]
    release.set()
    await asyncio.gather(first, second)
    assert order == ["claimed", "ended", "second in", "second out"]


async def test_claim_refuses_a_driver_another_change_holds():
    release = asyncio.Event()
    held = asyncio.create_task(_run_holding([], "held", "acme_other", until=release))
    await _settle()
    async with driver_change.holding(DRIVER_ID):
        with pytest.raises(DriverRefused) as refused:
            driver_change.claim("acme_other")
    assert refused.value.status_code == 409
    assert refused.value.detail == (
        "Another change to the driver 'acme_other' is still being made. "
        "Try again once it has finished."
    )
    release.set()
    await held


def test_claim_with_no_event_loop_running_has_nothing_to_order():
    driver_change.claim(DRIVER_ID)


async def _held_elsewhere(driver_id: str) -> tuple[asyncio.Event, asyncio.Task]:
    release = asyncio.Event()
    task = asyncio.create_task(_run_holding([], "elsewhere", driver_id, until=release))
    await _settle()
    return release, task


async def test_admit_refuses_a_driver_another_change_holds_and_leaves_the_folder_as_it_was(world):
    world.installed("Acme Widget 1")
    release, elsewhere = await _held_elsewhere(DRIVER_ID)
    name = f"{DRIVER_ID}.avcdriver"
    with pytest.raises(DriverRefused) as refused:
        driver_change.admit(DriverChange(world.repo, name, {name: _yaml_file("Acme Widget 2")}))
    assert refused.value.status_code == 409
    assert world.stored() == {DRIVER_ID: "Acme Widget 1"}
    assert world.serving() == "Acme Widget 1"
    release.set()
    await elsewhere


async def test_an_import_whose_id_is_computed_is_refused_while_that_driver_is_being_changed(world):
    release, elsewhere = await _held_elsewhere(DRIVER_ID)
    source = _python_source(DRIVER_ID, computed=True).encode()
    answer = await _answer(
        LIBRARY.upload_driver(_Request(upload=_Upload("acme_dynamic.py", source)))
    )
    assert isinstance(answer, HTTPException) and answer.status_code == 409
    assert sorted(p.name for p in world.repo.iterdir() if p.is_file()) == []
    assert not is_driver_registered(DRIVER_ID)
    release.set()
    await elsewhere


async def test_a_code_view_reload_onto_a_driver_being_changed_is_refused_and_changes_nothing(world):
    created = await CODE_VIEW.create_python_driver(
        PythonDriverCreateRequest(id="acme_relay", source=_python_source("acme_relay"))
    )
    assert created["status"] == "created" and is_driver_registered("acme_relay")
    (world.repo / "acme_relay.py").write_text(_python_source(DRIVER_ID))
    release, elsewhere = await _held_elsewhere(DRIVER_ID)

    answer = await _answer(CODE_VIEW.reload_python_driver_endpoint("acme_relay"))

    assert isinstance(answer, HTTPException) and answer.status_code == 409
    assert is_driver_registered("acme_relay")
    assert not is_driver_registered(DRIVER_ID)
    assert "openavc_driver_acme_relay" in sys.modules
    release.set()
    await elsewhere


async def test_a_code_view_create_whose_driver_is_being_changed_leaves_nothing_behind(world):
    release, elsewhere = await _held_elsewhere(DRIVER_ID)
    answer = await _answer(
        CODE_VIEW.create_python_driver(
            PythonDriverCreateRequest(id="acme_new", source=_python_source(DRIVER_ID))
        )
    )
    assert isinstance(answer, HTTPException) and answer.status_code == 409
    assert sorted(p.name for p in world.repo.iterdir() if p.is_file()) == []
    assert not is_driver_registered(DRIVER_ID)
    release.set()
    await elsewhere
