"""How a YAML driver spaces the lines it sends on its own: on_connect and polling.

Three behaviours, each exercised with an invented device:

* **The default gap.** Sent back to back, a poll of any size reaches a tcp or
  serial device as one burst, and a device that reads slower than that keeps
  only what fits its receive buffer. With no ``inter_command_delay`` declared,
  the driver leaves ``DEFAULT_LINE_GAP_S`` between its own lines -- between
  sends, outside the transport's lock, so a command somebody presses is never
  queued behind the rest of a poll. With a delay declared the transport waits
  after every write, so the driver adds nothing.
* **No doubled start-up delay.** on_connect used to wait ``inter_command_delay``
  after each line on top of the transport's own wait, on tcp, serial, udp and
  osc alike. Only HTTP, whose transport does not wait, still waits here.
* **No repeat at connect.** The first poll runs straight after on_connect, so a
  line start-up has just sent is not sent again by it.

Time is a fake clock and ``asyncio.sleep`` a recorder inside the driver module,
so every assertion is about the order of sends and pauses, never wall time.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers import base as base_mod
from openavc.drivers import configurable as configurable_mod
from openavc.drivers.configurable import (
    DEFAULT_LINE_GAP_S,
    create_configurable_driver_class,
)
from openavc.drivers.dry_run import preview_connect
from openavc.transport.osc_codec import osc_encode_message

_real_sleep = asyncio.sleep


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now

    def __getattr__(self, name: str) -> Any:
        import time

        return getattr(time, name)


class _RecordingAsyncio:
    """``asyncio`` as the driver module sees it, with ``sleep`` recorded.

    A pause advances the fake clock and then yields once for real, so a task
    waiting to send (a press) gets its turn exactly where the poll pauses.
    """

    def __init__(self, clock: _Clock, log: list[tuple[str, Any]]) -> None:
        self._clock = clock
        self._log = log

    def __getattr__(self, name: str) -> Any:
        return getattr(asyncio, name)

    async def sleep(self, seconds: float, result: Any = None) -> Any:
        self._log.append(("pause", round(seconds, 6)))
        self._clock.now += seconds
        await _real_sleep(0)
        return result


class _Wire:
    connected = True

    def __init__(self, log: list[tuple[str, Any]]) -> None:
        self._log = log

    async def send(self, data: bytes) -> None:
        self._log.append(("send", data))


@pytest.fixture
def wire_log(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, Any]]:
    log: list[tuple[str, Any]] = []
    clock = _Clock()
    recorder = _RecordingAsyncio(clock, log)
    monkeypatch.setattr(configurable_mod, "asyncio", recorder)
    monkeypatch.setattr(configurable_mod, "time", clock)
    # The line gap is measured and slept through the pacing helpers.
    monkeypatch.setattr(configurable_mod, "pacing_clock", clock.monotonic)
    monkeypatch.setattr(configurable_mod, "sleep_at_least", recorder.sleep)
    return log


def _acme(transport: str = "tcp", **extra: Any) -> dict[str, Any]:
    definition: dict[str, Any] = {
        "id": f"acme_widget_{transport}",
        "name": "Acme Widget",
        "manufacturer": "Acme",
        "category": "utility",
        "transport": transport,
        "default_config": {},
        "config_schema": {},
        "state_variables": {},
        "commands": {"power_on": {"label": "Power On", "send": "PWR 1\\r"}},
        "responses": [],
        "on_connect": ["SUB\\r", "A?\\r"],
        "polling": {"queries": ["A?\\r", "B?\\r", "C?\\r"]},
    }
    definition.update(extra)
    return definition


def _driver(definition: dict[str, Any], log: list[tuple[str, Any]], **config: Any) -> Any:
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    cls = create_configurable_driver_class(definition)
    driver = cls("acme1", {"host": "127.0.0.1", "port": 5000, **config}, state, events)
    driver.transport = _Wire(log)
    return driver


GAP = ("pause", DEFAULT_LINE_GAP_S)


# --- the default gap -------------------------------------------------------


@pytest.mark.parametrize("transport", ["tcp", "serial"])
async def test_poll_lines_are_spaced_when_the_driver_declares_no_delay(transport, wire_log):
    driver = _driver(_acme(transport), wire_log)
    await driver.poll()
    assert wire_log == [
        ("send", b"A?\r"), GAP, ("send", b"B?\r"), GAP, ("send", b"C?\r"),
    ]


async def test_a_declared_delay_is_left_to_the_transport(wire_log):
    # The real transport waits 0.2 s after every write, inside its lock; the
    # driver adds no pause of its own on top.
    driver = _driver(_acme(), wire_log, inter_command_delay=0.2)
    await driver.poll()
    assert wire_log == [("send", b"A?\r"), ("send", b"B?\r"), ("send", b"C?\r")]


async def test_datagram_lines_are_not_paced_by_the_driver(wire_log):
    driver = _driver(_acme("udp"), wire_log)
    await driver.poll()
    assert [kind for kind, _ in wire_log] == ["send", "send", "send"]


async def test_a_press_goes_out_between_poll_lines_not_after_the_poll(wire_log):
    driver = _driver(_acme(), wire_log)
    polling = asyncio.create_task(driver.poll())
    await _real_sleep(0)  # the poll sends its first line, then pauses
    await driver.send_command("power_on")
    await polling
    sent = [data for kind, data in wire_log if kind == "send"]
    assert sent == [b"A?\r", b"PWR 1\r", b"B?\r", b"C?\r"]


async def test_start_up_lines_are_spaced_and_so_is_the_first_poll_line(wire_log):
    driver = _driver(_acme(), wire_log)
    await driver._run_on_connect()
    await driver.poll()
    assert wire_log == [
        ("send", b"SUB\r"), GAP, ("send", b"A?\r"),
        GAP,  # the last start-up line and the first poll line
        ("send", b"A?\r"), GAP, ("send", b"B?\r"), GAP, ("send", b"C?\r"),
    ]


async def test_a_connection_preview_records_the_lines_without_pausing(wire_log):
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    cls = create_configurable_driver_class(_acme())
    preview = await preview_connect(cls("acme1", {"host": "127.0.0.1", "port": 5000}, state, events))
    assert preview.available
    assert [step["data"] for step in preview.steps] == [
        b"SUB\r", b"A?\r", b"A?\r", b"B?\r", b"C?\r",
    ]
    assert not [entry for entry in wire_log if entry[0] == "pause"]


# --- no doubled start-up delay ---------------------------------------------


@pytest.mark.parametrize("transport", ["tcp", "serial", "udp"])
async def test_start_up_does_not_wait_on_top_of_the_transport(transport, wire_log):
    driver = _driver(_acme(transport), wire_log, inter_command_delay=0.2)
    await driver._run_on_connect()
    assert wire_log == [("send", b"SUB\r"), ("send", b"A?\r")]


def _acme_osc(**extra: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "commands": {},
        "on_connect": ["/xremote", {"send": "/info"}],
        "polling": {"queries": ["/status"]},
    }
    fields.update(extra)
    return _acme("osc", **fields)


async def test_osc_start_up_does_not_wait_on_top_of_the_transport(wire_log):
    # OSC writes through the udp (or tcp) transport, which already waits.
    driver = _driver(_acme_osc(), wire_log, inter_command_delay=0.2)
    await driver._run_on_connect()
    assert wire_log == [
        ("send", osc_encode_message("/xremote")), ("send", osc_encode_message("/info")),
    ]


@pytest.mark.parametrize(("delay", "pauses"), [(0, [("pause", 0.005)]), (0.2, [])])
async def test_osc_state_queries_at_start_up_keep_their_floor_only_without_a_delay(
    delay, pauses, wire_log,
):
    driver = _driver(
        _acme_osc(
            on_connect=["/xremote"],
            responses=[{"address": "/ch/01/fader", "mappings": []}],
        ),
        wire_log,
        inter_command_delay=delay,
    )
    await driver._run_on_connect()
    assert wire_log == [
        ("send", osc_encode_message("/xremote")),
        ("send", osc_encode_message("/ch/01/fader")),
        *pauses,
    ]


# --- no repeat at connect --------------------------------------------------


@pytest.fixture
def connect_without_a_socket(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """``super().connect()`` stubbed (the driver keeps the wire it was given)
    and ``start_polling`` recorded instead of started, so a test runs the
    first poll itself."""
    started: list[float] = []

    async def no_socket(self: Any) -> None:
        return None

    async def record_start(self: Any, interval: float) -> None:
        started.append(interval)

    monkeypatch.setattr(base_mod.BaseDriver, "connect", no_socket)
    monkeypatch.setattr(base_mod.BaseDriver, "start_polling", record_start)
    return started


async def test_the_first_poll_skips_what_start_up_just_sent(wire_log, connect_without_a_socket):
    driver = _driver(_acme(), wire_log, poll_interval=10)
    await driver.connect()
    assert connect_without_a_socket == [10]
    wire_log.clear()

    await driver.poll()
    assert [d for k, d in wire_log if k == "send"] == [b"B?\r", b"C?\r"]

    wire_log.clear()
    await driver.poll()
    assert [d for k, d in wire_log if k == "send"] == [b"A?\r", b"B?\r", b"C?\r"]


async def test_with_polling_off_a_later_poll_skips_nothing(wire_log, connect_without_a_socket):
    driver = _driver(_acme(), wire_log, poll_interval=0)
    await driver.connect()
    assert connect_without_a_socket == []
    wire_log.clear()
    await driver.poll()
    assert [d for k, d in wire_log if k == "send"] == [b"A?\r", b"B?\r", b"C?\r"]


async def test_osc_start_up_lines_are_not_repeated_by_the_first_poll(
    wire_log, connect_without_a_socket,
):
    driver = _driver(
        _acme_osc(polling={"queries": ["/xremote", "/status"]}), wire_log, poll_interval=10,
    )
    await driver.connect()
    wire_log.clear()
    await driver.poll()
    assert [d for k, d in wire_log if k == "send"] == [osc_encode_message("/status")]
