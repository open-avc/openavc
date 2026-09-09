"""What still calls into a script, and what still feeds it.

The sibling of ``device_references``, for the other thing a delete can strand.
Two ways into a script, and they are the only two:

- **A control calls a function in it.** A ``script.call`` binding names a
  ``function`` and, when the Builder wrote it, the ``script`` it came from.
  Hand-authored bindings carry only the name, so which script one reaches is a
  question about what is loaded -- and the answer has to be the runtime's own
  (``ScriptEngine.find_callable``), or a reference check and a press would
  disagree about the same binding. That is why ``defines_function`` is injected
  rather than re-derived here.
- **An event it handles is emitted.** Nothing else reaches a script: there is
  no macro step that calls one (``macro_validation.BUILTIN_STEP_ACTIONS``), so
  a macro reaches a script only by emitting an event the script subscribes.
  The event half is answered from ``event_references``' existing readers, not
  from a second parse.

Reports, never refuses, and never edits -- like the device walk. The caller
decides what a reference means: ``check_references`` says it out loud, a delete
hands it back as impact.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from openavc.core.event_references import (
    event_listeners,
    is_emitted,
    project_emitters,
    script_emitters,
)


@dataclass(frozen=True)
class ScriptReference:
    """One place the project still reaches into a script."""

    kind: str
    """``binding`` or ``event`` -- the report key it lands under."""

    message: str
    """The sentence to show, e.g. ``UI page "Main" button "Lights": lights_on()``."""

    where: dict[str, Any]
    """The ids to go and fix it with."""


def _mapping(obj: Any) -> Mapping[str, Any] | None:
    if isinstance(obj, Mapping):
        return obj
    dump = getattr(obj, "model_dump", None)
    if callable(dump):
        try:
            return dump(mode="json")
        except TypeError:
            return dump()
    return None


def _script_calls(element: Any) -> Iterator[tuple[str, Mapping[str, Any]]]:
    """Every ``script.call`` action on one element, as (slot, action).

    A slot holds one action or a list of them, and both shapes are written by
    the Builder, so both are walked here rather than at the call sites.
    """
    dump = _mapping(element)
    if dump is None:
        return
    bindings = dump.get("bindings")
    if not isinstance(bindings, Mapping):
        return
    for slot, binding in bindings.items():
        actions = binding if isinstance(binding, list) else [binding]
        for action in actions:
            action_map = _mapping(action)
            if action_map is None:
                continue
            if action_map.get("action") == "script.call":
                yield str(slot), action_map


def _reaches(action: Mapping[str, Any], script_id: str,
             defines_function: Callable[[str, str], bool] | None) -> bool:
    """Does this ``script.call`` land in ``script_id``?

    The recorded ``script`` decides it when there is one. Without it the name
    stands alone, and a name can exist in two scripts -- so this asks the
    runtime, which is what a press asks. No resolver means no opinion, and a
    bare name is then left out rather than guessed at.
    """
    named = str(action.get("script", "") or "")
    if named:
        return named == script_id
    function = str(action.get("function", "") or "")
    if not function or defines_function is None:
        return False
    return defines_function(script_id, function)


def _elements(project: Any) -> Iterator[tuple[Any, str, dict[str, Any]]]:
    """Every element that can carry a binding, with how to name and locate it.

    Page elements, then master elements -- the same order and the same reason
    as ``device_references``: a master carries its own bindings and is drawn on
    every page, so a script named only by one is reached everywhere and was
    invisible to a walk that only read ``pages[].elements``.
    """
    ui = getattr(project, "ui", None)
    for page in getattr(ui, "pages", None) or []:
        for element in getattr(page, "elements", None) or []:
            label = getattr(element, "label", "") or getattr(element, "id", "?")
            yield (
                element,
                f'UI page "{getattr(page, "name", "") or getattr(page, "id", "?")}" element "{label}"',
                {"page_id": getattr(page, "id", ""), "element_id": getattr(element, "id", "")},
            )
    for master in getattr(ui, "master_elements", None) or []:
        label = getattr(master, "label", "") or getattr(master, "id", "?")
        yield (
            master,
            f'UI master element "{label}"',
            {"element_id": getattr(master, "id", "")},
        )


def find_references_to_script(
    project: Any,
    script_id: str,
    *,
    defines_function: Callable[[str, str], bool] | None = None,
    sources: Mapping[str, str] | None = None,
) -> list[ScriptReference]:
    """Everything in the project that still reaches into ``script_id``.

    ``defines_function(script_id, name)`` answers whether that script defines
    that callable -- ``ScriptEngine.find_callable`` is the one to pass, because
    it is what the press resolves through. ``sources`` is script id -> source
    text (``event_references.project_script_sources``) and turns on the event
    half; without it only the direct calls are reported.
    """
    refs: list[ScriptReference] = []

    for element, where_text, where in _elements(project):
        for slot, action in _script_calls(element):
            if not _reaches(action, script_id, defines_function):
                continue
            function = str(action.get("function", "") or "?")
            refs.append(ScriptReference(
                kind="binding",
                message=f"{where_text}: {function}()",
                where={**where, "slot": slot, "function": function},
            ))

    refs.extend(_event_references(project, script_id, sources))
    return refs


def _event_references(
    project: Any, script_id: str, sources: Mapping[str, str] | None,
) -> list[ScriptReference]:
    """The handlers in this script that something in the project can fire.

    A handler waiting for an event nothing emits is NOT reported: it is not a
    reference, it is the dead listener ``event_references`` already warns
    about, and reporting it here would say a script is depended upon by
    something that never speaks to it. Reached-or-not is that module's
    ``is_emitted``, both ways round, rather than a second reading of what a
    glob means.

    A script's own emits are left out of the emitter set on purpose: a script
    that fires its own handler is not a reason to keep it when the rest of the
    project has stopped calling it.
    """
    if not sources or script_id not in sources:
        return []
    emitters = project_emitters(project)
    for other_id, source in sources.items():
        if other_id != script_id:
            emitters.update(script_emitters(source))

    refs: list[ScriptReference] = []
    for line, pattern in event_listeners(sources[script_id]):
        if not is_emitted(pattern, emitters):
            continue
        refs.append(ScriptReference(
            kind="event",
            message=f"Handles \"{pattern}\", which this project emits",
            where={"script_id": script_id, "line": line, "event": pattern},
        ))
    return refs


_REPORT_KEYS = {"binding": "bindings", "event": "events"}


def as_reference_report(refs: list[ScriptReference]) -> dict[str, list[dict[str, Any]]]:
    """The references as one JSON body, grouped by kind, empties dropped.

    Same shape as the device report next door, for the same reason: whoever is
    reading it -- an API response or a tool result -- is looking at ids to go
    and fix, with a sentence saying what each one is.
    """
    report: dict[str, list[dict[str, Any]]] = {}
    for ref in refs:
        report.setdefault(_REPORT_KEYS[ref.kind], []).append(
            {**ref.where, "message": ref.message}
        )
    return report
