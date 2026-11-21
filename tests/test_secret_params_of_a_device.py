"""Which parameters of a command on a device are secret: one answer, read
off the device's own declaration.

A YAML device can author commands of its own in its config, merged over its
driver's by name. Whether a parameter is secret is judged on that merged
declaration everywhere: when a project is saved (the value moves into the
login file and a marker takes its place), when a command is sent (the marker
is filled in, a refusal and the log leave the value out), and when a problem
report or the Device Audit writes the command down. The save has no running
driver, so it works the declaration out from the project's data; the running
driver's merge and the save's are one function, so the two cannot drift.

The declaration decides: marked secret, or named ``password``. A name that
merely looks like a credential's (``pin``) is the outbound reports' own net
over what they send away, and decides nothing else.

An invented device (Acme) throughout.
"""

from __future__ import annotations

import copy
from types import SimpleNamespace
from typing import Any

import pytest

from openavc.audit import commands as audit_commands
from openavc.core import command_gates, saved_params
from openavc.core.device_config import layered_config
from openavc.core.event_bus import EventBus
from openavc.core.project_loader import DeviceConfig
from openavc.core.state_store import StateStore
from openavc.drivers import registry
from openavc.drivers.base import CommandParamError
from openavc.drivers.configurable import create_configurable_driver_class
from openavc.drivers.inline_merge import declared_commands
from openavc.drivers.secret_params import secret_params
from openavc.support import problem_report as pr
from openavc.utils.log_redaction import REDACTED

DRIVER = "acme_encoder_secrets"
KEY = "live-Acme-KEY-7f3a"
CODE = "Zx-99107"

DEFINITION: dict[str, Any] = {
    "id": DRIVER,
    "name": "Acme Encoder",
    "manufacturer": "Acme",
    "category": "streaming",
    "transport": "tcp",
    "delimiter": "\r",
    "default_config": {"host": "", "port": 4000, "zone": 1},
    "config_schema": {},
    "state_variables": {},
    "commands": {
        "set_stream_key": {
            "label": "Set Stream Key",
            "send": "KEY {stream_key}\r",
            "params": {"stream_key": {"type": "string", "secret": True, "label": "Stream Key"}},
        },
        "set_label": {
            "label": "Set Label",
            "send": "LABEL {text}\r",
            "params": {"text": {"type": "string", "label": "Text"}},
        },
    },
    "responses": [],
}

#: The device's own commands, as the Generic device editor or the AI writes them.
PAIR = {"send": "PAIR {code}", "params": {"code": {"type": "string", "secret": True}}}

#: Device config, connection row, the command and the parameters a step holds,
#: and which of them are secret by the device's own declaration.
CASES: dict[str, tuple[dict[str, Any], dict[str, Any], str, dict[str, Any], set[str]]] = {
    "the driver's own command": (
        {}, {}, "set_stream_key", {"stream_key": KEY}, {"stream_key"},
    ),
    "a command the device authors, its parameter marked secret": (
        {"commands": {"pair": PAIR}}, {}, "pair", {"code": CODE}, {"code"},
    ),
    "a device command replacing the driver's, its parameter unmarked": (
        {"commands": {"set_stream_key": "KEY {stream_key}"}}, {},
        "set_stream_key", {"stream_key": KEY}, set(),
    ),
    "a placeholder named password, with no password set": (
        {"commands": {"sign_in": "LOGIN {password}"}}, {},
        "sign_in", {"password": CODE}, {"password"},
    ),
    # The password is the device's setting here, not a parameter of the command.
    "a placeholder named password, the connection's password set": (
        {"commands": {"sign_in": "LOGIN {password}"}}, {"password": "<saved on this system>"},
        "sign_in", {}, set(),
    ),
    "an IR code replacing a command": (
        {"ir_codes": {"set_stream_key": {"pronto": "0000 006D 0000 0002 0001 0002 0003 0004"}}},
        {}, "set_stream_key", {"stream_key": KEY}, set(),
    ),
    "a prefix carrying a setting, a command the device authors": (
        {"command_prefix": "Z{zone}:", "commands": {"pair": PAIR}}, {}, "pair", {"code": CODE}, {"code"},
    ),
}


@pytest.fixture(autouse=True)
def _driver(monkeypatch):
    monkeypatch.setattr(registry, "_DRIVER_REGISTRY", dict(registry._DRIVER_REGISTRY))
    monkeypatch.setattr(registry, "_RESERVED_DRIVERS", set(registry._RESERVED_DRIVERS))
    cls = create_configurable_driver_class(copy.deepcopy(DEFINITION))
    registry.register_driver(cls)
    return cls


def _settings(config: dict[str, Any], connection: dict[str, Any]) -> dict[str, Any]:
    return layered_config(DEFINITION["default_config"], config, connection)


def _running(cls: type, config: dict[str, Any], connection: dict[str, Any]) -> Any:
    """The device's driver as the device manager runs it."""
    state, events = StateStore(), EventBus()
    state.set_event_bus(events)
    return cls("enc1", _settings(config, connection), state, events)


def _project(config: dict[str, Any], connection: dict[str, Any], command: str,
             params: dict[str, Any]) -> dict[str, Any]:
    """A project file, read as JSON, with one macro step on the device."""
    return {
        "devices": [{"id": "enc1", "driver": DRIVER, "name": "Encoder", "config": copy.deepcopy(config)}],
        "connections": {"enc1": dict(connection)} if connection else {},
        "device_groups": [],
        "macros": [{"id": "go", "name": "Go", "steps": [
            {"action": "device.command", "device": "enc1", "command": command, "params": dict(params)},
        ]}],
    }


@pytest.mark.parametrize("case", list(CASES))
def test_the_save_and_the_running_driver_read_one_declaration(_driver, case):
    config, connection, _command, _params, _secret = CASES[case]
    live = _running(_driver, config, connection)
    assert declared_commands(_driver, _settings(config, connection)) == live.DRIVER_INFO["commands"]


@pytest.mark.parametrize("case", list(CASES))
def test_the_save_moves_exactly_the_parameters_the_send_treats_as_secret(_driver, case):
    config, connection, command, params, secret = CASES[case]
    live = _running(_driver, config, connection)
    assert {k for k in params if command_gates.is_secret_param(live, command, k)} == secret

    marked, values = saved_params.mark_clear(_project(config, connection, command, params))
    stored = marked["macros"][0]["steps"][0]["params"]
    assert {k for k, v in stored.items() if saved_params.is_marker(v)} == secret
    assert sorted(values.values()) == sorted(params[k] for k in secret)


@pytest.mark.parametrize("case", list(CASES))
def test_what_the_save_moved_the_send_fills_in_and_nothing_else(_driver, case):
    """A step saved on this system runs: the send fills in each value the
    save moved, and the device is handed the value, never the marker."""
    config, connection, command, params, _secret = CASES[case]
    live = _running(_driver, config, connection)
    marked, values = saved_params.mark_clear(_project(config, connection, command, params))
    stored = marked["macros"][0]["steps"][0]["params"]
    fill = saved_params.filler(saved_params.saved_names(stored), values.get)
    assert command_gates.fill_saved_params(live, command, stored, fill) == params


def test_a_marker_in_a_parameter_the_device_does_not_mark_is_refused(_driver):
    """The device's own command replaced the driver's and marks nothing: a
    marker put there by the driver's declaration is not filled in."""
    config = {"commands": {"set_stream_key": "KEY {stream_key}"}}
    live = _running(_driver, config, {})
    name = saved_params.new_name()
    stored = {"stream_key": saved_params.marker(name)}
    fill = saved_params.filler(saved_params.saved_names(stored), {name: KEY}.get)
    with pytest.raises(CommandParamError, match="not saved on this system"):
        command_gates.fill_saved_params(live, "set_stream_key", stored, fill)


def test_a_name_like_a_credential_s_is_not_a_secret_parameter():
    commands = {"pair": {"params": {"pin": {"type": "string"}, "token": {"type": "string"}}}}
    assert secret_params(commands, "pair", ["pin", "token"]) == frozenset()


# --- the outbound reports ----------------------------------------------------------------


def _engine(config: dict[str, Any]) -> SimpleNamespace:
    project = SimpleNamespace(
        devices=[DeviceConfig(id="enc1", driver=DRIVER, name="Encoder", config=config)],
        connections={},
    )
    return SimpleNamespace(
        project=project,
        state=StateStore(),
        devices=SimpleNamespace(
            installed_equipment=None, simulation_gap=None,
            get_device_info=lambda device_id: {"id": device_id},
        ),
        simulation=SimpleNamespace(is_redirected=lambda device_id: False),
    )


def test_a_problem_report_masks_a_secret_parameter_of_the_device_s_own_command():
    """``code`` is named like nothing a credential is called: only the
    device's own declaration keeps it out of the report."""
    snapshot = pr.Snapshot(_engine({"host": "192.168.1.50", "commands": {"pair": PAIR}}), "enc1", catalog=None)
    context, masked = pr._context({"command": "pair", "params": {"code": CODE}}, snapshot.commands)
    assert context["params"] == {"code": REDACTED}
    assert masked == {CODE}


def test_a_problem_report_shows_a_parameter_the_device_s_command_does_not_mark():
    config = {"host": "192.168.1.50", "commands": {"set_label": "LABEL {text}"}}
    snapshot = pr.Snapshot(_engine(config), "enc1", catalog=None)
    context, masked = pr._context({"command": "set_label", "params": {"text": "Lobby"}}, snapshot.commands)
    assert context["params"] == {"text": "Lobby"}
    assert masked == set()


def test_the_audit_keeps_out_a_parameter_named_password_whatever_its_definition():
    entry = {"name": "sign_in", "params": {"password": None, "level": {"type": "integer"}}}
    assert audit_commands._secret_params(entry) == {"password"}


def test_the_audit_still_keeps_out_a_parameter_named_like_a_credential():
    """The audit's report goes away from this system: a name like a
    credential's is masked there on top of the declaration."""
    entry = {"name": "pair", "params": {"pin": {"type": "string"}, "level": {"type": "integer"}}}
    assert audit_commands._secret_params(entry) == {"pin"}
