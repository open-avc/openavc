"""Tests for simulator scaffold output (A26).

The OSC handler template references `Any` in its function signature
(`args: list[tuple[str, Any]]`), and PEP 604/585 evaluates these
annotations at runtime — so without `from typing import Any` the
generated `_sim.py` fails to load with `NameError: name 'Any' is not
defined` the first time the simulator tries to import it.
"""

import pytest

from openavc.simulator.scaffold import generate_skeleton


def _sample_driver_info(transport: str) -> dict:
    return {
        "id": "sample_driver",
        "name": "Sample Device",
        "category": "audio",
        "transport": transport,
        "default_config": {"port": 8000},
        "state_variables": {
            "volume": {"type": "number", "label": "Volume"},
        },
        "commands": {
            "set_volume": {
                "label": "Set Volume",
                "help": "Set the channel volume",
                "params": {"value": {"type": "number"}},
            },
        },
    }


def test_osc_scaffold_compiles_cleanly():
    """The OSC template uses `Any` in its handler signature. Without the
    `from typing import Any` import in the generated file, importing it
    raises NameError the moment Python evaluates the function annotations.
    """
    skeleton = generate_skeleton(_sample_driver_info("osc"), "sample_driver")

    # Static parse must succeed — generated file is syntactically valid Python.
    compiled = compile(skeleton, "<scaffold-osc>", "exec")

    # Execute the module body so annotation evaluation actually runs. This is
    # what catches the missing `Any` import (parse passes, execution fails).
    ns: dict = {}
    exec(compiled, ns)
    assert "SampleDriverSimulator" in ns


def test_tcp_scaffold_compiles_cleanly():
    """TCP template doesn't reference `Any`; this guards against the OSC fix
    accidentally regressing the TCP path (e.g. adding unconditional imports).
    """
    skeleton = generate_skeleton(_sample_driver_info("tcp"), "sample_driver")
    ns: dict = {}
    exec(compile(skeleton, "<scaffold-tcp>", "exec"), ns)
    assert "SampleDriverSimulator" in ns


def test_http_scaffold_compiles_cleanly():
    """HTTP template also doesn't reference `Any`."""
    skeleton = generate_skeleton(_sample_driver_info("http"), "sample_driver")
    ns: dict = {}
    exec(compile(skeleton, "<scaffold-http>", "exec"), ns)
    assert "SampleDriverSimulator" in ns


# The simulator server each transport is served on (the transport table's
# simulator column), as the base class a scaffolded simulator subclasses. A
# byte pipe the simulator has no server for (serial, ssh) is served over TCP.
_BASES = {
    "tcp": "TCPSimulator",
    "serial": "TCPSimulator",
    "ssh": "TCPSimulator",
    "udp": "UDPSimulator",
    "http": "HTTPSimulator",
    "osc": "OSCSimulator",
    "mqtt": "MQTTSimulator",
    "snmp": "SNMPSimulator",
}


@pytest.mark.parametrize("transport", sorted(_BASES))
def test_each_transport_gets_the_server_it_is_served_on(transport):
    """A UDP, MQTT or SNMP driver's simulator listens where its driver
    sends: a TCP server labelled udp never hears a datagram."""
    skeleton = generate_skeleton(_sample_driver_info(transport), "sample_driver")
    ns: dict = {}
    exec(compile(skeleton, f"<scaffold-{transport}>", "exec"), ns)
    simulator = ns["SampleDriverSimulator"]
    assert _BASES[transport] in [base.__name__ for base in simulator.__mro__]
    assert simulator.SIMULATOR_INFO["transport"] == transport
    simulator("sample_device", {})


def test_a_bridge_device_is_refused_with_why():
    """A device on a bridge opens nothing itself: its bridge is simulated."""
    with pytest.raises(ValueError, match="through its bridge"):
        generate_skeleton(_sample_driver_info("bridge"), "sample_driver")
