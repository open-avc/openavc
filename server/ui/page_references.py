"""What a page's bindings POINT AT, and whether any of it is there.

(Plus one thing about their SHAPE. An action list written as a single object
rather than an array is checked here because this is the module that walks every
``do`` slot and has to normalise that shape anyway; giving it a home of its own
would be a third module for one finding.)


``page_review`` answers what a page will draw wrong. This answers a different
question with the same posture: an element can be perfectly sized, perfectly
bound, and aimed at a macro that does not exist. The button draws, it takes a
press, and nothing happens -- ``ui_events``' dispatch chain ends without an
``else``, so there is no log line either.

Why this is a separate module from the review it rides beside
------------------------------------------------------------
Every check here needs the PROJECT (its pages, devices and macros) or the
driver registry, and ``review_page`` deliberately takes only a page. More to the
point, the Builder already runs these checks -- ``validateProject`` in
``uiBuilderHelpers.ts`` has resolved page ids, device ids and macro ids for a
while, including recursion into ``value_map``. So this is not a mirror waiting
to be written; it is the AI door catching up to the surface a human already had.
Keeping it out of ``page_review`` keeps that straight: what lives there is
mirrored byte for byte in ``reviewPage`` and pinned by
``tests/test_ui_review_parity.py``; what lives here is answered by
``validateProject`` on the other side, in the Validate panel, in its own words.

Two of these the Builder cannot do at all. It has no driver command list in
``validateProject``, so a command name is unchecked there; and nothing compares
a ``value_map`` against the select's own options.

What is deliberately NOT checked
--------------------------------
Whether a state key exists past its device id. ``device.amp1.channel.07.fader``
is a key a driver's child entities produce at runtime, and a key that resolves
to nothing today resolves fine the moment the device connects and reports it.
The device id itself IS checkable -- devices are declared in the project and the
set is closed -- so that is what is checked, and the rest is left alone rather
than guessed at. Same for ``var.``: a script or a macro can create one, so an
undeclared variable is not evidence of a mistake.

Everything here WARNS. The write lands and the findings ride back with it, for
the same reason as the rest of the review: a rejection costs a whole round trip,
a warning costs nothing.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any

from server.ui.page_review import Finding

#: Every interaction slot an element can carry actions under. The same set
#: ``ui_events`` dispatches and the Builder's ACTION_SLOTS enumerates.
ACTION_SLOTS = (
    "press", "release", "hold", "change", "submit", "select",
    "route", "audio_route", "mute_route", "audio_mute_route",
)


def _mapping(value: Any) -> Mapping[str, Any] | None:
    if isinstance(value, Mapping):
        return value
    dump = getattr(value, "model_dump", None)
    return dump() if callable(dump) else None


def _actions(do_map: Mapping[str, Any], slot: str) -> list[Mapping[str, Any]]:
    """The action list under one slot, whichever shape it was written in.

    A bare object is a one-element list here exactly as it is at runtime
    (``ui_events.py`` wraps a non-list before executing it), so an author who
    wrote the object form gets the same checking as one who wrote the array.
    """
    raw = do_map.get(slot)
    if raw is None:
        return []
    items = raw if isinstance(raw, list) else [raw]
    return [item for item in items if isinstance(item, Mapping)]


def _shape_finding(
    do_map: Mapping[str, Any], slot: str, el_id: str, el_type: str,
) -> Finding | None:
    """An action list written as a single object rather than an array.

    It RUNS -- ``ui_events.py`` wraps a non-list before executing it, and the
    dict form is handled explicitly for ``off_action`` and ``hold_action``. So
    this warns and does not reject: refusing a shape the runtime executes would
    be the review inventing a rule the platform does not have.

    What it costs is real but smaller than it looks: an object holds exactly one
    action, so a second can never be added without rewriting it, and the Builder
    stores the array form on the next edit either way.
    """
    raw = do_map.get(slot)
    if raw is None or isinstance(raw, list) or not isinstance(raw, Mapping):
        return None
    return Finding(
        el_id, "object_action_list",
        f"{el_id} ({el_type}) writes do.{slot} as an object, not an array. The panel does run "
        f"it -- a single action is wrapped before it executes -- but an object holds exactly "
        f"one, so a second cannot be added without rewriting it, and the Builder stores the "
        f"array form on the next edit anyway. Write do.{slot} as [{{...}}].",
        key=("object_action_list", el_id, slot),
    )


def _device_of(key: str, device_ids: Iterable[str]) -> str | None:
    """The device a state key belongs to, or None if no declared device owns it.

    Resolved by longest matching id rather than by splitting on dots: a device
    id may contain a dot, and a child-entity key looks exactly like a nested
    one. The same rule ``_declared_state_variable`` uses.
    """
    for device_id in sorted(device_ids, key=len, reverse=True):
        if key.startswith(f"device.{device_id}."):
            return device_id
    return None


def reference_findings(
    page: Any,
    *,
    touched: set[str] | None = None,
    page_ids: set[str],
    device_ids: set[str],
    macro_ids: set[str],
    device_commands: Callable[[str], set[str] | None] | None = None,
) -> list[Finding]:
    """Every binding on this page that names something that is not there.

    ``touched`` scopes it the way ``review_page`` is scoped: a write answers for
    what it wrote. Re-reporting the other fifty elements on every call is how a
    caller learns to skip the field.

    ``device_commands`` returns a driver's declared command names, or None for
    "no opinion" -- a device with no driver loaded, one that is disabled, a
    driver that enumerates nothing. None must never become a warning: the
    commonest reason to have it is that the device is simply not connected yet.
    """
    findings: list[Finding] = []
    in_scope = (lambda el_id: True) if touched is None else (lambda el_id: el_id in touched)

    for element in getattr(page, "elements", []) or []:
        dump = _mapping(element)
        if dump is None:
            continue
        el_id = str(dump.get("id", "?"))
        if not in_scope(el_id):
            continue
        findings.extend(_element_findings(
            dump, el_id, page_ids, device_ids, macro_ids, device_commands,
        ))
    return findings


def _element_findings(
    dump: Mapping[str, Any],
    el_id: str,
    page_ids: set[str],
    device_ids: set[str],
    macro_ids: set[str],
    device_commands: Callable[[str], set[str] | None] | None,
) -> list[Finding]:
    findings: list[Finding] = []
    el_type = str(dump.get("type", "?"))

    target = dump.get("target_page")
    if el_type == "page_nav" and isinstance(target, str) and target and target not in page_ids:
        findings.append(Finding(
            el_id, "dangling_reference",
            f"{el_id} ({el_type}) navigates to page '{target}', which does not exist. "
            f"The pages are: {_listed(page_ids)}.",
            key=("dangling_reference", el_id, "target_page"),
        ))

    bindings = _mapping(dump.get("bindings")) or {}
    show = _mapping(bindings.get("show")) or {}
    for slot in ("value", "look"):
        binding = _mapping(show.get(slot))
        key = binding.get("key") if binding else None
        if not isinstance(key, str) or not key.startswith("device."):
            continue
        if _device_of(key, device_ids) is None:
            named = key.split(".")[1] if key.count(".") >= 1 else key
            findings.append(Finding(
                el_id, "dangling_reference",
                f"{el_id} ({el_type}) reads show.{slot} from '{key}', and no device "
                f"'{named}' is in this project. The devices are: {_listed(device_ids)}.",
                key=("dangling_reference", el_id, f"show.{slot}"),
            ))

    do_map = _mapping(bindings.get("do")) or {}
    options = _option_values(dump)
    for slot in ACTION_SLOTS:
        shape = _shape_finding(do_map, slot, el_id, el_type)
        if shape:
            findings.append(shape)
        for index, action in enumerate(_actions(do_map, slot)):
            findings.extend(_action_findings(
                action, el_id, el_type, f"do.{slot}[{index}]",
                page_ids, device_ids, macro_ids, device_commands, options,
            ))
    return findings


def _option_values(dump: Mapping[str, Any]) -> set[str] | None:
    """A select's own option values, or None when it declares none.

    None is not an empty set: a select with no options has nothing to compare a
    value_map against, and saying its whole map is unreachable would be blaming
    the wrong half.
    """
    options = dump.get("options")
    if not isinstance(options, list) or not options:
        return None
    values = {
        str(opt["value"]) for opt in options
        if isinstance(opt, Mapping) and opt.get("value") is not None
    }
    return values or None


def _action_findings(
    action: Mapping[str, Any],
    el_id: str,
    el_type: str,
    where: str,
    page_ids: set[str],
    device_ids: set[str],
    macro_ids: set[str],
    device_commands: Callable[[str], set[str] | None] | None,
    options: set[str] | None,
) -> list[Finding]:
    findings: list[Finding] = []
    name = action.get("action")

    if name == "ui.navigate":
        target = action.get("page")
        if isinstance(target, str) and target and target not in page_ids:
            findings.append(Finding(
                el_id, "dangling_reference",
                f"{el_id} ({el_type}) {where} navigates to page '{target}', which does not "
                f"exist. The pages are: {_listed(page_ids)}.",
                key=("dangling_reference", el_id, where, "page"),
            ))

    elif name == "macro":
        macro = action.get("macro")
        if isinstance(macro, str) and macro and macro not in macro_ids:
            findings.append(Finding(
                el_id, "dangling_reference",
                f"{el_id} ({el_type}) {where} runs macro '{macro}', which does not exist. "
                f"The macros are: {_listed(macro_ids)}.",
                key=("dangling_reference", el_id, where, "macro"),
            ))

    elif name == "device.command":
        device = action.get("device")
        command = action.get("command")
        if isinstance(device, str) and device and device not in device_ids:
            findings.append(Finding(
                el_id, "dangling_reference",
                f"{el_id} ({el_type}) {where} commands device '{device}', which is not in "
                f"this project. The devices are: {_listed(device_ids)}.",
                key=("dangling_reference", el_id, where, "device"),
            ))
        elif isinstance(device, str) and isinstance(command, str) and device_commands:
            declared = device_commands(device)
            if declared is not None and command not in declared:
                findings.append(Finding(
                    el_id, "dangling_reference",
                    f"{el_id} ({el_type}) {where} sends '{command}' to '{device}', which its "
                    f"driver does not have. Its commands are: {_listed(declared)}.",
                    key=("dangling_reference", el_id, where, "command"),
                ))

    elif name == "value_map":
        mapped = _mapping(action.get("map")) or {}
        if options is not None:
            stray = sorted(str(k) for k in mapped if str(k) not in options)
            if stray:
                findings.append(Finding(
                    el_id, "dangling_reference",
                    f"{el_id} ({el_type}) {where} maps "
                    f"{', '.join(repr(s) for s in stray)}, which {'is' if len(stray) == 1 else 'are'} "
                    f"not among its options ({_listed(options)}), so nothing the user can pick "
                    f"reaches {'it' if len(stray) == 1 else 'them'}.",
                    key=("dangling_reference", el_id, where, "value_map"),
                ))
        # The engine executes each mapped entry as an action of its own, so the
        # same checks apply one level down. This is where the AI's unreachable
        # device command was hiding.
        for value, inner in mapped.items():
            nested = _mapping(inner)
            if nested is None:
                continue
            findings.extend(_action_findings(
                nested, el_id, el_type, f"{where}['{value}']",
                page_ids, device_ids, macro_ids, device_commands, None,
            ))

    return findings


def _listed(names: Iterable[str]) -> str:
    """The valid set, named. An empty one says so rather than trailing off."""
    ordered = sorted(names)
    return ", ".join(ordered) if ordered else "none"
