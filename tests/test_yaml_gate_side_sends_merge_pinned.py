"""A YAML reply rule's ``only_when:`` gate, the commands that go out without
the device's own connection (an ``ir:`` code, a ``udp:`` block), the
device's own protocol merged over the file, and the ``DRIVER_INFO`` a YAML
driver class carries: what each one does today, pinned.

* The gate: which state key a condition reads (a bare name, ``$id``, a key
  that already names a namespace), the default operator, the ``all:`` /
  ``any:`` groups and which is read first, and each malformed shape, which
  opens.
* The side sends: which commands take them, what reaches the bridge's
  emitter and the datagram (host, port, broadcast, bytes), the parameters'
  wire values and their precedence, the MAC's precedence, and each refusal.
* The merge: framing, precedence by name, the IR codes, the placeholders,
  the order of replies and poll lines, the state variables and their
  seeding, and that the class's own definition and ``DRIVER_INFO`` are left
  as they were.
* ``DRIVER_INFO``: the identity defaults, which fields are copied (falsy
  ones too) and in which order, a command's entry, and the class itself.

Called straight on the driver with the sender it hands off to replaced by a
recorder, so nothing here opens a socket. Invented devices ("acme_*") and
synthetic payloads throughout.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.base import WakeAddressError
from openavc.drivers.configurable import create_configurable_driver_class

UNIT: dict[str, Any] = {
    "id": "acme_gate_unit",
    "name": "Acme Gate Unit",
    "manufacturer": "Acme",
    "category": "video",
    "transport": "tcp",
    "delimiter": "\r",
    "default_config": {"host": "", "port": 23},
    "state_variables": {
        "mode": {"type": "string", "label": "Mode"},
        "level": {"type": "integer", "label": "Level"},
    },
    "commands": {
        "ping": {"label": "Ping", "send": "PING\r"},
    },
    "responses": [
        {"match": r"^LEVEL=(\d+)$", "set": {"level": "$1"}},
    ],
}


def _with(definition: dict[str, Any], **extra: Any) -> dict[str, Any]:
    out = copy.deepcopy(definition)
    out.update(extra)
    return out


def _driver(definition: dict[str, Any] = UNIT, **config: Any):
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    cls = create_configurable_driver_class(definition)
    base = {"host": "10.0.0.5", "port": 9000, **definition.get("default_config", {})}
    driver = cls("dev1", {**base, **config}, state, events)
    driver._pace_lines = False
    return driver


class Recorder:
    """Stands in for one async sender: records each call, answers ``result``."""

    def __init__(self, result: Any = "sent") -> None:
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        self.result = result

    async def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append((args, kwargs))
        return self.result


# ── the only_when gate: which key a condition reads ──


def test_a_bare_key_is_the_devices_own_state_variable() -> None:
    drv = _driver()
    assert drv._condition_key("mode") == "device.dev1.mode"


def test_id_in_a_key_becomes_the_devices_id() -> None:
    drv = _driver()
    assert drv._condition_key("device.$id.mode") == "device.dev1.mode"
    assert drv._condition_key("$id_mode") == "device.dev1.dev1_mode"


@pytest.mark.parametrize("key", ["var.room_mode", "device.other.power", "system.version"])
def test_a_key_that_names_a_namespace_is_read_as_written(key: str) -> None:
    assert _driver()._condition_key(key) == key


def test_a_namespaced_key_gates_on_that_key() -> None:
    drv = _driver()
    drv.state.set("var.room_mode", "lecture", source="test")
    assert drv._rule_applies({"key": "var.room_mode", "value": "lecture"}) is True
    assert drv._rule_applies({"key": "var.room_mode", "value": "film"}) is False


def test_id_reaches_the_device_through_the_gate() -> None:
    drv = _driver()
    drv.state.set("device.dev1.mode", "a", source="test")
    assert drv._rule_applies({"key": "device.$id.mode", "value": "a"}) is True
    assert drv._rule_applies({"key": "device.$id.mode", "value": "b"}) is False


# ── the gate: operators and groups ──


def _gate_driver(mode: Any = "a", level: Any = 5):
    drv = _driver()
    drv.state.set("device.dev1.mode", mode, source="test")
    drv.state.set("device.dev1.level", level, source="test")
    return drv


def test_no_operator_means_equals() -> None:
    drv = _gate_driver()
    assert drv._rule_applies({"key": "mode", "value": "a"}) is True
    assert drv._rule_applies({"key": "mode", "value": "b"}) is False


def test_a_named_operator_is_judged() -> None:
    drv = _gate_driver(level=5)
    assert drv._rule_applies({"key": "level", "operator": "gt", "value": 3}) is True
    assert drv._rule_applies({"key": "level", "operator": "lt", "value": 3}) is False


def test_all_needs_every_condition_and_any_needs_one() -> None:
    drv = _gate_driver(mode="a", level=5)
    yes = {"key": "mode", "value": "a"}
    no = {"key": "level", "value": 9}
    assert drv._rule_applies({"all": [yes, no]}) is False
    assert drv._rule_applies({"all": [yes, yes]}) is True
    assert drv._rule_applies({"any": [no, yes]}) is True
    assert drv._rule_applies({"any": [no, no]}) is False


def test_all_is_read_before_any() -> None:
    drv = _gate_driver(mode="a")
    yes = {"key": "mode", "value": "a"}
    no = {"key": "mode", "value": "b"}
    assert drv._rule_applies({"all": [no], "any": [yes]}) is False
    assert drv._rule_applies({"all": [yes], "any": [no]}) is True


def test_an_empty_group_is_not_read_and_the_next_one_decides() -> None:
    drv = _gate_driver(mode="a")
    no = {"key": "mode", "value": "b"}
    assert drv._rule_applies({"all": [], "any": [no]}) is False
    assert drv._rule_applies({"all": "not a list", "any": [no]}) is False


def test_a_group_of_no_objects_opens() -> None:
    drv = _gate_driver()
    assert drv._rule_applies({"all": ["mode", 3]}) is True
    assert drv._rule_applies({"any": [None]}) is True


def test_items_that_are_not_objects_are_left_out_of_a_group() -> None:
    drv = _gate_driver(mode="a")
    no = {"key": "mode", "value": "b"}
    assert drv._rule_applies({"all": ["junk", no]}) is False
    assert drv._rule_applies({"any": ["junk", {"key": "mode", "value": "a"}]}) is True


@pytest.mark.parametrize("gate", [None, {}])
def test_no_gate_opens(gate: Any) -> None:
    assert _gate_driver()._rule_applies(gate) is True


@pytest.mark.parametrize("leaf", [{"value": "b"}, {"key": ""}, {"key": 3, "value": "b"}])
def test_a_condition_with_no_usable_key_opens(leaf: dict[str, Any]) -> None:
    assert _gate_driver(mode="a")._rule_applies(leaf) is True


def test_a_gate_with_neither_group_is_one_condition() -> None:
    drv = _gate_driver(mode="a")
    assert drv._rule_applies({"key": "mode", "value": "b", "other": [1]}) is False


# ── which commands go out without the device's own connection ──


def test_only_a_mapping_definition_can_be_a_side_send() -> None:
    drv = _driver()
    assert drv._side_sender(None) is None
    assert drv._side_sender("udp") is None
    assert drv._side_sender({"send": "X"}) is None


def test_an_ir_code_goes_to_the_bridge_and_comes_first() -> None:
    drv = _driver()
    sender = drv._side_sender({"ir": {"pronto": "0000"}, "udp": {"payload": "x"}})
    assert sender == drv._send_ir_command


def test_a_udp_block_must_be_a_mapping() -> None:
    drv = _driver()
    assert drv._side_sender({"udp": {"payload": "x"}}) == drv._send_udp_side
    assert drv._side_sender({"udp": "x"}) is None


async def test_an_ir_code_is_emitted_with_its_pronto_and_repeat() -> None:
    drv = _driver()
    drv.emit_via_bridge = Recorder("emitted")
    out = await drv._send_ir_command("power", {"ir": {"pronto": "0000 006D", "repeat": 3}}, {})
    assert out == "emitted"
    assert drv.emit_via_bridge.calls == [(("ir", {"pronto": "0000 006D", "repeat": 3}), {})]


async def test_an_ir_code_repeats_once_and_has_empty_pronto_when_absent() -> None:
    drv = _driver()
    drv.emit_via_bridge = Recorder()
    await drv._send_ir_command("power", {"ir": {"code": "x"}}, {})
    assert drv.emit_via_bridge.calls == [(("ir", {"pronto": "", "repeat": 1}), {})]


async def test_an_ir_command_goes_out_with_no_transport() -> None:
    drv = _driver(ir_codes={"power": {"pronto": "0000 006D 0001"}})
    drv.emit_via_bridge = Recorder("emitted")
    drv.transport = None
    assert await drv.send_command("power") == "emitted"


# ── a udp: block ──


SIDE: dict[str, Any] = _with(
    UNIT,
    command_prefix="<",
    command_suffix=">",
    send_frame={"type": "length_prefix", "header": "HD", "length_size": 1},
    config_schema={
        "mac_address": {"type": "string", "label": "Display MAC"},
        "rx_port": {"type": "integer"},
    },
    state_variables={**UNIT["state_variables"], "mac_reported": {"type": "string"}},
    commands={
        "say": {
            "label": "Say",
            "udp": {"port": "{rx_port}", "payload": "SAY {word}\\x01", "host": "{rx_host}"},
            "params": {"word": {"type": "string", "map": {"hi": "HELLO"}}},
        },
        "bare": {"label": "Bare", "udp": {"port": 4000}},
        "shout": {"label": "Shout", "udp": {"port": 4001, "payload": "X", "broadcast": True}},
        "noport": {"label": "No Port", "udp": {"payload": "X"}},
        "blankport": {"label": "Blank Port", "udp": {"payload": "X", "port": ""}},
        "wake": {"label": "Wake", "udp": {"magic_packet": "mac_address"}},
        "wake_rep": {"label": "Wake Rep", "udp": {"magic_packet": "mac_reported", "port": 7}},
        "wake_host": {"label": "Wake Host", "udp": {"magic_packet": "mac_address", "host": "10.9.9.9"}},
    },
)


def _side(**config: Any):
    drv = _driver(SIDE, **{"rx_port": 5100, "rx_host": "10.1.1.1", "mac_address": "", **config})
    drv.send_udp = Recorder(None)
    drv.wake_on_lan = Recorder(None)
    return drv


async def test_a_payload_is_filled_mapped_and_never_framed() -> None:
    drv = _side()
    assert await drv.send_command("say", {"word": "hi"}) is True
    assert drv.send_udp.calls == [
        ((b"SAY HELLO\x01",), {"host": "10.1.1.1", "port": 5100, "broadcast": False}),
    ]


async def test_a_parameter_wins_over_config_of_the_same_name() -> None:
    drv = _side(word="from-config")
    await drv.send_command("say", {"word": "plain"})
    assert drv.send_udp.calls[0][0] == (b"SAY plain\x01",)


async def test_no_payload_sends_an_empty_datagram_and_no_host_is_the_devices() -> None:
    drv = _side()
    await drv.send_command("bare")
    assert drv.send_udp.calls == [((b"",), {"host": None, "port": 4000, "broadcast": False})]


async def test_broadcast_is_passed_on() -> None:
    drv = _side()
    await drv.send_command("shout")
    assert drv.send_udp.calls[0][1]["broadcast"] is True


@pytest.mark.parametrize("command", ["noport", "blankport"])
async def test_a_payload_with_no_port_is_refused(command: str) -> None:
    drv = _side()
    with pytest.raises(ValueError) as caught:
        await drv.send_command(command)
    assert str(caught.value) == f"[dev1] commands.{command}.udp: no port to send to"
    assert drv.send_udp.calls == []


async def test_a_port_that_is_not_a_number_is_refused_quoted() -> None:
    drv = _side(rx_port="abc")
    with pytest.raises(ValueError) as caught:
        await drv.send_command("say", {"word": "x"})
    assert str(caught.value) == "[dev1] commands.say.udp: port 'abc' is not a number"


@pytest.mark.parametrize("port", [0, 65536])
async def test_a_port_out_of_range_is_refused(port: int) -> None:
    drv = _side(rx_port=port)
    with pytest.raises(ValueError) as caught:
        await drv.send_command("say", {"word": "x"})
    assert str(caught.value) == (
        f"[dev1] commands.say.udp: port {port} is not between 1 and 65535"
    )


@pytest.mark.parametrize("port", [1, 65535])
async def test_the_ends_of_the_port_range_are_sent_to(port: int) -> None:
    drv = _side(rx_port=port)
    await drv.send_command("say", {"word": "x"})
    assert drv.send_udp.calls[0][1]["port"] == port


async def test_a_magic_packet_goes_to_the_wake_port_by_default() -> None:
    drv = _side(mac_address=" AA-BB-CC-DD-EE-FF ")
    assert await drv.send_command("wake") is True
    assert drv.wake_on_lan.calls == [(("aa:bb:cc:dd:ee:ff",), {"host": None, "port": 9})]
    assert drv.send_udp.calls == []


async def test_a_magic_packet_takes_its_own_port_and_host() -> None:
    drv = _side()
    drv.state.set("device.dev1.mac_reported", "11:22:33:44:55:66", source="test")
    await drv.send_command("wake_rep")
    assert drv.wake_on_lan.calls == [(("11:22:33:44:55:66",), {"host": None, "port": 7})]
    drv = _side(mac_address="aa:bb:cc:dd:ee:ff")
    await drv.send_command("wake_host")
    assert drv.wake_on_lan.calls[0][1] == {"host": "10.9.9.9", "port": 9}


async def test_the_reported_mac_beats_the_typed_one() -> None:
    drv = _side(mac_address="aa:bb:cc:dd:ee:ff")
    drv.state.set("device.dev1.mac_address", "11:22:33:44:55:66", source="test")
    await drv.send_command("wake")
    assert drv.wake_on_lan.calls[0][0] == ("11:22:33:44:55:66",)


async def test_no_usable_mac_refuses_naming_the_field_and_the_typed_text() -> None:
    drv = _side(mac_address="  not-a-mac  ")
    with pytest.raises(WakeAddressError) as caught:
        await drv.send_command("wake")
    assert caught.value.field_label == "Display MAC"
    assert caught.value.value == "not-a-mac"
    assert caught.value.reported is False
    assert drv.wake_on_lan.calls == []


async def test_a_mac_held_in_a_state_variable_is_one_that_fills_itself_in() -> None:
    drv = _side()
    with pytest.raises(WakeAddressError) as caught:
        await drv.send_command("wake_rep")
    assert caught.value.reported is True
    assert caught.value.field_label == "MAC Reported"


async def test_a_learned_field_fills_itself_in(monkeypatch) -> None:
    import openavc.core.learned_config as learned

    drv = _side()
    monkeypatch.setattr(learned, "learned_fields", lambda driver: {"mac_address"})
    with pytest.raises(WakeAddressError) as caught:
        await drv.send_command("wake")
    assert caught.value.reported is True


# ── the device's own protocol over the file ──


def test_an_inline_command_takes_the_prefix_once() -> None:
    drv = _driver(_with(UNIT, command_prefix="@"), commands={"hello": "HI"})
    assert drv._definition["commands"]["hello"]["send"].startswith("@HI")


def test_an_ir_code_alone_merges() -> None:
    drv = _driver(ir_codes={"power": {"pronto": "0000 006D 0001"}})
    assert "power" in drv._definition["commands"]
    assert "power" in drv.DRIVER_INFO["commands"]


def test_an_ir_code_wins_over_an_inline_command_of_the_same_name() -> None:
    drv = _driver(
        commands={"power": "PWR"}, ir_codes={"power": {"pronto": "0000 006D 0001"}},
    )
    assert drv._definition["commands"]["power"].get("ir")


def test_an_inline_command_wins_over_the_files_of_the_same_name() -> None:
    drv = _driver(commands={"ping": "PONG"})
    assert drv._definition["commands"]["ping"]["send"].startswith("PONG")


def test_a_placeholder_naming_a_config_field_is_not_a_param() -> None:
    drv = _driver(commands={"go": "GO {zone} {host}"})
    assert set(drv._definition["commands"]["go"]["params"]) == {"zone"}
    assert set(drv.DRIVER_INFO["commands"]["go"]["params"]) == {"zone"}


def test_the_driver_info_lists_the_files_commands_and_the_devices() -> None:
    drv = _driver(commands={"hello": "HI"})
    assert list(drv.DRIVER_INFO["commands"]) == ["ping", "hello"]
    assert drv.DRIVER_INFO["commands"]["hello"]["label"] == "hello"


def test_the_devices_replies_go_after_the_files() -> None:
    drv = _driver(responses=[{"match": "^MODE=(.*)$", "set": {"mode": "$1"}}])
    assert [r.get("match") for r in drv._definition["responses"]] == [
        r"^LEVEL=(\d+)$", "^MODE=(.*)$",
    ]


def test_poll_lines_join_the_files_queries_and_keep_its_other_fields() -> None:
    definition = _with(UNIT, polling={"queries": ["LVL?\r"], "when_state": {"power": True}})
    drv = _driver(definition, commands={"q": {"send": "Q?", "poll": True}})
    assert drv._definition["polling"]["queries"] == ["LVL?\r", "Q?\r"]
    assert drv._definition["polling"]["when_state"] == {"power": True}


def test_no_poll_lines_leave_the_polling_block_out() -> None:
    drv = _driver(commands={"hello": "HI"})
    assert "polling" not in drv._definition


def test_an_explicit_state_variable_wins_over_a_derived_one() -> None:
    drv = _driver(
        responses=[{"match": "^X=(.*)$", "set": {"x": "$1"}}],
        state_variables={"x": {"type": "integer", "label": "X Count"}},
    )
    assert drv._definition["state_variables"]["x"]["label"] == "X Count"
    assert drv.DRIVER_INFO["state_variables"]["x"]["label"] == "X Count"


def test_the_devices_own_state_variables_are_seeded() -> None:
    drv = _driver(state_variables={"extra": {"type": "integer", "label": "Extra"}})
    assert "device.dev1.extra" in drv.state.snapshot()


def test_the_class_definition_and_driver_info_are_left_as_they_were() -> None:
    cls = create_configurable_driver_class(copy.deepcopy(UNIT))
    before_def = copy.deepcopy(cls._definition)
    before_info = copy.deepcopy(cls.DRIVER_INFO)
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    drv = cls("dev1", {"host": "h", "port": 1, "commands": {"hello": "HI"}}, state, events)
    assert "hello" in drv.DRIVER_INFO["commands"]
    assert cls._definition == before_def
    assert cls.DRIVER_INFO == before_info
    assert drv._definition is not cls._definition


def test_a_device_with_no_protocol_shares_the_class_definition() -> None:
    cls = create_configurable_driver_class(copy.deepcopy(UNIT))
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    drv = cls("dev1", {"host": "h", "port": 1}, state, events)
    assert drv._definition is cls._definition
    assert drv.DRIVER_INFO is cls.DRIVER_INFO


# ── the DRIVER_INFO a YAML driver class carries ──


def test_the_identity_fields_default_when_the_file_leaves_them_out() -> None:
    cls = create_configurable_driver_class({})
    info = cls.DRIVER_INFO
    assert {k: info[k] for k in (
        "id", "name", "manufacturer", "category", "version", "author",
        "description", "transport", "default_config", "config_schema",
        "state_variables", "commands",
    )} == {
        "id": "unknown", "name": "unknown", "manufacturer": "Generic",
        "category": "utility", "version": "1.0.0", "author": "Community",
        "description": "", "transport": "tcp", "default_config": {},
        "config_schema": {}, "state_variables": {}, "commands": {},
    }
    assert cls.__name__ == "ConfigurableDriver_unknown"


def test_the_name_defaults_to_the_id_and_the_class_is_named_for_it() -> None:
    cls = create_configurable_driver_class({"id": "acme_box"})
    assert cls.DRIVER_INFO["name"] == "acme_box"
    assert cls.__name__ == "ConfigurableDriver_acme_box"


def test_the_class_keeps_the_definition_it_was_given() -> None:
    definition = copy.deepcopy(UNIT)
    assert create_configurable_driver_class(definition)._definition is definition


def test_the_driver_info_lists_its_fields_in_one_order() -> None:
    definition = _with(
        UNIT, delimiter="\n", web_ui=True, routing={"x": 1}, help={"overview": "o"},
        actions=[], bridge={}, protocols=["p"],
    )
    keys = list(create_configurable_driver_class(definition).DRIVER_INFO)
    assert keys == [
        "id", "name", "manufacturer", "category", "version", "author",
        "description", "transport", "default_config", "config_schema",
        "state_variables", "help", "protocols", "bridge", "routing",
        "commands", "actions", "web_ui", "delimiter",
    ]


def test_a_field_is_copied_whatever_its_value() -> None:
    definition = _with(UNIT, web_ui=False, help="", routing=None)
    info = create_configurable_driver_class(definition).DRIVER_INFO
    assert (info["web_ui"], info["help"], info["routing"]) == (False, "", None)


def test_a_command_entry_is_its_label_params_then_its_declared_fields() -> None:
    definition = _with(UNIT, commands={
        "go": {
            "send": "GO", "confirm": "Sure?", "query_for": ["level"], "sets": {"mode": "x"},
            "available_offline": False, "help": "", "address": "/go",
            "params": {"n": {"type": "integer"}}, "label": "Go Now",
        },
        "plain": {"send": "P"},
        "junk": "not a mapping",
    })
    commands = create_configurable_driver_class(definition).DRIVER_INFO["commands"]
    assert list(commands) == ["go", "plain"]
    assert commands["go"] == {
        "label": "Go Now", "params": {"n": {"type": "integer"}}, "address": "/go",
        "help": "", "available_offline": False, "sets": {"mode": "x"},
        "query_for": ["level"], "confirm": "Sure?",
    }
    assert list(commands["go"]) == [
        "label", "params", "address", "help", "available_offline", "sets",
        "query_for", "confirm",
    ]
    assert commands["plain"] == {"label": "plain", "params": {}}


def test_a_state_variable_with_help_is_copied_into_the_definition() -> None:
    definition = copy.deepcopy(UNIT)
    definition["state_variables"]["mode"]["help"] = "What mode"
    before = definition["state_variables"]["mode"]
    plain = definition["state_variables"]["level"]
    info = create_configurable_driver_class(definition).DRIVER_INFO
    assert info["state_variables"] is definition["state_variables"]
    assert definition["state_variables"]["mode"] == before
    assert definition["state_variables"]["mode"] is not before
    assert definition["state_variables"]["level"] is plain
