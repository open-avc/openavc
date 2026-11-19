"""Which simulator a ``_sim.py`` file offers for each driver id, and that the
simulator can import any file Python can.

Platform test: invented simulators for an invented driver (``acme_widget``),
written into a temporary driver folder and found the way the simulator finds
them (``SimulatorManager.discover``).
"""

from __future__ import annotations

import sys
import textwrap

import pytest

from openavc.core.connection_fault import SimulatorCause
from openavc.simulator.engine import SimulatorManager, SimulatorRefusal
from openavc.simulator.simulator_file import load_simulator_file, module_name

_SIMULATOR = '''
class {name}(TCPSimulator):
    SIMULATOR_INFO = {{"driver_id": "{driver_id}", "name": "{name}", "initial_state": {{}}}}

    def handle_command(self, data):
        return None
'''


def _sim_file(folder, stem: str, body: str):
    path = folder / f"{stem}_sim.py"
    path.write_text(
        "from openavc.simulator.tcp_simulator import TCPSimulator\n"
        + textwrap.dedent(body),
        encoding="utf-8",
    )
    return path


def _simulator(name: str, driver_id: str = "acme_widget") -> str:
    return _SIMULATOR.format(name=name, driver_id=driver_id)


def _discover(*folders) -> SimulatorManager:
    manager = SimulatorManager()
    manager.discover([str(folder) for folder in folders])
    return manager


async def _refusal(manager: SimulatorManager, driver_id: str = "acme_widget") -> SimulatorRefusal:
    with pytest.raises(SimulatorRefusal) as refused:
        await manager.start_device(driver_id, "dev1")
    return refused.value


# ── Importing the file ──────────────────────────────────────────────────────


def test_a_simulator_whose_file_defines_a_dataclass_loads(tmp_path):
    # Under string annotations a dataclass looks its own module up by name
    # while the class is made, so the module has to be registered first, as
    # an import registers it.
    path = tmp_path / "acme_widget_sim.py"
    path.write_text(
        "from __future__ import annotations\n"
        "from dataclasses import dataclass\n"
        "from openavc.simulator.tcp_simulator import TCPSimulator\n"
        "@dataclass(frozen=True)\n"
        "class Reading:\n"
        "    level: int\n"
        + _simulator("AcmeWidgetSimulator"),
        encoding="utf-8",
    )
    manager = _discover(tmp_path)
    assert manager._load_errors == {}
    assert manager._available["acme_widget"].simulator_class.__name__ == "AcmeWidgetSimulator"


def test_a_rescan_imports_each_file_under_the_same_name(tmp_path):
    path = _sim_file(tmp_path, "acme_widget", _simulator("AcmeWidgetSimulator"))
    manager = _discover(tmp_path)
    before = set(sys.modules)
    manager.rescan()
    manager.rescan()
    assert set(sys.modules) - before == set()
    assert module_name(path) in sys.modules


def test_a_file_that_fails_to_import_leaves_no_module(tmp_path):
    path = _sim_file(tmp_path, "acme_widget", "raise RuntimeError('no table')\n")
    with pytest.raises(RuntimeError):
        load_simulator_file(path)
    assert module_name(path) not in sys.modules


def test_two_files_of_one_name_in_two_folders_import_as_two_modules(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    first = _sim_file(tmp_path / "a", "acme_widget", "")
    second = _sim_file(tmp_path / "b", "acme_widget", "")
    assert module_name(first) != module_name(second)


# ── One class per driver id ─────────────────────────────────────────────────


def test_a_class_listed_under_a_second_name_is_one_simulator(tmp_path):
    _sim_file(
        tmp_path, "acme_widget",
        _simulator("AcmeWidgetSimulator") + "\nSIMULATOR_CLASS = AcmeWidgetSimulator\n",
    )
    manager = _discover(tmp_path)
    assert manager._load_errors == {}
    assert manager._available["acme_widget"].simulator_class.__name__ == "AcmeWidgetSimulator"


def test_a_base_class_the_file_imports_is_not_its_simulator(tmp_path):
    found = load_simulator_file(_sim_file(tmp_path, "acme_widget", _simulator("AcmeWidgetSimulator")))
    assert list(found.classes) == ["acme_widget"]
    assert found.classes["acme_widget"].__name__ == "AcmeWidgetSimulator"
    assert found.refused == {}


def test_a_class_with_no_driver_id_serves_no_driver(tmp_path):
    _sim_file(tmp_path, "acme_widget", _simulator("AcmeWidgetSimulator", driver_id=""))
    assert _discover(tmp_path)._available == {}


def test_one_file_may_serve_two_drivers_with_a_class_each(tmp_path):
    _sim_file(
        tmp_path, "acme_widget",
        _simulator("AcmeWidgetSimulator") + _simulator("AcmeGadgetSimulator", "acme_gadget"),
    )
    manager = _discover(tmp_path)
    assert manager._load_errors == {}
    assert set(manager._available) == {"acme_widget", "acme_gadget"}


async def test_two_classes_naming_one_driver_id_serve_neither_and_say_why(tmp_path):
    _sim_file(tmp_path, "acme_widget", _simulator("AlphaSimulator") + _simulator("ZetaSimulator"))
    manager = _discover(tmp_path)
    assert "acme_widget" not in manager._available
    refusal = await _refusal(manager)
    assert refusal.cause == SimulatorCause.BUILD_FAILED
    assert "AlphaSimulator" in refusal.detail and "ZetaSimulator" in refusal.detail
    assert "'acme_widget'" in refusal.detail


async def test_a_class_inheriting_another_classes_driver_id_is_a_second_simulator(tmp_path):
    _sim_file(
        tmp_path, "acme_widget",
        _simulator("AcmeWidgetBase") + "\nclass AcmeWidgetSimulator(AcmeWidgetBase):\n    pass\n",
    )
    manager = _discover(tmp_path)
    refusal = await _refusal(manager)
    assert refusal.cause == SimulatorCause.BUILD_FAILED
    assert "AcmeWidgetBase" in refusal.detail and "AcmeWidgetSimulator" in refusal.detail


def test_a_later_folders_simulator_still_replaces_an_earlier_folders(tmp_path):
    built_in, installed = tmp_path / "built_in", tmp_path / "driver_repo"
    built_in.mkdir()
    installed.mkdir()
    _sim_file(built_in, "acme_widget", _simulator("BuiltInSimulator"))
    _sim_file(installed, "acme_widget", _simulator("InstalledSimulator"))
    manager = _discover(built_in, installed)
    assert manager._load_errors == {}
    assert manager._available["acme_widget"].simulator_class.__name__ == "InstalledSimulator"


async def test_two_files_in_one_folder_naming_one_driver_id_serve_neither(tmp_path):
    _sim_file(tmp_path, "acme_widget", _simulator("AcmeWidgetSimulator"))
    _sim_file(tmp_path, "acme_widget_v2", _simulator("AcmeWidgetTwoSimulator"))
    manager = _discover(tmp_path)
    assert "acme_widget" not in manager._available
    refusal = await _refusal(manager)
    assert refusal.cause == SimulatorCause.BUILD_FAILED
    assert refusal.detail.startswith(
        "AcmeWidgetSimulator in acme_widget_sim.py and "
        "AcmeWidgetTwoSimulator in acme_widget_v2_sim.py each name driver id 'acme_widget'"
    )


def test_a_file_that_fails_to_import_leaves_another_files_simulator_serving(tmp_path):
    _sim_file(tmp_path, "acme_widget", "raise RuntimeError('no table')\n")
    _sim_file(tmp_path, "acme_widget_v2", _simulator("AcmeWidgetSimulator"))
    manager = _discover(tmp_path)
    assert manager._available["acme_widget"].simulator_class.__name__ == "AcmeWidgetSimulator"
    assert manager._load_errors == {}
