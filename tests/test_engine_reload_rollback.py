"""Tests for Engine project-reload rollback paths.

When reload_project raises midway, the engine restores the previous project
and re-syncs lightweight subsystems. The rollback branch must not leave state
that the normal path wouldn't — in particular, trigger listeners must not
stack up if `triggers.start()` already succeeded before the failure point.
"""

import json

import pytest

from openavc.core.engine import Engine


def _project_with_state_trigger(tmp_path):
    """Write a v0.4.0 project with one state_change trigger to disk."""
    project = {
        "version": "0.4.0",
        "project": {"id": "p", "name": "P"},
        "variables": [{"id": "x", "type": "string", "default": "", "label": "X"}],
        "macros": [{
            "id": "m1",
            "name": "M1",
            "steps": [{"action": "state.set", "key": "var.fired", "value": True}],
            "triggers": [{
                "id": "trg_1",
                "type": "state_change",
                "enabled": True,
                "state_key": "var.x",
                "state_operator": "any",
            }],
        }],
        "devices": [],
        "device_groups": [],
        "connections": {},
        "scripts": [],
        "plugins": {},
        "ui": {
            "settings": {},
            "pages": [{"id": "main", "name": "Main", "grid": {"columns": 12, "rows": 8}, "elements": []}],
        },
        "isc": {"enabled": False},
        "themes": [],
    }
    project_path = tmp_path / "project.avc"
    project_path.write_text(json.dumps(project), encoding="utf-8")
    return project_path


@pytest.mark.asyncio
async def test_rollback_does_not_double_register_trigger_listeners(tmp_path, monkeypatch):
    """Regression for A9: if reload fails AFTER triggers.start() succeeds,
    the rollback branch must stop triggers before re-starting them, or the
    state-change/event listener lists stack up and triggers fire 2x per change.
    """
    project_path = _project_with_state_trigger(tmp_path)
    eng = Engine(str(project_path))

    # Load the project synchronously so we can drive reload_project later.
    from openavc.core.project_loader import load_project
    eng.project = load_project(project_path)
    eng._running = True

    # Prime macros + triggers like Engine.start() would, without spinning up
    # the rest of the stack (devices, plugins, scripts, ISC, etc.).
    macros_data = [m.model_dump() for m in eng.project.macros]
    eng.macros.load_macros(macros_data)
    eng.triggers.load_triggers(macros_data)
    await eng.triggers.start()

    baseline = len(eng.triggers._state_sub_ids)
    assert baseline == 1, "precondition: one state_change trigger registered"

    # Inject a failure into a step that runs AFTER the trigger restart in
    # the reconcile — _reload_isc fits, it's the last awaited step before
    # the broadcast.
    async def boom():
        raise RuntimeError("simulated late-reload failure")

    monkeypatch.setattr(eng, "_reload_isc", boom)

    with pytest.raises(RuntimeError, match="simulated late-reload failure"):
        await eng.reload_project()

    # Project should be rolled back to its previous value, and the trigger
    # listener count should match the baseline — not double it.
    assert eng.project is not None
    assert len(eng.triggers._state_sub_ids) == baseline, (
        f"Rollback double-registered listeners: "
        f"{len(eng.triggers._state_sub_ids)} != baseline {baseline}. "
        f"start() was called again without stop() first."
    )

    # Sanity check: firing the trigger should call the macro exactly once.
    fire_count = {"n": 0}
    original_execute = eng.macros.execute

    async def counting_execute(macro_id, *a, **k):
        fire_count["n"] += 1
        return await original_execute(macro_id, *a, **k)

    eng.macros.execute = counting_execute
    eng.state.set("var.x", "trigger-me", source="test")
    # Give the listener callback the event-loop tick it needs.
    import asyncio
    await asyncio.sleep(0.05)
    assert fire_count["n"] == 1, f"trigger fired {fire_count['n']} times, expected 1"

    await eng.triggers.stop()
