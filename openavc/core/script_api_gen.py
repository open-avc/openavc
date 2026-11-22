"""Render the scripting API into the script editor's completions.

Produces ``openavc/web/programmer/src/api/scriptApi.gen.ts`` from what a script
can use: the names ``openavc/__init__.py`` publishes, each object's members
and what each one takes, returns and is awaited, read from
``core/script_api.py``; the words for each from ``core/script_api_help.py``;
and the events a project hears, from ``core/event_bus.EVENTS``. The editor's
completions read this file and nothing else for those, so a name added to the
API reaches the editor at the next render, and one removed stops being offered.

Run:  python -m openavc.core.script_api_gen
A test compares the committed file against a fresh render, so changing the API,
its words or the events without rendering again, or editing the file by hand,
fails CI.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any

import openavc
from openavc.core import script_api
from openavc.core.event_bus import EVENTS, event_pattern
from openavc.core.script_api_help import HELP

ARTIFACT = "openavc/web/programmer/src/api/scriptApi.gen.ts"

BANNER = """\
// GENERATED FILE - DO NOT EDIT.
// Rendered from the scripting API (openavc/__init__.py, openavc/core/script_api.py),
// its words (openavc/core/script_api_help.py) and the events a project hears
// (openavc/core/event_bus.py EVENTS).
// Regenerate with:  python -m openavc.core.script_api_gen
// A test compares this file against a fresh render, so hand edits fail CI.

"""

TYPES = """\
/** One name a script can use, or one member of one of its objects. */
export interface ScriptApiName {
  name: string;
  kind: "function" | "class" | "object";
  /** A function's parameters as Python writes them, or "". */
  signature: string;
  /** What a function returns, or "". */
  returns: string;
  /** Called with `await`. */
  awaited: boolean;
  /** What choosing it inserts; `${n:...}` marks each place to fill in. */
  insertText: string;
  /** What typing `@` and choosing it inserts, for a decorator; else "". */
  decorator: string;
  summary: string;
  example: string;
}

/** One family of events: `<...>` where the sender puts an id. */
export interface ScriptEvent {
  name: string;
  /** The pattern that hears the whole family. */
  pattern: string;
  description: string;
}

/** What `from openavc import` offers, in `__all__`'s order. */
export const SCRIPT_API: ScriptApiName[] = %(names)s;

/** Each object's members, by the object's name. */
export const SCRIPT_API_MEMBERS: Record<string, ScriptApiName[]> = %(members)s;

/** The events a project hears, in the order the docs list them. */
export const SCRIPT_EVENTS: ScriptEvent[] = %(events)s;
"""


def _unwrapped(obj: Any) -> Any:
    return inspect.unwrap(obj) if callable(obj) else obj


def _parameters(fn: Any) -> list[inspect.Parameter]:
    params = list(inspect.signature(_unwrapped(fn)).parameters.values())
    if params and params[0].name in ("self", "cls"):
        params = params[1:]
    return params


def _param_text(param: inspect.Parameter) -> str:
    text = param.name
    if param.kind is inspect.Parameter.VAR_POSITIONAL:
        text = f"*{text}"
    elif param.kind is inspect.Parameter.VAR_KEYWORD:
        text = f"**{text}"
    if param.annotation is not inspect.Parameter.empty:
        text += f": {param.annotation}"
    if param.default is not inspect.Parameter.empty:
        text += f" = {param.default!r}"
    return text


def _placeholders(params: list[inspect.Parameter]) -> str:
    """Each required argument as a place to fill in, quoted when it is text."""
    required = [
        p for p in params
        if p.default is inspect.Parameter.empty
        and p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    slots = []
    for number, param in enumerate(required, start=1):
        slot = f"${{{number}:{param.name}}}"
        slots.append(f'"{slot}"' if param.annotation == "str" else slot)
    return ", ".join(slots)


def _entry(path: str, name: str, obj: Any) -> dict[str, Any]:
    help_ = HELP[path]
    entry: dict[str, Any] = {
        "name": name, "kind": "object", "signature": "", "returns": "",
        "awaited": False, "insertText": name, "decorator": help_.snippet,
        "summary": help_.summary, "example": help_.example,
    }
    if inspect.isclass(obj):
        entry["kind"] = "class"
    elif callable(obj) and not _is_proxy(obj):
        params = _parameters(obj)
        returns = inspect.signature(_unwrapped(obj)).return_annotation
        entry.update(
            kind="function",
            signature="(" + ", ".join(_param_text(p) for p in params) + ")",
            returns="" if returns is inspect.Signature.empty else str(returns),
            awaited=inspect.iscoroutinefunction(obj),
            insertText=f"{name}({_placeholders(params)})",
        )
    return entry


def _is_proxy(obj: Any) -> bool:
    """One of script_api's objects (``devices``, ``state``...), not a function."""
    return type(obj).__module__ == script_api.__name__ and type(obj).__name__.startswith("_")


def public_members(obj: Any) -> list[str]:
    """An object's members a script uses: what its class defines without a
    leading underscore. ``plugins`` has none; its members are the running
    plugins'."""
    return sorted(
        name for name in vars(type(obj))
        if not name.startswith("_")
    )


def surface() -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    """Every name ``openavc`` publishes, and each object's members."""
    names = [_entry(name, name, getattr(openavc, name)) for name in openavc.__all__]
    members: dict[str, list[dict[str, Any]]] = {}
    for name in openavc.__all__:
        obj = getattr(openavc, name)
        if not _is_proxy(obj):
            continue
        listed = [
            _entry(f"{name}.{member}", member, getattr(obj, member))
            for member in public_members(obj)
        ]
        if listed:
            members[name] = listed
    return names, members


def surface_paths() -> list[str]:
    """Every name and member the editor describes, as ``HELP`` keys them."""
    names, members = surface()
    return [n["name"] for n in names] + [
        f"{obj}.{m['name']}" for obj, listed in members.items() for m in listed
    ]


def render() -> str:
    names, members = surface()
    events = [
        {"name": f.name, "pattern": event_pattern(f.name), "description": f.description}
        for f in EVENTS
    ]
    dump = lambda value: json.dumps(value, indent=2, ensure_ascii=False)  # noqa: E731
    return BANNER + TYPES % {
        "names": dump(names), "members": dump(members), "events": dump(events),
    }


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    path = repo_root / ARTIFACT
    path.write_text(render(), encoding="utf-8", newline="\n")
    print(f"wrote {ARTIFACT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
