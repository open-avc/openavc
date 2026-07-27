"""Tests for child-entity state access through scripts, plugins, macros,
and triggers.

These subsystems all consume child-entity state through the same flat
state-key surface they use for top-level device state — there's no
dedicated child-entity API. This file verifies that contract end-to-end
so future refactors can't silently strand the consumers.

Plan section: openavc-device-children-plan.md §9 "ScriptAPI / PluginAPI /
Macro / Trigger updates" — the section explicitly notes that no API
changes are expected and mostly tests are required.
"""

from __future__ import annotations

import asyncio
import sys
import textwrap
from unittest.mock import AsyncMock, MagicMock

import pytest

from server.core.device_manager import DeviceManager
from server.core.event_bus import EventBus
from server.core.macro_engine import MacroEngine
from server.core.plugin_api import PluginAPI
from server.core.plugin_registry import PluginRegistry
from server.core.script_engine import ScriptEngine
from server.core.state_store import StateStore
from server.core.trigger_engine import TriggerEngine


CHILD_KEY = "device.ctrl1.encoder.005.signal_present"
CHILD_NAME = "device.ctrl1.encoder.005.name"
CHILD_PATTERN = "device.ctrl1.encoder.*.signal_present"


@pytest.fixture
def core():
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    return state, events


@pytest.fixture
def macro_engine(core):
    state, events = core
    devices = DeviceManager(state, events)
    devices.send_command = AsyncMock()
    return MacroEngine(state, events, devices)


# ---------------------------------------------------------------------------
# Triggers
# ---------------------------------------------------------------------------


async def test_trigger_state_change_fires_on_child_key_pattern(core, macro_engine):
    """state_change trigger with a glob pattern on a child-entity key
    fires the macro when any matching child key changes. Covers both
    the deeply-nested key shape (four dotted segments) and sibling
    routing through the same trigger.
    """
    state, _ = core
    # Use mute-then-unmute toggles to count fires: the macro sets var.fired
    # to True. Reset between events to confirm the second fire really
    # happened.
    macro_engine.load_macros([{
        "id": "on_signal_change",
        "name": "Signal Change",
        "steps": [{"action": "state.set", "key": "var.fired", "value": True}],
    }])
    trigger_engine = TriggerEngine(state, core[1], macro_engine)
    trigger_engine.load_triggers([{
        "id": "on_signal_change",
        "name": "Signal Change",
        "triggers": [{
            "id": "trg_pattern",
            "type": "state_change",
            "enabled": True,
            "state_key": CHILD_PATTERN,
            "state_operator": "any",
        }],
    }])
    await trigger_engine.start()
    try:
        state.set(CHILD_KEY, True, source="driver")
        await asyncio.sleep(0.1)
        assert state.get("var.fired") is True, (
            "Trigger did not fire for the deeply-nested child key"
        )

        # Confirm a sibling encoder ID routes through the same trigger.
        state.set("var.fired", False)
        state.set("device.ctrl1.encoder.012.signal_present", False, source="driver")
        await asyncio.sleep(0.1)
        assert state.get("var.fired") is True, (
            "Trigger did not fire for a sibling child key matched by the same "
            "wildcard"
        )

        # A non-matching property (same parent, different prop) must NOT
        # fire — confirms the pattern's property suffix is honored.
        state.set("var.fired", False)
        state.set("device.ctrl1.encoder.012.name", "X", source="driver")
        await asyncio.sleep(0.1)
        assert state.get("var.fired") is False
    finally:
        await trigger_engine.stop()


async def test_trigger_state_operator_eq_on_child_key(core, macro_engine):
    """state_operator: 'eq' filtering works against child-entity state
    values just like any other key — the trigger fires only when the
    decoder reports the matching mode.
    """
    state, _ = core
    macro_engine.load_macros([{
        "id": "on_video_wall_mode",
        "name": "Video Wall Mode",
        "steps": [{"action": "state.set", "key": "var.entered_vw", "value": True}],
    }])
    trigger_engine = TriggerEngine(state, core[1], macro_engine)
    trigger_engine.load_triggers([{
        "id": "on_video_wall_mode",
        "name": "Video Wall Mode",
        "triggers": [{
            "id": "trg_eq",
            "type": "state_change",
            "enabled": True,
            "state_key": "device.ctrl1.decoder.001.mode",
            "state_operator": "eq",
            "state_value": "VW",
        }],
    }])
    await trigger_engine.start()
    try:
        state.set("device.ctrl1.decoder.001.mode", "MX", source="driver")
        await asyncio.sleep(0.05)
        assert state.get("var.entered_vw") is None

        state.set("device.ctrl1.decoder.001.mode", "VW", source="driver")
        await asyncio.sleep(0.05)
        assert state.get("var.entered_vw") is True
    finally:
        await trigger_engine.stop()


async def test_trigger_guard_condition_reads_child_key(core, macro_engine):
    """A trigger guard condition (`conditions: [{key, operator, value}]`)
    reads child state through `state.get(key)`. Verifies that the four-
    segment key works as a condition target.
    """
    state, _ = core
    macro_engine.load_macros([{
        "id": "switch_only_when_online",
        "name": "Switch Only When Online",
        "steps": [{"action": "state.set", "key": "var.executed", "value": True}],
    }])
    trigger_engine = TriggerEngine(state, core[1], macro_engine)
    trigger_engine.load_triggers([{
        "id": "switch_only_when_online",
        "name": "Switch Only When Online",
        "triggers": [{
            "id": "trg_guarded",
            "type": "event",
            "enabled": True,
            "event_pattern": "test.switch_request",
            "conditions": [{
                "key": "device.ctrl1.encoder.005.online",
                "operator": "eq",
                "value": True,
            }],
        }],
    }])
    await trigger_engine.start()
    try:
        # Encoder offline — guard fails, macro doesn't run.
        state.set("device.ctrl1.encoder.005.online", False, source="driver")
        await core[1].emit("test.switch_request", {})
        await asyncio.sleep(0.05)
        assert state.get("var.executed") is None

        # Bring it online — guard passes.
        state.set("device.ctrl1.encoder.005.online", True, source="driver")
        await core[1].emit("test.switch_request", {})
        await asyncio.sleep(0.05)
        assert state.get("var.executed") is True
    finally:
        await trigger_engine.stop()


# ---------------------------------------------------------------------------
# Macros
# ---------------------------------------------------------------------------


async def test_macro_dollar_resolves_child_state_key(core, macro_engine):
    """`$<state_key>` in a macro step param resolves to the child entity's
    current value. Mirrors how a route_decoder macro would forward the
    decoder's current source onto another action.
    """
    state, _ = core
    state.set(CHILD_NAME, "Lobby Encoder", source="driver")

    macro_engine.load_macros([{
        "id": "forward_name",
        "name": "Forward Name",
        "steps": [
            {"action": "state.set", "key": "var.captured_name",
             "value": f"${CHILD_NAME}"},
        ],
    }])
    await macro_engine.execute("forward_name")
    assert state.get("var.captured_name") == "Lobby Encoder"


async def test_macro_skip_if_reads_child_key(core, macro_engine):
    """skip_if on a step reads child state — when the gate is true the
    step runs; flipping the gate skips it.
    """
    state, _ = core

    macro_engine.load_macros([{
        "id": "gated_set",
        "name": "Gated Set",
        "steps": [{
            "action": "state.set",
            "key": "var.ran",
            "value": True,
            "skip_if": {
                "key": "device.ctrl1.encoder.005.online",
                "operator": "eq",
                "value": False,
            },
        }],
    }])

    state.set("device.ctrl1.encoder.005.online", False, source="driver")
    await macro_engine.execute("gated_set")
    assert state.get("var.ran") is None

    state.set("device.ctrl1.encoder.005.online", True, source="driver")
    await macro_engine.execute("gated_set")
    assert state.get("var.ran") is True


# ---------------------------------------------------------------------------
# Scripts
# ---------------------------------------------------------------------------


@pytest.fixture
def script_engine(core, tmp_path):
    """ScriptEngine wired to a fresh tmp scripts/ directory."""
    state, events = core
    devices = DeviceManager(state, events)
    scripts_root = tmp_path
    (scripts_root / "scripts").mkdir()
    se = ScriptEngine(state, events, devices, scripts_root)
    se.install()
    yield se
    se.unload_all()
    sys.modules.pop("openavc", None)


def _write_script(scripts_root, filename, code):
    path = scripts_root / "scripts" / filename
    path.write_text(textwrap.dedent(code), encoding="utf-8")
    return path


async def test_script_reads_child_state_via_state_get(script_engine, core, tmp_path):
    """A user script reads child state with `state.get(key)`. Verifies
    the script_api's _StateProxy passes four-segment keys through cleanly.
    """
    state, _ = core
    state.set(CHILD_KEY, True, source="driver")

    _write_script(tmp_path, "read_child.py", """\
        from openavc import on_event, state

        @on_event("test.read_child")
        async def handle(event):
            v = state.get("device.ctrl1.encoder.005.signal_present")
            state.set("var.read_value", v)
    """)
    script_engine.load_scripts([{
        "id": "read_child", "file": "read_child.py", "enabled": True,
    }])
    await core[1].emit("test.read_child", {})
    await asyncio.sleep(0.05)
    assert state.get("var.read_value") is True


async def test_script_get_namespace_returns_child_state(
    script_engine, core, tmp_path,
):
    """state.get_namespace('device.ctrl1.encoder.005.') returns the full
    per-child state dict — the bulk-read path the IDE Child Entities tab
    documents (state_store.py:get_namespace).
    """
    state, _ = core
    state.set("device.ctrl1.encoder.005.name", "Lobby", source="driver")
    state.set("device.ctrl1.encoder.005.ip", "10.0.0.5", source="driver")
    state.set("device.ctrl1.encoder.005.signal_present", True, source="driver")

    _write_script(tmp_path, "ns_child.py", """\
        from openavc import on_event, state

        @on_event("test.ns")
        async def handle(event):
            # get_namespace strips the prefix, so keys come back short.
            ns = state.get_namespace("device.ctrl1.encoder.005")
            state.set("var.ns_count", len(ns))
            state.set("var.ns_name", ns.get("name"))
            state.set("var.ns_signal", ns.get("signal_present"))
    """)
    script_engine.load_scripts([{
        "id": "ns_child", "file": "ns_child.py", "enabled": True,
    }])
    await core[1].emit("test.ns", {})
    await asyncio.sleep(0.05)
    assert state.get("var.ns_count") == 3
    assert state.get("var.ns_name") == "Lobby"
    assert state.get("var.ns_signal") is True


async def test_script_on_state_change_matches_child_pattern(
    script_engine, core, tmp_path,
):
    """@on_state_change with a child-key glob fires per matching key — and
    the handler sees the actual key, not just the pattern."""
    state, _ = core

    _write_script(tmp_path, "subscribe_child.py", """\
        from openavc import on_state_change, state

        @on_state_change("device.ctrl1.encoder.*.signal_present")
        async def handle(key, old_value, new_value):
            state.set("var.subscribed_key", key)
            state.set("var.subscribed_value", new_value)
    """)
    script_engine.load_scripts([{
        "id": "subscribe_child", "file": "subscribe_child.py", "enabled": True,
    }])
    state.set("device.ctrl1.encoder.007.signal_present", True, source="driver")
    await asyncio.sleep(0.05)
    assert state.get("var.subscribed_key") == \
        "device.ctrl1.encoder.007.signal_present"
    assert state.get("var.subscribed_value") is True


# ---------------------------------------------------------------------------
# Plugins
# ---------------------------------------------------------------------------


@pytest.fixture
def plugin_api(core):
    state, events = core
    return PluginAPI(
        plugin_id="sample",
        capabilities=["state_read", "state_write", "event_subscribe"],
        config={},
        registry=PluginRegistry("sample"),
        state_store=state,
        event_bus=events,
        macro_engine=MagicMock(),
        device_manager=MagicMock(),
        platform_id="test",
    )


async def test_plugin_state_get_reads_child_key(plugin_api, core):
    state, _ = core
    state.set(CHILD_KEY, True, source="driver")
    assert await plugin_api.state_get(CHILD_KEY) is True


async def test_plugin_state_get_pattern_reads_child_namespace(plugin_api, core):
    """state_get_pattern returns the full child-state dict for the
    matching glob — confirms PluginAPI's wrapper around
    StateStore.get_matching honors four-segment keys.
    """
    state, _ = core
    state.set("device.ctrl1.encoder.005.name", "Lobby", source="driver")
    state.set("device.ctrl1.encoder.005.ip", "10.0.0.5", source="driver")
    state.set("device.ctrl1.encoder.007.name", "Auditorium", source="driver")

    result = await plugin_api.state_get_pattern(
        "device.ctrl1.encoder.*.name",
    )
    assert result == {
        "device.ctrl1.encoder.005.name": "Lobby",
        "device.ctrl1.encoder.007.name": "Auditorium",
    }


async def test_plugin_state_subscribe_matches_child_pattern(plugin_api, core):
    state, _ = core
    changes: list[tuple[str, object]] = []

    async def on_change(key, value, old_value):
        changes.append((key, value))

    await plugin_api.state_subscribe(CHILD_PATTERN, on_change)
    state.set(CHILD_KEY, True, source="driver")
    state.set("device.ctrl1.encoder.007.signal_present", False, source="driver")
    # An unrelated key (not matching the pattern) must NOT fire — confirms
    # the prefix-index filter respects the property suffix.
    state.set("device.ctrl1.encoder.007.name", "X", source="driver")
    await asyncio.sleep(0.05)

    assert changes == [
        (CHILD_KEY, True),
        ("device.ctrl1.encoder.007.signal_present", False),
    ]
