"""Judging a driver file returns messages; it never raises.

A part of the error walk or the warnings walk that raises on a value of the
wrong kind is named, with the place it was reading, and the rest of the walk
still runs. The library load skips the one file it cannot take and loads the
rest. ``python -m openavc.drivers.check`` reports every file it was handed.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from openavc.drivers import avcdriver_semantic, check, driver_loader, registry
from openavc.drivers.avcdriver_semantic import (
    validate_driver_definition,
    validate_driver_issues,
    validate_driver_warnings,
)

BASE: dict[str, Any] = {
    "id": "acme_widget",
    "name": "Acme Widget",
    "transport": "tcp",
    "commands": {"power_on": {"label": "On", "send": "PON\r"}},
    "responses": [{"match": r"PWR=(\d)", "mappings": [{"group": 1, "state": "power"}]}],
    "state_variables": {"power": {"type": "string", "label": "Power"}},
}

# Strict mode's last part reports this, so seeing it says the walk went on.
TYPO = {"state_varibles": {}}
TYPO_MESSAGE = "unknown key 'state_varibles' (did you mean 'state_variables'?)"


def _broke(*_args: Any, **_kwargs: Any) -> Any:
    raise RuntimeError("broke here")


def _errors(definition: dict[str, Any], **kwargs: Any) -> list[tuple[str, str]]:
    return [
        (issue["path"], issue["message"])
        for issue in validate_driver_issues(definition, **kwargs)
        if issue["severity"] == "error"
    ]


# --- the error walk -----------------------------------------------------------

# The name a part's check is reached by in avcdriver_semantic, and the place
# the message is tagged with: the place the part was reading when it raised.
ERROR_PARTS = [
    ("response_rule_errors", "responses[0]"),
    ("CommandSection", "commands"),
    ("child_param_reference_errors", "commands"),
    ("device_setting_errors", "device_settings.mode"),
    ("push_block_errors", "push"),
    ("auth_errors", "auth"),
    ("liveness_errors", "liveness"),
    ("validate_actions", "actions"),
    ("state_variable_errors", "state_variables.power"),
    ("value_conversion_errors", "state_variables"),
    ("frame_parser_errors", "frame_parser"),
    ("send_frame_errors", "send_frame"),
    ("RosterNames", "child_entity_types"),
    ("QueryEntries", "polling"),
    ("routing_block_errors", "routing"),
    ("validate_substitutions", ""),
]


@pytest.mark.parametrize("name,where", ERROR_PARTS)
def test_a_part_that_raises_is_named_and_the_walk_goes_on(monkeypatch, name, where):
    monkeypatch.setattr(avcdriver_semantic, name, _broke)
    definition = {
        **BASE,
        **TYPO,
        "device_settings": {"mode": {"type": "string", "label": "Mode", "state_key": "power"}},
    }
    errors = _errors(definition, strict=True)
    raised = [(path, m) for path, m in errors if "broke here" in m]
    assert len(raised) == 1
    path, message = raised[0]
    assert path == where
    opening = f"{where}: could not be checked" if where else "This driver could not be checked"
    assert message.startswith(f"{opening} (broke here). ")
    assert errors[-1] == ("", TYPO_MESSAGE)


def test_the_config_field_part_names_the_field_it_was_reading(monkeypatch):
    monkeypatch.setattr(avcdriver_semantic, "config_learned_from_errors", _broke)
    definition = {
        **BASE,
        **TYPO,
        "config_schema": {"serial": {"type": "string", "learned_from": "power"}},
    }
    errors = _errors(definition, strict=True)
    assert [path for path, m in errors if "broke here" in m] == ["config_schema.serial"]
    assert errors[-1] == ("", TYPO_MESSAGE)


def test_a_discovery_check_that_raises_is_named(monkeypatch):
    errors = validate_driver_definition({**BASE, **TYPO}, discovery_validator=_broke, strict=True)
    tagged = list(zip(errors.tags, errors))  # type: ignore[attr-defined]
    assert [tag for tag, m in tagged if "broke here" in m] == ["discovery"]
    assert tagged[-1] == ("", TYPO_MESSAGE)


@pytest.mark.parametrize("name,where", [
    ("unknown_key_errors", ""),
    ("platform_version_errors", "min_platform_version"),
])
def test_strict_modes_own_checks_are_named_too(monkeypatch, name, where):
    monkeypatch.setattr(avcdriver_semantic, name, _broke)
    errors = _errors({**BASE, **TYPO}, strict=True)
    assert [path for path, m in errors if "broke here" in m] == [where]


def test_a_part_that_raises_keeps_what_it_said_before_it_raised(monkeypatch):
    real = avcdriver_semantic.response_rule_errors

    def second_rule_breaks(index, *args):
        if index == 1:
            raise RuntimeError("broke here")
        return real(index, *args)

    monkeypatch.setattr(avcdriver_semantic, "response_rule_errors", second_rule_breaks)
    definition = {**BASE, "responses": ["not a rule", {"match": "x"}]}
    errors = _errors(definition)
    assert errors[0][0] == "responses[0]"
    assert [path for path, m in errors if "broke here" in m] == ["responses[1]"]


# --- the warnings walk ----------------------------------------------------------

WARNING_PARTS = [
    ("masked_default_warnings", "config_schema"),
    ("port_open_warnings", "discovery"),
    ("unlabeled_param_warnings", "commands"),
    ("poll_length_warnings", "polling.queries"),
    ("unwatched_silence_warnings", "liveness"),
]


@pytest.mark.parametrize("name,where", WARNING_PARTS)
def test_a_warning_part_that_raises_is_a_warning_and_the_walk_goes_on(monkeypatch, name, where):
    monkeypatch.setattr(avcdriver_semantic, name, _broke)
    warnings = validate_driver_warnings(BASE)
    tagged = list(zip(warnings.tags, warnings))  # type: ignore[attr-defined]
    assert [(tag, m.split(" (", 1)[0]) for tag, m in tagged if "broke here" in m] == [
        (where, f"{where}: could not be checked for warnings")
    ]
    issues = validate_driver_issues(BASE)
    assert [i["severity"] for i in issues if "broke here" in i["message"]] == ["warning"]


def test_every_warning_part_runs_after_one_raises(monkeypatch):
    monkeypatch.setattr(avcdriver_semantic, "masked_default_warnings", _broke)
    definition = {**BASE, "commands": {"go": {"send": "GO {x}\r", "params": {"x": {"type": "integer"}}}}}
    tags = list(validate_driver_warnings(definition).tags)  # type: ignore[attr-defined]
    assert tags[0] == "config_schema"
    assert "commands.go.params.x" in tags


# --- the library load ----------------------------------------------------------


@pytest.fixture
def library(tmp_path, monkeypatch):
    """An empty driver folder, and a registry without this file's ids."""
    monkeypatch.setattr(registry, "_DRIVER_REGISTRY", {
        k: v for k, v in registry._DRIVER_REGISTRY.items() if not k.startswith("acme_widget")
    })
    modules = set(sys.modules)
    folder = tmp_path / "driver_repo"
    folder.mkdir()
    yield folder
    for name in set(sys.modules) - modules:
        if name.startswith("openavc_driver_"):
            sys.modules.pop(name, None)


def _write(folder: Path, name: str, definition: dict[Any, Any]) -> Path:
    path = folder / name
    path.write_text(yaml.safe_dump(definition, sort_keys=False), encoding="utf-8")
    return path


def test_a_file_the_check_raised_on_is_skipped_and_the_rest_load(library, caplog):
    _write(library, "a_number_id.avcdriver", {**BASE, "id": 5})
    _write(library, "b_push.avcdriver", {**BASE, "id": "acme_widget_push", "push": {"type": "multicast", 1: "x"}})
    _write(library, "c_good.avcdriver", BASE)
    with caplog.at_level(logging.WARNING, logger="openavc.drivers.driver_loader"):
        loaded = driver_loader.load_driver_files([library])
    assert loaded == 1
    assert registry.get_driver_class("acme_widget") is not None
    assert registry.get_driver_class("acme_widget_push") is None
    skipped = [r.getMessage() for r in caplog.records if "Invalid driver definition" in r.getMessage()]
    assert len(skipped) == 2
    assert "a_number_id.avcdriver" in skipped[0] and "discovery: could not be checked" in skipped[0]
    assert "b_push.avcdriver" in skipped[1] and "push: unknown key(s): 1 " in skipped[1]


def test_top_level_keys_that_are_not_text_load_with_a_warning_each(library, caplog):
    _write(library, "acme_widget.avcdriver", {**BASE, 1: "x", "zz": "y"})
    with caplog.at_level(logging.WARNING, logger="openavc.drivers.driver_loader"):
        assert driver_loader.load_driver_files([library]) == 1
    messages = [r.getMessage() for r in caplog.records]
    assert any(m.endswith("unknown key '1'") for m in messages)
    assert any(m.endswith("unknown key 'zz'") for m in messages)


def test_a_definition_whose_load_raises_is_skipped_and_the_rest_load(library, monkeypatch, caplog):
    real = driver_loader.load_driver_file

    def load(filepath):
        if filepath.name.startswith("a_"):
            raise RuntimeError("broke here")
        return real(filepath)

    monkeypatch.setattr(driver_loader, "load_driver_file", load)
    _write(library, "a_first.avcdriver", {**BASE, "id": "acme_widget_first"})
    _write(library, "b_good.avcdriver", BASE)
    with caplog.at_level(logging.ERROR, logger="openavc.drivers.driver_loader"):
        assert driver_loader.load_driver_files([library]) == 1
    assert registry.get_driver_class("acme_widget") is not None
    assert any("a_first.avcdriver" in r.getMessage() for r in caplog.records)


def test_a_definition_file_that_raises_reads_as_none(library, monkeypatch, caplog):
    monkeypatch.setattr(driver_loader, "companion_relpath_from_def", _broke)
    path = _write(library, "acme_widget.avcdriver", BASE)
    with caplog.at_level(logging.WARNING, logger="openavc.drivers.driver_loader"):
        assert driver_loader.load_driver_file(path) is None
    assert any("acme_widget.avcdriver" in r.getMessage() and "broke here" in r.getMessage()
               for r in caplog.records)


PYTHON_DRIVER = (
    "from openavc.drivers.base import BaseDriver\n\n\n"
    "class AcmeWidget(BaseDriver):\n"
    "    DRIVER_INFO = {{'id': {id!r}, 'name': 'Acme Widget', 'transport': 'tcp'}}\n"
)


def test_a_python_driver_whose_load_raises_is_skipped_and_the_rest_load(library, monkeypatch, caplog):
    real = driver_loader.load_python_driver_file

    def load(filepath):
        if filepath.name.startswith("a_"):
            raise RuntimeError("broke here")
        return real(filepath)

    monkeypatch.setattr(driver_loader, "load_python_driver_file", load)
    (library / "a_first.py").write_text(PYTHON_DRIVER.format(id="acme_widget_first"), encoding="utf-8")
    (library / "acme_widget.py").write_text(PYTHON_DRIVER.format(id="acme_widget"), encoding="utf-8")
    with caplog.at_level(logging.ERROR, logger="openavc.drivers.driver_loader"):
        assert driver_loader.load_python_drivers([library]) == 1
    assert registry.get_driver_class("acme_widget") is not None
    assert any("a_first.py" in r.getMessage() for r in caplog.records)


# --- python -m openavc.drivers.check -----------------------------------------------


def test_a_folder_with_a_file_that_cannot_be_read_reports_every_file(tmp_path, capsys):
    (tmp_path / "a_latin1.avcdriver").write_bytes("id: acme_widget\nname: Caf\xe9\n".encode("latin-1"))
    _write(tmp_path, "b_good.avcdriver", BASE)
    assert check.main([str(tmp_path)]) == 1
    err = capsys.readouterr().err
    assert "a_latin1.avcdriver: error: " in err
    assert "b_good.avcdriver: error" not in err
    assert "2 file(s) checked, 1 with errors" in err


def test_a_check_that_raises_is_that_files_error(tmp_path, monkeypatch):
    monkeypatch.setattr(check, "_check_yaml_driver", _broke)
    path = _write(tmp_path, "acme_widget.avcdriver", BASE)
    result = check.check_driver_file(path)
    assert result.errors == ["could not be checked (broke here)"]
    assert not result.readable
