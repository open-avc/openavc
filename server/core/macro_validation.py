"""Semantic validation for macro steps and triggers.

The Pydantic models in ``project_loader`` check a macro's *shape* (field names
and types). These rules check its *meaning*: that the step action exists, that
the fields that action needs are filled in, that operator and trigger-type
names are ones the runtime understands, and that referenced ids resolve.

It lives in core, not next to a caller, because more than one door creates
macros and they must agree on what a valid macro is. Previously these rules
existed only inside the cloud AI tool layer, so a macro the AI refused to write
saved without complaint through the REST project-save door.

Enforcement is deliberately not symmetric, and that is a policy choice, not
drift: a generator (the AI tools) is told immediately when it emits something
the runtime can't run, while the IDE's project save stays shape-only because a
half-built macro is a normal intermediate state for someone editing one.
"""

from __future__ import annotations

from collections.abc import Collection
from typing import Any

from server.core.condition_eval import _OPERATOR_ALIASES as _COND_ALIASES
from server.utils.logger import get_logger

log = get_logger(__name__)



BUILTIN_STEP_ACTIONS = frozenset((
    "device.command", "group.command", "delay", "state.set",
    "macro", "event.emit", "conditional", "wait_until", "ui.navigate",
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


def validate_macro_step(
    step: dict, path: str, *, extra_actions: Collection[str] = ()
) -> list[str]:
    """Validate a single macro step. Returns list of error strings.

    ``extra_actions`` carries the action types plugins have registered with
    the macro engine at runtime. They are as valid as the built-ins, but only
    the engine knows them, so a caller that can reach it must pass them in —
    otherwise a macro using a plugin action reads as malformed.
    """
    errors: list[str] = []
    action = step.get("action", "")
    if not action:
        errors.append(f"{path}: missing 'action' field")
        return errors
    if action not in BUILTIN_STEP_ACTIONS and action not in extra_actions:
        usable = ", ".join(sorted(BUILTIN_STEP_ACTIONS) + sorted(extra_actions))
        errors.append(
            f"{path}: step action '{action}' is not valid. Use: {usable}"
        )
        return errors

    required = STEP_REQUIRED_FIELDS.get(action, ())
    for field in required:
        val = step.get(field)
        if val is None or val == "":
            errors.append(f"{path}: {action} step requires '{field}'")

    if action == "delay":
        seconds = step.get("seconds")
        if seconds is not None and (not isinstance(seconds, (int, float)) or seconds < 0):
            errors.append(f"{path}: delay 'seconds' must be a non-negative number")

    if action == "conditional":
        cond = step.get("condition")
        if isinstance(cond, dict):
            if not cond.get("key"):
                errors.append(f"{path}: conditional condition requires 'key'")
            op = cond.get("operator", "eq")
            if op not in VALID_CONDITION_OPS:
                errors.append(f"{path}: condition operator '{op}' is not valid")
        # Recursively validate then/else steps
        for branch in ("then_steps", "else_steps"):
            branch_steps = step.get(branch)
            if isinstance(branch_steps, list):
                for i, sub in enumerate(branch_steps):
                    if isinstance(sub, dict):
                        errors.extend(validate_macro_step(
                            sub, f"{path}.{branch}[{i}]", extra_actions=extra_actions
                        ))

    if action == "wait_until":
        cond = step.get("condition")
        if isinstance(cond, dict):
            if not cond.get("key"):
                errors.append(f"{path}: wait_until condition requires 'key'")
            op = cond.get("operator", "eq")
            if op not in VALID_CONDITION_OPS:
                errors.append(f"{path}: wait_until condition operator '{op}' is not valid")
        # timeout is required as a field, but may be null to mean "never time out"
        if "timeout" not in step:
            errors.append(
                f"{path}: wait_until requires 'timeout' (number of seconds, or null for no timeout)"
            )
        else:
            tmo = step.get("timeout")
            if tmo is not None and (not isinstance(tmo, (int, float)) or tmo < 0):
                errors.append(f"{path}: wait_until 'timeout' must be a non-negative number or null")
        on_timeout = step.get("on_timeout")
        if on_timeout is not None and on_timeout not in ("fail", "continue"):
            errors.append(f"{path}: wait_until 'on_timeout' must be 'fail' or 'continue'")

    # Validate skip_if guard
    skip_if = step.get("skip_if")
    if isinstance(skip_if, dict):
        if not skip_if.get("key"):
            errors.append(f"{path}: skip_if requires 'key'")
        op = skip_if.get("operator", "eq")
        if op not in VALID_CONDITION_OPS:
            errors.append(f"{path}: skip_if operator '{op}' is not valid")

    return errors


def validate_trigger(trigger: dict, path: str) -> list[str]:
    """Validate a single trigger definition. Returns list of error strings."""
    errors: list[str] = []
    ttype = trigger.get("type", "")
    if not ttype:
        errors.append(f"{path}: missing 'type' field")
        return errors
    if ttype not in VALID_TRIGGER_TYPES:
        errors.append(
            f"{path}: trigger type '{ttype}' is not valid. "
            f"Use: schedule, state_change, event, startup"
        )
        return errors

    if ttype == "schedule":
        cron = trigger.get("cron")
        if not cron or not isinstance(cron, str):
            errors.append(f"{path}: schedule trigger requires 'cron' (string)")
        elif cron:
            parts = cron.strip().split()
            if len(parts) not in (5, 6):
                errors.append(f"{path}: cron expression must have 5 or 6 fields, got {len(parts)}")
    elif ttype == "state_change":
        if not trigger.get("state_key"):
            errors.append(f"{path}: state_change trigger requires 'state_key'")
        op = trigger.get("state_operator")
        if op and op not in VALID_STATE_TRIGGER_OPS:
            errors.append(
                f"{path}: state_operator '{op}' is not valid. "
                f"Use: any, eq, ne, gt, lt, gte, lte, truthy, falsy"
            )
    elif ttype == "event":
        if not trigger.get("event_pattern"):
            errors.append(f"{path}: event trigger requires 'event_pattern'")

    overlap = trigger.get("overlap")
    if overlap and overlap not in VALID_OVERLAP_MODES:
        errors.append(f"{path}: overlap '{overlap}' is not valid. Use: skip, queue, allow")

    # Validate guard conditions
    conditions = trigger.get("conditions")
    if isinstance(conditions, list):
        for i, c in enumerate(conditions):
            if isinstance(c, dict):
                if not c.get("key"):
                    errors.append(f"{path}.conditions[{i}]: missing 'key'")
                op = c.get("operator", "eq")
                if op not in VALID_CONDITION_OPS:
                    errors.append(f"{path}.conditions[{i}]: operator '{op}' is not valid")

    return errors


def validate_macro(
    steps: list,
    triggers: list,
    project: Any = None,
    *,
    extra_actions: Collection[str] = (),
) -> str | None:
    """Validate macro steps and triggers. Returns error string or None.

    ``project`` enables soft reference checks (unknown device/group/macro ids
    are logged as warnings, not errors — a macro may legitimately be authored
    before the device it drives). ``extra_actions`` see validate_macro_step.
    """
    errors: list[str] = []
    warnings: list[str] = []

    if isinstance(steps, list):
        for i, step in enumerate(steps):
            if isinstance(step, dict):
                errors.extend(validate_macro_step(
                    step, f"steps[{i}]", extra_actions=extra_actions
                ))
            else:
                errors.append(f"steps[{i}]: expected an object, got {type(step).__name__}")

    if isinstance(triggers, list):
        for i, trigger in enumerate(triggers):
            if isinstance(trigger, dict):
                errors.extend(validate_trigger(trigger, f"triggers[{i}]"))
            else:
                errors.append(f"triggers[{i}]: expected an object, got {type(trigger).__name__}")

    # Soft reference checks
    if project and not errors:
        macro_ids = {m.id for m in project.macros} if hasattr(project, "macros") else set()
        device_ids = {d.id for d in project.devices} if hasattr(project, "devices") else set()
        group_ids = {g.id for g in project.device_groups} if hasattr(project, "device_groups") else set()

        def _check_step_refs(step: dict, path: str) -> None:
            action = step.get("action", "")
            if action == "device.command" and step.get("device"):
                if step["device"] not in device_ids:
                    warnings.append(f"{path}: device '{step['device']}' not found in project")
            if action == "group.command" and step.get("group"):
                if step["group"] not in group_ids:
                    warnings.append(f"{path}: device group '{step['group']}' not found in project")
            if action == "macro" and step.get("macro"):
                if step["macro"] not in macro_ids:
                    warnings.append(f"{path}: macro '{step['macro']}' not found in project")
            for branch in ("then_steps", "else_steps"):
                for i, sub in enumerate(step.get(branch) or []):
                    if isinstance(sub, dict):
                        _check_step_refs(sub, f"{path}.{branch}[{i}]")

        if isinstance(steps, list):
            for i, step in enumerate(steps):
                if isinstance(step, dict):
                    _check_step_refs(step, f"steps[{i}]")

    if errors:
        return "Macro validation failed: " + "; ".join(errors)
    if warnings:
        log.warning("Macro reference warnings: %s", "; ".join(warnings))
    return None
