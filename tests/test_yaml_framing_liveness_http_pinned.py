"""A YAML driver's send-side framing, its ``liveness:`` block, an HTTP
command's request, a command parameter's wire value, a ``udp:`` block's port
and the command list the IDE shows: what each one does today, pinned.

* Send framing on every origin: a file command takes ``command_prefix`` /
  ``command_suffix`` (the device's config first) unless it says ``raw: true``
  or the device authored it; a raw line, the liveness probe and a setting's
  ``send`` line take only the ``send_frame`` header, and a raw line not on
  UDP while the other two take it there too; a ``udp:`` side-send takes none.
* The ``liveness:`` block: when it switches the watchdog on, the numbers it
  sets on the driver, what a bad block leaves behind, and the probe's two
  forms (OSC, a raw line).
* An HTTP command's request: method, path, body as JSON or raw content,
  ``query_params``, headers, the platform's push values, and which
  placeholders are warned about, in order.
* A parameter's ``map:`` / ``scale`` / ``offset`` on the way to the wire, and
  which dict comes back.
* A ``udp:`` block's port: blank, a placeholder, and each refusal.

Called straight on the driver with a capturing transport and pacing off, so
nothing here waits on a socket. Invented devices ("acme_*") and synthetic
payloads throughout.
"""

from __future__ import annotations

import asyncio
import copy
import logging
from typing import Any

import pytest

from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.configurable import (
    ConfigurableDriver,
    create_configurable_driver_class,
)
from openavc.drivers.liveness import LivenessWatch
from openavc.drivers.yaml_driver_info import build_commands_meta as _build_commands_meta
from openavc.transport.http_client import HTTPClientTransport, HTTPResponse
from openavc.transport.osc_codec import osc_encode_message

LOGGER = "openavc.drivers.configurable"

# A one-byte length field after a two-byte header: header + len + data.
FRAME = {"type": "length_prefix", "header": "HD", "length_size": 1}

UNIT: dict[str, Any] = {
    "id": "acme_frame_unit",
    "name": "Acme Frame Unit",
    "manufacturer": "Acme",
    "category": "video",
    "transport": "tcp",
    "delimiter": "\r",
    "default_config": {"host": "", "port": 23},
    "state_variables": {
        "level": {"type": "integer", "label": "Level"},
    },
    "commands": {
        "power": {
            "label": "Power",
            "send": "PWR{v}",
            "params": {"v": {"type": "integer", "label": "V"}},
        },
        "bare": {"label": "Bare", "send": "BARE", "raw": True},
        "silent": {"label": "Silent"},
    },
    "responses": [
        {"match": r"^LEVEL=(\d+)$", "set": {"level": "$1"}},
    ],
}


class Capture:
    """A transport that records what is sent."""

    def __init__(self) -> None:
        self.connected = True
        self.sent: list[bytes] = []

    async def wait_idle(self) -> None:
        return None

    async def send(self, data: bytes) -> None:
        self.sent.append(data)


class Http(HTTPClientTransport):
    """An HTTP transport that records each request and answers ``text``."""

    connected = True

    def __init__(self, text: str = "") -> None:
        self.requests: list[Any] = []
        self.text = text

    async def request(self, method: str, path: str, **kw: Any) -> HTTPResponse:
        self.requests.append((method, path, kw))
        return HTTPResponse(status_code=200, text=self.text, headers={})


def _with(definition: dict[str, Any], **extra: Any) -> dict[str, Any]:
    out = copy.deepcopy(definition)
    out.update(extra)
    return out


def _driver(definition: dict[str, Any], link: Any = None, **config: Any):
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    cls = create_configurable_driver_class(definition)
    base = {"host": "127.0.0.1", "port": 9000, **definition.get("default_config", {})}
    driver = cls("dev1", {**base, **config}, state, events)
    driver.transport = link
    driver._pace_lines = False
    return driver


# ── a file command's framing (build_wire) ──


def test_a_file_command_takes_the_definitions_prefix_and_suffix() -> None:
    drv = _driver(_with(UNIT, command_prefix="<", command_suffix=">"))
    assert drv.build_wire("power", {"v": 1}) == b"<PWR1>"


def test_the_devices_config_prefix_and_suffix_win_over_the_definitions() -> None:
    drv = _driver(
        _with(UNIT, command_prefix="<", command_suffix=">"),
        command_prefix="[", command_suffix="]",
    )
    assert drv.build_wire("power", {"v": 1}) == b"[PWR1]"


def test_a_prefix_alone_or_a_suffix_alone_still_frames() -> None:
    assert _driver(_with(UNIT, command_prefix="<")).build_wire("power", {"v": 2}) == b"<PWR2"
    assert _driver(_with(UNIT, command_suffix=">")).build_wire("power", {"v": 2}) == b"PWR2>"


def test_a_raw_command_goes_as_written() -> None:
    drv = _driver(_with(UNIT, command_prefix="<", command_suffix=">"))
    assert drv.build_wire("bare") == b"BARE"


def test_a_command_the_device_authored_is_framed_once_at_the_merge() -> None:
    drv = _driver(
        _with(UNIT, command_prefix="<", command_suffix=">"),
        commands={"mine": {"send": "MINE{v}"}},
    )
    assert drv._definition["commands"]["mine"]["send"] == "<MINE{v}>"
    assert drv.build_wire("mine", {"v": 4}) == b"<MINE4>"
    # The file's own command still takes its framing at send time.
    assert drv.build_wire("power", {"v": 4}) == b"<PWR4>"


def test_a_device_authored_command_ends_with_the_delimiter_when_no_suffix_is_set() -> None:
    drv = _driver(_with(UNIT, command_prefix="<"), commands={"mine": "MINE"})
    assert drv._definition["commands"]["mine"]["send"] == "<MINE\r"
    # A file command never falls back to the delimiter.
    assert drv.build_wire("power", {"v": 1}) == b"<PWR1"


def test_a_file_command_goes_inside_the_send_frame_header_after_its_framing() -> None:
    drv = _driver(_with(UNIT, command_suffix="\\r", send_frame=FRAME))
    assert drv.build_wire("power", {"v": 1}) == b"HD\x05PWR1\r"


def test_a_send_that_is_not_text_is_refused_unless_a_prefix_or_suffix_frames_it() -> None:
    unit = _with(UNIT)
    unit["commands"]["num"] = {"label": "Num", "send": 123}
    unit["commands"]["flag"] = {"label": "Flag", "send": True, "raw": True}
    with pytest.raises(TypeError):
        _driver(unit).build_wire("num")
    framed = _driver(_with(unit, command_prefix="<"))
    assert framed.build_wire("num") == b"<123"
    with pytest.raises(TypeError):
        framed.build_wire("flag")


def test_an_unknown_command_or_one_with_no_send_string_builds_nothing(caplog) -> None:
    drv = _driver(UNIT)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        assert drv.build_wire("nope") is None
        assert drv.build_wire("silent") is None
    assert [r.getMessage() for r in caplog.records if r.name == LOGGER] == [
        "[dev1] Command 'silent' has no send string",
    ]


# ── the send_frame header on every other origin ──


def test_a_raw_line_takes_the_header_except_on_udp() -> None:
    drv = _driver(_with(UNIT, command_prefix="<", command_suffix=">", send_frame=FRAME))
    assert drv._raw_bytes("tcp", "Q\\r") == b"HD\x02Q\r"
    assert drv._raw_bytes("serial", "Q") == b"HD\x01Q"
    assert drv._raw_bytes("udp", "Q\\r") == b"Q\r"


async def test_a_setting_line_takes_the_header_on_udp_too() -> None:
    unit = _with(
        UNIT, transport="udp", command_prefix="<", send_frame=FRAME,
        device_settings={"lvl": {"label": "Lvl", "write": {"send": "L{value}\\r"}}},
    )
    link = Capture()
    drv = _driver(unit, link)
    assert await drv.set_device_setting("lvl", 7) is True
    assert link.sent == [b"HD\x03L7\r"]


async def test_a_udp_side_send_takes_no_framing_and_no_header() -> None:
    unit = _with(UNIT, command_prefix="<", command_suffix=">", send_frame=FRAME)
    unit["commands"]["ping"] = {
        "label": "Ping", "udp": {"payload": "P{v}\\r", "port": 9100},
        "params": {"v": {"type": "integer", "label": "V"}},
    }
    drv = _driver(unit, Capture())
    sent: list[Any] = []

    async def send_udp(data: bytes, **kw: Any) -> None:
        sent.append((data, kw))

    drv.send_udp = send_udp
    assert await drv.send_command("ping", {"v": 3}) is True
    assert sent == [(b"P3\r", {"host": None, "port": 9100, "broadcast": False})]


async def test_an_empty_setting_body_sends_no_body() -> None:
    unit = _with(
        UNIT, transport="http",
        device_settings={"lvl": {"label": "Lvl", "write": {"method": "put", "path": "/l", "body": ""}}},
    )
    http = Http(text="LEVEL=5")
    drv = _driver(unit, http)
    response = await drv.set_device_setting("lvl", 5)
    assert response.text == "LEVEL=5"
    assert http.requests == [("PUT", "/l", {"json_body": None, "headers": None})]
    assert drv.get_state("level") == 5


# ── the liveness: block ──


def test_no_block_leaves_the_watchdog_off_and_the_class_numbers() -> None:
    drv = _driver(UNIT)
    assert drv._health_enabled() is False
    assert drv._liveness_def is None
    for name in ("HEALTH_INTERVAL_S", "HEALTH_TIMEOUT_S", "HEALTH_MAX_FAILURES"):
        assert name not in drv.__dict__
    assert drv.HEALTH_INTERVAL_S == LivenessWatch.HEALTH_INTERVAL_S


def test_a_block_sets_its_numbers_on_the_driver_as_written_types() -> None:
    block = {"send": "PING\\r", "expect": "^PONG", "interval": "12", "timeout": 3, "max_failures": "4"}
    drv = _driver(_with(UNIT, liveness=block))
    assert drv._health_enabled() is True
    assert drv._liveness_def is drv._definition["liveness"]
    assert drv._liveness_expect.pattern == "^PONG"
    assert (drv.HEALTH_INTERVAL_S, drv.HEALTH_TIMEOUT_S, drv.HEALTH_MAX_FAILURES) == (12.0, 3.0, 4)
    assert type(drv.HEALTH_INTERVAL_S) is float and type(drv.HEALTH_MAX_FAILURES) is int


def test_a_block_with_no_numbers_takes_the_defaults_and_no_expect() -> None:
    drv = _driver(_with(UNIT, liveness={"send": "PING"}))
    assert drv._health_enabled() is True
    assert drv._liveness_expect is None
    assert (drv.HEALTH_INTERVAL_S, drv.HEALTH_TIMEOUT_S, drv.HEALTH_MAX_FAILURES) == (30.0, 5.0, 2)
    assert "HEALTH_INTERVAL_S" in drv.__dict__


@pytest.mark.parametrize("block", [
    "PING", {"send": ""}, {"send": 5}, {"expect": "x"}, {"send": None},
])
def test_a_block_with_nothing_to_send_is_off_and_says_nothing(block, caplog) -> None:
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        drv = _driver(_with(UNIT, liveness=block))
    assert drv._health_enabled() is False
    assert drv._liveness_expect is None
    assert "HEALTH_INTERVAL_S" not in drv.__dict__
    assert not [r for r in caplog.records if r.name == LOGGER]


def test_a_bad_expect_turns_the_watchdog_off_and_says_why(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        drv = _driver(_with(UNIT, liveness={"send": "PING", "expect": "(", "interval": 9}))
    assert drv._health_enabled() is False
    assert drv._liveness_expect is None
    assert "HEALTH_INTERVAL_S" not in drv.__dict__
    messages = [r.getMessage() for r in caplog.records if r.name == LOGGER]
    assert len(messages) == 1
    assert messages[0].startswith("[dev1] Invalid liveness block — watchdog disabled: ")


def test_a_bad_number_keeps_the_numbers_before_it_and_drops_the_expect(caplog) -> None:
    block = {"send": "PING", "expect": "PONG", "interval": 9, "timeout": "soon"}
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        drv = _driver(_with(UNIT, liveness=block))
    assert drv._health_enabled() is False
    assert drv._liveness_expect is None
    assert drv.__dict__["HEALTH_INTERVAL_S"] == 9.0
    assert "HEALTH_TIMEOUT_S" not in drv.__dict__
    assert "Invalid liveness block" in caplog.text


def test_a_bad_max_failures_keeps_interval_and_timeout() -> None:
    block = {"send": "PING", "interval": 9, "timeout": 2, "max_failures": [1]}
    drv = _driver(_with(UNIT, liveness=block))
    assert drv._health_enabled() is False
    assert (drv.__dict__["HEALTH_INTERVAL_S"], drv.__dict__["HEALTH_TIMEOUT_S"]) == (9.0, 2.0)
    assert "HEALTH_MAX_FAILURES" not in drv.__dict__


async def test_the_probe_is_a_raw_line_with_config_and_the_header_but_no_framing() -> None:
    unit = _with(
        UNIT, transport="udp", command_prefix="<", command_suffix=">",
        send_frame=FRAME, liveness={"send": "P{port}\\r"},
    )
    link = Capture()
    drv = _driver(unit, link)
    await drv._send_liveness_probe(drv._liveness_def)
    assert link.sent == [b"HD\x04P23\r"]


async def test_the_probe_with_no_placeholder_goes_as_written() -> None:
    link = Capture()
    drv = _driver(_with(UNIT, liveness={"send": "P\\x01{"}), link)
    drv.config["x"] = "unused"
    await drv._send_liveness_probe({"send": "P\\x01"})
    assert link.sent == [b"P\x01"]


async def test_an_osc_probe_is_its_address_and_args_with_config_filled() -> None:
    unit = _with(
        UNIT, transport="osc", send_frame=FRAME,
        liveness={"send": "/acme/{room}/ping", "args": [{"type": "s", "value": "{room}"}]},
    )
    link = Capture()
    drv = _driver(unit, link, room="r1")
    await drv._send_liveness_probe(drv._liveness_def)
    assert link.sent == [osc_encode_message("/acme/r1/ping", drv._build_osc_args(
        [{"type": "s", "value": "{room}"}], drv.config,
    ))]
    assert link.sent[0].startswith(b"/acme/r1/ping")


async def test_an_osc_probe_with_no_args_sends_the_bare_address() -> None:
    link = Capture()
    drv = _driver(_with(UNIT, transport="osc", liveness={"send": "/ping"}), link)
    await drv._send_liveness_probe(drv._liveness_def)
    assert link.sent == [osc_encode_message("/ping", [])]


async def test_the_probe_sends_nothing_with_no_block_or_no_transport() -> None:
    drv = _driver(UNIT, Capture())
    assert await drv._liveness_probe() is None
    assert drv.transport.sent == []
    drv = _driver(_with(UNIT, liveness={"send": "PING"}), None)
    assert await drv._liveness_probe() is None
    assert drv._liveness_waiter is None


async def test_the_probe_waits_for_a_reply_that_matches_expect_and_clears_its_waiter() -> None:
    link = Capture()
    drv = _driver(_with(UNIT, liveness={"send": "PING", "expect": "^PONG"}), link)
    probe = asyncio.ensure_future(drv._liveness_probe())
    await asyncio.sleep(0)
    assert link.sent == [b"PING"]
    waiter = drv._liveness_waiter
    await drv.on_data_received(b"LEVEL=3")
    assert not waiter.done()
    await drv.on_data_received(b"PONG ok")
    await probe
    assert waiter.done()
    assert drv._liveness_waiter is None
    # The reply still went to the reply rules.
    assert drv.get_state("level") == 3


async def test_expect_is_searched_anywhere_in_the_reply() -> None:
    drv = _driver(_with(UNIT, liveness={"send": "PING", "expect": "PONG"}), Capture())
    probe = asyncio.ensure_future(drv._liveness_probe())
    await asyncio.sleep(0)
    await drv.on_data_received(b"OK PONG")
    await asyncio.wait_for(probe, 1)
    assert drv._liveness_waiter is None


async def test_any_reply_answers_a_probe_with_no_expect() -> None:
    link = Capture()
    drv = _driver(_with(UNIT, liveness={"send": "PING"}), link)
    probe = asyncio.ensure_future(drv._liveness_probe())
    await asyncio.sleep(0)
    await drv.on_data_received(b"\xff\xfe")
    await asyncio.wait_for(probe, 1)
    assert drv._liveness_waiter is None


async def test_a_cancelled_probe_clears_its_waiter_and_a_later_reply_is_harmless() -> None:
    drv = _driver(_with(UNIT, liveness={"send": "PING"}), Capture())
    probe = asyncio.ensure_future(drv._liveness_probe())
    await asyncio.sleep(0)
    probe.cancel()
    with pytest.raises(asyncio.CancelledError):
        await probe
    assert drv._liveness_waiter is None
    await drv.on_data_received(b"PONG")


def test_a_done_waiter_is_left_alone() -> None:
    drv = _driver(_with(UNIT, liveness={"send": "PING"}))
    loop = asyncio.new_event_loop()
    try:
        waiter = loop.create_future()
        waiter.set_result("first")
        drv._liveness_waiter = waiter
        drv._liveness_note_data(b"PONG")
        assert waiter.result() == "first"
    finally:
        loop.close()


def test_a_newer_probes_waiter_is_not_cleared_by_an_older_probe() -> None:
    async def run() -> None:
        drv = _driver(_with(UNIT, liveness={"send": "PING"}), Capture())
        first = asyncio.ensure_future(drv._liveness_probe())
        await asyncio.sleep(0)
        newer = asyncio.get_running_loop().create_future()
        drv._liveness_waiter = newer
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert drv._liveness_waiter is newer

    asyncio.run(run())


# ── a parameter's wire value ──

wire = ConfigurableDriver._apply_param_wire_maps


def test_a_mapped_param_goes_as_its_word_in_a_new_dict() -> None:
    given = {"a": 1, "b": 2}
    out = wire({"a": {"map": {"1": "X"}}}, given)
    assert out == {"a": "X", "b": 2}
    assert list(out) == ["a", "b"]
    assert given == {"a": 1, "b": 2}


def test_scale_and_offset_turn_the_real_value_into_the_devices_number() -> None:
    assert wire({"a": {"scale": 0.5}}, {"a": 10}) == {"a": 20}
    assert wire({"a": {"offset": 5}}, {"a": 10}) == {"a": 5}


def test_a_scaled_param_comes_back_in_a_new_dict_and_the_given_one_is_untouched() -> None:
    given = {"a": 10}
    out = wire({"a": {"scale": 0.5}}, given)
    assert out is not given
    assert given == {"a": 10}


def test_a_map_hit_wins_over_scale_and_a_miss_falls_to_it() -> None:
    defs = {"a": {"map": {"1": "X"}, "scale": 2}}
    assert wire(defs, {"a": 1}) == {"a": "X"}
    assert wire(defs, {"a": 4}) == {"a": 2}


def test_nothing_to_translate_returns_the_same_dict() -> None:
    given = {"a": 1, "b": "x"}
    assert wire({"a": {"type": "integer"}}, given) is given
    assert wire({"c": {"map": {"1": "X"}}}, given) is given
    assert wire({"a": "not a dict"}, given) is given
    assert wire("not a dict", given) is given
    empty: dict[str, Any] = {}
    assert wire({"a": {"map": {"1": "X"}}}, empty) is empty


def test_none_is_left_alone_without_scale_even_with_unknown_declared() -> None:
    given = {"a": None}
    assert wire({"a": {"unknown": [255]}}, given) is given
    assert wire({"a": {"unknown": [255], "scale": 1}}, {"a": None}) == {"a": 255}


def test_only_the_translated_params_change_and_the_rest_keep_their_order() -> None:
    out = wire(
        {"b": {"map": {"2": "TWO"}}, "c": {"scale": 10}},
        {"a": 1, "b": 2, "c": 30, "d": 4},
    )
    assert list(out.items()) == [("a", 1), ("b", "TWO"), ("c", 3), ("d", 4)]


# ── a udp: block's port ──


def _ports() -> Any:
    return _driver(UNIT)._resolve_udp_port


@pytest.mark.parametrize("raw", [None, ""])
def test_a_blank_port_is_the_default(raw) -> None:
    assert _ports()("w", raw, {}, default=9) == 9


@pytest.mark.parametrize("raw", [None, ""])
def test_a_blank_port_with_no_default_is_refused(raw) -> None:
    with pytest.raises(ValueError) as err:
        _ports()("w", raw, {})
    assert str(err.value) == "[dev1] w: no port to send to"


@pytest.mark.parametrize("raw, params, port", [
    (5000, {}, 5000), ("{p}", {"p": " 7000 "}, 7000), (" 1 ", {}, 1), (65535, {}, 65535),
])
def test_a_port_is_a_number_or_a_placeholder(raw, params, port) -> None:
    assert _ports()("w", raw, params, default=9) == port


@pytest.mark.parametrize("raw, message, parse_failed", [
    ("abc", "[dev1] w: port 'abc' is not a number", True),
    ("9.0", "[dev1] w: port '9.0' is not a number", True),
    (False, "[dev1] w: port 'False' is not a number", True),
    ("{nope}", "[dev1] w: port '{nope}' is not a number", True),
    (0, "[dev1] w: port 0 is not between 1 and 65535", False),
    ("65536", "[dev1] w: port 65536 is not between 1 and 65535", False),
    (-1, "[dev1] w: port -1 is not between 1 and 65535", False),
])
def test_a_port_that_is_not_one_is_refused(raw, message, parse_failed) -> None:
    with pytest.raises(ValueError) as err:
        _ports()("w", raw, {}, default=9)
    assert str(err.value) == message
    assert err.value.__cause__ is None
    # The parse failure is raised from None: no int() traceback beneath it.
    assert err.value.__suppress_context__ is parse_failed
    assert err.value.__context__ is None or parse_failed


def test_an_unfilled_port_placeholder_is_warned_about_under_its_place(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger=LOGGER), pytest.raises(ValueError):
        _ports()("commands.x.udp", "{nope}", {})
    assert "commands.x.udp.port: '{nope}' was not substituted" in caplog.text


# ── an HTTP command's request ──


def _http_unit(**cmd: Any) -> dict[str, Any]:
    unit = _with(UNIT, transport="http")
    unit["commands"]["h"] = {"label": "H", **cmd}
    return unit


async def test_an_http_command_defaults_to_get_on_the_root() -> None:
    http = Http()
    drv = _driver(_http_unit(method="get"), http)
    response = await drv.send_command("h")
    assert response.status_code == 200
    assert http.requests == [
        ("GET", "/", {"params": None, "json_body": None, "content": None, "headers": None}),
    ]


async def test_a_path_alone_is_a_get() -> None:
    http = Http()
    drv = _driver(_http_unit(path="/a/{port}"), http)
    await drv.send_command("h")
    assert http.requests[0][:2] == ("GET", "/a/23")


async def test_a_body_that_is_not_json_goes_as_raw_content(caplog) -> None:
    http = Http()
    drv = _driver(_http_unit(method="put", path="/x", body="<v>{port}</v>"), http)
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        await drv.send_command("h")
    assert http.requests == [
        ("PUT", "/x", {"params": None, "json_body": None, "content": b"<v>23</v>", "headers": None}),
    ]
    assert "[dev1] Body for 'h' is not JSON, sending as raw content" in caplog.text


async def test_a_json_body_goes_as_json_and_an_empty_one_as_none() -> None:
    http = Http()
    drv = _driver(_http_unit(method="post", path="/x", body='{"p": {port}}'), http)
    await drv.send_command("h")
    assert http.requests[0][2]["json_body"] == {"p": 23}
    http2 = Http()
    drv2 = _driver(_http_unit(method="post", path="/x", body=""), http2)
    await drv2.send_command("h")
    assert http2.requests[0][2]["json_body"] is None
    assert http2.requests[0][2]["content"] is None


async def test_headers_are_filled_and_made_text() -> None:
    http = Http()
    drv = _driver(
        _http_unit(path="/x", headers={"Content-Type": "text/xml", "X-N": 5, "X-T": "{port}"}),
        http,
    )
    await drv.send_command("h")
    assert http.requests[0][2]["headers"] == {"Content-Type": "text/xml", "X-N": "5", "X-T": "23"}


@pytest.mark.parametrize("query", [{}, ["a"], "a=1"])
async def test_query_params_that_are_not_a_filled_dict_send_none(query) -> None:
    http = Http()
    drv = _driver(_http_unit(path="/x", query_params=query), http)
    await drv.send_command("h")
    assert http.requests[0][2]["params"] is None


async def test_query_params_fill_text_and_keep_other_values() -> None:
    http = Http()
    drv = _driver(_http_unit(path="/x", query_params={"a": "{port}", "b": 2, "c": None}), http)
    await drv.send_command("h")
    assert http.requests[0][2]["params"] == {"a": "23", "b": 2, "c": None}


async def test_the_push_values_fill_an_http_command() -> None:
    http = Http()
    drv = _driver(_http_unit(path="/reg", body='{"u": "{push_callback_url}", "s": "{push_session}"}'), http)
    drv._push_callback_url = "http://cb"
    drv._push_session = "S1"
    await drv.send_command("h")
    assert http.requests[0][2]["json_body"] == {"u": "http://cb", "s": "S1"}


async def test_the_answer_is_read_by_the_reply_rules_and_returned() -> None:
    http = Http(text="LEVEL=42")
    drv = _driver(_http_unit(path="/x"), http)
    response = await drv.send_command("h")
    assert isinstance(response, HTTPResponse) and response.text == "LEVEL=42"
    assert drv.get_state("level") == 42


async def test_unfilled_placeholders_are_warned_about_path_then_body_never_query_or_headers(caplog) -> None:
    http = Http()
    drv = _driver(
        _http_unit(
            path="/x/{p1}", body="{b1}", query_params={"q": "{q1}"}, headers={"H": "{h1}"},
        ),
        http,
    )
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        await drv.send_command("h")
    warned = [r.getMessage() for r in caplog.records if r.name == LOGGER]
    assert [m.split(":")[0] for m in warned] == ["[dev1] commands.h.path", "[dev1] commands.h.body"]
    assert http.requests[0][1] == "/x/{p1}"
    assert http.requests[0][2]["params"] == {"q": "{q1}"}
    assert http.requests[0][2]["headers"] == {"H": "{h1}"}


async def test_an_http_command_on_another_transport_sends_nothing_and_warns_of_nothing_else(caplog) -> None:
    link = Capture()
    unit = _http_unit(path="/x/{p1}")
    unit["transport"] = "tcp"
    drv = _driver(unit, link)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        assert await drv.send_command("h") is None
    assert link.sent == []
    assert [r.getMessage() for r in caplog.records if r.name == LOGGER] == [
        "[dev1] Command 'h' uses HTTP fields but transport is not HTTP",
    ]


# ── the command list the IDE shows ──


def test_the_command_list_carries_its_fields_in_order_and_skips_what_is_not_a_command() -> None:
    meta = _build_commands_meta({
        "a": "not a command",
        "b": {"send": "X"},
        "c": {
            "confirm": "Sure?", "send": "Y", "query_for": ["x"], "sets": {"x": 1},
            "sleeps_device": True, "restarts_device_for": 30, "available_offline": True,
            "help": "H", "args": [1], "address": "/a", "body": "B", "path": "/p",
            "method": "POST", "label": "C", "params": {"v": {}}, "unrelated": 1,
        },
    })
    assert meta == {
        "b": {"label": "b", "params": {}},
        "c": {
            "label": "C", "params": {"v": {}}, "method": "POST", "path": "/p",
            "body": "B", "address": "/a", "args": [1], "help": "H",
            "available_offline": True, "restarts_device_for": 30,
            "sleeps_device": True, "sets": {"x": 1}, "query_for": ["x"],
            "confirm": "Sure?",
        },
    }
    assert list(meta["c"]) == [
        "label", "params", "method", "path", "body", "address", "args", "help",
        "available_offline", "restarts_device_for", "sleeps_device", "sets",
        "query_for", "confirm",
    ]


def test_no_commands_is_an_empty_list() -> None:
    assert _build_commands_meta(None) == {}
    assert _build_commands_meta({}) == {}
