"""Which file serves a driver id, and which id a file declares: the loader's
answer, held against every reader that asks.

The id a ``.py`` file declares is read from its source without running it,
and must name the class the runtime registers (``driver_class_in``: the first
by name, an inherited table counting), so each source shape here is imported
for real and the two answers compared.

The conflicting-copies library puts several files on one id (a renamed copy
beside the file named for the id, a later folder over an earlier one, a Python
file and a definition, a refused file beside a good copy), loads it with the
real loader, and asks each listing which copy it shows.

Every id and file stem starts with ``acme_widget`` or ``acme_`` and the
``isolated`` fixture puts the registry and ``sys.modules`` back.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from openavc.drivers import refused_files, registry
from openavc.drivers.driver_loader import driver_id_from_file
from openavc.drivers.python_info import ExtractError, extract_python_driver_info_full
from openavc.drivers.python_modules import driver_class_in, read_python_driver_file

BASE = "from openavc.drivers.base import BaseDriver\n"


@pytest.fixture
def isolated():
    saved = dict(registry._DRIVER_REGISTRY)
    before = set(sys.modules)
    yield
    registry._DRIVER_REGISTRY.clear()
    registry._DRIVER_REGISTRY.update(saved)
    refused_files.forget_all()
    for name in set(sys.modules) - before:
        if "acme_" in name:
            sys.modules.pop(name, None)


# --- the id a Python file declares, against the class the runtime takes ------

SHAPES = {
    "one driver class": (
        "class AcmeWidgetDriver(BaseDriver):\n"
        "    DRIVER_INFO = {'id': 'acme_widget'}\n"
    ),
    "a subclass declares its own id and sorts first": (
        "class WidgetDriver(BaseDriver):\n"
        "    DRIVER_INFO = {'id': 'acme_widget'}\n"
        "class AcmeWidgetPro(WidgetDriver):\n"
        "    DRIVER_INFO = {'id': 'acme_widget_pro'}\n"
    ),
    "a subclass inherits the table and sorts first": (
        "class WidgetDriver(BaseDriver):\n"
        "    DRIVER_INFO = {'id': 'acme_widget'}\n"
        "class AcmeWidgetPro(WidgetDriver):\n"
        "    pass\n"
    ),
    "a helper class with a table, deriving from nothing, sorts first": (
        "class AcmeDefaults:\n"
        "    DRIVER_INFO = {'id': 'acme_defaults'}\n"
        "class WidgetDriver(BaseDriver):\n"
        "    DRIVER_INFO = {'id': 'acme_widget'}\n"
    ),
    "an annotated table": (
        "from typing import Any\n"
        "class AcmeWidgetDriver(BaseDriver):\n"
        "    DRIVER_INFO: dict[str, Any] = {'id': 'acme_widget'}\n"
    ),
    "a class whose table has an empty id is passed over": (
        "class AcmeBase(BaseDriver):\n"
        "    DRIVER_INFO = {'id': ''}\n"
        "class WidgetDriver(AcmeBase):\n"
        "    DRIVER_INFO = {'id': 'acme_widget'}\n"
    ),
    "a later key of the same name wins": (
        "class AcmeWidgetDriver(BaseDriver):\n"
        "    DRIVER_INFO = {'id': 'acme_old', 'id': 'acme_widget'}\n"
    ),
    "a table at module level is no driver": "DRIVER_INFO = {'id': 'acme_widget'}\n",
    "a table built in a function is no driver": (
        "def info():\n"
        "    DRIVER_INFO = {'id': 'acme_widget'}\n"
        "    return DRIVER_INFO\n"
    ),
}


def _runtime_id(path: Path) -> str | None:
    driver_class = read_python_driver_file(path).driver_class
    return driver_class.DRIVER_INFO["id"] if driver_class is not None else None


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_the_id_read_from_the_source_is_the_one_the_runtime_registers(
    tmp_path, isolated, shape
):
    path = tmp_path / "acme_shape.py"
    path.write_text(BASE + SHAPES[shape], encoding="utf-8")
    assert driver_id_from_file(path) == _runtime_id(path)


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_the_checked_table_is_the_one_the_runtime_registers(tmp_path, isolated, shape):
    path = tmp_path / "acme_shape.py"
    path.write_text(BASE + SHAPES[shape], encoding="utf-8")
    runtime = _runtime_id(path)
    try:
        info, _opaque = extract_python_driver_info_full(path)
    except ExtractError:
        info = {}
    assert (info.get("id") or None) == runtime


def test_a_computed_id_is_not_read(tmp_path):
    path = tmp_path / "acme_widget.py"
    path.write_text(
        BASE + "ID = 'acme_widget'\nclass AcmeWidgetDriver(BaseDriver):\n    DRIVER_INFO = {'id': ID}\n",
        encoding="utf-8",
    )
    assert driver_id_from_file(path) is None


def test_the_class_chosen_is_the_one_driver_class_in_takes(tmp_path, isolated):
    """The shape the runtime and the source reader once disagreed on, run
    through ``driver_class_in`` itself rather than the read verdict."""
    path = tmp_path / "acme_two.py"
    path.write_text(BASE + SHAPES["a subclass declares its own id and sorts first"], encoding="utf-8")
    read = read_python_driver_file(path)
    module = sys.modules[read.driver_class.__module__]
    assert driver_class_in(module, module.__name__).__name__ == "AcmeWidgetPro"
    assert driver_id_from_file(path) == "acme_widget_pro"


# --- the conflicting-copies library: every listing shows the copy that runs --


def _definition(driver_id: str, name: str, transport: str = "tcp") -> str:
    return f"id: {driver_id}\nname: {name}\ntransport: {transport}\n"


def _python(class_name: str, driver_id: str, name: str, *, broken: bool = False) -> str:
    return (
        BASE
        + ("raise RuntimeError('broken on purpose')\n" if broken else "")
        + f"class {class_name}(BaseDriver):\n"
        + f"    DRIVER_INFO = {{'id': {driver_id!r}, 'name': {name!r}}}\n"
    )


# Each copy names itself, so a reader's choice can be told by its name.
COPIES = {
    # a later directory over an earlier one
    ("built_in", "acme_override.avcdriver"): _definition("acme_override", "Built in"),
    ("repo", "acme_override.avcdriver"): _definition("acme_override", "User copy"),
    # in one directory, the file named for the id over a renamed copy
    ("repo", "a_copy.avcdriver"): _definition("acme_widget", "Renamed copy"),
    ("repo", "acme_widget.avcdriver"): _definition("acme_widget", "Named"),
    ("repo", "acme_py.py"): _python("PyDriver", "acme_py", "Named"),
    ("repo", "a_py_copy.py"): _python("PyDriver", "acme_py", "Renamed copy"),
    # a Python file over a definition, wherever each is
    ("repo", "acme_mixed.avcdriver"): _definition("acme_mixed", "Definition"),
    ("repo", "acme_mixed.py"): _python("MixedDriver", "acme_mixed", "Python"),
    # a refused file never serves: its good copy does
    ("repo", "acme_refused.avcdriver"): _definition("acme_refused", "Refused", "carrier_pigeon"),
    ("repo", "b_copy.avcdriver"): _definition("acme_refused", "Good copy"),
    ("repo", "acme_broken.py"): _python("BrokenDriver", "acme_broken", "Refused", broken=True),
    ("repo", "b_py_copy.py"): _python("BrokenDriver", "acme_broken", "Good copy"),
}
IDS = ["acme_override", "acme_widget", "acme_py", "acme_mixed", "acme_refused", "acme_broken"]


@pytest.fixture
def library(tmp_path, isolated):
    from openavc.drivers.driver_loader import load_all_drivers

    for (folder, name), text in COPIES.items():
        (tmp_path / folder).mkdir(exist_ok=True)
        (tmp_path / folder / name).write_text(text, encoding="utf-8")
    dirs = [tmp_path / "built_in", tmp_path / "repo"]
    load_all_drivers(dirs)
    return dirs


def _running(driver_id: str) -> str:
    """The name of the copy the loader registered."""
    return registry.get_driver_class(driver_id).DRIVER_INFO["name"]


def _named(path: Path) -> str:
    """The name a library file gives itself."""
    if path.suffix == ".py":
        from openavc.drivers.driver_loader import python_driver_info

        return python_driver_info(path)["name"]
    import yaml

    return yaml.safe_load(path.read_text(encoding="utf-8"))["name"]


def test_the_library_holds_the_conflicts_it_is_meant_to(library):
    assert {driver_id: _running(driver_id) for driver_id in IDS} == {
        "acme_override": "User copy",
        "acme_widget": "Named",
        "acme_py": "Named",
        "acme_mixed": "Python",
        "acme_refused": "Good copy",
        "acme_broken": "Good copy",
    }


@pytest.mark.parametrize("driver_id", IDS)
def test_the_serving_file_is_the_copy_the_loader_registered(library, driver_id):
    from openavc.drivers.driver_loader import serving_file, serving_files

    assert _named(serving_file(library, driver_id)) == _running(driver_id)
    assert serving_files(library)[driver_id] == serving_file(library, driver_id)


def test_the_builder_lists_the_definition_that_runs(library):
    from openavc.drivers.driver_loader import list_driver_definitions

    listed = {d["id"]: d for d in list_driver_definitions(library)}
    for driver_id, definition in listed.items():
        assert definition["name"] == _running(driver_id)
        assert _named(Path(definition["_source_file"])) == _running(driver_id)
    # an id a Python file serves is the Code view's, not the Builder's
    assert sorted(listed) == ["acme_override", "acme_refused", "acme_widget"]


def test_the_code_view_lists_the_python_file_that_runs(library):
    from openavc.drivers.driver_loader import list_python_drivers

    listed = {d["id"]: d for d in list_python_drivers(library)}
    assert sorted(listed) == ["acme_broken", "acme_mixed", "acme_py"]
    for driver_id, entry in listed.items():
        assert entry["name"] == _running(driver_id)
        assert (entry["loaded"], entry["load_error"]) == (True, None)
        assert _named(library[1] / entry["filename"]) == _running(driver_id)


async def test_the_installed_list_shows_the_copy_that_runs(library, monkeypatch):
    from openavc.api.routes import drivers as drivers_routes

    monkeypatch.setattr(drivers_routes, "_get_driver_repo_dir", lambda: library[1])
    rows = (await drivers_routes.list_installed_community_drivers())["drivers"]
    # The IDE keeps the last row of an id (a Map by id), so that is the row
    # held to the loader; there is one per id.
    by_id = {row["id"]: row for row in rows}
    assert len(rows) == len(by_id) == len(IDS)
    for driver_id, row in by_id.items():
        assert row["name"] == _running(driver_id)
        assert _named(library[1] / row["filename"]) == _running(driver_id)
        assert row["load_error"] is None


# --- the readers that map an id back to its file ------------------------------


@pytest.mark.parametrize("driver_id", IDS)
def test_the_audit_ships_the_file_that_runs(library, monkeypatch, driver_id):
    import openavc.system_config as system_config
    from openavc.audit.driver_choice import driver_main_file

    monkeypatch.setattr(system_config, "DRIVER_DEFINITIONS_DIR", library[0])
    monkeypatch.setattr(system_config, "DRIVER_REPO_DIR", library[1])
    assert _named(driver_main_file(driver_id)) == _running(driver_id)


def test_the_audit_names_no_file_for_a_definition_whose_file_is_gone(library, monkeypatch):
    import openavc.system_config as system_config
    from openavc.audit.driver_choice import driver_main_file

    monkeypatch.setattr(system_config, "DRIVER_DEFINITIONS_DIR", library[0])
    monkeypatch.setattr(system_config, "DRIVER_REPO_DIR", library[1])
    for path in library[1].glob("*acme_widget*.avcdriver"):
        path.unlink()
    (library[1] / "a_copy.avcdriver").unlink()
    assert driver_main_file("acme_widget") is None


@pytest.mark.parametrize("driver_id", IDS)
def test_an_update_replaces_the_file_that_runs(library, driver_id):
    from openavc.api.routes.drivers import _file_an_update_replaces

    assert _named(_file_an_update_replaces(library[1], driver_id)) == _running(driver_id)


def test_an_update_finds_a_python_driver_named_otherwise(tmp_path, isolated):
    from openavc.api.routes.drivers import _file_an_update_replaces

    (tmp_path / "uploaded_name.py").write_text(
        _python("WidgetDriver", "acme_widget", "Uploaded"), encoding="utf-8"
    )
    assert _file_an_update_replaces(tmp_path, "acme_widget") == tmp_path / "uploaded_name.py"


@pytest.mark.parametrize("driver_id", IDS)
def test_an_export_carries_the_file_that_runs(library, monkeypatch, driver_id):
    from openavc.core import project_library

    monkeypatch.setattr(project_library, "_DRIVER_REPO_DIR", library[1])
    exported = project_library._find_driver_files([{"driver_id": driver_id, "source": "community"}])
    assert [_named(path) for _name, path in exported] == [_running(driver_id)]


def test_taking_a_definition_away_leaves_the_python_driver_beside_it_serving(library):
    from openavc.drivers.driver_loader import delete_driver_definition, restore_driver_registration

    python_class = registry.get_driver_class("acme_mixed")
    assert delete_driver_definition("acme_mixed", library)
    assert restore_driver_registration("acme_mixed", library)
    assert registry.get_driver_class("acme_mixed") is python_class


def test_taking_a_python_driver_away_lets_the_definition_beside_it_serve(library):
    from openavc.drivers.driver_loader import restore_driver_registration
    from openavc.drivers.python_modules import forget_module

    (library[1] / "acme_mixed.py").unlink()
    forget_module(library[1] / "acme_mixed.py")
    assert restore_driver_registration("acme_mixed", library)
    assert _running("acme_mixed") == "Definition"


def test_the_builder_s_delete_takes_every_copy_of_a_definition(library):
    from openavc.drivers.driver_loader import delete_driver_definition, restore_driver_registration

    assert delete_driver_definition("acme_widget", library)
    assert sorted(p.name for p in library[1].glob("*.avcdriver") if "acme_widget" in p.read_text()) == []
    assert not restore_driver_registration("acme_widget", library)
    assert registry.get_driver_class("acme_widget") is None


def test_the_simulator_builds_each_definition_from_the_copy_that_runs(library):
    from openavc.simulator.engine import SimulatorManager

    available = SimulatorManager().discover([str(folder) for folder in library])
    definitions = {
        driver_id: _named(info.avcdriver_path)
        for driver_id, info in available.items()
        if info.avcdriver_path is not None
    }
    # acme_mixed runs from its Python file, which has no simulator here
    assert definitions == {
        driver_id: _running(driver_id) for driver_id in ("acme_override", "acme_widget", "acme_refused")
    }
