"""A device a scan found is matched to its driver as soon as the driver
arrives, at every door, and a running simulation reads the driver again.

Platform test: an invented driver (``acme_widget``) whose ``discovery:`` block
claims an mDNS service, a real Discovery engine holding one device a scan
found answering that service, and a stand-in for the rest of the engine.

The Code view's New Driver and Reload, a project imported into the library
with its drivers, and a library copy opened with its drivers each changed the
drivers on this server without the step every other door's change ends with
(``driver_change.changed``). The found device stayed unmatched on the
Discovery page, and the next scan did not match it either, since the scan
reads the installed drivers that step last told it about; a simulated device
on a reloaded Python driver went on running its old simulator; a project
device waiting for a driver a project brought stayed waiting. A plain Save in
the Code view changes no running driver (its Save & Reload's reload does), so
it is not one of these doors.
"""

from __future__ import annotations

import io
import json
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

import openavc.system_config as system_config
from openavc.api import discovery as discovery_api
from openavc.api.models import LibraryOpenRequest, PythonDriverCreateRequest
from openavc.api.routes import project as PROJECT
from openavc.api.routes import python_drivers as CODE_VIEW
from openavc.core import project_library
from openavc.discovery.engine import DiscoveryEngine
from openavc.discovery.result import DeviceState, DiscoveredDevice
from openavc.discovery.tier_matcher import evidence_mdns
from openavc.drivers import driver_change, refused_files, registry
from openavc.drivers.driver_loader import load_python_driver_file
from openavc.drivers.registry import list_registered_drivers

DRIVER_ID = "acme_widget"
SERVICE = "_acme-widget._tcp"
FOUND = "10.0.0.5"


# --- the drivers -------------------------------------------------------------


def _python_source(*, discovery: bool) -> str:
    block = f"        'discovery': {{'mdns': [{SERVICE!r}]}},\n" if discovery else ""
    return (
        "from openavc.drivers.base import BaseDriver\n\n\n"
        "class AcmeWidget(BaseDriver):\n"
        "    DRIVER_INFO = {\n"
        f"        'id': {DRIVER_ID!r},\n"
        "        'name': 'Acme Widget',\n"
        "        'manufacturer': 'Acme',\n"
        "        'category': 'utility',\n"
        "        'version': '1.0.0',\n"
        "        'transport': 'tcp',\n"
        f"{block}"
        "    }\n"
    )


def _yaml_driver() -> bytes:
    definition = {
        "id": DRIVER_ID,
        "name": "Acme Widget",
        "manufacturer": "Acme",
        "category": "utility",
        "version": "1.0.0",
        "transport": "tcp",
        "discovery": {"mdns": [SERVICE]},
        "commands": {"power_on": {"label": "On", "send": "P\\r"}},
    }
    return yaml.safe_dump(definition, sort_keys=False).encode()


def _project_bundle() -> bytes:
    """A project export carrying the driver its one device uses."""
    project = project_library.create_blank_project("acme_room", "Acme Room").model_dump(mode="json")
    project["devices"] = [{"id": "d1", "name": "Widget", "driver": DRIVER_ID, "config": {}}]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("project.avc", json.dumps(project))
        archive.writestr(f"drivers/{DRIVER_ID}.avcdriver", _yaml_driver())
    return buffer.getvalue()


class _Upload:
    """A file as a browser sends it to the library's import."""

    def __init__(self, filename: str, content: bytes) -> None:
        self.filename = filename
        self._content = content

    async def read(self) -> bytes:
        return self._content


class _Request:
    def __init__(self, upload: _Upload) -> None:
        self._upload = upload

    async def form(self) -> dict:
        return {"file": self._upload}


# --- the world ---------------------------------------------------------------


class _Devices:
    """The project's devices, each rebuild an event."""

    def __init__(self, events: list) -> None:
        self.events = events
        self.on: dict[str, str] = {}
        self.request_bringup = None

    def get_devices_using_driver(self, driver_id: str) -> list[str]:
        return [device for device, on in self.on.items() if on == driver_id]

    def status_of(self, device_id: str) -> str:
        return "orphaned"

    async def reload_driver(self, driver_id: str) -> list[str]:
        self.events.append(("devices built", driver_id))
        return self.get_devices_using_driver(driver_id)

    async def retry_all_orphans(self) -> list[str]:
        return []


@pytest.fixture
def world(tmp_path: Path, monkeypatch):
    builtin = tmp_path / "definitions"
    repo = tmp_path / "driver_repo"
    builtin.mkdir()
    repo.mkdir()
    monkeypatch.setattr(system_config, "DRIVER_DEFINITIONS_DIR", builtin)
    monkeypatch.setattr(system_config, "DRIVER_REPO_DIR", repo)
    monkeypatch.setattr(registry, "_DRIVER_REGISTRY", {})
    monkeypatch.setattr(refused_files, "_refused", {})
    monkeypatch.setattr(refused_files, "_noticed", {})

    events: list = []

    async def _simulation(driver_ids):
        events.append(("simulation told", tuple(driver_ids)))
        return False

    async def _broadcast(message, client_type=None):
        if message.get("type") == "drivers.changed":
            events.append(("announced", message.get("driver_id")))

    async def _reload_project():
        events.append(("project reloaded",))

    # One engine, as every module reaches it: the driver-change step, and the
    # project library's open, which replaces the running project.
    (tmp_path / "active").mkdir()
    engine = MagicMock(project_path=tmp_path / "active" / "project.avc", project=None)
    engine.devices = _Devices(events)
    engine.simulation = SimpleNamespace(drivers_changed=_simulation)
    engine.ws = SimpleNamespace(broadcast=_broadcast)
    engine.broadcast_ws = AsyncMock()
    engine.reload_project = _reload_project
    engine.logins_needed = MagicMock(return_value=[])
    engine.plugin_logins_needed = MagicMock(return_value=[])
    monkeypatch.setattr(driver_change, "_running_engine", lambda: engine)
    monkeypatch.setattr(PROJECT, "_get_engine", lambda: engine)

    lib = tmp_path / "saved_projects"
    lib.mkdir()
    monkeypatch.setattr(project_library, "_lib_dir", lambda: lib)
    monkeypatch.setattr(project_library, "_DRIVER_REPO_DIR", repo)
    monkeypatch.setattr("openavc.core.backup_manager.create_backup", lambda *a, **k: None)

    # Discovery as a start leaves it, holding one device a scan found.
    discovery = DiscoveryEngine()
    monkeypatch.setattr(discovery_api, "_engine", discovery)
    sent: list[dict] = []

    async def _to_programmers(message):
        sent.append(message)

    monkeypatch.setattr(discovery_api, "_broadcast_fn", _to_programmers)
    discovery.load_driver_hints_from_registry(list_registered_drivers())
    found = DiscoveredDevice(ip=FOUND)
    found.evidence_log.append(evidence_mdns(SERVICE))
    discovery.results[FOUND] = found

    def installed_python(*, discovery_block: bool = False) -> None:
        """A Python driver in the folder, registered, as a start leaves it."""
        path = repo / f"{DRIVER_ID}.py"
        path.write_text(_python_source(discovery=discovery_block), encoding="utf-8")
        registry.register_driver(load_python_driver_file(path))

    def on_the_page() -> str | None:
        """The driver the Discovery page was last sent the found device
        matched to, or None when it was sent nothing for it."""
        updates = [m["device"] for m in sent if m["device"]["ip"] == FOUND]
        if not updates:
            return None
        identification = updates[-1]["identification"]
        return identification["driver_id"] if identification["state"] == "identified" else None

    async def after_the_next_scan_folds_the_catalog() -> str | None:
        """What the next scan matches the found device to, once it has folded
        the community catalog in with the installed drivers."""
        discovery.community_index.get_drivers = AsyncMock(return_value=[])
        await discovery.refresh_signal_index_with_catalog()
        await discovery.refresh_device_matches(FOUND)
        identification = discovery.results[FOUND].identification
        return identification.driver_id if identification.state == DeviceState.IDENTIFIED else None

    yield SimpleNamespace(
        repo=repo, lib=lib, events=events, devices=engine.devices,
        installed_python=installed_python, on_the_page=on_the_page,
        after_the_next_scan=after_the_next_scan_folds_the_catalog,
    )
    for name in [n for n in sys.modules if n.startswith("openavc_driver_acme")]:
        sys.modules.pop(name, None)


async def _open_library_copy() -> dict:
    """Open a library copy whose saved bundle carries the driver."""
    content = _project_bundle()
    saved = project_library._lib_dir() / "acme_room"
    saved.mkdir()
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        (saved / "project.avc").write_bytes(archive.read("project.avc"))
    (saved / "bundle.zip").write_bytes(content)
    return await PROJECT.open_from_library(
        LibraryOpenRequest(library_id="acme_room", project_name="Acme Room")
    )


# --- the doors ---------------------------------------------------------------


async def test_a_new_python_driver_is_matched_to_the_found_device(world):
    await CODE_VIEW.create_python_driver(
        PythonDriverCreateRequest(id=DRIVER_ID, source=_python_source(discovery=True))
    )

    assert world.on_the_page() == DRIVER_ID
    assert await world.after_the_next_scan() == DRIVER_ID
    assert ("simulation told", (DRIVER_ID,)) in world.events
    assert world.events[-1] == ("announced", DRIVER_ID)


async def test_a_python_driver_s_plain_save_changes_nothing_running_and_its_reload_does(world):
    world.installed_python()
    world.devices.on = {"d1": DRIVER_ID}

    saved = await CODE_VIEW.save_python_driver_source(
        DRIVER_ID, {"source": _python_source(discovery=True)}
    )
    assert saved["status"] == "saved"
    assert world.events == []
    assert world.on_the_page() is None

    reloaded = await CODE_VIEW.reload_python_driver_endpoint(DRIVER_ID)
    assert reloaded["devices_reconnected"] == ["d1"]
    assert world.events == [
        ("simulation told", (DRIVER_ID,)),
        ("devices built", DRIVER_ID),
        ("announced", DRIVER_ID),
    ]
    assert world.on_the_page() == DRIVER_ID
    assert await world.after_the_next_scan() == DRIVER_ID


async def test_a_reload_that_renamed_the_driver_builds_the_devices_on_both_ids(world):
    world.installed_python()
    world.devices.on = {"d1": DRIVER_ID, "d2": "acme_widget_v2"}
    source = _python_source(discovery=True).replace(repr(DRIVER_ID), "'acme_widget_v2'")
    (world.repo / f"{DRIVER_ID}.py").write_text(source, encoding="utf-8")

    reloaded = await CODE_VIEW.reload_python_driver_endpoint(DRIVER_ID)

    assert reloaded["driver_id"] == "acme_widget_v2"
    assert sorted(reloaded["devices_reconnected"]) == ["d1", "d2"]
    assert ("simulation told", ("acme_widget_v2", DRIVER_ID)) in world.events
    assert world.on_the_page() == "acme_widget_v2"


async def test_a_project_imported_with_its_driver_matches_the_found_device(world):
    world.devices.on = {"waiting": DRIVER_ID}

    answer = await PROJECT.import_library_project(
        _Request(_Upload("acme_room.zip", _project_bundle()))
    )

    assert answer["installed_drivers"] == [DRIVER_ID]
    assert world.on_the_page() == DRIVER_ID
    assert await world.after_the_next_scan() == DRIVER_ID
    assert world.events == [
        ("simulation told", (DRIVER_ID,)),
        ("devices built", DRIVER_ID),
        ("announced", DRIVER_ID),
    ]


async def test_a_project_imported_with_no_driver_changes_nothing(world):
    project = project_library.create_blank_project("acme_room", "Acme Room").model_dump(mode="json")

    await PROJECT.import_library_project(
        _Request(_Upload("acme_room.avc", json.dumps(project).encode()))
    )

    assert world.events == []
    assert world.on_the_page() is None


async def test_a_library_copy_opened_with_its_driver_matches_the_found_device(world):
    answer = await _open_library_copy()

    assert answer["installed_drivers"] == [DRIVER_ID]
    assert world.on_the_page() == DRIVER_ID
    assert await world.after_the_next_scan() == DRIVER_ID


async def test_an_open_tells_the_simulation_before_the_project_reloads_and_builds_no_device_twice(world):
    """The simulator reads the driver before the opened project's devices
    first come up, the project's reload is what builds them, and the
    Programmers hear of the change once the opened project runs, so the
    devices each driver lists are the opened project's."""
    await _open_library_copy()

    reloaded = world.events.index(("project reloaded",))
    assert world.events.index(("simulation told", (DRIVER_ID,))) < reloaded
    assert ("devices built", DRIVER_ID) not in world.events[reloaded:]
    assert world.events[-1][0] == "announced"


async def test_an_open_that_fails_after_its_drivers_went_in_still_follows_them(world, monkeypatch):
    """The drivers an open installed are this server's whatever came of the
    rest of the open, so the running project's waiting device comes up and
    the found device is matched, though no project reloads."""
    world.devices.on = {"waiting": DRIVER_ID}

    def _disk_full(*_args, **_kwargs):
        raise OSError("No space left on device")

    monkeypatch.setattr(project_library, "save_project", _disk_full)
    with pytest.raises(OSError):
        await _open_library_copy()

    assert world.on_the_page() == DRIVER_ID
    assert ("devices built", DRIVER_ID) in world.events
    assert ("project reloaded",) not in world.events
