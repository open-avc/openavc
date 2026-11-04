"""``read_back:``: a command that reads its new value back as its own message.

Some devices answer a set with an acknowledgement that carries no value
("OK"), some never report a client's own change back to it, and some ask for
each reply before the next message. A command's ``read_back:`` queries go out
after the command's write, each as its own message, so the value is read at
once instead of at the next poll.

The TCP cases run a real socket on loopback against an invented display that
answers a set with ``OK`` and a get with the value, and pin the order on the
wire (the set, its reply, then the get), the spacing from the command, the
when_state hold, the coalescing of a burst of presses, the parameters a
named command is given, and a dropped connection. OSC and HTTP use the dry
run's recording transports: what matters there is what the read-back sends.
"""

from __future__ import annotations

import asyncio
import copy
from typing import Any

import pytest

from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.avcdriver_semantic import validate_driver_definition
from openavc.drivers.configurable import create_configurable_driver_class
from openavc.drivers.dry_run import capture_http_transport, capture_osc_transport


def _acme(**extra: Any) -> dict[str, Any]:
    definition: dict[str, Any] = {
        "id": "acme_widget",
        "name": "Acme Widget",
        "manufacturer": "Acme",
        "category": "utility",
        "version": "1.0.0",
        "transport": "tcp",
        "delimiter": "\r",
        "default_config": {"poll_interval": 0, "inter_command_delay": 0.1},
        "state_variables": {
            "volume": {"type": "integer", "label": "Volume"},
            "input": {"type": "string", "label": "Input"},
            "standby": {"type": "boolean", "label": "Standby"},
        },
        "commands": {
            "set_volume": {
                "label": "Set Volume",
                "send": "SV{level:03d}\r",
                "params": {"level": {"type": "integer", "required": True, "label": "Level"}},
                "read_back": "GV\r",
            },
            "set_input": {
                "label": "Set Input",
                "send": "SI{input}\r",
                "params": {
                    "input": {
                        "type": "enum", "required": True, "label": "Input",
                        "values": ["hdmi1", "hdmi2"],
                        "map": {"hdmi1": "1", "hdmi2": "2"},
                    },
                },
                # The wire form: {input} is what the command sent.
                "read_back": "GI{input}\r",
            },
            "get_input": {
                "label": "Get Input",
                "send": "GI{input}\r",
                "params": {
                    "input": {
                        "type": "enum", "required": True, "label": "Input",
                        "values": ["hdmi1", "hdmi2"],
                        "map": {"hdmi1": "1", "hdmi2": "2"},
                    },
                },
            },
            "select_input": {
                "label": "Select Input",
                "send": "SI{input}\r",
                "params": {
                    "input": {
                        "type": "enum", "required": True, "label": "Input",
                        "values": ["hdmi1", "hdmi2"],
                        "map": {"hdmi1": "1", "hdmi2": "2"},
                    },
                    "fade": {"type": "integer", "label": "Fade"},
                },
                # Named: runs get_input, given the parameters it declares.
                "read_back": "get_input",
            },
            "standby_on": {
                "label": "Standby On",
                "send": "STBY1\r",
                "read_back": ["GV\r"],
            },
        },
        "responses": [
            {"match": r"^VOL(\d+)$", "set": {"volume": "$1"}},
            {"match": r"^IN(\d)$", "set": {"input": "$1"}},
            {"match": r"^STBY=1$", "set": {"standby": "true"}},
            {"match": r"^STBY=0$", "set": {"standby": "false"}},
        ],
        "polling": {"queries": ["GV\r"], "when_state": {"standby": False}},
    }
    definition.update(extra)
    return definition


def _build(definition: dict[str, Any], port: int = 0, **config: Any) -> Any:
    assert validate_driver_definition(definition, strict=True) == []
    cls = create_configurable_driver_class(definition)
    # What the device manager hands a driver: its defaults under the
    # device's own settings.
    merged = {
        **(definition.get("default_config") or {}),
        "host": "127.0.0.1", "port": port, "poll_interval": 0, **config,
    }
    return cls(
        device_id="acme1", config=merged, state=StateStore(), events=EventBus(),
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
async def display():
    """An Acme display: answers a set with OK and no value (after
    ``reply_after`` seconds), a get with the value, and records each line it
    receives with the time it arrived and whether the reply to the line
    before it had already gone out."""
    loop = asyncio.get_running_loop()
    dev: dict[str, Any] = {
        "volume": 20, "inputs": {"1": "1", "2": "2"}, "input": "1",
        "lines": [], "times": [], "replied_before": [], "replied": True,
        "reply_after": 0.0, "writers": [],
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
                    dev["times"].append(loop.time())
                    dev["replied_before"].append(dev["replied"])
                    dev["replied"] = False
                    if dev["reply_after"]:
                        await asyncio.sleep(dev["reply_after"])
                    reply = "OK"
                    if line.startswith("SV"):
                        dev["volume"] = int(line[2:])
                    elif line == "GV":
                        reply = f"VOL{dev['volume']}"
                    elif line.startswith("SI"):
                        dev["input"] = line[2:]
                    elif line.startswith("GI"):
                        reply = f"IN{dev['input']}"
                    elif line == "STBY1":
                        reply = "STBY=1"
                    writer.write(reply.encode() + b"\r")
                    await writer.drain()
                    dev["replied"] = True
        except (ConnectionError, OSError):
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    yield port, dev
    server.close()
    await server.wait_closed()


# ---------------------------------------------------------------------------
# TCP: the order on the wire
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_value_is_read_after_the_sets_reply(display) -> None:
    """The set goes out, the device answers OK, and only then the get: the
    new value is in state within a reply, not at the next poll."""
    port, dev = display
    drv = _build(_acme(), port)
    await drv.connect()
    try:
        assert await _until(lambda: dev["writers"], 3.0)
        await drv.send_command("set_volume", {"level": 35})
        assert await _until(lambda: drv.get_state("volume") == 35, 3.0)
        assert dev["lines"] == ["SV035", "GV"]
        # The get reached the device after the set's reply had gone out.
        assert dev["replied_before"][1] is True
        # Spaced from the command by its inter_command_delay.
        assert dev["times"][1] - dev["times"][0] >= 0.1
    finally:
        await drv.disconnect()


@pytest.mark.asyncio
async def test_without_a_delay_the_get_keeps_a_lines_gap(display) -> None:
    """With no inter_command_delay the driver spaces its own lines; the
    command counts as the line before the first query."""
    port, dev = display
    definition = _acme()
    definition["default_config"].pop("inter_command_delay")
    drv = _build(definition, port)
    await drv.connect()
    try:
        assert await _until(lambda: dev["writers"], 3.0)
        await drv.send_command("set_volume", {"level": 12})
        assert await _until(lambda: drv.get_state("volume") == 12, 3.0)
        assert dev["lines"] == ["SV012", "GV"]
        assert dev["times"][1] - dev["times"][0] >= 0.04
    finally:
        await drv.disconnect()


@pytest.mark.asyncio
async def test_the_query_takes_the_commands_wire_values(display) -> None:
    """{input} in the read-back is what the command sent (after map:)."""
    port, dev = display
    drv = _build(_acme(), port)
    await drv.connect()
    try:
        assert await _until(lambda: dev["writers"], 3.0)
        await drv.send_command("set_input", {"input": "hdmi2"})
        assert await _until(lambda: drv.get_state("input") == "2", 3.0)
        assert dev["lines"] == ["SI2", "GI2"]
    finally:
        await drv.disconnect()


@pytest.mark.asyncio
async def test_a_named_command_gets_the_parameters_it_declares(display) -> None:
    """select_input reads back with get_input, which is given the input as
    the caller gave it (and maps it itself) and not the fade it does not
    declare."""
    port, dev = display
    drv = _build(_acme(), port)
    await drv.connect()
    try:
        assert await _until(lambda: dev["writers"], 3.0)
        await drv.send_command("select_input", {"input": "hdmi2", "fade": 3})
        assert await _until(lambda: drv.get_state("input") == "2", 3.0)
        assert dev["lines"] == ["SI2", "GI2"]
    finally:
        await drv.disconnect()


@pytest.mark.asyncio
async def test_the_caller_does_not_wait_for_the_read_back(display) -> None:
    """send_command returns once the command is sent; the read-back follows
    on its own."""
    port, dev = display
    dev["reply_after"] = 0.3
    drv = _build(_acme(), port)
    await drv.connect()
    try:
        assert await _until(lambda: dev["writers"], 3.0)
        await drv.send_command("set_volume", {"level": 40})
        assert drv._read_back_tasks
        assert dev["lines"] == ["SV040"]
        assert await _until(lambda: drv.get_state("volume") == 40, 3.0)
    finally:
        await drv.disconnect()


@pytest.mark.asyncio
async def test_a_burst_of_presses_reads_back_after_the_last(display) -> None:
    """A slider dragged through five values: the query waiting to go out is
    not queued again, and the one that goes out reads the last value."""
    port, dev = display
    drv = _build(_acme(), port)
    await drv.connect()
    try:
        assert await _until(lambda: dev["writers"], 3.0)
        for level in (1, 2, 3, 4, 5):
            await drv.send_command("set_volume", {"level": level})
        assert await _until(lambda: not drv._read_back_tasks, 3.0)
        assert await _until(lambda: drv.get_state("volume") == 5, 3.0)
        sets = [line for line in dev["lines"] if line.startswith("SV")]
        gets = [line for line in dev["lines"] if line == "GV"]
        assert sets == ["SV001", "SV002", "SV003", "SV004", "SV005"]
        assert 1 <= len(gets) < 5
        # The last line on the wire is a read-back, after the last set.
        assert dev["lines"][-1] == "GV"
    finally:
        await drv.disconnect()


@pytest.mark.asyncio
async def test_a_held_poll_holds_the_read_back(display) -> None:
    """Standby On's answer shuts polling.when_state: its read-back would
    wake the unit, so it is not sent."""
    port, dev = display
    drv = _build(_acme(), port)
    await drv.connect()
    try:
        assert await _until(lambda: dev["writers"], 3.0)
        await drv.send_command("standby_on")
        assert await _until(lambda: drv.get_state("standby") is True, 3.0)
        await asyncio.sleep(0.4)
        assert dev["lines"] == ["STBY1"]
    finally:
        await drv.disconnect()


@pytest.mark.asyncio
async def test_the_drivers_own_lines_read_nothing_back(display) -> None:
    """A poll entry that names a command runs it without its read-back."""
    port, dev = display
    definition = _acme()
    definition["polling"] = {"queries": ["set_volume"]}
    definition["commands"]["set_volume"]["params"]["level"]["required"] = False
    definition["commands"]["set_volume"]["params"]["level"]["default"] = 7
    drv = _build(definition, port)
    await drv.connect()
    try:
        assert await _until(lambda: dev["writers"], 3.0)
        await drv.poll()
        await asyncio.sleep(0.4)
        assert dev["lines"] == ["SV007"]
    finally:
        await drv.disconnect()


@pytest.mark.asyncio
async def test_a_disconnect_cancels_a_read_back_waiting(display) -> None:
    port, dev = display
    drv = _build(_acme(), port, inter_command_delay=0.5)
    await drv.connect()
    assert await _until(lambda: dev["writers"], 3.0)
    await drv.send_command("set_volume", {"level": 9})
    assert drv._read_back_tasks
    await drv.disconnect()
    assert drv._read_back_tasks == set()
    assert drv._read_back_waiting == set()
    await asyncio.sleep(0.3)
    assert dev["lines"] == ["SV009"]


# ---------------------------------------------------------------------------
# OSC and HTTP: what the read-back sends
# ---------------------------------------------------------------------------


def _osc_mixer(**extra: Any) -> dict[str, Any]:
    definition: dict[str, Any] = {
        "id": "acme_mixer",
        "name": "Acme Mixer",
        "transport": "osc",
        "default_config": {"poll_interval": 0},
        "child_entity_types": {
            "channel": {
                "label": "Channel",
                "id_format": {"type": "integer", "min": 1, "max": 8},
                "instances": {"count": 2},
                "state_variables": {"fader": {"type": "number", "label": "Fader"}},
            },
        },
        "commands": {
            "set_fader": {
                "label": "Set Fader",
                "address": "/ch/{channel:02d}/fader",
                "args": [{"type": "f", "value": "{level}"}],
                "params": {
                    "channel": {"type": "child_id", "child_type": "channel", "label": "Channel"},
                    "level": {"type": "number", "label": "Level"},
                },
                "read_back": True,
            },
            "recall_scene": {
                "label": "Recall Scene",
                "address": "/scene/go",
                "args": [{"type": "i", "value": "{scene}"}],
                "params": {"scene": {"type": "integer", "label": "Scene"}},
                "read_back": ["/scene/current", "/scene/name"],
            },
        },
        "responses": [],
    }
    definition.update(extra)
    return definition


async def _sent_osc(definition: dict[str, Any], command: str, params: dict[str, Any]) -> list[Any]:
    from openavc.transport.osc_codec import osc_decode_message

    drv = _build(definition)
    drv._pace_lines = False
    capture = capture_osc_transport()
    drv.transport = capture
    await drv.send_command(command, params)
    await asyncio.gather(*list(drv._read_back_tasks))
    return [osc_decode_message(frame) for frame in capture.frames]


@pytest.mark.asyncio
async def test_osc_true_reads_the_commands_own_address() -> None:
    sent = await _sent_osc(_osc_mixer(), "set_fader", {"channel": 3, "level": 0.5})
    assert sent == [("/ch/03/fader", [("f", 0.5)]), ("/ch/03/fader", [])]


@pytest.mark.asyncio
async def test_osc_addresses_are_sent_with_no_arguments() -> None:
    sent = await _sent_osc(_osc_mixer(), "recall_scene", {"scene": 4})
    assert sent == [
        ("/scene/go", [("i", 4)]), ("/scene/current", []), ("/scene/name", []),
    ]


def _http_camera(**extra: Any) -> dict[str, Any]:
    definition: dict[str, Any] = {
        "id": "acme_camera",
        "name": "Acme Camera",
        "transport": "http",
        "default_config": {"poll_interval": 0},
        "state_variables": {"tally": {"type": "boolean", "label": "Tally"}},
        "commands": {
            "set_tally": {
                "label": "Set Tally",
                "method": "POST",
                "path": "/api/v1/tally/{led}",
                "body": '{"state": {on}}',
                "query_params": {"save": "1"},
                "params": {
                    "led": {"type": "string", "label": "LED"},
                    "on": {"type": "boolean", "label": "On"},
                },
                "read_back": True,
            },
            "start_record": {
                "label": "Start Record",
                "method": "POST",
                "path": "/api/StartRecord",
                "read_back": "record_status",
            },
            "record_status": {
                "label": "Record Status",
                "method": "POST",
                "path": "/api/RecordStatus",
            },
            "switch": {
                "label": "Switch",
                "method": "POST",
                "path": "/command/channel",
                "body": "{channel}",
                "params": {"channel": {"type": "integer", "label": "Channel"}},
                "read_back": "/details/channel",
            },
        },
        "responses": [],
    }
    definition.update(extra)
    return definition


async def _sent_http(command: str, params: dict[str, Any]) -> list[str]:
    drv = _build(_http_camera())
    capture = capture_http_transport()
    drv.transport = capture
    await drv.send_command(command, params)
    await asyncio.gather(*list(drv._read_back_tasks))
    return [f"{r['method']} {r['target']}" for r in capture.requests]


@pytest.mark.asyncio
async def test_http_true_gets_the_commands_own_path() -> None:
    """Without its query_params: those may carry the value being set."""
    sent = await _sent_http("set_tally", {"led": "red", "on": True})
    assert sent == ["POST /api/v1/tally/red?save=1", "GET /api/v1/tally/red"]


@pytest.mark.asyncio
async def test_http_a_named_command_is_a_post_read() -> None:
    sent = await _sent_http("start_record", {})
    assert sent == ["POST /api/StartRecord", "POST /api/RecordStatus"]


@pytest.mark.asyncio
async def test_http_a_path_is_read_with_get() -> None:
    sent = await _sent_http("switch", {"channel": 12})
    assert sent == ["POST /command/channel", "GET /details/channel"]


# ---------------------------------------------------------------------------
# The contract: an enhancement, and what the validator refuses
# ---------------------------------------------------------------------------


def test_read_back_does_not_raise_the_floor() -> None:
    from openavc.drivers.spec import platform_requirements

    with_it = platform_requirements(_acme())
    without = copy.deepcopy(_acme())
    for cmd in without["commands"].values():
        cmd.pop("read_back", None)
    assert with_it == platform_requirements(without)


# ---------------------------------------------------------------------------
# The Driver Builder's dry run shows the read-back after the command
# ---------------------------------------------------------------------------


def _dry_request(definition: dict[str, Any], command: str, params: dict[str, Any]) -> Any:
    from openavc.api.models import TestCommandRequest

    return TestCommandRequest(
        host="192.0.2.10", port=4001, transport=definition["transport"],
        definition=definition, command_name=command, params=params, dry_run=True,
    )


@pytest.mark.asyncio
async def test_dry_run_lists_the_read_back_after_the_wire() -> None:
    from openavc.api.routes.driver_test import _dry_run_command

    result = await _dry_run_command(_dry_request(_acme(), "set_input", {"input": "hdmi2"}))
    assert result["success"], result
    assert result["wire"] == "SI2\r"
    assert result["read_back"] == [{"wire": "GI2\r", "wire_hex": b"GI2\r".hex()}]


@pytest.mark.asyncio
async def test_dry_run_osc_read_back_is_the_bare_address() -> None:
    from openavc.api.routes.driver_test import _dry_run_command

    result = await _dry_run_command(
        _dry_request(_osc_mixer(), "set_fader", {"channel": 2, "level": 0.25})
    )
    assert result["success"], result
    assert result["wire"] == "/ch/02/fader [f=0.25]"
    assert [r["wire"] for r in result["read_back"]] == ["/ch/02/fader"]


@pytest.mark.asyncio
async def test_dry_run_http_read_back_is_its_own_request() -> None:
    from openavc.api.routes.driver_test import _dry_run_command

    result = await _dry_run_command(
        _dry_request(_http_camera(), "start_record", {})
    )
    assert result["success"], result
    assert result["wire"] == "POST /api/StartRecord"
    assert [r["wire"] for r in result["read_back"]] == ["POST /api/RecordStatus"]


@pytest.mark.asyncio
async def test_dry_run_of_a_command_with_no_read_back_lists_none() -> None:
    from openavc.api.routes.driver_test import _dry_run_command

    result = await _dry_run_command(
        _dry_request(_acme(), "get_input", {"input": "hdmi1"})
    )
    assert result["wire"] == "GI1\r"
    assert result["read_back"] == []


# ---------------------------------------------------------------------------
# The auto-simulator answers the read-back queries
# ---------------------------------------------------------------------------


def _routed(**extra: Any) -> dict[str, Any]:
    """An Acme matrix whose route command reads the output back, and whose
    volume read-back is not in the poll."""
    definition = _acme(**extra)
    definition["polling"] = {"queries": []}
    definition["child_entity_types"] = {
        "output": {
            "label": "Output",
            "id_format": {"type": "integer", "min": 1, "max": 8},
            "instances": {"count": 2},
            "state_variables": {"input": {"type": "integer", "label": "Input"}},
        },
    }
    definition["commands"]["route"] = {
        "label": "Route",
        "send": "R{output:02d}={input}\r",
        "params": {
            "output": {"type": "child_id", "child_type": "output", "label": "Output"},
            "input": {"type": "integer", "label": "Input"},
        },
        "sets": {"input": "{input}"},
        "read_back": "R{output:02d}?\r",
    }
    definition["commands"]["set_volume"]["sets"] = {"volume": "{level}"}
    definition["responses"].append({
        "match": r"^OUT(\d+) IN(\d+)$",
        "child_set": [{"type": "output", "id": "$1", "state": {"input": "$2"}}],
    })
    return definition


def test_read_back_queries_reach_the_simulator_as_poll_entries() -> None:
    from openavc.drivers.compiled_protocol import read_back_queries

    entries = read_back_queries(_routed())
    # What the command sets is what the query reports.
    assert {"send": "GV\r", "query_for": "volume"} in entries
    # Filled from the child_id parameter: answered for every child.
    assert {
        "each_child": "output", "send": "R{child_id:02d}?\r", "query_for": "input",
    } in entries
    # Filled from another parameter: cannot be enumerated ahead.
    assert not any("GI" in str(e) for e in entries)
    # Naming a command: the simulator answers that command.
    assert "get_input" not in entries


def test_a_read_back_the_poll_already_sends_is_left_out() -> None:
    from openavc.drivers.compiled_protocol import read_back_queries

    entries = read_back_queries(_acme())
    assert not any("GV" in str(e) for e in entries)


def test_the_simulator_answers_the_read_back() -> None:
    from openavc.simulator.yaml_auto import YAMLAutoSimulator

    definition = _routed()
    sim = YAMLAutoSimulator("sim1", {}, driver_def=copy.deepcopy(definition))
    sim.handle_command(b"SV042\r")
    assert sim.handle_command(b"GV\r") == b"VOL42\r"
    sim.handle_command(b"R02=5\r")
    assert sim.handle_command(b"R02?\r") == b"OUT2 IN5\r"
