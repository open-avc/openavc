"""What a YAML driver's ``auth:`` sign-in does on the paths nothing else tests.

The sign-in reads the device's bytes raw (the frame parser comes off the
transport for it), waits for each prompt in turn, and fails the connect
with a precise offline reason. These pin the edges: a transport gone before
the sign-in starts, bytes the frame parser had already buffered, a
transport with no frame parser at all, a pattern that does not compile, a
stage with no time left, a success indicator that never comes, and the
listen for a prompt a blank credential cannot answer. On an invented Acme
device.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any
from unittest.mock import AsyncMock

import pytest

from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.base import ConnectionFaultError
from openavc.drivers.configurable import create_configurable_driver_class

_AUTH = {
    "type": "telnet_login",
    "username_prompt": "login: ",
    "password_prompt": "Password: ",
    "timeout_seconds": 0.2,
}


def _driver(auth: dict[str, Any] | None = None, **config: Any) -> Any:
    definition: dict[str, Any] = {
        "id": "acme_secure",
        "name": "Acme Secure Widget",
        "manufacturer": "Acme",
        "category": "utility",
        "version": "1.0.0",
        "transport": "tcp",
        "default_config": {"host": "", "port": 23},
        "config_schema": {"username": {"type": "string", "label": "Login Name"}},
        "state_variables": {"power": {"type": "string", "label": "Power"}},
        "commands": {},
        "responses": [{"match": r"PWR=(\w+)", "set": {"power": "$1"}}],
        "auth": dict(_AUTH if auth is None else auth),
    }
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    cls = create_configurable_driver_class(definition)
    cfg = {"host": "10.0.0.9", "port": 23, "username": "admin", "password": "pw"}
    cfg.update(config)
    return cls("dev1", cfg, state, events)


class _Parser:
    def __init__(self, buffered: bytes = b"") -> None:
        self._buffer = buffered
        self.fed: list[bytes] = []

    def feed(self, data: bytes) -> list[bytes]:
        self.fed.append(data)
        return [line for line in data.split(b"\r\n") if line]


class _Wire:
    def __init__(self, parser: Any = None, *, has_parser: bool = True) -> None:
        self.connected = True
        self.sent: list[bytes] = []
        if has_parser:
            self._frame_parser = parser

    async def send(self, data: bytes) -> None:
        self.sent.append(bytes(data))


# --- what connect() plans --------------------------------------------------


@pytest.mark.parametrize("auth, plan", [
    ({**_AUTH, "type": "http_basic"}, None),
    ({**_AUTH, "username_prompt": ""}, None),
    ({k: v for k, v in _AUTH.items() if k != "password_prompt"}, None),
    (_AUTH, "run"),
])
def test_what_connect_plans_for_an_auth_block(auth, plan):
    driver = _driver(auth)
    assert driver._auth_plan(auth) == plan
    assert driver._auth_should_run(auth) is (plan == "run")


def test_a_blank_credential_plans_a_listen_on_tcp_only():
    driver = _driver(username="")
    assert driver._auth_plan(_AUTH) == "blank"
    assert driver._auth_plan({**_AUTH, "skip_if_empty": False}) == "run"


# --- taking the line raw ---------------------------------------------------


async def test_a_transport_gone_before_the_sign_in_starts_skips_it():
    driver = _driver()
    driver._auth_pending = True
    driver._auth_mode = True
    driver._perform_auth_handshake = AsyncMock()
    driver.transport = None
    await driver._post_connect()
    assert driver._auth_mode is False
    driver._perform_auth_handshake.assert_not_awaited()

    driver._auth_mode = True
    wire = _Wire(_Parser())
    wire.connected = False
    driver.transport = wire
    await driver._post_connect()
    assert driver._auth_mode is False
    driver._perform_auth_handshake.assert_not_awaited()


async def test_nothing_planned_turns_raw_mode_off():
    driver = _driver()
    driver._auth_mode = True
    driver._perform_auth_handshake = AsyncMock()
    driver.transport = _Wire(_Parser())
    await driver._post_connect()
    assert driver._auth_mode is False
    driver._perform_auth_handshake.assert_not_awaited()


async def test_bytes_the_frame_parser_buffered_move_into_the_sign_in():
    driver = _driver()
    driver._auth_pending = True
    parser = _Parser(b"banner\r\nlogin: ")
    wire = _Wire(parser)
    driver.transport = wire
    seen: dict[str, Any] = {}

    async def handshake() -> None:
        seen["buffer"] = bytes(driver._auth_buffer)
        seen["event"] = driver._auth_event.is_set()
        seen["parser_on_wire"] = wire._frame_parser
        seen["saved"] = driver._saved_frame_parser

    driver._perform_auth_handshake = handshake
    await driver._post_connect()
    assert seen == {
        "buffer": b"banner\r\nlogin: ",
        "event": True,
        "parser_on_wire": None,
        "saved": parser,
    }
    assert parser._buffer == b""


async def test_an_empty_parser_buffer_leaves_the_event_unset():
    driver = _driver()
    driver._auth_pending = True
    parser = _Parser(b"")
    driver.transport = _Wire(parser)
    seen: dict[str, Any] = {}

    async def handshake() -> None:
        seen["event"] = driver._auth_event.is_set()
        seen["buffer"] = bytes(driver._auth_buffer)

    driver._perform_auth_handshake = handshake
    await driver._post_connect()
    assert seen == {"event": False, "buffer": b""}


async def test_a_transport_with_no_frame_parser_is_left_as_it_is():
    driver = _driver()
    driver._auth_pending = True
    wire = _Wire(has_parser=False)
    driver.transport = wire
    driver._perform_auth_handshake = AsyncMock()
    await driver._post_connect()
    assert driver._saved_frame_parser is None
    assert not hasattr(wire, "_frame_parser")
    driver._perform_auth_handshake.assert_awaited_once()


async def test_a_failed_sign_in_is_logged_and_raised(caplog):
    driver = _driver()
    driver._auth_pending = True
    driver.transport = _Wire(_Parser())
    driver._perform_auth_handshake = AsyncMock(
        side_effect=ConnectionFaultError("login rejected", code="auth_failed")
    )
    with pytest.raises(ConnectionFaultError):
        await driver._post_connect()
    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert errors == ["[dev1] Auth handshake failed: login rejected"]


async def test_a_blank_plan_listens_instead_of_signing_in():
    driver = _driver()
    driver._auth_watch_blank = True
    driver.transport = _Wire(_Parser())
    driver._perform_auth_handshake = AsyncMock()
    driver._refuse_unanswerable_prompt = AsyncMock()
    await driver._post_connect()
    driver._refuse_unanswerable_prompt.assert_awaited_once()
    driver._perform_auth_handshake.assert_not_awaited()


# --- the handshake ---------------------------------------------------------


async def test_a_handshake_with_no_auth_block_or_nothing_to_run_does_nothing():
    driver = _driver()
    wire = _Wire(_Parser())
    driver.transport = wire
    driver._definition = {**driver._definition, "auth": "telnet"}
    driver._auth_mode = True
    await driver._perform_auth_handshake()
    assert driver._auth_mode is False

    driver._definition = {**driver._definition, "auth": {**_AUTH, "type": "other"}}
    driver._auth_mode = True
    await driver._perform_auth_handshake()
    assert driver._auth_mode is False
    assert wire.sent == []


@pytest.mark.parametrize("field", [
    "username_prompt", "password_prompt", "success_pattern", "failure_pattern",
])
async def test_a_pattern_that_does_not_compile_fails_before_anything_is_sent(field):
    driver = _driver({**_AUTH, field: "(unclosed"})
    wire = _Wire(_Parser())
    driver.transport = wire
    with pytest.raises(ValueError, match="^Invalid auth regex pattern: "):
        await driver._perform_auth_handshake()
    assert wire.sent == []


async def test_a_timeout_that_is_not_a_number_fails_before_the_patterns_are_read():
    driver = _driver({**_AUTH, "timeout_seconds": "soon", "username_prompt": "(unclosed"})
    driver.transport = _Wire(_Parser())
    with pytest.raises(ValueError, match="could not convert string to float"):
        await driver._perform_auth_handshake()


async def test_a_full_handshake_sends_both_credentials_and_restores_the_parser():
    driver = _driver({**_AUTH, "success_pattern": "OK> ", "line_ending": "\\n"})
    parser = _Parser()
    wire = _Wire(None)
    driver.transport = wire
    driver._saved_frame_parser = parser
    driver._auth_mode = True
    driver._auth_buffer = bytearray(b"login: ")
    # Left by an earlier sign-in: this one reads from the top of its buffer.
    driver._auth_search_pos = 99

    async def answer(data: bytes) -> None:
        wire.sent.append(bytes(data))
        reply = b"Password: " if data == b"admin\n" else b"\r\nOK> "
        driver._take_sign_in_bytes(reply)

    wire.send = answer
    await driver._perform_auth_handshake()
    assert wire.sent == [b"admin\n", b"pw\n"]
    assert wire._frame_parser is parser
    assert driver._auth_mode is False
    assert driver._auth_buffer == bytearray()


async def test_a_handshake_with_no_success_pattern_waits_out_the_noise_and_succeeds():
    driver = _driver()
    wire = _Wire(None)
    driver.transport = wire
    driver._auth_mode = True
    driver._auth_buffer = bytearray(b"login: ")

    async def answer(data: bytes) -> None:
        wire.sent.append(bytes(data))
        if data == b"admin\r\n":
            driver._take_sign_in_bytes(b"Password: ")

    wire.send = answer
    started = time.monotonic()
    await driver._perform_auth_handshake()
    # Nothing came after the password: the drain waited its half second.
    assert time.monotonic() - started >= 0.4
    assert wire.sent == [b"admin\r\n", b"pw\r\n"]
    assert wire._frame_parser is None
    assert driver._auth_mode is False


async def test_noise_after_the_password_ends_the_drain_and_is_not_read_as_a_reply():
    driver = _driver()
    wire = _Wire(None)
    driver.transport = wire
    driver._auth_mode = True
    driver._auth_buffer = bytearray(b"login: ")

    async def answer(data: bytes) -> None:
        wire.sent.append(bytes(data))
        if data == b"admin\r\n":
            driver._take_sign_in_bytes(b"Password: ")
        else:
            driver._take_sign_in_bytes(b"\r\nPWR=on\r\n")

    wire.send = answer
    started = time.monotonic()
    await driver._perform_auth_handshake()
    assert time.monotonic() - started < 0.3
    assert driver.get_state("power") is None
    assert driver._auth_buffer == bytearray()


# --- one stage -------------------------------------------------------------


async def test_a_stage_with_no_time_left_is_no_response():
    driver = _driver()
    driver._auth_buffer = bytearray(b"welcome")
    with pytest.raises(ConnectionFaultError) as caught:
        await driver._auth_wait_for(re.compile("login: "), None, 0.0, stage="username_prompt")
    assert caught.value.fault_code == "no_response"
    assert str(caught.value) == (
        "the device never presented the expected login prompt (timeout waiting "
        "for 'login: '; got 'welcome') — wrong host, port, or protocol for this "
        "device?"
    )


async def test_a_success_indicator_that_never_comes_is_auth_failed():
    driver = _driver()
    driver._auth_buffer = bytearray(b"login: Password: ")
    driver._auth_search_pos = len(b"login: Password: ")
    with pytest.raises(ConnectionFaultError) as caught:
        await driver._auth_wait_for(re.compile("OK> "), None, 0.05, stage="success")
    assert caught.value.fault_code == "auth_failed"
    assert str(caught.value) == (
        "no login confirmation after sending credentials (timeout waiting for "
        "'OK> '; got '') — check the username and password."
    )


async def test_a_failure_pattern_is_checked_before_the_target():
    driver = _driver()
    driver._auth_buffer = bytearray(b"Login incorrect\r\nlogin: ")
    with pytest.raises(ConnectionFaultError) as caught:
        await driver._auth_wait_for(
            re.compile("login: "), re.compile("incorrect"), 1.0,
        )
    assert caught.value.fault_code == "auth_failed"
    assert str(caught.value) == (
        "login rejected by the device (matched failure pattern in "
        "'Login incorrect\\r\\nlogin: ')"
    )


async def test_a_flood_aborts_the_stage_as_no_response():
    driver = _driver()
    driver._auth_overflow = True
    with pytest.raises(ConnectionFaultError) as caught:
        await driver._auth_wait_for(re.compile("login: "), None, 1.0)
    assert caught.value.fault_code == "no_response"
    assert str(caught.value).startswith("auth aborted: more than ")


async def test_a_match_advances_the_search_past_it():
    driver = _driver()
    driver._auth_buffer = bytearray(b"xx login: yy")
    driver._auth_search_pos = 1
    await driver._auth_wait_for(re.compile("login: "), None, 1.0)
    assert driver._auth_search_pos == 1 + len("x login: ")


# --- the listen for a prompt nobody can answer -----------------------------


async def test_a_listen_with_a_pattern_that_does_not_compile_raises_and_restores():
    driver = _driver({**_AUTH, "username_prompt": "(unclosed"}, username="")
    parser = _Parser()
    wire = _Wire(None)
    driver.transport = wire
    driver._saved_frame_parser = parser
    driver._auth_mode = True
    driver._auth_buffer = bytearray(b"junk")
    with pytest.raises(ValueError, match="^Invalid auth regex pattern: "):
        await driver._refuse_unanswerable_prompt()
    assert wire._frame_parser is parser
    assert driver._auth_mode is False
    assert driver._auth_buffer == bytearray()


async def test_a_listen_that_sees_the_prompt_names_the_blank_setting():
    driver = _driver(username="")
    wire = _Wire(None)
    driver.transport = wire
    driver._saved_frame_parser = _Parser()
    driver._auth_mode = True
    driver._auth_buffer = bytearray(b"Welcome\r\nlogin: ")
    with pytest.raises(ConnectionFaultError) as caught:
        await driver._refuse_unanswerable_prompt()
    assert caught.value.fault_code == "auth_failed"
    assert str(caught.value) == (
        "The device asks for a sign-in, and the Login Name setting is blank."
    )
    assert driver._auth_mode is False


async def test_a_flood_ends_the_listen_and_replays_what_came_through_the_parser():
    driver = _driver(username="")
    parser = _Parser()
    wire = _Wire(None)
    driver.transport = wire
    driver._saved_frame_parser = parser
    driver._auth_mode = True
    driver._auth_buffer = bytearray(b"PWR=on\r\nPWR=off\r\n")
    driver._auth_overflow = True
    started = time.monotonic()
    await driver._refuse_unanswerable_prompt()
    # At once: not after the quiet window (0.5 s) a listen would sit out.
    assert time.monotonic() - started < 0.3
    assert parser.fed == [b"PWR=on\r\nPWR=off\r\n"]
    assert driver.get_state("power") == "off"
    assert wire._frame_parser is parser
    assert driver._auth_mode is False


async def test_a_flood_with_nothing_buffered_replays_nothing():
    driver = _driver(username="")
    parser = _Parser()
    driver.transport = _Wire(None)
    driver._saved_frame_parser = parser
    driver._auth_overflow = True
    await driver._refuse_unanswerable_prompt()
    assert parser.fed == []


async def test_bytes_then_quiet_end_the_listen_and_replay_them_whole_without_a_parser():
    driver = _driver(username="")
    driver.transport = _Wire(None)
    driver._saved_frame_parser = None
    driver._auth_mode = True
    driver._auth_buffer = bytearray(b"PWR=on")
    await driver._refuse_unanswerable_prompt()
    assert driver.get_state("power") == "on"


async def test_arming_a_reconnect_after_a_flood_starts_the_sign_in_clean():
    driver = _driver()
    driver._auth_overflow = True
    driver._auth_search_pos = 40
    driver._auth_buffer = bytearray(b"junk")
    old_event = driver._auth_event
    old_event.set()

    driver._arm_sign_in()

    assert driver._auth_overflow is False
    assert driver._auth_search_pos == 0
    assert driver._auth_buffer == bytearray()
    assert driver._auth_event is not old_event
    assert not driver._auth_event.is_set()
    assert (driver._auth_mode, driver._auth_pending, driver._auth_watch_blank) == (True, True, False)


async def test_arming_with_nothing_planned_leaves_the_line_to_the_reply_rules():
    driver = _driver({**_AUTH, "type": "other"})
    driver._auth_buffer = bytearray(b"kept")
    driver._arm_sign_in()
    assert (driver._auth_mode, driver._auth_pending, driver._auth_watch_blank) == (False, False, False)
    assert driver._auth_buffer == bytearray(b"kept")


async def test_arming_a_blank_credential_reads_the_line_raw_for_the_listen():
    driver = _driver(username="")
    driver._arm_sign_in()
    assert (driver._auth_mode, driver._auth_pending, driver._auth_watch_blank) == (True, False, True)
