"""What a generated simulator does with a datagram beside its stream
transport, how it starts its servers on one port number, and which children
it holds, pinned as they behave today.

The side receiver: a TCP or HTTP device with ``udp:`` commands also listens
for datagrams on its own port number. ``start(0)`` promises one number both
servers answer on, so the side that refuses a number picks the next one, up
to five times; a number the caller named gets one try. A Wake-on-LAN magic
packet applies the ``sets`` of every magic-packet command; any other datagram
runs through the command pipeline, its reply discarded.

The child roster: a child type's ``instances:`` block read against this
instance's config (a literal list, a count, a count from a config field, a
comma list from a config field), the dotted keys a ``simulator:`` section
seeds, and the project's children delivered after construction, each child
seeded with its variables' defaults where nothing is held yet.

The server tests replace the servers on the instance with recorders, so they
check the order of calls and the error that escapes, not sockets.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from openavc.simulator.yaml_auto import YAMLAutoSimulator

MAGIC = b"\xff" * 6 + bytes.fromhex("aabbccddeeff") * 16


def _definition(transport: str = "tcp", commands: dict | None = None, **extra) -> dict:
    definition = {
        "id": "acme_widget",
        "name": "Acme Widget",
        "transport": transport,
        "delimiter": "\r",
        "default_config": {"port": 4000},
        "state_variables": {
            "power": {"type": "boolean", "label": "Power"},
            "input": {"type": "string", "label": "Input"},
            "level": {"type": "integer", "label": "Level"},
        },
        "commands": commands if commands is not None else {
            "wake": {"label": "Wake", "udp": {"magic_packet": "mac"},
                     "sets": {"power": True}},
            "pwr_on": {"label": "On", "send": "PWR 1\r", "sets": {"power": True}},
        },
    }
    definition.update(extra)
    return definition


def _sim(definition: dict, config: dict | None = None) -> YAMLAutoSimulator:
    return YAMLAutoSimulator("w1", config or {}, driver_def=definition)


# ── start(): the servers, in the order they are asked ──


def _refused(port: int) -> OSError:
    return OSError(10013, f"refused {port}")


class _Servers:
    """Stand-in servers on one simulator: each start and stop is recorded in
    one list, and a start refuses when the script says so for that call."""

    def __init__(self, sim: YAMLAutoSimulator, refuse: dict[str, set[int]] | None = None):
        self.sim = sim
        self.calls: list[tuple] = []
        self.refuse = refuse or {}
        self.counts = {"stream": 0, "datagram": 0}
        self.next_pick = 40000
        sim._open_push_channels = lambda: self.calls.append(("push_open",))
        sim._close_push_channels = lambda: self.calls.append(("push_close",))
        sim._start_stream_server = self._starter("stream")
        sim._stop_stream_server = self._stopper("stream")
        sim.start_datagram_server = self._starter("datagram")
        sim.stop_datagram_server = self._stopper("datagram")

    def _starter(self, side: str):
        async def start(port: int) -> None:
            self.counts[side] += 1
            self.calls.append((side, port))
            if self.counts[side] in self.refuse.get(side, set()):
                raise _refused(port)
            if port == 0:
                self.next_pick += 1
                port = self.next_pick
            self.sim._port = port
        return start

    def _stopper(self, side: str):
        async def stop() -> None:
            self.calls.append((f"stop_{side}",))
        return stop


def _start(sim: YAMLAutoSimulator, port: int) -> None:
    asyncio.run(sim.start(port))


def test_a_stream_device_without_udp_commands_starts_the_stream_server_alone(caplog):
    sim = _sim(_definition(commands={"pwr_on": {"label": "On", "send": "PWR 1\r"}}))
    servers = _Servers(sim)
    with caplog.at_level(logging.INFO, logger="openavc.simulator.yaml_auto"):
        _start(sim, 0)
    assert servers.calls == [("push_open",), ("stream", 0)]
    assert "side-sends" not in caplog.text


@pytest.mark.parametrize("transport", ["udp", "osc"])
def test_a_datagram_device_starts_the_datagram_server_on_the_number_asked(transport):
    sim = _sim(_definition(transport=transport))
    servers = _Servers(sim)
    _start(sim, 0)
    assert servers.calls == [("push_open",), ("datagram", 0)]


def test_start_0_puts_the_side_receiver_on_the_number_the_stream_server_picked(caplog):
    sim = _sim(_definition())
    servers = _Servers(sim)
    with caplog.at_level(logging.INFO, logger="openavc.simulator.yaml_auto"):
        _start(sim, 0)
    assert servers.calls == [("push_open",), ("stream", 0), ("datagram", 40001)]
    assert sim.port == 40001
    assert [r.getMessage() for r in caplog.records if "side-sends" in r.getMessage()] == [
        f"{sim.name} also receives UDP side-sends on port 40001"
    ]


def test_a_named_port_is_tried_once_and_the_refusal_escapes_with_the_stream_server_up():
    sim = _sim(_definition())
    servers = _Servers(sim, refuse={"datagram": {1}})
    with pytest.raises(OSError, match="refused 4100"):
        _start(sim, 4100)
    assert servers.calls == [("push_open",), ("stream", 4100), ("datagram", 4100)]


def test_start_0_alternates_the_side_that_picks_and_gives_up_after_five_tries():
    sim = _sim(_definition())
    servers = _Servers(sim, refuse={"datagram": {1, 3, 5}, "stream": {2, 4}})
    with pytest.raises(OSError, match="refused 40005"):
        _start(sim, 0)
    # The last refusal escapes with the fifth pick's stream server still up.
    assert servers.calls == [
        ("push_open",),
        ("stream", 0), ("datagram", 40001), ("stop_stream",),
        ("datagram", 0), ("stream", 40002), ("stop_datagram",),
        ("stream", 0), ("datagram", 40003), ("stop_stream",),
        ("datagram", 0), ("stream", 40004), ("stop_datagram",),
        ("stream", 0), ("datagram", 40005),
    ]


def test_start_0_succeeds_on_its_fifth_try(caplog):
    sim = _sim(_definition())
    servers = _Servers(sim, refuse={"datagram": {1, 3}, "stream": {2, 4}})
    with caplog.at_level(logging.INFO, logger="openavc.simulator.yaml_auto"):
        _start(sim, 0)
    assert servers.calls[-3:] == [("stop_datagram",), ("stream", 0), ("datagram", 40005)]
    assert sim.port == 40005
    assert sum(1 for c in servers.calls if c[0] in ("stream", "datagram")) == 10
    assert f"{sim.name} also receives UDP side-sends on port 40005" in caplog.text


def test_a_refused_pick_of_the_first_server_escapes_at_once():
    sim = _sim(_definition())
    servers = _Servers(sim, refuse={"stream": {1}})
    with pytest.raises(OSError, match="refused 0"):
        _start(sim, 0)
    assert servers.calls == [("push_open",), ("stream", 0)]


def test_a_refused_pick_of_the_datagram_side_escapes_at_once():
    sim = _sim(_definition())
    servers = _Servers(sim, refuse={"datagram": {1, 2}})
    with pytest.raises(OSError, match="refused 0"):
        _start(sim, 0)
    assert servers.calls == [
        ("push_open",), ("stream", 0), ("datagram", 40001), ("stop_stream",), ("datagram", 0),
    ]


def test_start_0_succeeds_on_the_datagram_side_and_says_so(caplog):
    sim = _sim(_definition())
    servers = _Servers(sim, refuse={"datagram": {1}})
    with caplog.at_level(logging.INFO, logger="openavc.simulator.yaml_auto"):
        _start(sim, 0)
    assert servers.calls[-2:] == [("datagram", 0), ("stream", 40002)]
    assert sim.port == 40002
    assert f"{sim.name} also receives UDP side-sends on port 40002" in caplog.text


def test_an_error_other_than_a_refusal_is_not_retried():
    sim = _sim(_definition())
    servers = _Servers(sim)

    async def broken(port: int) -> None:
        servers.calls.append(("datagram", port))
        raise RuntimeError("not a refusal")

    sim.start_datagram_server = broken
    with pytest.raises(RuntimeError):
        _start(sim, 0)
    assert servers.calls == [("push_open",), ("stream", 0), ("datagram", 40001)]


def test_an_http_device_runs_the_http_server_as_its_stream_server():
    sim = _sim(_definition(transport="http"))
    calls: list[tuple] = []

    async def start_http(port: int) -> None:
        calls.append(("http", port))
        sim._port = 41000

    async def stop_http() -> None:
        calls.append(("stop_http",))

    async def start_datagram(port: int) -> None:
        calls.append(("datagram", port))

    async def stop_datagram() -> None:
        calls.append(("stop_datagram",))

    sim.start_http_server = start_http
    sim.stop_http_server = stop_http
    sim.start_datagram_server = start_datagram
    sim.stop_datagram_server = stop_datagram
    asyncio.run(sim.start(0))
    asyncio.run(sim.stop())
    assert calls == [("http", 0), ("datagram", 41000), ("stop_datagram",), ("stop_http",)]


def test_stop_closes_the_side_receiver_before_the_stream_server():
    sim = _sim(_definition())
    servers = _Servers(sim)
    stopped: list[str] = []

    async def stop_tcp(self) -> None:
        stopped.append("tcp")

    from openavc.simulator.tcp_simulator import TCPSimulator
    original = TCPSimulator.stop
    TCPSimulator.stop = stop_tcp
    try:
        asyncio.run(sim.stop())
    finally:
        TCPSimulator.stop = original
    assert servers.calls == [("push_close",), ("stop_datagram",)]
    assert stopped == ["tcp"]


# ── A datagram beside the stream transport ──


def _side_sim(commands: dict) -> tuple[YAMLAutoSimulator, list[bytes]]:
    sim = _sim(_definition(commands=commands))
    handled: list[bytes] = []
    real = sim.handle_command

    def record(data: bytes):
        handled.append(data)
        return real(data)

    sim.handle_command = record
    return sim, handled


def test_a_magic_packet_applies_the_sets_of_every_magic_packet_command_in_order():
    sim, handled = _side_sim({
        "wake": {"label": "Wake", "udp": {"magic_packet": "mac"},
                 "sets": {"power": True, "input": "hdmi1"}},
        "ping": {"label": "Ping", "udp": {"payload": "PING", "port": 9}, "sets": {"level": 9}},
        "wake2": {"label": "Wake 2", "udp": {"magic_packet": "mac"},
                  "sets": {"input": "{source}", "level": 7}},
    })
    order: list[tuple] = []
    real_set = sim.set_state
    sim.set_state = lambda key, value: (order.append((key, value)), real_set(key, value))
    sim._apply_side_datagram(MAGIC)
    assert order == [("power", True), ("input", "hdmi1"), ("level", 7)]
    assert handled == []


@pytest.mark.parametrize("value, kept", [
    ("{source}", False),
    ("{a}{b}", True),
    ("x{a}", True),
    ("{}", True),
    (" {a}", True),
    (5, True),
    (None, True),
])
def test_a_magic_packet_skips_a_value_that_is_one_placeholder(value, kept):
    sim, _ = _side_sim({
        "wake": {"label": "Wake", "udp": {"magic_packet": "mac"}, "sets": {"input": value}},
    })
    sim._state["input"] = "before"
    sim._apply_side_datagram(MAGIC)
    assert sim._state["input"] == (value if kept else "before")


@pytest.mark.parametrize("sets", [None, ["power"], "power"])
def test_a_magic_packet_command_whose_sets_are_not_a_mapping_sets_nothing(sets):
    sim, handled = _side_sim({
        "wake": {"label": "Wake", "udp": {"magic_packet": "mac"}, "sets": sets},
    })
    before = dict(sim._state)
    sim._apply_side_datagram(MAGIC)
    assert sim._state == before and handled == []


def test_a_magic_packet_with_no_magic_packet_command_sets_nothing_and_is_not_a_command():
    sim, handled = _side_sim({
        "ping": {"label": "Ping", "udp": {"payload": "PING", "port": 9}, "sets": {"level": 9}},
    })
    before = dict(sim._state)
    sim._apply_side_datagram(MAGIC + b"tail")
    assert sim._state == before and handled == []


def test_a_magic_packet_with_a_falsy_magic_packet_field_is_not_that_command():
    sim, _ = _side_sim({
        "wake": {"label": "Wake", "udp": {"magic_packet": ""}, "sets": {"level": 3}},
    })
    sim._apply_side_datagram(MAGIC)
    assert sim._state["level"] == 0


def test_a_magic_packet_reads_the_commands_section_as_the_definition_holds_it_now():
    sim, _ = _side_sim({
        "wake": {"label": "Wake", "udp": {"magic_packet": "mac"}, "sets": {"level": 3}},
    })
    sim._driver_def["commands"]["wake"]["sets"] = {"level": 4}
    sim._apply_side_datagram(MAGIC)
    assert sim._state["level"] == 4
    sim._driver_def["commands"] = None
    sim._state["level"] = 0
    sim._apply_side_datagram(MAGIC)
    assert sim._state["level"] == 0


@pytest.mark.parametrize("data", [
    MAGIC[:101],
    b"\xff" * 5 + b"\x00" + MAGIC[6:],
    b"PWR 1\r",
])
def test_anything_short_of_a_magic_packet_runs_as_a_command(data):
    sim, handled = _side_sim({
        "wake": {"label": "Wake", "udp": {"magic_packet": "mac"}, "sets": {"level": 3}},
        "pwr_on": {"label": "On", "send": "PWR 1\r", "sets": {"power": True}},
    })
    sim._apply_side_datagram(data)
    assert handled == [data]
    assert sim._state["level"] == 0
    assert sim._state["power"] is (data == b"PWR 1\r")


def test_a_side_send_that_raises_in_the_pipeline_is_logged_not_raised(caplog):
    sim, _ = _side_sim({"wake": {"label": "Wake", "udp": {"magic_packet": "mac"}}})

    def boom(data: bytes):
        raise ValueError("bad line")

    sim.handle_command = boom
    with caplog.at_level(logging.ERROR, logger="openavc.simulator.yaml_auto"):
        sim._apply_side_datagram(b"junk")
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert [r.getMessage() for r in errors] == [f"{sim.name}: error applying UDP side-send"]
    assert errors[0].exc_info[0] is ValueError


def test_a_datagram_beside_the_stream_transport_is_applied_and_never_answered():
    sim, handled = _side_sim({
        "wake": {"label": "Wake", "udp": {"magic_packet": "mac"}},
        "pwr_on": {"label": "On", "send": "PWR 1\r", "sets": {"power": True}},
    })
    answered: list = []

    async def dispatch(data, addr):
        answered.append(data)

    sim.dispatch_command_datagram = dispatch
    asyncio.run(sim.dispatch_datagram(b"PWR 1\r", ("127.0.0.1", 5)))
    assert handled == [b"PWR 1\r"] and answered == []
    assert sim._state["power"] is True


# ── The child roster ──


def _roster_sim(
    child_types: dict, config: dict | None = None, default_config: dict | None = None,
    simulator: dict | None = None,
) -> YAMLAutoSimulator:
    definition = _definition(child_entity_types=child_types)
    if default_config is not None:
        definition["default_config"] = default_config
    if simulator is not None:
        definition["simulator"] = simulator
    return _sim(definition, config)


def _output(instances, **extra) -> dict:
    return {"output": {
        "state_variables": {"route": {"type": "integer", "default": 0},
                            "mute": {"type": "boolean"}},
        "instances": instances,
        **extra,
    }}


@pytest.mark.parametrize("instances, config, roster", [
    # A literal list: strings and integers, as text, in order, once each.
    ({"ids": [3, "b", 1, 3, True, 2.5, None, {"x": 1}, ""]}, {}, ["3", "b", "1", ""]),
    # An empty list is the roster, and the count beside it is never read.
    ({"ids": [], "count": 3}, {}, None),
    # A list of nothing usable registers nothing.
    ({"ids": [True, 1.5]}, {}, None),
    # `ids` that are not a list are passed over for the count.
    ({"ids": "1,2", "count": 2}, {}, ["1", "2"]),
    ({"ids": {"a": 1}, "ids_from": "names"}, {"names": "x"}, ["x"]),
    ({"count": 3}, {}, ["1", "2", "3"]),
    ({"count": "4"}, {}, ["1", "2", "3", "4"]),
    ({"count": " 2 "}, {}, ["1", "2"]),
    ({"count": 0}, {}, None),
    ({"count": -2}, {}, None),
    ({"count": 2.5}, {}, None),
    ({"count": True}, {}, None),
    ({"count": "two"}, {}, None),
    ({"count": ""}, {}, None),
    ({"count": [2]}, {}, None),
    # A count from a config field.
    ({"count_from": "n"}, {"n": 3}, ["1", "2", "3"]),
    ({"count_from": "n"}, {"n": "2"}, ["1", "2"]),
    ({"count_from": "n"}, {"n": ""}, None),
    ({"count_from": "n"}, {"n": None}, None),
    ({"count_from": "n"}, {"n": "x"}, None),
    ({"count_from": "n"}, {}, None),
    # A written count wins over the field, even a count of nothing.
    ({"count": 2, "count_from": "n"}, {"n": 5}, ["1", "2"]),
    ({"count": 0, "count_from": "n"}, {"n": 5}, None),
    ({"count": None, "count_from": "n"}, {"n": 2}, ["1", "2"]),
    # A field name that is not truthy is not read.
    ({"count": None, "count_from": ""}, {"": 4}, None),
    # The device's own count is not known to the device it simulates.
    ({"count_from_state": "outputs"}, {}, None),
    ({"count_from_state": "outputs", "count_from": "n"}, {"n": 2}, ["1", "2"]),
    # A count wins over ids_from.
    ({"count": 1, "ids_from": "names"}, {"names": "a,b"}, ["1"]),
    # A comma list from a config field.
    ({"ids_from": "names"}, {"names": " a, b,,c ,"}, ["a", "b", "c"]),
    ({"ids_from": "names"}, {"names": 7}, ["7"]),
    ({"ids_from": "names"}, {"names": None}, None),
    ({"ids_from": "names"}, {"names": 0}, None),
    ({"ids_from": "names"}, {}, None),
    ({"ids_from": 5}, {5: "a"}, None),
    ({}, {}, None),
    ({"label": "Out {id}"}, {}, None),
])
def test_an_instances_block_is_read_against_this_instances_config(instances, config, roster):
    sim = _roster_sim(_output(instances), config)
    assert sim._child_roster.get("output") == roster


def test_the_config_read_is_the_drivers_defaults_under_this_instances_own():
    sim = _roster_sim(
        _output({"count_from": "n"}), config={"n": 2}, default_config={"n": 4, "port": 1},
    )
    assert sim._child_roster["output"] == ["1", "2"]
    sim = _roster_sim(_output({"count_from": "n"}), config={}, default_config={"n": 3})
    assert sim._child_roster["output"] == ["1", "2", "3"]
    sim = _roster_sim(_output({"count_from": "n"}), config={"m": 1}, default_config=None)
    assert "output" not in sim._child_roster


@pytest.mark.parametrize("child_types", [
    None, [], "output", {"output": None}, {"output": "x"},
    {"output": {"instances": None}}, {"output": {"instances": [1, 2]}},
])
def test_a_declaration_that_is_not_a_mapping_holds_no_children(child_types):
    sim = _roster_sim(child_types)
    assert sim._child_roster == {}


def test_each_registered_child_holds_its_variables_defaults_in_roster_order():
    sim = _roster_sim(_output({"ids": ["2", 1]}))
    keys = [k for k in sim._state if k.startswith("output.")]
    assert keys == ["output.2.route", "output.2.mute", "output.1.route", "output.1.mute"]
    assert sim._state["output.2.route"] == 0
    assert sim._state["output.1.mute"] is False


def test_types_are_read_in_the_order_the_driver_declares_them():
    types = {
        "zone": {"state_variables": {"vol": {"type": "integer"}}, "instances": {"count": 1}},
        **_output({"count": 1}),
    }
    sim = _roster_sim(types)
    assert list(sim._child_roster) == ["zone", "output"]
    assert [k for k in sim._state if "." in k] == ["zone.1.vol", "output.1.route", "output.1.mute"]


def test_a_child_type_with_no_variables_still_holds_its_children():
    sim = _roster_sim({"output": {"state_variables": "x", "instances": {"count": 2}}})
    assert sim._child_roster == {"output": ["1", "2"]}
    assert not [k for k in sim._state if k.startswith("output.")]


def test_section_seeds_name_children_and_keep_their_values():
    sim = _roster_sim(
        _output({"count": 1}),
        simulator={"initial_state": {
            "output.3.route": 5, "output.1.mute": True, "output.4.volume": 2,
            "zone.2.route": 1, "output.x.y.route": 1, "output.7": 1, "level": 4,
        }},
    )
    assert sim._child_roster == {"output": ["1", "3"]}
    assert sim._state["output.3.route"] == 5
    assert sim._state["output.3.mute"] is False
    assert sim._state["output.1.mute"] is True
    assert "output.4.route" not in sim._state


def test_seeded_children_join_after_the_declared_ones_in_seed_order():
    sim = _roster_sim(
        _output({"ids": ["9"]}),
        simulator={"initial_state": {"output.5.mute": True, "output.2.route": 3,
                                     "output.5.route": 1}},
    )
    assert sim._child_roster["output"] == ["9", "5", "2"]


def test_seeds_with_no_child_types_name_no_children():
    sim = _roster_sim(None, simulator={"initial_state": {"output.1.route": 2}})
    assert sim._child_roster == {}
    assert sim._state["output.1.route"] == 2


def _typed(id_format: dict | None = None, instances: dict | None = None) -> dict:
    tdef: dict = {"state_variables": {"route": {"type": "integer"}}}
    if id_format is not None:
        tdef["id_format"] = id_format
    if instances is not None:
        tdef["instances"] = instances
    return {"output": tdef, "zone": {"state_variables": {"vol": {"type": "integer"}}}}


def test_project_children_join_the_roster_as_their_wire_ids():
    sim = _roster_sim(_typed({"type": "integer", "pad": 3}, {"count": 2}))
    sim.set_child_entities({
        "output": {"003": {"label": "Three"}, "001": {"label": "One"}, "abc": {}},
        "zone": {"02": {"label": "Lobby"}},
        "nope": {"1": {}},
    })
    assert sim._child_roster == {"output": ["1", "2", "3", "abc"], "zone": ["2"]}
    assert sim._state["output.3.route"] == 0
    assert sim._state["output.abc.route"] == 0
    assert sim._state["zone.2.vol"] == 0
    assert sim._child_entities["output"]["003"] == {"label": "Three"}


def test_project_children_of_a_text_id_type_keep_their_text():
    sim = _roster_sim(_typed({"type": "string"}))
    sim.set_child_entities({"output": {"007": {}, " B ": {}}})
    assert sim._child_roster["output"] == ["007", "B"]


def test_project_children_that_are_not_a_mapping_add_nobody():
    sim = _roster_sim(_typed(None, {"count": 1}))
    sim.set_child_entities({"output": ["5"], "zone": None})
    sim.set_child_entities(None)
    assert sim._child_roster == {"output": ["1"]}


def test_project_children_rebuild_the_per_child_reads_for_the_new_roster():
    types = _typed(None, None)
    definition = _definition(
        child_entity_types=types,
        polling={"queries": [{"send": "ROUTE? {child_id}\r", "each_child": "output",
                              "query_for": "route"}]},
        responses=[{"match": r"^ROUTE (\d+) (\d+)$",
                    "child_set": [{"type": "output", "id": "$1", "state": {"route": "$2"}}]}],
    )
    sim = _sim(definition)
    assert sim._child_query_handlers == []
    sim.set_child_entities({"output": {"4": {}}})
    assert [(h.child_type, h.child_id, h.response_var) for h in sim._child_query_handlers] == [
        ("output", "4", "route")
    ]
    assert sim.handle_command(b"ROUTE? 4\r") == b"ROUTE 4 0\r"


def test_a_count_key_makes_a_counted_roster_even_when_only_the_device_could_count_it():
    """``count_from_state`` alone is a counted roster the simulated device
    cannot size, so an ``ids_from`` beside it is never read."""
    sim = _roster_sim(
        _output({"count_from_state": "outputs", "ids_from": "names"}), {"names": "a,b"},
    )
    assert "output" not in sim._child_roster


def test_a_padded_project_id_of_zero_is_the_wire_id_zero():
    """Local id 0 is a real id: ``000`` joins as ``0``, not as its padded
    text."""
    sim = _roster_sim(_typed({"type": "integer", "pad": 3}))
    sim.set_child_entities({"output": {"000": {}, "007": {}}})
    assert sim._child_roster["output"] == ["0", "7"]
    assert "output.0.route" in sim._state and "output.000.route" not in sim._state


def test_seeded_children_are_adopted_in_the_order_the_section_seeds_them():
    """Neither sorted nor reversed: the order the seeds were written."""
    sim = _roster_sim(
        _output({"ids": []}),
        simulator={"initial_state": {"output.5.mute": True, "output.2.route": 3,
                                     "output.8.route": 1}},
    )
    assert sim._child_roster["output"] == ["5", "2", "8"]
