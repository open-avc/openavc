"""The auto-simulator answers in the framing the driver reads.

A driver whose replies arrive length-prefixed or in fixed-size frames reads
them through its ``frame_parser``; a device that dials back to a push listener
sends frames that listener's own ``frame_parser`` reads. The simulator writes
each through the same table the runtime builds its parser from
(``frame_parsers.write_frame``), so whatever type the driver declares, the
platform's parser reads back what the simulator sent. A ``send_frame`` header,
when the driver declares one, still wraps the control link's messages: it
carries the device's own constant bytes, where a frame parser's writer can
only put zero bytes. Every message on the control link is framed the same way,
a reply or one the device sends unprompted.

An invented device throughout.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest

from openavc.simulator.yaml_auto import YAMLAutoSimulator
from openavc.transport.frame_parsers import build_frame_parser


def _def(**overrides: Any) -> dict[str, Any]:
    d: dict[str, Any] = {
        "id": "acme_widget",
        "name": "Acme Widget",
        "transport": "tcp",
        "delimiter": "\r",
        "default_config": {"port": 9000},
        "state_variables": {"power": {"type": "boolean"}},
        "commands": {
            "power_on": {"send": "PWR1\r"},
            "subscribe": {"send": "SUB {listener_port}\r"},
        },
        "responses": [{"match": r"PWR(\d)", "set": {"power": "$1"}}],
    }
    d.update(overrides)
    return d


def _sim(**overrides: Any) -> YAMLAutoSimulator:
    return YAMLAutoSimulator("w1", {}, driver_def=_def(**overrides))


class _Pushed:
    """What the simulator sends on its control link unprompted."""

    def __init__(self, sim: YAMLAutoSimulator) -> None:
        self.sent: list[bytes] = []
        sim.push = self._push
        sim._clients = {"c": None}

    async def _push(self, data: bytes) -> None:
        self.sent.append(data)


# --- the device's dial-back ----------------------------------------------------------


@pytest.mark.parametrize(
    "frame_parser",
    [
        {"type": "length_prefix", "header_size": 2},
        {"type": "length_prefix", "length_offset": 4, "header_size": 4, "header_extra": 2},
        {"type": "fixed_length", "length": 5},
        {"type": "struct_frame", "header_reserve": 3, "length_adjust": -1},
    ],
    ids=lambda fp: fp["type"],
)
def test_a_dial_back_is_framed_as_the_listener_reads_it(frame_parser) -> None:
    sim = _sim(push={"type": "tcp_listener", "port": 0, "register": "subscribe",
                     "frame_parser": frame_parser})
    framed = sim._wrap_push_tcp_frame(b"PWR1\r")
    assert build_frame_parser(frame_parser).feed(framed) == [b"PWR1\r"]


def test_a_dial_back_its_frame_cannot_carry_goes_out_as_it_is(caplog) -> None:
    caplog.set_level(logging.WARNING)
    sim = _sim(push={"type": "tcp_listener", "port": 0, "register": "subscribe",
                     "frame_parser": {"type": "fixed_length", "length": 8}})
    assert sim._wrap_push_tcp_frame(b"PWR1\r") == b"PWR1\r"
    said = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert said and "exactly 8 bytes" in said[-1]


def test_a_dial_back_with_no_frame_the_runtime_builds_goes_out_as_it_is() -> None:
    for frame_parser in ({"type": "fixed_length", "length": 0}, {"type": "delimiter"}):
        sim = _sim(push={"type": "tcp_listener", "port": 0, "register": "subscribe",
                         "frame_parser": frame_parser})
        assert sim._wrap_push_tcp_frame(b"PWR1\r") == b"PWR1\r"


# --- the control link ----------------------------------------------------------------


def test_a_reply_is_framed_as_the_drivers_frame_parser_reads_it() -> None:
    frame_parser = {"type": "length_prefix", "header_size": 1}
    reply = _sim(frame_parser=frame_parser).handle_command(b"PWR1")
    assert reply is not None
    assert build_frame_parser(frame_parser).feed(reply) == [b"PWR1\r"]


SEND_FRAME = {"type": "length_prefix", "header": "AC\\x00", "length_size": 2,
              "after_length": "\\x01"}
FRAME_PARSER = {"type": "length_prefix", "length_offset": 3, "header_size": 2,
                "header_extra": 1}


def test_a_send_frame_header_still_wraps_a_reply() -> None:
    sim = _sim(send_frame=SEND_FRAME, frame_parser=FRAME_PARSER)
    reply = sim.handle_command(b"PWR1")
    assert reply == b"AC\x00\x00\x05\x01PWR1\r"
    assert build_frame_parser(FRAME_PARSER).feed(reply) == [b"PWR1\r"]


@pytest.mark.asyncio
async def test_a_value_pushed_unprompted_is_framed_as_a_reply_is() -> None:
    frame_parser = {"type": "length_prefix", "header_size": 1}
    sim = _sim(frame_parser=frame_parser, simulator={"push_state": True})
    pushed = _Pushed(sim)
    sim.set_state("power", True)
    await asyncio.sleep(0)
    assert [build_frame_parser(frame_parser).feed(data) for data in pushed.sent] == [[b"PWR1\r"]]



@pytest.mark.asyncio
async def test_a_send_frame_header_wraps_a_value_pushed_unprompted() -> None:
    sim = _sim(send_frame=SEND_FRAME, frame_parser=FRAME_PARSER,
               simulator={"push_state": True})
    pushed = _Pushed(sim)
    sim.set_state("power", True)
    await asyncio.sleep(0)
    assert pushed.sent == [b"AC\x00\x00\x05\x01PWR1\r"]


def test_a_line_driver_is_answered_in_lines() -> None:
    assert _sim().handle_command(b"PWR1") == b"PWR1\r"


def test_a_reply_its_frame_cannot_carry_goes_out_as_it_is(caplog) -> None:
    caplog.set_level(logging.WARNING)
    sim = _sim(frame_parser={"type": "fixed_length", "length": 8})
    assert sim.handle_command(b"PWR1") == b"PWR1\r"
    said = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert said and "exactly 8 bytes" in said[-1]
