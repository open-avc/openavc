"""The simulator subprocess receives and exposes v0.5.0 child_entities.

Before this, child_entities were dropped from the launch payload, so a
controller-of-children device simulated only the parent and the children were
silently absent — even a Python _sim.py couldn't model them because it never
received them. These tests pin the contract end of the chain: the StartRequest
model carries the field (Pydantic's default extra='ignore' would otherwise
drop it) and BaseSimulator exposes it to subclasses + the info API.
"""

from simulator.api import StartRequest
from simulator.base import BaseSimulator


CHILDREN = {"encoder": {"01": {"label": "Enc 1", "config": {}}, "02": {"label": "Enc 2", "config": {}}}}


def test_start_request_carries_child_entities():
    req = StartRequest(driver_id="acme", child_entities=CHILDREN)
    assert req.child_entities == CHILDREN


def test_start_request_child_entities_optional():
    req = StartRequest(driver_id="acme")
    assert req.child_entities is None


class _Sim(BaseSimulator):
    SIMULATOR_INFO = {"driver_id": "acme_ctrl", "name": "Acme", "transport": "tcp"}

    async def start(self, port):
        self._running = True
        self._port = port

    async def stop(self):
        self._running = False


def test_base_simulator_defaults_to_no_children():
    sim = _Sim(device_id="dev1")
    assert sim.child_entities == {}
    assert "child_entities" not in sim.to_info_dict()


def test_base_simulator_exposes_children():
    sim = _Sim(device_id="dev1")
    sim.set_child_entities(CHILDREN)
    assert sim.child_entities == CHILDREN
    assert sim.to_info_dict()["child_entities"] == CHILDREN


def test_set_child_entities_none_is_safe():
    sim = _Sim(device_id="dev1")
    sim.set_child_entities(None)
    assert sim.child_entities == {}
