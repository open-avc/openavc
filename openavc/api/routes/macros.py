"""Macro and trigger REST API endpoints."""

from typing import Any

from fastapi import APIRouter, Body, HTTPException

from openavc.api._engine import _get_engine, _rate_limit_test
from openavc.api.errors import api_error as _api_error
from openavc.core.command_params import missing_params_check
from openavc.core.macro_validation import macro_issues

router = APIRouter()


# --- Macros ---


@router.post("/macros/{macro_id}/execute")
async def execute_macro(macro_id: str) -> dict[str, Any]:
    """Execute a macro by ID."""
    # Same runaway guard as /triggers/{id}/test: the only callers are the
    # IDE's manual "run this macro" buttons, so debounce rapid re-firing of
    # the same macro (runtime automation never uses this endpoint).
    #
    # The cloud AI's run_macro tool shares this bucket by key — it's the same
    # "a person asked for this macro, twice" case. The WebSocket
    # `macro.execute` deliberately does NOT: that one carries panel button
    # presses, where a throttle would make a real room stop responding. So the
    # asymmetry is the policy, not drift — this guard belongs on the operator
    # doors, not on the macro engine where it would catch panels and triggers.
    _rate_limit_test(f"macro_execute:{macro_id}")
    engine = _get_engine()
    try:
        await engine.macros.execute(macro_id)
    except ValueError as e:
        raise _api_error(404, str(e))
    except Exception as e:
        # The traceback goes to the log (which the IDE streams live) rather than
        # into the toast — a failing step's exception is rarely a sentence.
        raise _api_error(500, f"Macro '{macro_id}' failed to run — see the log for details.", exc=e)
    return {"status": "executed", "macro_id": macro_id}


@router.post("/macros/{macro_id}/cancel")
async def cancel_macro(macro_id: str) -> dict[str, Any]:
    """Cancel a running macro by ID."""
    engine = _get_engine()
    cancelled = await engine.macros.cancel(macro_id)
    if cancelled:
        return {"status": "cancelled", "macro_id": macro_id}
    return {"status": "not_running", "macro_id": macro_id}


@router.post("/macros/validate")
async def validate_macros(body: Any = Body(...)) -> dict[str, Any]:
    """What is incomplete about these macros, without saving anything.

    The question the macro editor asks after an edit: which steps will not do
    what they say? A `delay` with no seconds, a `device.command` with no
    command chosen, an operator name the runtime does not know -- all of them
    save cleanly today, report "Saved", and then do nothing in the room. The
    sting is that a clean save is the reason nobody ever opens that macro
    again, so the search goes to the projector, the cable and the network.

    The rules are the platform's own, at the same door the cloud AI's macro
    tools use, so a macro built by hand and the same macro written by the AI
    get the same reading. Nothing here blocks and nothing 422s: a half-built
    step is a normal state for somebody mid-edit, which is exactly why the
    project save stays shape-only (`core/macro_validation` records that
    policy). This endpoint is the third option -- show it, refuse nothing.

    Takes the macros posted rather than reading the saved project, because the
    editor holds unsaved edits and the mark has to describe what is on screen.
    Many at once rather than one per call: the macro LIST marks its rows too,
    and a project's worth of macros must not be a project's worth of requests.

    Plugin-registered actions and the drivers' own required parameters come
    from the running engine, which is the other reason this cannot be a copy of
    the rules in the browser: only the engine knows which plugin actions are
    loaded and what each command needs, and a lint that flags a working step is
    worse than no lint at all.
    """
    engine = _get_engine()
    extra_actions = (
        engine.macros.plugin_action_types() if getattr(engine, "macros", None) else frozenset()
    )
    # The other thing only this side knows: what each driver declares its
    # commands need. A step whose command is chosen and whose required
    # parameters are empty used to clear the banner and then be refused by the
    # device on every run.
    missing_params = missing_params_check(
        getattr(engine, "devices", None), getattr(engine, "project", None),
    )

    if not isinstance(body, dict) or not isinstance(body.get("macros"), list):
        raise _api_error(400, 'Expected {"macros": [{"id": ..., "steps": [...], "triggers": [...]}]}')

    results: dict[str, Any] = {}
    for entry in body["macros"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or not entry["id"]:
            raise _api_error(400, "Every macro needs a non-empty 'id'")
        results[entry["id"]] = {
            "issues": macro_issues(
                entry.get("steps") or [],
                entry.get("triggers") or [],
                extra_actions=extra_actions,
                missing_params=missing_params,
            )
        }
    return {"macros": results}


# --- Triggers ---


@router.get("/triggers")
async def list_triggers() -> dict[str, Any]:
    """List all triggers with status."""
    engine = _get_engine()
    return {"triggers": engine.triggers.list_triggers()}


@router.post("/triggers/{trigger_id}/test")
async def test_trigger(trigger_id: str) -> dict[str, Any]:
    """Fire a trigger's macro immediately, bypassing conditions."""
    _rate_limit_test(f"test_trigger:{trigger_id}")
    engine = _get_engine()
    ok = await engine.triggers.test_trigger(trigger_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Trigger '{trigger_id}' not found")
    return {"status": "fired", "trigger_id": trigger_id}
