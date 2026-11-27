"""Semantic validation for macro steps and triggers.

The Pydantic models in ``project_loader`` check a macro's *shape* (field names
and types). These rules check its *meaning*: that the step action exists, that
the fields that action needs are filled in, that the command it names can
actually run, that operator and trigger-type names are ones the runtime
understands, and that referenced ids resolve. What a condition needs where it
sits, and the sentence for what it lacks, is ``condition_eval``'s
(``condition_issues``), the rule the runtime judges it by.

It lives in core, not next to a caller, because more than one door creates
macros and they must agree on what a valid macro is. Previously these rules
existed only inside the cloud AI tool layer, so a macro the AI refused to write
saved without complaint through the REST project-save door.

Enforcement is deliberately not symmetric, and that is a policy choice, not
drift: a generator (the AI tools) is told immediately when it emits something
the runtime can't run, while the IDE's project save stays shape-only because a
half-built macro is a normal intermediate state for someone editing one. A
trigger's delay, debounce and cooldown are bounded on the model itself
(``project_loader.SECONDS_MIN``), so the save refuses one outside it; the
finding here reads that bound from the model. Like the delay step's check,
it also refuses a value that is not a number, which the model would read as
one (``"5"``, ``true``): the AI is told to send a number.

What the IDE gets instead is ``macro_issues`` -- the same rules, the same
traversal, returned as placed records rather than one refusal string, so the
editor can mark the step that is incomplete and the macro list can mark the
macro without anything being blocked or rejected. That is the third option:
a macro that saves cleanly and then does nothing is the failure this closes,
and it is invisible precisely because nobody reopens a macro that saved.

It is also THE rule for a trigger's id, which names one trigger in the whole
project (``trigger_holders``): the lint marks each trigger the engine will
never fire because a trigger loaded after it has its id, and the AI's doors
refuse a trigger whose id another trigger already has. The save does not:
a file from somewhere else still opens, and the lint says which to fix.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from openavc.core.condition_eval import (
    CONDITIONAL,
    GUARD,
    SKIP_IF,
    STATE_TRIGGER,
    WAIT_UNTIL,
    condition_issues,
    state_condition,
)
from openavc.core.event_bus import check_project_emit, silent_pattern_sentence, silent_platform_pattern
from openavc.core.project_loader import SECONDS_MIN, MacroStep, TriggerConfig, choice_rule
from openavc.core.state_store import (
    DEVICE_PREFIX,
    DEVICE_WRITE_NOTE,
    flat_value_sentence,
    platform_key_sentence,
)
from openavc.drivers.param_labels import param_label as _readable_param
from openavc.drivers.param_validation import required_param_sentence
from openavc.utils.logger import get_logger

log = get_logger(__name__)

#: What a caller holding the driver registry hands in: given a step, the
#: parameters its command declares required and it does not supply, or None
#: when nothing can be said about it.
MissingParams = Callable[[Mapping[str, Any]], "list[str] | None"]

#: The same caller's sentence for one of those parameters: given a step and a
#: parameter's key, the runtime's refusal, the command and the parameter named
#: as the driver names them. See ``core/command_params.required_refusal``.
RequiredSentence = Callable[[Mapping[str, Any], str], str]

#: The same caller's other answer: given a step, the sentence saying its
#: command cannot run from a macro at all (it takes or hands back a file), or
#: None. See ``core/command_params.file_command_reason``.
FileCommand = Callable[[Mapping[str, Any]], "str | None"]


BUILTIN_STEP_ACTIONS = frozenset((
    "device.command", "group.command", "delay", "state.set",
    "macro", "event.emit", "conditional", "wait_until", "ui.navigate",
    "help.request", "help.clear",
))
STEP_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "device.command": ("device", "command"),
    "group.command": ("group", "command"),
    "delay": ("seconds",),
    "state.set": ("key",),
    "macro": ("macro",),
    "event.emit": ("event",),
    "conditional": ("condition",),
    # wait_until: condition is required; timeout must be present in the step
    # (null is allowed to mean "never time out") so we enforce it below rather
    # than as a plain non-empty required field.
    "wait_until": ("condition",),
    # ui.navigate: 'page' is a page id, or the overlay-stack controls
    # '$back' / '$dismiss' (all non-empty strings, so the required-field
    # check below matches the runtime's "page is required" rule).
    "ui.navigate": ("page",),
    # help.request: every field is optional. A bare step is the common case --
    # a panel button whose whole meaning is "someone in this room needs a
    # person" -- and requiring a message would make the simplest wiring the
    # fussiest one.
    # help.clear: no fields. It puts the help state back to idle.
}
# The words the schema declares (project_loader.OneOf); it takes any other and
# keeps it as written, so these say which ones run.
VALID_TRIGGER_TYPES = frozenset(choice_rule(TriggerConfig, "type").words)
VALID_OVERLAP_MODES = frozenset(choice_rule(TriggerConfig, "overlap").words)
_ON_TIMEOUT_WORDS = choice_rule(MacroStep, "on_timeout").words


#: What the lint's caller hands in to name a parameter the way the step's form
#: does: given a step and a parameter's key, its label, or None.
ParamLabel = Callable[[Mapping[str, Any], str], "str | None"]


@dataclass(frozen=True)
class _Lookups:
    """What only a caller holding the engine can say about a macro's steps,
    handed to every check as one, by the keywords the public functions take.

    ``extra_actions`` carries the action types plugins have registered with
    the macro engine at runtime. They are as valid as the built-ins, but only
    the engine knows them, so a caller that can reach it must pass them in --
    otherwise a macro using a plugin action reads as malformed.

    ``missing_params`` answers whether a chosen command can actually run -- the
    driver's own required parameters, which nothing here can see. A caller that
    cannot reach the driver registry must produce no finding rather than guess.
    ``required_sentence`` words what it finds as the runtime refuses it, and
    ``param_label`` names the parameter as the step's form does.
    ``file_command`` answers for a command that takes or hands back a file,
    which cannot run from a macro. See ``core/command_params``.
    """

    extra_actions: Collection[str] = ()
    missing_params: MissingParams | None = None
    param_label: ParamLabel | None = None
    required_sentence: RequiredSentence | None = None
    file_command: FileCommand | None = None


def _finding(path: str, message: str, **where: Any) -> dict[str, Any]:
    """One finding: the sentence, the place it was written against, and what
    the editor needs to say it in the form's own words (``action``,
    ``missing``, ``param``, ``param_label``). The sentence itself is the one
    the AI tools and the logs read: it keeps the step's field keys, and names
    a command's parameter as the runtime's refusal does, its key in ``param``."""
    return {"path": path, "message": message, **{k: v for k, v in where.items() if v is not None}}


def _step_findings(step: dict, path: str, lookups: _Lookups) -> list[dict[str, Any]]:
    """The findings :func:`validate_macro_step` writes, as records."""
    found: list[dict[str, Any]] = []
    action = step.get("action", "")
    if not action:
        found.append(_finding(path, "missing 'action' field"))
        return found
    if action not in BUILTIN_STEP_ACTIONS and action not in lookups.extra_actions:
        usable = ", ".join(sorted(BUILTIN_STEP_ACTIONS) + sorted(lookups.extra_actions))
        found.append(_finding(
            path, f"step action '{action}' is not valid. Use: {usable}", action=action,
        ))
        return found

    required = STEP_REQUIRED_FIELDS.get(action, ())
    for field in required:
        val = step.get(field)
        if val is None or val == "":
            found.append(_finding(
                path, f"{action} step requires '{field}'", action=action, missing=field,
            ))

    # The step names a command; the command names parameters of its own. The
    # fields above are all this module can see, and stopping there is what let a
    # step read as complete while the device refused every run of it. The
    # sentence is the runtime's own, word for word, for the AI tools and the
    # logs; the editor names the parameter as its form does, from `param_label`.
    found.extend(_missing_param_findings(step, path, lookups))
    # And a command no step can run at all: it takes or hands back a file.
    reason = lookups.file_command(step) if lookups.file_command is not None else None
    if reason:
        found.append(_finding(path, reason, action=action, refused="file"))

    found.extend(_refused_step_findings(step, path))

    if action == "delay":
        seconds = step.get("seconds")
        if seconds is not None and (not isinstance(seconds, (int, float)) or seconds < 0):
            found.append(_finding(
                path, "delay 'seconds' must be a non-negative number",
                action=action,
            ))

    if action == "conditional":
        found.extend(_condition_findings(step.get("condition"), CONDITIONAL, path, action))
        # Recursively validate then/else steps
        for branch in ("then_steps", "else_steps"):
            branch_steps = step.get(branch)
            if isinstance(branch_steps, list):
                for i, sub in enumerate(branch_steps):
                    if isinstance(sub, dict):
                        found.extend(_step_findings(sub, f"{path}.{branch}[{i}]", lookups))

    if action == "wait_until":
        found.extend(_condition_findings(step.get("condition"), WAIT_UNTIL, path, action))
        # timeout is required as a field, but may be null to mean "never time out"
        if "timeout" not in step:
            found.append(_finding(
                path,
                "wait_until requires 'timeout' (number of seconds, or null for no timeout)",
                action=action, missing="timeout",
            ))
        else:
            tmo = step.get("timeout")
            if tmo is not None and (not isinstance(tmo, (int, float)) or tmo < 0):
                found.append(_finding(
                    path, "wait_until 'timeout' must be a non-negative number or null",
                    action=action,
                ))
        on_timeout = step.get("on_timeout")
        if on_timeout is not None and on_timeout not in _ON_TIMEOUT_WORDS:
            found.append(_finding(
                path, "wait_until 'on_timeout' must be 'fail' or 'continue'",
                action=action,
            ))

    found.extend(_condition_findings(step.get("skip_if"), SKIP_IF, path, action))
    return found


def _condition_findings(
    condition: Any, where: str, path: str, action: str,
) -> list[dict[str, Any]]:
    """What a condition at ``where`` lacks (``condition_eval.condition_issues``),
    as findings. Nothing for one that is not a mapping: the step holds none."""
    if not isinstance(condition, Mapping):
        return []
    return [
        _finding(path, issue.sentence, action=action, missing=issue.missing)
        for issue in condition_issues(condition, where)
    ]


def _refused_emit_findings(step: dict, path: str) -> list[dict[str, Any]]:
    """An Emit Event step naming one of the platform's own events, in the
    runtime's refusal, word for word (``core/event_bus.check_project_emit``)."""
    if step.get("action") != "event.emit":
        return []
    refused = check_project_emit(step.get("event"))
    if not refused:
        return []
    return [_finding(path, refused, action="event.emit", refused="platform_event")]


def _refused_step_findings(step: dict, path: str) -> list[dict[str, Any]]:
    """What the runtime refuses or warns of when a step runs, in its words: an
    Emit Event step's platform name, and a Set Variable step's write."""
    return _refused_emit_findings(step, path) + _state_write_findings(step, path)


def _state_write_findings(step: dict, path: str) -> list[dict[str, Any]]:
    """A Set Variable step the runtime refuses (``state_store.check_authored_write``),
    in its words: a key OpenAVC owns (``refused: "platform_key"``) or a value
    written as a list or a dict (``refused: "value"``). A ``$`` reference is
    only known when the step runs, and the runtime refuses it then. A device's
    key is a warning: the write runs and lasts until the device's next report."""
    if step.get("action") != "state.set":
        return []
    key = step.get("key")
    if not isinstance(key, str):
        return []
    return _refused_write_findings(step, path, key) or _device_write_findings(path, key)


def _refused_write_findings(step: dict, path: str, key: str) -> list[dict[str, Any]]:
    refusals = (
        ("platform_key", platform_key_sentence(key)),
        ("value", flat_value_sentence(step.get("value"))),
    )
    for refused, sentence in refusals:
        if sentence:
            return [_finding(path, sentence, action="state.set", refused=refused)]
    return []


def _device_write_findings(path: str, key: str) -> list[dict[str, Any]]:
    if not key.startswith(DEVICE_PREFIX):
        return []
    return [_finding(path, DEVICE_WRITE_NOTE.format(key=key), action="state.set", warning=True)]


def _event_trigger_findings(trigger: dict, path: str) -> list[dict[str, Any]]:
    """An Event trigger with no pattern, or one that can hear nothing: under
    ``system.`` and matching none of the events the platform sends."""
    pattern = trigger.get("event_pattern")
    if not pattern:
        return [_finding(
            path, "event trigger requires 'event_pattern'",
            action="event", missing="event_pattern",
        )]
    if silent_platform_pattern(pattern):
        return [_finding(path, silent_pattern_sentence(pattern), action="event")]
    return []


def _missing_param_findings(
    step: dict, path: str, lookups: _Lookups,
) -> list[dict[str, Any]]:
    """One finding per parameter the step's command requires and the step
    leaves empty: the runtime's refusal, its key in ``param`` and its label
    on the form in ``param_label``. Nothing when the caller cannot ask."""
    if lookups.missing_params is None:
        return []
    return [
        _finding(
            path, _required_sentence(step, name, lookups.required_sentence),
            action=step.get("action", ""), missing="param", param=name,
            param_label=lookups.param_label(step, name) if lookups.param_label else None,
        )
        for name in lookups.missing_params(step) or ()
    ]


def _required_sentence(
    step: Mapping[str, Any], name: str, required_sentence: RequiredSentence | None,
) -> str:
    """The runtime's refusal of ``step`` for leaving ``name`` out; from the
    command's id and the key made readable when the caller could not say."""
    if required_sentence is not None:
        return required_sentence(step, name)
    return required_param_sentence(str(step.get("command") or ""), _readable_param(name))


def _ai_line(issue: Mapping[str, Any]) -> str:
    """One finding as the AI tools' refusal says it: where, then the sentence.
    A missing parameter's place (only those carry ``param``) ends in its key,
    the one thing the sentence does not say and the thing a corrected call has
    to fill in."""
    place = issue.get("path") or ""
    if issue.get("param"):
        place = f"{place}.params.{issue['param']}"
    return f"{place}: {issue['message']}" if place else issue["message"]


def validate_macro_step(step: dict, path: str, **lookups: Any) -> list[str]:
    """Validate a single macro step. Returns list of error strings.

    ``lookups`` are the keywords :class:`_Lookups` names (``extra_actions``,
    ``missing_params``, ``required_sentence``, ``file_command``): what only a
    caller holding the engine and the driver registry can answer. A keyword it
    does not name is a TypeError, as on any signature.
    """
    return [
        _ai_line(f) for f in _step_findings(step, path, _Lookups(**lookups))
        if not f.get("warning")
    ]


def _trigger_findings(trigger: dict, path: str) -> list[dict[str, Any]]:
    """The findings :func:`validate_trigger` writes, as records."""
    found: list[dict[str, Any]] = []
    ttype = trigger.get("type", "")
    if not ttype:
        found.append(_finding(path, "missing 'type' field"))
        return found
    if ttype not in VALID_TRIGGER_TYPES:
        found.append(_finding(
            path,
            f"trigger type '{ttype}' is not one this version of OpenAVC knows, so the "
            f"trigger never fires. Use: {', '.join(choice_rule(TriggerConfig, 'type').words)}",
            action=ttype,
        ))
        return found

    if ttype == "schedule":
        cron = trigger.get("cron")
        if not cron or not isinstance(cron, str):
            found.append(_finding(
                path, "schedule trigger requires 'cron' (string)", action=ttype, missing="cron",
            ))
        elif cron:
            parts = cron.strip().split()
            if len(parts) not in (5, 6):
                found.append(_finding(
                    path, f"cron expression must have 5 or 6 fields, got {len(parts)}",
                    action=ttype,
                ))
    elif ttype == "state_change":
        found.extend(_condition_findings(state_condition(trigger), STATE_TRIGGER, path, ttype))
    elif ttype == "event":
        found.extend(_event_trigger_findings(trigger, path))

    found.extend(_execution_findings(trigger, path, ttype))

    conditions = trigger.get("conditions")
    if isinstance(conditions, list):
        for i, c in enumerate(conditions):
            found.extend(_condition_findings(c, GUARD, f"{path}.conditions[{i}]", ttype))
    return found


def _execution_findings(trigger: dict, path: str, ttype: str) -> list[dict[str, Any]]:
    """What a trigger's overlap, delay, debounce and cooldown say wrong."""
    found: list[dict[str, Any]] = []
    overlap = trigger.get("overlap")
    if overlap and overlap not in VALID_OVERLAP_MODES:
        found.append(_finding(
            path, f"overlap '{overlap}' is not valid. Use: {', '.join(choice_rule(TriggerConfig, 'overlap').words)}",
            action=ttype,
        ))
    return found + _seconds_findings(trigger, path, ttype)


# A trigger's times in seconds: each field its model bounds at SECONDS_MIN.
_TRIGGER_SECONDS = tuple(
    name for name, info in TriggerConfig.model_fields.items()
    if any(getattr(rule, "ge", None) == SECONDS_MIN for rule in info.metadata)
)


def _seconds_findings(trigger: dict, path: str, ttype: str) -> list[dict[str, Any]]:
    """A delay, debounce or cooldown outside the bound the trigger model
    declares: a number of seconds, finite, ``SECONDS_MIN`` or more. The save
    refuses one; this says so on the trigger's row and to the AI assistant."""
    return [
        _finding(path, f"'{field}' must be a number of seconds, {SECONDS_MIN} or more", action=ttype)
        for field in _TRIGGER_SECONDS
        if field in trigger and not _is_seconds(trigger[field])
    ]


def _is_seconds(value: Any) -> bool:
    return (
        isinstance(value, int | float) and not isinstance(value, bool)
        and math.isfinite(value) and value >= SECONDS_MIN
    )


def validate_trigger(trigger: dict, path: str) -> list[str]:
    """Validate a single trigger definition. Returns list of error strings."""
    return [f"{f['path']}: {f['message']}" for f in _trigger_findings(trigger, path)]


# --- A trigger's id -----------------------------------------------------------
#
# A trigger's id names one trigger in the whole project. The trigger engine
# keeps one trigger per id, the last it loads (TriggerEngine.load_triggers: the
# macros in order, then each macro's triggers), so every earlier trigger with
# that id never fires. The lint marks each of those and names the one kept in
# its place; the AI's doors refuse an id another trigger has, whichever of the
# two the engine would keep, since a new macro loads last and its trigger
# would take the id from the one already there.

_UNIQUE_ID = "give each trigger an id no other trigger in the project has"


@dataclass(frozen=True)
class TriggerPlace:
    """Where one trigger sits: its macro's id and its index in that macro's
    triggers."""

    macro: str
    index: int


@dataclass(frozen=True)
class TriggerIds:
    """The trigger ids of the macros checked together (``trigger_holders``)
    and which of those macros is the one being checked."""

    holders: Mapping[str, Sequence[TriggerPlace]]
    macro: str


def trigger_holders(macros: Iterable[Any]) -> dict[str, list[TriggerPlace]]:
    """Each trigger id in ``macros`` (in the project's order, mappings or
    ``MacroConfig``) and every trigger that has it, in the order the trigger
    engine loads them, so the last is the one it keeps. A trigger with no id
    holds none: the engine loads no such trigger."""
    holders: dict[str, list[TriggerPlace]] = {}
    for macro in macros:
        macro_id = str(_field(macro, "id") or "")
        triggers = _field(macro, "triggers")
        for index, trigger in enumerate(triggers if isinstance(triggers, list) else ()):
            trigger_id = _field(trigger, "id")
            if isinstance(trigger_id, str) and trigger_id:
                holders.setdefault(trigger_id, []).append(TriggerPlace(macro_id, index))
    return holders


def _field(item: Any, name: str) -> Any:
    """``name`` of a mapping or of a model, or None."""
    if isinstance(item, Mapping):
        return item.get(name)
    return getattr(item, name, None)


def _same_id_findings(trigger: dict, index: int, ids: TriggerIds) -> list[dict[str, Any]]:
    """A trigger whose id a trigger loaded after it also has: it never fires.
    The finding names the one kept in its place in ``same_id_as`` (its
    ``index``, and its ``macro`` when that is another macro)."""
    trigger_id = trigger.get("id")
    held = ids.holders.get(trigger_id) if isinstance(trigger_id, str) else None
    if not held or held[-1] == TriggerPlace(ids.macro, index):
        return []
    kept = held[-1]
    if kept.macro == ids.macro:
        where, place = f"triggers[{kept.index}]", {"index": kept.index}
    else:
        where = f"macro '{kept.macro}' triggers[{kept.index}]"
        place = {"macro": kept.macro, "index": kept.index}
    return [_finding(
        f"triggers[{index}]",
        f"trigger id '{trigger_id}' is also the id of {where}, which is kept in its place, "
        f"so this trigger never fires; {_UNIQUE_ID}",
        action=trigger.get("type") or None, same_id_as=place,
    )]


def _taken_id_lines(triggers: Any, project: Any, macro_id: str | None) -> list[str]:
    """Each of ``triggers`` whose id a trigger of another of the project's
    macros already has, whichever of the two the engine would keep. The
    project's own copy of the macro being written (``macro_id``) is no other
    macro: an update hands its triggers back."""
    if project is None or not isinstance(triggers, list):
        return []
    others = trigger_holders(
        m for m in _listed(project, "macros") if _field(m, "id") != macro_id
    )
    lines: list[str] = []
    for i, trigger in enumerate(triggers):
        trigger_id = _field(trigger, "id")
        held = others.get(trigger_id) if isinstance(trigger_id, str) else None
        if held:
            lines.append(
                f"triggers[{i}]: trigger id '{trigger_id}' is already the id of macro "
                f"'{held[0].macro}' triggers[{held[0].index}]; {_UNIQUE_ID}"
            )
    return lines


def macro_issues(
    steps: Any, triggers: Any, *, trigger_ids: TriggerIds | None = None, **lookups: Any,
) -> list[dict[str, Any]]:
    """Everything wrong with a macro, placed where an editor can draw it.

    The same rules and the same traversal ``validate_macro`` runs -- it is
    written over this one, so the two cannot come to different conclusions --
    returned as records instead of one joined refusal:

    ``scope``   which list the problem is in, ``"step"`` or ``"trigger"``.
    ``index``   its position in that list. A problem inside a conditional's
                branch is reported against the branch's own top-level step,
                because that is the row somebody has to open to fix it.
    ``path``    the full location the message was written against
                (``steps[2].then_steps[0]``), for the sentence itself.
    ``message`` the sentence, without the path it was prefixed with.

    And what the editor needs to word a finding the way its form is labelled
    rather than by key: ``action`` (the step's action or the trigger's type)
    wherever it is known, and, on a finding that a field is not filled in,
    ``missing`` (``device``, ``condition.key``, ``state_key``...; ``param`` for
    one of the command's parameters, with ``param`` naming it and, when
    ``param_label`` can say, ``param_label`` its label on the form; its
    ``message`` is the runtime's refusal, from ``required_sentence``; the
    lookups are :class:`_Lookups`, by keyword). A step
    whose command no macro can run (``file_command``) carries
    ``refused: "file"``, an Emit Event step naming one of the platform's
    own events ``refused: "platform_event"``, and a Set Variable step whose
    write is refused ``refused: "platform_key"`` or ``refused: "value"``; each
    message is the runtime's own refusal. A trigger whose id a trigger loaded
    after it also has never fires: its record carries ``same_id_as``, the
    place of the one kept instead (``index``, and ``macro`` when it is in
    another macro). ``trigger_ids`` is the ids of the macros checked together,
    which only a caller holding all of them can say; without it, only this
    macro's own triggers are compared.

    Nothing here is a refusal, and every record but one kind means the step or
    trigger as written cannot do what it says. That kind carries
    ``warning: true``: a Set Variable step on a device's key, which runs and
    lasts until the device's next report. The editor does not count it as a
    step that won't run, and ``validate_macro`` does not refuse on it.
    Deliberately NOT
    included are ``validate_macro``'s soft reference checks (a device or macro
    id that no longer resolves) -- those are logged there rather than raised
    for a reason that still holds, that a macro may legitimately be authored
    before the device it drives exists.

    ``missing_params`` belongs here rather than with those, and the difference
    is worth being clear about: an unresolved id may resolve tomorrow, but a
    driver that is loaded and declares a parameter required is telling us about
    today. When it is not loaded the check says nothing at all, so the
    authored-in-advance case stays as quiet as it is for the id checks.
    """
    issues: list[dict[str, Any]] = []
    bundle = _Lookups(**lookups)

    if isinstance(steps, list):
        for i, step in enumerate(steps):
            if isinstance(step, dict):
                found = _step_findings(step, f"steps[{i}]", bundle)
            else:
                found = [_finding(f"steps[{i}]", f"expected an object, got {type(step).__name__}")]
            issues.extend({"scope": "step", "index": i, **f} for f in found)

    if isinstance(triggers, list):
        ids = trigger_ids or TriggerIds(trigger_holders([{"triggers": triggers}]), "")
        for i, trigger in enumerate(triggers):
            if isinstance(trigger, dict):
                found = _trigger_findings(trigger, f"triggers[{i}]")
                found.extend(_same_id_findings(trigger, i, ids))
            else:
                found = [_finding(
                    f"triggers[{i}]", f"expected an object, got {type(trigger).__name__}",
                )]
            issues.extend({"scope": "trigger", "index": i, **f} for f in found)

    return issues


def validate_macro(
    steps: list, triggers: list, project: Any = None, *, macro_id: str | None = None,
    **lookups: Any,
) -> str | None:
    """Validate macro steps and triggers. Returns error string or None.

    ``project`` enables soft reference checks (unknown device/group/macro ids,
    and a page move's page, are logged as warnings, not errors — a macro may
    legitimately be authored before the device it drives or the page it
    opens). It also refuses a trigger whose id a trigger of another of its
    macros has; ``macro_id`` is the macro being written, whose saved copy is
    not another macro. ``lookups`` see validate_macro_step.
    """
    # One traversal, shared with the editor's lint door: what refuses a
    # generated macro and what marks a hand-built one are the same findings,
    # rendered two ways.
    errors: list[str] = [
        _ai_line(issue)
        for issue in macro_issues(steps, triggers, **lookups)
        if not issue.get("warning")
    ]
    errors += _taken_id_lines(triggers, project, macro_id)
    warnings: list[str] = []
    if project and not errors:
        refs = _ProjectRefs.of(project)
        for i, step in enumerate(steps if isinstance(steps, list) else []):
            if isinstance(step, dict):
                warnings += _reference_warnings(step, f"steps[{i}]", refs)

    if errors:
        return "Macro validation failed: " + "; ".join(errors)
    if warnings:
        log.warning("Macro reference warnings: %s", "; ".join(warnings))
    return None


# --- Soft reference checks ----------------------------------------------------
#
# What ``validate_macro`` logs, never refuses: an id a step names that this
# project does not have. A macro may legitimately be authored before the
# device, group or page it names exists.


@dataclass(frozen=True)
class _ProjectRefs:
    """The ids a macro step may name, read once from the project."""

    devices: frozenset[str]
    macros: frozenset[str]
    #: Each group's MEMBERS too, which nothing else checks: the project save
    #: is shape-only, so a group listing a device that does not exist saves
    #: silently and the fan-out then reports the ghost as an offline device.
    groups: Mapping[str, list[str]]
    #: The pages a page move can land on: the project's, and the panel's own
    #: ``$back`` and ``$dismiss``.
    page_targets: frozenset[str]

    @classmethod
    def of(cls, project: Any) -> _ProjectRefs:
        # Imported here: openavc.ui's reviews import this module.
        from openavc.ui.page_references import NAVIGATION_SENTINELS

        return cls(
            devices=_ids(project, "devices"),
            macros=_ids(project, "macros"),
            groups={g.id: list(g.device_ids or []) for g in _listed(project, "device_groups")},
            page_targets=_ids(getattr(project, "ui", None), "pages") | NAVIGATION_SENTINELS,
        )


def _listed(holder: Any, name: str) -> list:
    """``holder.<name>``, or an empty list when it has none."""
    return getattr(holder, name, None) or []


def _ids(holder: Any, name: str) -> frozenset[str]:
    return frozenset(item.id for item in _listed(holder, name))


def _device_reference(step: dict, path: str, refs: _ProjectRefs) -> list[str]:
    device = step.get("device")
    if not device or device in refs.devices:
        return []
    return [f"{path}: device '{device}' not found in project"]


def _group_reference(step: dict, path: str, refs: _ProjectRefs) -> list[str]:
    group = step.get("group")
    if not group:
        return []
    if group not in refs.groups:
        return [f"{path}: device group '{group}' not found in project"]
    return [
        f"{path}: device group '{group}' lists device '{member}', which is not in the project"
        for member in refs.groups[group] if member not in refs.devices
    ]


def _macro_reference(step: dict, path: str, refs: _ProjectRefs) -> list[str]:
    macro = step.get("macro")
    if not macro or macro in refs.macros:
        return []
    return [f"{path}: macro '{macro}' not found in project"]


def _page_reference(step: dict, path: str, refs: _ProjectRefs) -> list[str]:
    page = step.get("page")
    if not page or page in refs.page_targets:
        return []
    return [f"{path}: page '{page}' not found in project"]


#: The one id each action names, checked by its kind.
_REFERENCE_CHECKS: dict[str, Callable[[dict, str, _ProjectRefs], list[str]]] = {
    "device.command": _device_reference,
    "group.command": _group_reference,
    "macro": _macro_reference,
    "ui.navigate": _page_reference,
}


def _reference_warnings(step: dict, path: str, refs: _ProjectRefs) -> list[str]:
    """What ``step`` names that the project does not have, then its branches'."""
    check = _REFERENCE_CHECKS.get(step.get("action", ""))
    found = check(step, path, refs) if check else []
    for branch in ("then_steps", "else_steps"):
        for i, sub in enumerate(step.get(branch) or []):
            if isinstance(sub, dict):
                found += _reference_warnings(sub, f"{path}.{branch}[{i}]", refs)
    return found
