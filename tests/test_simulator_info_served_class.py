"""A Python simulator's ``SIMULATOR_INFO`` is read off the class the simulator
serves: the one whose table names a driver id (``simulator_claims``), its own
table or the one a base in the same file gives it. A base another class builds
on leaves ``driver_id`` out, which is what the "two classes name one driver
id" sentence advises, so the base's table is not the simulator's.

A table merged from another mapping (``{**Base.SIMULATOR_INFO, ...}``) is read
as far as it can be: a required field it may hold is named as not checked,
never reported missing.

All drivers and simulators here are invented (acme_widget).
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from openavc.drivers.simulator_info import check_simulator_file, read_simulator_info
from openavc.simulator.validate import validate_python_driver

_DRIVER = '''\
    from openavc.drivers.base import BaseDriver


    class AcmeWidgetDriver(BaseDriver):
        DRIVER_INFO = {
            "id": "acme_widget",
            "name": "Acme Widget",
            "transport": "tcp",
            "state_variables": {"power": {"type": "boolean", "label": "Power"}},
            "commands": {"power_on": {"label": "Power On"}},
        }
'''

_BASE = '''\
    from openavc.simulator.tcp_simulator import TCPSimulator


    class AcmeBaseSimulator(TCPSimulator):
        SIMULATOR_INFO = {
            "name": "Acme Base",
            "transport": "tcp",
            "initial_state": {"power": False},
        }

        def handle_command(self, data):
            return "power_on"

'''


def _write(tmp_path: Path, simulator: str) -> Path:
    driver = tmp_path / "acme_widget.py"
    driver.write_text(textwrap.dedent(_DRIVER), encoding="utf-8")
    (tmp_path / "acme_widget_sim.py").write_text(textwrap.dedent(simulator), encoding="utf-8")
    return driver


_OWN_TABLE = _BASE + '''\
    class AcmeWidgetSimulator(AcmeBaseSimulator):
        SIMULATOR_INFO = {
            "driver_id": "acme_widget",
            "name": "Acme Widget Simulator",
            "transport": "tcp",
            "initial_state": {"power": True},
        }
'''

_MERGED_TABLE = _BASE + '''\
    class AcmeWidgetSimulator(AcmeBaseSimulator):
        SIMULATOR_INFO = {**AcmeBaseSimulator.SIMULATOR_INFO, "driver_id": "acme_widget"}
'''


def test_the_table_of_the_class_that_names_the_id_is_read(tmp_path: Path) -> None:
    driver = _write(tmp_path, _OWN_TABLE)
    info = read_simulator_info(driver.with_name("acme_widget_sim.py"))
    assert info["driver_id"] == "acme_widget"
    assert info["name"] == "Acme Widget Simulator"


def test_a_merged_table_is_read_off_the_class_that_names_the_id(tmp_path: Path) -> None:
    driver = _write(tmp_path, _MERGED_TABLE)
    info = read_simulator_info(driver.with_name("acme_widget_sim.py"))
    assert info["driver_id"] == "acme_widget"


def test_a_file_where_no_class_names_an_id_reads_the_first_table(tmp_path: Path) -> None:
    driver = _write(tmp_path, _BASE)
    info = read_simulator_info(driver.with_name("acme_widget_sim.py"))
    assert info == {"name": "Acme Base", "transport": "tcp", "initial_state": {"power": False}}


def test_the_pair_with_its_own_table_validates_with_no_errors(tmp_path: Path) -> None:
    result = validate_python_driver(_write(tmp_path, _OWN_TABLE))
    assert result.errors == []


def test_the_pair_with_a_merged_table_validates_with_no_errors(tmp_path: Path) -> None:
    result = validate_python_driver(_write(tmp_path, _MERGED_TABLE))
    assert result.errors == []
    notes = " ".join(issue.message for issue in result.issues)
    assert "'name' could not be checked" in notes
    assert "initial_state is computed" in notes


def test_the_contract_check_judges_the_served_classs_table(tmp_path: Path) -> None:
    simulator = _BASE.replace('"transport": "tcp",', '"transport": "tcp", "default_port": "x",') + '''\
    class AcmeWidgetSimulator(AcmeBaseSimulator):
        SIMULATOR_INFO = {
            "driver_id": "acme_widget",
            "name": "Acme Widget Simulator",
            "transport": "tcp",
            "default_port": 23,
            "initial_state": {"power": True},
        }
'''
    driver = _write(tmp_path, simulator)
    verdict = check_simulator_file(driver.with_name("acme_widget_sim.py"))
    assert verdict.errors == []
