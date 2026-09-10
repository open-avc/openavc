"""A datagram beside the main transport: the ``udp:`` command block.

A display driver's Wake-on-LAN packet and a signage player's presentation
message have the same shape: one UDP datagram, from a socket opened for the
send and closed after it, aimed somewhere other than the device's control
link. The platform gives that shape one home — ``BaseDriver.send_udp`` and
``BaseDriver.wake_on_lan`` for Python drivers, a command's ``udp:`` block for
YAML — instead of a hand-rolled socket per driver.

Everything here uses an invented device (Acme) and asserts on the bytes the
driver handed the socket:

  - the two forms on the wire (payload: substitution, escapes, a port from a
    {config} placeholder; magic_packet: the packet bytes, broadcast plus the
    host, state beating config for the MAC, the missing-MAC refusal);
  - the offline path: a udp command runs with no transport at all while a
    plain send command is still refused;
  - the authoring rules: one shape per command, one form per block, a port
    with a payload, a field the MAC can live in;
  - the simulator: a TCP device with a udp command also receives datagrams on
    its port, logs them, and applies the command's declared ``sets``;
  - the two helpers on their own, and the simulation redirect that points a
    side-send at the simulator and back.
"""

from __future__ import annotations

import asyncio
import socket

import pytest

from openavc.core.event_bus import EventBus
from openavc.core.simulation import SimulationManager
from openavc.core.state_store import StateStore
from openavc.drivers.avcdriver_semantic import validate_driver_definition
from openavc.drivers.base import (
    WAKE_ON_LAN_PORT,
    BaseDriver,
    magic_packet,
    normalize_mac,
)
from openavc.drivers.configurable import create_configurable_driver_class
from openavc.simulator.yaml_auto import YAMLAutoSimulator

MAC = "aa:bb:cc:dd:ee:ff"
MAGIC = b"\xff" * 6 + bytes.fromhex("aabbccddeeff") * 16


def _definition(**over):
    base = {
        "id": "acme_widget",
        "name": "Acme Widget",
        "manufacturer": "Acme",
        "category": "display",
        "transport": "tcp",
        "delimiter": "\r",
        "default_config": {"host": "", "port": 5000, "mac_address": "", "udp_port": 5100},
        "config_schema": {
            "host": {"type": "string"},
            "mac_address": {"type": "string"},
            "udp_port": {"type": "integer"},
        },
        "state_variables": {
            "power": {"type": "boolean", "label": "Power"},
            "greeting": {"type": "string", "label": "Greeting"},
            "mac_address": {"type": "string", "label": "MAC Address"},
        },
        "commands": {
            "power_on": {
                "label": "Power On",
                "available_offline": True,
                "udp": {"magic_packet": "mac_address"},
                "sets": {"power": True},
            },
            "say": {
                "label": "Say",
                "udp": {"port": "{udp_port}", "payload": "hello {name}\\r"},
                "params": {"name": {"type": "string", "required": True}},
                "sets": {"greeting": "{name}"},
            },
            "shout": {
                "label": "Shout",
                "udp": {"port": 4000, "payload": "HEY\\x01", "broadcast": True},
            },
            "beep": {"label": "Beep", "send": "BEEP\r"},
        },
    }
    base.update(over)
    return base


class _CaptureUdp:
    """Stands in for the socket send_udp opens: records instead of sending."""

    def __init__(self) -> None:
        self.sent: list[tuple[bytes, str, int]] = []
        self.closed = 0

    async def send_to(self, data: bytes, host: str, port: int) -> None:
        self.sent.append((bytes(data), host, port))

    async def close(self) -> None:
        self.closed += 1


def _driver(config=None, definition=None):
    cls = create_configurable_driver_class(definition or _definition())
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    cfg = {"host": "10.0.0.5", "port": 5000, "mac_address": "", "udp_port": 5100}
    cfg.update(config or {})
    drv = cls("d1", cfg, state, events)
    capture = _CaptureUdp()

    async def _open():
        return capture

    drv._open_udp = _open
    return drv, capture


# ── The two forms on the wire ──────────────────────────────────────────────


def test_payload_form_substitutes_escapes_and_takes_its_port_from_config():
    drv, cap = _driver()
    assert asyncio.run(drv.send_command("say", {"name": "bob"})) is True
    assert cap.sent == [(b"hello bob\r", "10.0.0.5", 5100)]
    assert cap.closed == 1, "the socket is opened for the send and closed after it"


def test_payload_form_can_broadcast_and_carries_hex_escapes():
    drv, cap = _driver()
    asyncio.run(drv.send_command("shout"))
    assert cap.sent == [(b"HEY\x01", "255.255.255.255", 4000)]


def test_payload_form_is_not_framed_by_the_main_protocol():
    definition = _definition(command_prefix="!1", command_suffix="\r")
    drv, cap = _driver(definition=definition)
    asyncio.run(drv.send_command("say", {"name": "bob"}))
    assert cap.sent[0][0] == b"hello bob\r"


def test_magic_packet_form_goes_to_broadcast_and_the_host_on_port_nine():
    drv, cap = _driver({"mac_address": "AA-BB-CC-DD-EE-FF"})
    assert asyncio.run(drv.send_command("power_on")) is True
    assert cap.sent == [
        (MAGIC, "255.255.255.255", WAKE_ON_LAN_PORT),
        (MAGIC, "10.0.0.5", WAKE_ON_LAN_PORT),
    ]


def test_magic_packet_reads_state_before_config():
    drv, cap = _driver({"mac_address": "AA-BB-CC-DD-EE-FF"})
    drv.set_state("mac_address", "11:22:33:44:55:66")
    asyncio.run(drv.send_command("power_on"))
    assert cap.sent[0][0][6:12] == bytes.fromhex("112233445566")


def test_magic_packet_without_a_mac_refuses_and_says_where_to_put_one():
    drv, cap = _driver()
    with pytest.raises(ValueError, match="no MAC address in 'mac_address' yet"):
        asyncio.run(drv.send_command("power_on"))
    assert cap.sent == []


def test_magic_packet_with_a_bad_mac_names_the_shape_expected():
    drv, cap = _driver({"mac_address": "not a mac"})
    with pytest.raises(ValueError, match="six hex pairs"):
        asyncio.run(drv.send_command("power_on"))
    assert cap.sent == []


def test_udp_port_placeholder_that_is_not_a_number_is_refused():
    drv, cap = _driver({"udp_port": "soon"})
    with pytest.raises(ValueError, match="not a number"):
        asyncio.run(drv.send_command("say", {"name": "x"}))
    assert cap.sent == []


def test_udp_command_still_validates_its_params():
    from openavc.drivers.base import CommandParamError

    drv, cap = _driver()
    with pytest.raises(CommandParamError):
        asyncio.run(drv.send_command("say", {}))
    assert cap.sent == []


# ── The offline path ───────────────────────────────────────────────────────


def test_udp_command_runs_with_no_transport_while_a_send_command_is_refused():
    drv, cap = _driver({"mac_address": MAC})
    assert drv.transport is None
    asyncio.run(drv.send_command("power_on"))
    assert len(cap.sent) == 2
    with pytest.raises(ConnectionError, match="Not connected"):
        asyncio.run(drv.send_command("beep"))


# ── The authoring rules ────────────────────────────────────────────────────


def _errors(**over):
    return validate_driver_definition(_definition(**over))


def test_a_clean_udp_driver_validates():
    assert _errors() == []


def test_a_command_declares_one_shape():
    errs = _errors(commands={
        "wake": {"send": "PWR\r", "udp": {"magic_packet": "mac_address"}},
    })
    assert any("declares send and udp" in e for e in errs), errs


def test_a_udp_block_is_payload_or_magic_packet_not_both():
    errs = _errors(commands={
        "wake": {"udp": {"port": 9, "payload": "X", "magic_packet": "mac_address"}},
    })
    assert any("one or the other" in e for e in errs), errs
    errs = _errors(commands={"wake": {"udp": {"port": 9}}})
    assert any("needs a 'payload'" in e for e in errs), errs


def test_a_payload_needs_a_port_and_a_magic_packet_does_not():
    errs = _errors(commands={"say": {"udp": {"payload": "X"}}})
    assert any("'udp.port' is required" in e for e in errs), errs
    assert _errors(commands={"wake": {"udp": {"magic_packet": "mac_address"}}}) == []


def test_a_magic_packet_names_a_config_field():
    errs = _errors(commands={"wake": {"udp": {"magic_packet": "hw_addr"}}})
    assert any("not a config field" in e for e in errs), errs
    # A state variable alone is a wake that cannot work before the first
    # connect: the config field is required, the state variable optional.
    errs = _errors(
        commands={"wake": {"udp": {"magic_packet": "learned_mac"}}},
        state_variables={"learned_mac": {"type": "string", "label": "MAC"}},
    )
    assert any("not a config field" in e for e in errs), errs


def test_a_port_placeholder_names_a_declared_config_field():
    errs = _errors(commands={"say": {"udp": {"port": "{nope}", "payload": "X"}}})
    assert any("does not declare" in e for e in errs), errs


def test_a_payload_placeholder_outside_the_params_is_flagged():
    errs = _errors(commands={
        "say": {
            "udp": {"port": 9, "payload": "hello {nmae}"},
            "params": {"name": {"type": "string"}},
        },
    })
    assert any("udp.payload" in e for e in errs), errs


# ── The simulator ──────────────────────────────────────────────────────────


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_a_tcp_device_with_udp_commands_receives_datagrams_on_its_port():
    async def scenario():
        port = _free_port()
        sim = YAMLAutoSimulator("d1", {}, driver_def=_definition())
        await sim.start(port)
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sock.sendto(b"hello bob\r", ("127.0.0.1", port))
                sock.sendto(MAGIC, ("127.0.0.1", port))
            finally:
                sock.close()
            for _ in range(50):
                await asyncio.sleep(0.02)
                if sim.get_state("power") is True and sim.get_state("greeting") == "bob":
                    break
            log = sim.get_protocol_log()
            # The TCP server is still the main link.
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b"BEEP\r")
            await writer.drain()
            await asyncio.sleep(0.05)
            writer.close()
            return sim.get_state("greeting"), sim.get_state("power"), log
        finally:
            await sim.stop()

    greeting, power, log = asyncio.run(scenario())
    assert greeting == "bob", "the payload ran through the command pipeline"
    assert power is True, "the magic packet applied the wake's declared sets"
    directions = [(e["direction"], e["data_text"][:9]) for e in log]
    assert ("in", "hello bob") in directions
    assert all(d == "in" for d, _ in directions), "a side-send is never answered"


def test_a_device_without_udp_commands_gets_no_side_receiver():
    definition = _definition()
    definition["commands"] = {"beep": {"label": "Beep", "send": "BEEP\r"}}
    sim = YAMLAutoSimulator("d1", {}, driver_def=definition)
    assert sim._side_receiver is False


# ── The helpers ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "spelling",
    ["aa:bb:cc:dd:ee:ff", "AA-BB-CC-DD-EE-FF", "aabb.ccdd.eeff", "AABBCCDDEEFF"],
)
def test_normalize_mac_accepts_every_usual_spelling(spelling):
    assert normalize_mac(spelling) == MAC


@pytest.mark.parametrize("bad", ["", None, "aa:bb:cc", "00:00:00:00:00:00", "zz:bb:cc:dd:ee:ff"])
def test_normalize_mac_rejects_what_cannot_be_woken(bad):
    assert normalize_mac(bad) is None


def test_magic_packet_is_six_ff_then_the_mac_sixteen_times():
    packet = magic_packet("AA-BB-CC-DD-EE-FF")
    assert len(packet) == 102
    assert packet == MAGIC
    with pytest.raises(ValueError, match="not a MAC address"):
        magic_packet("kettle")


class _AcmePython(BaseDriver):
    DRIVER_INFO = {"id": "acme_widget", "name": "Acme Widget", "transport": "tcp",
                   "state_variables": {}}

    async def connect(self):  # pragma: no cover - never called
        pass

    async def disconnect(self):  # pragma: no cover - never called
        pass

    async def send_command(self, command, params=None):  # pragma: no cover
        return None


def _python_driver(config=None):
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    drv = _AcmePython("p1", {"host": "10.0.0.9", **(config or {})}, state, events)
    capture = _CaptureUdp()

    async def _open():
        return capture

    drv._open_udp = _open
    return drv, capture


def test_send_udp_defaults_to_the_device_host_and_needs_a_port():
    drv, cap = _python_driver()
    targets = asyncio.run(drv.send_udp(b"hi", port=7000))
    assert targets == [("10.0.0.9", 7000)]
    assert cap.sent == [(b"hi", "10.0.0.9", 7000)]
    with pytest.raises(ValueError, match="needs a port"):
        asyncio.run(drv.send_udp(b"hi"))
    drv2, _ = _python_driver({"host": ""})
    with pytest.raises(ValueError, match="needs a host"):
        asyncio.run(drv2.send_udp(b"hi", port=7000))


def test_wake_on_lan_broadcasts_then_sends_to_the_host_and_normalizes():
    drv, cap = _python_driver()
    assert asyncio.run(drv.wake_on_lan("AA-BB-CC-DD-EE-FF")) == MAC
    assert cap.sent == [(MAGIC, "255.255.255.255", 9), (MAGIC, "10.0.0.9", 9)]
    cap.sent.clear()
    asyncio.run(drv.wake_on_lan(MAC, host="10.0.0.77", port=7))
    assert cap.sent == [(MAGIC, "255.255.255.255", 7), (MAGIC, "10.0.0.77", 7)]


def test_wake_on_lan_survives_an_unroutable_host():
    drv, cap = _python_driver()
    calls = []

    async def flaky(data, host, port):
        calls.append(host)
        if host != "255.255.255.255":
            raise OSError("No route to host")

    cap.send_to = flaky
    asyncio.run(drv.wake_on_lan(MAC))
    assert calls == ["255.255.255.255", "10.0.0.9"]


def test_udp_redirect_points_every_side_send_at_one_place():
    drv, cap = _python_driver()
    drv.udp_redirect = ("127.0.0.1", 19123)
    asyncio.run(drv.send_udp(b"hi", host="10.9.9.9", port=7000, broadcast=True))
    asyncio.run(drv.wake_on_lan(MAC))
    assert [(h, p) for _, h, p in cap.sent] == [("127.0.0.1", 19123)] * 2, (
        "broadcast and direct both land on the simulator, the wake once"
    )


def test_simulation_redirect_sets_and_restores_udp_redirect():
    drv, _ = _python_driver()
    manager = SimulationManager(engine=None)
    manager._apply_sim_redirect(drv, "p1", 19201)
    assert drv.udp_redirect == ("127.0.0.1", 19201)
    assert drv.config["host"] == "127.0.0.1"
    SimulationManager._restore_original_config(drv, manager._original_configs["p1"])
    assert drv.udp_redirect is None
    assert drv.config["host"] == "10.0.0.9"


# ── The Builder's dry run ──────────────────────────────────────────────────


def test_dry_run_reports_the_datagram_and_where_it_goes():
    from openavc.api.models import TestCommandRequest
    from openavc.api.routes.driver_test import _dry_run_command

    body = TestCommandRequest(
        host="10.0.0.5", port=5000, transport="tcp", dry_run=True,
        definition=_definition(), command_name="say", params={"name": "bob"},
    )
    result = asyncio.run(_dry_run_command(body))
    assert result["success"], result
    assert result["route"] == "udp"
    assert result["wire"] == "hello bob\r"
    assert result["wire_hex"] == b"hello bob\r".hex()
    assert result["udp_targets"] == ["10.0.0.5:5100"]
