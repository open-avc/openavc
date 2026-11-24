"""A text rule carrying both ``set:`` and ``mappings:``: the simulator answers
every variable the rule writes, as the runtime reads both forms.

The runtime keeps both forms (``reply_rule``), so a reply ``P=1 I=hdmi2``
writes power from the ``mappings:`` entry and input from the ``set:`` item.
The simulator used to read the ``mappings:`` only when there was no ``set:``,
so it had no reply for power at all. Invented Acme device.
"""

from __future__ import annotations

from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.configurable import create_configurable_driver_class
from openavc.simulator.yaml_auto import YAMLAutoSimulator

_DEFINITION = {
    "id": "acme_switcher",
    "name": "Acme Switcher",
    "transport": "tcp",
    "delimiter": "\r",
    "commands": {},
    "state_variables": {
        "power": {"type": "boolean"},
        "input": {"type": "string"},
    },
    "responses": [
        {
            "match": r"P=(\d) I=(\w+)",
            "mappings": [{"group": 1, "state": "power", "map": {"1": True, "0": False}}],
            "set": {"input": "$2"},
        }
    ],
}


def _sim() -> YAMLAutoSimulator:
    return YAMLAutoSimulator("s1", {}, driver_def=_DEFINITION)


def test_the_simulator_has_a_reply_for_each_variable_the_rule_writes():
    tables = _sim()._state_responses
    assert set(tables) == {"power", "input"}


def test_the_mapped_variable_replies_with_its_wire_token():
    power = _sim()._state_responses["power"]
    assert power.format(True).startswith("P=1 ")
    assert power.format(False).startswith("P=0 ")


async def test_the_runtime_reads_the_simulated_reply_back_as_the_value_it_was():
    reply = _sim()._state_responses["power"].format(True)
    state, events = StateStore(), EventBus()
    state.set_event_bus(events)
    drv = create_configurable_driver_class(_DEFINITION)("acme1", {}, state, events)
    await drv.on_data_received(reply.encode())
    assert state.get("device.acme1.power") is True
