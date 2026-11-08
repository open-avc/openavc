"""A YAML driver reading the device again after a change, sending a command,
and taking in a device's own protocol: what each one does today, pinned.

* A reply rule's ``then_query`` (a follow-up round): one round at a time
  per rule and child, a duplicate child read once, what a disconnect, a
  shut gate and a failed line do to the round, and the hold it leaves.
* A command's ``read_back:``: what each form sends on each transport, the
  key that says two queries are the same one, and what a failure to build
  or send one does.
* The line gap a TCP or serial link keeps.
* ``send_command``'s routes: an IR code, a UDP side-send, the connected
  gate, an unknown command, and when a read-back follows each form.
* The inline protocol a device authors in its config: where its line
  ending, prefix and suffix come from, what it adds to the poll, and the
  per-device ``DRIVER_INFO`` it leaves the class's alone for.

Called straight on the driver with a capturing transport and pacing off,
so nothing here waits on a socket. Invented devices ("acme_*") and
synthetic payloads throughout.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import time
from typing import Any

import pytest

from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.param_validation import CommandParamError
from openavc.drivers.configurable import create_configurable_driver_class
from openavc.drivers.contract_events import UNKNOWN_COMMAND
from openavc.drivers.spec import DEFAULT_LINE_GAP_S, FOLLOW_UP_SETTLE_S
from openavc.transport.http_client import HTTPResponse

LINE_UNIT: dict[str, Any] = {
    "id": "acme_line_unit",
    "name": "Acme Line Unit",
    "manufacturer": "Acme",
    "category": "video",
    "transport": "tcp",
    "delimiter": "\r",
    "default_config": {"host": "", "port": 23},
    "state_variables": {
        "standby": {"type": "boolean", "label": "Standby"},
        "level": {"type": "integer", "label": "Level"},
        "mode": {"type": "string", "label": "Mode"},
    },
    "commands": {
        "set_level": {
            "label": "Set Level",
            "send": "SL{level}\r",
            "params": {
                "level": {"type": "integer", "label": "Level", "map": {"1": "A"}},
                "fade": {"type": "integer", "label": "Fade"},
            },
            "read_back": "GL\r",
        },
        "get_level": {
            "label": "Get Level",
            "send": "GL{level}\r",
            "params": {"level": {"type": "integer", "label": "Level"}},
        },
        "hello": {"label": "Hello", "send": "HELLO\r"},
        "silent": {"label": "Silent"},
    },
    "responses": [
        {"match": r"^LEVEL=(\d+)$", "set": {"level": "$1"}},
    ],
}


class Capture:
    """A transport that records what is sent; ``fail`` names a substring of
    the bytes whose send raises."""

    def __init__(self, fail: bytes | None = None) -> None:
        self.connected = True
        self.sent: list[bytes] = []
        self.fail = fail
        self.after_send: Any = None

    async def wait_idle(self) -> None:
        return None

    async def send(self, data: bytes) -> None:
        if self.fail is not None and self.fail in data:
            raise OSError("write failed")
        self.sent.append(data)
        if self.after_send is not None:
            self.after_send()


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


async def _settle(driver: Any) -> None:
    for _ in range(5):
        tasks = list(driver._follow_up_tasks) + list(driver._read_back_tasks)
        if not tasks:
            return
        await asyncio.gather(*tasks, return_exceptions=True)


# ── follow-ups (then_query) ──


def test_a_follow_up_with_no_running_loop_schedules_nothing() -> None:
    drv = _driver(LINE_UNIT, Capture())
    drv._follow_up(object(), ["GL\r"], "", [])
    assert drv._follow_up_tasks == set()
    assert drv._follow_up_until == {}


async def test_a_child_written_twice_is_read_once() -> None:
    cap = Capture()
    drv = _driver(LINE_UNIT, cap)
    children = [("output", 1, {}), ("output", 1, {}), ("output", 2, {})]
    drv._follow_up(object(), ["GO{child_id}\r"], "", children)
    await _settle(drv)
    assert cap.sent == [b"GO1\r", b"GO2\r"]


async def test_a_round_held_by_its_settle_window_sends_nothing(caplog) -> None:
    cap = Capture()
    drv = _driver(LINE_UNIT, cap)
    rule = object()
    drv._follow_up(rule, ["GL\r"], "", [])
    await _settle(drv)
    with caplog.at_level(logging.DEBUG, logger="openavc.drivers.configurable"):
        drv._follow_up(rule, ["GL\r"], "", [])
    assert drv._follow_up_tasks == set()
    assert cap.sent == [b"GL\r"]
    assert f"went out less than {FOLLOW_UP_SETTLE_S:g} s ago" in caplog.text
    # Another child of the same rule is its own round.
    drv._follow_up(rule, ["GL\r"], "output:2", [])
    await _settle(drv)
    assert cap.sent == [b"GL\r", b"GL\r"]


async def test_a_round_that_finishes_holds_the_rule_for_the_settle_window() -> None:
    drv = _driver(LINE_UNIT, Capture())
    rule = object()
    before = time.monotonic()
    drv._follow_up(rule, ["GL\r"], "s", [])
    assert drv._follow_up_until[(id(rule), "s")] == float("inf")
    await _settle(drv)
    held = drv._follow_up_until[(id(rule), "s")]
    assert before + FOLLOW_UP_SETTLE_S <= held <= time.monotonic() + FOLLOW_UP_SETTLE_S


async def test_a_disconnect_mid_round_ends_it_and_still_holds_the_rule() -> None:
    cap = Capture()
    drv = _driver(LINE_UNIT, cap)

    def drop() -> None:
        cap.connected = False

    cap.after_send = drop
    rule = object()
    drv._follow_up(rule, ["GA\r", "GB\r"], "", [])
    await _settle(drv)
    assert cap.sent == [b"GA\r"]
    assert drv._follow_up_until[(id(rule), "")] > time.monotonic()


async def test_a_line_whose_own_gate_is_shut_is_skipped_and_the_round_goes_on() -> None:
    cap = Capture()
    drv = _driver(LINE_UNIT, cap)
    drv.state.set("device.dev1.standby", True)
    shut = {"send": "GA\r", "when_state": {"standby": False}}
    drv._follow_up(object(), [shut, "GB\r"], "", [])
    await _settle(drv)
    assert cap.sent == [b"GB\r"]


async def test_a_held_poll_sends_no_follow_up_and_still_holds_the_rule() -> None:
    cap = Capture()
    drv = _driver(_with(LINE_UNIT, polling={"queries": [], "when_state": {"standby": False}}), cap)
    drv.state.set("device.dev1.standby", True)
    rule = object()
    drv._follow_up(rule, ["GA\r", "GB\r"], "", [])
    await _settle(drv)
    assert cap.sent == []
    assert drv._follow_up_until[(id(rule), "")] > time.monotonic()


async def test_a_failed_line_ends_the_round_with_a_warning(caplog) -> None:
    cap = Capture(fail=b"GA")
    drv = _driver(LINE_UNIT, cap)
    rule = object()
    with caplog.at_level(logging.WARNING, logger="openavc.drivers.configurable"):
        drv._follow_up(rule, ["GA\r", "GB\r"], "", [])
        await _settle(drv)
    assert cap.sent == []
    assert "then_query failed; the next poll reads the values: write failed" in caplog.text
    assert drv._follow_up_until[(id(rule), "")] > time.monotonic()


async def test_a_cancelled_round_leaves_no_hold() -> None:
    cap = Capture()
    drv = _driver(LINE_UNIT, cap)
    gate = asyncio.Event()

    async def stall() -> None:
        await gate.wait()

    drv._pace_line = stall  # the round waits for its slot
    rule = object()
    drv._follow_up(rule, ["GA\r"], "", [])
    (task,) = drv._follow_up_tasks
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert (id(rule), "") not in drv._follow_up_until
    assert cap.sent == []


async def test_dropping_the_follow_ups_cancels_them_and_forgets_every_hold() -> None:
    drv = _driver(LINE_UNIT, Capture())
    gate = asyncio.Event()

    async def stall() -> None:
        await gate.wait()

    drv._pace_line = stall
    drv._follow_up(object(), ["GA\r"], "", [])
    drv._follow_up_until[(1, "old")] = time.monotonic() + 60
    (task,) = drv._follow_up_tasks
    drv._drop_follow_ups()
    await asyncio.gather(task, return_exceptions=True)
    assert task.cancelled()
    assert drv._follow_up_tasks == set()
    assert drv._follow_up_until == {}


def test_each_child_lines_are_left_as_the_poll_expands_them() -> None:
    drv = _driver(LINE_UNIT, Capture())
    expanded = drv._expand_query
    drv._expand_query = lambda entry: ["GO{child_id}\r"]
    assert drv._follow_up_lines({"each_child": "output", "send": "x"}, [1, 2]) == ["GO{child_id}\r"]
    drv._expand_query = expanded
    assert drv._follow_up_lines("GO{child_id}\r", [1, 2]) == ["GO1\r", "GO2\r"]
    assert drv._follow_up_lines("GO{child_id}\r", []) == []
    assert drv._follow_up_lines("GL\r", []) == ["GL\r"]


# ── read-backs ──


def _sends(drv: Any, command: str, given: dict[str, Any] | None = None, wire: dict[str, Any] | None = None):
    cmd_def = drv._definition["commands"][command]
    return drv._read_back_sends(command, cmd_def, given or {}, wire or {})


def test_read_back_true_on_a_byte_stream_command_names_no_query(caplog) -> None:
    unit = _with(LINE_UNIT)
    unit["commands"]["hello"]["read_back"] = True
    drv = _driver(unit, Capture())
    with caplog.at_level(logging.DEBUG, logger="openavc.drivers.configurable"):
        assert _sends(drv, "hello") == []
    assert "read_back: true on 'hello' names no query: only an OSC or HTTP command reads itself back" in caplog.text


@pytest.mark.parametrize("value", [5, {"q": "GL"}, None])
def test_a_read_back_of_no_known_form_sends_nothing(value: Any) -> None:
    unit = _with(LINE_UNIT)
    unit["commands"]["hello"]["read_back"] = value
    drv = _driver(unit, Capture())
    assert _sends(drv, "hello") == []


def test_a_read_back_list_keeps_its_nonblank_strings_in_order() -> None:
    unit = _with(LINE_UNIT)
    unit["commands"]["hello"]["read_back"] = ["", 3, "GB\r", None, "GA\r"]
    drv = _driver(unit, Capture())
    assert _sends(drv, "hello") == [("raw GB\r", ("raw", b"GB\r")), ("raw GA\r", ("raw", b"GA\r"))]


SEND_FRAME = {"type": "length_prefix", "header": "HD", "length_size": 1}


def test_a_tcp_read_back_is_framed_and_a_udp_one_is_not() -> None:
    tcp = _driver(_with(LINE_UNIT, send_frame=SEND_FRAME), Capture())
    assert _sends(tcp, "set_level", {"level": 1}, {"level": "A"}) == [
        ("raw GL\r", ("raw", b"HD\x03GL\r")),
    ]
    udp = _driver(_with(LINE_UNIT, transport="udp", send_frame=SEND_FRAME), Capture())
    assert _sends(udp, "set_level", {"level": 1}, {"level": "A"}) == [
        ("raw GL\r", ("raw", b"GL\r")),
    ]


def test_a_read_back_text_is_filled_from_config_push_and_the_wire_values() -> None:
    unit = _with(LINE_UNIT)
    unit["commands"]["set_level"]["read_back"] = "G{zone}{level}\\x41\r"
    drv = _driver(unit, Capture(), zone="Z")
    assert _sends(drv, "set_level", {"level": 1}, {"level": "A"}) == [
        ("raw GZA\\x41\r", ("raw", b"GZAA\r")),
    ]


def test_a_read_back_on_osc_and_http_names_an_address_and_a_path() -> None:
    osc = _driver(_with(LINE_UNIT, transport="osc"), Capture())
    assert _sends(osc, "set_level", {"level": 1}, {"level": "A"}) == [("osc GL\r", ("osc", "GL\r"))]
    http = _driver(_with(LINE_UNIT, transport="http"), Capture())
    assert _sends(http, "set_level", {"level": 1}, {"level": "A"}) == [("http GL\r", ("http", "GL\r"))]


def test_a_read_back_naming_a_command_gives_it_the_callers_shared_values() -> None:
    unit = _with(LINE_UNIT)
    unit["commands"]["set_level"]["read_back"] = ["get_level", "set_level"]
    drv = _driver(unit, Capture())
    sends = _sends(drv, "set_level", {"level": 1, "fade": 5}, {"level": "A", "fade": 5})
    assert sends[0] == ("command get_level [('level', 1)]", ("command", ("get_level", {"level": 1})))
    # The command's own name is a raw line, never the command again.
    assert sends[1] == ("raw set_level", ("raw", b"set_level"))


async def test_a_read_back_that_cannot_be_built_is_not_sent(caplog) -> None:
    unit = _with(LINE_UNIT, send_frame=SEND_FRAME)
    unit["commands"]["hello"]["read_back"] = "G" * 300
    cap = Capture()
    drv = _driver(unit, cap)
    with caplog.at_level(logging.WARNING, logger="openavc.drivers.configurable"):
        assert await drv.send_command("hello") is True
    assert drv._read_back_tasks == set()
    assert drv._read_back_waiting == set()
    assert "read_back after 'hello' not sent; the next poll reads the value:" in caplog.text
    assert cap.sent == [b"HD\x06HELLO\r"]


def test_a_read_back_with_no_running_loop_queues_nothing() -> None:
    drv = _driver(LINE_UNIT, Capture())
    drv._read_back("set_level", drv._definition["commands"]["set_level"], {"level": 1}, {"level": "A"})
    assert drv._read_back_tasks == set()
    assert drv._read_back_waiting == set()


async def test_a_command_with_no_read_back_schedules_none() -> None:
    drv = _driver(LINE_UNIT, Capture())
    drv._read_back("hello", drv._definition["commands"]["hello"], {}, {})
    assert drv._read_back_tasks == set()


async def test_a_read_back_already_waiting_is_not_queued_again() -> None:
    cap = Capture()
    drv = _driver(LINE_UNIT, cap)
    cmd_def = drv._definition["commands"]["set_level"]
    drv._read_back("set_level", cmd_def, {"level": 1}, {"level": "A"})
    assert drv._read_back_waiting == {"raw GL\r"}
    drv._read_back("set_level", cmd_def, {"level": 2}, {"level": 2})
    assert len(drv._read_back_tasks) == 1
    await _settle(drv)
    assert cap.sent == [b"GL\r"]
    assert drv._read_back_waiting == set()


async def test_a_failed_read_back_query_ends_the_round_with_a_warning(caplog) -> None:
    unit = _with(LINE_UNIT)
    unit["commands"]["set_level"]["read_back"] = ["GA\r", "GB\r"]
    cap = Capture(fail=b"GA")
    drv = _driver(unit, cap)
    with caplog.at_level(logging.WARNING, logger="openavc.drivers.configurable"):
        drv._read_back("set_level", drv._definition["commands"]["set_level"], {"level": 1}, {"level": "A"})
        await _settle(drv)
    assert cap.sent == []
    assert drv._read_back_waiting == set()
    assert "read_back after 'set_level' failed; the next poll reads the value: write failed" in caplog.text


async def test_a_read_back_held_by_the_poll_gate_says_so(caplog) -> None:
    cap = Capture()
    drv = _driver(_with(LINE_UNIT, polling={"queries": [], "when_state": {"standby": False}}), cap)
    drv.state.set("device.dev1.standby", True)
    with caplog.at_level(logging.DEBUG, logger="openavc.drivers.configurable"):
        drv._read_back("set_level", drv._definition["commands"]["set_level"], {"level": 1}, {"level": "A"})
        await _settle(drv)
    assert cap.sent == []
    assert drv._read_back_waiting == set()
    assert "read_back after 'set_level' not sent: polling.when_state holds the poll" in caplog.text


async def test_a_read_back_after_the_link_drops_sends_nothing() -> None:
    cap = Capture()
    drv = _driver(LINE_UNIT, cap)
    cap.connected = False
    drv._read_back("set_level", drv._definition["commands"]["set_level"], {"level": 1}, {"level": "A"})
    await _settle(drv)
    assert cap.sent == []
    assert drv._read_back_waiting == set()


async def test_the_first_read_back_keeps_a_line_gap_from_the_command(monkeypatch) -> None:
    cap = Capture()
    drv = _driver(LINE_UNIT, cap)
    drv._pace_lines = True
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    async def no_pace() -> None:
        return None

    drv._pace_line = no_pace
    import openavc.drivers.configurable as configurable_mod

    monkeypatch.setattr(configurable_mod, "sleep_at_least", sleep)
    drv._read_back("set_level", drv._definition["commands"]["set_level"], {"level": 1}, {"level": "A"})
    await _settle(drv)
    assert slept == [DEFAULT_LINE_GAP_S]
    assert cap.sent == [b"GL\r"]


async def test_a_read_back_sends_each_kind_its_own_way() -> None:
    from openavc.transport.osc_codec import osc_encode_message

    calls: list[Any] = []

    class Link:
        connected = True

        async def wait_idle(self) -> None:
            return None

        async def send(self, data: bytes) -> None:
            calls.append(("send", data))

        async def get(self, path: str) -> HTTPResponse:
            calls.append(("get", path))
            return HTTPResponse(status_code=200, text="LEVEL=7", headers={})

    drv = _driver(LINE_UNIT, Link())
    sent_commands: list[Any] = []

    async def send_command(name: str, params: dict[str, Any] | None = None, **kw: Any) -> Any:
        sent_commands.append((name, params, kw))
        return True

    drv._send_command = send_command
    await drv._send_read_back("command", ("get_level", {"level": 1}))
    await drv._send_read_back("osc", "/acme/level")
    await drv._send_read_back("http", "/level")
    await drv._send_read_back("raw", b"GL\r")
    assert sent_commands == [("get_level", {"level": 1}, {})]
    assert calls == [("send", osc_encode_message("/acme/level")), ("get", "/level"), ("send", b"GL\r")]
    assert drv.get_state("level") == 7


async def test_dropping_the_read_backs_cancels_them_and_forgets_the_waiting() -> None:
    drv = _driver(LINE_UNIT, Capture())
    gate = asyncio.Event()

    async def stall() -> None:
        await gate.wait()

    drv._pace_line = stall
    drv._read_back("set_level", drv._definition["commands"]["set_level"], {"level": 1}, {"level": "A"})
    (task,) = drv._read_back_tasks
    await asyncio.sleep(0)
    drv._drop_read_backs()
    await asyncio.gather(task, return_exceptions=True)
    assert task.cancelled()
    assert drv._read_back_tasks == set()
    assert drv._read_back_waiting == set()


async def test_closing_the_session_drops_both_kinds_of_round() -> None:
    drv = _driver(LINE_UNIT, Capture())
    gate = asyncio.Event()

    async def stall() -> None:
        await gate.wait()

    drv._pace_line = stall
    drv._follow_up(object(), ["GA\r"], "", [])
    drv._read_back("set_level", drv._definition["commands"]["set_level"], {"level": 1}, {"level": "A"})
    tasks = list(drv._follow_up_tasks) + list(drv._read_back_tasks)
    await asyncio.sleep(0)
    await drv._close_session()
    await asyncio.gather(*tasks, return_exceptions=True)
    assert all(t.cancelled() for t in tasks)
    assert drv._follow_up_tasks == set() and drv._read_back_tasks == set()


# ── the line gap ──


@pytest.mark.parametrize(
    ("transport", "delay", "pace", "gap"),
    [
        ("tcp", None, True, DEFAULT_LINE_GAP_S),
        ("serial", 0, True, DEFAULT_LINE_GAP_S),
        ("tcp", "not a number", True, DEFAULT_LINE_GAP_S),
        ("tcp", 0.1, True, 0.0),
        ("tcp", "0.2", True, 0.0),
        ("udp", None, True, 0.0),
        ("http", None, True, 0.0),
        ("tcp", None, False, 0.0),
    ],
)
def test_the_line_gap(transport: str, delay: Any, pace: bool, gap: float) -> None:
    drv = _driver(_with(LINE_UNIT, transport=transport), Capture(), inter_command_delay=delay)
    drv._pace_lines = pace
    assert drv._line_gap() == gap


def test_the_config_transport_decides_the_gap_before_the_definitions() -> None:
    drv = _driver(_with(LINE_UNIT, transport="udp"), Capture(), transport="tcp")
    drv._pace_lines = True
    assert drv._line_gap() == DEFAULT_LINE_GAP_S


# ── send_command's routes ──


async def test_an_ir_code_goes_through_the_bridge_before_the_connected_gate() -> None:
    drv = _driver(LINE_UNIT, None, ir_codes={"power": {"pronto": "0000 006D 0001"}})
    emitted: list[Any] = []

    async def emit(kind: str, payload: dict[str, Any]) -> Any:
        emitted.append((kind, payload))
        return "emitted"

    drv.emit_via_bridge = emit
    assert await drv.send_command("power", {"ignored": 1}) == "emitted"
    pronto = drv._definition["commands"]["power"]["ir"]["pronto"]
    assert emitted == [("ir", {"pronto": pronto, "repeat": 1})]


async def test_a_udp_side_send_checks_its_params_before_the_connected_gate() -> None:
    unit = _with(LINE_UNIT)
    unit["commands"]["announce"] = {
        "label": "Announce",
        "udp": {"port": 9999, "payload": "N{n}"},
        "params": {"n": {"type": "integer", "label": "N", "min": 1, "max": 5}},
    }
    drv = _driver(unit, None)
    with pytest.raises(CommandParamError):
        await drv.send_command("announce", {"n": 9})
    sent: list[Any] = []

    async def send_udp(data: bytes, **kw: Any) -> None:
        sent.append((data, kw))

    drv.send_udp = send_udp
    assert await drv.send_command("announce", {"n": 2}) is True
    assert sent == [(b"N2", {"host": None, "port": 9999, "broadcast": False})]


async def test_the_connected_gate_comes_before_the_params_and_the_command_lookup() -> None:
    drv = _driver(LINE_UNIT, None)
    with pytest.raises(ConnectionError, match=r"\[dev1\] Not connected"):
        await drv.send_command("set_level", {"level": "not a number"})
    with pytest.raises(ConnectionError):
        await drv.send_command("no_such_command")
    cap = Capture()
    cap.connected = False
    drv.transport = cap
    with pytest.raises(ConnectionError):
        await drv.send_command("hello")


async def test_an_unknown_command_is_reported_and_sends_nothing(caplog) -> None:
    cap = Capture()
    drv = _driver(LINE_UNIT, cap)
    seen: list[Any] = []
    drv.contract_observer = lambda kind, detail: seen.append((kind, detail))
    with caplog.at_level(logging.WARNING, logger="openavc.drivers.configurable"):
        assert await drv.send_command("no_such_command") is None
    assert seen == [(UNKNOWN_COMMAND, {"command": "no_such_command"})]
    assert "[dev1] Unknown command: no_such_command" in caplog.text
    assert cap.sent == []


async def test_a_byte_stream_command_with_no_send_string_is_not_read_back() -> None:
    unit = _with(LINE_UNIT)
    unit["commands"]["silent"]["read_back"] = "GL\r"
    cap = Capture()
    drv = _driver(unit, cap)
    assert await drv.send_command("silent") is None
    assert drv._read_back_tasks == set()
    assert cap.sent == []


async def test_a_sent_command_is_read_back_with_the_callers_values_and_the_wire_ones() -> None:
    drv = _driver(LINE_UNIT, Capture())
    seen: list[Any] = []
    drv._read_back = lambda *args: seen.append(args)
    assert await drv.send_command("set_level", {"level": 1, "fade": 2}) is True
    cmd_def = drv._definition["commands"]["set_level"]
    assert seen == [("set_level", cmd_def, {"level": 1, "fade": 2}, {"level": "A", "fade": 2})]
    seen.clear()
    # The driver's own sends never read back.
    assert await drv._send_command("set_level", {"level": 1}) is True
    assert seen == []


async def test_an_osc_or_http_command_on_the_wrong_link_is_not_read_back(caplog) -> None:
    unit = _with(LINE_UNIT)
    unit["commands"]["osc_cmd"] = {"label": "O", "address": "/x", "read_back": True}
    unit["commands"]["http_cmd"] = {"label": "H", "path": "/x", "read_back": True}
    drv = _driver(unit, Capture())
    seen: list[Any] = []
    drv._read_back = lambda *args: seen.append(args)
    with caplog.at_level(logging.ERROR, logger="openavc.drivers.configurable"):
        assert await drv.send_command("osc_cmd") is None
        assert await drv.send_command("http_cmd") is None
    assert seen == []
    assert "Command 'osc_cmd' uses OSC fields but transport is not OSC" in caplog.text
    assert "Command 'http_cmd' uses HTTP fields but transport is not HTTP" in caplog.text


async def test_an_http_command_is_read_back_whatever_status_it_got() -> None:
    from openavc.transport.http_client import HTTPClientTransport

    class Http(HTTPClientTransport):
        connected = True

        def __init__(self) -> None:
            self.requests: list[Any] = []

        async def request(self, method: str, path: str, **kw: Any) -> HTTPResponse:
            self.requests.append((method, path, kw))
            return HTTPResponse(status_code=500, text="", headers={})

    unit = _with(LINE_UNIT, transport="http")
    unit["commands"]["http_cmd"] = {
        "label": "H", "method": "post", "path": "/x/{level}", "body": "{level}",
        "query_params": {"a": "{level}", "b": 2}, "read_back": True,
        "params": {"level": {"type": "integer", "label": "Level"}},
    }
    http = Http()
    drv = _driver(unit, http)
    seen: list[Any] = []
    drv._read_back = lambda *args: seen.append(args[0])
    response = await drv.send_command("http_cmd", {"level": 3})
    assert response.status_code == 500
    assert seen == ["http_cmd"]
    assert http.requests == [
        ("POST", "/x/3", {"params": {"a": "3", "b": 2}, "json_body": 3, "content": None, "headers": None}),
    ]


# ── the inline protocol a device authors ──


def test_a_file_only_device_shares_the_class_definition() -> None:
    drv = _driver(LINE_UNIT, Capture())
    assert drv._definition is type(drv)._definition
    assert drv._inline_command_names == set()
    assert drv.DRIVER_INFO is type(drv).DRIVER_INFO


def test_inline_commands_take_the_config_delimiter_first() -> None:
    drv = _driver(LINE_UNIT, Capture(), delimiter="\n", commands={"ping": {"send": "PING"}})
    assert drv._definition["commands"]["ping"]["send"] == "PING\n"
    plain = _driver(LINE_UNIT, Capture(), commands={"ping": {"send": "PING"}})
    assert plain._definition["commands"]["ping"]["send"] == "PING\r"
    bare = _driver(_with(LINE_UNIT, delimiter=""), Capture(), commands={"ping": {"send": "PING"}})
    assert bare._definition["commands"]["ping"]["send"] == "PING"


def test_inline_commands_take_the_prefix_and_the_suffix_over_the_delimiter() -> None:
    unit = _with(LINE_UNIT, command_prefix="<", command_suffix=">")
    drv = _driver(unit, Capture(), commands={"ping": {"send": "PING"}})
    assert drv._definition["commands"]["ping"]["send"] == "<PING>"
    assert drv._inline_command_names == {"ping"}
    # The config's own prefix wins over the file's.
    own = _driver(unit, Capture(), command_prefix="[", commands={"ping": {"send": "PING"}})
    assert own._definition["commands"]["ping"]["send"] == "[PING>"
    # A file command is framed at send time instead, and only once.
    assert drv.build_wire("hello") == b"<HELLO\r>"
    assert drv.build_wire("ping") == b"<PING>"


def test_send_framing_is_set_even_when_the_device_authors_nothing() -> None:
    drv = _driver(_with(LINE_UNIT, command_prefix="<", send_frame=SEND_FRAME), Capture())
    assert drv._command_prefix == "<"
    assert drv._command_suffix == ""
    assert drv._send_frame == {"header": b"HD", "after_length": b"", "length_size": 1, "length_endian": "big"}
    assert drv._definition is type(drv)._definition


def test_inline_poll_commands_join_the_poll_after_the_files_queries() -> None:
    unit = _with(LINE_UNIT, polling={"queries": ["GL\r"], "interval": 5})
    drv = _driver(unit, Capture(), commands={
        "status": {"send": "ST?", "poll": True},
        "other": {"send": "OT?"},
    })
    assert drv._definition["polling"] == {"queries": ["GL\r", "ST?\r"], "interval": 5}
    assert type(drv)._definition["polling"] == {"queries": ["GL\r"], "interval": 5}


def test_an_inline_command_with_no_send_string_polls_by_name() -> None:
    drv = _driver(_with(LINE_UNIT, transport="http"), Capture(), commands={
        "status": {"method": "GET", "path": "/status", "poll": True},
    })
    assert drv._definition["polling"]["queries"] == ["status"]


def test_the_inline_protocol_gets_its_own_driver_info() -> None:
    drv = _driver(LINE_UNIT, Capture(), commands={"ping": {"send": "PING", "label": "Ping"}},
                  state_variables={"temp": {"type": "integer", "label": "Temp"}})
    cls = type(drv)
    assert "ping" in drv.DRIVER_INFO["commands"] and "ping" not in cls.DRIVER_INFO["commands"]
    assert drv.DRIVER_INFO["commands"]["ping"]["label"] == "Ping"
    assert drv.DRIVER_INFO["commands"]["hello"] == {"label": "Hello", "params": {}}
    assert drv.DRIVER_INFO["state_variables"]["temp"]["label"] == "Temp"
    assert "temp" not in cls.DRIVER_INFO["state_variables"]
    # Seeded before the first reply.
    assert "device.dev1.temp" in drv.state.snapshot()


def test_inline_state_variables_layer_file_then_derived_then_config() -> None:
    drv = _driver(LINE_UNIT, Capture(),
                  responses=[{"match": r"^MODE=(\w+)$", "mappings": [{"group": 1, "state": "mode", "type": "string"}]},
                             {"match": r"^LEVEL=(\d+)$", "mappings": [{"group": 1, "state": "level", "type": "integer"}]}],
                  state_variables={"level": {"type": "integer", "label": "Config Level"}})
    vars_ = drv._definition["state_variables"]
    assert vars_["level"] == {"type": "integer", "label": "Config Level"}
    # A file variable a rule writes is replaced by the derived default (filed).
    assert vars_["mode"] == {"type": "string"}
    assert vars_["standby"] == {"type": "boolean", "label": "Standby"}
    assert drv._definition["responses"][0] == LINE_UNIT["responses"][0]
    assert len(drv._definition["responses"]) == 3


def test_ir_codes_alone_make_commands() -> None:
    drv = _driver(LINE_UNIT, Capture(), ir_codes={"power": {"pronto": "0000 006D 0001", "repeat": 3}})
    assert drv._definition["commands"]["power"]["ir"] == {"pronto": "0000 006D 0001", "repeat": 3}
    assert "power" in drv.DRIVER_INFO["commands"]
    assert drv._inline_command_names == set()


def test_an_inline_placeholder_prompts_unless_config_fills_it() -> None:
    drv = _driver(LINE_UNIT, Capture(), zone="Z", commands={"go": {"send": "GO{zone}{level}"}})
    assert set(drv._definition["commands"]["go"]["params"]) == {"level"}
