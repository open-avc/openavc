"""Tests for the driver push primitive (``push: {type: multicast}``).

Platform-feature tests with an INVENTED device (acme_mixer) and synthetic
frames per the test policy. Socket-level tests drive unicast loopback
datagrams into the shared listener socket — delivery and demux are
source-IP based, so no actual multicast group membership is required in
the test environment (group joins are best-effort there anyway).
"""

import asyncio
import socket

import pytest

from server.core.event_bus import EventBus
from server.core.state_store import StateStore
from server.drivers.configurable import create_configurable_driver_class
from server.drivers.driver_loader import validate_driver_definition
from server.transport import multicast_listener as ml


def _make_driver(definition: dict, config: dict | None = None, device_id: str = "dev1"):
    cls = create_configurable_driver_class(definition)
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    return cls(device_id, config or {}, state, events)


def _free_udp_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _mixer_def(**overrides) -> dict:
    d = {
        "id": "acme_mixer",
        "name": "Acme Mixer",
        "manufacturer": "Acme",
        "category": "audio",
        "version": "1.0.0",
        "author": "Test",
        "description": "Invented push-capable mixer",
        "transport": "tcp",
        "delimiter": "\\r",
        "source_url": "https://example.com",
        "config_schema": {
            "host": {"type": "string", "required": True, "label": "IP"},
            "notify_group": {"type": "string", "default": "239.10.10.10"},
            "notify_port": {"type": "integer", "default": 17000},
        },
        "default_config": {
            "host": "",
            "notify_group": "239.10.10.10",
            "notify_port": 17000,
        },
        "push": {
            "type": "multicast",
            "group": "{notify_group}",
            "port": "{notify_port}",
        },
        "commands": {},
        "state_variables": {
            "mute": {"type": "boolean", "label": "Mute"},
            "level": {"type": "integer", "label": "Level"},
            "meter": {"type": "integer", "label": "Meter"},
        },
        "responses": [
            {"match": r"NOTIFY MUTE (\d)", "set": {"mute": "$1"}},
            {"match": r"NOTIFY LEVEL (\d+)", "set": {"level": "$1"}},
            {"match": r"NOTIFY METER (\d+)", "set": {"meter": "$1"}, "throttle": 60},
        ],
    }
    d.update(overrides)
    return d


@pytest.fixture(autouse=True)
async def _clean_registry():
    yield
    await ml.close_all()


# ===========================================================================
# Loader validation
# ===========================================================================


def test_loader_accepts_valid_push_block():
    assert validate_driver_definition(_mixer_def()) == []


def test_loader_accepts_literal_group_and_port():
    d = _mixer_def()
    d["push"] = {"type": "multicast", "group": "239.0.0.100", "port": 17000}
    assert validate_driver_definition(d) == []


@pytest.mark.parametrize(
    "push, expect",
    [
        ({"type": "tcp_listener", "group": "239.0.0.1", "port": 1}, "not supported yet"),
        ({"type": "http_listener", "group": "239.0.0.1", "port": 1}, "not supported yet"),
        # sse is a real type now, but group/port don't apply to it (and it
        # requires the http transport — this invented mixer is tcp).
        ({"type": "sse", "group": "239.0.0.1", "port": 1}, "unknown key"),
        ({"group": "239.0.0.1", "port": 1}, "missing or unknown 'type'"),
        ({"type": "multicast", "group": "239.0.0.1", "port": 1, "bogus": 1}, "unknown key"),
        ({"type": "multicast", "group": "192.168.1.5", "port": 1}, "multicast address"),
        ({"type": "multicast", "group": "not-an-ip", "port": 1}, "multicast address"),
        ({"type": "multicast", "port": 17000}, "missing 'group'"),
        ({"type": "multicast", "group": "239.0.0.1"}, "missing 'port'"),
        ({"type": "multicast", "group": "239.0.0.1", "port": 0}, "port must be"),
        ({"type": "multicast", "group": "239.0.0.1", "port": 65536}, "port must be"),
        ({"type": "multicast", "group": "239.0.0.1", "port": True}, "port must be"),
        (
            {"type": "multicast", "group": "{undeclared_field}", "port": 1},
            "not declared",
        ),
        ({"type": "multicast", "group": "{}", "port": 1}, "no {config_field} token"),
        ("multicast", "must be a mapping"),
    ],
)
def test_loader_rejects_bad_push_blocks(push, expect):
    d = _mixer_def()
    d["push"] = push
    errors = validate_driver_definition(d)
    assert any(expect in e for e in errors), errors


@pytest.mark.parametrize("throttle", ["fast", -1, 0, True])
def test_loader_rejects_bad_throttle(throttle):
    d = _mixer_def()
    d["responses"][0]["throttle"] = throttle
    errors = validate_driver_definition(d)
    assert any("throttle must be a positive number" in e for e in errors), errors


def test_factory_copies_push_into_driver_info():
    drv = _make_driver(_mixer_def(), {"host": "10.0.0.5"})
    assert drv.DRIVER_INFO["push"]["type"] == "multicast"


# ===========================================================================
# Listener registry — sharing, refcounting, demux
# ===========================================================================


@pytest.mark.asyncio
async def test_registry_shares_socket_and_refcounts():
    port = _free_udp_port()
    got_a: list[bytes] = []
    got_b: list[bytes] = []
    sub_a = await ml.subscribe("239.10.10.10", port, "10.0.0.1", lambda d, a: got_a.append(d), "a")
    sub_b = await ml.subscribe("239.10.10.11", port, "10.0.0.2", lambda d, a: got_b.append(d), "b")

    assert len(ml._registry._listeners) == 1
    listener = ml._registry._listeners[port]
    assert set(listener.groups) == {"239.10.10.10", "239.10.10.11"}

    await sub_a.close()
    assert port in ml._registry._listeners  # b still holds it
    assert "239.10.10.10" not in listener.groups

    await sub_b.close()
    assert port not in ml._registry._listeners


@pytest.mark.asyncio
async def test_registry_demuxes_by_source_ip():
    port = _free_udp_port()
    got_a: list[bytes] = []
    got_b: list[bytes] = []
    sub_a = await ml.subscribe("239.10.10.10", port, "10.0.0.1", lambda d, a: got_a.append(d), "a")
    sub_b = await ml.subscribe("239.10.10.10", port, "10.0.0.2", lambda d, a: got_b.append(d), "b")
    listener = ml._registry._listeners[port]

    listener.deliver(b"for-a", ("10.0.0.1", 5000))
    listener.deliver(b"for-b", ("10.0.0.2", 5000))
    listener.deliver(b"for-nobody", ("10.0.0.3", 5000))

    assert got_a == [b"for-a"]
    assert got_b == [b"for-b"]
    await sub_a.close()
    await sub_b.close()


@pytest.mark.asyncio
async def test_loopback_subscription_matches_local_sources():
    """A simulated device (host rewritten to 127.0.0.1) accepts frames from
    any local address — the simulator's sender socket uses a real interface."""
    port = _free_udp_port()
    got: list[bytes] = []
    sub = await ml.subscribe("239.10.10.10", port, "127.0.0.1", lambda d, a: got.append(d), "sim")
    listener = ml._registry._listeners[port]

    listener.deliver(b"loop", ("127.0.0.9", 5000))
    listener.deliver(b"remote", ("203.0.113.7", 5000))

    assert got == [b"loop"]
    await sub.close()


@pytest.mark.asyncio
async def test_same_source_feeds_all_matching_subscriptions():
    """Two device entries for one host (e.g. two ports on one chassis) both
    receive the host's frames."""
    port = _free_udp_port()
    got_a: list[bytes] = []
    got_b: list[bytes] = []
    sub_a = await ml.subscribe("239.10.10.10", port, "10.0.0.1", lambda d, a: got_a.append(d), "a")
    sub_b = await ml.subscribe("239.10.10.10", port, "10.0.0.1", lambda d, a: got_b.append(d), "b")
    ml._registry._listeners[port].deliver(b"frame", ("10.0.0.1", 5000))
    assert got_a == [b"frame"] and got_b == [b"frame"]
    await sub_a.close()
    await sub_b.close()


# ===========================================================================
# Driver lifecycle + dispatch (end to end over a real loopback datagram)
# ===========================================================================


async def _wait_for(predicate, timeout: float = 2.0) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not met within timeout")


@pytest.mark.asyncio
async def test_push_datagram_reaches_driver_state():
    port = _free_udp_port()
    drv = _make_driver(
        _mixer_def(),
        {"host": "127.0.0.1", "notify_group": "239.10.10.10", "notify_port": port},
    )
    await drv._start_push()
    assert drv._push_subscription is not None
    try:
        tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        tx.sendto(b"NOTIFY MUTE 1 \r", ("127.0.0.1", port))
        tx.close()
        await _wait_for(lambda: drv.state.get("device.dev1.mute") is True)
    finally:
        await drv._stop_push()
    assert drv._push_subscription is None
    assert port not in ml._registry._listeners


@pytest.mark.asyncio
async def test_push_datagram_splits_on_delimiter():
    port = _free_udp_port()
    drv = _make_driver(
        _mixer_def(),
        {"host": "127.0.0.1", "notify_group": "239.10.10.10", "notify_port": port},
    )
    await drv._start_push()
    try:
        tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        tx.sendto(b"NOTIFY MUTE 1 \rNOTIFY LEVEL 42 \r", ("127.0.0.1", port))
        tx.close()
        await _wait_for(
            lambda: drv.state.get("device.dev1.mute") is True
            and drv.state.get("device.dev1.level") == 42
        )
    finally:
        await drv._stop_push()


@pytest.mark.asyncio
async def test_start_push_unresolved_template_is_nonfatal():
    """A device whose config lacks the referenced field connects without push
    (subscription skipped with a warning) instead of erroring."""
    drv = _make_driver(_mixer_def(), {"host": "10.0.0.5", "notify_group": ""})
    drv.config.pop("notify_group", None)
    drv.config.pop("notify_port", None)
    # Simulate a stripped config: the template can't resolve.
    drv.DRIVER_INFO = dict(drv.DRIVER_INFO)
    drv.DRIVER_INFO["push"] = {
        "type": "multicast",
        "group": "{missing_field}",
        "port": "{missing_port}",
    }
    await drv._start_push()
    assert drv._push_subscription is None


@pytest.mark.asyncio
async def test_stop_push_is_idempotent():
    port = _free_udp_port()
    drv = _make_driver(
        _mixer_def(),
        {"host": "127.0.0.1", "notify_group": "239.10.10.10", "notify_port": port},
    )
    await drv._start_push()
    await drv._stop_push()
    await drv._stop_push()
    assert drv._push_subscription is None


# ===========================================================================
# Response throttle
# ===========================================================================


@pytest.mark.asyncio
async def test_throttle_drops_repeat_matches_inside_window():
    drv = _make_driver(_mixer_def(), {"host": "10.0.0.5"})
    await drv.on_data_received(b"NOTIFY METER 10 \r")
    await drv.on_data_received(b"NOTIFY METER 99 \r")
    assert drv.state.get("device.dev1.meter") == 10  # second frame dropped


@pytest.mark.asyncio
async def test_throttle_allows_after_window_elapses():
    d = _mixer_def()
    d["responses"][2]["throttle"] = 0.02
    drv = _make_driver(d, {"host": "10.0.0.5"})
    await drv.on_data_received(b"NOTIFY METER 10 \r")
    await asyncio.sleep(0.05)
    await drv.on_data_received(b"NOTIFY METER 99 \r")
    assert drv.state.get("device.dev1.meter") == 99


@pytest.mark.asyncio
async def test_throttle_does_not_affect_unthrottled_rules():
    drv = _make_driver(_mixer_def(), {"host": "10.0.0.5"})
    await drv.on_data_received(b"NOTIFY LEVEL 1 \r")
    await drv.on_data_received(b"NOTIFY LEVEL 2 \r")
    assert drv.state.get("device.dev1.level") == 2


@pytest.mark.asyncio
async def test_throttle_on_json_rule():
    d = _mixer_def()
    d["responses"] = [
        {"json": True, "set": {"level": "vol"}, "throttle": 60},
    ]
    drv = _make_driver(d, {"host": "10.0.0.5"})
    await drv.on_data_received(b'{"vol": 10}')
    await drv.on_data_received(b'{"vol": 99}')
    assert drv.state.get("device.dev1.level") == 10


@pytest.mark.asyncio
async def test_throttle_on_osc_rule():
    from server.transport.osc_codec import osc_encode_message

    d = _mixer_def()
    d["transport"] = "osc"
    d["responses"] = [
        {
            "address": "/acme/meter",
            "mappings": [{"arg": 0, "state": "meter", "type": "integer"}],
            "throttle": 60,
        },
    ]
    drv = _make_driver(d, {"host": "10.0.0.5"})
    await drv.on_data_received(osc_encode_message("/acme/meter", [("i", 10)]))
    await drv.on_data_received(osc_encode_message("/acme/meter", [("i", 99)]))
    assert drv.state.get("device.dev1.meter") == 10


# ===========================================================================
# Simulator emission
# ===========================================================================


class _FakeSock:
    def __init__(self):
        self.sent: list[tuple[bytes, tuple[str, int]]] = []

    def sendto(self, data: bytes, dest: tuple[str, int]) -> None:
        self.sent.append((data, dest))

    def close(self) -> None:
        pass


def _sim_def() -> dict:
    d = _mixer_def()
    d["simulator"] = {
        "initial_state": {"mute": False, "level": 100},
        "notifications": {
            "mute": {"*": "NOTIFY MUTE {value:d} "},
            "level": {"*": "NOTIFY LEVEL {value} "},
        },
    }
    return d


def test_sim_resolves_push_block_from_config_templates():
    from simulator.yaml_auto import YAMLAutoSimulator

    sim = YAMLAutoSimulator(device_id="acme1", config={}, driver_def=_sim_def())
    assert sim._push_multicast == ("239.10.10.10", 17000)

    sim2 = YAMLAutoSimulator(
        device_id="acme2",
        config={"notify_group": "239.20.20.20", "notify_port": 18000},
        driver_def=_sim_def(),
    )
    assert sim2._push_multicast == ("239.20.20.20", 18000)


def test_sim_render_notification_specs():
    from simulator.yaml_auto import YAMLAutoSimulator

    render = YAMLAutoSimulator._render_notification
    assert render("V {value}", "k", 42) == "V 42"
    assert render("M {value:d}", "k", True) == "M 1"
    assert render("M {value:d}", "k", False) == "M 0"
    assert render("H {value:04X}", "k", 255) == "H 00FF"
    assert render("K {key} {value}", "vol", 7) == "K vol 7"
    assert render("bad {value:zz}", "k", 7) == "bad 7"


def test_sim_emits_notification_via_multicast_not_tcp():
    from simulator.yaml_auto import YAMLAutoSimulator

    sim = YAMLAutoSimulator(device_id="acme1", config={}, driver_def=_sim_def())
    fake = _FakeSock()
    sim._mcast_sock = fake

    sim.set_state("mute", True)
    assert fake.sent, "expected a multicast notification frame"
    data, dest = fake.sent[0]
    assert data == b"NOTIFY MUTE 1 \r"
    assert dest == ("239.10.10.10", 17000)


def test_sim_without_push_block_keeps_tcp_notifications():
    from simulator.yaml_auto import YAMLAutoSimulator

    d = _sim_def()
    d.pop("push")
    sim = YAMLAutoSimulator(device_id="acme1", config={}, driver_def=d)
    assert sim._push_multicast is None
