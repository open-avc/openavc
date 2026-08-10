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
``var.`` keys. A script or a macro can create one at any time, so an undeclared
variable is not evidence of a mistake.

A device key's PROPERTY was on this list too, on the reasoning that child-entity
keys appear at runtime. That was too broad, and an adversarial pass said so: a
typo'd property is at least as common as a typo'd device, and the device was
caught while the property was not. It is checked now, but only where the driver
declares a set to check against -- and never for the platform's own device
properties (``online``, ``offline_reason``, ...), which no DRIVER_INFO contains
and which are the commonest bindings on any panel.

A child ID is checked the same narrow way, and for the same reason the property
is: a page that repeats one block per output gets the NUMBER wrong far more
often than the name, and ``output.7`` on a four-output frame draws perfectly
while binding to nothing. Only against a roster the driver DECLARES -- a fixed
count or a literal list, or a config field this device has actually filled in.
Never against the live roster, because a page is usually authored before the
device is connected and an unconnected device has no children at all; and never
when ``count_from_state`` is declared, because the device resizes the roster
once it answers.

Everything here WARNS. The write lands and the findings ride back with it, for
the same reason as the rest of the review: a rejection costs a whole round trip,
a warning costs nothing.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from openavc.ui.page_review import Finding

#: Navigation targets that are not page ids and never will be.
#:
#: The panel resolves both itself (``navigateToPage``): ``$back`` dismisses an
#: open overlay or pops the page history, ``$dismiss`` closes an overlay and
#: nothing else. ``macro_engine`` has whitelisted them since they shipped; this
#: module did not, so the one validator an authoring client actually reads
#: reported the documented spelling as a dangling page. What that costs is not
#: the warning -- it is that believing it means hardcoding a page id into a
#: confirm dialog's Cancel button, which makes the dialog single-use.
NAVIGATION_SENTINELS = frozenset({"$back", "$dismiss"})

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


#: What the panel accepts in a plugin element's two ids, character for
#: character. ``panel.js:3785`` tests both against this and draws its
#: unconfigured placeholder if either fails, so anything else is decorative.
_PLUGIN_ID_CHARS = re.compile(r"^[A-Za-z0-9_-]+$")


def plugin_element_finding(
    dump: Mapping[str, Any],
    el_id: str,
    plugin_elements: Callable[[str], set[str] | None] | None,
) -> Finding | None:
    """A ``plugin`` element that will draw the unconfigured placeholder.

    ``plugin`` is a real type and passes the type check, so nothing here used to
    look inside it. But the renderer needs BOTH ``plugin_id`` and
    ``plugin_type``, both matching ``[A-Za-z0-9_-]+``, and it builds
    ``/api/plugins/<id>/panel/<type>.html`` out of them. Miss either, or invent a
    type the plugin does not declare, and the panel draws a dashed grey box that
    says "Plugin" -- which reads as a loading state, not as a mistake.

    This is the one type the authoring guide singles out as easy to get wrong,
    which is exactly why leaving it unchecked was the wrong call.
    """
    if str(dump.get("type", "")) != "plugin":
        return None
    plugin_id = dump.get("plugin_id")
    plugin_type = dump.get("plugin_type")

    missing = [
        name for name, value in (("plugin_id", plugin_id), ("plugin_type", plugin_type))
        if not isinstance(value, str) or not value.strip()
    ]
    if missing:
        return Finding(
            el_id, "plugin_element_unconfigured",
            f"{el_id} (plugin) has no {' and no '.join(missing)}, so the panel draws its "
            f"unconfigured placeholder instead of anything. A plugin element needs both, "
            f"read off the installed plugin.",
            key=("plugin_element_unconfigured", el_id, "missing"),
        )

    malformed = [
        f"{name} '{value}'"
        for name, value in (("plugin_id", plugin_id), ("plugin_type", plugin_type))
        if not _PLUGIN_ID_CHARS.match(str(value))
    ]
    if malformed:
        return Finding(
            el_id, "plugin_element_unconfigured",
            f"{el_id} (plugin): {' and '.join(malformed)} is not a name the panel accepts "
            f"-- letters, digits, underscore and hyphen only -- so it draws its unconfigured "
            f"placeholder.",
            key=("plugin_element_unconfigured", el_id, "malformed"),
        )

    if plugin_elements is None:
        return None
    declared = plugin_elements(str(plugin_id))
    if declared is None:
        # The plugin is not loaded here. That is a deployment fact, not an
        # authoring mistake -- it may simply be stopped or not installed yet.
        return None
    if str(plugin_type) not in declared:
        return Finding(
            el_id, "plugin_element_unconfigured",
            f"{el_id} (plugin) asks '{plugin_id}' for a '{plugin_type}' element, which it "
            f"does not declare, so the panel loads a renderer that is not there. "
            f"{plugin_id} declares: {_listed(declared)}.",
            key=("plugin_element_unconfigured", el_id, "undeclared"),
        )
    return None


def reference_findings(
    page: Any,
    *,
    touched: set[str] | None = None,
    page_ids: set[str],
    device_ids: set[str],
    macro_ids: set[str],
    device_commands: Callable[[str], set[str] | None] | None = None,
    plugin_elements: Callable[[str], set[str] | None] | None = None,
    undeclared_property: Callable[[str], set[str] | None] | None = None,
    unknown_child_id: Callable[[str], set[str] | None] | None = None,
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
        plugin_issue = plugin_element_finding(dump, el_id, plugin_elements)
        if plugin_issue:
            findings.append(plugin_issue)
        findings.extend(_element_findings(
            dump, el_id, page_ids, device_ids, macro_ids, device_commands,
            undeclared_property, unknown_child_id,
        ))
    return findings


#: The `matrix_config` keys that hold a state-key GLOB rather than a plain key.
#:
#: Each is a state key with the input or output number replaced by `*`, which
#: the panel substitutes 1..count over. They are the only bindings on a panel
#: that point at state without living under `bindings.show`, which is exactly
#: why nothing checked them.
MATRIX_KEY_PATTERNS = (
    "route_key_pattern",
    "audio_route_key_pattern",
    "input_key_pattern",
    "output_key_pattern",
)

#: What `*` is replaced with to make a pattern into a checkable key. Every
#: matrix is 1-based and has at least one of each, so index 1 always exists if
#: any does -- and a child type whose roster is dynamic answers "no opinion"
#: rather than guessing, so this cannot invent a complaint.
_PROBE_INDEX = "1"


def _matrix_pattern_findings(
    dump: Mapping[str, Any],
    el_id: str,
    el_type: str,
    device_ids: set[str],
    undeclared_property: Callable[[str], set[str] | None] | None,
) -> list[Finding]:
    """State-key globs inside `matrix_config`, checked like any other binding.

    A matrix is the one control that reads state from somewhere other than
    `bindings.show`, so the property check that catches a typo'd key everywhere
    else has never seen these. The gap is not theoretical: a build authored
    `input_key_pattern: device.<id>.input.*.label` against a driver that
    declares no `label` on its input children. Nothing failed -- the renderer
    only writes a header when the key resolves, so the columns silently keep
    their default captions and the author believes the labels are live.

    Checked by substituting `*` for index 1 and asking the same question
    `show.value` asks. A pattern with no `*` is left alone: it is a plain key,
    it will be read for every row, and saying "this is not a glob" is a
    different complaint than this function makes.
    """
    if el_type != "matrix":
        return []
    config = _mapping(dump.get("matrix_config"))
    if not config:
        return []

    findings: list[Finding] = []
    for name in MATRIX_KEY_PATTERNS:
        pattern = config.get(name)
        if not isinstance(pattern, str) or not pattern.startswith("device."):
            continue
        if _device_of(pattern, device_ids) is None:
            named = pattern.split(".")[1] if pattern.count(".") >= 1 else pattern
            findings.append(Finding(
                el_id, "dangling_reference",
                f"{el_id} ({el_type}) reads matrix_config.{name} from '{pattern}', and no "
                f"device '{named}' is in this project. The devices are: {_listed(device_ids)}.",
                key=("dangling_reference", el_id, f"matrix_config.{name}"),
            ))
            continue
        if undeclared_property is None or "*" not in pattern:
            continue
        declared = undeclared_property(pattern.replace("*", _PROBE_INDEX, 1))
        if declared:
            findings.append(Finding(
                el_id, "dangling_reference",
                f"{el_id} ({el_type}) reads matrix_config.{name} from '{pattern}', and its "
                f"driver does not declare that. It declares: {_listed(declared)}.",
                key=("dangling_reference", el_id, f"matrix_config.{name}.property"),
            ))
    return findings


def _element_findings(
    dump: Mapping[str, Any],
    el_id: str,
    page_ids: set[str],
    device_ids: set[str],
    macro_ids: set[str],
    device_commands: Callable[[str], set[str] | None] | None,
    undeclared_property: Callable[[str], set[str] | None] | None = None,
    unknown_child_id: Callable[[str], set[str] | None] | None = None,
) -> list[Finding]:
    findings: list[Finding] = []
    el_type = str(dump.get("type", "?"))

    target = dump.get("target_page")
    if (
        el_type == "page_nav" and isinstance(target, str) and target
        and target not in page_ids and target not in NAVIGATION_SENTINELS
    ):
        findings.append(Finding(
            el_id, "dangling_reference",
            f"{el_id} ({el_type}) navigates to page '{target}', which does not exist. "
            f"The pages are: {_listed(page_ids)}.",
            key=("dangling_reference", el_id, "target_page"),
        ))

    findings.extend(_matrix_pattern_findings(
        dump, el_id, el_type, device_ids, undeclared_property,
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
            continue
        # The child id before the property, because a page repeating one block
        # per output gets the NUMBER wrong, not the name -- and reported first
        # it names the actual mistake instead of a property that is spelled
        # right on a child that does not exist. Reported instead of, not as well
        # as, the property complaint: one binding, one thing to fix.
        if unknown_child_id is not None:
            roster = unknown_child_id(key)
            if roster:
                findings.append(Finding(
                    el_id, "dangling_reference",
                    f"{el_id} ({el_type}) reads show.{slot} from '{key}', and its driver "
                    f"declares no such child. It declares: {_listed(roster)}.",
                    key=("dangling_reference", el_id, f"show.{slot}.child"),
                ))
                continue
        # The device resolved; the property after it is the half that used to
        # pass silently while a typo'd device, command, macro and page were all
        # caught. A typo'd property is at least as common as a typo'd device.
        if undeclared_property is None:
            continue
        declared = undeclared_property(key)
        if declared:
            findings.append(Finding(
                el_id, "dangling_reference",
                f"{el_id} ({el_type}) reads show.{slot} from '{key}', and its driver does "
                f"not declare that. It declares: {_listed(declared)}.",
                key=("dangling_reference", el_id, f"show.{slot}.property"),
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
        if (
            isinstance(target, str) and target
            and target not in page_ids and target not in NAVIGATION_SENTINELS
        ):
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
