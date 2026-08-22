"""Tests for MacroEngine."""

from unittest.mock import AsyncMock

import pytest

from openavc.core.device_manager import DeviceManager
from openavc.core.event_bus import EventBus
from openavc.core.macro_engine import MacroEngine
from openavc.core.state_store import StateStore


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
    # Mock send_command so we don't need real devices
    devices.send_command = AsyncMock()
    return MacroEngine(state, events, devices)


async def test_state_set_step(macro_engine, core):
    state, _ = core
    macro_engine.load_macros([{
        "id": "test",
        "name": "Test",
        "steps": [
            {"action": "state.set", "key": "var.x", "value": 42}
        ],
    }])
    await macro_engine.execute("test")
    assert state.get("var.x") == 42


async def test_device_command_step(macro_engine):
    macro_engine.load_macros([{
        "id": "test",
        "name": "Test",
        "steps": [
            {"action": "device.command", "device": "proj1", "command": "power_on", "params": {}}
        ],
    }])
    await macro_engine.execute("test")
    macro_engine.devices.send_command.assert_called_once_with("proj1", "power_on", {})


async def test_event_emit_step(macro_engine, core):
    _, events = core
    received = []
    events.on("custom.test_event", lambda e, p: received.append(p))

    macro_engine.load_macros([{
        "id": "test",
        "name": "Test",
        "steps": [
            {"action": "event.emit", "event": "custom.test_event", "payload": {"msg": "hi"}}
        ],
    }])
    await macro_engine.execute("test")
    assert len(received) == 1
    assert received[0]["msg"] == "hi"


async def test_nested_macro(macro_engine, core):
    state, _ = core
    macro_engine.load_macros([
        {
            "id": "inner",
            "name": "Inner",
            "steps": [{"action": "state.set", "key": "var.inner_ran", "value": True}],
        },
        {
            "id": "outer",
            "name": "Outer",
            "steps": [
                {"action": "state.set", "key": "var.outer_ran", "value": True},
                {"action": "macro", "macro": "inner"},
            ],
        },
    ])
    await macro_engine.execute("outer")
    assert state.get("var.outer_ran") is True
    assert state.get("var.inner_ran") is True


async def test_error_continues_to_next_step(macro_engine, core):
    state, _ = core
    macro_engine.devices.send_command = AsyncMock(side_effect=Exception("boom"))

    macro_engine.load_macros([{
        "id": "test",
        "name": "Test",
        "steps": [
            {"action": "device.command", "device": "proj1", "command": "power_on"},
            {"action": "state.set", "key": "var.after_error", "value": True},
        ],
    }])
    await macro_engine.execute("test")
    # Second step should still run
    assert state.get("var.after_error") is True


async def test_unknown_macro(macro_engine):
    # Should raise ValueError for unknown macro
    with pytest.raises(ValueError, match="not found"):
        await macro_engine.execute("nonexistent")


async def test_multi_step_sequence(macro_engine, core):
    state, _ = core
    macro_engine.load_macros([{
        "id": "seq",
        "name": "Sequence",
        "steps": [
            {"action": "state.set", "key": "var.a", "value": 1},
            {"action": "state.set", "key": "var.b", "value": 2},
            {"action": "state.set", "key": "var.c", "value": 3},
        ],
    }])
    await macro_engine.execute("seq")
    assert state.get("var.a") == 1
    assert state.get("var.b") == 2
    assert state.get("var.c") == 3


# --- Device group command tests ---


async def test_group_command_all_devices(macro_engine, core):
    """Group command sends to all devices in the group."""
    state, _ = core
    state.set("device.proj1.connected", True)
    state.set("device.proj2.connected", True)
    state.set("device.proj3.connected", True)

    macro_engine.load_groups([{
        "id": "projectors",
        "device_ids": ["proj1", "proj2", "proj3"],
    }])
    macro_engine.load_macros([{
        "id": "power_on_all",
        "name": "Power On All",
        "steps": [
            {"action": "group.command", "group": "projectors", "command": "power_on", "params": {}}
        ],
    }])

    await macro_engine.execute("power_on_all")

    assert macro_engine.devices.send_command.call_count == 3
    called_devices = {call.args[0] for call in macro_engine.devices.send_command.call_args_list}
    assert called_devices == {"proj1", "proj2", "proj3"}
    for call in macro_engine.devices.send_command.call_args_list:
        assert call.args[1] == "power_on"


async def test_group_command_concurrent(macro_engine, core):
    """Commands execute concurrently (all sent via asyncio.gather)."""
    state, _ = core
    state.set("device.d1.connected", True)
    state.set("device.d2.connected", True)

    macro_engine.load_groups([{
        "id": "displays",
        "device_ids": ["d1", "d2"],
    }])
    macro_engine.load_macros([{
        "id": "test",
        "name": "Test",
        "steps": [
            {"action": "group.command", "group": "displays", "command": "input_select", "params": {"input": "hdmi1"}}
        ],
    }])

    await macro_engine.execute("test")

    assert macro_engine.devices.send_command.call_count == 2
    for call in macro_engine.devices.send_command.call_args_list:
        assert call.args[1] == "input_select"
        assert call.args[2] == {"input": "hdmi1"}


async def test_group_command_partial_offline(macro_engine, core):
    """Offline devices are skipped, online devices still get commands."""
    state, _ = core
    state.set("device.proj1.connected", True)
    state.set("device.proj2.connected", False)
    state.set("device.proj3.connected", True)

    macro_engine.load_groups([{
        "id": "projectors",
        "device_ids": ["proj1", "proj2", "proj3"],
    }])
    macro_engine.load_macros([{
        "id": "test",
        "name": "Test",
        "steps": [
            {"action": "group.command", "group": "projectors", "command": "power_on"}
        ],
    }])

    await macro_engine.execute("test")

    assert macro_engine.devices.send_command.call_count == 2
    called_devices = {call.args[0] for call in macro_engine.devices.send_command.call_args_list}
    assert called_devices == {"proj1", "proj3"}


async def test_group_command_empty_group(macro_engine, core):
    """Empty group is a no-op (no error, no commands sent)."""
    macro_engine.load_groups([{
        "id": "empty",
        "device_ids": [],
    }])
    macro_engine.load_macros([{
        "id": "test",
        "name": "Test",
        "steps": [
            {"action": "group.command", "group": "empty", "command": "power_on"}
        ],
    }])

    await macro_engine.execute("test")
    assert macro_engine.devices.send_command.call_count == 0


async def test_group_command_unknown_group(macro_engine, core):
    """Unknown group logs error but doesn't crash."""
    macro_engine.load_groups([])
    macro_engine.load_macros([{
        "id": "test",
        "name": "Test",
        "steps": [
            {"action": "group.command", "group": "nonexistent", "command": "power_on"}
        ],
    }])

    await macro_engine.execute("test")
    assert macro_engine.devices.send_command.call_count == 0


# --- ui.navigate step tests ---


async def test_ui_navigate_broadcasts_message(core):
    """ui.navigate broadcasts the same payload the element press-side action uses."""
    state, events = core
    devices = DeviceManager(state, events)
    devices.send_command = AsyncMock()
    broadcast = AsyncMock()
    engine = MacroEngine(state, events, devices, broadcast_ws=broadcast)

    engine.load_macros([{
        "id": "go_home",
        "name": "Go home",
        "steps": [
            {"action": "ui.navigate", "page": "home"}
        ],
    }])
    await engine.execute("go_home")

    broadcast.assert_awaited_once_with({"type": "ui.navigate", "page_id": "home"})


async def test_ui_navigate_emits_page_event(core):
    """ui.navigate emits ui.page.<page_id> like the element press-side action does."""
    state, events = core
    devices = DeviceManager(state, events)
    devices.send_command = AsyncMock()
    engine = MacroEngine(state, events, devices, broadcast_ws=AsyncMock())

    received = []
    events.on("ui.page.welcome", lambda e, p: received.append(e))

    engine.load_macros([{
        "id": "welcome",
        "name": "Welcome",
        "steps": [{"action": "ui.navigate", "page": "welcome"}],
    }])
    await engine.execute("welcome")

    assert received == ["ui.page.welcome"]


async def test_ui_navigate_back_does_not_emit_page_event(core):
    """$back / $dismiss are overlay-stack controls, not page targets — no ui.page.* emit."""
    state, events = core
    devices = DeviceManager(state, events)
    devices.send_command = AsyncMock()
    broadcast = AsyncMock()
    engine = MacroEngine(state, events, devices, broadcast_ws=broadcast)

    received_events = []

    def _capture(event_name, _payload):
        if event_name.startswith("ui.page."):
            received_events.append(event_name)

    events.on("ui.page.*", _capture)

    engine.load_macros([{
        "id": "dismiss",
        "name": "Dismiss",
        "steps": [{"action": "ui.navigate", "page": "$back"}],
    }])
    await engine.execute("dismiss")

    broadcast.assert_awaited_once_with({"type": "ui.navigate", "page_id": "$back"})
    assert received_events == []


async def test_ui_navigate_missing_page_raises(macro_engine):
    """Missing 'page' field raises during step execution (caught and logged, not crash)."""
    # macro_engine fixture has no broadcast_ws wired; that's a separate path.
    macro_engine._broadcast_ws = AsyncMock()
    macro_engine.load_macros([{
        "id": "broken",
        "name": "Broken",
        "steps": [{"action": "ui.navigate"}],  # no page
    }])
    # Errors are logged but execution continues (no stop_on_error)
    await macro_engine.execute("broken")
    # broadcast must NOT have fired since the step raised before broadcasting
    macro_engine._broadcast_ws.assert_not_awaited()


async def test_ui_navigate_without_broadcast_does_not_crash(macro_engine):
    """If broadcast_ws is None (test/plugin contexts), the step warns but doesn't crash."""
    assert macro_engine._broadcast_ws is None
    macro_engine.load_macros([{
        "id": "go",
        "name": "Go",
        "steps": [{"action": "ui.navigate", "page": "home"}],
    }])
    # Should complete without raising
    await macro_engine.execute("go")


# --- $trigger.<field>: macros can read the firing trigger's context ---


async def test_trigger_ref_resolves_in_state_set(macro_engine, core):
    """$trigger.<field> in a state.set value reads the event payload."""
    state, _ = core
    macro_engine.load_macros([{
        "id": "m",
        "name": "M",
        "steps": [{"action": "state.set", "key": "var.last", "value": "$trigger.data"}],
    }])
    await macro_engine.execute("m", context={"event": "device.response.x", "data": "POWER_ON"})
    assert state.get("var.last") == "POWER_ON"


async def test_trigger_ref_resolves_in_device_params(macro_engine):
    """$trigger.<field> in device.command params reads the trigger context."""
    macro_engine.load_macros([{
        "id": "m",
        "name": "M",
        "steps": [{
            "action": "device.command", "device": "proj1", "command": "set_input",
            "params": {"input": "$trigger.new_value"},
        }],
    }])
    await macro_engine.execute(
        "m", context={"key": "var.src", "old_value": "HDMI1", "new_value": "HDMI2"}
    )
    macro_engine.devices.send_command.assert_called_once_with(
        "proj1", "set_input", {"input": "HDMI2"}
    )


async def test_unknown_state_ref_in_params_warns(macro_engine, caplog):
    """An unknown $state key in a macro param resolves to None and warns (the
    shared resolver's behavior — previously a silent None)."""
    import logging

    macro_engine.load_macros([{
        "id": "m",
        "name": "M",
        "steps": [{
            "action": "device.command", "device": "proj1", "command": "set_input",
            "params": {"input": "$var.missing"},
        }],
    }])
    with caplog.at_level(logging.WARNING):
        await macro_engine.execute("m")
    macro_engine.devices.send_command.assert_called_once_with(
        "proj1", "set_input", {"input": None}
    )
    assert "var.missing" in caplog.text


async def test_trigger_ref_is_none_without_context(macro_engine, core):
    """Run directly (no trigger context) -> $trigger.* resolves to None."""
    state, _ = core
    macro_engine.load_macros([{
        "id": "m",
        "name": "M",
        "steps": [{"action": "state.set", "key": "var.last", "value": "$trigger.data"}],
    }])
    await macro_engine.execute("m")  # no context
    assert state.get("var.last") is None


async def test_trigger_ref_does_not_shadow_state_refs(macro_engine, core):
    """A normal $var.* / $device.* ref still resolves from state even when a
    trigger context is present (only the trigger.* namespace reads context)."""
    state, _ = core
    state.set("var.src", "HDMI3", source="test")
    macro_engine.load_macros([{
        "id": "m",
        "name": "M",
        "steps": [{"action": "state.set", "key": "var.copy", "value": "$var.src"}],
    }])
    await macro_engine.execute("m", context={"data": "ignored"})
    assert state.get("var.copy") == "HDMI3"


async def test_conditional_branches_on_trigger_field(macro_engine, core):
    """A conditional step can branch on a trigger.<field> key."""
    state, _ = core
    macro_engine.load_macros([{
        "id": "m",
        "name": "M",
        "steps": [{
            "action": "conditional",
            "condition": {"key": "trigger.data", "operator": "eq", "value": "ON"},
            "then_steps": [{"action": "state.set", "key": "var.out", "value": "matched"}],
            "else_steps": [{"action": "state.set", "key": "var.out", "value": "no_match"}],
        }],
    }])
    await macro_engine.execute("m", context={"data": "ON"})
    assert state.get("var.out") == "matched"
    await macro_engine.execute("m", context={"data": "OFF"})
    assert state.get("var.out") == "no_match"


async def test_skip_if_honors_trigger_field(macro_engine, core):
    """A step's skip_if guard can reference a trigger.<field> key."""
    state, _ = core
    macro_engine.load_macros([{
        "id": "m",
        "name": "M",
        "steps": [{
            "action": "state.set", "key": "var.ran", "value": True,
            "skip_if": {"key": "trigger.event", "operator": "eq", "value": "skip.me"},
        }],
    }])
    await macro_engine.execute("m", context={"event": "skip.me"})
    assert state.get("var.ran") is None  # step skipped
    await macro_engine.execute("m", context={"event": "other"})
    assert state.get("var.ran") is True  # step ran


# --- What a failed step REPORTS (Q-104) ---
#
# A macro that dies mid-run used to look, from a panel, exactly like one that
# worked. These pin the two halves of the frame that fixes that: the sentence
# it carries, and the cases where the frame is emitted at all.


@pytest.fixture
def failing_engine(core):
    """An engine whose 'projector' is not there and whose 'screen' is."""
    state, events = core
    devices = DeviceManager(state, events)

    async def _send(device_id, command, params=None):
        if device_id in _ABSENT:
            raise ConnectionError(f"Device '{device_id}' is not connected")
        return True

    devices.send_command = _send
    state.set("device.projector.name", "Ceiling Projector")
    state.set("device.projector.connected", True)
    state.set("device.screen.name", "Screen")
    state.set("device.screen.connected", True)
    return MacroEngine(state, events, devices)


_ABSENT = {"projector"}


def _collect(events, pattern="macro.*"):
    seen: list[tuple[str, dict]] = []
    events.on(pattern, lambda e, p: seen.append((e, p)))
    return seen


async def test_a_failed_step_says_why_in_words_somebody_can_read(failing_engine, core):
    """The reason, translated where the exception still exists.

    ``error`` keeps what was raised, because a log and the IDE's run history
    want it. ``message`` is the same failure written for whoever is standing
    at the panel -- and the panel cannot derive it, holding no device names,
    no host and no exception type.
    """
    _, events = core
    seen = _collect(events, "macro.step_error.*")
    failing_engine.load_macros([{
        "id": "on", "name": "System On",
        "steps": [{"action": "device.command", "device": "projector",
                   "command": "power_on"}],
    }])

    await failing_engine.execute("on")

    assert len(seen) == 1
    payload = seen[0][1]
    assert payload["error"] == "Device 'projector' is not connected"
    assert payload["call_chain"] == ["on"]
    # The device is named the way the room names it, and the sentence is the
    # one a direct press already produces -- one failure, one wording.
    assert payload["message"] == "Ceiling Projector is not connected."


async def test_a_macro_that_fails_mid_run_still_reports_completed(failing_engine, core):
    """Why the message has to ride the step, not the end of the run.

    ``stop_on_error`` is off by default, so the steps after a failure still
    run and the macro ends on ``macro.completed`` -- the identical frame a
    clean run sends. Anything watching only the outcome cannot tell the two
    apart, which is exactly how this failure stayed silent.
    """
    _, events = core
    seen = _collect(events)
    failing_engine.load_macros([{
        "id": "on", "name": "System On",
        "steps": [
            {"action": "device.command", "device": "projector", "command": "power_on"},
            {"action": "state.set", "key": "var.phase", "value": "done"},
        ],
    }])

    await failing_engine.execute("on")

    kinds = [e.rsplit(".", 1)[0] for e, _ in seen]
    assert "macro.step_error" in kinds
    assert kinds[-1] == "macro.completed"
    assert "macro.error" not in kinds
    assert failing_engine.state.get("var.phase") == "done"


async def test_a_failure_inside_a_sub_macro_names_what_it_happened_inside(
    failing_engine, core,
):
    """``macro_id`` is the sub-macro, which nobody at a panel has heard of.

    A panel deciding whether a failure belongs to something somebody there
    pressed has to be able to ask "was this inside the macro I started", and
    the button they pressed ran the OUTER one.
    """
    _, events = core
    seen = _collect(events, "macro.step_error.*")
    failing_engine.load_macros([
        {"id": "system_on", "name": "System On",
         "steps": [{"action": "macro", "macro": "projector_on"}]},
        {"id": "projector_on", "name": "Projector On",
         "steps": [{"action": "device.command", "device": "projector",
                    "command": "power_on"}]},
    ])

    await failing_engine.execute("system_on")

    assert len(seen) == 1
    payload = seen[0][1]
    assert payload["macro_id"] == "projector_on"
    assert payload["call_chain"] == ["projector_on", "system_on"]


async def test_the_stop_on_error_reason_does_not_number_the_step_twice(
    failing_engine, core,
):
    """One step number in the sentence, not two.

    The detail string already opens with ``step N/T``, so wrapping it in
    "Step N/T failed (...)" said it twice before saying the reason.
    """
    _, events = core
    seen = _collect(events, "macro.error.*")
    failing_engine.load_macros([{
        "id": "on", "name": "System On", "stop_on_error": True,
        "steps": [{"action": "device.command", "device": "projector",
                   "command": "power_on"}],
    }])

    await failing_engine.execute("on")

    assert len(seen) == 1
    assert seen[0][1]["error"].count("1/1") == 1


async def test_a_group_command_that_reached_nobody_reports_a_failed_step(
    macro_engine, core,
):
    """Nothing happened, and until now that looked like success.

    Every member offline means no send was even attempted, so nothing raised
    and no step error existed -- and with no attempt there was no
    ``group_complete`` progress event either, leaving the run silent on every
    surface at once.
    """
    state, events = core
    state.set("device.dsp.connected", False)
    state.set("device.dsp.name", "Rack DSP")
    state.set("device.amp.connected", False)
    state.set("device.amp.name", "Zone Amp")
    seen = _collect(events, "macro.step_error.*")

    macro_engine.load_groups([{"id": "audio", "device_ids": ["dsp", "amp"]}])
    macro_engine.load_macros([{
        "id": "m", "name": "Mute", "steps": [
            {"action": "group.command", "group": "audio", "command": "mute"},
        ],
    }])

    await macro_engine.execute("m")

    assert len(seen) == 1
    # The first member, by the order the group declares them: a name somebody
    # can go and look at beats a count of how many were unreachable.
    assert seen[0][1]["message"] == "Rack DSP is not connected."
    assert seen[0][1]["group"] == "audio"
    assert macro_engine.devices.send_command.call_count == 0


async def test_a_group_command_that_reached_somebody_reports_nothing(
    macro_engine, core,
):
    """One dead member out of three is not a failed step.

    A group step is a fan-out. The room did most of what was asked, and a
    message about it lands mid-sequence on somebody starting a class.
    """
    state, events = core
    for did, connected in (("d1", True), ("d2", False), ("d3", True)):
        state.set(f"device.{did}.connected", connected)
    seen = _collect(events, "macro.step_error.*")

    macro_engine.load_groups([{"id": "displays", "device_ids": ["d1", "d2", "d3"]}])
    macro_engine.load_macros([{
        "id": "m", "name": "Off", "steps": [
            {"action": "group.command", "group": "displays", "command": "power_off"},
        ],
    }])

    await macro_engine.execute("m")

    assert seen == []


async def test_every_member_of_a_group_reports_in_the_order_it_was_declared(
    macro_engine, core,
):
    """Attempted-then-skipped is our bookkeeping; declared order is the room's."""
    state, events = core
    for did, connected in (("d1", False), ("d2", True), ("d3", False)):
        state.set(f"device.{did}.connected", connected)
    seen = _collect(events, "macro.progress.*")

    macro_engine.load_groups([{"id": "displays", "device_ids": ["d1", "d2", "d3"]}])
    macro_engine.load_macros([{
        "id": "m", "name": "Off", "steps": [
            {"action": "group.command", "group": "displays", "command": "power_off"},
        ],
    }])

    await macro_engine.execute("m")

    group_events = [p for _, p in seen if p.get("status") == "group_complete"]
    assert len(group_events) == 1
    assert [r["device_id"] for r in group_events[0]["device_results"]] == ["d1", "d2", "d3"]
    # An offline member reads the same way it would from a device.command
    # step: the panel must not learn a second sentence for the same fact.
    assert group_events[0]["device_results"][0]["message"] == "d1 is not connected."
