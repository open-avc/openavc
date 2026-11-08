"""The lines a YAML driver sends on its own: start-up (``on_connect``) and
the poll, line by line.

What each entry form sends, in what order, which ones the start-up notes so
the first poll does not ask again, and what a failed line does to the rest.
Called straight on the driver with a capturing transport and pacing off, so
nothing here waits on a clock or a socket.

Invented devices ("acme_*") and synthetic payloads throughout.
"""

from __future__ import annotations

import copy
import logging
from typing import Any

import pytest

from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.base import ConnectionFaultError
from openavc.drivers.configurable import create_configurable_driver_class
from openavc.transport.osc import OSCTransport
from openavc.transport.osc_codec import osc_encode_message

OSC_DEVICE: dict[str, Any] = {
    "id": "acme_osc_desk",
    "name": "Acme OSC Desk",
    "manufacturer": "Acme",
    "category": "audio",
    "transport": "osc",
    "config_schema": {"meters": {"type": "boolean", "label": "Meters"}},
    "default_config": {"meters": False, "bus": "B1"},
    "state_variables": {
        "standby": {"type": "boolean", "label": "Standby"},
        "level": {"type": "float", "label": "Level"},
    },
    "commands": {"recall": {"label": "Recall", "address": "/recall"}},
    "responses": [
        {"address": "/acme/standby", "mappings": [{"arg": 0, "state": "standby", "type": "boolean"}]},
        {"address": "/acme/{bus}/level", "mappings": [{"arg": 0, "state": "level", "type": "float"}]},
        {"address": "/acme/*/meter", "mappings": [{"arg": 0, "state": "level", "type": "float"}]},
    ],
}

LINE_DEVICE: dict[str, Any] = {
    "id": "acme_line_unit",
    "name": "Acme Line Unit",
    "manufacturer": "Acme",
    "category": "video",
    "transport": "tcp",
    "delimiter": "\\r",
    "default_config": {"host": "", "port": 23, "zone": "Z1"},
    "state_variables": {
        "standby": {"type": "boolean", "label": "Standby"},
        "level": {"type": "integer", "label": "Level"},
    },
    "commands": {"hello": {"label": "Hello", "send": "HELLO\\r"}},
    "responses": [
        {"match": r"^STANDBY=(\d)$", "set": {"standby": "$1"}},
        {"match": r"^LEVEL=(\d+)$", "set": {"level": "$1"}},
    ],
}


class Capture:
    """A transport that records what is sent; ``fail`` names a substring of
    the bytes whose send raises ``error``."""

    connected = True

    def __init__(self, fail: bytes | None = None, error: BaseException | None = None) -> None:
        self.sent: list[bytes] = []
        self.fail = fail
        self.error = error or OSError("write failed")
        self.on_send: Any = None
        self.on_idle: Any = None

    async def wait_idle(self) -> None:
        """Where a gated line waits for the write before it; ``on_idle`` is
        what the device says meanwhile."""
        if self.on_idle is not None:
            self.on_idle()

    async def send(self, data: bytes) -> None:
        if self.fail is not None and self.fail in data:
            raise self.error
        self.sent.append(data)
        if self.on_send is not None:
            await self.on_send(data)


class OscCapture(OSCTransport, Capture):
    """Capture, passing as an OSC transport (an OSC command checks)."""

    connected = True
    _tcp_mode = False
    _udp = None

    def __init__(self, fail: bytes | None = None, error: BaseException | None = None) -> None:
        Capture.__init__(self, fail, error)

    send = Capture.send
    wait_idle = Capture.wait_idle


def _driver(definition: dict[str, Any], transport: Any, **config: Any):
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    cls = create_configurable_driver_class(definition)
    base = {"host": "127.0.0.1", "port": 9000, **definition.get("default_config", {})}
    driver = cls("dev1", {**base, **config}, state, events)
    driver.transport = transport
    driver._pace_lines = False
    return driver


def _osc(address: str, *args: tuple[str, Any]) -> bytes:
    return osc_encode_message(address, list(args))


def _with(definition: dict[str, Any], **extra: Any) -> dict[str, Any]:
    out = copy.deepcopy(definition)
    out.update(extra)
    return out


# ── OSC start-up ──


async def test_osc_start_up_sends_each_form_then_asks_every_reply_address() -> None:
    definition = _with(OSC_DEVICE, on_connect=[
        "/hello/{bus}",
        {"send": "/sub/{bus}"},
        {"address": "/set/{bus}", "args": [{"type": "i", "value": "3"}]},
        {"address": "/plain"},
        42,
    ])
    transport = OscCapture()
    driver = _driver(definition, transport, bus="B2")
    await driver._run_on_connect()
    assert transport.sent == [
        _osc("/hello/B2"), _osc("/sub/B2"), _osc("/set/B2", ("i", 3)), _osc("/plain"),
        _osc("/acme/standby"), _osc("/acme/B2/level"),
    ]
    # An item with args is a write, not a read: the first poll still asks it.
    assert driver._start_up_lines == {
        "/hello/{bus}", "/sub/{bus}", "/plain", "/acme/standby", "/acme/B2/level",
    }


async def test_osc_start_up_skips_an_entry_whose_when_is_off() -> None:
    definition = _with(OSC_DEVICE, on_connect=[
        {"send": "/meters", "when": "meters"},
        {"address": "/arm", "args": [{"type": "i", "value": "1"}], "when": "meters"},
        "/always",
    ])
    transport = OscCapture()
    driver = _driver(definition, transport)
    await driver._run_on_connect()
    assert transport.sent[:1] == [_osc("/always")]
    assert all(b"/meters" not in m and b"/arm" not in m for m in transport.sent)


async def test_osc_start_up_expands_each_child_and_notes_the_expanded_line() -> None:
    definition = _with(
        OSC_DEVICE,
        on_connect=[{"each_child": "input", "send": "/in/{child_id}/{bus}"}],
        child_entity_types={"input": {
            "label": "Input", "id_format": {"type": "integer"}, "state_variables": {},
        }},
    )
    transport = OscCapture()
    driver = _driver(definition, transport)
    driver.register_child("input", 1)
    driver.register_child("input", 2)
    await driver._run_on_connect()
    assert transport.sent[:2] == [_osc("/in/1/B1"), _osc("/in/2/B1")]
    assert {"/in/1/{bus}", "/in/2/{bus}"} <= driver._start_up_lines


async def test_osc_start_up_judges_an_entry_gate_against_the_state_now() -> None:
    definition = _with(OSC_DEVICE, on_connect=[
        {"send": "/wake"},
        {"send": "/meters", "when_state": {"standby": False}},
        "/after",
    ])
    transport = OscCapture()
    driver = _driver(definition, transport)

    async def reply(data: bytes) -> None:
        if b"/wake" in data:
            driver.set_state("standby", True)

    transport.on_send = reply
    await driver._run_on_connect()
    assert transport.sent[:2] == [_osc("/wake"), _osc("/after")]


async def test_a_failed_osc_start_up_line_is_logged_and_the_next_one_sent(caplog) -> None:
    definition = _with(OSC_DEVICE, on_connect=["/broken", "/fine"])
    transport = OscCapture(fail=b"/broken")
    driver = _driver(definition, transport)
    with caplog.at_level(logging.WARNING):
        await driver._run_on_connect()
    assert transport.sent[0] == _osc("/fine")
    assert "/broken" not in driver._start_up_lines
    assert "[dev1] on_connect OSC command failed: write failed" in caplog.text


async def test_a_failed_osc_reply_address_ask_is_logged_and_the_rest_asked(caplog) -> None:
    definition = _with(OSC_DEVICE, on_connect=["/hello"])
    transport = OscCapture(fail=b"/acme/standby")
    driver = _driver(definition, transport)
    with caplog.at_level(logging.WARNING):
        await driver._run_on_connect()
    assert transport.sent == [_osc("/hello"), _osc("/acme/B1/level")]
    assert "/acme/standby" not in driver._start_up_lines
    assert "[dev1] OSC initial query failed: write failed" in caplog.text


async def test_the_reply_address_ask_stops_at_a_shut_poll_gate() -> None:
    definition = _with(
        OSC_DEVICE, on_connect=["/hello"],
        polling={"queries": [], "when_state": {"standby": False}},
    )
    transport = OscCapture()
    driver = _driver(definition, transport)

    async def reply(data: bytes) -> None:
        if b"/acme/standby" in data:
            driver.set_state("standby", True)

    transport.on_send = reply
    await driver._run_on_connect()
    assert transport.sent == [_osc("/hello"), _osc("/acme/standby")]


async def test_osc_start_up_with_nothing_declared_or_no_connection_sends_nothing() -> None:
    transport = OscCapture()
    driver = _driver(_with(OSC_DEVICE, on_connect=[]), transport)
    driver._start_up_lines = {"stale"}
    await driver._run_on_connect()
    assert transport.sent == []
    assert driver._start_up_lines == set()

    gone = OscCapture()
    gone.connected = False
    driver = _driver(_with(OSC_DEVICE, on_connect=["/hello"]), gone)
    await driver._run_on_connect()
    assert gone.sent == []


# ── Line start-up ──


async def test_line_start_up_sends_commands_and_raw_lines_and_notes_them() -> None:
    definition = _with(LINE_DEVICE, on_connect=["hello", "RAW {zone}\\r"])
    transport = Capture()
    driver = _driver(definition, transport)
    await driver._run_on_connect()
    assert transport.sent == [b"HELLO\r", b"RAW Z1\r"]
    assert driver._start_up_lines == {"hello", "RAW {zone}\\r"}


async def test_line_start_up_skips_a_step_its_gate_shuts_and_sends_the_rest() -> None:
    definition = _with(LINE_DEVICE, on_connect=[
        "WAKE\\r",
        {"send": "METERS\\r", "when_state": {"standby": False}},
        "AFTER\\r",
    ])
    transport = Capture()
    driver = _driver(definition, transport)

    async def reply(data: bytes) -> None:
        if data == b"WAKE\r":
            driver.set_state("standby", True)

    transport.on_send = reply
    await driver._run_on_connect()
    assert transport.sent == [b"WAKE\r", b"AFTER\r"]
    assert "METERS\\r" not in driver._start_up_lines


@pytest.mark.parametrize("error", [
    OSError("write failed"),
    ConnectionFaultError("gone quiet", code="no_response"),
])
async def test_a_failed_line_step_is_logged_not_noted_and_the_next_sent(error, caplog) -> None:
    definition = _with(LINE_DEVICE, on_connect=["BROKEN\\r", "FINE\\r"])
    transport = Capture(fail=b"BROKEN", error=error)
    driver = _driver(definition, transport)
    with caplog.at_level(logging.WARNING):
        await driver._run_on_connect()
    assert transport.sent == [b"FINE\r"]
    assert driver._start_up_lines == {"FINE\\r"}
    assert f"[dev1] on_connect command failed: {error}" in caplog.text


async def test_a_refused_login_at_start_up_ends_it_and_is_raised() -> None:
    definition = _with(LINE_DEVICE, on_connect=["LOGIN\\r", "NEXT\\r"])
    error = ConnectionFaultError("refused", code="auth_failed")
    transport = Capture(fail=b"LOGIN", error=error)
    driver = _driver(definition, transport)
    with pytest.raises(ConnectionFaultError) as caught:
        await driver._run_on_connect()
    assert caught.value is error
    assert transport.sent == []


# ── The poll, line by line ──


async def test_a_poll_on_a_closed_connection_sends_nothing_and_keeps_its_skips() -> None:
    definition = _with(LINE_DEVICE, polling={"queries": ["Q\\r"]})
    transport = Capture()
    transport.connected = False
    driver = _driver(definition, transport)
    driver._first_poll_skips = {"Q\\r"}
    await driver.poll()
    assert transport.sent == []
    assert driver._first_poll_skips == {"Q\\r"}


async def test_a_held_poll_forgets_the_first_poll_skips() -> None:
    definition = _with(
        LINE_DEVICE, polling={"queries": ["Q\\r"], "when_state": {"standby": False}},
    )
    transport = Capture()
    driver = _driver(definition, transport)
    driver.set_state("standby", True)
    driver._first_poll_skips = {"Q\\r"}
    await driver.poll()
    assert transport.sent == []
    assert driver._first_poll_skips is None


async def test_the_first_poll_skips_what_start_up_sent_and_only_once() -> None:
    definition = _with(LINE_DEVICE, polling={"queries": ["A\\r", "B\\r"]})
    transport = Capture()
    driver = _driver(definition, transport)
    driver._first_poll_skips = {"A\\r"}
    await driver.poll()
    await driver.poll()
    assert transport.sent == [b"B\r", b"A\r", b"B\r"]


async def test_an_osc_poll_sends_commands_by_name_and_raw_addresses() -> None:
    definition = _with(OSC_DEVICE, polling={"queries": ["recall", "/acme/{bus}/level", "/plain"]})
    transport = OscCapture()
    driver = _driver(definition, transport, bus="B3")
    await driver.poll()
    assert transport.sent == [_osc("/recall"), _osc("/acme/B3/level"), _osc("/plain")]


async def test_an_osc_poll_skips_a_gated_entry_and_stops_at_a_shut_poll_gate() -> None:
    definition = _with(OSC_DEVICE, polling={
        "queries": [
            "/first",
            {"send": "/meters", "when_state": {"level": 0}},
            "/sleep",
            "/never",
        ],
        "when_state": {"standby": False},
    })
    transport = OscCapture()
    driver = _driver(definition, transport)
    driver.set_state("level", 5.0)

    async def reply(data: bytes) -> None:
        if b"/sleep" in data:
            driver.set_state("standby", True)

    transport.on_send = reply
    await driver.poll()
    assert transport.sent == [_osc("/first"), _osc("/sleep")]


async def test_a_line_poll_stops_at_a_shut_poll_gate_across_entries() -> None:
    definition = _with(LINE_DEVICE, polling={
        "queries": ["A\\r", "SLEEP\\r", "NEVER\\r", "NEVER2\\r"],
        "when_state": {"standby": False},
    })
    transport = Capture()
    driver = _driver(definition, transport)

    async def reply(data: bytes) -> None:
        if data == b"SLEEP\r":
            driver.set_state("standby", True)

    transport.on_send = reply
    await driver.poll()
    assert transport.sent == [b"A\r", b"SLEEP\r"]


@pytest.mark.parametrize("error", [OSError("write failed"), TimeoutError("late"), ConnectionError("gone")])
async def test_a_transport_failure_ends_the_poll_and_is_raised(error, caplog) -> None:
    definition = _with(LINE_DEVICE, polling={"queries": ["A\\r", "B\\r"]})
    transport = Capture(fail=b"A", error=error)
    driver = _driver(definition, transport)
    with caplog.at_level(logging.WARNING), pytest.raises(type(error)):
        await driver.poll()
    assert transport.sent == []
    assert "[dev1] Poll query failed (transport)" in caplog.text
    assert driver._http_cycle is None


async def test_a_refused_login_ends_the_poll_without_the_transport_warning(caplog) -> None:
    definition = _with(LINE_DEVICE, polling={"queries": ["A\\r", "B\\r"]})
    error = ConnectionFaultError("refused", code="auth_failed")
    transport = Capture(fail=b"A", error=error)
    driver = _driver(definition, transport)
    with caplog.at_level(logging.WARNING), pytest.raises(ConnectionFaultError):
        await driver.poll()
    assert "Poll query failed" not in caplog.text


async def test_a_line_that_fails_to_build_reports_a_device_error_and_the_poll_goes_on() -> None:
    definition = _with(LINE_DEVICE, polling={"queries": ["A\\r", "B\\r"]})
    transport = Capture(fail=b"A", error=ValueError("bad template"))
    driver = _driver(definition, transport)
    seen: list[tuple[str, Any]] = []

    async def record(event: str, payload: Any) -> None:
        seen.append((event, payload))

    driver.events.on("device.error.*", record)
    await driver.poll()
    assert transport.sent == [b"B\r"]
    assert seen == [("device.error.dev1", {"device_id": "dev1", "error": "bad template"})]


async def test_a_device_error_that_cannot_be_reported_is_logged_and_the_poll_goes_on(caplog) -> None:
    definition = _with(LINE_DEVICE, polling={"queries": ["A\\r", "B\\r"]})
    transport = Capture(fail=b"A", error=ValueError("bad template"))
    driver = _driver(definition, transport)

    async def broken_emit(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("bus down")

    driver.events.emit = broken_emit
    with caplog.at_level(logging.ERROR):
        await driver.poll()
    assert transport.sent == [b"B\r"]
    assert "[dev1] Failed to emit device.error" in caplog.text


# ── Entries the runtime cannot read ──


async def test_a_when_that_names_no_field_skips_its_entry_with_a_warning(caplog) -> None:
    definition = _with(LINE_DEVICE, polling={"queries": [{"send": "X\\r", "when": 5}, "Y\\r"]})
    transport = Capture()
    driver = _driver(definition, transport)
    with caplog.at_level(logging.WARNING):
        await driver.poll()
    assert transport.sent == [b"Y\r"]
    assert "[dev1] query 'when' must name a config field; skipping entry" in caplog.text


@pytest.mark.parametrize("entry,message", [
    ({"each_child": "input"}, "each_child query needs 'each_child' + 'send'; skipping"),
    ({"each_child": 3, "send": "X{child_id}"}, "each_child query needs 'each_child' + 'send'; skipping"),
    ({"send": ""}, "Unrecognized query entry"),
    (7, "Unrecognized query entry 7; skipping"),
])
async def test_an_entry_of_no_known_form_is_skipped_with_a_warning(entry, message, caplog) -> None:
    definition = _with(LINE_DEVICE, polling={"queries": [entry, "Y\\r"]})
    transport = Capture()
    driver = _driver(definition, transport)
    with caplog.at_level(logging.WARNING):
        await driver.poll()
    assert transport.sent == [b"Y\r"]
    assert f"[dev1] {message}" in caplog.text


def test_a_shut_gate_names_only_the_variables_holding_it_shut() -> None:
    driver = _driver(LINE_DEVICE, Capture())
    driver.set_state("standby", True)
    driver.set_state("level", 3)
    wanted, reported = driver._describe_state_gate({"standby": False, "level": [3, 4]})
    assert wanted == "Standby is false and Level is 3 or 4"
    assert reported == "Standby true"


# ── A gate judged again just before its line goes ──

CHILDREN = {"input": {"label": "Input", "id_format": {"type": "integer"}, "state_variables": {}}}


async def test_an_osc_start_up_item_is_held_when_its_gate_shuts_while_it_waits() -> None:
    definition = _with(OSC_DEVICE, on_connect=[
        {"send": "/meters", "when_state": {"standby": False}},
        "/after",
    ])
    transport = OscCapture()
    driver = _driver(definition, transport)
    transport.on_idle = lambda: driver.set_state("standby", True)
    await driver._run_on_connect()
    assert transport.sent[:1] == [_osc("/after")]
    assert all(b"/meters" not in m for m in transport.sent)


async def test_a_start_up_step_held_for_one_child_still_sends_the_next() -> None:
    definition = _with(
        LINE_DEVICE,
        on_connect=[{"each_child": "input", "send": "M{child_id}\\r", "when_state": {"standby": False}}],
        child_entity_types=CHILDREN,
    )
    transport = Capture()
    driver = _driver(definition, transport)
    for local_id in (1, 2, 3):
        driver.register_child("input", local_id)
    flips = iter([None, True, False])
    transport.on_idle = lambda: driver.set_state("standby", next(flips))
    await driver._run_on_connect()
    assert transport.sent == [b"M1\r", b"M3\r"]


async def test_an_osc_poll_skips_the_rest_of_an_entry_its_gate_shuts_and_goes_on() -> None:
    definition = _with(
        OSC_DEVICE,
        polling={"queries": [
            {"each_child": "input", "send": "/in/{child_id}", "when_state": {"standby": False}},
            "/after",
        ]},
        child_entity_types=CHILDREN,
    )
    transport = OscCapture()
    driver = _driver(definition, transport)
    driver.register_child("input", 1)
    driver.register_child("input", 2)

    async def reply(data: bytes) -> None:
        if b"/in/1" in data:
            driver.set_state("standby", True)

    transport.on_send = reply
    await driver.poll()
    assert transport.sent == [_osc("/in/1"), _osc("/after")]


@pytest.mark.parametrize("definition", [LINE_DEVICE, OSC_DEVICE])
async def test_a_poll_its_gate_stopped_reads_none_of_its_remaining_entries(definition, caplog) -> None:
    first, second = ("SLEEP\\r", "NEXT\\r") if definition is LINE_DEVICE else ("/sleep", "/next")
    polled = _with(definition, polling={
        "queries": [first, second, {"send": "LATER", "when": 5}],
        "when_state": {"standby": False},
    })
    transport = Capture() if definition is LINE_DEVICE else OscCapture()
    driver = _driver(polled, transport)

    async def reply(data: bytes) -> None:
        driver.set_state("standby", True)

    transport.on_send = reply
    with caplog.at_level(logging.WARNING):
        await driver.poll()
    assert len(transport.sent) == 1
    assert "must name a config field" not in caplog.text


# ── The when_state rule itself ──


def test_a_gate_compares_in_the_variables_declared_type() -> None:
    driver = _driver(LINE_DEVICE, Capture())
    driver.set_state("level", 3)
    driver.set_state("standby", False)
    assert driver._state_gate_open({"level": "3", "standby": "false"})
    assert driver._state_gate_open({"level": ["2", 3.0]})
    assert not driver._state_gate_open({"level": "4"})


async def test_a_quiet_judge_leaves_the_polls_paused_bookkeeping_alone(caplog) -> None:
    polling = {"queries": [], "when_state": {"standby": False}}
    driver = _driver(_with(LINE_DEVICE, polling=polling), Capture())
    driver.set_state("standby", True)
    with caplog.at_level(logging.INFO):
        assert await driver._line_gate_shut(polling, None, quiet=True) == "poll"
        assert await driver._liveness_held() is True
    assert driver._poll_waits_on_device() is False
    assert "Polling paused" not in caplog.text
    assert await driver._line_gate_shut(polling, None) == "poll"
    assert driver._poll_waits_on_device() is True


# ── The HTTP credential's tally around a poll ──


class HttpAnswers:
    """An HTTP transport stand-in: ``get`` answers each path with a status,
    or raises."""

    connected = True

    def __init__(self, answers: dict[str, Any]) -> None:
        self.answers = answers
        self.asked: list[str] = []

    async def get(self, path: str) -> Any:
        self.asked.append(path)
        answer = self.answers[path]
        if isinstance(answer, BaseException):
            raise answer
        from types import SimpleNamespace

        return SimpleNamespace(status_code=answer, text="")


HTTP_DEVICE = _with(LINE_DEVICE, id="acme_http_unit", transport="http", delimiter="")


async def test_a_failed_http_poll_line_ends_the_tally() -> None:
    polled = _with(HTTP_DEVICE, polling={"queries": ["/a", "/b"]})
    transport = HttpAnswers({"/a": 200, "/b": ConnectionError("gone")})
    driver = _driver(polled, transport)
    with pytest.raises(ConnectionError):
        await driver.poll()
    assert transport.asked == ["/a", "/b"]
    assert driver._http_cycle is None


async def test_an_answer_with_no_status_is_not_weighed() -> None:
    driver = _driver(HTTP_DEVICE, HttpAnswers({}))
    driver._http_cycle = [0, 0]
    driver._judge_http_answer("/a", object())
    driver._judge_http_answer("/b", None)
    assert driver._http_answered == set()
    assert driver._http_refused_unanswered == set()
    assert driver._http_cycle == [0, 0]


async def test_a_yaml_drivers_poll_notes_when_it_started() -> None:
    """BaseDriver notes the start of a poll() the driver class defines; the
    poll loop reads the note after a connect."""
    definition = _with(LINE_DEVICE, polling={"queries": ["Q\\r"]})
    cls = create_configurable_driver_class(definition)
    assert getattr(cls.poll, "_notes_poll_start", False) is True
    driver = _driver(definition, Capture())
    assert driver._last_poll_started_at is None
    await driver.poll()
    assert isinstance(driver._last_poll_started_at, float)


# ── What the review found unwatched ──

FRAMED = {"type": "length_prefix", "header": "AB", "length_size": 1}


async def test_a_raw_byte_stream_line_goes_in_the_send_frame_but_a_udp_one_does_not() -> None:
    tcp = _with(LINE_DEVICE, send_frame=FRAMED, on_connect=["HI\\r"], polling={"queries": ["Q\\r"]})
    transport = Capture()
    driver = _driver(tcp, transport)
    await driver._run_on_connect()
    await driver.poll()
    assert transport.sent == [b"AB\x03HI\r", b"AB\x02Q\r"]

    udp = _with(tcp, id="acme_udp_unit", transport="udp")
    transport = Capture()
    driver = _driver(udp, transport)
    await driver.poll()
    assert transport.sent == [b"Q\r"]


async def test_an_httpx_failure_ends_the_poll_and_the_tally(caplog) -> None:
    import httpx

    polled = _with(HTTP_DEVICE, polling={"queries": ["/a", "/b"]})
    transport = HttpAnswers({"/a": 200, "/b": httpx.ReadTimeout("slow")})
    driver = _driver(polled, transport)
    with caplog.at_level(logging.WARNING), pytest.raises(httpx.ReadTimeout):
        await driver.poll()
    assert driver._http_cycle is None
    assert "[dev1] Poll query failed (HTTP): slow" in caplog.text


def test_the_connect_check_judges_the_polls_gate_quietly(caplog) -> None:
    polling = {"queries": ["Q\\r"], "when_state": {"standby": False}}
    driver = _driver(_with(LINE_DEVICE, polling=polling), Capture())
    driver.set_state("standby", True)
    with caplog.at_level(logging.INFO):
        assert driver._connect_check_held() is True
    assert driver._poll_waits_on_device() is False
    assert "Polling paused" not in caplog.text
    assert (
        "[dev1] Connect check skipped: it polls only while Standby is false, "
        "and the device reports Standby true"
    ) in caplog.text


def _answer(status: int) -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(status_code=status, text="")


def test_the_credential_rules_say_why_they_drop_the_connection(caplog) -> None:
    driver = _driver(HTTP_DEVICE, HttpAnswers({}))
    with caplog.at_level(logging.DEBUG, logger="openavc.drivers.configurable"):
        driver._judge_http_answer("/a", _answer(200))
        driver._judge_http_answer("/locked", _answer(403))
        with pytest.raises(ConnectionFaultError) as stopped:
            driver._judge_http_answer("/a", _answer(401))
    assert stopped.value.fault_code == "auth_failed"
    assert str(stopped.value) == (
        "The device stopped accepting the username and password. "
        "Check them in the device settings."
    )
    records = [(r.name, r.levelname, r.getMessage()) for r in caplog.records
               if r.name == "openavc.drivers.configurable"]
    assert ("openavc.drivers.configurable", "DEBUG",
            "[dev1] '/locked' refused (HTTP 403); other queries answer, so the "
            "account may not read it") in records
    assert ("openavc.drivers.configurable", "WARNING",
            "[dev1] '/a' answered earlier on this connection and is now refused "
            "(HTTP 401); dropping the connection") in records

    caplog.clear()
    fresh = _driver(HTTP_DEVICE, HttpAnswers({}))
    with caplog.at_level(logging.WARNING, logger="openavc.drivers.configurable"):
        fresh._judge_http_answer("/a", _answer(401))
        fresh._judge_http_answer("/b", _answer(407))
        with pytest.raises(ConnectionFaultError) as refused:
            fresh._judge_http_answer("/c", _answer(403))
    assert str(refused.value) == (
        "The device refused the username and password. Check them in the device settings."
    )
    assert [(r.levelname, r.getMessage()) for r in caplog.records
            if r.name == "openavc.drivers.configurable"] == [
        ("WARNING", "[dev1] 3 queries refused and none answered; dropping the connection"),
    ]


async def test_the_moved_lines_log_under_the_yaml_runtimes_name(caplog) -> None:
    definition = _with(
        LINE_DEVICE,
        polling={"queries": ["A\\r"], "when_state": {"standby": False}},
        device_settings={"gain": {"write": {"send": "GAIN {value}\\r"}}},
    )
    driver = _driver(definition, Capture())
    with caplog.at_level(logging.DEBUG):
        await driver.set_device_setting("gain", 3)
        driver.set_state("standby", True)
        await driver.poll()
    mine = {(r.name, r.levelname, r.getMessage()) for r in caplog.records}
    assert ("openavc.drivers.configurable", "DEBUG",
            "[dev1] Set device setting 'gain' = 3") in mine
    assert ("openavc.drivers.configurable", "INFO",
            "[dev1] Polling paused: it polls only while Standby is false, and the "
            "device reports Standby true") in mine


async def test_a_poll_line_that_fails_to_build_is_logged_with_its_traceback(caplog) -> None:
    definition = _with(LINE_DEVICE, polling={"queries": ["A\\r"]})
    driver = _driver(definition, Capture(fail=b"A", error=ValueError("bad template")))
    with caplog.at_level(logging.ERROR):
        await driver.poll()
    (record,) = [r for r in caplog.records if r.getMessage() == "[dev1] Poll query error"]
    assert record.name == "openavc.drivers.configurable"
    assert record.levelname == "ERROR" and record.exc_info is not None
