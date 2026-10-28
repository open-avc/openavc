"""``then_query:``: a response rule that reads values again as soon as it has
applied.

Some devices announce that something changed without saying what it changed
to (a tie made at the front panel, a microphone back in range). These run a
real socket on loopback against an invented matrix that does that, and pin
the runtime's side: the follow-up queries go out at once (once per child for
``each_child``, to the child the rule wrote for ``{child_id}``), a rule that
its own follow-ups' replies match reads once and stops, a held poll holds the
follow-ups too, and a dropped connection cancels a round under way. The
auto-simulator answers the follow-up queries like any query.
"""

from __future__ import annotations

import asyncio
import copy
from typing import Any

import pytest

from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers import configurable
from openavc.drivers.avcdriver_semantic import validate_driver_definition
from openavc.drivers.compiled_protocol import follow_up_queries
from openavc.drivers.configurable import create_configurable_driver_class


def _acme(**extra: Any) -> dict[str, Any]:
    definition: dict[str, Any] = {
        "id": "acme_widget",
        "name": "Acme Widget",
        "manufacturer": "Acme",
        "category": "utility",
        "version": "1.0.0",
        "transport": "tcp",
        "delimiter": "\r",
        "default_config": {"poll_interval": 0},
        "state_variables": {
            "volume": {"type": "integer", "label": "Volume"},
            "standby": {"type": "boolean", "label": "Standby"},
        },
        "child_entity_types": {
            "output": {
                "label": "Output",
                "id_format": {"type": "integer", "min": 1, "max": 8},
                "instances": {"count": 3},
                "state_variables": {
                    "input": {"type": "integer", "label": "Input"},
                    "available": {"type": "boolean", "label": "Available"},
                    "name": {"type": "string", "label": "Name"},
                },
            },
        },
        "commands": {},
        "responses": [
            # A tie made at the front panel; it does not say which.
            {
                "match": r"^CHG$",
                "set": {},
                "then_query": [
                    {"each_child": "output", "send": "IN? {child_id}\r", "query_for": "input"},
                ],
            },
            {
                "match": r"^OUT(\d) IN(\d)$",
                "child_set": [{"type": "output", "id": "$1", "state": {"input": "$2"}}],
            },
            # An output back in service; its name may have changed meanwhile.
            {
                "match": r"^AVAIL (\d) YES$",
                "child_set": [{"type": "output", "id": "$1", "state": {"available": "true"}}],
                "then_query": [{"send": "NAME? {child_id}\r", "query_for": "name"}],
            },
            {
                "match": r"^NAME (\d) (.+)$",
                "child_set": [{"type": "output", "id": "$1", "state": {"name": "$2"}}],
            },
            # A notice the device also sends as the reply to its own query.
            {"match": r"^VOL (\d+)$", "set": {"volume": "$1"}, "then_query": ["VOL?\r"]},
            {"match": r"^STBY=1$", "set": {"standby": "true"}},
            {"match": r"^STBY=0$", "set": {"standby": "false"}},
        ],
        "polling": {"queries": [], "when_state": {"standby": False}},
    }
    definition.update(extra)
    return definition


def _build(definition: dict[str, Any], port: int) -> Any:
    assert validate_driver_definition(definition) == []
    cls = create_configurable_driver_class(definition)
    config = {"host": "127.0.0.1", "port": port, "poll_interval": 0}
    return cls(
        device_id="acme1", config=config, state=StateStore(), events=EventBus(),
    )


async def _until(predicate: Any, timeout: float) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return predicate()


@pytest.fixture
async def matrix():
    """An Acme matrix: answers IN?, NAME? and VOL?, records every line it
    receives, and can send a line of its own (``push``)."""
    dev: dict[str, Any] = {
        "ties": {1: 4, 2: 5, 3: 6},
        "names": {1: "Lectern", 2: "Wall", 3: "Ceiling"},
        "volume": 30,
        "lines": [],
        "writers": [],
    }

    async def handle(reader, writer):
        dev["writers"].append(writer)
        buf = b""
        try:
            while True:
                chunk = await reader.read(4096)
                if not chunk:
                    break
                buf += chunk
                while b"\r" in buf:
                    raw, buf = buf.split(b"\r", 1)
                    line = raw.decode()
                    dev["lines"].append(line)
                    reply = ""
                    if line.startswith("IN? "):
                        out = int(line[4:])
                        reply = f"OUT{out} IN{dev['ties'][out]}"
                    elif line.startswith("NAME? "):
                        out = int(line[6:])
                        reply = f"NAME {out} {dev['names'][out]}"
                    elif line == "VOL?":
                        reply = f"VOL {dev['volume']}"
                    if reply:
                        writer.write(reply.encode() + b"\r")
                        await writer.drain()
        except (ConnectionError, OSError):
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass

    async def push(text: str) -> None:
        writer = dev["writers"][-1]
        writer.write(text.encode() + b"\r")
        await writer.drain()

    dev["push"] = push
    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    yield port, dev
    server.close()
    await server.wait_closed()


def _inputs(drv: Any) -> list[Any]:
    return [drv.get_child_state("output", n).get("input") for n in (1, 2, 3)]


@pytest.mark.asyncio
async def test_a_notice_reads_every_child_at_once(matrix) -> None:
    """CHG says a tie changed and not which: every output's tie is read."""
    port, dev = matrix
    drv = _build(_acme(), port)
    await drv.connect()
    try:
        assert await _until(lambda: dev["writers"], 3.0)
        await dev["push"]("CHG")
        assert await _until(lambda: _inputs(drv) == [4, 5, 6], 3.0)
        assert dev["lines"] == ["IN? 1", "IN? 2", "IN? 3"]
    finally:
        await drv.disconnect()


@pytest.mark.asyncio
async def test_child_id_is_the_child_the_rule_wrote(matrix) -> None:
    """Two outputs back in service one after the other: each one's name is
    read, and only theirs. The guard is per child, so the second report is
    not held behind the first."""
    port, dev = matrix
    drv = _build(_acme(), port)
    await drv.connect()
    try:
        assert await _until(lambda: dev["writers"], 3.0)
        dev["names"][2] = "Stage Left"
        await dev["push"]("AVAIL 2 YES")
        await dev["push"]("AVAIL 3 YES")
        assert await _until(
            lambda: drv.get_child_state("output", 3).get("name") == "Ceiling", 3.0
        )
        assert drv.get_child_state("output", 2).get("name") == "Stage Left"
        assert drv.get_child_state("output", 2).get("available") is True
        assert dev["lines"] == ["NAME? 2", "NAME? 3"]
    finally:
        await drv.disconnect()


@pytest.mark.asyncio
async def test_a_rule_its_own_replies_match_reads_once(matrix, monkeypatch) -> None:
    """VOL 30 is both the notice and the reply to VOL?. The reply arrives
    inside the settle window and sends nothing; a notice after the window
    reads again."""
    monkeypatch.setattr(configurable, "FOLLOW_UP_SETTLE_S", 0.3)
    port, dev = matrix
    drv = _build(_acme(), port)
    await drv.connect()
    try:
        assert await _until(lambda: dev["writers"], 3.0)
        await dev["push"]("VOL 30")
        assert await _until(lambda: dev["lines"] == ["VOL?"], 3.0)
        await asyncio.sleep(0.6)
        assert dev["lines"] == ["VOL?"]
        assert drv.get_state("volume") == 30

        dev["volume"] = 31
        await dev["push"]("VOL 31")
        assert await _until(lambda: dev["lines"] == ["VOL?", "VOL?"], 3.0)
        await asyncio.sleep(0.6)
        assert dev["lines"] == ["VOL?", "VOL?"]
    finally:
        await drv.disconnect()


@pytest.mark.asyncio
async def test_a_held_poll_holds_the_follow_ups(matrix) -> None:
    """In standby (polling.when_state shut) a notice sends nothing: any line
    would wake the unit."""
    port, dev = matrix
    drv = _build(_acme(), port)
    await drv.connect()
    try:
        assert await _until(lambda: dev["writers"], 3.0)
        await dev["push"]("STBY=1")
        assert await _until(lambda: drv.get_state("standby") is True, 3.0)
        await dev["push"]("CHG")
        await asyncio.sleep(0.4)
        assert dev["lines"] == []
    finally:
        await drv.disconnect()


@pytest.mark.asyncio
async def test_a_disconnect_cancels_a_round_under_way(matrix) -> None:
    port, dev = matrix
    drv = _build(_acme(), port)
    await drv.connect()
    assert await _until(lambda: dev["writers"], 3.0)
    await dev["push"]("CHG")
    assert await _until(lambda: drv._follow_up_tasks, 3.0)
    await drv.disconnect()
    assert drv._follow_up_tasks == set()
    await asyncio.sleep(0.3)
    # The cancelled round leaves no hold for the next connection.
    assert drv._follow_up_until == {}
    assert len(dev["lines"]) < 3


@pytest.mark.asyncio
async def test_a_throttled_frame_sends_nothing() -> None:
    """A frame the throttle holds applies nothing yet, so it asks nothing."""
    definition = _acme()
    definition["responses"][4]["throttle"] = 5
    drv = create_configurable_driver_class(definition)(
        device_id="acme1", config={"poll_interval": 0}, state=StateStore(),
        events=EventBus(),
    )
    sent: list[Any] = []
    drv._follow_up = lambda *args: sent.append(args)  # type: ignore[method-assign]
    await drv.on_data_received(b"VOL 1")
    await drv.on_data_received(b"VOL 2")
    assert len(sent) == 1


# ---------------------------------------------------------------------------
# The auto-simulator answers the follow-up queries
# ---------------------------------------------------------------------------


def test_follow_up_queries_reach_the_simulator_as_poll_entries() -> None:
    entries = follow_up_queries(_acme())
    assert {"each_child": "output", "send": "IN? {child_id}\r", "query_for": "input"} in entries
    # {child_id} sent once names the child the rule wrote: the simulator
    # cannot know which, so it answers for every child of that type.
    assert {"each_child": "output", "send": "NAME? {child_id}\r", "query_for": "name"} in entries
    assert "VOL?\r" in entries


def test_the_simulator_answers_the_follow_up_queries() -> None:
    from openavc.simulator.yaml_auto import YAMLAutoSimulator

    sim = YAMLAutoSimulator("sim1", {}, driver_def=copy.deepcopy(_acme()))
    sim.set_state("output.2.input", 7)
    sim.set_state("output.3.name", "Booth")
    assert sim.handle_command(b"IN? 2\r") == b"OUT2 IN7\r"
    assert sim.handle_command(b"NAME? 3\r") == b"NAME 3 Booth\r"
