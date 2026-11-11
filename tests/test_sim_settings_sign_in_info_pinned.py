"""Pins for three things a simulated device reads from its driver: its settings
(a Python simulator's SIMULATOR_INFO and a YAML driver's simulator: section),
the Telnet-style sign-in a YAML driver's auth: block asks for, and what the
Simulator UI is told about a generated device (its controls and its children).
Recorded from the code as it is, on invented devices.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from openavc.simulator.tcp_simulator import TCPSimulator
from openavc.simulator.ui_info import slider_step
from openavc.simulator.yaml_auto import SIGN_IN_REQUIRED_MODE, YAMLAutoSimulator


def _records(caplog) -> list[tuple[str, str, str]]:
    return [(r.name, r.levelname, r.getMessage()) for r in caplog.records
            if r.name.startswith("openavc.simulator") and r.levelno >= logging.WARNING]


def _python_sim(info: dict) -> TCPSimulator:
    class Acme(TCPSimulator):
        SIMULATOR_INFO = {"driver_id": "acme_py", "name": "Acme Py", "transport": "tcp", **info}

        def handle_command(self, data):  # pragma: no cover - not dispatched
            return None

    return Acme("p1")


def _yaml_sim(section: dict | None = None, config: dict | None = None, **overrides) -> YAMLAutoSimulator:
    d = {
        "id": "acme_widget", "name": "Acme Widget", "transport": "tcp", "delimiter": "\\r",
        "default_config": {"inter_command_delay": 0.3},
        "state_variables": {"power": {"type": "boolean"}, "level": {"type": "integer"}},
        "commands": {}, "responses": [],
    }
    d.update(overrides)
    if section is not None:
        d["simulator"] = section
    return YAMLAutoSimulator("w1", config or {}, driver_def=d)


_GOOD_MACHINE = {"states": ["a", "b"], "initial": "b",
                 "transitions": [{"from": "a", "to": "b", "trigger": "go"}]}


# ── Settings ──


def test_a_python_simulator_takes_its_settings_as_dict_takes_them(caplog):
    with caplog.at_level(logging.DEBUG):
        sim = _python_sim({
            "initial_state": [("level", 3), ("phase", "x")],
            "error_modes": [("jam", {"behavior": "no_response", "description": "Jammed"})],
            "delays": [("command_response", 0.4)],
            "capacity": {"rx_buffer": 64, "process_ms": 5},
            "single_client": "reject",
            "state_machines": {"phase": _GOOD_MACHINE},
        })
    assert sim._state == {"level": 3, "phase": "b"}
    assert list(sim._state) == ["level", "phase"]
    assert sim._error_modes == {"jam": {"behavior": "no_response", "description": "Jammed"}}
    assert sim._delays == {"command_response": 0.4}
    assert sim._capacity.declared is not None and sim._capacity.declared.rx_buffer == 64
    assert sim._capacity.enabled is False
    assert sim._single_client.declared == "reject" and sim._single_client.mode == "reject"
    assert list(sim._state_machines) == ["phase"]
    assert _records(caplog) == []


def test_a_python_simulator_with_none_settings(caplog):
    with caplog.at_level(logging.DEBUG):
        sim = _python_sim({"capacity": None, "single_client": None})
    assert sim._capacity.declared is None and sim._single_client.declared is None
    assert _records(caplog) == []
    with pytest.raises(TypeError):
        _python_sim({"initial_state": None})
    with pytest.raises(TypeError):
        _python_sim({"delays": 5})
    with pytest.raises(AttributeError):
        _python_sim({"state_machines": ["phase"]})


def test_a_python_simulator_warns_capacity_then_connections_then_machines(caplog):
    with caplog.at_level(logging.DEBUG):
        sim = _python_sim({"capacity": "lots", "single_client": "first",
                           "state_machines": {"bad": {"states": ["a"]}}})
    assert sim._capacity.declared is None and sim._single_client.declared is None
    assert _records(caplog) == [
        ("openavc.simulator.capacity", "WARNING",
         "p1: ignoring the declared capacity (capacity must be a mapping with rx_buffer and process_ms)"),
        ("openavc.simulator.single_client", "WARNING",
         "p1: ignoring the declared single_client (single_client must be newest, oldest or reject)"),
        ("openavc.simulator.base", "WARNING",
         "Skipping malformed state_machine 'bad' on p1 (needs states, initial, transitions)"),
    ]


def test_a_machine_named_like_a_seeded_key_starts_in_its_own_state():
    sim = _python_sim({"initial_state": {"phase": "x", "z": 1},
                       "state_machines": {"phase": _GOOD_MACHINE}})
    assert sim._state == {"phase": "b", "z": 1}
    ysim = _yaml_sim({"initial_state": {"phase": "x"}, "state_machines": {"phase": _GOOD_MACHINE}})
    assert ysim._state["phase"] == "b"
    assert list(ysim._state) == ["power", "level", "phase"]


def test_a_yaml_section_declares_capacity_and_connections_over_the_definition(caplog):
    with caplog.at_level(logging.DEBUG):
        sim = _yaml_sim({
            "capacity": {"rx_buffer": 32, "process_ms": 2},
            "single_client": "newest",
            "delays": {"other": 1, "command_response": 0.9},
            "error_modes": {"jam": {"behavior": "no_response", "description": "Jammed"}},
        })
    assert sim._capacity.declared.rx_buffer == 32 and sim._capacity.declared.process_ms == 2
    assert sim._single_client.mode == "newest"
    assert sim._delays == {"command_response": 0.9, "other": 1}
    assert list(sim._delays) == ["command_response", "other"]
    assert list(sim._error_modes) == ["jam"]
    assert _records(caplog) == []
    bare = _yaml_sim()
    assert bare._delays == {"command_response": 0.3}
    assert bare._capacity.declared is None and bare._single_client.declared is None


def test_a_yaml_section_warns_capacity_then_connections_then_machines(caplog):
    with caplog.at_level(logging.DEBUG):
        sim = _yaml_sim({"capacity": {"rx_buffer": "x"}, "single_client": "all",
                         "state_machines": {"bad": {"states": ["a"]}}})
    assert sim._capacity.declared is None and sim._single_client.declared is None
    names = [r[0] for r in _records(caplog)]
    assert names == ["openavc.simulator.capacity", "openavc.simulator.single_client",
                     "openavc.simulator.yaml_auto"]
    assert _records(caplog)[2][2] == (
        "acme_widget: skipping malformed state_machine 'bad' (needs states, initial, transitions)"
    )


def test_a_section_error_mode_comes_before_the_sign_in_mode_and_keeps_its_own():
    auth = {"type": "telnet_login", "username_prompt": "login: ", "password_prompt": "Password: "}
    sim = _yaml_sim({"error_modes": {"jam": {"behavior": "no_response"}}}, auth=auth)
    assert list(sim._error_modes) == ["jam", SIGN_IN_REQUIRED_MODE]
    own = {"behavior": SIGN_IN_REQUIRED_MODE, "description": "Ours"}
    sim = _yaml_sim({"error_modes": {SIGN_IN_REQUIRED_MODE: own}}, auth=auth)
    assert sim._error_modes == {SIGN_IN_REQUIRED_MODE: own}


# ── Sign-in ──


class _Writer:
    def __init__(self) -> None:
        self.written = bytearray()

    def write(self, data: bytes) -> None:
        self.written.extend(data)

    async def drain(self) -> None:
        pass


_AUTH = {"type": "telnet_login", "username_prompt": "^login: $", "password_prompt": "Pass\\w+: ",
         "timeout_seconds": 1}


def _signer(auth: dict | None, config: dict | None = None, transport: str = "tcp"):
    d = {"id": "acme_secure", "name": "Acme Secure", "transport": transport,
         "state_variables": {}, "commands": {}, "responses": []}
    if auth is not None:
        d["auth"] = auth
    return YAMLAutoSimulator("s1", config, driver_def=d)


async def _sign_in(sim, feed: bytes = b"", eof: bool = False, reader=None):
    reader = reader or asyncio.StreamReader()
    if feed:
        reader.feed_data(feed)
    if eof:
        reader.feed_eof()
    writer = _Writer()
    ok = await sim.authenticate_client(reader, writer, "c1")
    return ok, bytes(writer.written)


@pytest.mark.parametrize("auth", [
    None, "telnet_login", {"type": "token"}, {"type": None, "username_prompt": "u"},
])
async def test_no_telnet_login_admits_without_a_word(auth):
    ok, out = await _sign_in(_signer(auth, {"username": "u"}))
    assert (ok, out) == (True, b"")


async def test_the_prompts_go_out_as_literals_and_no_success_ends_quietly():
    sim = _signer(dict(_AUTH), {"username": "admin"})
    ok, out = await _sign_in(sim, b"admin\r\nsecret\r\n")
    assert (ok, out) == (True, b"login: Pass\\w+: ".replace(b"\\", b""))
    assert [e["direction"] for e in sim.get_protocol_log()] == ["out", "out"]


async def test_a_prompt_written_as_none_goes_out_empty():
    sim = _signer({**_AUTH, "username_prompt": None}, {"username": "admin"})
    ok, out = await _sign_in(sim, b"admin\r\nsecret\r\n")
    assert (ok, out) == (True, b"Passw+: ")


async def test_a_silenced_client_hears_nothing_but_is_still_read():
    sim = _signer({**_AUTH, "success_pattern": "OK"}, {"username": "admin"})
    sim._client_silenced = lambda client_id: True
    ok, out = await _sign_in(sim, b"admin\r\nsecret\r\n")
    assert (ok, out) == (True, b"")
    assert sim.get_protocol_log() == []


async def test_a_blank_user_is_admitted_unless_the_device_insists():
    sim = _signer(dict(_AUTH), {})
    assert await _sign_in(sim) == (True, b"")
    sim = _signer({**_AUTH, "skip_if_empty": "false"}, {"username": None})
    assert await _sign_in(sim) == (True, b"")
    sim = _signer({**_AUTH, "skip_if_empty": 0, "success_pattern": "Hi$"}, {})
    assert await _sign_in(sim, b"\r\n\r\n") == (True, b"login: Passw+: Hi\r\n")
    sim = _signer(dict(_AUTH), {})
    sim.inject_error(SIGN_IN_REQUIRED_MODE)
    assert await _sign_in(sim, b"x\r\ny\r\n") == (True, b"login: Passw+: ")


async def test_a_bad_timeout_is_read_only_when_the_sign_in_runs():
    assert await _sign_in(_signer({**_AUTH, "timeout_seconds": "soon"}, {})) == (True, b"")
    with pytest.raises(ValueError):
        await _sign_in(_signer({**_AUTH, "timeout_seconds": "soon"}, {"username": "u"}))


async def test_a_custom_field_and_an_ending_without_newline():
    sim = _signer({**_AUTH, "username_field": "user", "line_ending": "\\r",
                   "success_pattern": "\\bWelcome\\n"}, {"user": "a"})
    ok, out = await _sign_in(sim, b"a\rb\r")
    assert (ok, out) == (True, b"login: Passw+:  Welcome \r")


async def test_an_ending_cut_short_or_overrun():
    sim = _signer({**_AUTH, "line_ending": "\r", "failure_pattern": "Nope"}, {"username": "a"})
    ok, out = await _sign_in(sim, b"invalid", eof=True)
    assert (ok, out) == (False, b"login: Passw+: Nope\r")
    sim = _signer({**_AUTH, "line_ending": "\r"}, {"username": "a"})
    reader = asyncio.StreamReader(limit=4)
    reader.feed_data(b"abcdefgh")
    ok, out = await _sign_in(sim, reader=reader)
    assert ok is True


async def test_the_bad_credential_reprompts_without_a_failure_banner(caplog):
    sim = _signer(dict(_AUTH), {"username": "a"})
    with caplog.at_level(logging.INFO):
        ok, out = await _sign_in(sim, b"a\r\ninvalid\r\n")
    assert (ok, out) == (False, b"login: Passw+: \r\nlogin: ")
    assert ("openavc.simulator.yaml_auto", "INFO",
            "Acme Secure Simulator: client c1 sent the designated bad credential — rejecting login") in [
        (r.name, r.levelname, r.getMessage()) for r in caplog.records]


async def test_a_timeout_and_a_dropped_connection_refuse(caplog):
    sim = _signer({**_AUTH, "timeout_seconds": 0.01}, {"username": "a"})
    with caplog.at_level(logging.DEBUG):
        assert await _sign_in(sim) == (False, b"login: ")
    assert _records(caplog) == [
        ("openavc.simulator.yaml_auto", "WARNING", "Acme Secure Simulator: client c1 auth timed out"),
    ]
    reader = asyncio.StreamReader()
    reader.set_exception(ConnectionResetError())
    assert await _sign_in(_signer(dict(_AUTH), {"username": "a"}), reader=reader) == (False, b"login: ")
    reader = asyncio.StreamReader()
    reader.set_exception(OSError("gone"))
    assert await _sign_in(_signer(dict(_AUTH), {"username": "a"}), reader=reader) == (False, b"login: ")
    reader = asyncio.StreamReader()
    reader.set_exception(ValueError("odd"))
    with pytest.raises(ValueError):
        await _sign_in(_signer(dict(_AUTH), {"username": "a"}), reader=reader)


@pytest.mark.parametrize(("auth", "transport", "offered"), [
    (dict(_AUTH), "tcp", True),
    (dict(_AUTH), "serial", False),
    (dict(_AUTH), "udp", False),
    ({**_AUTH, "type": "token"}, "tcp", False),
    ({k: v for k, v in _AUTH.items() if k != "type"}, "tcp", True),
    ({**_AUTH, "username_prompt": ""}, "tcp", False),
    ({**_AUTH, "password_prompt": None}, "tcp", False),
    ({**_AUTH, "skip_if_empty": False}, "tcp", False),
    ({**_AUTH, "skip_if_empty": "no"}, "tcp", True),
    ("telnet_login", "tcp", False),
])
def test_which_devices_offer_the_sign_in_mode(auth, transport, offered):
    assert (SIGN_IN_REQUIRED_MODE in _signer(auth, transport=transport)._error_modes) is offered


def test_the_sign_in_mode_reads_as_the_platform_shows_it():
    sim = _signer(dict(_AUTH))
    assert sim._error_modes[SIGN_IN_REQUIRED_MODE] == {
        "behavior": SIGN_IN_REQUIRED_MODE,
        "description": "Device asks for a sign-in (even with none set)",
        "builtin": True,
    }
    assert sim.to_info_dict()["available_errors"][SIGN_IN_REQUIRED_MODE] == {
        "description": "Device asks for a sign-in (even with none set)", "builtin": True,
    }


# ── What the Simulator UI is told ──


def test_every_kind_of_variable_gets_its_control():
    info = YAMLAutoSimulator._build_info({
        "id": "acme_amp", "name": "Acme Amp", "category": "audio", "transport": "udp",
        "delimiter": "\\n", "default_config": {"port": 4000},
        "state_variables": {
            "gain": {"type": "integer", "min": -10, "max": 10, "unit": "dB"},
            "count": {"type": "integer", "min": 0},
            "level_db": {"type": "number", "min": -80, "max": 0, "label": "Level"},
            "draw": {"type": "number", "min": 0, "max": 5000, "step": 2.5, "unit": "W"},
            "temp": {"type": "number"},
            "mute": {"type": "boolean"},
            "power": {"type": "enum", "values": ["on", "off"]},
            "input": {"type": "enum", "values": ["a", "b"]},
            "mode": {"type": "enum"},
            "name": {"type": "string"},
            "raw": {},
        },
    })
    assert list(info) == ["driver_id", "name", "category", "transport", "default_port",
                          "delimiter", "initial_state", "delays", "controls"]
    assert info["driver_id"] == "acme_amp" and info["name"] == "Acme Amp Simulator"
    assert (info["category"], info["transport"], info["default_port"]) == ("audio", "udp", 4000)
    assert info["delimiter"] == "\\n" and info["delays"] == {"command_response": 0.05}
    assert list(info["initial_state"]) == ["gain", "count", "level_db", "draw", "temp", "mute",
                                           "power", "input", "mode", "name", "raw"]
    assert info["controls"] == [
        {"type": "slider", "key": "gain", "label": "Gain", "min": -10, "max": 10, "step": 1,
         "unit": "dB"},
        {"type": "indicator", "key": "count", "label": "Count"},
        {"type": "slider", "key": "level_db", "label": "Level", "min": -80, "max": 0, "step": 0.2},
        {"type": "slider", "key": "draw", "label": "Draw", "min": 0, "max": 5000, "step": 2.5,
         "unit": "W"},
        {"type": "indicator", "key": "temp", "label": "Temp"},
        {"type": "toggle", "key": "mute", "label": "Mute"},
        {"type": "power", "key": "power"},
        {"type": "select", "key": "input", "label": "Input", "options": ["a", "b"]},
        {"type": "indicator", "key": "mode", "label": "Mode"},
        {"type": "indicator", "key": "name", "label": "Name"},
        {"type": "indicator", "key": "raw", "label": "Raw"},
    ]
    assert [list(c) for c in info["controls"][:1]] == [
        ["type", "key", "label", "min", "max", "step", "unit"]]
    bare = YAMLAutoSimulator._build_info({})
    assert bare == {"driver_id": "unknown", "name": "Unknown Simulator", "category": "generic",
                    "transport": "tcp", "default_port": 0, "delimiter": None,
                    "initial_state": {}, "delays": {"command_response": 0.05}}
    with pytest.raises(AttributeError):
        YAMLAutoSimulator._build_info({"state_variables": {"x": "integer"}})
    with pytest.raises(ValueError):
        YAMLAutoSimulator._build_info(
            {"state_variables": {"x": {"type": "integer", "min": "low", "max": 3}}})


@pytest.mark.parametrize(("args", "step"), [
    ((0, 300, None, False), 1), ((0, 5000, None, False), 10), ((-80, 0, None, False), 0.2),
    ((0, 1, None, False), 0.01), ((0, 0, None, False), 0.1), ((5, 1, None, True), 1),
    ((0, 10, 3, True), 3), ((0, 10, True, True), 1), ((0, 10, 0.25, False), 0.25),
    ((0, 10 ** 13, None, True), 10 ** 13), ((0, 10 ** 13, None, False), 10 ** 13),
    ((0, 0.4, None, True), 1), ((0, 0.3, None, False), 0.01), ((0, 3, None, False), 0.01), ((0, 7, None, False), 0.02),
])
def test_the_step_an_invented_slider_gets(args, step):
    got = slider_step(*args[:3], integral=args[3])
    assert got == step and type(got) is type(step)


def _children_sim() -> YAMLAutoSimulator:
    return _yaml_sim(child_entity_types={
        "output": {
            "id_format": "int", "label": "Zone Out",
            "instances": {"count": 2, "label": "Out {id} of {id}"},
            "state_variables": {
                "gain": {"type": "number", "min": -60, "max": 12, "step": 0.5, "unit": "dB",
                         "label": "Gain"},
                "mute": {"type": "boolean"},
                "src": {"type": "enum", "values": ["a", "b"]},
                "note": None,
            },
        },
        "input_bus": {"id_format": "str", "instances": {"ids": ["7", "002"], "label": 5},
                      "state_variables": {}},
    })


def test_the_children_section_names_each_child_by_its_project_label_then_its_template():
    sim = _children_sim()
    sim.set_child_entities({
        "output": {"001": {"label": "Lobby"}, "002": {"label": ""}, " 03 ": {"label": "Bar"},
                   "x4": {"label": "Odd"}, "005": "not a mapping"},
        "input_bus": {"002": {"label": "Two"}, "7": {}},
    })
    info = sim.to_info_dict()
    out = info["children"]["output"]
    assert out["label"] == "Zone Out"
    assert sim._child_roster["output"] == ["1", "2", "3", "x4", "5"]
    assert out["entries"] == [
        {"id": "1", "label": "Lobby"},
        {"id": "2", "label": "Out 2 of 2"},
        {"id": "3", "label": "Bar"},
        {"id": "x4", "label": "Odd"},
        {"id": "5", "label": "Out 5 of 5"},
    ]
    assert out["props"] == ["gain", "mute", "src", "note"]
    assert out["prop_defs"] == {
        "gain": {"type": "number", "label": "Gain", "min": -60, "max": 12, "step": 0.5, "unit": "dB"},
        "mute": {"type": "boolean"},
        "src": {"type": "enum", "values": ["a", "b"]},
        "note": {"type": "string"},
    }
    assert list(out["prop_defs"]["gain"]) == ["type", "label", "min", "max", "step", "unit"]
    bus = info["children"]["input_bus"]
    assert bus["label"] == "Input Bus"
    assert bus["entries"] == [{"id": "7", "label": "Input Bus 7"},
                              {"id": "002", "label": "Input Bus 002"},
                              {"id": "2", "label": "Two"}]
    assert list(info["children"]) == ["output", "input_bus"]
    assert info["child_entities"]["output"]["001"] == {"label": "Lobby"}


def test_no_roster_sends_no_children_and_notifications_read_as_pushing():
    sim = _yaml_sim()
    info = sim.to_info_dict()
    assert "children" not in info and info["push_state"] is False
    sim = _yaml_sim({"notifications": {"power": "PWR {value}"}})
    assert sim.to_info_dict()["push_state"] is True
    sim = _yaml_sim({"push_state": 1})
    assert sim.to_info_dict()["push_state"] is True
    sim = _yaml_sim({"notifications": {"power": "PWR {value}"}}, transport="http")
    assert sim.to_info_dict()["push_state"] is False


def test_an_empty_capacity_or_mode_in_a_section_is_still_declared(caplog):
    with caplog.at_level(logging.DEBUG):
        _yaml_sim({"capacity": {}, "single_client": ""})
    assert [r[0] for r in _records(caplog)] == [
        "openavc.simulator.capacity", "openavc.simulator.single_client"]


async def test_a_tab_in_a_prompt_prints_as_a_space():
    sim = _signer({**_AUTH, "username_prompt": "User\\tname:"}, {"username": "a"})
    ok, out = await _sign_in(sim, b"a\r\nb\r\n")
    assert out == b"User name:Passw+: "


def test_a_blank_project_label_never_hides_another_for_the_same_child():
    sim = _children_sim()
    sim.set_child_entities({"output": {"2": {"label": "Two"}, "002": {"label": ""}}})
    entries = sim.to_info_dict()["children"]["output"]["entries"]
    assert entries[1] == {"id": "2", "label": "Two"}


def test_a_text_id_that_is_all_digits_misses_its_project_label():
    # Recorded as it is: the label lookup reads "001" as the number 1 whatever
    # the child type's id kind, while a text-id roster keeps "001".
    sim = _yaml_sim(child_entity_types={"zone": {
        "id_format": {"type": "string"}, "instances": {"ids": ["001", "B2"]},
        "state_variables": {"x": {"type": "integer"}}}})
    sim.set_child_entities({"zone": {"001": {"label": "Lobby"}, "B2": {"label": "Bar"}}})
    assert sim._child_roster["zone"] == ["001", "B2"]
    assert sim.to_info_dict()["children"]["zone"]["entries"] == [
        {"id": "001", "label": "Zone 001"}, {"id": "B2", "label": "Bar"}]
