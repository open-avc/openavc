"""A YAML driver's ``simulator:`` section in a shape the simulator cannot read
(its ``initial_state``, ``delays`` or ``error_modes`` a list, or the section
itself not a mapping) is reported by the simulator validator, never raised
on, and never stops a simulated device from being built; a null section is
read as absent, as the driver reads one."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from openavc.simulator.validate import _validate_file, validate_yaml_driver
from openavc.simulator.yaml_auto import YAMLAutoSimulator

BASE: dict[str, Any] = {
    "id": "acme_widget",
    "name": "Acme Widget",
    "transport": "tcp",
    "state_variables": {"power": {"type": "boolean", "label": "Power"}},
    "commands": {"power_on": {"send": "PWR 1\r"}},
    "responses": [{"match": "^PWR (\\d)$", "set": {"power": "$1"}}],
}

MISSHAPEN = {
    "initial_state": ({"initial_state": [["power", True]]}, "simulator.initial_state: must be a mapping"),
    "delays": ({"delays": [1, 2]}, "simulator.delays: must be a mapping"),
    "error_modes": ({"error_modes": ["no_response"]}, "simulator.error_modes: must be a mapping"),
    "section": (["initial_state"], "simulator: must be a mapping"),
}


def _file(tmp_path: Path, simulator: Any) -> Path:
    path = tmp_path / "acme_widget.avcdriver"
    path.write_text(yaml.safe_dump({**BASE, "simulator": simulator}), encoding="utf-8")
    return path


@pytest.mark.parametrize("case", sorted(MISSHAPEN))
def test_the_yaml_door_reports_a_misshapen_section(tmp_path: Path, case: str) -> None:
    section, said = MISSHAPEN[case]
    result = validate_yaml_driver(_file(tmp_path, section))
    assert [(i.severity, i.check, i.message) for i in result.issues] == [
        ("error", "simulator_section", said)
    ]


@pytest.mark.parametrize("case", sorted(MISSHAPEN))
def test_the_command_line_reports_it_as_the_contract_does(tmp_path: Path, case: str) -> None:
    section, said = MISSHAPEN[case]
    result = _validate_file(_file(tmp_path, section), "yaml")
    assert [(i.severity, i.check, i.message) for i in result.issues if i.severity == "error"] == [
        ("error", "contract", said)
    ]


@pytest.mark.parametrize("case", ["initial_state", "delays", "error_modes"])
def test_a_simulated_device_is_built_from_a_misshapen_section(case: str) -> None:
    section, _ = MISSHAPEN[case]
    device = YAMLAutoSimulator("acme_1", {}, driver_def={**BASE, "simulator": section})
    assert device.driver_id == "acme_widget"


def test_a_null_section_is_read_as_absent(tmp_path: Path) -> None:
    definition = {**BASE, "simulator": None, "polling": None, "default_config": None}
    device = YAMLAutoSimulator("acme_1", {}, driver_def=definition)
    assert device.driver_id == "acme_widget"
    assert validate_yaml_driver(_file(tmp_path, None)).errors == []
