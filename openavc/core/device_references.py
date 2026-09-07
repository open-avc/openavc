"""What in this project still points at a device, asked once for every door.

A device is not a leaf. By the time somebody deletes one it is named by device
groups, by macro steps buried in conditional branches, by triggers that watch
``device.<id>.connected``, by the show and do bindings of controls on pages a
panel is drawing right now, and by scripts. Deleting it does not break any of
them loudly: the macro step raises at run time, the trigger never fires again,
the bound control draws its floor, and nobody finds out until the room is in
use.

So every door that deletes a device has to be able to say what it is about to
orphan -- and until this module existed, three different answers to that one
question were written three different ways, of three different qualities:

* the Builder's delete dialog (``deviceUtils.ts`` ``findDeviceReferences``) was
  thorough and correct;
* the AI's ``_find_references("device", ...)`` checked ``step["device"]`` at the
  top level of a macro and nothing else -- no groups, no ``then_steps``, no
  trigger, no ``show`` binding on a ``device.<id>.<prop>`` key. Being told the
  impact is empty when it is not is worse than being told nothing;
* the REST door ``DELETE /api/devices/{id}`` asked nothing at all.

This is the Builder's rule, ported, and it is now the only one on the server.
The Builder keeps its own copy because its dialog runs on UNSAVED project state
that the server has never seen -- so the two are pinned message for message by
``tests/test_device_references_parity.py``, the way ``page_review`` and
``reviewPage`` are. Edit one side and the test names the other.

What a door does with the answer
--------------------------------
It reports it. ``DELETE /api/devices/{id}`` returns the references on a
successful delete rather than refusing, which is the rule ``delete_variable``
and ``delete_macro`` already established and the rule the AI's own
``delete_device`` already followed. The only door with a human on it lists the
references BEFORE it calls DELETE, so a refusal would be a second gate behind
one that already works, and it would turn a 200 into a 409 for everything
deleting on purpose. The cost of that choice, stated plainly: a caller that
ignores the response body gets nothing -- but such a caller ignores a refusal's
body too, so the refusal would buy only the status code.

Reporting, and the one thing that is swept instead
--------------------------------------------------
Almost everything here is reported and left alone, because what to do about a
macro step or a bound control is the author's call and only they can make it.
Device-group membership is the exception, and ``drop_device_from_groups`` is
it: a group is a list of ids with no other content, so an id left in one after
its device is gone has nothing a person could decide about it. It cannot be
repaired, only removed, and until it is the group reports the ghost as an
offline device forever. The same reasoning the doors already apply to
monitors, which they drop outright -- a monitor whose key can never report
again is a tile reading "--" forever.

A group emptied by the sweep is kept, not deleted. An empty group is a legal
and useful state (the fan-out skips it cleanly), and deleting one because its
last member went would throw away a name, a place in the IDE and every macro
step and binding that points at it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeviceReference:
    """One thing that still names the device.

    ``message`` is the whole finding in one self-contained sentence and is the
    half that is pinned against the Builder -- it is what reaches a person or an
    AI, and two surfaces phrasing the same dependency differently is how they
    drift apart unnoticed. ``kind`` groups them; ``where`` carries the ids a
    caller needs to actually go and fix the reference.
    """

    kind: str
    message: str
    where: dict[str, Any] = field(default_factory=dict)


def _key_targets_device(value: Any, device_id: str) -> bool:
    """True when ``value`` is a state key aimed at this device.

    Anchored on both ends -- exactly ``device.<id>`` or beginning
    ``device.<id>.`` -- so ``device.proj1`` never matches a sibling
    ``device.proj10``. A bare ``startswith`` or a substring test over-reports,
    which on a delete confirmation is a warning about a device that is not
    affected. A leading ``$`` (a dynamic macro parameter such as
    ``$device.<id>.volume``) is tolerated.
    """
    if not isinstance(value, str):
        return False
    s = value[1:] if value.startswith("$") else value
    prefix = f"device.{device_id}"
    return s == prefix or s.startswith(f"{prefix}.")


def _group_contains_device(project: Any, group_id: Any, device_id: str) -> bool:
    """True when device group ``group_id`` exists and lists the device.

    A ``group.command`` step names a group, not a device, so this is what
    resolves it back to the devices it actually drives.
    """
    if not isinstance(group_id, str):
        return False
    for group in getattr(project, "device_groups", None) or []:
        if group.id == group_id:
            return device_id in (group.device_ids or [])
    return False


def _tree_references_device(value: Any, device_id: str, project: Any) -> bool:
    """Recursively test a value tree for a reference to the device.

    Catches a state key at any depth, and any object node that is a command
    action or step targeting the device directly (``device: "<id>"``) or via a
    ``group.command`` whose group contains it. Macro-step params, ``state.set``
    values and the show/do binding trees on UI elements all nest device
    references at arbitrary depth, so none of them can be checked one level
    deep.
    """
    if isinstance(value, str):
        return _key_targets_device(value, device_id)
    if isinstance(value, (list, tuple)):
        return any(_tree_references_device(v, device_id, project) for v in value)
    if isinstance(value, dict):
        if value.get("device") == device_id:
            return True
        if _group_contains_device(project, value.get("group"), device_id):
            return True
        return any(_tree_references_device(v, device_id, project) for v in value.values())
    return False


def _step_targets_device(step: Any, device_id: str, project: Any) -> bool:
    """True when one macro step targets the device.

    A direct ``device.command``, a ``group.command`` on a group containing it, a
    ``state.set`` or guard key, or a ``$device.<id>`` reference buried in params
    or the set value. Does NOT descend into ``then_steps``/``else_steps`` -- the
    caller walks those so each nested step is counted on its own.
    """
    if getattr(step, "device", None) == device_id:
        return True
    if _group_contains_device(project, getattr(step, "group", None), device_id):
        return True
    if _key_targets_device(getattr(step, "key", None), device_id):
        return True
    condition = getattr(step, "condition", None)
    if condition is not None and _key_targets_device(getattr(condition, "key", None), device_id):
        return True
    skip_if = getattr(step, "skip_if", None)
    if skip_if is not None and _key_targets_device(getattr(skip_if, "key", None), device_id):
        return True
    if _tree_references_device(getattr(step, "params", None), device_id, project):
        return True
    if _tree_references_device(getattr(step, "value", None), device_id, project):
        return True
    return False


def _count_step_references(steps: Any, device_id: str, project: Any) -> int:
    """Count steps referencing the device, recursing into conditional branches."""
    count = 0
    for step in steps or []:
        if _step_targets_device(step, device_id, project):
            count += 1
        count += _count_step_references(getattr(step, "then_steps", None), device_id, project)
        count += _count_step_references(getattr(step, "else_steps", None), device_id, project)
    return count


def _event_pattern_targets_device(pattern: Any, device_id: str) -> bool:
    """True when an event trigger pattern targets the device.

    Device lifecycle events are ``device.<event>.<deviceId>`` (connected,
    disconnected, error, ...), so the id is matched only as a whole dotted
    segment -- never ``proj10`` for ``proj1`` -- and a wildcard glob that fans
    out to every device is not a reference to this one.
    """
    if not isinstance(pattern, str) or not pattern.startswith("device."):
        return False
    return device_id in pattern.split(".")


def _element_reference(
    element: Any, device_id: str, project: Any, message: str, where: dict[str, Any]
) -> DeviceReference | None:
    """One UI element's reference, with the binding slots that carry it.

    The verdict is taken over the whole ``bindings`` dict, which is the walk the
    Builder makes; the per-slot pass only fills in WHICH slots so a caller can
    go straight to them. It never changes the answer.
    """
    bindings = getattr(element, "bindings", None) or {}
    if not _tree_references_device(bindings, device_id, project):
        return None
    slots = sorted(
        slot
        for slot, binding in bindings.items()
        if _tree_references_device(binding, device_id, project)
    )
    return DeviceReference(kind="binding", message=message, where={**where, "slots": slots})


def find_device_references(project: Any, device_id: str) -> list[DeviceReference]:
    """Everything in the project that still names ``device_id``.

    Walked in a fixed order -- device groups, then each macro's steps and
    triggers, then page elements, then master elements -- because the Builder
    walks it in that order and the two are compared finding for finding.
    """
    refs: list[DeviceReference] = []

    # Device group membership. A group.command acts on this device, and an id
    # left behind in a group after the delete is itself a broken reference.
    for group in getattr(project, "device_groups", None) or []:
        if device_id in (group.device_ids or []):
            refs.append(DeviceReference(
                kind="device_group",
                message=f'Device group "{group.name}"',
                where={"group_id": group.id, "group_name": group.name},
            ))

    for macro in getattr(project, "macros", None) or []:
        step_hits = _count_step_references(macro.steps, device_id, project)
        if step_hits > 0:
            refs.append(DeviceReference(
                kind="macro",
                message=f'Macro "{macro.name}": {step_hits} step(s)',
                where={"macro_id": macro.id, "macro_name": macro.name, "steps": step_hits},
            ))
        for trigger in getattr(macro, "triggers", None) or []:
            where = {
                "macro_id": macro.id,
                "macro_name": macro.name,
                "trigger_id": getattr(trigger, "id", "") or "",
            }
            state_key = getattr(trigger, "state_key", None)
            if _key_targets_device(state_key, device_id):
                refs.append(DeviceReference(
                    kind="trigger",
                    message=f'Macro "{macro.name}" trigger: {state_key}',
                    where={**where, "field": "state_key", "value": state_key},
                ))
            pattern = getattr(trigger, "event_pattern", None)
            if _event_pattern_targets_device(pattern, device_id):
                refs.append(DeviceReference(
                    kind="trigger",
                    message=f'Macro "{macro.name}" trigger: {pattern}',
                    where={**where, "field": "event_pattern", "value": pattern},
                ))
            for condition in getattr(trigger, "conditions", None) or []:
                if _key_targets_device(getattr(condition, "key", None), device_id):
                    refs.append(DeviceReference(
                        kind="trigger",
                        message=f'Macro "{macro.name}" trigger condition: {condition.key}',
                        where={**where, "field": "condition", "value": condition.key},
                    ))

    ui = getattr(project, "ui", None)
    for page in getattr(ui, "pages", None) or []:
        for element in page.elements or []:
            ref = _element_reference(
                element, device_id, project,
                f'UI page "{page.name}" element "{element.label or element.id}"',
                {"page_id": page.id, "element_id": element.id},
            )
            if ref is not None:
                refs.append(ref)

    # Master elements are repeated across pages and carry their own bindings, so
    # a device named only by one is bound on every page it appears on -- and was
    # invisible to all three of the old scanners.
    for master in getattr(ui, "master_elements", None) or []:
        ref = _element_reference(
            master, device_id, project,
            f'UI master element "{master.label or master.id}"',
            {"element_id": master.id},
        )
        if ref is not None:
            refs.append(ref)

    return refs


def drop_device_from_groups(groups: Any, device_id: str) -> list[Any]:
    """Every group with ``device_id`` removed from its membership.

    Returns the new list; groups that did not list it come back untouched, and
    a group left empty is kept rather than deleted (see the module docstring).
    Mirrors ``monitors.drop_monitors_for_device``, which every delete door
    already calls beside this one.
    """
    swept: list[Any] = []
    for group in groups or []:
        members = list(group.device_ids or [])
        if device_id in members:
            group = group.model_copy(
                update={"device_ids": [d for d in members if d != device_id]}
            )
        swept.append(group)
    return swept


def find_script_references(project: Any, scripts_dir: Path, ref_id: str) -> list[dict[str, Any]]:
    """Grep the project's scripts for ``ref_id``.

    Deliberately a plain substring test and deliberately not part of the
    parity corpus: a script is Python the platform does not parse, the Builder
    cannot read one at all, and a loose hit before a destructive delete is a
    cheap warning while a missed one is a broken room. Paths are
    containment-checked like the scripts API route, and ``scripts_dir`` is
    passed in rather than assumed -- the scripts live beside the LOADED project
    file, and every non-dev deployment puts that somewhere other than
    ``projects/default``.
    """
    from openavc.utils.paths import safe_path_within

    hits: list[dict[str, Any]] = []
    for script in getattr(project, "scripts", None) or []:
        try:
            script_path = safe_path_within(scripts_dir, script.file)
            if script_path is None or not script_path.exists():
                continue
            if ref_id in script_path.read_text(encoding="utf-8"):
                hits.append({"script_id": script.id, "file": script.file})
        except (OSError, UnicodeDecodeError):
            log.debug("Failed to read script '%s' for reference check", script.file)
    return hits


_REPORT_KEYS = {
    "device_group": "device_groups",
    "macro": "macros",
    "trigger": "triggers",
    "binding": "bindings",
}


def as_reference_report(
    refs: list[DeviceReference], scripts: list[dict[str, Any]] | None = None
) -> dict[str, list[dict[str, Any]]]:
    """The references as one JSON body, grouped by kind, empties dropped.

    One shape for both doors: it is what ``DELETE /api/devices/{id}`` returns
    and what the AI's ``delete_device`` and ``check_references`` hand back, so a
    person reading an API response and an AI reading a tool result are looking
    at the same thing. Each entry carries its sentence alongside its ids --
    the ids are for going and fixing it, the sentence is for saying what it is.
    """
    report: dict[str, list[dict[str, Any]]] = {}
    for ref in refs:
        report.setdefault(_REPORT_KEYS[ref.kind], []).append({**ref.where, "message": ref.message})
    if scripts:
        report["scripts"] = list(scripts)
    return report
