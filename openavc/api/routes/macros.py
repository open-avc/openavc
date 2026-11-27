"""Macro and trigger REST API endpoints."""

from typing import Any

from openavc.api.host_policy import ProgrammerRoute
from fastapi import APIRouter, Body, HTTPException

from openavc.api._engine import _get_engine, _rate_limit_test
from openavc.api.errors import api_error as _api_error
from openavc.core.command_params import (
    file_command_check,
    missing_params_check,
    param_label_check,
    required_refusal_check,
    unsaved_value_check,
)
from openavc.core.macro_validation import TriggerIds, macro_issues, trigger_holders

router = APIRouter(route_class=ProgrammerRoute)


# --- Macros ---


@router.post("/macros/{macro_id}/execute")
async def execute_macro(macro_id: str) -> dict[str, Any]:
    """Execute a macro by ID.

    ``status`` is how the run ended: ``completed``, ``failed`` (at least one
    step did not run, whether or not the macro carried on past it),
    ``cancelled``, ``skipped`` (it never started; ``code`` and ``message`` say
    why, as its ``macro.skipped`` event does), or ``running`` when it is still
    going after the operator wait.
    All of those are a 200 — the request succeeded; what it reports is how the
    macro went. A 4xx/5xx here means the request itself could not be served.
    """
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
        # Bounded: the macro runs in its own task and this waits only long
        # enough to report an ordinary one's outcome. A macro is allowed to
        # wait forever (`wait_until` with no timeout), and holding the request
        # for that told the caller a running macro had failed as soon as some
        # client or proxy gave up on the socket.
        end = await engine.macros.execute_detached(macro_id)
    except ValueError as e:
        raise _api_error(404, str(e))
    except Exception as e:
        # The traceback goes to the log (which the IDE streams live) rather than
        # into the toast — a failing step's exception is rarely a sentence.
        raise _api_error(500, f"Macro '{macro_id}' failed to run — see the log for details.", exc=e)
    return {**end.answer(), "macro_id": macro_id}


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
    It is also how a trigger's id is read: across every macro posted, in the
    order posted, so a trigger another trigger takes the id from is marked.

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
    # And what the step's form calls each of those parameters, so the editor
    # can name the field it shows rather than the driver's key for it.
    param_label = param_label_check(
        getattr(engine, "devices", None), getattr(engine, "project", None),
    )
    # And the runtime's own refusal of such a step, in the same names, for the
    # sentence the record carries.
    required_sentence = required_refusal_check(
        getattr(engine, "devices", None), getattr(engine, "project", None),
    )
    # And a command no macro can run at all: one that takes or hands back a
    # file is refused at run time, in this same sentence.
    file_command = file_command_check(
        getattr(engine, "devices", None), getattr(engine, "project", None),
    )
    # And a value a step saved on another system that this one does not have:
    # the run refuses it in this sentence, so the step is marked until it is
    # entered here (core/saved_params.py). The editor's door only.
    unsaved = unsaved_value_check(
        getattr(engine, "devices", None), getattr(engine, "project", None),
    )

    macros = _posted_macros(body)
    # A trigger's id names one trigger across every macro posted, in the order
    # posted, which is the project's: the one the engine keeps is the last.
    holders = trigger_holders(macros)

    results: dict[str, Any] = {}
    for entry in macros:
        results[entry["id"]] = {
            "issues": macro_issues(
                entry.get("steps") or [],
                entry.get("triggers") or [],
                trigger_ids=TriggerIds(holders, entry["id"]),
                extra_actions=extra_actions,
                missing_params=missing_params,
                param_label=param_label,
                required_sentence=required_sentence,
                file_command=lambda step: file_command(step) or unsaved(step),
            )
        }
    return {"macros": results}


def _posted_macros(body: Any) -> list[dict[str, Any]]:
    """The macros a lint request posts, each with a non-empty id; a 400 for
    anything else."""
    if not isinstance(body, dict) or not isinstance(body.get("macros"), list):
        raise _api_error(400, 'Expected {"macros": [{"id": ..., "steps": [...], "triggers": [...]}]}')
    if not all(_has_id(entry) for entry in body["macros"]):
        raise _api_error(400, "Every macro needs a non-empty 'id'")
    return body["macros"]


def _has_id(entry: Any) -> bool:
    return isinstance(entry, dict) and isinstance(entry.get("id"), str) and bool(entry["id"])


# --- Triggers ---


@router.get("/triggers")
async def list_triggers() -> dict[str, Any]:
    """List all triggers with status.

    Each entry carries `last_fired` (when it last started its macro) and,
    alongside it, its last-run record: `last_outcome` / `last_error`, how its
    last fire actually ended, `last_reason`, why a skipped one did not go
    ahead (`code` and `message`), and `last_run_at`, when. On its own
    `last_fired` shows a trigger whose macro fails every night with a fresh
    timestamp, which reads as healthy.
    """
    engine = _get_engine()
    return {"triggers": engine.triggers.list_triggers()}


@router.post("/triggers/{trigger_id}/test")
async def test_trigger(trigger_id: str) -> dict[str, Any]:
    """Fire a trigger's macro immediately, bypassing conditions.

    ``status`` is how the run ended — the same words `POST
    /macros/{id}/execute` answers with, for the same reason: this said
    ``fired`` for a macro whose every step failed. A 404 means there was
    nothing to fire. A ``skipped`` answer carries ``code`` and ``message``,
    the macro's reason for refusing the run.
    """
    _rate_limit_test(f"test_trigger:{trigger_id}")
    engine = _get_engine()
    end = await engine.triggers.test_trigger(trigger_id)
    if end is None:
        raise HTTPException(status_code=404, detail=f"Trigger '{trigger_id}' not found")
    return {**end.answer(), "trigger_id": trigger_id}
