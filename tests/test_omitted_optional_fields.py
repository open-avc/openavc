"""A field nobody filled in is stored as ``null``, and every read of it has to
expect one.

The project models declare their optional fields ``X | None = None`` and the
save dumps with ``model_dump(mode="json")`` and no ``exclude_none`` — so a
field the author simply did not use is written into the ``.avc`` as an explicit
``null`` rather than left out. That is fine in itself and is what makes a saved
project self-describing. What is not fine is a reader written as
``t.get("state_operator", "any")``: **the key exists**, so the default never
applies, the reader gets ``None``, and the documented default is unreachable
through the API save path.

That cost a whole trigger type. A ``state_change`` trigger authored without an
operator — which the docs say means "fire on any change" — logged
``invalid operator 'None'`` and returned, on every fire, forever. It never
showed in the IDE because the macro editor always writes an operator, so this
was live only for triggers written by the API, by the AI, or by hand.

The cases below are the class, not only the instance: every macro step kind and
every trigger type is driven from a REAL model dump, because a test that
hand-writes the dict it wants proves nothing about what the save path produces.

Every device, macro and trigger here is invented. This is platform behaviour.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from openavc.core.device_manager import DeviceManager
from openavc.core.event_bus import EventBus
from openavc.core.macro_engine import MacroEngine
from openavc.core.project_loader import (
    MacroConfig,
    MacroStep,
    ProjectConfig,
    ProjectMeta,
    TriggerConfig,
    load_project,
    save_project,
)
from openavc.core.state_store import StateStore
from openavc.core.trigger_engine import TriggerEngine


def _engines() -> tuple[StateStore, EventBus, MacroEngine]:
    state, events = StateStore(), EventBus()
    state.set_event_bus(events)
    devices = DeviceManager(state, events)
    devices.send_command = AsyncMock()
    return state, events, MacroEngine(state, events, devices)


def _project(macro: MacroConfig) -> ProjectConfig:
    return ProjectConfig(
        project=ProjectMeta(id="sim", name="Sim"), macros=[macro],
    )


def _fire_macro() -> MacroConfig:
    """A macro whose only step is a state write, so "did it run" is a value."""
    return MacroConfig(
        id="on_occupancy", name="On Occupancy",
        steps=[MacroStep(action="state.set", key="var.fired", value=True)],
    )


def _saved_and_reloaded(tmp_path: Path, macro: MacroConfig) -> dict[str, Any]:
    """Put a macro through the real save and load, and hand back what the
    engine would be given.

    Not a hand-written dict: the whole defect lives in what ``save_project``
    writes for a field nobody set, so a test that skips the file skips the bug.
    """
    path = tmp_path / "project.avc"
    save_project(path, _project(macro))
    reloaded = load_project(path)
    return reloaded.macros[0].model_dump(mode="json")


# --- The mechanism, pinned ---------------------------------------------------


def test_an_omitted_field_is_written_into_the_file_as_null(tmp_path) -> None:
    """If this ever stops being true the readers below are no longer the
    interesting part — so it is pinned rather than assumed."""
    macro = _fire_macro()
    macro.triggers = [TriggerConfig(
        id="trg_occupancy", type="state_change", state_key="var.occupancy",
    )]
    path = tmp_path / "project.avc"
    save_project(path, _project(macro))

    stored = json.loads(path.read_text(encoding="utf-8"))
    trigger = stored["macros"][0]["triggers"][0]
    assert "state_operator" in trigger, "the key is present, which is the whole point"
    assert trigger["state_operator"] is None


# --- The trigger that never fired --------------------------------------------


@pytest.mark.parametrize("delay_seconds", [0.0, 0.05], ids=["immediate", "after a delay"])
async def test_a_state_change_trigger_with_no_operator_fires_on_any_change(
    tmp_path, delay_seconds,
) -> None:
    """The documented default, reached through the save path.

    Both timings on purpose. The delayed path re-checks the condition when the
    delay is up, in a second reader of the same field — and it is unreachable
    while the first one refuses, so fixing only the first is what would expose
    it. A fix tested at one timing is half a fix that looks whole.
    """
    macro = _fire_macro()
    macro.triggers = [TriggerConfig(
        id="trg_occupancy", type="state_change", state_key="var.occupancy",
        delay_seconds=delay_seconds,
    )]
    dumped = _saved_and_reloaded(tmp_path, macro)

    state, events, macros = _engines()
    macros.load_macros([dumped])
    triggers = TriggerEngine(state, events, macros)
    triggers.load_triggers([dumped])
    await triggers.start()
    try:
        state.set("var.occupancy", True, source="test")
        for _ in range(40):
            await asyncio.sleep(0.02)
            if state.get("var.fired"):
                break
        assert state.get("var.fired") is True, (
            "the trigger never fired — the operator it was never given is being "
            "read as the string 'None'"
        )
    finally:
        await triggers.stop()


async def test_an_operator_that_was_chosen_still_decides(tmp_path) -> None:
    """The guard on the guard: filling the default in must not swallow a real
    operator, or every conditional trigger in the product becomes fire-always."""
    macro = _fire_macro()
    macro.triggers = [TriggerConfig(
        id="trg_occupancy", type="state_change", state_key="var.level",
        state_operator="gt", state_value=10,
    )]
    dumped = _saved_and_reloaded(tmp_path, macro)

    state, events, macros = _engines()
    macros.load_macros([dumped])
    triggers = TriggerEngine(state, events, macros)
    triggers.load_triggers([dumped])
    await triggers.start()
    try:
        state.set("var.level", 5, source="test")
        await asyncio.sleep(0.15)
        assert state.get("var.fired") is None, "5 is not greater than 10"

        state.set("var.level", 50, source="test")
        for _ in range(40):
            await asyncio.sleep(0.02)
            if state.get("var.fired"):
                break
        assert state.get("var.fired") is True
    finally:
        await triggers.stop()


# --- The same class, swept over every step kind ------------------------------


#: One of each, carrying only the fields that kind needs — so every OTHER field
#: on the step arrives as an explicit null.
_STEP_KINDS = {
    "delay": MacroStep(action="delay", seconds=0.01),
    "state.set": MacroStep(action="state.set", key="var.a", value=1),
    "event.emit": MacroStep(action="event.emit", event="custom.thing"),
    "ui.navigate": MacroStep(action="ui.navigate", page="main"),
    "device.command": MacroStep(action="device.command", device="acme_1", command="power_on"),
    "conditional": MacroStep(
        action="conditional",
        condition={"key": "var.a", "operator": "eq", "value": 1},
        then_steps=[MacroStep(action="state.set", key="var.b", value=2)],
    ),
    "wait_until": MacroStep(
        action="wait_until",
        condition={"key": "var.a", "operator": "eq", "value": 1},
        timeout=0.2,
    ),
}


@pytest.mark.parametrize("kind", sorted(_STEP_KINDS))
async def test_every_step_kind_survives_the_fields_it_did_not_use(tmp_path, kind) -> None:
    """A step carries seventeen fields it does not use, all of them null.

    This is the sweep the trigger defect asked for, kept as a test rather than
    a one-off: the readers are spread across the engine, and the way this class
    of bug arrives is somebody adding a `.get(name, default)` for a field the
    save writes as null.
    """
    dumped = _saved_and_reloaded(
        tmp_path, MacroConfig(id="m", name="M", steps=[_STEP_KINDS[kind]]),
    )
    state, _events, macros = _engines()
    state.set("var.a", 1, source="test")
    macros.load_macros([dumped])

    await asyncio.wait_for(macros.execute("m"), timeout=5)


async def test_a_conditional_step_with_no_condition_does_not_kill_the_macro(
    tmp_path,
) -> None:
    """Found by that sweep, and it is not the same failure as the trigger.

    A `conditional` step with no condition is malformed — but the project save
    is shape-only on purpose, so a hand-edited or AI-authored project reaches
    the engine holding one. The progress line for every step asks the step to
    describe itself, and the conditional branch read `condition` with a `{}`
    default that the null defeats: `'NoneType' object has no attribute 'get'`,
    raised while announcing step one, so the macro died before running ANY of
    its steps. The `wait_until` branch on the next line already had it right.
    """
    dumped = _saved_and_reloaded(tmp_path, MacroConfig(
        id="m", name="M",
        steps=[
            MacroStep(action="conditional"),
            MacroStep(action="state.set", key="var.reached_the_end", value=True),
        ],
    ))
    state, _events, macros = _engines()
    macros.load_macros([dumped])

    await asyncio.wait_for(macros.execute("m"), timeout=5)

    assert state.get("var.reached_the_end") is True, (
        "a malformed step took the rest of the macro with it"
    )
