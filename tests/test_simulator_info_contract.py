"""A Python simulator's ``SIMULATOR_INFO`` is held to the keys the driver
contract declares for it: a YAML driver's ``simulator:`` section's keys and the
seven that say which device it stands in for.

The doors: ``python -m openavc.drivers.check`` checks a driver's companion
simulator with the driver (and a ``_sim.py`` named on its own), and the
simulator validator holds a Python simulator's controls to the rules a YAML
driver's are held to. A setting written as a null reads as absent everywhere a
simulator's settings are read, in either form: the checks, the published
schema and the simulator itself.

All drivers and simulators here are invented (acme_widget).
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from openavc.drivers import spec
from openavc.drivers.avcdriver_semantic import validate_driver_definition
from openavc.drivers.check import check_driver_file, main
from openavc.drivers.simulator_info import check_simulator_info
from openavc.simulator.tcp_simulator import TCPSimulator
from openavc.simulator.validate import validate_python_driver
from openavc.simulator.yaml_auto import YAMLAutoSimulator

_SECTION_KEYS = tuple(spec.DEFS["simulatorSection"]["fields"])
_IDENTITY_KEYS = ("driver_id", "name", "transport", "default_port", "category", "delimiter", "tls")

_DRIVER = '''\
    from openavc.drivers.base import BaseDriver


    class AcmeWidgetDriver(BaseDriver):
        DRIVER_INFO = {
            "id": "acme_widget",
            "name": "Acme Widget",
            "transport": "tcp",
            "state_variables": {
                "power": {"type": "boolean", "label": "Power"},
            },
            "commands": {
                "power_on": {"label": "Power On"},
            },
        }
'''


def _simulator(info: str) -> str:
    """A companion simulator whose SIMULATOR_INFO is ``info``, a dict literal
    holding the given entries; a handler names the driver's command."""
    return textwrap.dedent('''\
        from openavc.simulator.tcp_simulator import TCPSimulator

        _LEVELS = ["low", "high"]


        class AcmeWidgetSimulator(TCPSimulator):
            SIMULATOR_INFO = {{
                "driver_id": "acme_widget",
                "name": "Acme Widget Simulator",
                "transport": "tcp",
                "initial_state": {{"power": False}},
                {info}
            }}

            def handle_command(self, data):
                return "power_on"
    ''').format(info=info)


def _pair(tmp_path: Path, info: str) -> Path:
    driver = tmp_path / "acme_widget.py"
    driver.write_text(textwrap.dedent(_DRIVER), encoding="utf-8")
    (tmp_path / "acme_widget_sim.py").write_text(_simulator(info), encoding="utf-8")
    return driver


# ── The declaration ──────────────────────────────────────────────────────────


def test_simulator_info_declares_the_section_keys_and_the_identity_keys():
    assert set(spec.SIMULATOR_IDENTITY) == set(_IDENTITY_KEYS)
    assert not set(spec.SIMULATOR_IDENTITY) & set(_SECTION_KEYS)
    assert tuple(spec.SIMULATOR_INFO_FIELDS) == _SECTION_KEYS + tuple(spec.SIMULATOR_IDENTITY)


# ── A null reads as absent ───────────────────────────────────────────────────


def test_a_python_simulator_with_a_null_setting_is_built_as_if_it_were_left_out():
    class AcmeWidgetSimulator(TCPSimulator):
        SIMULATOR_INFO = {
            "driver_id": "acme_widget",
            "name": None,
            "initial_state": None,
            "error_modes": None,
            "delays": None,
            "state_machines": None,
            "capacity": None,
            "single_client": None,
            "delimiter": None,
        }

        def handle_command(self, data):
            return None

    sim = AcmeWidgetSimulator("w1")
    assert sim.state == {}
    assert sim.name == "acme_widget"
    assert list(sim.available_errors) == ["communication_timeout"]


def _yaml_driver(simulator: dict) -> dict:
    return {
        "id": "acme_widget",
        "name": "Acme Widget",
        "transport": "tcp",
        "state_variables": {"power": {"type": "boolean", "label": "Power"}},
        "commands": {"power_on": {"label": "Power On", "send": "PWR1\r", "sets": {"power": True}}},
        "simulator": simulator,
    }


def test_a_yaml_simulator_section_with_null_settings_passes_and_is_built():
    definition = _yaml_driver({key: None for key in _SECTION_KEYS})
    assert validate_driver_definition(definition, strict=True) == []
    sim = YAMLAutoSimulator("w1", {}, driver_def=definition)
    assert sim.state == {"power": False}


def test_the_published_schema_lets_each_simulator_setting_be_null():
    schema = json.loads(
        (Path(__file__).parents[1] / "openavc/drivers/avcdriver.schema.json").read_text("utf-8")
    )
    properties = schema["$defs"]["simulatorSection"]["properties"]
    assert set(properties) == set(_SECTION_KEYS)
    for key, node in properties.items():
        assert "null" in node["type"], key


# ── The check ────────────────────────────────────────────────────────────────


def test_an_unknown_key_warns_with_the_nearest_declared_key():
    verdict = check_simulator_info({
        "driver_id": "acme_widget",
        "initial_state": {},
        "errors": {"no_response": {"behavior": "no_response"}},
    })
    assert verdict.errors == []
    assert verdict.warnings == [
        "SIMULATOR_INFO: unknown key 'errors' (did you mean 'error_modes'?); "
        "the simulator does not read it",
    ]


def test_a_value_of_the_wrong_kind_is_an_error_naming_its_place():
    verdict = check_simulator_info({
        "driver_id": "acme_widget",
        "initial_state": [["power", False]],
        "default_port": "4999",
        "tls": "yes",
        "capacity": {"rx_buffer": 64},
    })
    assert verdict.errors == [
        "SIMULATOR_INFO.initial_state: must be a mapping",
        "SIMULATOR_INFO.capacity: missing required 'process_ms'",
        "SIMULATOR_INFO.default_port: must be an integer",
        "SIMULATOR_INFO.tls: must be a boolean (true or false)",
    ]
    assert verdict.warnings == []


def test_every_declared_key_and_a_null_pass():
    verdict = check_simulator_info({
        "driver_id": "acme_widget",
        "name": "Acme Widget Simulator",
        "transport": "tcp",
        "default_port": 4999,
        "category": "utility",
        "delimiter": b"\r",
        "tls": False,
        "push_state": True,
        "initial_state": {"power": False},
        "delays": {"command_response": 0.05},
        "error_modes": None,
        "controls": [{"type": "power", "key": "power"}],
        "single_client": "newest",
    })
    assert (verdict.errors, verdict.warnings, verdict.skipped) == ([], [], [])


def test_a_value_built_in_code_is_named_not_judged(tmp_path):
    driver = _pair(tmp_path, '"default_port": _PORT, "controls": _CONTROLS,')
    result = check_driver_file(driver)
    assert result.errors == [] and result.warnings == []
    assert (
        "acme_widget_sim.py: not checked: SIMULATOR_INFO.controls, SIMULATOR_INFO.default_port "
        "(holds a value that could not be read from the source)"
    ) in result.notes


# ── The doors ────────────────────────────────────────────────────────────────


def test_drivers_check_reads_a_drivers_companion_simulator(tmp_path, capsys):
    driver = _pair(tmp_path, '"errors": {}, "initial_state": [],')
    assert main([str(driver)]) == 1
    printed = capsys.readouterr().err
    assert (
        "acme_widget.py: error: acme_widget_sim.py: SIMULATOR_INFO.initial_state: must be a mapping"
    ) in printed
    assert (
        "acme_widget.py: warning: acme_widget_sim.py: SIMULATOR_INFO: unknown key 'errors' "
        "(did you mean 'error_modes'?); the simulator does not read it"
    ) in printed


def test_a_simulator_named_on_its_own_is_checked_as_a_simulator(tmp_path):
    _pair(tmp_path, '"errors": {},')
    result = check_driver_file(tmp_path / "acme_widget_sim.py")
    assert result.kind == "simulator"
    assert result.errors == []
    assert result.warnings == [
        "SIMULATOR_INFO: unknown key 'errors' (did you mean 'error_modes'?); "
        "the simulator does not read it",
    ]


def test_a_simulator_with_no_readable_info_is_an_error(tmp_path):
    driver = tmp_path / "acme_widget.py"
    driver.write_text(textwrap.dedent(_DRIVER), encoding="utf-8")
    (tmp_path / "acme_widget_sim.py").write_text("SIMULATOR_INFO = {}\n", encoding="utf-8")
    assert check_driver_file(driver).errors == [
        "acme_widget_sim.py: no SIMULATOR_INFO could be read: the file does not parse, "
        "or no class in it assigns SIMULATOR_INFO a mapping",
    ]


@pytest.mark.parametrize("control", ['{"type": "number", "key": "power", "min": 0, "max": 9}'])
def test_the_simulator_validator_holds_a_python_simulators_controls(tmp_path, control):
    driver = _pair(tmp_path, f'"controls": [{control}],')
    errors = [i.message for i in validate_python_driver(driver).errors if i.check == "controls"]
    assert errors == [
        "controls[0]: unknown control type 'number' — the panel renders nothing for it "
        "(valid types: group, indicator, matrix, meters, power, presets, select, slider, toggle)",
    ]


def test_a_python_simulators_control_built_in_code_is_named_not_refused(tmp_path):
    driver = _pair(
        tmp_path,
        '"controls": [{"type": "select", "key": "level", "options": _LEVELS}],',
    )
    result = validate_python_driver(driver)
    assert [i for i in result.errors if i.check == "controls"] == []
    assert [i.message for i in result.infos if i.check == "controls"] == [
        "controls[0] holds a value that could not be read from the source, so it was not checked",
    ]


def test_a_python_simulators_control_key_is_not_held_to_its_seeds(tmp_path):
    """A Python simulator fills its state in code, so a key its seeds do not
    hold is no finding."""
    driver = _pair(tmp_path, '"controls": [{"type": "power", "key": "standby"}],')
    assert [i for i in validate_python_driver(driver).warnings if i.check == "controls"] == []
