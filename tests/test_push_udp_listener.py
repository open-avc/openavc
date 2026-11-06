"""Tests for the driver push primitive (``push: {type: udp_listener}``).

Platform-feature tests with an INVENTED device (acme_sensor) and synthetic
datagrams per the test policy. The device shape under test: a box controlled
over UDP that also sends a status line, unasked, to an address and port set
on the box itself, every time something changes. Several such boxes send to
one port; each datagram belongs to the box that sent it.
"""

import asyncio
import logging
import socket
from typing import Any

import pytest

from openavc.core import push_ports
from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers import spec
from openavc.drivers.avcdriver_semantic import platform_version_errors
from openavc.drivers.base import BaseDriver
from openavc.drivers.configurable import create_configurable_driver_class
from openavc.drivers.driver_loader import validate_driver_definition
from openavc.drivers.python_info import python_driver_info_issues
from openavc.drivers.spec import platform_requirements
from openavc.transport import udp_listener as ul


def _make_driver(definition: dict, config: dict | None = None, device_id: str = "s1"):
    cls = create_configurable_driver_class(definition)
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    return cls(device_id, config or {}, state, events)


def _sensor_def(**overrides) -> dict:
    d = {
        "id": "acme_sensor",
        "name": "Acme Sensor",
        "manufacturer": "Acme",
        "category": "utility",
        "version": "1.0.0",
        "author": "Test",
        "description": "Invented device that sends status packets to a port",
        "transport": "udp",
        "delimiter": "\\r",
        "source_url": "https://example.com",
        "config_schema": {
            "host": {"type": "string", "required": True, "label": "IP"},
            "port": {"type": "integer", "default": 5000, "label": "Port"},
            "status_port": {"type": "integer", "default": 5001, "label": "Status Port"},
        },
        "default_config": {"host": "", "port": 5000, "status_port": 5001},
        "push": {"type": "udp_listener", "port": "{status_port}"},
        "commands": {},
        "state_variables": {
            "door": {"type": "string", "label": "Door"},
            "level": {"type": "integer", "label": "Level"},
        },
        "responses": [
            {"match": r"DOOR (\w+)", "set": {"door": "$1"}},
            {"match": r"LEVEL (\d+)", "set": {"level": "$1"}},
        ],
    }
    d.update(overrides)
    return d


def _free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("0.0.0.0", 0))
        return s.getsockname()[1]


def _send(port: int, data: bytes, source: str = "127.0.0.1") -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as tx:
        tx.bind((source, 0))
        tx.sendto(data, ("127.0.0.1", port))


async def _wait_for(predicate, timeout: float = 2.0) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not met within timeout")


@pytest.fixture(autouse=True)
async def _clean_registry():
    yield
    await ul.close_all()


# ===========================================================================
# Loader validation
# ===========================================================================


def test_loader_accepts_a_template_port():
    assert validate_driver_definition(_sensor_def()) == []


def test_loader_accepts_a_literal_port():
    d = _sensor_def(push={"type": "udp_listener", "port": 162})
    assert validate_driver_definition(d) == []


@pytest.mark.parametrize(
    "push, expect",
    [
        ({"type": "udp_listener"}, "missing 'port'"),
        ({"type": "udp_listener", "port": 0}, "port must be an integer 1-65535"),
        ({"type": "udp_listener", "port": 65536}, "port must be an integer 1-65535"),
        ({"type": "udp_listener", "port": True}, "port must be an integer 1-65535"),
        ({"type": "udp_listener", "port": "{undeclared}"}, "not declared"),
        ({"type": "udp_listener", "port": 5001, "register": "x"}, "unknown key(s): register"),
        ({"type": "udp_listnr", "port": 5001}, "udp_listener"),
    ],
)
def test_loader_rejects_bad_blocks(push, expect):
    errors = validate_driver_definition(_sensor_def(push=push))
    assert any(expect in e for e in errors), errors


def test_the_unknown_type_message_names_every_type():
    errors = validate_driver_definition(_sensor_def(push={"type": "nope"}))
    [message] = [e for e in errors if "unknown 'type'" in e]
    for ptype in spec.PUSH_TYPE_KEYS:
        assert ptype in message


def test_factory_copies_push_into_driver_info():
    cls = create_configurable_driver_class(_sensor_def())
    assert cls.DRIVER_INFO["push"] == {"type": "udp_listener", "port": "{status_port}"}


# ===========================================================================
# The floor: a YAML driver needs the release that understands the type; a
# Python driver does not (an older release runs it, polling, without it)
# ===========================================================================

_TYPE_NODE = spec.DEFS["pushBlock"]["fields"]["type"]
_FLOOR = _TYPE_NODE["yaml_since_values"]["udp_listener"]


def test_a_yaml_driver_using_it_has_the_floor():
    assert ("push.type: udp_listener", _FLOOR) in platform_requirements(_sensor_def())
    d = _sensor_def(min_platform_version="0.39.0")
    assert any(f'Raise it to "{_FLOOR}"' in e for e in platform_version_errors(d))


def test_a_python_driver_using_it_has_no_floor():
    info = _sensor_def(min_platform_version="0.34.0")
    # Only the push block's own floor, from when push arrived.
    assert platform_requirements(info, "python") == [("push", "0.23.0")]
    assert platform_version_errors(info, require_declaration=True, tier="python") == []
    assert not [i for i in python_driver_info_issues(info) if "min_platform_version" in i]


def test_each_schema_says_what_it_means_for_its_format():
    yaml_doc = spec.node_doc(_TYPE_NODE, _TYPE_NODE["enum"], "yaml")
    python_doc = spec.node_doc(_TYPE_NODE, _TYPE_NODE["enum"], "python")
    assert f'Value "udp_listener" requires platform {_FLOOR}.' in yaml_doc
    assert (
        f'Value "udp_listener" takes effect on platform {_FLOOR}; an older platform '
        "ignores it and still runs a Python driver"
    ) in python_doc
    assert "requires platform" not in python_doc.split('Value "udp_listener"')[1]


# ===========================================================================
# Listener registry: sharing, refcounting, demux, exclusivity
# ===========================================================================


@pytest.mark.asyncio
async def test_registry_shares_one_socket_per_port_and_refcounts():
    port = _free_udp_port()
    a = await ul.subscribe(port, "127.0.0.1", lambda d, s: None, "a")
    b = await ul.subscribe(port, "127.0.0.1", lambda d, s: None, "b")
    assert a._listener is b._listener
    assert a.port == b.port == port
    await a.close()
    assert port in ul._registry._listeners
    await b.close()
    assert port not in ul._registry._listeners
    # The socket is really gone: the port binds again.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("0.0.0.0", port))


@pytest.mark.asyncio
async def test_the_last_device_leaving_and_coming_back_at_once_gets_the_port():
    """A reconnect stops push and starts it again with no pause between."""
    port = _free_udp_port()
    got: list[bytes] = []
    sub = await ul.subscribe(port, "127.0.0.1", lambda d, s: None, "a")
    await sub.close()
    again = await ul.subscribe(port, "127.0.0.1", lambda d, s: got.append(d), "a")
    _send(port, b"DOOR OPEN\r")
    await _wait_for(lambda: got)
    await again.close()


@pytest.mark.asyncio
async def test_a_datagram_reaches_the_device_that_sent_it_only():
    port = _free_udp_port()
    got_local: list[bytes] = []
    got_other: list[bytes] = []
    await ul.subscribe(port, "127.0.0.1", lambda d, s: got_local.append(d), "local")
    await ul.subscribe(port, "192.0.2.10", lambda d, s: got_other.append(d), "other")
    _send(port, b"DOOR OPEN\r")
    await _wait_for(lambda: got_local)
    await asyncio.sleep(0.05)
    assert got_local == [b"DOOR OPEN\r"]
    assert got_other == []


@pytest.mark.asyncio
async def test_a_datagram_from_no_subscribed_device_is_dropped():
    port = _free_udp_port()
    got: list[bytes] = []
    await ul.subscribe(port, "192.0.2.10", lambda d, s: got.append(d), "other")
    _send(port, b"DOOR OPEN\r")
    await asyncio.sleep(0.1)
    assert got == []


@pytest.mark.asyncio
async def test_a_device_with_no_host_receives_nothing():
    port = _free_udp_port()
    got: list[bytes] = []
    await ul.subscribe(port, "", lambda d, s: got.append(d), "nohost")
    _send(port, b"DOOR OPEN\r")
    await asyncio.sleep(0.1)
    assert got == []


@pytest.mark.asyncio
async def test_each_datagram_arrives_whole_with_its_sender():
    port = _free_udp_port()
    got: list[tuple[bytes, tuple]] = []
    await ul.subscribe(port, "127.0.0.1", lambda d, s: got.append((d, s)), "a")
    _send(port, b"\x30\x82\x00\x10binary\x00payload")
    await _wait_for(lambda: got)
    data, source = got[0]
    assert data == b"\x30\x82\x00\x10binary\x00payload"
    assert source[0].startswith("127.")


@pytest.mark.asyncio
async def test_a_port_another_program_holds_is_refused():
    """No address reuse: a unicast datagram reaches one of several sockets
    sharing a port, so sharing would lose packets without a word."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as holder:
        holder.bind(("0.0.0.0", 0))
        port = holder.getsockname()[1]
        with pytest.raises(OSError):
            await ul.subscribe(port, "127.0.0.1", lambda d, s: None, "a")
    assert port not in ul._registry._listeners


@pytest.mark.asyncio
@pytest.mark.parametrize("port", [0, -1, 65536, True])
async def test_a_port_outside_the_range_is_refused(port):
    with pytest.raises(ValueError):
        await ul.subscribe(port, "127.0.0.1", lambda d, s: None, "a")


# ===========================================================================
# Driver lifecycle + dispatch (end to end over a real loopback datagram)
# ===========================================================================


@pytest.mark.asyncio
async def test_a_pushed_datagram_reaches_driver_state():
    port = _free_udp_port()
    drv = _make_driver(_sensor_def(), {"host": "127.0.0.1", "status_port": port})
    await drv._start_push()
    assert drv._push_subscription is not None
    try:
        _send(port, b"DOOR OPEN\r")
        await _wait_for(lambda: drv.state.get("device.s1.door") == "OPEN")
    finally:
        await drv._stop_push()
    assert drv._push_subscription is None
    assert port not in ul._registry._listeners


@pytest.mark.asyncio
async def test_a_datagram_splits_on_the_delimiter():
    port = _free_udp_port()
    drv = _make_driver(_sensor_def(), {"host": "127.0.0.1", "status_port": port})
    await drv._start_push()
    try:
        _send(port, b"DOOR SHUT\rLEVEL 42\r")
        await _wait_for(
            lambda: drv.state.get("device.s1.door") == "SHUT"
            and drv.state.get("device.s1.level") == 42
        )
    finally:
        await drv._stop_push()


@pytest.mark.asyncio
async def test_two_devices_on_one_port_each_get_their_own():
    port = _free_udp_port()
    near = _make_driver(_sensor_def(), {"host": "127.0.0.1", "status_port": port}, "near")
    far = _make_driver(_sensor_def(), {"host": "192.0.2.10", "status_port": port}, "far")
    await near._start_push()
    await far._start_push()
    try:
        assert near._push_subscription._listener is far._push_subscription._listener
        _send(port, b"LEVEL 7\r")
        await _wait_for(lambda: near.state.get("device.near.level") == 7)
        await asyncio.sleep(0.05)
        assert far.state.get("device.far.level") is None
    finally:
        await near._stop_push()
        assert port in ul._registry._listeners  # far still holds it
        await far._stop_push()
    assert port not in ul._registry._listeners


@pytest.mark.asyncio
async def test_a_port_in_use_leaves_the_device_connected_without_push(caplog):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as holder:
        holder.bind(("0.0.0.0", 0))
        port = holder.getsockname()[1]
        drv = _make_driver(_sensor_def(), {"host": "127.0.0.1", "status_port": port})
        with caplog.at_level(logging.WARNING):
            await drv._start_push()
    assert drv._push_subscription is None
    assert any(
        f"could not listen on UDP port {port}" in r.getMessage() for r in caplog.records
    )


@pytest.mark.asyncio
async def test_an_unresolved_port_is_nonfatal(caplog):
    drv = _make_driver(_sensor_def(), {"host": "127.0.0.1"})
    drv.config.pop("status_port", None)
    with caplog.at_level(logging.WARNING):
        await drv._start_push()
    assert drv._push_subscription is None
    assert any("did not resolve to a UDP port" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_stop_push_is_idempotent():
    port = _free_udp_port()
    drv = _make_driver(_sensor_def(), {"host": "127.0.0.1", "status_port": port})
    await drv._start_push()
    await drv._stop_push()
    await drv._stop_push()
    assert drv._push_subscription is None


# --- a Python driver decodes the datagram itself ---


class _AcmeTrapBox(BaseDriver):
    """A Python driver whose device sends a binary packet: it overrides the
    datagram hook and reads the bytes itself."""

    DRIVER_INFO: dict[str, Any] = {
        "id": "acme_trap_box",
        "name": "Acme Trap Box",
        "manufacturer": "Acme",
        "category": "utility",
        "transport": "udp",
        "default_config": {"host": "", "port": 5000, "trap_port": 5001},
        "push": {"type": "udp_listener", "port": "{trap_port}"},
        "commands": {},
        "state_variables": {"alarm": {"type": "integer", "label": "Alarm"}},
    }

    async def _handle_push_datagram(self, data: bytes, source: tuple[str, int]) -> None:
        if data[:1] == b"\xa7":
            self.set_state("alarm", data[1])

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        return None


@pytest.mark.asyncio
async def test_a_python_driver_reads_the_raw_datagram():
    port = _free_udp_port()
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    drv = _AcmeTrapBox("t1", {"host": "127.0.0.1", "trap_port": port}, state, events)
    await drv._start_push()
    try:
        _send(port, b"\xa7\x05")
        await _wait_for(lambda: state.get("device.t1.alarm") == 5)
    finally:
        await drv._stop_push()


# ===========================================================================
# The host firewall record
# ===========================================================================


def test_the_firewall_record_names_the_udp_port():
    assert push_ports.push_listen_port(_AcmeTrapBox.DRIVER_INFO, {"trap_port": 162}) == (
        162,
        "udp",
    )


@pytest.mark.parametrize("config", [{"trap_port": 0}, {"trap_port": "x"}, {}, {"trap_port": 22}])
def test_the_firewall_record_skips_a_port_it_cannot_open(config):
    assert push_ports.push_listen_port(_AcmeTrapBox.DRIVER_INFO, config) is None
