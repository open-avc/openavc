"""Driver <-> simulator parity over a real loopback socket.

The same driver definition powers both sides here: a real ConfigurableDriver
connects over TCP to the auto-generated simulator built from the identical
definition, with no stubs in between. Every test drives the full loop —
command -> framed wire -> simulator dispatch -> simulated state change ->
reply -> driver response matching -> state store — and the closing assertion
is always parity: the driver's view of a variable equals the simulator's.

Invented devices only (platform-feature tests). The two fixtures split the
corpus idioms between them:

  acme_display  terminated protocol (CRLF), command_suffix framing, declared
                sets/query_for with a hex format spec, boolean value maps,
                terminated poll queries (string + mapping form)
  acme_matrix   unterminated protocol (bare wires, no trailing CR),
                command_prefix framing, declared literal sets, bare poll
                queries answered through the QueryHandler path, on_connect
                query_for
"""

import asyncio

import pytest

from server.core.event_bus import EventBus
from server.core.state_store import StateStore
from server.drivers.configurable import create_configurable_driver_class
from server.drivers.driver_loader import validate_driver_definition
from simulator.yaml_auto import YAMLAutoSimulator


def _display_def() -> dict:
    return {
        "id": "acme_display",
        "name": "Acme Display",
        "manufacturer": "Acme",
        "category": "display",
        "version": "1.0.0",
        "transport": "tcp",
        "delimiter": "\r\n",
        "command_suffix": "\r\n",
        "default_config": {"host": "", "port": 4999},
        "config_schema": {
            "host": {"type": "string", "required": True, "label": "IP Address"},
            "port": {"type": "integer", "default": 4999, "label": "Port"},
        },
        "state_variables": {
            "power": {"type": "boolean", "label": "Power"},
            "input": {"type": "integer", "label": "Input"},
            # Hex-code protocol idiom: the 2-digit wire code IS the state
            # value, so the variable is a string and keeps the wire form.
            "volume": {"type": "string", "label": "Volume Code"},
            "mute": {"type": "boolean", "label": "Mute"},
        },
        "commands": {
            "power_on": {"send": "PWR ON"},
            "power_off": {"send": "PWR OFF"},
            "set_input": {
                "send": "INP{input}",
                "params": {"input": {"type": "integer", "min": 1, "max": 8, "required": True}},
            },
            "get_input": {"send": "INP?"},
            "set_volume": {
                "send": "VOL{level:02X}",
                "params": {"level": {"type": "integer", "min": 0, "max": 255, "required": True}},
                "sets": {"volume": "{level}"},
                "query_for": "volume",
            },
            "mute_on": {"send": "MUTE 1"},
            "mute_off": {"send": "MUTE 0"},
        },
        "responses": [
            {"match": "^PWR ON$", "set": {"power": True}},
            {"match": "^PWR OFF$", "set": {"power": False}},
            {"match": r"^SRC(\d+)$", "set": {"input": "$1"}},
            {"match": "^VOL([0-9A-F]{2})$", "set": {"volume": "$1"}},
            {"match": "^MUTE ON$", "set": {"mute": True}},
            {"match": "^MUTE OFF$", "set": {"mute": False}},
        ],
        "polling": {
            "queries": [
                # Answered via the command-handler path: same send as the
                # param-free get_input command (the framed wire matches its
                # inverted pattern).
                "INP?\r\n",
                # Answered via the QueryHandler path: mapping form declares
                # what the reply reports. The query text carries its own
                # terminator, exactly like the corpus majority — the handler
                # pattern must match the stripped line it arrives as.
                {"send": "PWR ?\r\n", "query_for": "power"},
                {"send": "VOL ?\r\n", "query_for": "volume"},
                {"send": "MUTE ?\r\n", "query_for": "mute"},
            ],
        },
    }


def _matrix_def() -> dict:
    return {
        "id": "acme_matrix",
        "name": "Acme Matrix",
        "manufacturer": "Acme",
        "category": "switcher",
        "version": "1.0.0",
        "transport": "tcp",
        "delimiter": "\r",
        "command_prefix": "*",
        "default_config": {
            "host": "",
            "port": 5000,
            # Bare unterminated wires ride the simulator's quiet-window
            # flush; pacing keeps two bare queries from landing in the same
            # window and merging into one unparseable line — the same
            # setting an unterminated protocol needs against real hardware
            # that parses on inter-character timeout.
            "inter_command_delay": 0.4,
        },
        "config_schema": {
            "host": {"type": "string", "required": True, "label": "IP Address"},
            "port": {"type": "integer", "default": 5000, "label": "Port"},
        },
        "state_variables": {
            "route": {"type": "integer", "label": "Route"},
            "locked": {"type": "boolean", "label": "Front Panel Lock"},
            "label": {"type": "string", "label": "Unit Label"},
        },
        "commands": {
            # No terminators anywhere: SIS-style protocol.
            "set_route": {
                "send": "R{route}",
                "params": {"route": {"type": "integer", "min": 1, "max": 16, "required": True}},
            },
            "get_route": {"send": "R?"},
            "lock": {"send": "LK1", "sets": {"locked": True}, "query_for": "locked"},
            "unlock": {"send": "LK0", "sets": {"locked": False}, "query_for": "locked"},
        },
        "responses": [
            {"match": r"^ROUTE (\d+)$", "set": {"route": "$1"}},
            {"match": "^LOCKED$", "set": {"locked": True}},
            {"match": "^UNLOCKED$", "set": {"locked": False}},
            {"match": "^LABEL:(.+)$", "set": {"label": "$1"}},
        ],
        "polling": {
            "queries": [
                # Bare query. The driver sends it raw (no prefix, no
                # terminator); the framed command pattern for get_route
                # carries the "*" prefix so this line falls through to the
                # QueryHandler built from the same send text.
                "R?",
                {"send": "L?", "query_for": "locked"},
            ],
        },
        "on_connect": [
            {"send": "N?", "query_for": "label"},
        ],
    }


def _make_driver(definition: dict, port: int, device_id: str = "dev1"):
    cls = create_configurable_driver_class(definition)
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    config = dict(definition.get("default_config") or {})
    config.update({"host": "127.0.0.1", "port": port, "poll_interval": 0})
    return cls(device_id, config, state, events)


async def _start_sim(definition: dict) -> tuple[YAMLAutoSimulator, int]:
    sim = YAMLAutoSimulator(definition["id"], config={}, driver_def=definition)
    await sim.start(0)
    port = sim._server.sockets[0].getsockname()[1]
    return sim, port


async def _wait_for(predicate, timeout: float = 3.0) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition not met within timeout")


def _assert_parity(drv, sim, var_names) -> None:
    """The core property: both sides agree on every exercised variable."""
    for name in var_names:
        assert drv.get_state(name) == sim.get_state(name), (
            f"parity broken for {name!r}: "
            f"driver={drv.get_state(name)!r} sim={sim.get_state(name)!r}"
        )


@pytest.mark.parametrize("definition", [_display_def(), _matrix_def()])
def test_fixture_definitions_are_valid(definition):
    assert validate_driver_definition(definition) == []


# ===========================================================================
# Terminated protocol (acme_display)
# ===========================================================================


async def test_display_command_round_trips():
    definition = _display_def()
    sim, port = await _start_sim(definition)
    drv = _make_driver(definition, port)
    try:
        await drv.connect()

        await drv.send_command("power_on")
        await _wait_for(lambda: drv.get_state("power") is True)
        assert sim.get_state("power") is True

        await drv.send_command("set_input", {"input": 3})
        await _wait_for(lambda: drv.get_state("input") == 3)
        assert sim.get_state("input") == 3

        # Declared sets with a hex format spec on a string variable: the
        # 2-digit wire code round-trips verbatim on both sides.
        await drv.send_command("set_volume", {"level": 26})
        await _wait_for(lambda: drv.get_state("volume") == "1A")
        assert sim.get_state("volume") == "1A"

        await drv.send_command("mute_on")
        await _wait_for(lambda: drv.get_state("mute") is True)

        _assert_parity(drv, sim, ["power", "input", "volume", "mute"])
    finally:
        await drv.disconnect()
        await sim.stop()


async def test_display_poll_reports_simulator_state():
    definition = _display_def()
    sim, port = await _start_sim(definition)
    # Simulated device state the poll cycle must carry across the wire.
    sim.set_state("power", True)
    sim.set_state("input", 7)
    sim.set_state("volume", "3F")
    sim.set_state("mute", False)
    drv = _make_driver(definition, port)
    try:
        await drv.connect()
        await drv.poll()
        await _wait_for(
            lambda: drv.get_state("input") == 7 and drv.get_state("volume") == "3F"
        )
        await _wait_for(
            lambda: drv.get_state("power") is True and drv.get_state("mute") is False
        )
        _assert_parity(drv, sim, ["power", "input", "volume", "mute"])
    finally:
        await drv.disconnect()
        await sim.stop()


async def test_display_fragmented_command_still_one_message():
    """A line split across TCP writes inside the quiet window coalesces."""
    definition = _display_def()
    sim, port = await _start_sim(definition)
    sim.set_state("input", 5)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b"INP")
        await writer.drain()
        await asyncio.sleep(0.05)
        writer.write(b"?\r\n")
        await writer.drain()
        data = await asyncio.wait_for(reader.read(256), timeout=2.0)
        assert data == b"SRC5\r\n"
        writer.close()
    finally:
        await sim.stop()


# ===========================================================================
# Unterminated protocol (acme_matrix)
# ===========================================================================


async def test_matrix_on_connect_query_populates_state():
    definition = _matrix_def()
    sim, port = await _start_sim(definition)
    sim.set_state("label", "Main Rack")
    drv = _make_driver(definition, port)
    try:
        # connect() runs the on_connect query itself; the bare unterminated
        # wire dispatches via the simulator's quiet-window flush.
        await drv.connect()
        await _wait_for(lambda: drv.get_state("label") == "Main Rack")
        _assert_parity(drv, sim, ["label"])
    finally:
        await drv.disconnect()
        await sim.stop()


async def test_matrix_command_round_trips():
    definition = _matrix_def()
    sim, port = await _start_sim(definition)
    drv = _make_driver(definition, port)
    try:
        await drv.connect()

        # Prefixed, unterminated command wire ("*R4").
        await drv.send_command("set_route", {"route": 4})
        await _wait_for(lambda: drv.get_state("route") == 4)
        assert sim.get_state("route") == 4

        # Declared literal sets coerce to the variable's type on the sim
        # side and come back through the boolean value map.
        await drv.send_command("lock")
        await _wait_for(lambda: drv.get_state("locked") is True)
        assert sim.get_state("locked") is True

        await drv.send_command("unlock")
        await _wait_for(lambda: drv.get_state("locked") is False)

        _assert_parity(drv, sim, ["route", "locked"])
    finally:
        await drv.disconnect()
        await sim.stop()


async def test_matrix_bare_poll_queries_answered():
    definition = _matrix_def()
    sim, port = await _start_sim(definition)
    sim.set_state("route", 12)
    sim.set_state("locked", True)
    drv = _make_driver(definition, port)
    try:
        await drv.connect()
        await drv.poll()
        await _wait_for(
            lambda: drv.get_state("route") == 12 and drv.get_state("locked") is True
        )
        _assert_parity(drv, sim, ["route", "locked"])
    finally:
        await drv.disconnect()
        await sim.stop()


# ===========================================================================
# Query-handler wire-form normalization (table-level pins)
# ===========================================================================


def test_query_handler_pattern_matches_stripped_line():
    """Handlers built from terminated / escape-text query entries must match
    the line the wire actually arrives as (terminators stripped)."""
    definition = _display_def()
    sim = YAMLAutoSimulator("acme_display", config={}, driver_def=definition)
    matched = [h.pattern.pattern for h in sim._query_handlers]
    assert any(h.pattern.match("PWR ?") for h in sim._query_handlers), matched
    assert any(h.pattern.match("VOL ?") for h in sim._query_handlers), matched
    # Escape-sequence spelling of the same terminator normalizes identically
    # (single-quoted YAML authors "\r" as two characters).
    escaped = _display_def()
    escaped["polling"]["queries"] = [{"send": "PWR ?\\r\\n", "query_for": "power"}]
    sim2 = YAMLAutoSimulator("acme_display", config={}, driver_def=escaped)
    assert any(h.pattern.match("PWR ?") for h in sim2._query_handlers)


def test_query_handler_substitutes_config_placeholders():
    definition = _matrix_def()
    definition["default_config"]["unit_id"] = 2
    definition["polling"]["queries"] = [{"send": "U{unit_id}R?", "query_for": "route"}]
    sim = YAMLAutoSimulator("acme_matrix", config={}, driver_def=definition)
    assert any(h.pattern.match("U2R?") for h in sim._query_handlers)
