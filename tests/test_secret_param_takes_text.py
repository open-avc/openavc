"""A secret parameter takes text, and a field named like a credential is
marked secret.

A PIN, a code or a key is text to the device, and the platform keeps a
secret parameter as text: saved on this system rather than in the project,
and masked as text. So ``secret`` on a parameter that takes a number, a
choice, a true/false or a child is refused where a driver is written, naming
the fix; a driver already installed with one still loads, and its Installed
entry names it. A parameter named ``password`` is a secret whatever it is
marked, so it is held to the same rule.

A parameter or config field that takes text and is named like a credential
(ending in password, passphrase, pin, psk, secret, api_key or stream_key)
but is not marked secret draws a warning, never a refusal: the mark is what
makes a value secret.

Invented driver (acme_widget) throughout.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from openavc.drivers import driver_loader
from openavc.drivers.avcdriver_semantic import (
    check_driver_definition,
    validate_driver_issues,
    validate_driver_warnings,
)
from openavc.drivers.check import check_driver_file
from openavc.drivers.python_info import (
    check_python_driver_info,
    extract_python_driver_info_from_source,
    python_driver_reference_skips,
)

FIX = "A PIN or code is text: use type string"


def _refusal(at: str, ptype: str) -> str:
    return f"{at}: is marked secret, and a secret takes text, not type '{ptype}'. {FIX}"


def _named_refusal(at: str, ptype: str) -> str:
    return (
        f"{at}: is named password, which makes it a secret, and a secret takes "
        f"text, not type '{ptype}'. {FIX}"
    )


BASE: dict[str, Any] = {
    "id": "acme_widget",
    "name": "Acme Widget",
    "manufacturer": "Acme",
    "category": "utility",
    "version": "1.0.0",
    "transport": "tcp",
    "commands": {
        "pair": {
            "label": "Pair",
            "send": "PAIR {code}\\r",
            "params": {"code": {"type": "string", "label": "Code", "secret": True}},
        },
    },
}


def _definition(**param: Any) -> dict[str, Any]:
    definition = copy.deepcopy(BASE)
    definition["commands"]["pair"]["params"]["code"].update(param)
    return definition


def _with_param(name: str, pdef: dict[str, Any]) -> dict[str, Any]:
    definition = copy.deepcopy(BASE)
    definition["commands"]["pair"]["params"] = {name: {"label": "Value", **pdef}}
    definition["commands"]["pair"]["send"] = f"PAIR {{{name}}}\\r"
    return definition


CODE = "Command 'pair' param 'code'"


# ── The refusal, where a driver is written ──


@pytest.mark.parametrize("ptype", ["integer", "number", "boolean"])
def test_a_secret_that_is_not_text_is_refused_naming_the_fix(ptype: str) -> None:
    assert check_driver_definition(_definition(type=ptype), strict=True).errors == [
        _refusal(CODE, ptype)
    ]


def test_a_secret_choice_is_refused_too() -> None:
    definition = _definition(type="enum", values=["1234", "5678"])
    assert check_driver_definition(definition, strict=True).errors == [_refusal(CODE, "enum")]


def test_a_secret_child_is_refused_too() -> None:
    definition = _definition(type="child_id", child_type="zone")
    definition["child_entity_types"] = {
        "zone": {"label": "Zone", "id_format": {"type": "integer"}, "instances": {"count": 2}},
    }
    assert _refusal(CODE, "child_id") in check_driver_definition(definition, strict=True).errors


@pytest.mark.parametrize("pdef", [
    {"type": "string", "secret": True},
    {"secret": True},
    {"type": "string"},
    {"type": "integer"},
])
def test_text_or_no_mark_is_no_refusal(pdef: dict[str, Any]) -> None:
    definition = copy.deepcopy(BASE)
    definition["commands"]["pair"]["params"]["code"] = {"label": "Code", **pdef}
    assert check_driver_definition(definition, strict=True).errors == []


def test_a_parameter_named_password_is_held_to_it_unmarked() -> None:
    definition = _with_param("password", {"type": "integer"})
    assert check_driver_definition(definition, strict=True).errors == [
        _named_refusal("Command 'pair' param 'password'", "integer")
    ]


def test_an_actions_parameter_is_held_to_it() -> None:
    definition = copy.deepcopy(BASE)
    definition["actions"] = [{
        "id": "pair",
        "label": "Pair",
        "params": {"code": {"type": "integer", "label": "Code", "secret": True}},
    }]
    assert check_driver_definition(definition, strict=True).errors == [
        _refusal("actions[0] param 'code'", "integer")
    ]


def test_the_builder_puts_it_on_the_parameter() -> None:
    issues = validate_driver_issues(_definition(type="integer"), strict=True)
    assert issues == [{
        "severity": "error",
        "message": _refusal(CODE, "integer"),
        "path": "commands.pair.params.code",
    }]


# ── A driver already installed keeps loading ──


def test_a_driver_already_installed_still_loads_and_names_it() -> None:
    loaded = check_driver_definition(_definition(type="integer"))
    assert (loaded.errors, loaded.notices) == ([], [_refusal(CODE, "integer")])


def test_the_loader_reads_the_file_and_names_it(tmp_path: Path) -> None:
    import yaml

    path = tmp_path / "acme_widget.avcdriver"
    path.write_text(yaml.safe_dump(_definition(type="integer"), sort_keys=False))
    read = driver_loader.read_driver_file(path)
    assert read.definition is not None and read.reasons == ()
    assert read.notices == (_refusal(CODE, "integer"),)


# ── A Python driver ──


def _py_info(pdef: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {
        "id": "acme_widget",
        "name": "Acme Widget",
        "transport": "tcp",
        "commands": {"pair": {"label": "Pair", "params": {"code": {"label": "Code", **pdef}}}},
        **extra,
    }


def test_a_python_driver_is_refused_where_it_is_written_and_named_where_it_loads() -> None:
    info = _py_info({"type": "integer", "secret": True})
    assert check_python_driver_info(info, strict=True).errors == [_refusal(CODE, "integer")]
    loaded = check_python_driver_info(info)
    assert (loaded.errors, loaded.notices) == ([], [_refusal(CODE, "integer")])


def test_a_python_actions_parameter_is_held_to_it() -> None:
    info = _py_info({"type": "string", "secret": True}, actions=[{
        "id": "pair",
        "kind": "setup",
        "label": "Pair",
        "params": {"pin": {"type": "number", "label": "PIN", "secret": True}},
    }])
    assert check_python_driver_info(info, strict=True).errors == [
        _refusal("actions[0] param 'pin'", "number")
    ]


def test_a_python_file_parameter_keeps_its_own_rule_alone() -> None:
    info = _py_info({"type": "file", "secret": True})
    errors = check_python_driver_info(info, strict=True).errors
    assert errors and not any(FIX in message for message in errors)


_SOURCE = '''
from openavc.drivers.base import BaseDriver

CODE_TYPE = "integer"


class AcmeWidget(BaseDriver):
    DRIVER_INFO = {
        "id": "acme_widget",
        "name": "Acme Widget",
        "transport": "tcp",
        "commands": {
            "pair": {"label": "Pair", "params": {"code": {"type": CODE_TYPE, "label": "Code", "secret": True}}},
        },
    }
'''


def test_a_python_type_built_in_code_is_not_judged_and_the_skip_is_named() -> None:
    info, _ = extract_python_driver_info_from_source(_SOURCE, "acme_widget.py")
    assert check_python_driver_info(info, strict=True).errors == []
    assert any("commands.pair.params.code" in skip for skip in python_driver_reference_skips(info))


# ── The warning on a credential's name ──


def _warning(at: str) -> str:
    return (
        f"{at} is named like a credential but is not marked secret, so what is "
        "typed shows on screen and is saved in the project file. Mark it secret "
        "if it holds a password, PIN or key."
    )


@pytest.mark.parametrize("name", [
    "admin_password", "passphrase", "pin", "user_psk", "client_secret", "api_key", "stream_key",
])
def test_an_unmarked_text_parameter_named_like_a_credential_draws_a_warning(name: str) -> None:
    definition = _with_param(name, {"type": "string"})
    assert check_driver_definition(definition, strict=True).errors == []
    assert list(validate_driver_warnings(definition)) == [
        _warning(f"Command 'pair' param '{name}'")
    ]


def test_the_warning_is_on_the_parameter() -> None:
    issues = validate_driver_issues(_with_param("stream_key", {}), strict=True)
    assert issues == [{
        "severity": "warning",
        "message": _warning("Command 'pair' param 'stream_key'"),
        "path": "commands.pair.params.stream_key",
    }]


@pytest.mark.parametrize("name", ["streamKey", "stream-key", "AdminPassword"])
def test_a_name_is_read_without_case_or_separators(name: str) -> None:
    definition = _with_param(name, {"type": "string"})
    assert list(validate_driver_warnings(definition)) == [
        _warning(f"Command 'pair' param '{name}'")
    ]


@pytest.mark.parametrize("name, pdef", [
    ("stream_key", {"type": "string", "secret": True}),
    ("password", {"type": "string"}),
    ("relay_pin", {"type": "integer"}),
    ("pin", {"type": "enum", "values": ["1", "2"]}),
    ("username", {"type": "string"}),
    ("key", {"type": "string"}),
    ("token", {"type": "string"}),
])
def test_a_marked_password_named_or_not_text_parameter_draws_no_warning(
    name: str, pdef: dict[str, Any],
) -> None:
    assert list(validate_driver_warnings(_with_param(name, pdef))) == []


def test_an_unmarked_config_field_named_like_a_credential_draws_a_warning() -> None:
    definition = copy.deepcopy(BASE)
    definition["config_schema"] = {
        "api_key": {"type": "string", "label": "API Key"},
        "notes_secret": {"type": "text", "label": "Notes"},
        "device_pin": {"type": "integer", "label": "Device PIN"},
        "app_secret": {"type": "string", "label": "App Secret", "secret": True},
    }
    warnings = validate_driver_warnings(definition)
    assert list(warnings) == [
        _warning("Config field 'api_key'"),
        _warning("Config field 'notes_secret'"),
    ]
    assert warnings.tags == ["config_schema.api_key", "config_schema.notes_secret"]


def test_an_unmarked_actions_parameter_draws_a_warning() -> None:
    definition = copy.deepcopy(BASE)
    definition["actions"] = [{
        "id": "pair", "label": "Pair", "params": {"pin": {"type": "string", "label": "PIN"}},
    }]
    warnings = validate_driver_warnings(definition)
    assert list(warnings) == [_warning("actions[0] param 'pin'")]
    assert warnings.tags == ["actions[0].params.pin"]


_PY_UNMARKED = '''
from openavc.drivers.base import BaseDriver


class AcmeWidget(BaseDriver):
    DRIVER_INFO = {
        "id": "acme_widget",
        "name": "Acme Widget",
        "transport": "tcp",
        "commands": {
            "start_stream": {"label": "Start Stream", "params": {"stream_key": {"type": "string", "label": "Stream Key"}}},
        },
    }
'''


def test_the_checker_warns_on_a_python_driver_too(tmp_path: Path) -> None:
    path = tmp_path / "acme_widget.py"
    path.write_text(_PY_UNMARKED)
    result = check_driver_file(path)
    assert result.errors == []
    assert result.warnings == [_warning("Command 'start_stream' param 'stream_key'")]


def test_the_checker_says_nothing_once_a_python_driver_marks_it(tmp_path: Path) -> None:
    path = tmp_path / "acme_widget.py"
    path.write_text(_PY_UNMARKED.replace('"label": "Stream Key"', '"label": "Stream Key", "secret": True'))
    result = check_driver_file(path)
    assert (result.errors, result.warnings) == ([], [])
