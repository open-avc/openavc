"""What a matrix is, once the shorthand is expanded.

A matrix element is two lists: the sources you can pick from, and the
destinations you can send them to. Everything else -- the crosspoint grid, the
list of dropdowns -- is a way of drawing those two lists.

Before project format 0.10.0 the lists did not exist. A matrix was two counts,
two label arrays and four glob patterns, and the renderer expanded them by
substituting a 1-based index into a ``*``. That shape can only express one
thing: a rectangular frame whose ports are numbered 1..N, contiguous, on one
device, reporting plain integers. The driver corpus is not that::

    a Chazy decoder routes six independent planes through one child entity
    a Crestron NVX takes an IP address or an rtsp:// URL as its source
    a Gefen frame's output ids are strings
    an Atlona DSP's destinations are children called `input`
    an 8x8 frame with two ports patched is a six-row list, not an 8x8 grid

So each destination carries **its own** route key rather than sharing a pattern,
and ``value`` is opaque -- whatever the device reports and accepts. Those two
changes are what make subsets, gaps, non-contiguous ids, string ids, and
destinations spread across several devices all sayable, and they need no new
machinery for multiple routing planes either: the plane is part of the state
key, so one element covers one plane and a page that needs two carries two
elements.

The shorthand is not gone, it is a GENERATOR. ``{from: {count: 128, route_key:
"device.mx.output.*.input"}}`` is still the terse way to say "all 128 outputs of
this Extron", and it is what every project written before 0.10.0 migrates to, so
nothing needs re-authoring.

Where this runs
---------------
**Once, on the server** (matrix plan D6). The panel and the Builder canvas -- an
iframe of the same panel -- both receive lists that are already resolved, so the
translation from config to rows-and-columns exists in exactly one renderer.

The one other place that resolves is the page review, which exists twice by
design (``page_review.py`` and ``reviewPage`` in uiBuilderHelpers.ts) and is
pinned message-for-message by ``tests/test_ui_review_parity.py``. Its TypeScript
half therefore mirrors this module, and the same test that catches a review
drifting catches a resolver drifting -- which is not true of a renderer, and is
why the renderer does not get a copy.

Pure stdlib, so ``control_minimums.py`` can ask it how many rows a matrix draws
without either of them growing an import.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

#: Every key the matrix renderer reads out of ``matrix_config``.
#:
#: The page review reads this to name keys nothing reads; ``matrix_config_problems``
#: below reads it to check the ones that ARE read are shaped right.
MATRIX_CONFIG_KEYS = frozenset({
    "sources", "destinations",
    "audio_follow_video", "show_lock", "show_mute", "presets",
})

#: What a generator entry (``{from: ..., exclude: ..., overrides: ...}``) may say.
GENERATOR_KEYS = frozenset({"from", "exclude", "overrides"})

#: What a resolved source entry carries.
SOURCE_KEYS = frozenset({"value", "label", "label_key"})

#: What a resolved destination entry carries. ``route`` is the per-destination
#: action-list override; absent means the element's own ``bindings.do.route``.
DESTINATION_KEYS = frozenset({
    "value", "label", "label_key", "route_key", "audio_route_key", "route",
})

#: How each axis names an entry nobody labelled. These are the captions the
#: pattern-form renderer produced ("In 1", "Out 3"), kept so a migrated project
#: draws the same words it drew before.
_DEFAULT_LABEL_PREFIX = {"sources": "In", "destinations": "Out"}

_NUMERIC = re.compile(r"^[+-]?\d+(?:\.\d+)?$")
_DIGIT_RUN = re.compile(r"\d+")


# --- The comparison every match goes through -------------------------------


def route_value(value: Any) -> float | int | str | None:
    """A routed-source value in comparable form, or None when nothing is routed.

    Mirrors ``_routeValue`` in panel.js, which is the copy that decides what a
    panel actually lights. Everything about which crosspoint is lit used to go
    through ``parseInt``, and ``parseInt('IN1')`` is NaN -- so a switcher that
    labels its ports lit no crosspoint at all and flagged every output as an
    audio mismatch while audio and video were byte-for-byte identical.

    0 counts as unrouted rather than as port zero: every routing driver in the
    corpus numbers its ports from 1, and AV gear conventionally reports 0 for an
    idle port.
    """
    if value is None:
        return None
    # bool before number: JavaScript's `typeof true` is 'boolean', so panel.js
    # takes it down the string branch. Python would call it an int and compare
    # True equal to 1.
    if isinstance(value, int | float) and not isinstance(value, bool):
        return value if math.isfinite(value) and value != 0 else None
    text = str(value).strip()
    if text in ("", "0"):
        return None
    if _NUMERIC.match(text):
        number = float(text)
        return int(number) if number.is_integer() else number
    return text.lower()


def _route_digits(text: str) -> int | None:
    """The single digit run in a string, or None when it does not have exactly one."""
    runs = _DIGIT_RUN.findall(text)
    return int(runs[0]) if len(runs) == 1 else None


def route_matches(a: Any, b: Any) -> bool:
    """Do two routed-source values name the same source?

    Equality first, so identical values can never read as a mismatch. Then a
    single embedded number, so a device reporting 'IN2' or 'HDMI 3' still lights
    its crosspoint -- but only when there is exactly one digit run, so nothing is
    guessed from a source NAME ('Laptop') or from a value carrying two numbers
    ('1080p60').
    """
    x, y = route_value(a), route_value(b)
    if x is None or y is None:
        return False
    if x == y:
        return True
    dx = x if isinstance(x, int | float) else _route_digits(x)
    dy = y if isinstance(y, int | float) else _route_digits(y)
    return dx is not None and dx == dy


# --- Resolution ------------------------------------------------------------


def _substitute(pattern: Any, value: Any) -> str | None:
    """A key pattern with ``*`` replaced by this entry's value.

    First occurrence only, matching JavaScript's ``String.replace`` with a string
    needle -- which is what the pattern form has always done. A pattern with no
    ``*`` is a concrete key every entry shares, and that is allowed: several
    destinations can legitimately watch one key.
    """
    if not isinstance(pattern, str) or not pattern:
        return None
    return pattern.replace("*", str(value), 1)


def _entry_key(value: Any) -> str:
    """How ``exclude`` and ``overrides`` name an entry.

    JSON object keys are strings, so ``overrides: {1: ...}`` arrives as ``"1"``
    however it was typed. Both sides go through str() rather than through
    ``route_value``: this is addressing an entry in a list somebody wrote, not
    deciding whether two devices mean the same port.
    """
    return str(value)


def _normalise_entry(raw: Any, axis: str, position: int) -> dict[str, Any] | None:
    """One explicitly authored entry, filled out.

    A bare scalar is allowed and means "just this value" -- ``sources: [1, 2, 5]``
    is a real thing somebody will write, and rejecting it would be pedantry.
    """
    if isinstance(raw, Mapping):
        entry = dict(raw)
    elif isinstance(raw, str | int | float) and not isinstance(raw, bool):
        entry = {"value": raw}
    else:
        return None
    if "value" not in entry:
        return None
    if not entry.get("label"):
        entry["label"] = f"{_DEFAULT_LABEL_PREFIX[axis]} {position + 1}"
    return entry


def _generate(spec: Mapping[str, Any], axis: str) -> list[dict[str, Any]]:
    """Expand a generator entry into the list it stands for."""
    source = spec.get("from")
    source = source if isinstance(source, Mapping) else {}

    values = source.get("values")
    if not isinstance(values, Sequence) or isinstance(values, str | bytes):
        try:
            count = int(source.get("count") or 0)
        except (TypeError, ValueError):
            count = 0
        values = list(range(1, count + 1)) if count > 0 else []

    labels = source.get("labels")
    labels = labels if isinstance(labels, Sequence) and not isinstance(labels, str) else ()

    excluded = {_entry_key(v) for v in spec.get("exclude") or ()}
    overrides = spec.get("overrides")
    overrides = overrides if isinstance(overrides, Mapping) else {}

    entries: list[dict[str, Any]] = []
    for position, value in enumerate(values):
        if _entry_key(value) in excluded:
            continue
        label = labels[position] if position < len(labels) else None
        entry: dict[str, Any] = {
            "value": value,
            "label": str(label) if label else f"{_DEFAULT_LABEL_PREFIX[axis]} {position + 1}",
        }
        for pattern_key, entry_key in (
            ("label_key", "label_key"),
            ("route_key", "route_key"),
            ("audio_route_key", "audio_route_key"),
        ):
            resolved = _substitute(source.get(pattern_key), value)
            if resolved:
                entry[entry_key] = resolved
        if isinstance(source.get("route"), list):
            entry["route"] = source["route"]

        override = overrides.get(_entry_key(value))
        if isinstance(override, Mapping):
            entry.update(override)
        entries.append(entry)
    return entries


def resolve_axis(config: Mapping[str, Any] | None, axis: str) -> list[dict[str, Any]]:
    """One axis of a matrix, as the list the renderer draws.

    ``axis`` is ``"sources"`` or ``"destinations"``. Three authoring forms land
    here and leave identical: an explicit list, a generator object, and nothing
    at all (which resolves to nothing -- a matrix that says nothing about its
    destinations has none, and the page review says so rather than the renderer
    inventing a 4x4 that can never route).
    """
    if not isinstance(config, Mapping):
        return []
    spec = config.get(axis)
    if isinstance(spec, Mapping):
        return _generate(spec, axis)
    if isinstance(spec, Sequence) and not isinstance(spec, str | bytes):
        entries = []
        for position, raw in enumerate(spec):
            entry = _normalise_entry(raw, axis, position)
            if entry is not None:
                entries.append(entry)
        return entries
    return []


def axis_count(config: Mapping[str, Any] | None, axis: str) -> int:
    """How many entries this axis draws.

    The question ``control_minimums`` asks, and the only one it asks -- a floor
    is a function of the counts, not of what anything is called.
    """
    return len(resolve_axis(config, axis))


def resolve_matrix_config(config: Mapping[str, Any] | None) -> dict[str, Any]:
    """``matrix_config`` with both axes expanded into lists.

    Every other key is passed through untouched: presets, the lock and mute
    flags and audio-follow-video are read straight by the renderer and have no
    shorthand to expand.
    """
    resolved = dict(config) if isinstance(config, Mapping) else {}
    resolved["sources"] = resolve_axis(config, "sources")
    resolved["destinations"] = resolve_axis(config, "destinations")
    return resolved


# --- The schema -------------------------------------------------------------
#
# What a matrix_config MAY say, as opposed to what happens to survive being
# stored. Those were the same thing until now: matrix_config is dict[str, Any]
# at every layer, so an invented shape round-tripped through save, reload and
# export perfectly and drew nothing. Every rule below names a real silence --
# a list of eight destinations that resolves to six, a generator whose count
# sits one level too high, a key on the wrong axis -- and the sentence it
# produces is what somebody reads instead of staring at an empty box.


#: The keys a generator's ``from`` may carry, per axis. Read straight off what
#: ``_generate`` above actually substitutes: a source has no route, so
#: ``route_key`` on the sources axis is a line that does nothing, which is
#: exactly the mistake worth catching.
_GENERATOR_FROM_KEYS = {
    "sources": frozenset({"count", "values", "labels", "label_key"}),
    "destinations": frozenset({
        "count", "values", "labels", "label_key",
        "route_key", "audio_route_key", "route",
    }),
}

#: The keys one written-out entry may carry, per axis.
_ENTRY_KEYS = {"sources": SOURCE_KEYS, "destinations": DESTINATION_KEYS}

#: What a preset button is: a caption and the macro it runs (``panel.js``
#: reads exactly these two).
_PRESET_KEYS = frozenset({"name", "macro"})

#: The matrix_config keys that are simply on or off.
_FLAG_KEYS = ("audio_follow_video", "show_lock", "show_mute")


def _type_name(value: Any) -> str:
    """What a wrong value IS, in words an author recognises."""
    if isinstance(value, bool):
        return "true/false"
    if isinstance(value, Mapping):
        return "an object"
    if isinstance(value, str):
        return "text"
    if isinstance(value, int | float):
        return "a number"
    if isinstance(value, Sequence):
        return "a list"
    return "nothing" if value is None else type(value).__name__


def _unknown(keys: Any, allowed: frozenset[str]) -> list[str]:
    return sorted(str(k) for k in keys if str(k) not in allowed)


def _generator_problems(spec: Mapping[str, Any], axis: str, where: str) -> list[str]:
    """One axis written as a generator."""
    problems: list[str] = []
    stray = _unknown(spec, GENERATOR_KEYS)
    if stray:
        problems.append(
            f"{where}.{axis} is a generator, so it may only say "
            f"{', '.join(sorted(GENERATOR_KEYS))} -- not {', '.join(repr(s) for s in stray)}. "
            f"A count goes inside 'from'.",
        )
    source = spec.get("from")
    if "from" in spec and not isinstance(source, Mapping):
        problems.append(
            f"{where}.{axis}.from must be an object saying what to generate, "
            f"not {_type_name(source)}.",
        )
    elif isinstance(source, Mapping):
        stray = _unknown(source, _GENERATOR_FROM_KEYS[axis])
        if stray:
            problems.append(
                f"{where}.{axis}.from sets {', '.join(repr(s) for s in stray)}, which "
                f"nothing expands. It may say "
                f"{', '.join(sorted(_GENERATOR_FROM_KEYS[axis]))}.",
            )
        if "count" in source and not isinstance(source["count"], int | str):
            problems.append(
                f"{where}.{axis}.from.count must be a whole number, "
                f"not {_type_name(source['count'])}.",
            )
        for key in ("values", "labels", "route"):
            value = source.get(key)
            if key in source and (
                not isinstance(value, Sequence) or isinstance(value, str | bytes)
            ):
                problems.append(
                    f"{where}.{axis}.from.{key} must be a list, not {_type_name(value)}.",
                )
    exclude = spec.get("exclude")
    if "exclude" in spec and (
        not isinstance(exclude, Sequence) or isinstance(exclude, str | bytes)
    ):
        problems.append(
            f"{where}.{axis}.exclude must be a list of the values to leave out, "
            f"not {_type_name(exclude)}.",
        )
    overrides = spec.get("overrides")
    if "overrides" in spec and not isinstance(overrides, Mapping):
        problems.append(
            f"{where}.{axis}.overrides must be an object keyed by value, "
            f"not {_type_name(overrides)}.",
        )
    elif isinstance(overrides, Mapping):
        for key, value in overrides.items():
            if not isinstance(value, Mapping):
                problems.append(
                    f"{where}.{axis}.overrides['{key}'] must be an object of the "
                    f"fields to change, not {_type_name(value)}.",
                )
            else:
                stray = _unknown(value, _ENTRY_KEYS[axis] - {"value"})
                if stray:
                    problems.append(
                        f"{where}.{axis}.overrides['{key}'] sets "
                        f"{', '.join(repr(s) for s in stray)}, which a "
                        f"{axis[:-1]} does not have.",
                    )
    return problems


def _entry_problems(entry: Any, axis: str, position: int, where: str) -> list[str]:
    """One written-out entry."""
    at = f"{where}.{axis}[{position}]"
    if isinstance(entry, str | int | float) and not isinstance(entry, bool):
        return []
    if not isinstance(entry, Mapping):
        return [
            f"{at} must be an entry object or a bare value, not {_type_name(entry)} -- "
            f"and an entry the renderer cannot read is dropped, so the list draws "
            f"shorter than it was written.",
        ]
    problems: list[str] = []
    if "value" not in entry:
        problems.append(
            f"{at} has no 'value', so it is dropped and the list draws one entry "
            f"shorter. A value is whatever the device reports and accepts for it "
            f"(a port number, a name, an address).",
        )
    elif isinstance(entry["value"], bool) or not isinstance(
        entry["value"], str | int | float,
    ):
        problems.append(
            f"{at}.value must be a number or text, not {_type_name(entry['value'])}.",
        )
    stray = _unknown(entry, _ENTRY_KEYS[axis])
    if stray:
        problems.append(
            f"{at} sets {', '.join(repr(s) for s in stray)}, which a {axis[:-1]} "
            f"does not have. It may say {', '.join(sorted(_ENTRY_KEYS[axis]))}.",
        )
    if "route" in entry and not isinstance(entry["route"], list):
        problems.append(
            f"{at}.route must be a list of actions (an empty list makes the row "
            f"deliberately do nothing), not {_type_name(entry['route'])}.",
        )
    return problems


def matrix_config_problems(
    config: Any, *, where: str = "matrix_config",
) -> list[str]:
    """Everything wrong with a matrix_config, said one sentence at a time.

    Empty for a config that will draw what it says. Every problem here is one
    the renderer answers by drawing LESS than was written and saying nothing:
    the point of the check is that silence.

    Deliberately not a check of what the values MEAN -- whether a route key
    names a real device, whether two entries collide, whether an axis came out
    empty. Those need the project or the driver registry and are the page
    review's, which warns rather than refuses because they are judgement calls.
    This one is structure, and structure is answerable without asking anybody.
    """
    if config is None:
        return []
    if not isinstance(config, Mapping):
        return [f"{where} must be an object, not {_type_name(config)}."]

    problems: list[str] = []
    for axis in ("sources", "destinations"):
        if axis not in config:
            continue
        spec = config[axis]
        if isinstance(spec, Mapping):
            problems.extend(_generator_problems(spec, axis, where))
        elif isinstance(spec, Sequence) and not isinstance(spec, str | bytes):
            for position, entry in enumerate(spec):
                problems.extend(_entry_problems(entry, axis, position, where))
        else:
            problems.append(
                f"{where}.{axis} must be a list of entries or a generator object, "
                f"not {_type_name(spec)}.",
            )

    for flag in _FLAG_KEYS:
        if flag in config and not isinstance(config[flag], bool):
            problems.append(
                f"{where}.{flag} must be true or false, not {_type_name(config[flag])}.",
            )

    presets = config.get("presets")
    if "presets" in config and (
        not isinstance(presets, Sequence) or isinstance(presets, str | bytes)
    ):
        problems.append(f"{where}.presets must be a list, not {_type_name(presets)}.")
    elif isinstance(presets, Sequence) and not isinstance(presets, str | bytes):
        for position, preset in enumerate(presets):
            at = f"{where}.presets[{position}]"
            if not isinstance(preset, Mapping):
                problems.append(
                    f"{at} must be an object with a name and the macro it runs, "
                    f"not {_type_name(preset)}.",
                )
                continue
            stray = _unknown(preset, _PRESET_KEYS)
            if stray:
                problems.append(
                    f"{at} sets {', '.join(repr(s) for s in stray)}; a preset button "
                    f"is a 'name' and the 'macro' it runs.",
                )
    return problems


def destination_for(config: Mapping[str, Any] | None, value: Any) -> dict[str, Any] | None:
    """The destination a panel just routed to, found by the value it sent.

    The panel sends the destination's own opaque ``value``, not a row index, so
    this is how the server finds whose ``route`` override to run. Matching goes
    through ``route_matches`` for the same reason the crosspoints do: a panel
    that read ``"2"`` off a dropdown and a project that wrote ``2`` are naming
    one destination.
    """
    for entry in resolve_axis(config, "destinations"):
        if route_matches(entry.get("value"), value):
            return entry
    return None


def resolve_element(element: Any) -> None:
    """Expand one element's matrix config in place, if it is a matrix.

    Takes a plain dict (an already-dumped element) because this runs on the way
    OUT to a renderer, never on the project the Builder edits -- resolving what
    an author is editing would materialise their generator into a list on the
    next save, and the terse form is the point of having one.
    """
    if not isinstance(element, dict) or element.get("type") != "matrix":
        return
    element["matrix_config"] = resolve_matrix_config(element.get("matrix_config"))


def resolve_ui(ui: Any) -> Any:
    """A dumped UI definition with every matrix element resolved.

    Mutates and returns the dict it is given; callers hand it a fresh
    ``model_dump`` rather than the live project.
    """
    if not isinstance(ui, dict):
        return ui
    for page in ui.get("pages") or ():
        if isinstance(page, dict):
            for element in page.get("elements") or ():
                resolve_element(element)
    for element in ui.get("master_elements") or ():
        resolve_element(element)
    return ui
