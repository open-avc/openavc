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
point, the Builder already runs these checks -- ``validateProject`` (in
``validateProject.ts``) has resolved page ids, device ids and macro ids for a
while, including recursion into ``value_map``. So this is not a mirror waiting
to be written; it is the AI door catching up to the surface a human already had.
Keeping it out of ``page_review`` keeps that straight: what lives there is
mirrored byte for byte in ``reviewPage`` and pinned by
``tests/test_ui_review_parity.py``; what lives here is answered by
``validateProject`` on the other side, in the Validate panel, in its own words.

One of these the Builder cannot do at all: nothing there compares a
``value_map`` against the select's own options. A second one it could not
either -- it has no driver command list in ``validateProject``, so a command
name is unchecked there -- and the half of that which matters most, whether a
named command has the parameters its driver requires, it now ASKS for
(``POST /api/ui/validate-actions``) rather than answering from a copy of the
rule it would have to keep in step.

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
from dataclasses import dataclass, field
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


@dataclass(frozen=True)
class _Project:
    """What the project holds, and what its drivers, plugins and ``ui/`` folder say.

    ``reference_findings`` builds one per call from its keyword arguments and
    every check below reads it, so a new question for the platform is a field
    here and a keyword there, not an argument passed down every function in
    the file. Each lookup answers None for "no opinion", which never warns;
    what an EMPTY answer means is each check's own call (an empty command list
    warns, an empty roster does not).
    """

    page_ids: set[str]
    device_ids: set[str]
    macro_ids: set[str]
    device_commands: Callable[[str], set[str] | None] | None = None
    plugin_elements: Callable[[str], set[str] | None] | None = None
    undeclared_property: Callable[[str], set[str] | None] | None = None
    unknown_child_id: Callable[[str], set[str] | None] | None = None
    missing_params: Callable[[Mapping[str, Any]], list[str] | None] | None = None
    file_command: Callable[[Mapping[str, Any]], str | None] | None = None
    ui_files: set[str] | None = None


@dataclass(frozen=True)
class _Holder:
    """What holds a reference -- a control, or the page itself -- and how a finding about it reads."""

    id: str
    what: str
    """The word in brackets after the id: the control's type, or ``page``."""

    def finding(self, kind: str, said: str, *key: str) -> Finding:
        """``<id> (<what>) <said>``, keyed by the kind, this id, then ``key``."""
        return Finding(self.id, kind, f"{self.id} ({self.what}) {said}", key=(kind, self.id, *key))


@dataclass(frozen=True)
class _Control(_Holder):
    """One element of the page being checked, as the page walk found it."""

    dump: Mapping[str, Any] = field(default_factory=dict)
    page_id: str = "?"


@dataclass(frozen=True)
class _Site:
    """Where one action sits: its control, its path, and the options a value_map answers to."""

    control: _Control
    where: str
    options: set[str] | None

    def finding(self, kind: str, said: str, *key: str) -> Finding:
        """A finding about the action here: its path is in the sentence and the key."""
        return self.control.finding(kind, f"{self.where} {said}", self.where, *key)

    def entry(self, value: Any) -> _Site:
        """A value_map entry, run as an action of its own.

        It answers to no options: the select's options choose WHICH entry runs,
        and the entry itself is not picked from them.
        """
        return _Site(self.control, f"{self.where}['{value}']", None)


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


def _shape_finding(do_map: Mapping[str, Any], slot: str, control: _Control) -> Finding | None:
    """An action list written as a single object rather than an array.

    It RUNS -- ``ui_events.py`` wraps a non-list before executing it, and the
    dict form is handled explicitly for ``off_action`` and ``hold_action``. So
    this warns and does not reject: refusing a shape the runtime executes would
    be the review inventing a rule the platform does not have.

    What it costs is real but smaller than it looks: an object holds exactly one
    action, so a second can never be added without rewriting it, and the Builder
    stores the array form on the next edit either way.
    """
    if not isinstance(do_map.get(slot), Mapping):
        return None
    return control.finding(
        "object_action_list",
        f"writes do.{slot} as an object, not an array. The panel does run "
        f"it -- a single action is wrapped before it executes -- but an object holds exactly "
        f"one, so a second cannot be added without rewriting it, and the Builder stores the "
        f"array form on the next edit anyway. Write do.{slot} as [{{...}}].",
        slot,
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


def _page_grant_findings(page: Any, device_ids: set[str]) -> list[Finding]:
    """A custom page granted a device the project no longer has."""
    grant = _mapping(getattr(page, "grant", None))
    if not grant:
        return []
    holder = _Holder(str(getattr(page, "id", "?")), "page")
    findings: list[Finding] = []
    for granted in grant.get("devices") or []:
        if isinstance(granted, str) and granted and granted not in device_ids:
            findings.append(holder.finding(
                "dangling_reference",
                f"is granted device '{granted}', which is not in this "
                f"project, so the page it shows can neither read nor control it. The "
                f"devices are: {_listed(device_ids)}.",
                f"grant.devices.{granted}",
            ))
    return findings


@dataclass(frozen=True)
class CustomFileUse:
    """One place a page names a file in the project's ``ui/`` folder."""

    what: str
    """How it reads mid-sentence: ``page`` or ``custom control``."""

    holder_id: str
    file: str
    granted: tuple[str, ...]
    """The device and variable ids that holder's grant lets the markup reach."""


def _granted_ids(grant: Any) -> tuple[str, ...]:
    dump = _mapping(grant) or {}
    ids: list[str] = []
    for part in ("devices", "variables"):
        value = dump.get(part)
        if isinstance(value, (list, tuple)):
            ids.extend(str(v) for v in value if isinstance(v, str))
    return tuple(ids)


def custom_file_references(page: Any) -> list[CustomFileUse]:
    """Every place this page names a file in the project's ``ui/`` folder.

    The page itself when it has handed the screen over to markup, then each
    ``custom`` element on it, in the order they are written. One walk with three
    callers -- the missing-file warning below, the tool that lists the folder
    with what uses each file, and the tool that refuses to delete a file
    something still points at. A second implementation of "where can a
    custom_file appear" is how one of them would end up disagreeing with the
    panel about whether a control is still in use.

    Each use carries its grant, because the review of the markup itself asks a
    question only the pair can answer: whether the control's own source ever
    names what it was given.
    """
    found: list[CustomFileUse] = []
    if str(getattr(page, "render_mode", "") or "") == "custom":
        named = getattr(page, "custom_file", None)
        if isinstance(named, str) and named:
            found.append(CustomFileUse(
                "page", str(getattr(page, "id", "?")), named,
                _granted_ids(getattr(page, "grant", None)),
            ))
    for element in getattr(page, "elements", []) or []:
        dump = _mapping(element)
        if dump is None or dump.get("type") != "custom":
            continue
        named = dump.get("custom_file")
        if isinstance(named, str) and named:
            found.append(CustomFileUse(
                "custom control", str(dump.get("id", "?")), named,
                _granted_ids(dump.get("grant")),
            ))
    return found


def custom_file_users(project: Any, file_path: str) -> list[str]:
    """Who still shows ``file_path``, named the way a refusal reads.

    THE answer every door that deletes out of ``ui/`` asks before it unlinks
    anything: the AI's ``delete_ui_file`` and the REST door the IDE's Code view
    deletes through. Both used to be able to disagree about whether a control was
    still in use, and one of them did -- the REST door asked nothing at all and
    deleted a file an element was still drawing, which the panel then reports
    from the wall rather than at delete time.

    ``file_path`` may name a control's whole folder, so a use inside it counts:
    deleting ``room_map/`` takes ``room_map/index.html`` with it.

    Each phrase reads mid-sentence -- "page 'lights'", "element 'roomMap' on
    page 'main'" -- and the list is sorted so a refusal is stable to read.
    Empty means nothing points at it and the delete may go ahead.
    """
    wanted = str(file_path).replace("\\", "/").strip("/")
    who: set[str] = set()
    for page in getattr(getattr(project, "ui", None), "pages", None) or []:
        for use in custom_file_references(page):
            named = use.file.replace("\\", "/").strip("/")
            if named != wanted and not named.startswith(wanted + "/"):
                continue
            who.add(
                f"page '{use.holder_id}'" if use.what == "page"
                else f"element '{use.holder_id}' on page '{getattr(page, 'id', '?')}'"
            )
    return sorted(who)


def _custom_file_finding(
    what: str, holder_id: str, page_id: str, named: Any, ui_files: set[str] | None,
) -> Finding | None:
    """A custom control or page pointed at a file that is not in ``ui/``.

    ``ui_files`` is None for "no opinion" and must never warn -- the same rule
    every injected lookup here follows. A renamed file is the whole reason this
    exists: the element keeps drawing, the box comes up empty, and the only
    other thing that says so is the Builder's picker showing ``(missing)`` to
    somebody who is not looking at it.
    """
    if ui_files is None or not isinstance(named, str) or not named:
        return None
    wanted = named.replace("\\", "/").strip("/")
    if wanted in ui_files:
        return None
    listed = _listed(sorted(ui_files)) if ui_files else "none yet"
    return Finding(
        page_id, "dangling_reference",
        f"{holder_id} ({what}) shows '{named}', which is not in the project's ui/ "
        f"folder, so it draws an empty box. The files there are: {listed}.",
        key=("dangling_reference", holder_id, f"custom_file.{named}"),
    )


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
    missing_params: Callable[[Mapping[str, Any]], list[str] | None] | None = None,
    ui_files: set[str] | None = None,
    file_command: Callable[[Mapping[str, Any]], str | None] | None = None,
) -> list[Finding]:
    """Every binding on this page that names something that is not there.

    ``touched`` scopes it the way ``review_page`` is scoped: a write answers for
    what it wrote. Re-reporting the other fifty elements on every call is how a
    caller learns to skip the field.

    ``ui_files`` is every path in the project's ``ui/`` folder, or None for the
    same "no opinion" -- a caller that cannot enumerate it must not turn every
    custom control into a warning.

    ``device_commands`` returns a driver's declared command names, or None for
    "no opinion" -- a device with no driver loaded, one that is disabled, a
    driver that enumerates nothing. None must never become a warning: the
    commonest reason to have it is that the device is simply not connected yet.

    ``missing_params`` answers the question after it: the command exists and is
    spelled right, and the driver still refuses it because a parameter it
    declares required was never filled in. Same "None is no opinion" contract,
    and for the same reason. ``file_command`` answers the case no press can
    run: a command that takes or hands back a file, which runs only from the
    device page. See ``core/command_params``.
    """
    project = _Project(
        page_ids=page_ids, device_ids=device_ids, macro_ids=macro_ids,
        device_commands=device_commands, plugin_elements=plugin_elements,
        undeclared_property=undeclared_property, unknown_child_id=unknown_child_id,
        missing_params=missing_params, file_command=file_command, ui_files=ui_files,
    )
    findings: list[Finding] = []
    in_scope = (lambda el_id: True) if touched is None else (lambda el_id: el_id in touched)
    page_id = str(getattr(page, "id", "?"))

    # A custom page carries the same grant an iframe element does, so it can name
    # a device that has since left the project in exactly the same way -- and it
    # is exactly as invisible, because a grant matching nothing looks like a
    # grant nobody set. Not scoped by `touched`: the page is not an element, and
    # a write that never mentions it is still a write onto a page whose author
    # page can no longer reach what it was given.
    findings.extend(_page_grant_findings(page, device_ids))

    # A page that hands the screen to markup names its file the same way an
    # element does, and is missing it the same way. Not scoped by `touched`,
    # like the page grant beside it: the page is not one of its elements.
    # One walk answers both sites below, so what counts as naming a file lives
    # in one place -- see custom_file_references.
    named_files = custom_file_references(page)
    page_file = next((use.file for use in named_files if use.what == "page"), None)
    element_files = {
        use.holder_id: use.file for use in named_files if use.what != "page"
    }

    if page_file is not None:
        missing = _custom_file_finding("page", page_id, page_id, page_file, ui_files)
        if missing:
            findings.append(missing)

    for element in getattr(page, "elements", []) or []:
        dump = _mapping(element)
        if dump is None:
            continue
        el_id = str(dump.get("id", "?"))
        if not in_scope(el_id):
            continue
        control = _Control(el_id, str(dump.get("type", "?")), dump, page_id)
        plugin_issue = plugin_element_finding(dump, el_id, plugin_elements)
        if plugin_issue:
            findings.append(plugin_issue)
        if el_id in element_files:
            missing_file = _custom_file_finding(
                "custom control", el_id, page_id, element_files[el_id], ui_files,
            )
            if missing_file:
                findings.append(missing_file)
        findings.extend(_element_findings(control, project))
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


def _matrix_pattern_findings(control: _Control, project: _Project) -> list[Finding]:
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
    if control.what != "matrix":
        return []
    config = _mapping(control.dump.get("matrix_config"))
    if not config:
        return []

    findings: list[Finding] = []
    for name in MATRIX_KEY_PATTERNS:
        pattern = config.get(name)
        if not isinstance(pattern, str) or not pattern.startswith("device."):
            continue
        if _device_of(pattern, project.device_ids) is None:
            named = pattern.split(".")[1] if pattern.count(".") >= 1 else pattern
            findings.append(control.finding(
                "dangling_reference",
                f"reads matrix_config.{name} from '{pattern}', and no "
                f"device '{named}' is in this project. The devices are: "
                f"{_listed(project.device_ids)}.",
                f"matrix_config.{name}",
            ))
            continue
        if project.undeclared_property is None or "*" not in pattern:
            continue
        declared = project.undeclared_property(pattern.replace("*", _PROBE_INDEX, 1))
        if declared:
            findings.append(control.finding(
                "dangling_reference",
                f"reads matrix_config.{name} from '{pattern}', and its "
                f"driver does not declare that. It declares: {_listed(declared)}.",
                f"matrix_config.{name}.property",
            ))
    return findings


def _element_findings(control: _Control, project: _Project) -> list[Finding]:
    findings: list[Finding] = []
    dump = control.dump

    target = dump.get("target_page")
    if (
        control.what == "page_nav" and isinstance(target, str) and target
        and target not in project.page_ids and target not in NAVIGATION_SENTINELS
    ):
        findings.append(control.finding(
            "dangling_reference",
            f"navigates to page '{target}', which does not exist. "
            f"The pages are: {_listed(project.page_ids)}.",
            "target_page",
        ))

    # A grant names devices by id, and a control whose grant names a device that
    # is not here sees nothing from it and cannot command it -- with no error
    # anywhere, because a grant that matches nothing is indistinguishable at
    # runtime from a grant nobody set. Variables are deliberately not checked,
    # for the same reason bound var. keys are not: a script can create one.
    grant = _mapping(dump.get("grant"))
    for granted in (grant.get("devices") or []) if grant else []:
        if isinstance(granted, str) and granted and granted not in project.device_ids:
            findings.append(control.finding(
                "dangling_reference",
                f"is granted device '{granted}', which is not in this "
                f"project, so it can neither read nor control it. The devices are: "
                f"{_listed(project.device_ids)}.",
                f"grant.devices.{granted}",
            ))

    findings.extend(_matrix_pattern_findings(control, project))

    bindings = _mapping(dump.get("bindings")) or {}
    show = _mapping(bindings.get("show")) or {}
    for slot in ("value", "look"):
        binding = _mapping(show.get(slot))
        key = binding.get("key") if binding else None
        if not isinstance(key, str) or not key.startswith("device."):
            continue
        if _device_of(key, project.device_ids) is None:
            named = key.split(".")[1] if key.count(".") >= 1 else key
            findings.append(control.finding(
                "dangling_reference",
                f"reads show.{slot} from '{key}', and no device "
                f"'{named}' is in this project. The devices are: "
                f"{_listed(project.device_ids)}.",
                f"show.{slot}",
            ))
            continue
        # The child id before the property, because a page repeating one block
        # per output gets the NUMBER wrong, not the name -- and reported first
        # it names the actual mistake instead of a property that is spelled
        # right on a child that does not exist. Reported instead of, not as well
        # as, the property complaint: one binding, one thing to fix.
        if project.unknown_child_id is not None:
            roster = project.unknown_child_id(key)
            if roster:
                findings.append(control.finding(
                    "dangling_reference",
                    f"reads show.{slot} from '{key}', and its driver "
                    f"declares no such child. It declares: {_listed(roster)}.",
                    f"show.{slot}.child",
                ))
                continue
        # The device resolved; the property after it is the half that used to
        # pass silently while a typo'd device, command, macro and page were all
        # caught. A typo'd property is at least as common as a typo'd device.
        if project.undeclared_property is None:
            continue
        declared = project.undeclared_property(key)
        if declared:
            findings.append(control.finding(
                "dangling_reference",
                f"reads show.{slot} from '{key}', and its driver does "
                f"not declare that. It declares: {_listed(declared)}.",
                f"show.{slot}.property",
            ))

    do_map = _mapping(bindings.get("do")) or {}
    options = _option_values(dump)
    for slot in ACTION_SLOTS:
        shape = _shape_finding(do_map, slot, control)
        if shape:
            findings.append(shape)
        for index, action in enumerate(_actions(do_map, slot)):
            findings.extend(_action_findings(
                action, _Site(control, f"do.{slot}[{index}]", options), project,
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
    action: Mapping[str, Any], site: _Site, project: _Project,
) -> list[Finding]:
    """What one action names that is not there, asked of the check for its kind.

    Only a string is looked up. A nested action object written where the name
    goes (``{"action": {"action": ...}}``) is a shape that does arrive, and it
    cannot be a key in a table: asking would raise inside the one function whose
    job is turning bad input into a sentence.
    """
    name = action.get("action")
    check = _ACTION_CHECKS.get(name) if isinstance(name, str) else None
    return check(action, site, project) if check else []


def _navigate_findings(
    action: Mapping[str, Any], site: _Site, project: _Project,
) -> list[Finding]:
    target = action.get("page")
    if not (
        isinstance(target, str) and target
        and target not in project.page_ids and target not in NAVIGATION_SENTINELS
    ):
        return []
    return [site.finding(
        "dangling_reference",
        f"navigates to page '{target}', which does not "
        f"exist. The pages are: {_listed(project.page_ids)}.",
        "page",
    )]


def _macro_findings(
    action: Mapping[str, Any], site: _Site, project: _Project,
) -> list[Finding]:
    macro = action.get("macro")
    if not isinstance(macro, str) or not macro or macro in project.macro_ids:
        return []
    return [site.finding(
        "dangling_reference",
        f"runs macro '{macro}', which does not exist. "
        f"The macros are: {_listed(project.macro_ids)}.",
        "macro",
    )]


def _command_findings(
    action: Mapping[str, Any], site: _Site, project: _Project,
) -> list[Finding]:
    """A command for a device that is not here, one its driver does not have, or one it refuses.

    Each question is asked only once the one before it passed: a missing device
    is the whole story, and a command the driver does not have cannot be asked
    about its parameters.
    """
    device = action.get("device")
    command = action.get("command")
    if isinstance(device, str) and device and device not in project.device_ids:
        return [site.finding(
            "dangling_reference",
            f"commands device '{device}', which is not in "
            f"this project. The devices are: {_listed(project.device_ids)}.",
            "device",
        )]
    if not isinstance(device, str) or not isinstance(command, str):
        return []
    declared = project.device_commands(device) if project.device_commands else None
    if declared is not None and command not in declared:
        return [site.finding(
            "dangling_reference",
            f"sends '{command}' to '{device}', which its "
            f"driver does not have. Its commands are: {_listed(declared)}.",
            "command",
        )]
    refusal = _refusal(action, site, project)
    return [refusal] if refusal else []


def _refusal(action: Mapping[str, Any], site: _Site, project: _Project) -> Finding | None:
    """A command the driver has, which every press is still refused.

    Nothing else on the page says so, and the control draws and takes a press
    exactly like a working one. No press can run a command that takes or hands
    back a file, so that one is not asked about its parameters as well.
    """
    device, command = action.get("device"), action.get("command")
    refused = project.file_command(action) if project.file_command else None
    if refused:
        return site.finding(
            "unrunnable_command",
            f"sends '{command}' to '{device}', "
            f"and every press is refused with \"{refused}\"",
        )
    unsupplied = project.missing_params(action) if project.missing_params else None
    if unsupplied:
        return _unrunnable_finding(site, device, command, unsupplied)
    return None


def _value_map_findings(
    action: Mapping[str, Any], site: _Site, project: _Project,
) -> list[Finding]:
    mapped = _mapping(action.get("map")) or {}
    findings = _stray_findings(mapped, site)
    # The engine executes each mapped entry as an action of its own, so the
    # same checks apply one level down. This is where the AI's unreachable
    # device command was hiding.
    for value, inner in mapped.items():
        nested = _mapping(inner)
        if nested is not None:
            findings.extend(_action_findings(nested, site.entry(value), project))
    return findings


def _stray_findings(mapped: Mapping[Any, Any], site: _Site) -> list[Finding]:
    """The map's keys that none of the select's own options can produce."""
    if site.options is None:
        return []
    stray = sorted(str(k) for k in mapped if str(k) not in site.options)
    if not stray:
        return []
    one = len(stray) == 1
    return [site.finding(
        "dangling_reference",
        f"maps "
        f"{', '.join(repr(s) for s in stray)}, which {'is' if one else 'are'} "
        f"not among its options ({_listed(site.options)}), so nothing the user can pick "
        f"reaches {'it' if one else 'them'}.",
        "value_map",
    )]


#: The check for each action that names something, by the action's name. An
#: action not listed here names nothing this module can look up.
_ACTION_CHECKS: dict[
    str, Callable[[Mapping[str, Any], _Site, _Project], list[Finding]],
] = {
    "ui.navigate": _navigate_findings,
    "macro": _macro_findings,
    "device.command": _command_findings,
    "value_map": _value_map_findings,
}


def _unrunnable_finding(
    site: _Site, device: str, command: str, unsupplied: list[str],
) -> Finding:
    """A command the driver has, that the driver will still refuse.

    One finding for the action rather than one per parameter: they are filled in
    on the same form, in the same visit, and two sentences about one card would
    read as two mistakes. The refusal is quoted verbatim because it is what
    somebody sees on the panel when they finally press the control, and matching
    the two makes the connection without explaining it.
    """
    named = " and ".join(f"'{name}'" for name in unsupplied)
    them = "it" if len(unsupplied) == 1 else "them"
    return site.finding(
        "unrunnable_command",
        f"sends '{command}' to '{device}' without {named}. "
        f"Its driver requires {them}, so the device refuses every press with "
        f"\"'{command}': '{unsupplied[0]}' is required\".",
    )


def _listed(names: Iterable[str]) -> str:
    """The valid set, named. An empty one says so rather than trailing off."""
    ordered = sorted(names)
    return ", ".join(ordered) if ordered else "none"
