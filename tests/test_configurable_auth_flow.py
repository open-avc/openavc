"""Auth handshake ordering + fault classification for ConfigurableDriver.

The declarative `auth:` login handshake must complete BEFORE the platform
reports the device connected: a wrong credential must never set `connected`
or emit device.connected (it used to flap the device online/offline through
the reconnect backoff, firing triggers each cycle). Failure classification
is typed: auth_failed only when the device rejected the login; no_response
when it never spoke the expected protocol at all. Uses an invented device
(Acme) and a scripted in-test transport — no network.
"""

from __future__ import annotations

import asyncio
import copy
import re

import pytest

from openavc.drivers.base import ConnectionFaultError
from openavc.drivers.configurable import create_configurable_driver_class
from openavc.transport.tcp import TCPTransport

AUTH_DEFINITION = {
    "id": "acme_secure",
    "name": "Acme Secure Widget",
    "manufacturer": "Acme",
    "category": "utility",
    "version": "1.0.0",
    "transport": "tcp",
    "default_config": {"host": "", "port": 23, "username": "admin", "password": "pw"},
    "state_variables": {"power": {"type": "boolean"}},
    "commands": {"noop": {"send": "NOOP\r\n"}},
    "responses": [],
    "auth": {
        "type": "telnet_login",
        "username_prompt": "login: ",
        "password_prompt": "Password: ",
        "failure_pattern": "Login incorrect",
        "success_pattern": "OK> ",
        "timeout_seconds": 0.3,
    },
}

_PARSER_SENTINEL = object()


class _FakeTCP:
    """Minimal transport double — the test scripts the device's bytes."""

    def __init__(self, on_data, frame_parser):
        self.connected = True
        self.on_data = on_data
        # Mimic the real transport: a delimiter driver still has an internal
        # frame parser object stored on _frame_parser.
        self._frame_parser = frame_parser if frame_parser is not None else _PARSER_SENTINEL
        self.original_parser = self._frame_parser
        self.sent: list[bytes] = []
        self.script = None  # async callable(sent_bytes, transport)

    async def send(self, data):
        self.sent.append(bytes(data))
        if self.script:
            await self.script(bytes(data), self)

    async def close(self):
        self.connected = False

    async def feed(self, data: bytes):
        await self.on_data(data)


@pytest.fixture
def fake_tcp(monkeypatch):
    holder: dict = {}

    async def _create(**kwargs):
        t = _FakeTCP(kwargs.get("on_data"), kwargs.get("frame_parser"))
        holder["t"] = t
        return t

    monkeypatch.setattr(TCPTransport, "create", _create)
    return holder


def _make_driver(state, events, *, device_id="dev1", password="pw", **cfg):
    state.set_event_bus(events)
    cls = create_configurable_driver_class(AUTH_DEFINITION)
    config = {"host": "10.0.0.9", "port": 23, "username": "admin", "password": password}
    config.update(cfg)
    return cls(device_id, config, state, events)


async def _transport_of(holder) -> _FakeTCP:
    for _ in range(200):
        if "t" in holder:
            return holder["t"]
        await asyncio.sleep(0.005)
    raise AssertionError("transport was never created")


async def test_rejected_login_never_reports_connected(fake_tcp, state, events):
    """A wrong password fails connect() outright: no `connected` state, no
    device.connected event — the device must not flap online/offline."""
    drv = _make_driver(state, events, password="bad")
    connected_events: list[str] = []
    events.on("device.connected.dev1", lambda name, payload=None: connected_events.append(name))

    task = asyncio.create_task(drv.connect())
    t = await _transport_of(fake_tcp)

    async def script(sent, tt):
        s = sent.strip()
        if s == b"admin":
            await tt.feed(b"Password: ")
        elif s == b"bad":
            await tt.feed(b"\r\nLogin incorrect\r\n")

    t.script = script
    await t.feed(b"login: ")

    with pytest.raises(ConnectionFaultError) as ei:
        await task
    assert ei.value.fault_code == "auth_failed"
    assert drv.get_state("connected") is not True
    assert connected_events == []
    assert t.connected is False  # transport cleaned up


async def test_silent_device_classifies_no_response_not_auth(fake_tcp, state, events):
    """A host that accepts TCP but never prints a login prompt is a
    no_response fault (wrong host/port/protocol) — NOT auth_failed. The
    integrator should check the address, not the credentials."""
    drv = _make_driver(state, events, device_id="dev2")
    task = asyncio.create_task(drv.connect())
    await _transport_of(fake_tcp)  # device stays silent

    with pytest.raises(ConnectionFaultError) as ei:
        await task
    assert ei.value.fault_code == "no_response"
    assert drv.get_state("connected") is not True


async def test_successful_login_reports_connected_after_handshake(fake_tcp, state, events):
    """The happy path: connected + device.connected fire only after the
    success pattern, and the frame parser is restored."""
    drv = _make_driver(state, events, device_id="dev3")
    connected_events: list[str] = []
    events.on("device.connected.dev3", lambda name, payload=None: connected_events.append(name))

    task = asyncio.create_task(drv.connect())
    t = await _transport_of(fake_tcp)

    async def script(sent, tt):
        s = sent.strip()
        if s == b"admin":
            # Assert mid-handshake: the platform must not have reported
            # connected yet (the handshake is still running).
            assert drv.get_state("connected") is not True
            assert connected_events == []
            await tt.feed(b"Password: ")
        elif s == b"pw":
            await tt.feed(b"\r\nWelcome!\r\nOK> ")

    t.script = script
    await t.feed(b"login: ")

    await task
    assert drv.get_state("connected") is True
    assert connected_events == ["device.connected.dev3"]
    assert t._frame_parser is t.original_parser  # parser restored


async def test_zero_timeout_does_not_brick_the_handshake(fake_tcp, state, events):
    """A cleared/zero auth timeout (the Driver Builder box emptied, baking in
    timeout_seconds: 0) must not abort the handshake on the first loop
    iteration. The runtime clamps a non-positive timeout to the default so the
    device can still connect instead of failing every attempt."""
    definition = copy.deepcopy(AUTH_DEFINITION)
    definition["id"] = "acme_secure_zero"
    definition["auth"]["timeout_seconds"] = 0
    cls = create_configurable_driver_class(definition)
    state.set_event_bus(events)
    drv = cls(
        "dev6",
        {"host": "10.0.0.9", "port": 23, "username": "admin", "password": "pw"},
        state,
        events,
    )

    task = asyncio.create_task(drv.connect())
    t = await _transport_of(fake_tcp)

    async def script(sent, tt):
        s = sent.strip()
        if s == b"admin":
            await tt.feed(b"Password: ")
        elif s == b"pw":
            await tt.feed(b"\r\nOK> ")

    t.script = script
    await t.feed(b"login: ")

    await task
    assert drv.get_state("connected") is True


async def test_poll_interval_survives_failed_connect(monkeypatch, state, events):
    """connect() zeroes poll_interval while initializing; a failed attempt
    must restore it — leaving 0 behind permanently disabled polling on
    every later (successful) reconnect."""
    drv = _make_driver(
        state, events, device_id="dev4", username="", password="", poll_interval=15,
    )

    async def boom(**kwargs):
        raise ConnectionError("connection refused")

    monkeypatch.setattr(TCPTransport, "create", boom)
    with pytest.raises(ConnectionError):
        await drv.connect()
    assert drv.config["poll_interval"] == 15


async def test_auth_stage_cannot_match_bytes_before_previous_stage(state, events):
    """Each stage searches only bytes AFTER the previous stage's match — a
    banner mentioning 'Password: ' before the login prompt must not satisfy
    the password stage."""
    drv = _make_driver(state, events, device_id="dev5")
    drv._auth_mode = True
    drv._auth_buffer = bytearray(b"Enter your Password: at the prompt.\r\nlogin: ")
    drv._auth_event = asyncio.Event()
    drv._auth_overflow = False
    drv._auth_search_pos = 0

    # Stage 1 matches the real prompt at the end of the banner...
    await drv._auth_wait_for(
        re.compile("login: "), None, timeout=0.2, stage="username_prompt"
    )
    # ...so the "Password: " that appeared BEFORE it must not satisfy stage 2.
    with pytest.raises(ConnectionFaultError) as ei:
        await drv._auth_wait_for(
            re.compile("Password: "), None, timeout=0.1, stage="password_prompt"
        )
    assert ei.value.fault_code == "no_response"

    # Fresh bytes after the previous match DO satisfy the stage.
    drv._auth_buffer.extend(b"Password: ")
    drv._auth_event.set()
    await drv._auth_wait_for(
        re.compile("Password: "), None, timeout=0.2, stage="password_prompt"
    )
