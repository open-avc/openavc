"""The contract observer: faults the runtime notices and used to keep to itself.

A driver's ``contract_observer`` is None in production. A device audit and the
Driver Builder's Test tab set it on the one driver they run, and it hears each
reply no response rule matched, each write to undeclared state, each value not
of its declared type, each value that did not convert, each unknown command and
each write for a child that was never registered. The audit's own collection
(``audit/observe.py``) and its two traffic-read faults are pinned here too.
"""

from __future__ import annotations

import asyncio

import pytest

from openavc.api.models import TestCommandRequest as CommandRequest
from openavc.api.routes.driver_test import _test_via_configurable_driver
from openavc.audit.observe import (
    EVENT_DETAIL_KEPT,
    AuditObserver,
    command_sent_nothing,
    replies_to_nobody,
)
from openavc.core.device_traffic import RX, TX, TrafficEntry, get_traffic_recorder
from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.base import (
    CHILD_UNREGISTERED,
    COERCION_FAILURE,
    TYPE_MISMATCH,
    UNDECLARED_STATE,
    UNKNOWN_COMMAND,
    UNMATCHED_RESPONSE,
    BaseDriver,
)
from openavc.drivers.configurable import create_configurable_driver_class
from openavc.utils.log_redaction import get_secret_registry


def _definition(**extra) -> dict:
    definition = {
        "id": "acme_widget",
        "name": "Acme Widget",
        "manufacturer": "Acme",
        "transport": "tcp",
        "default_config": {"host": "127.0.0.1", "port": 0, "poll_interval": 0},
        "state_variables": {
            "power": {"type": "boolean", "label": "Power"},
            "volume": {"type": "integer", "label": "Volume"},
            "input": {"type": "enum", "label": "Input", "values": ["hdmi1", "hdmi2"]},
        },
        "commands": {"query_power": {"label": "Query Power", "send": "PWR?\\r"}},
        "responses": [
            {"match": r"PWR=(\w+)", "set": {"power": "$1"}},
            {"match": r"VOL=(\S+)", "set": {"volume": "$1"}},
            {"match": r"INPUT=(\w+)", "set": {"input": "$1"}},
        ],
    }
    definition.update(extra)
    return definition


def _driver(definition: dict | None = None):
    cls = create_configurable_driver_class(definition or _definition())
    driver = cls("acme_1", {"host": "127.0.0.1", "port": 1}, StateStore(), EventBus())
    events: list[tuple[str, dict]] = []
    driver.contract_observer = lambda kind, detail: events.append((kind, detail))
    return driver, events


async def test_a_reply_no_rule_matched_is_reported():
    driver, events = _driver()
    await driver.on_data_received(b"PWR=on\r")
    assert [k for k, _ in events] == []
    await driver.on_data_received(b"LAMP=450\r")
    assert events == [(UNMATCHED_RESPONSE, {"text": "LAMP=450"})]


async def test_values_that_did_not_convert_are_reported():
    driver, events = _driver()
    await driver.on_data_received(b"VOL=loud\r")
    kinds = [k for k, _ in events]
    assert COERCION_FAILURE in kinds and TYPE_MISMATCH in kinds
    coercion = dict(events)[COERCION_FAILURE]
    assert coercion["raw"] == "loud" and coercion["type"] == "integer"
    mismatch = dict(events)[TYPE_MISMATCH]
    assert mismatch["state"] == "volume" and mismatch["value"] == "loud"

    events.clear()
    # A flag the device spelled some other way is stored as False, silently.
    await driver.on_data_received(b"PWR=STANDBY\r")
    assert events == [(
        COERCION_FAILURE, {"raw": "STANDBY", "type": "boolean", "stored": False},
    )]
    events.clear()
    await driver.on_data_received(b"PWR=01\r")
    assert events == []


async def test_an_enum_value_outside_the_declared_list_is_a_type_mismatch():
    driver, events = _driver()
    await driver.on_data_received(b"INPUT=vga\r")
    assert events and events[0][0] == TYPE_MISMATCH
    assert events[0][1]["problem"] == "not one of the declared values"


async def test_an_unknown_command_is_reported():
    driver, events = _driver()

    class Connected:
        connected = True

        async def send(self, data):
            pass

    driver.transport = Connected()
    assert await driver.send_command("does_not_exist") is None
    assert events == [(UNKNOWN_COMMAND, {"command": "does_not_exist"})]


def test_undeclared_writes_and_unregistered_children_are_reported_every_time():
    class AcmeCode(BaseDriver):
        DRIVER_INFO = {
            "id": "acme_code",
            "name": "Acme",
            "transport": "tcp",
            "state_variables": {"power": {"type": "boolean"}},
            "child_entities": {"output": {"state_variables": {"level": {"type": "integer"}}}},
        }

        async def send_command(self, command, params=None):
            return None

    driver = AcmeCode("acme_2", {}, StateStore(), EventBus())
    driver.strict_state = False  # the suite runs strict; production does not
    events: list = []
    driver.contract_observer = lambda kind, detail: events.append((kind, detail))
    driver.set_state("lamp_hours", 450)
    driver.set_state("lamp_hours", 451)
    driver.set_state("power", True)
    driver.set_child_state("output", 3, "level", 5)
    driver.set_child_state_batch("output", 4, {"level": 1})
    assert events == [
        (UNDECLARED_STATE, {"state": "lamp_hours"}),
        (UNDECLARED_STATE, {"state": "lamp_hours"}),
        (CHILD_UNREGISTERED, {"child_type": "output", "local_id": 3, "props": ["level"]}),
        (CHILD_UNREGISTERED, {"child_type": "output", "local_id": 4, "props": ["level"]}),
    ]


async def test_without_an_observer_nothing_changes_and_a_failing_one_is_contained():
    cls = create_configurable_driver_class(_definition())
    driver = cls("acme_3", {"host": "127.0.0.1", "port": 1}, StateStore(), EventBus())
    assert driver.contract_observer is None
    await driver.on_data_received(b"VOL=loud\r")  # no observer: the raw value is kept
    assert driver.get_state("volume") == "loud"

    def broken(kind, detail):
        raise RuntimeError("observer bug")

    driver.contract_observer = broken
    await driver.on_data_received(b"VOL=12\r")
    await driver.on_data_received(b"NOPE\r")
    assert driver.get_state("volume") == 12


async def test_an_osc_message_no_rule_matched_is_reported():
    from openavc.transport.osc_codec import osc_encode_message

    definition = _definition(
        transport="osc",
        responses=[{"address": "/acme/power", "set": {"power": "$1"}}],
    )
    driver, events = _driver(definition)
    await driver.on_data_received(osc_encode_message("/acme/meter", [("f", 0.5)]))
    assert events == [(UNMATCHED_RESPONSE, {"address": "/acme/meter", "args": [0.5]})]


# ---------------------------------------------------------------------------
# The Builder's Test tab (decision 3)
# ---------------------------------------------------------------------------


async def test_the_builder_test_reports_replies_no_rule_matched():
    async def handle(reader, writer):
        await reader.read(100)
        writer.write(b"PWR=on\rLAMP=450\r")
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        result = await _test_via_configurable_driver(CommandRequest(
            host="127.0.0.1", port=str(port), transport="tcp",
            definition=_definition(), command_name="query_power", timeout=2,
        ))
    finally:
        server.close()
        await server.wait_closed()
    assert result["unmatched"] == ["LAMP=450"]
    assert result["success"] is True


# ---------------------------------------------------------------------------
# The audit's collection
# ---------------------------------------------------------------------------


@pytest.fixture
def recorder():
    rec = get_traffic_recorder()
    rec.clear()
    yield rec
    rec.clear()
    get_secret_registry().clear()


def test_the_audit_keeps_traffic_up_to_its_cap(recorder):
    seen: list = []
    obs = AuditObserver("audit-x", cap_bytes=10, on_entry=seen.append)
    obs.start()
    recorder.record("audit-x", TX, b"12345", channel="tcp")
    recorder.record_chunk("audit-x", b"abc", channel="tcp")
    recorder.record("audit-x", RX, b"abcdef", channel="tcp")
    recorder.record("audit-x", RX, b"x", channel="tcp")
    obs.stop()
    recorder.record("audit-x", RX, b"after", channel="tcp")
    assert [e.data for e in obs.traffic] == [b"12345", b"abc"]
    assert obs.truncated_at is not None and obs.dropped_entries == 2
    assert [e.data for e in obs.frames()] == [b"12345"]
    assert len(seen) == 2


def test_the_audit_counts_every_contract_event_and_keeps_the_first(recorder):
    obs = AuditObserver("audit-x")

    class Holder:
        contract_observer = None

    holder = Holder()
    obs.attach(holder)
    for i in range(EVENT_DETAIL_KEPT + 5):
        holder.contract_observer(UNMATCHED_RESPONSE, {"text": str(i)})
    assert obs.event_counts == {UNMATCHED_RESPONSE: EVENT_DETAIL_KEPT + 5}
    assert len(obs.events) == EVENT_DETAIL_KEPT


def test_the_audit_redacts_with_secrets_it_kept_after_the_device_is_gone(recorder):
    registry = get_secret_registry()
    registry.set_config_secrets("audit-x", {"hunter22"})
    obs = AuditObserver("audit-x")
    obs.start()
    recorder.record("audit-x", TX, b"LOGIN hunter22\r", channel="tcp")
    obs.stop()
    registry.forget("audit-x")
    (entry,) = obs.serialized()
    assert entry["text"] == "LOGIN ***\r"


def _entry(t: float, direction: str, channel: str = "tcp", chunk: bool = False) -> TrafficEntry:
    return TrafficEntry(t=t, device_id="d", direction=direction, channel=channel,
                        data=b"x", chunk=chunk, seq=int(t * 1000))


def test_a_command_that_sent_nothing_is_found():
    entries = [_entry(10.0, TX), _entry(10.2, RX), _entry(20.0, RX, chunk=True)]
    assert command_sent_nothing(entries, 15.0, 16.0)
    assert not command_sent_nothing(entries, 9.9, 10.1)
    # A send completing just after the call returned still counts.
    assert not command_sent_nothing(entries, 9.0, 9.6)


def test_replies_to_nobody_are_the_ones_with_no_request_before_them():
    entries = [
        _entry(1.0, RX),                  # a greeting before any request
        _entry(2.0, TX), _entry(2.3, RX), _entry(2.6, RX),   # an answer in two frames
        _entry(9.0, RX),                  # unprompted, long after
        _entry(9.5, RX, channel="multicast"),   # push channels exist for this
        _entry(9.6, RX, chunk=True),
    ]
    found = replies_to_nobody(entries)
    assert [e.t for e in found] == [1.0, 9.0]
