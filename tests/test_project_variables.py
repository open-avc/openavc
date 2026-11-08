"""The engine's user-variable runtime, pinned by what it does.

Covers the parts no other test reached: the validation warnings (every
sentence, which writes are checked and which are not, re-registration), a
binding's first read of its source and its source_map lookup, a rebind
dropping the old subscriptions, the start-up seeding with no project, and
re-applying state.json after a backup restore. Log lines are filed under the
engine's own logger name, which is how the log view groups them.
"""

from __future__ import annotations

import json
import logging

import pytest

from openavc.core.engine import Engine
from openavc.core.project_loader import load_project
from openavc.core.state_persister import StatePersister

ENGINE_LOG = "openavc.core.engine"


def _engine(tmp_path, variables) -> Engine:
    project = {
        "version": "0.5.0",
        "project": {"id": "p", "name": "P"},
        "variables": variables,
        "macros": [], "devices": [], "device_groups": [], "connections": {},
        "scripts": [], "plugins": {},
        "ui": {"settings": {}, "pages": [
            {"id": "main", "name": "Main", "grid": {"columns": 12, "rows": 8}, "elements": []},
        ]},
        "isc": {"enabled": False, "shared_state": [], "peers": [], "auth_key": ""},
    }
    path = tmp_path / "project.avc"
    path.write_text(json.dumps(project), encoding="utf-8")
    eng = Engine(str(path))
    eng.project = load_project(eng.project_path)
    return eng


def _warnings(caplog) -> list[str]:
    return [
        r.getMessage() for r in caplog.records
        if r.name == ENGINE_LOG and r.levelno == logging.WARNING
    ]


# ── validation warnings ──


def test_number_below_minimum_and_above_maximum_each_warn(tmp_path, caplog):
    eng = _engine(tmp_path, [
        {"id": "level", "type": "number", "default": 5, "validation": {"min": 0, "max": 10}},
    ])
    eng._register_variable_validation()
    with caplog.at_level(logging.WARNING, logger=ENGINE_LOG):
        eng.state.set("var.level", -1, source="ui")
        eng.state.set("var.level", 11, source="macro")
        eng.state.set("var.level", 4, source="ui")
    assert _warnings(caplog) == [
        "Variable validation: var.level — value -1 is below minimum 0.0 (source=ui)",
        "Variable validation: var.level — value 11 exceeds maximum 10.0 (source=macro)",
    ]


def test_both_limits_broken_at_once_warn_minimum_first(tmp_path, caplog):
    eng = _engine(tmp_path, [
        {"id": "odd", "type": "number", "default": 0, "validation": {"min": 10, "max": 0}},
    ])
    eng._register_variable_validation()
    with caplog.at_level(logging.WARNING, logger=ENGINE_LOG):
        eng.state.set("var.odd", 5, source="ui")
    assert _warnings(caplog) == [
        "Variable validation: var.odd — value 5 is below minimum 10.0 (source=ui)",
        "Variable validation: var.odd — value 5 exceeds maximum 0.0 (source=ui)",
    ]


def test_a_value_on_a_limit_is_within_it(tmp_path, caplog):
    eng = _engine(tmp_path, [
        {"id": "level", "type": "number", "default": 5, "validation": {"min": 0, "max": 10}},
    ])
    eng._register_variable_validation()
    with caplog.at_level(logging.WARNING, logger=ENGINE_LOG):
        eng.state.set("var.level", 0, source="ui")
        eng.state.set("var.level", 10, source="ui")
    assert _warnings(caplog) == []


def test_only_one_limit_set_checks_only_that_limit(tmp_path, caplog):
    eng = _engine(tmp_path, [
        {"id": "lo", "type": "number", "default": 0, "validation": {"min": 0}},
        {"id": "hi", "type": "number", "default": 0, "validation": {"max": 0}},
    ])
    eng._register_variable_validation()
    with caplog.at_level(logging.WARNING, logger=ENGINE_LOG):
        eng.state.set("var.lo", 1000, source="ui")
        eng.state.set("var.hi", -1000, source="ui")
    assert _warnings(caplog) == []


def test_a_bool_on_a_number_variable_is_checked_as_a_number(tmp_path, caplog):
    eng = _engine(tmp_path, [
        {"id": "n", "type": "number", "default": 50, "validation": {"min": 10}},
    ])
    eng._register_variable_validation()
    with caplog.at_level(logging.WARNING, logger=ENGINE_LOG):
        eng.state.set("var.n", True, source="ui")
    assert _warnings(caplog) == [
        "Variable validation: var.n — value True is below minimum 10.0 (source=ui)",
    ]


def test_string_outside_allowed_values_warns(tmp_path, caplog):
    eng = _engine(tmp_path, [
        {"id": "mode", "type": "string", "default": "a", "validation": {"allowed": ["a", "b"]}},
    ])
    eng._register_variable_validation()
    with caplog.at_level(logging.WARNING, logger=ENGINE_LOG):
        eng.state.set("var.mode", "c", source="script")
        eng.state.set("var.mode", "b", source="script")
    assert _warnings(caplog) == [
        "Variable validation: var.mode — value 'c' is not in allowed values: ['a', 'b'] (source=script)",
    ]


@pytest.mark.parametrize("variable,value", [
    # a value of the wrong kind for the rule is not checked at all
    ({"type": "number", "validation": {"min": 0, "max": 1}}, "lots"),
    ({"type": "string", "validation": {"allowed": ["a"]}}, 7),
    # an empty allowed list allows anything
    ({"type": "string", "validation": {"allowed": []}}, "z"),
    # the rule belongs to its type: a string rule on a number variable is ignored
    ({"type": "number", "validation": {"allowed": ["a"]}}, "z"),
    ({"type": "string", "validation": {"min": 0}}, -5),
])
def test_values_the_rules_do_not_apply_to_never_warn(tmp_path, caplog, variable, value):
    eng = _engine(tmp_path, [{"id": "v", "default": None, **variable}])
    eng._register_variable_validation()
    with caplog.at_level(logging.WARNING, logger=ENGINE_LOG):
        eng.state.set("var.v", value, source="ui")
    assert _warnings(caplog) == []


def test_system_writes_and_cleared_values_are_not_checked(tmp_path, caplog):
    eng = _engine(tmp_path, [
        {"id": "level", "type": "number", "default": 5, "validation": {"min": 0}},
    ])
    eng._register_variable_validation()
    with caplog.at_level(logging.WARNING, logger=ENGINE_LOG):
        eng.state.set("var.level", -1, source="system")
        eng.state.set("var.level", None, source="ui")
    assert _warnings(caplog) == []


def test_a_variable_without_rules_is_not_watched(tmp_path):
    eng = _engine(tmp_path, [
        {"id": "plain", "type": "number", "default": 0},
        {"id": "ruled", "type": "number", "default": 0, "validation": {"min": 0}},
    ])
    eng._register_variable_validation()
    assert len(eng._var_validation_subs) == 1


def test_registering_again_replaces_the_listeners(tmp_path, caplog):
    eng = _engine(tmp_path, [
        {"id": "level", "type": "number", "default": 5, "validation": {"min": 0}},
    ])
    eng._register_variable_validation()
    eng._register_variable_validation()
    assert len(eng._var_validation_subs) == 1
    with caplog.at_level(logging.WARNING, logger=ENGINE_LOG):
        eng.state.set("var.level", -1, source="ui")
    assert len(_warnings(caplog)) == 1


def test_registering_with_no_project_drops_the_listeners(tmp_path, caplog):
    eng = _engine(tmp_path, [
        {"id": "level", "type": "number", "default": 5, "validation": {"min": 0}},
    ])
    eng._register_variable_validation()
    eng.project = None
    eng._register_variable_validation()
    assert eng._var_validation_subs == []
    with caplog.at_level(logging.WARNING, logger=ENGINE_LOG):
        eng.state.set("var.level", -1, source="ui")
    assert _warnings(caplog) == []


# ── bindings ──


def test_a_binding_reads_its_source_at_once_through_the_map(tmp_path):
    eng = _engine(tmp_path, [
        {"id": "power", "type": "string", "default": "", "source_key": "device.tv.power",
         "source_map": {"on": "ON", "True": "yes"}},
    ])
    eng.state.set("device.tv.power", "on", source="device")
    eng._bind_variable_sources()
    assert eng.state.get("var.power") == "ON"
    # The map is looked up by the value's text, so a bool finds "True".
    eng.state.set("device.tv.power", True, source="device")
    assert eng.state.get("var.power") == "yes"
    # A value the map does not name passes through as it came.
    eng.state.set("device.tv.power", "standby", source="device")
    assert eng.state.get("var.power") == "standby"


def test_a_source_with_no_value_yet_leaves_the_variable_alone(tmp_path):
    eng = _engine(tmp_path, [
        {"id": "power", "type": "string", "default": "", "source_key": "device.tv.power"},
    ])
    eng.state.set("var.power", "kept", source="system")
    eng._bind_variable_sources()
    assert eng.state.get("var.power") == "kept"


def test_a_bound_write_is_tagged_as_the_binding(tmp_path):
    eng = _engine(tmp_path, [
        {"id": "power", "type": "string", "default": "", "source_key": "device.tv.power"},
    ])
    seen = []
    eng.state.subscribe("var.power", lambda k, o, n, s: seen.append((n, s)))
    eng.state.set("device.tv.power", "on", source="device")
    eng._bind_variable_sources()
    eng.state.set("device.tv.power", "off", source="device")
    assert seen == [("on", "variable_binding"), ("off", "variable_binding")]


def test_rebinding_drops_the_old_subscriptions(tmp_path):
    eng = _engine(tmp_path, [
        {"id": "power", "type": "string", "default": "", "source_key": "device.tv.power"},
    ])
    eng._bind_variable_sources()
    eng.project.variables[0].source_key = None
    eng._bind_variable_sources()
    assert eng._var_binding_subs == []
    eng.state.set("device.tv.power", "on", source="device")
    assert eng.state.get("var.power") is None


def test_binding_with_no_project_drops_the_old_subscriptions(tmp_path):
    eng = _engine(tmp_path, [
        {"id": "power", "type": "string", "default": "", "source_key": "device.tv.power"},
    ])
    eng._bind_variable_sources()
    eng.project = None
    eng._bind_variable_sources()
    assert eng._var_binding_subs == []
    eng.state.set("device.tv.power", "on", source="device")
    assert eng.state.get("var.power") is None


def test_a_binding_never_writes_the_variable_it_is_already_writing(tmp_path):
    eng = _engine(tmp_path, [
        {"id": "a", "type": "string", "default": "", "source_key": "var.b"},
        {"id": "b", "type": "string", "default": "", "source_key": "var.a"},
    ])
    eng._bind_variable_sources()
    writes = []
    eng.state.subscribe("var.*", lambda k, o, n, s: writes.append((k, n, s)))
    eng.state.set("var.a", "x", source="ui")
    # a -> b by the binding, and b's binding back to a writes nothing more
    # (the binding's own subscriber is told first, so the order is the store's).
    assert sorted(writes) == [("var.a", "x", "ui"), ("var.b", "x", "variable_binding")]
    assert eng._var_binding_active == set()


def test_a_cycle_whose_map_keeps_changing_the_value_stops_after_one_hop(tmp_path):
    eng = _engine(tmp_path, [
        {"id": "a", "type": "string", "default": "", "source_key": "var.b",
         "source_map": {"1": "2", "2": "3", "3": "1"}},
        {"id": "b", "type": "string", "default": "", "source_key": "var.a"},
    ])
    eng._bind_variable_sources()
    eng.state.set("var.b", "1", source="ui")
    # b -> a ("2") -> b ("2"), and a's binding is still writing a: cut there.
    assert (eng.state.get("var.a"), eng.state.get("var.b")) == ("2", "2")
    assert eng._var_binding_active == set()


# ── seeding at start ──


def test_seeding_with_no_project_writes_nothing(tmp_path):
    eng = _engine(tmp_path, [{"id": "a", "type": "string", "default": "x"}])
    eng.project = None
    assert eng._init_variable_values({"var.a": "saved"}) == set()
    assert eng.state.get("var.a") is None


def test_seeding_writes_as_the_system(tmp_path):
    eng = _engine(tmp_path, [
        {"id": "a", "type": "string", "default": "x"},
        {"id": "b", "type": "string", "default": "y", "persist": True},
    ])
    seen = []
    eng.state.subscribe("var.*", lambda k, o, n, s: seen.append((k, n, s)))
    keys = eng._init_variable_values({"var.b": "saved", "var.a": "ignored"})
    assert keys == {"var.b"}
    assert seen == [("var.a", "x", "system"), ("var.b", "saved", "system")]


# ── after a backup restore ──


def test_reload_persisted_state_restores_only_persistent_variables(tmp_path):
    eng = _engine(tmp_path, [
        {"id": "kept", "type": "string", "default": "d1", "persist": True},
        {"id": "missing", "type": "string", "default": "d2", "persist": True},
        {"id": "live", "type": "string", "default": "d3"},
    ])
    (tmp_path / "state.json").write_text(json.dumps({"var.kept": "restored"}), encoding="utf-8")
    eng.persister = StatePersister(tmp_path / "state.json", eng.state)
    eng.state.set("var.live", "running", source="ui")
    eng.state.set("var.missing", "stale", source="ui")
    eng.reload_persisted_state()
    assert eng.state.get("var.kept") == "restored"
    assert eng.state.get("var.missing") == "d2"
    assert eng.state.get("var.live") == "running"
    assert eng.persister._persistent_keys == {"var.kept", "var.missing"}
    eng.persister.stop()


def test_reload_persisted_state_without_a_persister_does_nothing(tmp_path):
    eng = _engine(tmp_path, [{"id": "a", "type": "string", "default": "d", "persist": True}])
    eng.persister = None
    eng.state.set("var.a", "running", source="ui")
    eng.reload_persisted_state()
    assert eng.state.get("var.a") == "running"
