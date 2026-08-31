"""What a script listens for, and whether anything in this project says it.

The other half of the dead-control check. A `do` binding naming an action the
runtime cannot dispatch is warned at the write door and logged at runtime
(`ui/page_review.py`, `core/ui_events.py`); a script carrying
`@on_event("custom.select_source")` for an event nothing emits gets no error,
no warning and no log line -- the handler simply never runs. Both halves have
to be wrong for the failure to happen and only one of them was checked, so the
same defect survived being written from the script end instead of the UI end.
That is how a starter template shipped for months with source buttons whose
routing logic was correct and whose event never arrived.

**Only the `custom.` namespace, deliberately.** Everywhere else the emitter set
is open -- `ui.*` comes from a panel, `device.*` from a driver's lifecycle,
`plugin.*` from whatever is installed, `isc.*` from another instance, `cloud.*`
from the cloud -- and a warning that fires on a working handler teaches people
to stop reading warnings. `custom.` is closed by construction: a plugin's emit
is auto-prefixed `plugin.<id>.` (`core/plugin_api.py`), a peer instance's is
prefixed `isc.<peer>.` (`core/isc.py`), and nothing in the REST or WebSocket
surface emits a caller-supplied name. So the three doors below are the whole
population, and "nothing emits this" is a fact rather than a guess:

    a macro's `event.emit` step, including inside a conditional's branches
    a control's `event.emit` action, including everywhere one nests
    another script's `events.emit(...)`

Read statically, never by importing: the editor asks about source it is holding
and has not saved, and a script that fails to load has to be readable too --
that is precisely when somebody needs to know why nothing happens.

Nothing here refuses anything. It is a lint, on the same policy as
`macro_validation.macro_issues`: show it, block nothing.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Iterator, Mapping
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from openavc.ui.page_review import do_action_dicts
from openavc.utils.logger import get_logger
from openavc.utils.paths import is_safe_script_filename, safe_path_within

log = get_logger(__name__)

# The namespace a project owns. A pattern outside it is not looked at.
CUSTOM_PREFIX = "custom."

# An emitter whose name cannot be read statically -- `events.emit(name)`. It
# stands for "could be anything", which suppresses rather than accuses: this
# check may never be the reason somebody deletes a handler that works.
UNREADABLE = "*"


def _callee(node: ast.AST) -> str:
    """The bare name a call is against: `on_event`, `openavc.on_event` -> both."""
    func = getattr(node, "func", None)
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _first_arg(node: ast.Call) -> ast.AST | None:
    return node.args[0] if node.args else None


def _literal(node: ast.AST | None) -> str | None:
    """The string a node is, or None when it is built at runtime."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _readable_name(node: ast.AST | None) -> str:
    """What an emitted name can be, as a glob.

    A literal is itself. An f-string keeps its literal parts and turns each
    substitution into a wildcard, so `f"custom.source.{n}"` still answers for
    the family it emits into rather than being written off as unreadable.
    Anything else is `UNREADABLE`.
    """
    literal = _literal(node)
    if literal is not None:
        return literal
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for piece in node.values:
            if isinstance(piece, ast.Constant) and isinstance(piece.value, str):
                parts.append(piece.value)
            else:
                parts.append("*")
        return "".join(parts)
    return UNREADABLE


def _parse(source: str) -> ast.AST | None:
    """The tree, or None when the file does not parse.

    A syntax error is somebody mid-edit, and it is already reported by the
    editor and by the script loader. Saying nothing is right: the alternative
    is a second complaint about the same broken line.
    """
    try:
        return ast.parse(source)
    except (SyntaxError, ValueError):
        return None


def event_listeners(source: str) -> list[tuple[int, str]]:
    """Every `@on_event("...")` in a script, as (line, pattern).

    The line is the decorator's own, which is the line to put a mark on -- the
    handler below it is fine, and the pattern is what is wrong.
    """
    tree = _parse(source)
    if tree is None:
        return []
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or _callee(decorator) != "on_event":
                continue
            pattern = _literal(_first_arg(decorator))
            # A pattern built at runtime is not something to have an opinion
            # about; there is no name here to say nothing emits.
            if pattern:
                found.append((decorator.lineno, pattern))
    return found


def script_emitters(source: str) -> list[str]:
    """Every event name a script can emit, each as a literal or a glob."""
    tree = _parse(source)
    if tree is None:
        # Unreadable source is not evidence that it emits nothing.
        return [UNREADABLE]
    return [
        _readable_name(_first_arg(node))
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _callee(node) == "emit"
    ]


def _dump(obj: Any) -> Mapping[str, Any]:
    """A model or a mapping, as a mapping. Both reach this module."""
    if isinstance(obj, Mapping):
        return obj
    dump = getattr(obj, "model_dump", None)
    if callable(dump):
        result = dump()
        if isinstance(result, Mapping):
            return result
    return {}


def _macro_step_emitters(steps: Any) -> Iterator[str]:
    """`event.emit` steps, including the ones inside a conditional's branches.

    A macro step's event name is never resolved -- `macro_engine` emits
    `step["event"]` verbatim, unlike the payload beside it -- so a literal is
    all there is to read and all there is to match.
    """
    if not isinstance(steps, list):
        return
    for step in steps:
        step_map = _dump(step)
        if not step_map:
            continue
        if step_map.get("action") == "event.emit":
            name = step_map.get("event")
            if isinstance(name, str) and name:
                yield name
        for branch in ("then_steps", "else_steps"):
            yield from _macro_step_emitters(step_map.get(branch))


def _element_emitters(element: Mapping[str, Any]) -> Iterator[str]:
    """`event.emit` actions on one element, wherever the runtime finds them.

    The nesting is not restated here: `page_review` already walks exactly what
    `ui_events` dispatches -- a toggle's off_action, a press mode's
    hold_action, a value_map's entries, a matrix destination's own route
    override -- and a second copy of that list is how one of them goes
    unchecked the next time a place to nest an action is added.
    """
    for action in do_action_dicts(element):
        if action.get("action") == "event.emit":
            name = action.get("event")
            if isinstance(name, str) and name:
                yield name


def _elements(project: Any) -> Iterator[Mapping[str, Any]]:
    """Every element that can carry a binding: each page's, plus the masters."""
    ui = _dump(getattr(project, "ui", None) or (project.get("ui") if isinstance(project, Mapping) else None))
    for page in ui.get("pages") or []:
        page_map = _dump(page)
        for element in page_map.get("elements") or []:
            element_map = _dump(element)
            if element_map:
                yield element_map
    for master in ui.get("master_elements") or []:
        master_map = _dump(master)
        if master_map:
            yield master_map


def project_emitters(project: Any) -> set[str]:
    """Every event name this project's macros and controls can emit."""
    emitters: set[str] = set()
    macros = getattr(project, "macros", None)
    if macros is None and isinstance(project, Mapping):
        macros = project.get("macros")
    for macro in macros or []:
        emitters.update(_macro_step_emitters(_dump(macro).get("steps")))
    for element in _elements(project):
        emitters.update(_element_emitters(element))
    return emitters


def is_emitted(pattern: str, emitters: Iterable[str]) -> bool:
    """Whether anything in ``emitters`` reaches a handler listening for *pattern*.

    Matched both ways round, because either side can be a glob and they mean
    different things. A handler on `custom.source.*` is reached by a macro
    emitting `custom.source.hdmi1` (the runtime's own test,
    ``fnmatch(event, pattern)``); a handler on `custom.source.hdmi1` is reached
    by a script emitting `f"custom.source.{name}"`, which is read here as a
    glob because that is the most this can know about it.
    """
    return any(fnmatch(name, pattern) or fnmatch(pattern, name) for name in emitters)


def project_script_sources(project: Any, scripts_dir: Path) -> dict[str, str]:
    """Every script this project declares, as id -> the text on disk.

    Both doors that ask this question need the whole set rather than the one
    in front of them: the emit that saves a handler from a warning is as
    likely to be in the script next to it. A stored filename that is not a
    plain `.py` name, or a file that is not there, costs that script's emits
    and nothing else -- the question being answered is still worth answering.
    """
    sources: dict[str, str] = {}
    for script in getattr(project, "scripts", None) or []:
        script_id = getattr(script, "id", None)
        filename = getattr(script, "file", None)
        if not isinstance(script_id, str) or not isinstance(filename, str):
            continue
        if not is_safe_script_filename(filename):
            continue
        path = safe_path_within(scripts_dir, filename)
        if path is None or not path.exists():
            continue
        try:
            sources[script_id] = path.read_text(encoding="utf-8")
        except OSError as exc:
            log.debug("Could not read script '%s' for the event check: %s", script_id, exc)
    return sources


def dead_listeners(
    sources: Mapping[str, str], project: Any = None
) -> dict[str, list[dict[str, Any]]]:
    """Handlers waiting for a `custom.` event nothing in the project emits.

    ``sources`` is script id -> source text, and it must be ALL of them, not
    just the one on screen: the emit that saves a handler from this warning is
    as likely to be in another script as in a macro. Every script's own emits
    count, including the one being checked -- a script that emits to itself is
    a normal shape, not a suspicious one.

    Returns script id -> ``[{line, event, message}]``. A script with nothing
    wrong is absent rather than present and empty, which is what lets a caller
    treat the result as "what to draw".
    """
    emitters = project_emitters(project) if project is not None else set()
    for source in sources.values():
        emitters.update(script_emitters(source))

    issues: dict[str, list[dict[str, Any]]] = {}
    for script_id, source in sources.items():
        found: list[dict[str, Any]] = []
        for line, pattern in event_listeners(source):
            if not pattern.startswith(CUSTOM_PREFIX):
                continue
            if is_emitted(pattern, emitters):
                continue
            found.append({
                "line": line,
                "event": pattern,
                "message": (
                    f'Nothing in this project emits "{pattern}", so this handler '
                    f"never runs. Emit it from a macro's Emit Event step, a "
                    f"control's Emit Event action, or events.emit() in another "
                    f"script."
                ),
            })
        if found:
            issues[script_id] = found
    return issues
