"""A push listener's own frame parser is held to its type's fields at every door
that judges a driver, as the driver's own frame_parser is.

A ``push: {type: tcp_listener}`` block may name a frame parser for the frames
the device dials back with. A value its type cannot frame with (a fixed-length
frame of no bytes, a negative count of header bytes) fails when the listener
builds the parser for each dial-back, and those pushes then reach the reply
rules unframed. So the author's doors (Validate, the Builder's save, the
checker, catalog CI) refuse it, with one sentence per field naming the rule
(the shape walk's own sentence at that field is left out),
and a door that loads a driver names it on the driver's Installed entry
instead, so a driver that loaded with one still loads. A struct_frame's
fields have refused at every door since push first judged them, and still do.

The runtime, given one anyway, says so once when the listener opens and reads
the pushes unframed, instead of a traceback on every dial-back.
"""
from __future__ import annotations

import logging
from typing import Any

import pytest

from openavc.drivers.avcdriver_semantic import check_driver_definition, validate_driver_issues


def _driver(frame_parser: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": "acme_widget",
        "name": "Acme Widget",
        "manufacturer": "Acme",
        "category": "utility",
        "version": "1.0.0",
        "transport": "tcp",
        "delimiter": "\r",
        "default_config": {"host": "", "port": 23},
        "state_variables": {"power": {"type": "boolean", "label": "Power"}},
        "commands": {
            "register": {"label": "Register", "send": "REG {listener_port}\r"},
            "power_on": {"label": "Power On", "send": "PWR1\r"},
        },
        "responses": [{"match": r"PWR(\d)", "set": {"power": "$1"}}],
        "liveness": {"send": "PWR?\r"},
        "push": {
            "type": "tcp_listener",
            "port": 0,
            "register": "register",
            "frame_parser": frame_parser,
        },
    }


REFUSED = {
    "fixed_length of no bytes": (
        {"type": "fixed_length", "length": 0},
        ["push.frame_parser.length: must be a positive integer"],
    ),
    "fixed_length of fewer than none": (
        {"type": "fixed_length", "length": -2},
        ["push.frame_parser.length: must be a positive integer"],
    ),
    "length_prefix behind a negative header": (
        {"type": "length_prefix", "length_offset": -1},
        ["push.frame_parser.length_offset: must be a non-negative integer"],
    ),
    "length_prefix before a negative header": (
        {"type": "length_prefix", "header_extra": -1},
        ["push.frame_parser.header_extra: must be a non-negative integer"],
    ),
    "length_prefix of three bytes": (
        {"type": "length_prefix", "header_size": 3},
        ["push.frame_parser.header_size: must be 1, 2, or 4"],
    ),
    "length_prefix with every field wrong": (
        {"type": "length_prefix", "header_size": 3, "header_offset": "x",
         "length_offset": -1, "header_extra": -1, "length_endian": "middle"},
        [
            "push.frame_parser.header_size: must be 1, 2, or 4",
            "push.frame_parser.header_offset: must be an integer",
            "push.frame_parser.length_offset: must be a non-negative integer",
            "push.frame_parser.header_extra: must be a non-negative integer",
            "push.frame_parser.length_endian: must be 'big' or 'little'",
        ],
    ),
}


@pytest.mark.parametrize("frame_parser, sentences", REFUSED.values(), ids=list(REFUSED))
def test_the_authors_doors_refuse_it_once_per_field(frame_parser, sentences) -> None:
    verdict = check_driver_definition(_driver(frame_parser), strict=True)
    assert list(verdict.errors) == sentences


def test_each_sentence_lands_on_its_field() -> None:
    frame_parser = REFUSED["length_prefix with every field wrong"][0]
    paths = [
        issue["path"] for issue in validate_driver_issues(_driver(frame_parser), strict=True)
        if issue["severity"] == "error"
    ]
    assert paths == [
        "push.frame_parser.header_size",
        "push.frame_parser.header_offset",
        "push.frame_parser.length_offset",
        "push.frame_parser.header_extra",
        "push.frame_parser.length_endian",
    ]


@pytest.mark.parametrize("frame_parser, sentences", REFUSED.values(), ids=list(REFUSED))
def test_a_door_that_loads_it_names_it_and_loads(frame_parser, sentences) -> None:
    verdict = check_driver_definition(_driver(frame_parser), strict=False)
    assert list(verdict.errors) == []
    assert list(verdict.notices) == sentences


@pytest.mark.parametrize(
    "frame_parser",
    [
        {"type": "fixed_length", "length": 6},
        {"type": "length_prefix", "length_offset": 8, "header_size": 4, "header_extra": 4},
        {"type": "struct_frame", "header_reserve": 22, "length_adjust": -8},
    ],
)
def test_a_frame_parser_it_can_build_is_refused_nowhere(frame_parser) -> None:
    for strict in (True, False):
        verdict = check_driver_definition(_driver(frame_parser), strict=strict)
        assert list(verdict.errors) == []
        assert list(verdict.notices) == []


def test_a_struct_frame_still_refuses_at_every_door() -> None:
    frame_parser = {"type": "struct_frame", "header_reserve": -1}
    sentence = "push: frame_parser header_reserve must be a non-negative integer"
    assert list(check_driver_definition(_driver(frame_parser), strict=False).errors) == [sentence]
    assert list(check_driver_definition(_driver(frame_parser), strict=True).errors) == [sentence]


# --- the runtime, given one anyway ---------------------------------------------------


class _Listening:
    """Stands in for the listener registry: records what push asked for."""

    def __init__(self) -> None:
        self.factory: Any = "never asked"

    async def subscribe(self, *, frame_parser_factory, **_: Any):
        self.factory = frame_parser_factory

        class _Sub:
            port = 40000

            async def close(self) -> None:
                return None

        return _Sub()


async def _open_listener(monkeypatch, frame_parser: dict[str, Any]) -> _Listening:
    from openavc.core.event_bus import EventBus
    from openavc.core.state_store import StateStore
    from openavc.drivers.configurable import create_configurable_driver_class
    from openavc.transport import tcp_listener

    listening = _Listening()
    monkeypatch.setattr(tcp_listener, "subscribe", listening.subscribe)
    definition = _driver(frame_parser)
    definition["push"].pop("register")
    driver = create_configurable_driver_class(definition)(
        "w1", {"host": "127.0.0.1", "port": 23}, StateStore(), EventBus()
    )
    await driver._start_push_tcp_listener(definition["push"])
    return listening


async def test_the_runtime_says_once_that_the_pushes_are_read_unframed(
    monkeypatch, caplog,
) -> None:
    caplog.set_level(logging.WARNING)
    listening = await _open_listener(monkeypatch, {"type": "fixed_length", "length": 0})
    assert listening.factory is None
    said = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(said) == 1
    assert "length must be a positive integer (got 0)" in said[0]
    assert "unframed" in said[0]
    assert not [r for r in caplog.records if r.exc_info]


async def test_the_runtime_builds_a_fresh_parser_per_dial_back(monkeypatch) -> None:
    listening = await _open_listener(monkeypatch, {"type": "fixed_length", "length": 4})
    first, second = listening.factory(), listening.factory()
    assert first is not second
    assert first.feed(b"abcd") == [b"abcd"]
