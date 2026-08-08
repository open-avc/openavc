"""Tests for the shared $-reference resolver (openavc.core.value_resolver).

Covers the namespace order (event context -> trigger context -> state store),
the precise warn-only-on-unknown-state rule, and StateStore.has().
"""

import logging

from openavc.core.state_store import StateStore
from openavc.core.value_resolver import resolve_ref


def make_state(values: dict) -> StateStore:
    state = StateStore()
    for key, val in values.items():
        state.set(key, val)
    return state


# --- Event context: $value/$input/$output/$mute resolve from the UI event ---

def test_event_context_tokens_resolve():
    state = StateStore()
    ctx = {"value": 42, "input": 3, "output": 1, "mute": True}
    assert resolve_ref("$value", state=state, event_ctx=ctx) == 42
    assert resolve_ref("$input", state=state, event_ctx=ctx) == 3
    assert resolve_ref("$output", state=state, event_ctx=ctx) == 1
    assert resolve_ref("$mute", state=state, event_ctx=ctx) is True


def test_event_value_never_falls_through_to_state(caplog):
    """$value resolves from the event context, never from state.get("value")
    (the cross-surface footgun this resolver kills)."""
    state = StateStore()
    state._store["value"] = "from_state"  # would be the silent-wrong behavior
    ctx = {"value": 7, "input": None, "output": None, "mute": None}
    with caplog.at_level(logging.WARNING):
        assert resolve_ref("$value", state=state, event_ctx=ctx) == 7
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


# --- State store: $var.* / $device.* resolve from the state store ---

def test_state_var_ref_resolves():
    state = make_state({"var.x": "hello"})
    assert resolve_ref("$var.x", state=state) == "hello"


def test_device_state_ref_resolves():
    state = make_state({"device.acme_widget.power": "on"})
    assert resolve_ref("$device.acme_widget.power", state=state) == "on"


# --- Trigger context: $trigger.<field> resolves from the trigger context ---

def test_trigger_ref_resolves_from_trigger_ctx():
    state = StateStore()
    assert resolve_ref("$trigger.foo", state=state, trigger_ctx={"foo": "bar"}) == "bar"


# --- Literal passthrough: non-$ values (and non-strings) come back unchanged ---

def test_literal_passthrough():
    state = StateStore()
    assert resolve_ref("hello", state=state) == "hello"
    assert resolve_ref(42, state=state) == 42
    assert resolve_ref(None, state=state) is None
    assert resolve_ref(True, state=state) is True


# --- Unknown state key -> None + WARNING ---

def test_unknown_state_ref_warns(caplog):
    state = StateStore()
    with caplog.at_level(logging.WARNING):
        result = resolve_ref("$var.missing", state=state)
    assert result is None
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings
    assert "var.missing" in caplog.text


def test_dollar_number_literal_warns(caplog):
    """A "$5"-style literal is a state ref to "5" — unknown, so it warns
    (surfacing the mistake) rather than silently resolving to None."""
    state = StateStore()
    with caplog.at_level(logging.WARNING):
        assert resolve_ref("$5", state=state) is None
    assert [r for r in caplog.records if r.levelno == logging.WARNING]
    assert "$5" in caplog.text


# --- The "empty when not fired" / legit-falsy cases do NOT warn ---

def test_trigger_miss_no_warning(caplog):
    """A $trigger.* miss (no trigger context, or absent field) returns None
    silently — "empty when not fired by a trigger" is documented behavior."""
    state = StateStore()
    with caplog.at_level(logging.WARNING):
        assert resolve_ref("$trigger.miss", state=state) is None
        assert resolve_ref("$trigger.miss", state=state, trigger_ctx={}) is None
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_legit_none_state_value_no_warning(caplog):
    """A state key that exists but holds None resolves to None with NO warning
    (exercises has(): present-but-falsy is a real value, not a typo)."""
    state = StateStore()
    state.set("device.acme_widget.connected", True)
    state.set("device.acme_widget.connected", None)  # a real stored None
    assert state.has("device.acme_widget.connected")
    with caplog.at_level(logging.WARNING):
        assert resolve_ref("$device.acme_widget.connected", state=state) is None
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


# --- StateStore.has() ---

def test_state_store_has():
    state = StateStore()
    assert state.has("var.x") is False
    state.set("var.x", 1)
    assert state.has("var.x") is True
    # present-but-None still counts as present
    state.set("var.x", None)
    assert state.has("var.x") is True
    assert state.get("var.x") is None
