"""A macro run from a control learns which control ran it.

A press used to call ``macros.execute(macro_id)`` with nothing else, so a macro
had no way to tell one button from another and an array of controls that differ
by a single value needed one macro each, or a script. The sibling branch on the
same dispatcher had resolved ``$value`` into a device command all along, so the
asymmetry was in the macro path alone.

The press now hands over a context the way a trigger does, read in any step as
``$trigger.<field>``: the author's own ``tag`` for the control, its ``element``
id, and the event tokens (``value`` and friends) already scaled exactly as the
device-command branch scales them.

Every control here is invented; this is a platform capability.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from openavc.core.engine import Engine
from openavc.core.project_loader import load_project


def _project(tmp_path: Path, elements: list[dict], macros: list[dict]) -> Path:
    path = tmp_path / "project.avc"
    path.write_text(json.dumps({
        "openavc_version": "0.14.0",
        "project": {"id": "sim", "name": "Sim", "description": ""},
        "devices": [],
        "macros": macros,
        "variables": [{"id": "level", "type": "number", "default": 0}],
        "ui": {
            "settings": {},
            "pages": [{"id": "main", "name": "Main", "elements": elements, "layouts": []}],
        },
    }), encoding="utf-8")
    return path


def _engine_recording_macro_runs(tmp_path: Path, elements: list[dict]):
    """An engine whose macro runs are captured rather than executed."""
    macros = [{"id": "pick_source", "name": "Pick", "steps": []}]
    engine = Engine(_project(tmp_path, elements, macros))
    engine.project = load_project(engine.project_path)
    engine.macros.load_macros([m.model_dump() for m in engine.project.macros])

    runs: list[tuple[str, dict | None]] = []

    async def execute(macro_id, context=None):
        runs.append((macro_id, context))

    engine.macros.execute = execute
    return engine, runs


async def _press(engine, event: str, element_id: str, data: dict | None = None) -> None:
    """Press, then let the loop run the macro.

    A binding starts its macro in a background task so a warm-up sequence
    cannot block the press, so the run has not happened yet when
    handle_ui_event returns.
    """
    await engine.handle_ui_event(event, element_id, data or {})
    await asyncio.sleep(0)


def _button(element_id: str, tag: str | None) -> dict:
    el = {
        "id": element_id, "type": "button", "label": element_id,
        "bindings": {"do": {"press": [{"action": "macro", "macro": "pick_source"}]}},
    }
    if tag is not None:
        el["tag"] = tag
    return el


@pytest.mark.asyncio
async def test_a_press_hands_the_macro_the_control_s_tag(tmp_path) -> None:
    """The reported case: one macro, an array of buttons, each saying which."""
    engine, runs = _engine_recording_macro_runs(
        tmp_path, [_button(f"btn_{n}", str(n)) for n in range(1, 4)],
    )

    for n in range(1, 4):
        await _press(engine, "press", f"btn_{n}")

    assert [ctx["tag"] for _macro, ctx in runs] == ["1", "2", "3"]
    assert [ctx["element"] for _macro, ctx in runs] == ["btn_1", "btn_2", "btn_3"]


@pytest.mark.asyncio
async def test_an_untagged_control_still_names_itself(tmp_path) -> None:
    """Every control has an id whether or not anybody tagged it, so a macro is
    never left with nothing to go on."""
    engine, runs = _engine_recording_macro_runs(tmp_path, [_button("btn_plain", None)])

    await _press(engine, "press", "btn_plain")

    _macro, ctx = runs[0]
    assert ctx["tag"] is None
    assert ctx["element"] == "btn_plain"


@pytest.mark.asyncio
async def test_the_touched_value_rides_along(tmp_path) -> None:
    """``$trigger.value`` answers the same thing ``$value`` answers on the same
    press, rather than being a second, rawer reading of it."""
    engine, runs = _engine_recording_macro_runs(tmp_path, [{
        "id": "vol", "type": "slider", "label": "Volume", "tag": "main",
        "bindings": {"do": {"change": [{"action": "macro", "macro": "pick_source"}]}},
    }])

    await _press(engine, "change", "vol", {"value": 42})

    _macro, ctx = runs[0]
    assert ctx["value"] == 42
    assert ctx["tag"] == "main"


@pytest.mark.asyncio
async def test_the_context_is_readable_as_trigger_fields(tmp_path) -> None:
    """The context is only useful if the steps can read it, and the namespace
    it arrives in is the one a triggered macro already uses."""
    from openavc.core.value_resolver import resolve_ref

    engine, runs = _engine_recording_macro_runs(tmp_path, [_button("btn_7", "hdmi2")])
    await _press(engine, "press", "btn_7")
    _macro, ctx = runs[0]

    class _NoState:
        def has(self, _key): return False
        def get(self, _key): return None

    assert resolve_ref("$trigger.tag", state=_NoState(), trigger_ctx=ctx) == "hdmi2"
    assert resolve_ref("$trigger.element", state=_NoState(), trigger_ctx=ctx) == "btn_7"
    # A field nobody set resolves to None silently, as it does for any trigger.
    assert resolve_ref("$trigger.nope", state=_NoState(), trigger_ctx=ctx) is None


def test_a_tag_survives_a_save_and_load(tmp_path) -> None:
    """The field is declared on the model, not riding extra='allow', so it
    round-trips instead of disappearing on the next save."""
    from openavc.core.project_loader import save_project

    path = _project(tmp_path, [_button("btn_1", "1")],
                    [{"id": "pick_source", "name": "Pick", "steps": []}])
    project = load_project(path)
    assert "tag" in type(project.ui.pages[0].elements[0]).model_fields

    save_project(path, project)
    assert load_project(path).ui.pages[0].elements[0].tag == "1"
