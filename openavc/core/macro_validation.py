"""Semantic validation for macro steps and triggers.

The Pydantic models in ``project_loader`` check a macro's *shape* (field names
and types). These rules check its *meaning*: that the step action exists, that
the fields that action needs are filled in, that the command it names can
actually run, that operator and trigger-type names are ones the runtime
understands, and that referenced ids resolve.

It lives in core, not next to a caller, because more than one door creates
macros and they must agree on what a valid macro is. Previously these rules
existed only inside the cloud AI tool layer, so a macro the AI refused to write
saved without complaint through the REST project-save door.

Enforcement is deliberately not symmetric, and that is a policy choice, not
drift: a generator (the AI tools) is told immediately when it emits something
the runtime can't run, while the IDE's project save stays shape-only because a
half-built macro is a normal intermediate state for someone editing one.

What the IDE gets instead is ``macro_issues`` -- the same rules, the same
traversal, returned as placed records rather than one refusal string, so the
editor can mark the step that is incomplete and the macro list can mark the
macro without anything being blocked or rejected. That is the third option:
a macro that saves cleanly and then does nothing is the failure this closes,
and it is invisible precisely because nobody reopens a macro that saved.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from typing import Any

from openavc.core.condition_eval import _OPERATOR_ALIASES as _COND_ALIASES
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
VALID_TRIGGER_TYPES = frozenset(("schedule", "state_change", "event", "startup"))
# Accept both canonical operator names and the user-facing aliases that
# condition_eval normalizes at runtime (==, !=, >=, equals, greater_than, …).
# Otherwise the AI tools reject macros loaded from human-edited project files
# that use the aliases the project format docs and triggerHelpers emit.
CANONICAL_CONDITION_OPS = frozenset((
    "eq", "ne", "gt", "lt", "gte", "lte", "truthy", "falsy",
))
VALID_CONDITION_OPS = frozenset(CANONICAL_CONDITION_OPS | _COND_ALIASES.keys())
VALID_STATE_TRIGGER_OPS = frozenset({"any"} | VALID_CONDITION_OPS)
VALID_OVERLAP_MODES = frozenset(("skip", "queue", "allow"))


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

    if action == "delay":
        seconds = step.get("seconds")
        if seconds is not None and (not isinstance(seconds, (int, float)) or seconds < 0):
            found.append(_finding(
                path, "delay 'seconds' must be a non-negative number",
                action=action,
            ))

    if action == "conditional":
        cond = step.get("condition")
        if isinstance(cond, dict):
            if not cond.get("key"):
                found.append(_finding(
                    path, "conditional condition requires 'key'",
                    action=action, missing="condition.key",
                ))
            op = cond.get("operator", "eq")
            if op not in VALID_CONDITION_OPS:
                found.append(_finding(
                    path, f"condition operator '{op}' is not valid",
                    action=action,
                ))
        # Recursively validate then/else steps
        for branch in ("then_steps", "else_steps"):
            branch_steps = step.get(branch)
            if isinstance(branch_steps, list):
                for i, sub in enumerate(branch_steps):
                    if isinstance(sub, dict):
                        found.extend(_step_findings(sub, f"{path}.{branch}[{i}]", lookups))

    if action == "wait_until":
        cond = step.get("condition")
        if isinstance(cond, dict):
            if not cond.get("key"):
                found.append(_finding(
                    path, "wait_until condition requires 'key'",
                    action=action, missing="condition.key",
                ))
            op = cond.get("operator", "eq")
            if op not in VALID_CONDITION_OPS:
                found.append(_finding(
                    path, f"wait_until condition operator '{op}' is not valid",
                    action=action,
                ))
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
        if on_timeout is not None and on_timeout not in ("fail", "continue"):
            found.append(_finding(
                path, "wait_until 'on_timeout' must be 'fail' or 'continue'",
                action=action,
            ))

    # Validate skip_if guard
    skip_if = step.get("skip_if")
    if isinstance(skip_if, dict):
        if not skip_if.get("key"):
            found.append(_finding(
                path, "skip_if requires 'key'", action=action, missing="skip_if.key",
            ))
        op = skip_if.get("operator", "eq")
        if op not in VALID_CONDITION_OPS:
            found.append(_finding(
                path, f"skip_if operator '{op}' is not valid",
                action=action,
            ))

    return found


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
    return [_ai_line(f) for f in _step_findings(step, path, _Lookups(**lookups))]


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
            f"trigger type '{ttype}' is not valid. "
            f"Use: schedule, state_change, event, startup",
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
        if not trigger.get("state_key"):
            found.append(_finding(
                path, "state_change trigger requires 'state_key'",
                action=ttype, missing="state_key",
            ))
        op = trigger.get("state_operator")
        if op and op not in VALID_STATE_TRIGGER_OPS:
            found.append(_finding(
                path,
                f"state_operator '{op}' is not valid. "
                f"Use: any, eq, ne, gt, lt, gte, lte, truthy, falsy",
                action=ttype,
            ))
    elif ttype == "event":
        if not trigger.get("event_pattern"):
            found.append(_finding(
                path, "event trigger requires 'event_pattern'",
                action=ttype, missing="event_pattern",
            ))

    overlap = trigger.get("overlap")
    if overlap and overlap not in VALID_OVERLAP_MODES:
        found.append(_finding(
            path, f"overlap '{overlap}' is not valid. Use: skip, queue, allow",
            action=ttype,
        ))

    # Validate guard conditions
    conditions = trigger.get("conditions")
    if isinstance(conditions, list):
        for i, c in enumerate(conditions):
            if isinstance(c, dict):
                if not c.get("key"):
                    found.append(_finding(
                        f"{path}.conditions[{i}]", "missing 'key'",
                        action=ttype, missing="conditions.key",
                    ))
                op = c.get("operator", "eq")
                if op not in VALID_CONDITION_OPS:
                    found.append(_finding(
                        f"{path}.conditions[{i}]", f"operator '{op}' is not valid",
                        action=ttype,
                    ))

    return found


def validate_trigger(trigger: dict, path: str) -> list[str]:
    """Validate a single trigger definition. Returns list of error strings."""
    return [f"{f['path']}: {f['message']}" for f in _trigger_findings(trigger, path)]


def macro_issues(steps: Any, triggers: Any, **lookups: Any) -> list[dict[str, Any]]:
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
    ``refused: "file"``; its message is the runtime's own refusal.

    Nothing here is a warning and nothing is a refusal: every record means the
    step or trigger as written cannot do what it says. Deliberately NOT
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
        for i, trigger in enumerate(triggers):
            if isinstance(trigger, dict):
                found = _trigger_findings(trigger, f"triggers[{i}]")
            else:
                found = [_finding(
                    f"triggers[{i}]", f"expected an object, got {type(trigger).__name__}",
                )]
            issues.extend({"scope": "trigger", "index": i, **f} for f in found)

    return issues


def validate_macro(
    steps: list, triggers: list, project: Any = None, **lookups: Any,
) -> str | None:
    """Validate macro steps and triggers. Returns error string or None.

    ``project`` enables soft reference checks (unknown device/group/macro ids,
    and a page move's page, are logged as warnings, not errors — a macro may
    legitimately be authored before the device it drives or the page it
    opens). ``lookups`` see validate_macro_step.
    """
    # One traversal, shared with the editor's lint door: what refuses a
    # generated macro and what marks a hand-built one are the same findings,
    # rendered two ways.
    errors: list[str] = [
        _ai_line(issue)
        for issue in macro_issues(steps, triggers, **lookups)
    ]
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
