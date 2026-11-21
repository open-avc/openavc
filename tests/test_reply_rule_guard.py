"""A YAML driver's reply rule that fails part-way through a reply.

One reply is read by several rules: every json rule whose keys the body
carries, the first text rule that matches (and the after_json rules after a
json read), one OSC rule per message of a bundle. A rule that raises while
it reads is skipped alone: what the rules before it wrote stays, the rules
after it still run, the reply counts as read (a rule matched it), and the
rule is reported once by name, in the log and on ``device.error``, and to a
contract observer every time. A strict-mode contract violation still raises.

The raise is injected by making one converter fail on one value, so these
hold whatever a real raise is; one case uses a real one, a throttle that
cannot be timed. On an invented Acme device.
"""

from __future__ import annotations

import asyncio
import logging
import struct
from typing import Any

import pytest

from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.base import RULE_FAILED, UndeclaredStateError
from openavc.drivers.configurable import create_configurable_driver_class
from openavc.transport.osc_codec import osc_encode_message

_LOGGER = "openavc.drivers.configurable"
_BOOM = "boom"


def _definition(transport: str, responses: list[dict[str, Any]], **over: Any) -> dict[str, Any]:
    definition: dict[str, Any] = {
        "id": "acme_widget",
        "name": "Acme Widget",
        "manufacturer": "Acme",
        "category": "utility",
        "version": "1.0.0",
        "transport": transport,
        "delimiter": "\\r\\n",
        "default_config": {"host": "", "port": 5000},
        "config_schema": {
            "host": {"type": "string", "required": True, "label": "IP Address"},
            "port": {"type": "integer", "default": 5000, "label": "Port"},
        },
        "state_variables": {
            "power": {"type": "string", "label": "Power"},
            "level": {"type": "integer", "label": "Level"},
            "label": {"type": "string", "label": "Label"},
        },
        "commands": {},
        "responses": responses,
    }
    definition.update(over)
    return definition


def _failing(convert: Any) -> Any:
    """``convert``, except that it raises on the value ``boom``."""

    def converter(raw: Any, value_type: str) -> Any:
        if str(raw) == _BOOM:
            raise ValueError("the value would not convert")
        return convert(raw, value_type)

    return converter


def _driver(definition: dict[str, Any]) -> tuple[Any, list[tuple[str, dict[str, Any]]], list[dict[str, Any]]]:
    """A driver whose converters fail on ``boom``, what its contract observer
    is told, and what it emits on ``device.error``."""
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    cls = create_configurable_driver_class(definition)
    driver = cls("dev1", {"host": "127.0.0.1", "port": 5000}, state, events)
    driver._convert_value = _failing(driver._convert_value)
    driver._convert_json_value = _failing(driver._convert_json_value)
    driver._convert_osc_value = _failing(driver._convert_osc_value)
    seen: list[tuple[str, dict[str, Any]]] = []
    driver.contract_observer = lambda kind, detail: seen.append((kind, detail))
    errors: list[dict[str, Any]] = []

    async def on_error(_event: str, payload: dict[str, Any]) -> None:
        errors.append(payload)

    events.on("device.error.dev1", on_error)
    return driver, seen, errors


def _bundle(*messages: bytes) -> bytes:
    """An OSC bundle holding ``messages``, timetag "immediately"."""
    elements = b"".join(struct.pack(">i", len(m)) + m for m in messages)
    return b"#bundle\x00" + struct.pack(">Q", 1) + elements


async def _emitted(driver: Any) -> None:
    """Wait for the driver's device.error emits, which run as tasks."""
    await asyncio.gather(*list(driver._rule_failure_emits))


def _failures(seen: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    return [detail for kind, detail in seen if kind == RULE_FAILED]


def _kinds(seen: list[tuple[str, dict[str, Any]]]) -> list[str]:
    return [kind for kind, _ in seen]


def _rule_warnings(caplog) -> list[str]:
    return [
        r.getMessage() for r in caplog.records
        if r.name == _LOGGER and r.levelno == logging.WARNING
        and "Response rule" in r.getMessage()
    ]


# --- json rules ------------------------------------------------------------


async def test_a_json_rule_that_fails_is_skipped_alone_and_the_body_counts_as_read():
    driver, seen, _ = _driver(_definition("tcp", [
        {"json": True, "set": {"power": "p"}},
        {"json": True, "set": {"level": "v"}},
        {"json": True, "set": {"label": "i"}},
    ]))
    await driver.on_data_received(b'{"p": "on", "v": "boom", "i": "hdmi1"}')
    assert driver.get_state("power") == "on"
    assert driver.get_state("level") is None
    assert driver.get_state("label") == "hdmi1"
    assert (driver._framed_messages, driver._parsed_messages) == (1, 1)
    assert driver._last_unread == ""
    assert _kinds(seen) == [RULE_FAILED]
    assert _failures(seen) == [{
        "rule": "json rule setting level",
        "error": "ValueError: the value would not convert",
        "text": '{"p": "on", "v": "boom", "i": "hdmi1"}',
    }]


async def test_a_body_only_a_failing_json_rule_matched_is_not_handed_to_the_text_rules():
    driver, seen, _ = _driver(_definition("tcp", [
        {"json": True, "set": {"level": "v"}},
        {"match": r'"v"', "set": {"label": "seen"}},
    ]))
    await driver.on_data_received(b'{"v": "boom"}')
    assert driver.get_state("label") is None
    assert (driver._framed_messages, driver._parsed_messages) == (1, 1)
    assert _kinds(seen) == [RULE_FAILED]


async def test_a_json_rule_names_its_child_types_too():
    driver, seen, _ = _driver(_definition(
        "tcp",
        [{"json": True, "set": {"level": "v"},
          "child_set": [{"type": "zone", "id": 1, "state": {"gain": "g"}}]}],
        child_entity_types={"zone": {
            "label": "Zone",
            "id_format": {"type": "integer", "min": 1, "max": 4},
            "state_variables": {"gain": {"type": "integer", "label": "Gain"}},
            "instances": {"count": 2, "label": "Zone {id}"},
        }},
    ))
    driver._register_declared_children()
    await driver.on_data_received(b'{"v": "boom", "g": 3}')
    assert _failures(seen)[0]["rule"] == "json rule setting level, zone"


# --- text (regex) rules ----------------------------------------------------


async def test_a_text_rule_whose_read_fails_has_still_read_the_line():
    driver, seen, _ = _driver(_definition("tcp", [
        {"match": r"LVL(\w+)", "set": {"level": "$1"}},
        {"match": r"LVL(\w+)", "set": {"label": "$1"}},
    ]))
    await driver.on_data_received(b"LVLboom")
    # First match wins: the rule that matched took the line, failing or not.
    assert driver.get_state("label") is None
    assert (driver._framed_messages, driver._parsed_messages) == (1, 1)
    assert driver._last_unread == ""
    assert _failures(seen) == [{
        "rule": r"match: LVL(\w+)",
        "error": "ValueError: the value would not convert",
        "text": "LVLboom",
    }]


async def test_an_after_json_rule_that_fails_leaves_the_json_reads_and_counts_once():
    driver, seen, _ = _driver(_definition("tcp", [
        {"json": True, "set": {"power": "p"}},
        {"match": r'"err": "(\w+)"', "after_json": True, "set": {"level": "$1"}},
    ]))
    await driver.on_data_received(b'{"p": "on", "err": "boom"}')
    assert driver.get_state("power") == "on"
    assert (driver._framed_messages, driver._parsed_messages) == (1, 1)
    assert _failures(seen)[0]["rule"] == r'match: "err": "(\w+)"'


@pytest.mark.parametrize("window", [float("nan"), float("inf"), 10**400], ids=["nan", "inf", "huge"])
async def test_a_throttle_that_cannot_be_timed_is_no_throttle(window: Any) -> None:
    """A window that is no usable number is not compiled, so the rule writes
    every reply rather than holding one against a fire never stamped."""
    driver, seen, _ = _driver(_definition("tcp", [
        {"match": r"PWR(\w+)", "throttle": window, "set": {"power": "$1"}},
    ]))
    await driver.on_data_received(b"PWRon")
    await driver.on_data_received(b"PWRoff")
    assert (driver._framed_messages, driver._parsed_messages) == (2, 2)
    assert _failures(seen) == []
    assert driver.get_state("power") == "off"


# --- OSC rules -------------------------------------------------------------


async def test_an_osc_bundle_is_read_past_a_message_whose_rule_fails():
    driver, seen, _ = _driver(_definition("osc", [
        {"address": "/acme/level", "mappings": [{"arg": 0, "state": "level", "type": "integer"}]},
        {"address": "/acme/label", "mappings": [{"arg": 0, "state": "label"}]},
    ]))
    bundle = _bundle(
        osc_encode_message("/acme/level", [("s", "boom")]),
        osc_encode_message("/acme/label", [("s", "Lobby")]),
    )
    await driver.on_data_received(bundle)
    assert driver.get_state("label") == "Lobby"
    assert (driver._framed_messages, driver._parsed_messages) == (2, 2)
    assert _failures(seen) == [{
        "rule": "address: /acme/level",
        "error": "ValueError: the value would not convert",
        "address": "/acme/level",
        "args": ["boom"],
    }]


async def test_a_single_osc_message_whose_rule_fails_is_not_told_unread():
    driver, seen, _ = _driver(_definition("osc", [
        {"address": "/acme/*", "mappings": [{"arg": 0, "state": "level", "type": "integer"}]},
    ]))
    await driver.on_data_received(osc_encode_message("/acme/level", [("s", "boom")]))
    assert driver._last_unread == ""
    assert _kinds(seen) == [RULE_FAILED]


# --- how a failure is reported ---------------------------------------------


async def test_a_failing_rule_is_reported_once_by_name_and_told_to_the_observer_every_time(caplog):
    driver, seen, errors = _driver(_definition("tcp", [
        {"match": r"LVL(\w+)", "set": {"level": "$1"}},
    ]))
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        await driver.on_data_received(b"LVLboom")
        await driver.on_data_received(b"LVLboom")
    await _emitted(driver)
    sentence = (
        r'Response rule "match: LVL(\w+)" failed on a reply and was skipped: '
        "ValueError: the value would not convert. Check this rule in the driver."
    )
    assert _rule_warnings(caplog) == [f"[dev1] {sentence}"]
    assert errors == [{"device_id": "dev1", "error": sentence, "source": "response_rule"}]
    assert _kinds(seen) == [RULE_FAILED, RULE_FAILED]


async def test_two_failing_rules_are_each_reported():
    driver, _, errors = _driver(_definition("tcp", [
        {"json": True, "set": {"level": "v"}},
        {"json": True, "set": {"label": "w"}},
    ]))
    await driver.on_data_received(b'{"v": "boom", "w": "boom"}')
    await _emitted(driver)
    assert [e["error"].split('"')[1] for e in errors] == [
        "json rule setting level", "json rule setting label",
    ]


async def test_an_error_with_no_text_is_named_by_its_type():
    driver, seen, _ = _driver(_definition("tcp", [{"match": r"LVL(\w+)", "set": {"level": "$1"}}]))

    def raises(_raw: Any, _type: str) -> Any:
        raise RuntimeError()

    driver._convert_value = raises
    await driver.on_data_received(b"LVL7")
    assert _failures(seen)[0]["error"] == "RuntimeError"


async def test_an_http_answer_a_rule_fails_on_still_returns_the_answer():
    """The reply rules read an HTTP answer inline; a rule that fails on it
    does not make the request read as failed."""
    driver, seen, errors = _driver(_definition("http", [
        {"json": True, "set": {"level": "v"}},
    ]))

    class _Answer:
        status_code = 200
        text = '{"v": "boom"}'

    answer = _Answer()
    assert await driver._process_http_response("get_level", answer) is answer
    await _emitted(driver)
    assert _kinds(seen) == [RULE_FAILED]
    assert len(errors) == 1


async def test_reading_a_reply_does_not_wait_for_device_error_handlers():
    """The bus waits for its handlers, a script's included, and the device's
    next message is read only after this one."""
    driver, _, _ = _driver(_definition("tcp", [{"match": r"LVL(\w+)", "set": {"level": "$1"}}]))
    release = asyncio.Event()

    async def slow_handler(_event: str, _payload: dict[str, Any]) -> None:
        await release.wait()

    driver.events.on("device.error.dev1", slow_handler)
    await asyncio.wait_for(driver.on_data_received(b"LVLboom"), timeout=1)
    release.set()
    await _emitted(driver)


async def test_a_secret_filled_into_a_rule_is_masked_where_the_rule_is_named():
    definition = _definition("tcp", [{"match": r"{pin}:LVL(\w+)", "set": {"level": "$1"}}])
    definition["config_schema"]["pin"] = {"type": "string", "secret": True, "label": "PIN"}
    state = StateStore()
    events = EventBus()
    cls = create_configurable_driver_class(definition)
    driver = cls("dev1", {"host": "127.0.0.1", "port": 5000, "pin": "s3cr3tpin"}, state, events)
    driver._convert_value = _failing(driver._convert_value)
    seen: list[tuple[str, dict[str, Any]]] = []
    driver.contract_observer = lambda kind, detail: seen.append((kind, detail))
    errors: list[dict[str, Any]] = []

    async def on_error(_event: str, payload: dict[str, Any]) -> None:
        errors.append(payload)

    events.on("device.error.dev1", on_error)
    await driver.on_data_received(b"s3cr3tpin:LVLboom")
    await _emitted(driver)
    assert "s3cr3tpin" not in _failures(seen)[0]["rule"]
    assert "s3cr3tpin" not in errors[0]["error"]


async def test_strict_mode_still_raises_a_write_to_an_undeclared_variable():
    """The platform's own suite runs strict, and the Driver Builder's test sets
    it on the one driver it runs: there an undeclared write is not a rule
    failure to skip but the contract violation strict mode exists to stop."""
    driver, seen, errors = _driver(_definition("tcp", [{"match": r"X(\w+)", "set": {"lamp_hours": "$1"}}]))
    driver.strict_state = True
    with pytest.raises(UndeclaredStateError):
        await driver.on_data_received(b"X12")
    assert RULE_FAILED not in _kinds(seen)
    assert errors == []


# --- the poll loop's unread-replies check, end to end ----------------------


@pytest.fixture
async def answering_server():
    """A TCP server answering each ``PWR?`` line with ``PWR=boom``."""

    async def handle(reader, writer):
        buf = b""
        try:
            while chunk := await reader.read(4096):
                buf += chunk
                while b"\r" in buf:
                    line, buf = buf.split(b"\r", 1)
                    if line == b"PWR?":
                        writer.write(b"PWR=boom\r")
                        await writer.drain()
        except (ConnectionError, OSError):
            pass
        finally:
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    yield server.sockets[0].getsockname()[1]
    server.close()
    await server.wait_closed()


async def test_replies_a_failing_rule_reads_never_say_the_driver_reads_none_of_them(
    answering_server, caplog,
):
    definition = _definition(
        "tcp",
        [{"match": r"PWR=(\w+)", "set": {"level": "$1"}}],
        delimiter="\r",
        polling={"queries": ["PWR?\r"]},
    )
    definition["state_variables"]["last_error"] = {"type": "string", "label": "Last Error"}
    cls = create_configurable_driver_class(definition)
    config = {
        "host": "127.0.0.1", "port": answering_server,
        "poll_interval": 0.05, "max_missed_polls": 3,
    }
    driver = cls(device_id="acme1", config=config, state=StateStore(), events=EventBus())
    driver._convert_value = _failing(driver._convert_value)
    caplog.set_level(logging.WARNING)
    await driver.connect()
    try:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 3
        while driver._parsed_messages < 6 and loop.time() < deadline:
            await asyncio.sleep(0.02)
        assert driver._parsed_messages >= 6
        assert driver.get_state("connected") is True
        assert "reads none of it" not in str(driver.get_state("last_error") or "")
        assert not any("Replies arrive but" in r.getMessage() for r in caplog.records)
        assert len([r for r in caplog.records if "Response rule" in r.getMessage()]) == 1
    finally:
        await driver.disconnect()


# --- what a device audit says ----------------------------------------------


def test_a_device_audit_names_the_failed_rule_and_its_error():
    from openavc.audit.observe import CONTRACT_TEXT, contract_example
    from openavc.audit.report import _contract_problems

    detail = {"rule": r"match: PWR=(\w+)", "error": "KeyError: ''", "text": "PWR=on"}
    assert contract_example(detail) == '"match: PWR=(\\w+)" ' + "(KeyError: '')"
    assert CONTRACT_TEXT[RULE_FAILED] == "A response rule failed while reading a reply"
    html = "".join(_contract_problems({
        "counts": {RULE_FAILED: 2},
        "events": [{"kind": RULE_FAILED, "detail": detail}],
    }))
    assert "A response rule failed while reading a reply: 2" in html
    assert "match: PWR=(\\w+)" in html


def test_every_contract_event_has_audit_words():
    """The report's words list every kind, and the audit wizard is sent them
    beside the counts (it keeps no list of its own)."""
    from openavc.audit.observe import CONTRACT_TEXT, contract_labels
    from openavc.drivers.base import CONTRACT_EVENTS

    assert set(CONTRACT_TEXT) == set(CONTRACT_EVENTS)
    assert contract_labels(dict.fromkeys(CONTRACT_EVENTS, 1)) == CONTRACT_TEXT
