"""A driver a device in the project uses is never deleted, at any of the three
doors that take one away: the Driver Builder's delete, the Installed page's
uninstall and the Code view's delete. Each asks the one in-use rule and
answers the same 409, naming the devices as the Installed page lists them (a
device's name, or its id when it has none) beside the code ``driver_in_use``,
and leaves the driver as it was.

The devices are the project's, so a device that is not running (waiting for
its driver, or failed to start) still holds the driver. A Python file saved
with another id, not reloaded since, still serves its devices on the id it
was loaded with, so a door that would remove that file is refused too.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import openavc.system_config as system_config
from openavc.api.errors import StructuredApiError
from openavc.api.routes import driver_definitions as BUILDER_ROUTES
from openavc.api.routes import drivers as INSTALL_ROUTES
from openavc.api.routes import python_drivers as CODE_VIEW_ROUTES
from openavc.drivers import driver_change
from openavc.drivers.driver_loader import load_python_driver_file, save_driver_definition
from openavc.drivers.registry import register_driver, unregister_driver

DRIVER_ID = "acme_widget"
WIDGET = {"id": DRIVER_ID, "name": "Acme Widget", "transport": "tcp",
          "commands": {"power_on": {"send": "PWR ON\\r"}}}


def _python(driver_id: str = DRIVER_ID) -> str:
    return (
        "from openavc.drivers.base import BaseDriver\n"
        "\n"
        "\n"
        "class AcmeWidget(BaseDriver):\n"
        "    DRIVER_INFO = {\n"
        f"        'id': {driver_id!r},\n"
        "        'name': 'Acme Widget',\n"
        "        'manufacturer': 'Acme',\n"
        "        'category': 'utility',\n"
        "        'version': '1.0.0',\n"
        "        'transport': 'tcp',\n"
        "    }\n"
    )


def _device(device_id: str, name: str, driver: str) -> SimpleNamespace:
    return SimpleNamespace(id=device_id, name=name, driver=driver)


# A device on the driver, one on another, and one on it with no name.
PROJECT = SimpleNamespace(devices=[
    _device("room_a", "Room A Projector", DRIVER_ID),
    _device("room_b", "Room B Display", "acme_other"),
    _device("lobby_1", "", DRIVER_ID),
])


@pytest.fixture
def library(tmp_path: Path, monkeypatch) -> SimpleNamespace:
    builtin = tmp_path / "definitions"
    repo = tmp_path / "driver_repo"
    builtin.mkdir()
    repo.mkdir()
    monkeypatch.setattr(system_config, "DRIVER_DEFINITIONS_DIR", builtin)
    monkeypatch.setattr(system_config, "DRIVER_REPO_DIR", repo)
    monkeypatch.setattr(BUILDER_ROUTES, "_get_driver_dirs", lambda: [builtin, repo])
    monkeypatch.setattr(INSTALL_ROUTES, "_get_driver_repo_dir", lambda: repo)
    # No device is running: the project alone says who uses a driver.
    engine = SimpleNamespace(
        project=None,
        devices=SimpleNamespace(get_devices_using_driver=lambda driver_id: []),
    )
    monkeypatch.setattr(driver_change, "_running_engine", lambda: engine)
    monkeypatch.setattr(CODE_VIEW_ROUTES, "_get_engine", lambda: engine)
    yield SimpleNamespace(repo=repo, engine=engine)
    for name in [n for n in sys.modules if n.startswith("openavc_driver_acme_")]:
        sys.modules.pop(name, None)
    for driver_id in (DRIVER_ID, "acme_gadget"):
        unregister_driver(driver_id)


def _files(repo: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(repo.iterdir()) if p.is_file()}


def _builder_delete(driver_id: str = DRIVER_ID):
    return BUILDER_ROUTES.delete_driver_definition_endpoint(driver_id)


def _uninstall(driver_id: str = DRIVER_ID):
    return INSTALL_ROUTES.uninstall_driver(driver_id)


def _code_view_delete(driver_id: str = DRIVER_ID):
    return CODE_VIEW_ROUTES.delete_python_driver(driver_id)


def _definition(repo: Path) -> None:
    save_driver_definition(dict(WIDGET), repo)


def _python_file(repo: Path) -> None:
    (repo / f"{DRIVER_ID}.py").write_text(_python(), encoding="utf-8")


# Each door, with the kind of driver file it deletes.
DOORS = {
    "the Driver Builder's delete": (_builder_delete, _definition),
    "the Installed page's uninstall of a definition": (_uninstall, _definition),
    "the Installed page's uninstall of a Python driver": (_uninstall, _python_file),
    "the Code view's delete": (_code_view_delete, _python_file),
}

REFUSAL = (
    409,
    "2 devices use this driver: Room A Projector, lobby_1. "
    "Open each one and choose another driver first.",
    {"code": "driver_in_use", "devices": [
        {"id": "room_a", "name": "Room A Projector"},
        {"id": "lobby_1", "name": "lobby_1"},
    ]},
)


def _answer(refused: pytest.ExceptionInfo[StructuredApiError]) -> tuple:
    return (refused.value.status_code, refused.value.detail, refused.value.fields)


@pytest.mark.asyncio
@pytest.mark.parametrize("door", DOORS)
async def test_every_door_refuses_a_driver_project_devices_use(library, door):
    remove, write = DOORS[door]
    write(library.repo)
    library.engine.project = PROJECT
    before = _files(library.repo)

    with pytest.raises(StructuredApiError) as refused:
        await remove()

    assert _answer(refused) == REFUSAL
    assert _files(library.repo) == before


@pytest.mark.asyncio
async def test_one_device_is_named_in_the_singular(library):
    _definition(library.repo)
    library.engine.project = SimpleNamespace(devices=[_device("room_a", "Room A Projector", DRIVER_ID)])

    with pytest.raises(StructuredApiError) as refused:
        await _builder_delete()

    assert refused.value.detail == (
        "Room A Projector uses this driver. Open the device and choose another driver first."
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("door", ["the Installed page's uninstall of a Python driver",
                                  "the Code view's delete"])
async def test_a_file_saved_with_another_id_still_holds_the_devices_it_serves(library, door):
    """Saved in the Code view as ``acme_gadget`` and not reloaded, the file
    still serves Room A on ``acme_widget``: deleting ``acme_gadget`` would
    remove the file that driver runs from."""
    path = library.repo / f"{DRIVER_ID}.py"
    path.write_text(_python(), encoding="utf-8")
    register_driver(load_python_driver_file(path))
    path.write_text(_python("acme_gadget"), encoding="utf-8")
    library.engine.project = SimpleNamespace(devices=[_device("room_a", "Room A Projector", DRIVER_ID)])
    remove = DOORS[door][0]

    with pytest.raises(StructuredApiError) as refused:
        await remove("acme_gadget")

    assert _answer(refused) == (
        409,
        "Room A Projector uses this driver. Open the device and choose another driver first.",
        {"code": "driver_in_use", "devices": [{"id": "room_a", "name": "Room A Projector"}]},
    )
    assert path.exists()


@pytest.mark.asyncio
async def test_the_code_view_lists_the_project_devices_on_each_driver(library):
    _python_file(library.repo)
    library.engine.project = PROJECT

    listed = await CODE_VIEW_ROUTES.list_python_drivers()

    assert [d["devices_using"] for d in listed["drivers"] if d["id"] == DRIVER_ID] == [
        ["room_a", "lobby_1"],
    ]
