"""A project's own drivers come in the way every other driver does.

Platform test: invented drivers (``acme_widget``) in invented project bundles,
through the library's import and open doors and the starter refresh.

A project bundle (an export, a starter) carries the drivers its devices use.
Each comes in as one change with the companions it owns, through
``drivers/driver_change.admit``: the platform floor it declares is checked,
it is loaded from a staged copy, and only then is it put in the driver folder
and registered. One that is refused is left out, and the import's or the
open's answer says why, beside the devices that use it; the others still go
in. A driver file already in the folder, or an id already served, is kept as
it is, companions and all.
"""

from __future__ import annotations

import io
import json
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

from openavc.api.models import LibraryOpenRequest
from openavc.api.routes import project as project_routes
from openavc.core import project_library
from openavc.drivers import driver_loader, refused_files, registry
from openavc.version import __version__

DRIVER_ID = "acme_widget"


@pytest.fixture
def world(tmp_path: Path, monkeypatch):
    """A library, a driver folder, a registry with no Acme driver and an
    empty record of refused files, all this test's own."""
    lib = tmp_path / "saved_projects"
    repo = tmp_path / "driver_repo"
    seed = tmp_path / "templates"
    for folder in (lib, repo, seed):
        folder.mkdir()
    monkeypatch.setattr(project_library, "_lib_dir", lambda: lib)
    monkeypatch.setattr(project_library, "_DRIVER_REPO_DIR", repo)
    monkeypatch.setattr(project_library, "_SEED_DIR", seed)
    monkeypatch.setattr(
        registry, "_DRIVER_REGISTRY",
        {k: v for k, v in registry._DRIVER_REGISTRY.items() if not k.startswith("acme")},
    )
    monkeypatch.setattr(refused_files, "_refused", {})
    monkeypatch.setattr(refused_files, "_noticed", {})
    modules_before = {n: m for n, m in sys.modules.items() if "acme" in n}
    yield SimpleNamespace(lib=lib, repo=repo, seed=seed)
    for name in [n for n in sys.modules if "acme" in n]:
        if name in modules_before:
            sys.modules[name] = modules_before[name]
        else:
            sys.modules.pop(name, None)


def _definition(driver_id: str = DRIVER_ID, name: str = "Acme Widget", **extra: Any) -> dict:
    body: dict[str, Any] = {
        "id": driver_id,
        "name": name,
        "manufacturer": "Acme",
        "category": "utility",
        "version": "1.0.0",
        "transport": "tcp",
        "commands": {"power_on": {"label": "On", "send": "P\r", "params": {}}},
    }
    body.update(extra)
    return body


def _yaml(driver_id: str = DRIVER_ID, name: str = "Acme Widget", **extra: Any) -> bytes:
    return yaml.safe_dump(_definition(driver_id, name, **extra), sort_keys=False).encode()


def _python(driver_id: str = DRIVER_ID, name: str = "Acme Python") -> bytes:
    return (
        "from openavc.drivers.base import BaseDriver\n\n\n"
        "class AcmeDriver(BaseDriver):\n"
        f"    DRIVER_INFO = {{'id': {driver_id!r}, 'name': {name!r}, 'transport': 'tcp'}}\n"
    ).encode()


# A floor this release does not meet, and a misspelled key the loader only warns about.
NEEDS_NEWER = {"min_platform_version": "99.0.0", "comands": {}}
BOGUS = {"state_variables": {"power": {"type": "bogus", "label": "Power"}}}

FLOOR = (
    "This driver requires OpenAVC 99.0.0 or later. "
    f"You are running {__version__}. Please update OpenAVC first."
)


def _project(pid: str, *drivers: str) -> bytes:
    data = project_library.create_blank_project(pid, pid).model_dump(mode="json")
    data["devices"] = [
        {"id": f"d{i}", "name": f"Device {i}", "driver": driver, "config": {}}
        for i, driver in enumerate(drivers, start=1)
    ]
    return json.dumps(data).encode()


def _bundle(drivers: dict[str, bytes], *devices_on: str, pid: str = "acme_room") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("project.avc", _project(pid, *devices_on))
        for name, content in drivers.items():
            archive.writestr(f"drivers/{name}", content)
    return buf.getvalue()


def _import(drivers: dict[str, bytes], *devices_on: str) -> dict[str, Any]:
    return project_library.import_project(_bundle(drivers, *devices_on), "acme_room.zip")


def _files(repo: Path) -> set[str]:
    return {p.name for p in repo.iterdir() if p.name != "__pycache__"}


def _not_installed(driver_id: str, why: str) -> str:
    return f"Driver '{driver_id}' came with this project but was not installed: {why}"


# --- the import ---------------------------------------------------------------------


def test_a_bundled_driver_is_admitted_and_registered(world):
    answer = _import({"acme_widget.avcdriver": _yaml()}, DRIVER_ID)

    assert answer["installed_drivers"] == [DRIVER_ID]
    assert answer["missing_drivers"] == [] and answer["warnings"] == []
    assert registry.get_driver_class(DRIVER_ID).DRIVER_INFO["name"] == "Acme Widget"
    assert (world.repo / "acme_widget.avcdriver").read_bytes() == _yaml()


def test_a_bundled_driver_that_needs_a_newer_openavc_is_left_out_and_the_import_says_why(world):
    answer = _import({"acme_widget.avcdriver": _yaml(**NEEDS_NEWER)}, DRIVER_ID)

    assert answer["installed_drivers"] == []
    assert [m["driver_id"] for m in answer["missing_drivers"]] == [DRIVER_ID]
    assert answer["warnings"] == [f"{_not_installed(DRIVER_ID, FLOOR)} (used by: d1)"]
    assert not registry.is_driver_registered(DRIVER_ID)
    assert _files(world.repo) == set()


def test_a_bundled_driver_that_does_not_load_is_left_out_and_the_import_says_why(world):
    reasons = driver_loader.validate_driver_definition(_definition(**BOGUS))
    assert reasons

    answer = _import({"acme_widget.avcdriver": _yaml(**BOGUS)}, DRIVER_ID)

    assert answer["installed_drivers"] == []
    assert answer["warnings"] == [
        f"{_not_installed(DRIVER_ID, 'Its file did not load: ' + '; '.join(reasons))} (used by: d1)"
    ]
    assert not registry.is_driver_registered(DRIVER_ID)
    assert _files(world.repo) == set()
    # Nothing is in the folder, so nothing is recorded as installed but not loading.
    assert refused_files.refusal_for(DRIVER_ID) is None


def test_a_refused_driver_no_device_uses_is_still_named(world):
    answer = _import({"acme_widget.avcdriver": _yaml(**NEEDS_NEWER)})

    assert answer["missing_drivers"] == []
    assert answer["warnings"] == [_not_installed(DRIVER_ID, FLOOR)]


def test_one_refused_driver_leaves_the_others_installed(world):
    answer = _import(
        {
            "acme_widget.avcdriver": _yaml(**NEEDS_NEWER),
            "acme_good.py": _python("acme_good"),
        },
        DRIVER_ID, "acme_good",
    )

    assert answer["installed_drivers"] == ["acme_good"]
    assert answer["warnings"] == [f"{_not_installed(DRIVER_ID, FLOOR)} (used by: d1)"]
    assert _files(world.repo) == {"acme_good.py"}
    assert registry.is_driver_registered("acme_good")


def test_a_python_driver_comes_in_with_its_companions(world):
    answer = _import(
        {
            "acme_widget.py": _python(),
            "acme_widget_sim.py": b"# simulator\n",
            "acme_widget_discovery.py": b"# discovery\n",
        },
        DRIVER_ID,
    )

    assert answer["installed_drivers"] == [DRIVER_ID]
    assert _files(world.repo) == {"acme_widget.py", "acme_widget_sim.py", "acme_widget_discovery.py"}


def test_companions_do_not_land_without_their_driver(world):
    """A bundle's Python driver whose id a YAML driver here already serves is
    kept out, and so are its simulator and discovery files: left in the
    folder, they would run in the simulator and Discovery for a driver that
    is not this one."""
    (world.repo / "acme_widget.avcdriver").write_bytes(_yaml(name="Mine"))
    driver_loader.load_all_drivers([world.repo])

    answer = _import(
        {
            "acme_widget.py": _python(),
            "acme_widget_sim.py": b"# simulator\n",
            "acme_widget_discovery.py": b"# discovery\n",
        },
        DRIVER_ID,
    )

    assert answer["installed_drivers"] == [] and answer["warnings"] == []
    assert _files(world.repo) == {"acme_widget.avcdriver"}
    assert registry.get_driver_class(DRIVER_ID).DRIVER_INFO["name"] == "Mine"


def test_a_companion_that_comes_without_its_driver_is_left_out(world):
    answer = _import({"acme_widget_sim.py": b"# simulator\n"})

    assert answer["installed_drivers"] == [] and answer["warnings"] == []
    assert _files(world.repo) == set()


def test_a_driver_file_already_in_the_folder_is_kept(world):
    (world.repo / "acme_widget.avcdriver").write_bytes(_yaml(name="Mine"))

    answer = _import({"acme_widget.avcdriver": _yaml(name="Theirs")}, DRIVER_ID)

    assert answer["installed_drivers"] == []
    assert (world.repo / "acme_widget.avcdriver").read_bytes() == _yaml(name="Mine")


def test_a_yaml_driver_comes_in_with_the_discovery_file_it_names(world):
    definition = _yaml(discovery={"python": {"file": "./acme_probe_discovery.py"}})
    answer = _import(
        {
            "acme_widget.avcdriver": definition,
            "acme_probe_discovery.py": b"async def probe(ctx):\n    return None\n",
        },
        DRIVER_ID,
    )

    assert answer["installed_drivers"] == [DRIVER_ID]
    assert _files(world.repo) == {"acme_widget.avcdriver", "acme_probe_discovery.py"}


# --- opening a library copy -----------------------------------------------------------


@pytest.fixture
def engine(tmp_path: Path, monkeypatch):
    active = tmp_path / "active"
    active.mkdir()
    stub = MagicMock()
    stub.project_path = active / "project.avc"
    stub.broadcast_ws = AsyncMock()
    stub.reload_project = AsyncMock()
    stub.replace_with = AsyncMock()
    stub.logins_needed = MagicMock(return_value=[])
    stub.plugin_logins_needed = MagicMock(return_value=[])
    monkeypatch.setattr(project_routes, "_get_engine", lambda: stub)
    monkeypatch.setattr("openavc.core.backup_manager.create_backup", lambda *a, **k: None)
    monkeypatch.setattr("openavc.api.routes.drivers.announce_drivers_changed", AsyncMock())
    return stub


def _saved_with_bundle(lib: Path, drivers: dict[str, bytes], *devices_on: str) -> None:
    saved = lib / "acme_room"
    saved.mkdir()
    (saved / "project.avc").write_bytes(_project("acme_room", *devices_on))
    (saved / "bundle.zip").write_bytes(_bundle(drivers, *devices_on))


async def test_an_opened_library_copy_says_which_of_its_drivers_were_left_out(world, engine):
    _saved_with_bundle(
        world.lib,
        {"acme_widget.avcdriver": _yaml(**NEEDS_NEWER), "acme_good.py": _python("acme_good")},
        DRIVER_ID, "acme_good",
    )

    answer = await project_routes.open_from_library(
        LibraryOpenRequest(library_id="acme_room", project_name="Acme Room")
    )

    assert answer["installed_drivers"] == ["acme_good"]
    assert answer["warnings"] == [f"{_not_installed(DRIVER_ID, FLOOR)} (used by: d1)"]
    assert _files(world.repo) == {"acme_good.py"}


async def test_an_opened_library_copy_whose_bundle_cannot_be_read_says_so(world, engine):
    _saved_with_bundle(world.lib, {})
    (world.lib / "acme_room" / "bundle.zip").write_bytes(b"not a zip")

    answer = await project_routes.open_from_library(
        LibraryOpenRequest(library_id="acme_room", project_name="Acme Room")
    )

    assert answer["installed_drivers"] == []
    assert answer["warnings"] == [
        "The drivers and plugins saved with this project could not be installed: "
        "File is not a zip file"
    ]


# --- the starter refresh --------------------------------------------------------------


def _starter(path: Path, driver: bytes) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("project.avc", "{}")
        archive.writestr("drivers/acme_widget.py", driver)


@pytest.fixture
def starter(world):
    """A seeded starter whose bundle an older release wrote, its driver
    unpacked unedited into the driver folder, and this release's bundle."""
    (world.lib / "simple_projector").mkdir()
    _starter(world.lib / "simple_projector" / "bundle.zip", _python(name="Old"))
    (world.lib / ".seeded").touch()
    (world.repo / "acme_widget.py").write_bytes(_python(name="Old"))
    return world


def test_a_refreshed_starter_driver_is_admitted_in_place_and_loads_at_the_start(starter):
    _starter(starter.seed / "simple_projector.zip", _python(name="New"))

    project_library.ensure_starter_projects()

    assert (starter.repo / "acme_widget.py").read_bytes() == _python(name="New")
    registry._DRIVER_REGISTRY.pop(DRIVER_ID, None)
    driver_loader.load_all_drivers([starter.repo])
    assert registry.get_driver_class(DRIVER_ID).DRIVER_INFO["name"] == "New"


def test_a_refreshed_starter_driver_that_does_not_load_keeps_the_copy_it_had(starter):
    _starter(starter.seed / "simple_projector.zip", b"import acme_nonexistent_lib\n")
    old_bundle = (starter.lib / "simple_projector" / "bundle.zip").read_bytes()

    project_library.ensure_starter_projects()

    assert (starter.repo / "acme_widget.py").read_bytes() == _python(name="Old")
    assert _files(starter.repo) == {"acme_widget.py"}
    # The old bundle stays, so the next start (a release that fixes it) tries again.
    assert (starter.lib / "simple_projector" / "bundle.zip").read_bytes() == old_bundle


def test_a_starter_whose_old_bundle_cannot_be_read_takes_the_shipped_one(starter):
    (starter.lib / "simple_projector" / "bundle.zip").write_bytes(b"not a zip")
    _starter(starter.seed / "simple_projector.zip", _python(name="New"))

    project_library.ensure_starter_projects()

    shipped = (starter.seed / "simple_projector.zip").read_bytes()
    assert (starter.lib / "simple_projector" / "bundle.zip").read_bytes() == shipped
