"""Runtime coverage for OSC `on_connect` entry execution — the platform
feature, exercised with an invented OSC device.

Two shapes an OSC `on_connect` entry can take were mishandled or newly added:

  * `{send, when}` — a bare OSC address gated on a config field (how a driver
    arms a chatty subscription behind an integrator checkbox). The dispatch
    read the address only from the ``address`` key, so a gated entry authored
    with ``send`` was sent as an empty OSC address. It now falls back to
    ``send``.
  * `{address, args}` — an OSC message carrying typed arguments (a value-setting
    bring-up message). The runtime builds the typed args and sends them.

These drive a ConfigurableDriver's real ``connect()`` on_connect path with a
capturing transport (``super().connect()`` is stubbed to inject it) and assert
on the encoded bytes.
"""
from __future__ import annotations

from server.core.event_bus import EventBus
from server.core.state_store import StateStore
from server.drivers import base as base_mod
from server.drivers.configurable import create_configurable_driver_class
from server.transport.osc_codec import osc_encode_message


class FakeTransport:
    connected = True

    def __init__(self) -> None:
        self.sent: list[bytes] = []

    async def send(self, data: bytes) -> None:
        self.sent.append(data)


def _osc(address: str, *args: tuple[str, object]) -> bytes:
    return osc_encode_message(address, list(args))


ACME_OSC_BRINGUP = {
    "id": "acme_osc_bringup",
    "name": "Acme OSC Bringup",
    "manufacturer": "Acme",
    "category": "audio",
    "transport": "osc",
    "config_schema": {"enable_meters": {"type": "boolean", "label": "Meters"}},
    "default_config": {"enable_meters": False},
    "state_variables": {},
    "commands": {},
    "responses": [],
}


def _make_driver(on_connect, config, monkeypatch):
    """A connected OSC driver whose on_connect dispatch runs against a capturing
    transport. Stubs BaseDriver.connect (the ``super().connect()`` call) so no
    real socket is opened; the ConfigurableDriver on_connect logic still runs."""

    async def fake_super_connect(self) -> None:
        self.transport = FakeTransport()

    monkeypatch.setattr(base_mod.BaseDriver, "connect", fake_super_connect)

    definition = {**ACME_OSC_BRINGUP, "on_connect": on_connect}
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    cls = create_configurable_driver_class(definition)
    merged = {"host": "127.0.0.1", "port": 10023, **config}
    return cls("dev1", merged, state, events)


async def test_gated_bare_osc_address_sends_address_when_enabled(monkeypatch):
    # {send, when} gated ON: the bare address goes on the wire — before the fix
    # the dispatch read item["address"] (absent) and sent an empty address.
    driver = _make_driver(
        [{"send": "/xremote", "when": "enable_meters"}],
        {"enable_meters": True},
        monkeypatch,
    )
    await driver.connect()
    assert any(b"/xremote" in m for m in driver.transport.sent)


async def test_gated_bare_osc_address_skipped_when_disabled(monkeypatch):
    # Same entry, gate OFF: nothing is sent for it.
    driver = _make_driver(
        [{"send": "/xremote", "when": "enable_meters"}],
        {"enable_meters": False},
        monkeypatch,
    )
    await driver.connect()
    assert not any(b"/xremote" in m for m in driver.transport.sent)


async def test_osc_address_args_item_sends_typed_args(monkeypatch):
    # {address, args}: address + a typed integer argument.
    driver = _make_driver(
        [{"address": "/main/mute", "args": [{"type": "i", "value": "1"}]}],
        {},
        monkeypatch,
    )
    await driver.connect()
    assert _osc("/main/mute", ("i", 1)) in driver.transport.sent


async def test_gated_osc_args_item_respects_gate(monkeypatch):
    # {address, args, when} gated OFF: the whole entry, args and all, is skipped.
    driver = _make_driver(
        [
            {
                "address": "/main/mute",
                "args": [{"type": "i", "value": "1"}],
                "when": "enable_meters",
            }
        ],
        {"enable_meters": False},
        monkeypatch,
    )
    await driver.connect()
    assert not any(b"/main/mute" in m for m in driver.transport.sent)
