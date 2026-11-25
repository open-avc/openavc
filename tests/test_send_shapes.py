"""What each kind of send carries, judged the same wherever it sits.

A command, a device setting's ``write``, a ``liveness:`` probe and an OSC
start-up message go out through the same builders (a line, an HTTP request,
an OSC message), so the contract's one table (``spec.SEND_SHAPES``) says
which fields each takes and what fills its templates, and the checks read
every one of them off it:

* an OSC argument whose written value its type cannot take is refused at the
  author's doors and named where a driver loads, at every origin;
* a ``{placeholder}`` that names nothing is refused at the author's doors in
  every template a send carries, filled from what that origin fills from (a
  probe and a start-up message from the config alone);
* a setting's HTTP write takes ``query_params`` as a command does;
* and a probe the runtime cannot build switches the watchdog off with one
  warning, instead of counting as a missed probe and dropping a device that
  answers everything else.

Invented device (``acme_widget``) and synthetic payloads throughout.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.avcdriver_semantic import check_driver_definition
from openavc.drivers.configurable import create_configurable_driver_class

LOGGER = "openavc.drivers.configurable"

OSC: dict[str, Any] = {
    "id": "acme_widget",
    "name": "Acme Widget",
    "transport": "osc",
    "default_config": {"host": "", "port": 9000, "zone": 1},
    "state_variables": {"level": {"type": "number", "label": "Level"}},
    "commands": {"mute": {"address": "/mute", "args": [{"type": "i", "value": 1}]}},
}


def _strict(definition: dict[str, Any]) -> list[tuple[str, str]]:
    verdict = check_driver_definition(definition, strict=True)
    return list(zip(verdict.errors.tags, verdict.errors))


def _at_load(definition: dict[str, Any]) -> tuple[list[str], list[tuple[str, str]]]:
    """What a door that loads the driver refuses, and what it names."""
    verdict = check_driver_definition(definition, strict=False)
    return list(verdict.errors), list(zip(verdict.notices.tags, verdict.notices))


def _with_args(origin: str, args: list[Any]) -> tuple[dict[str, Any], str]:
    """The OSC driver with ``args`` on one origin's send, and that send's place."""
    definition = copy.deepcopy(OSC)
    if origin == "command":
        definition["commands"]["mute"]["args"] = args
        return definition, "commands.mute"
    if origin == "setting_write":
        definition["device_settings"] = {
            "level": {"type": "number", "label": "Level", "write": {"address": "/level", "args": args}},
        }
        return definition, "device_settings.level.write"
    if origin == "liveness":
        definition["liveness"] = {"send": "/ping", "args": args}
        return definition, "liveness"
    definition["on_connect"] = [{"address": "/subscribe", "args": args}]
    return definition, "on_connect[0]"


ORIGINS = ("command", "setting_write", "liveness", "start_up_message")

# A value its tag cannot take: each raises when the message is built.
UNSENDABLE = [
    pytest.param({"type": "f", "value": "abc"}, "a number", id="f-text"),
    pytest.param({"type": "f"}, "a number", id="f-absent"),
    pytest.param({"type": "f", "value": ""}, "a number", id="f-empty"),
    pytest.param({"type": "d", "value": None}, "a number", id="d-null"),
    pytest.param({"type": "i", "value": True}, "a number", id="i-boolean"),
    pytest.param({"type": "i", "value": "inf"}, "a number", id="i-infinite"),
    pytest.param({"type": "i", "value": "nan"}, "a number", id="i-nan"),
    pytest.param({"value": "abc"}, "a number", id="default-tag-text"),
    pytest.param({"type": "h", "value": 1.5}, "a whole number", id="h-fraction"),
    pytest.param({"type": "h", "value": "1.0"}, "a whole number", id="h-fraction-text"),
]

# What every tag sends as written, or fills before it is read.
SENDABLE = [
    pytest.param({"type": "f", "value": "1.0"}, id="f-number-text"),
    pytest.param({"type": "f", "value": 2}, id="f-number"),
    pytest.param({"type": "f", "value": "nan"}, id="f-nan"),
    pytest.param({"type": "i", "value": "1.5"}, id="i-fraction-text"),
    pytest.param({"type": "h", "value": "7"}, id="h-whole-text"),
    pytest.param({"type": "i", "value": "{zone}"}, id="i-placeholder"),
    pytest.param({"type": "s"}, id="s-absent"),
    pytest.param({"type": "s", "value": None}, id="s-null"),
    pytest.param({"type": "T"}, id="T"),
    pytest.param({"type": "N", "value": "ignored"}, id="N-with-value"),
]


@pytest.mark.parametrize("origin", ORIGINS)
@pytest.mark.parametrize(("arg", "takes"), UNSENDABLE)
def test_an_osc_arg_its_type_cannot_take_is_refused_where_a_driver_is_written(
    origin: str, arg: dict[str, Any], takes: str,
) -> None:
    definition, place = _with_args(origin, [arg])
    tag = arg.get("type", "f")
    value = arg.get("value", "")
    written = (
        "an argument with no value" if value == ""
        else "a null value" if value is None else repr(value)
    )
    expected = (
        f"{place}.args[0]",
        f"{place}.args[0]: an OSC '{tag}' argument needs {takes}, so {written} cannot be sent",
    )
    assert expected in _strict(definition)
    refused, named = _at_load(definition)
    assert refused == []
    assert expected in named


@pytest.mark.parametrize("origin", ORIGINS)
@pytest.mark.parametrize("arg", SENDABLE)
def test_an_osc_arg_its_type_takes_passes_every_door(origin: str, arg: dict[str, Any]) -> None:
    definition, _ = _with_args(origin, [arg])
    assert _strict(definition) == []
    assert _at_load(definition) == ([], [])


def _driver(definition: dict[str, Any], link: Any) -> Any:
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    cls = create_configurable_driver_class(definition)
    base = {"host": "127.0.0.1", **definition.get("default_config", {})}
    driver = cls("dev1", base, state, events)
    driver.transport = link
    return driver


class Capture:
    """A transport that records what is sent."""

    def __init__(self) -> None:
        self.connected = True
        self.sent: list[bytes] = []

    async def send(self, data: bytes) -> None:
        self.sent.append(data)


async def test_a_probe_the_runtime_cannot_build_is_skipped_with_one_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    definition, _ = _with_args("liveness", [{"type": "f", "value": "abc"}])
    link = Capture()
    drv = _driver(definition, link)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        await drv._liveness_probe()
        await drv._liveness_probe()
    assert link.sent == []
    said = [r.getMessage() for r in caplog.records if "liveness probe" in r.getMessage()]
    assert len(said) == 1, said
    assert "OSC arg of type 'f' requires a numeric value, got 'abc'" in said[0]


async def test_a_probe_with_a_plain_value_among_its_args_is_skipped() -> None:
    definition, _ = _with_args("liveness", [1, "a"])
    link = Capture()
    drv = _driver(definition, link)
    await drv._liveness_probe()
    assert link.sent == []


async def test_a_skipped_probe_goes_out_once_the_config_fills_its_value(
    caplog: pytest.LogCaptureFixture,
) -> None:
    definition, _ = _with_args("liveness", [{"type": "i", "value": "{zone}"}])
    definition["default_config"]["zone"] = ""
    link = Capture()
    drv = _driver(definition, link)
    await drv._liveness_probe()
    assert link.sent == []
    drv.config["zone"] = 3
    probe = asyncio.create_task(drv._liveness_probe())
    await asyncio.sleep(0)
    assert len(link.sent) == 1
    drv._liveness_note_data(b"/pong")
    await probe
    caplog.clear()
    drv.config["zone"] = ""
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        await drv._liveness_probe()
    assert len(link.sent) == 1
    assert sum("liveness probe" in r.getMessage() for r in caplog.records) == 1


async def test_a_line_probe_goes_out_as_before() -> None:
    link = Capture()
    drv = _driver({**TCP, "liveness": {"send": "PING\\r"}}, link)
    probe = asyncio.create_task(drv._liveness_probe())
    await asyncio.sleep(0)
    assert link.sent == [b"PING\r"]
    drv._liveness_note_data(b"PONG")
    await probe


async def test_a_probe_the_runtime_can_build_still_waits_for_its_answer() -> None:
    definition, _ = _with_args("liveness", [{"type": "i", "value": "{zone}"}])
    link = Capture()
    drv = _driver(definition, link)
    probe = asyncio.create_task(drv._liveness_probe())
    await asyncio.sleep(0)
    assert len(link.sent) == 1
    drv._liveness_note_data(b"/pong")
    await probe


TCP: dict[str, Any] = {
    "id": "acme_widget",
    "name": "Acme Widget",
    "transport": "tcp",
    "default_config": {"host": "", "port": 23, "zone": 1},
    "state_variables": {"level": {"type": "number", "label": "Level"}},
    "commands": {"noop": {"send": "NOOP\r"}},
}

HTTP: dict[str, Any] = {**TCP, "transport": "http", "commands": {"noop": {"path": "/noop"}}}

_CFG = "It names no config field (only config values substitute here), so it would be sent to the device as written"
_WRITE = "It names no config field and is not {value}, so it would be sent to the device as written"
_CMD = "It names no parameter of this command and no config field, so it would be sent to the device as written"


def _unfilled(place: str, name: str, hint: str, sources: str) -> tuple[str, str]:
    return place, f"{place}: '{{{name}}}' resolves to nothing{hint}. {sources}"


ZONE = " (did you mean 'zone'?)"

PLACEHOLDER_CASES = [
    pytest.param(
        {**TCP, "liveness": {"send": "PING {zne}\r"}},
        _unfilled("liveness.send", "zne", ZONE, _CFG), id="probe-line",
    ),
    pytest.param(
        {**TCP, "liveness": {"send": "PING {push_callback_url}\r"}},
        _unfilled("liveness.send", "push_callback_url", "", _CFG), id="probe-line-push-token",
    ),
    pytest.param(
        {**OSC, "liveness": {"send": "/ping/{zne}"}},
        _unfilled("liveness.send", "zne", ZONE, _CFG), id="probe-address",
    ),
    pytest.param(
        {**OSC, "liveness": {"send": "/ping", "args": [{"type": "s", "value": "{zne}"}]}},
        _unfilled("liveness.args[0].value", "zne", ZONE, _CFG), id="probe-arg",
    ),
    pytest.param(
        {**OSC, "on_connect": [{"address": "/sub", "args": [{"type": "s", "value": "{zne}"}]}]},
        _unfilled("on_connect[0].args[0].value", "zne", ZONE, _CFG), id="start-up-arg",
    ),
    pytest.param(
        {**TCP, "device_settings": {"level": {"type": "number", "write": {"send": "LVL {zne} {value}\r"}}}},
        _unfilled("device_settings.level.write.send", "zne", ZONE, _WRITE), id="write-send",
    ),
    pytest.param(
        {**TCP, "device_settings": {"level": {"type": "number", "write": {"send": "LVL {vlaue}\r"}}}},
        _unfilled("device_settings.level.write.send", "vlaue", " (did you mean 'value'?)", _WRITE),
        id="write-send-value-typo",
    ),
    pytest.param(
        {**HTTP, "device_settings": {"level": {"type": "number", "write": {"path": "/z/{zne}"}}}},
        _unfilled("device_settings.level.write.path", "zne", ZONE, _WRITE), id="write-path",
    ),
    pytest.param(
        {**HTTP, "device_settings": {"level": {"type": "number", "write": {"path": "/z", "body": "{zne}"}}}},
        _unfilled("device_settings.level.write.body", "zne", ZONE, _WRITE), id="write-body",
    ),
    pytest.param(
        {**HTTP, "device_settings": {"level": {"type": "number", "write": {"path": "/z", "query_params": {"z": "{zne}"}}}}},
        _unfilled("device_settings.level.write.query_params.z", "zne", ZONE, _WRITE), id="write-query",
    ),
    pytest.param(
        {**HTTP, "device_settings": {"level": {"type": "number", "write": {"path": "/z", "headers": {"X-Zone": "{zne}"}}}}},
        _unfilled("device_settings.level.write.headers.X-Zone", "zne", ZONE, _WRITE), id="write-header",
    ),
    pytest.param(
        {**OSC, "device_settings": {"level": {"type": "number", "write": {"address": "/z/{zne}"}}}},
        _unfilled("device_settings.level.write.address", "zne", ZONE, _WRITE), id="write-address",
    ),
    pytest.param(
        {**OSC, "device_settings": {"level": {"type": "number", "write": {"address": "/z", "args": [{"type": "f", "value": "{zne}"}]}}}},
        _unfilled("device_settings.level.write.args[0].value", "zne", ZONE, _WRITE), id="write-arg",
    ),
    pytest.param(
        {**HTTP, "commands": {"set": {"path": "/z", "query_params": {"z": "{zne}"}, "params": {"level": {"type": "integer"}}}}},
        _unfilled("commands.set.query_params.z", "zne", ZONE, _CMD), id="command-query",
    ),
    pytest.param(
        {**OSC, "commands": {"set": {"address": "/z", "args": [{"type": "f", "value": "{levle}"}], "params": {"level": {"type": "integer"}}}}},
        _unfilled("commands.set.args[0].value", "levle", " (did you mean 'level'?)", _CMD), id="command-arg",
    ),
]


@pytest.mark.parametrize(("definition", "expected"), PLACEHOLDER_CASES)
def test_a_placeholder_naming_nothing_is_refused_in_every_template_a_send_carries(
    definition: dict[str, Any], expected: tuple[str, str],
) -> None:
    assert _strict(definition) == [expected]
    assert _at_load(definition) == ([], [expected])


FILLED_CASES = [
    pytest.param({**TCP, "liveness": {"send": "PING {zone}\r"}}, id="probe-config"),
    pytest.param({**TCP, "liveness": {"send": '{"ping":null}'}}, id="probe-json-braces"),
    pytest.param(
        {**TCP, "device_settings": {"level": {"type": "number", "write": {"send": "Z{zone} {value} {push_callback_url}\r"}}}},
        id="write-value-config-push",
    ),
    pytest.param(
        {**HTTP, "commands": {"set": {"path": "/z", "query_params": {"z": "{level}", "n": 3}, "params": {"level": {"type": "integer"}}}}},
        id="command-query-param",
    ),
    pytest.param(
        {**HTTP, "commands": {"set": {"path": "/z", "query_params": {"z": "{anything}"}}}},
        id="command-without-params-unjudged",
    ),
]


@pytest.mark.parametrize("definition", FILLED_CASES)
def test_a_placeholder_its_origin_fills_passes(definition: dict[str, Any]) -> None:
    assert _strict(definition) == []
    assert _at_load(definition) == ([], [])


def test_a_settings_http_write_takes_query_params_as_a_command_does() -> None:
    definition = {
        **HTTP,
        "device_settings": {
            "name": {"type": "string", "label": "Name", "write": {"path": "/name", "query_params": {"v": "{value}"}}},
        },
        "state_variables": {"name": {"type": "string", "label": "Name"}},
    }
    assert _strict(definition) == []


def test_every_send_block_in_the_contract_declares_its_origins_fields() -> None:
    from openavc.drivers.spec import DEFS, FIELDS, SEND_SHAPES

    on_connect_item = FIELDS["on_connect"]["items"]["one_of"]
    start_up = next(form for form in on_connect_item if "address" in form.get("fields", {}))
    blocks = {
        "command": DEFS["commandEntry"],
        "setting_write": DEFS["deviceSettingWrite"],
        "liveness": DEFS["livenessBlock"],
        "start_up_message": start_up,
    }
    for origin, block in blocks.items():
        declared = [name for name in block["fields"] if name in SEND_SHAPES["command"] + ("args",)]
        assert declared == list(SEND_SHAPES[origin]), origin


def test_the_send_shape_leaf_stays_pure() -> None:
    root = Path(__file__).resolve().parents[1]
    code = (
        "import sys; sys.path.insert(0, %r); import openavc.drivers.send_shape; "
        "bad = sorted(m for m in sys.modules if m.startswith('openavc') and m not in "
        "{'openavc', 'openavc.drivers', 'openavc.drivers.send_shape', 'openavc.drivers.spec', "
        "'openavc.drivers.driver_model', 'openavc.drivers.query_entry', "
        "'openavc.drivers.framing_checks', 'openavc.drivers.auth_block', "
        "'openavc.drivers.usable_number', 'openavc.drivers.command_form', "
        "'openavc.drivers.yaml_text', 'openavc.drivers.reply_rule', "
        "'openavc.drivers.capture_ref', 'openavc.drivers.json_value_spec', "
        "'openavc.drivers.pattern_fill', 'openavc.drivers.enum_options', "
        "'openavc.drivers.declared_names', 'openavc.drivers.tagged_errors'}); "
        "print(bad); sys.exit(1 if bad else 0)"
    ) % str(root)
    done = subprocess.run([sys.executable, "-I", "-S", "-c", code], capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr
