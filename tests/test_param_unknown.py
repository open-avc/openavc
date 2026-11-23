"""A command parameter has no ``unknown``: it names a value a device sends
when it has no reading, so it belongs on the state variable a reply sets.

The runtime and the simulator read a parameter's conversion as its scale and
offset only. Where a driver is written, an ``unknown`` on a parameter is
refused with one sentence saying where it goes; a driver already installed
with one still loads, and its Installed entry names it.

Invented driver (acme_widget) throughout.
"""

from __future__ import annotations

import asyncio
import copy
from typing import Any

from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.avcdriver_semantic import check_driver_definition
from openavc.drivers.configurable import create_configurable_driver_class
from openavc.simulator.yaml_auto import YAMLAutoSimulator

SENTENCE = (
    "Command 'set_level' param 'level': remove unknown. It marks a value the device "
    "sends when it has no reading, so it goes on the state variable a reply sets, "
    "never on a parameter."
)

BASE: dict[str, Any] = {
    "id": "acme_widget",
    "name": "Acme Widget",
    "manufacturer": "Acme",
    "category": "utility",
    "version": "1.0.0",
    "transport": "tcp",
    "state_variables": {"level": {"type": "number", "label": "Level"}},
    "commands": {
        "set_level": {
            "label": "Set Level",
            "send": "L{level}\\r",
            "params": {"level": {"type": "number", "label": "Level", "scale": 0.5, "unknown": [255]}},
        },
    },
}


def _definition(**param: Any) -> dict[str, Any]:
    definition = copy.deepcopy(BASE)
    definition["commands"]["set_level"]["params"]["level"].update(param)
    return definition


class _Line:
    connected = True

    def __init__(self) -> None:
        self.sent: list[bytes] = []

    async def send(self, data: bytes) -> None:
        self.sent.append(data)


def _sent(definition: dict[str, Any], value: Any) -> bytes:
    cls = create_configurable_driver_class(definition)
    driver = cls("dev1", {"host": "127.0.0.1"}, StateStore(), EventBus())
    driver.transport = _Line()
    driver._link_open = lambda: True
    asyncio.run(driver.send_command("set_level", {"level": value}))
    return driver.transport.sent[-1]


def _without_unknown() -> dict[str, Any]:
    definition = _definition()
    del definition["commands"]["set_level"]["params"]["level"]["unknown"]
    return definition


# ── The runtime and the simulator ──


def test_an_empty_value_goes_out_as_it_would_with_no_unknown() -> None:
    assert _sent(_definition(), None) == _sent(_without_unknown(), None)
    assert _sent(_definition(), None) != b"L255\r"


def test_the_scale_still_converts() -> None:
    assert _sent(_definition(), 10) == b"L20\r"


def test_the_simulator_reads_the_code_as_a_number_through_the_scale() -> None:
    sim = YAMLAutoSimulator("acme_widget", config={}, driver_def=_definition())
    sim.handle_command(b"L255")
    assert sim.get_state("level") == 127.5


# ── The doors ──


def test_where_a_driver_is_written_it_is_refused_in_one_sentence() -> None:
    assert check_driver_definition(_definition(), strict=True).errors == [SENTENCE]


def test_a_driver_already_installed_with_one_still_loads_and_names_it() -> None:
    loaded = check_driver_definition(_definition())
    assert (loaded.errors, loaded.notices) == ([], [SENTENCE])


def test_a_text_parameter_with_one_is_told_only_where_it_goes() -> None:
    definition = _definition(type="string")
    del definition["commands"]["set_level"]["params"]["level"]["scale"]
    assert check_driver_definition(definition, strict=True).errors == [SENTENCE]


def test_another_unknown_key_on_the_parameter_is_still_named() -> None:
    errors = check_driver_definition(_definition(colour="red"), strict=True).errors
    assert errors == [SENTENCE, "commands.set_level.params.level: unknown key 'colour'"]


def test_a_state_variables_unknown_is_still_its_own() -> None:
    definition = copy.deepcopy(BASE)
    del definition["commands"]["set_level"]["params"]["level"]["unknown"]
    definition["state_variables"]["level"]["unknown"] = [255]
    assert check_driver_definition(definition, strict=True).errors == []
