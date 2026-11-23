"""A ``simulator: initial_state`` key is text. One YAML reads as something else
(``5: 1``, a number) is refused where a driver is written, naming it, and read
as its text by the simulator, so a driver already installed with one is still
simulated, child types and all.

Invented driver (acme_widget) throughout.
"""

from __future__ import annotations

import copy
from typing import Any

from openavc.drivers.avcdriver_semantic import check_driver_definition
from openavc.simulator.yaml_auto import YAMLAutoSimulator

SENTENCE = "simulator.initial_state: the key 5 is not text; write it in quotes, as \"5\""

BASE: dict[str, Any] = {
    "id": "acme_widget",
    "name": "Acme Widget",
    "manufacturer": "Acme",
    "category": "utility",
    "version": "1.0.0",
    "transport": "tcp",
    "commands": {"ping": {"label": "Ping", "send": "PING\\r"}},
    "state_variables": {"power": {"type": "boolean", "label": "Power"}},
    "child_entity_types": {
        "output": {
            "label": "Output",
            "instances": {"count": 2},
            "state_variables": {"input": {"type": "integer", "label": "Input"}},
        },
    },
    "simulator": {"initial_state": {5: 1, "power": True, "output.3.input": 2}},
}


def _definition() -> dict[str, Any]:
    return copy.deepcopy(BASE)


def test_where_a_driver_is_written_the_key_is_refused_by_name() -> None:
    assert check_driver_definition(_definition(), strict=True).errors == [SENTENCE]


def test_a_driver_already_installed_with_one_loads_and_names_it() -> None:
    loaded = check_driver_definition(_definition())
    assert (loaded.errors, loaded.notices) == ([], [SENTENCE])


def test_text_keys_pass() -> None:
    definition = _definition()
    definition["simulator"]["initial_state"] = {"5": 1, "power": True}
    assert check_driver_definition(definition, strict=True).errors == []


def test_the_simulator_reads_the_key_as_its_text_and_is_built() -> None:
    sim = YAMLAutoSimulator("acme_widget", config={}, driver_def=_definition())
    assert sim.get_state("5") == 1
    assert sim.get_state("power") is True
    assert "3" in sim._child_roster["output"]
