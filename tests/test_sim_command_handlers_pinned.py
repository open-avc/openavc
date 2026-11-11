"""Pins for how the auto-simulator reads a driver's commands.

Each command becomes a handler: the pattern its send template matches (the
driver's prefix, a ``udp:`` payload, a passthrough left out), which capture
writes which state variable and how its text decodes (a format spec's base, a
parameter's scale), the variable the reply reports, and whether the command
is addressed to one child. A command that declares ``sets:`` / ``query_for:``
is wired as written; one that does not is read from its parameter names and
its own name. Recorded from the code as it is, on an invented device.
"""

from __future__ import annotations

import logging

from openavc.simulator.yaml_auto import YAMLAutoSimulator

LOGGER = "openavc.simulator.yaml_auto"

FIELDS = ("name", "pattern", "state_changes", "response_var", "group_bases",
          "group_conversions", "group_value_maps", "child_type", "child_id_group",
          "child_wire_map", "child_state_keys", "response_is_child")


def _sim(commands: dict, **overrides) -> YAMLAutoSimulator:
    d = {
        "id": "acme_widget",
        "name": "Acme Widget",
        "transport": "tcp",
        "delimiter": "\r",
        "state_variables": {
            "power": {"type": "enum", "values": ["on", "off"]},
            "level": {"type": "integer"},
            "mute": {"type": "boolean"},
            "input": {"type": "integer"},
        },
        "commands": commands,
        "responses": [
            {"match": r"LVL(\d+)", "set": {"level": "$1"}},
            {"match": r"PWR (on|off)", "set": {"power": "$1"}},
        ],
    }
    d.update(overrides)
    return YAMLAutoSimulator("w1", {}, driver_def=d)


def _view(handler) -> dict:
    out = {}
    for f in FIELDS:
        v = getattr(handler, f)
        out[f] = v.pattern if f == "pattern" else v
    return out


def _views(sim) -> list[dict]:
    return [_view(h) for h in sim._command_handlers]


def _one(commands: dict, **overrides) -> dict:
    views = _views(_sim(commands, **overrides))
    assert len(views) == 1, views
    return views[0]


def _h(name, pattern, **kw) -> dict:
    out = {"name": name, "pattern": pattern, "state_changes": {}, "response_var": None,
           "group_bases": {}, "group_conversions": {}, "group_value_maps": {},
           "child_type": None, "child_id_group": 0, "child_wire_map": None,
           "child_state_keys": set(), "response_is_child": False}
    out.update(kw)
    return out


# ── the pattern ──


def test_the_prefix_frames_every_command_but_raw_and_side_sends():
    views = _views(_sim({
        "power_on": {"send": "PWR ON"},
        "raw_one": {"send": "RAW", "raw": True},
        "framed": {"send": "!X"},
        "wake": {"udp": {"payload": "WAKE", "magic_packet": False}},
    }, command_prefix="!"))
    assert [(v["name"], v["pattern"]) for v in views] == [
        ("power_on", r"^!PWR ON$"), ("raw_one", r"^RAW$"), ("framed", r"^!X$"), ("wake", r"^WAKE$")]


def test_a_command_with_no_send_or_a_passthrough_builds_nothing(caplog):
    caplog.set_level(logging.WARNING, logger=LOGGER)
    sim = _sim({
        "http_only": {"method": "GET", "path": "/x"},
        "empty": {"send": ""},
        "passthru": {"send": "{command}\r", "params": {"command": {"type": "string"}}},
        "udp_no_payload": {"udp": {"magic_packet": True}},
    })
    assert _views(sim) == []
    assert [(r.name, r.getMessage()) for r in caplog.records if r.levelno >= logging.WARNING] == []


# ── declared semantics ──


def test_declared_sets_read_captures_in_template_order():
    view = _one({"route": {
        "send": "R{input}*{level}",
        "params": {"level": {"type": "integer"}, "input": {"type": "integer"}},
        "sets": {"level": "{level}", "input": "{input}", "mute": True, "power": "on",
                 "x": "{nope}"},
    }})
    assert view == _h(
        "route", r"^R(-?\d+)\*(-?\d+)$",
        state_changes={"level": 2, "input": 1, "mute": ("literal", True), "power": ("literal", "on")},
        response_var="level",
    )


def test_declared_query_for_names_the_reply_and_sets_nothing():
    view = _one({"get_mode": {"send": "MODE?", "query_for": "power"}})
    assert view == _h("get_mode", r"^MODE\?$", response_var="power")


def test_declared_param_bases_and_conversions_follow_template_order():
    view = _one({"set": {
        "send": "S{b:02X}{a:03d}",
        "params": {"a": {"type": "integer", "scale": 0.5}, "b": {"type": "integer"},
                   "c": {"type": "integer", "scale": 2}},
        "sets": {"level": "{a}", "input": "{b}"},
    }})
    assert view["state_changes"] == {"level": 2, "input": 1}
    assert view["group_bases"] == {1: 16}
    assert view["group_conversions"] == {2: {"scale": 0.5}}


def test_a_declared_command_addressed_to_a_child():
    sim = _sim({"route": {
        "send": "{output}*{input}!",
        "params": {"output": {"type": "child_id", "child_type": "output",
                              "map": {"1": "A", "2": "B"}},
                   "input": {"type": "integer"}},
        "sets": {"input": "{input}", "level": 7},
    }}, child_entity_types={"output": {"state_variables": {"input": {"type": "integer"}}}})
    assert _views(sim) == [_h(
        # the id capture is the child_id type's, whatever the param's map
        "route", r"^(\d+)\*(-?\d+)!$",
        state_changes={"input": 2, "level": ("literal", 7)}, response_var="input",
        child_type="output", child_id_group=1, child_wire_map={"A": "1", "B": "2"},
        child_state_keys={"input"}, response_is_child=True,
    )]


def test_a_declared_child_command_whose_id_is_not_in_the_template_routes_flat():
    sim = _sim({"set_in": {
        "send": "IN {input}",
        "params": {"output": {"type": "child_id", "child_type": "output"},
                   "input": {"type": "integer"}},
        "sets": {"input": "{input}"},
    }}, child_entity_types={"output": {"state_variables": {"input": {"type": "integer"}}}})
    assert _views(sim) == [_h("set_in", r"^IN (-?\d+)$", state_changes={}, response_var="input")]


def test_a_declared_query_for_a_child_variable_answers_for_the_child():
    sim = _sim({"q": {
        "send": "Q{output}",
        "params": {"output": {"type": "child_id", "child_type": "output"}},
        "query_for": "input",
    }}, child_entity_types={"output": {"state_variables": {"input": {"type": "integer"}}}})
    assert _views(sim) == [_h("q", r"^Q(\d+)$", response_var="input", child_type="output",
                              child_id_group=1, response_is_child=True)]


def test_two_child_id_params_route_nothing_to_a_child():
    sim = _sim({"tie": {
        "send": "{a}*{b}",
        "params": {"a": {"type": "child_id", "child_type": "output"},
                   "b": {"type": "child_id", "child_type": "output"}},
        "sets": {"input": "{a}"},
    }}, child_entity_types={"output": {"state_variables": {"input": {"type": "integer"}}}})
    view = _views(sim)[0]
    assert (view["state_changes"], view["child_type"], view["child_id_group"]) == ({"input": 1}, None, 0)


# ── read from names ──


def test_a_param_named_after_a_variable_writes_it_in_declaration_order():
    # The template puts {input} first, but the heuristic numbers captures by
    # the order the params are declared in.
    view = _one({"route": {
        "send": "R{input}*{level:02X}",
        "params": {"level": {"type": "integer", "scale": 2}, "input": {"type": "integer"}},
    }})
    assert view == _h(
        "route", r"^R(-?\d+)\*([0-9a-fA-F]+)$",
        state_changes={"level": 1, "input": 2}, response_var="level",
        group_bases={1: 16}, group_conversions={1: {"scale": 2}},
    )


def test_a_param_missing_from_the_template_still_takes_a_group_number():
    view = _one({"set_level": {
        "send": "L{level}",
        "params": {"ghost": {"type": "string"}, "level": {"type": "integer"}},
    }})
    assert view["state_changes"] == {"level": 2}
    assert view["response_var"] == "level"


def test_a_name_that_switches_sets_its_value():
    views = _views(_sim({
        "power_on": {"send": "PWR ON"},
        "mute_off": {"send": "MUTE 0"},
        "power_toggle": {"send": "PWR T"},
    }))
    assert [(v["name"], v["state_changes"], v["response_var"]) for v in views] == [
        # the switching word's value is a boolean whatever the variable's type
        ("power_on", {"power": True}, "power"),
        ("mute_off", {"mute": False}, "mute"),
        ("power_toggle", {}, "power"),
    ]


def test_a_set_name_with_params_takes_the_first_param_that_is_not_the_child_id():
    view = _one({"set_level": {"send": "VOL {v}", "params": {"v": {"type": "integer"}}}})
    assert (view["state_changes"], view["response_var"]) == ({"level": 1}, "level")
    sim = _sim({"set_input": {
        "send": "IN {out}",
        "params": {"out": {"type": "child_id", "child_type": "output"}},
    }}, child_entity_types={"output": {"state_variables": {"gain": {"type": "integer"}}}})
    view = _views(sim)[0]
    assert (view["state_changes"], view["response_var"], view["child_type"]) == ({}, "input", None)


def test_a_child_variable_name_is_read_against_the_child_type_first():
    sim = _sim({
        "set_gain": {"send": "G{out} {val}",
                     "params": {"out": {"type": "child_id", "child_type": "output"},
                                "val": {"type": "integer"}}},
        "mute_on": {"send": "M{out}",
                    "params": {"out": {"type": "child_id", "child_type": "output"}}},
        "level_query": {"send": "L{out}?",
                        "params": {"out": {"type": "child_id", "child_type": "output"}}},
    }, child_entity_types={"output": {"state_variables": {"gain": {"type": "integer"},
                                                           "mute": {"type": "boolean"}}}})
    assert [(v["name"], v["state_changes"], v["response_var"], v["child_type"],
             v["child_id_group"], v["child_state_keys"], v["response_is_child"])
            for v in _views(sim)] == [
        ("set_gain", {"gain": 2}, "gain", "output", 1, {"gain"}, True),
        ("mute_on", {"mute": True}, "mute", "output", 1, {"mute"}, True),
        # "_query" is no suffix the name is read by: nothing is inferred
        ("level_query", {}, None, None, 1, set(), False),
    ]


def test_a_child_param_named_after_a_child_variable():
    sim = _sim({"route": {
        "send": "{output}*{gain}",
        "params": {"output": {"type": "child_id", "child_type": "output"},
                   "gain": {"type": "integer"}, "level": {"type": "integer"}},
    }}, child_entity_types={"output": {"state_variables": {"gain": {"type": "integer"}}}})
    view = _views(sim)[0]
    assert (view["state_changes"], view["response_var"], view["child_state_keys"],
            view["response_is_child"], view["child_id_group"]) == (
        {"gain": 2, "level": 3}, "gain", {"gain"}, True, 1)


def test_a_query_name_with_no_params_reports_its_variable(caplog):
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    views = _views(_sim({"get_level": {"send": "L?"}, "mystery": {"send": "ZZZ"},
                         "frobnicate": {"send": "F {n}", "params": {"n": {"type": "integer"}}}}))
    assert [(v["name"], v["state_changes"], v["response_var"]) for v in views] == [
        ("get_level", {}, "level"), ("mystery", {}, None), ("frobnicate", {}, None)]
    debug = [r.getMessage() for r in caplog.records if r.name == LOGGER and r.levelno == logging.DEBUG]
    assert "Auto-sim acme_widget: could not infer behavior for command 'mystery'" in debug
    assert "Auto-sim acme_widget: could not infer behavior for command 'frobnicate'" in debug


def test_a_heuristic_child_command_with_no_id_capture_drops_its_child_writes():
    # The child_id param is not in the template, but the names are numbered
    # by declaration order, so it still takes group 1 (the gain's capture)
    # and the writes keep group numbers the pattern does not have.
    sim = _sim({"set_gain": {
        "send": "GAIN {gain}",
        "params": {"out": {"type": "child_id", "child_type": "output"},
                   "gain": {"type": "integer"}, "level": {"type": "integer"}},
    }}, child_entity_types={"output": {"state_variables": {"gain": {"type": "integer"}}}})
    view = _views(sim)[0]
    assert (view["state_changes"], view["response_var"], view["child_type"],
            view["child_state_keys"], view["response_is_child"], view["child_id_group"]) == (
        {"gain": 2, "level": 3}, "gain", "output", {"gain"}, True, 1)


def test_a_child_id_param_whose_type_is_not_declared_is_a_plain_param():
    view = _one({"set_level": {
        "send": "L{o} {level}",
        "params": {"o": {"type": "child_id", "child_type": "nope"}, "level": {"type": "integer"}},
    }})
    assert (view["state_changes"], view["child_type"], view["group_bases"]) == ({"level": 2}, None, {})


def test_a_hex_child_id_and_a_boolean_param():
    sim = _sim({"set_mute": {
        "send": "M{o:02X}={mute}",
        "params": {"o": {"type": "child_id", "child_type": "output"},
                   "mute": {"type": "boolean"}},
    }}, child_entity_types={"output": {"state_variables": {"x": {"type": "integer"}}}})
    view = _views(sim)[0]
    assert (view["group_bases"], view["state_changes"], view["child_type"]) == ({1: 16}, {"mute": 2}, None)


# ── answering ──


def test_a_command_writes_state_and_answers_with_its_reply():
    sim = _sim({"set_level": {"send": "VOL {v:02X}", "params": {"v": {"type": "integer"}}}})
    assert sim.handle_command(b"VOL 1F") == b"LVL31\r"
    assert sim._state["level"] == 31


def test_settings_handlers_follow_the_commands():
    sim = _sim({"set_level": {"send": "VOL {v}", "params": {"v": {"type": "integer"}}}},
               device_settings={"bright": {"type": "integer", "state_key": "level",
                                           "write": {"send": "BRT {value:02X}"}}})
    assert [v["name"] for v in _views(sim)] == ["set_level", "setting:bright"]
    assert _views(sim)[1] == _h("setting:bright", r"^BRT ([0-9a-fA-F]+)$",
                                state_changes={"level": 1}, response_var="level",
                                group_bases={1: 16})
