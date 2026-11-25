"""A parameter's form aids, checked when a driver loads.

A command's ``params:`` (and an action's, which fill a command's) may carry
aids for the form that collects each value: ``pattern``, the shape typed text
must match; ``min`` / ``max``; ``decimals``, the rounding of a number;
``trim: false``, which keeps the whitespace around a string; a wire-value
``map``; and three pickers, ``options_state`` (a state key that lists the
choices), ``options_from`` (choices that follow a sibling parameter) and
``type_from`` (the input type taken from a sibling cascade's chosen control).

The runtime checks the value a form submits (``param_validation.py``), and the
Programmer's forms read the pickers (``components/shared/paramOptions.ts``).
Neither can say that an aid is malformed: a typo in one leaves a free-text box,
or a map that never translates. So it is refused here, when the driver loads.

``param_aid_errors`` asks each aid's check about each parameter, in the order
of ``_AID_CHECKS``; a new aid is one function and one row there.

One aid is held to a rule the author's doors refuse and a load only names
(``secret_type_errors``): ``secret`` masks text, so a secret parameter takes
text (``spec.secret_off_text``). ``param_sites`` is THE walk over every
command's and action's parameters that it, and the checker's warning on a
credential's name (``driver_warnings``), read.

Purity contract, as for ``avcdriver_semantic``: standard library, the sibling
``spec`` table, the merged-key marker (``declared_names``), the tagged list
(``tagged_errors``) and the regex-safety helper. The module is vendored with the
rest of the driver contract (openavc-drivers' catalog CI, openavc-sdk).
"""

from __future__ import annotations

from typing import Any, Callable, Iterator, NamedTuple

from openavc.drivers.declared_names import UNEVALUATED_KEY
from openavc.drivers.spec import (
    PARAM_OPTIONS_FROM_SOURCES,
    VALUE_TYPES,
    marked_secret,
    secret_off_text,
)
from openavc.drivers.tagged_errors import TaggedErrors
from openavc.drivers.usable_number import is_number_value
from openavc.drivers.yaml_text import NOT_TEXT
from openavc.utils.regex_safety import regex_safety_error


def param_aid_errors(where: str, params: Any) -> list[str]:
    """Every aid of every parameter in ``params``, one parameter at a time.

    ``where`` names the command or action (``Command 'mute'``, ``actions[2]``);
    each message names the parameter after it. A parameter that is not a
    mapping declares no aids.
    """
    if not isinstance(params, dict):
        return []
    errors: list[str] = []
    for pname, pdef in params.items():
        errors.extend(one_param_aid_errors(where, pname, pdef, params))
    return errors


def one_param_aid_errors(where: str, pname: Any, pdef: Any, params: dict[str, Any]) -> list[str]:
    """Every aid of the one parameter ``pname``, with ``params`` the siblings
    its pickers may follow. ``python_info`` asks it a parameter at a time, to
    leave out one whose definition the source reader could not see."""
    if not isinstance(pdef, dict):
        return []
    at = f"{where} param '{pname}'"
    return [message for check in _AID_CHECKS for message in check(at, pdef, params)]


def sibling_refs(pdef: Any) -> list[str]:
    """The siblings a parameter's pickers follow by name (``options_from`` and
    ``type_from``), as written."""
    if not isinstance(pdef, dict):
        return []
    picks = (pdef.get(key) for key in ("options_from", "type_from"))
    return [pick["param"] for pick in picks if isinstance(pick, dict) and isinstance(pick.get("param"), str)]


def _pattern_errors(at: str, pdef: dict[str, Any], params: dict[str, Any]) -> list[str]:
    """A ``pattern`` compiles and cannot be made to backtrack without end."""
    pattern = pdef.get("pattern")
    if pattern is None:
        return []
    error = regex_safety_error(f"{at}: pattern", pattern)
    return [error] if error else []


def _bounds_errors(at: str, pdef: dict[str, Any], params: dict[str, Any]) -> list[str]:
    """``min`` and ``max`` are numbers, and min is not above max. Only usable
    numbers are compared (``is_number_value``): a boolean, an infinity or NaN
    is the shape walk's to name, refused at the author's doors and named at
    load."""
    low, high = pdef.get("min"), pdef.get("max")
    errors = [
        f"{at}: {name} must be a number"
        for name, bound in (("min", low), ("max", high))
        if bound is not None and not _numeric(bound)
    ]
    if _comparable(low, high) and low > high:
        errors.append(f"{at}: min ({low}) must be <= max ({high})")
    return errors


def _comparable(low: Any, high: Any) -> bool:
    return is_number_value(low) and is_number_value(high)


def _numeric(value: Any) -> bool:
    return isinstance(value, (int, float))


def _decimals_errors(at: str, pdef: dict[str, Any], params: dict[str, Any]) -> list[str]:
    """``decimals`` (a number's rounding) is a whole number, 0 or more."""
    decimals = pdef.get("decimals")
    if decimals is None or _non_negative_int(decimals):
        return []
    return [f"{at}: decimals must be a non-negative integer"]


def _non_negative_int(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, int):
        return False
    return value >= 0


def _trim_errors(at: str, pdef: dict[str, Any], params: dict[str, Any]) -> list[str]:
    """``trim`` is a switch: ``false`` sends a string with its whitespace."""
    trim = pdef.get("trim")
    if trim is None or isinstance(trim, bool):
        return []
    return [f"{at}: trim must be true or false"]


def _map_errors(at: str, pdef: dict[str, Any], params: dict[str, Any]) -> list[str]:
    """A wire-value ``map`` pairs a value with the text or number sent for it.

    The map translates the checked value before it is put in the send
    template (0-based channel numbers, letter codes), so a pair that is not
    two scalars would never translate. One message however many pairs are
    wrong: the first, in the words YAML's own reading needs (``yaml_text``)
    where a bare on or off was read as a boolean word.
    """
    value_map = pdef.get("map")
    if value_map is None:
        return []
    if not isinstance(value_map, dict) or not value_map:
        return [f"{at}: map must be a non-empty mapping of value -> wire value"]
    wrong = [(k, v) for k, v in value_map.items() if not (_is_wire_scalar(k) and _is_wire_scalar(v))]
    return [_wrong_pair_words(at, *wrong[0])] if wrong else []


def _wrong_pair_words(at: str, key: Any, word: Any) -> str:
    """What a map pair that is not two scalars says: a word YAML read as a
    boolean is to be quoted; anything else is no pair at all."""
    if isinstance(word, bool) and _is_wire_scalar(key):
        return f"{at}: map word for '{key}' is not text. {NOT_TEXT}"
    return f"{at}: map entries must be scalar value -> wire value pairs"


def _is_wire_scalar(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    return isinstance(value, (str, int, float))


def _options_state_errors(at: str, pdef: dict[str, Any], params: dict[str, Any]) -> list[str]:
    """``options_state`` names the state key whose value lists the choices."""
    ostate = pdef.get("options_state")
    if ostate is None or _is_name(ostate):
        return []
    return [f"{at}: options_state must be a non-empty string"]


def _is_name(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def _options_from_errors(at: str, pdef: dict[str, Any], params: dict[str, Any]) -> list[str]:
    """An ``options_from`` cascade: where its choices come from, the sibling
    it follows, and the types and units it may narrow them to."""
    ofrom = pdef.get("options_from")
    if ofrom is None:
        return []
    if not isinstance(ofrom, dict):
        return [f"{at}: options_from must be a mapping with 'param' and 'source'"]
    source = ofrom.get("source")
    errors: list[str] = []
    if source not in PARAM_OPTIONS_FROM_SOURCES:
        errors.append(
            f"{at}: options_from.source must be one of "
            f"{sorted(PARAM_OPTIONS_FROM_SOURCES)}"
        )
    errors.extend(_cascade_param_errors(at, ofrom.get("param"), source, params))
    # A typo in either list would quietly empty the picker.
    if not _absent_or_listed(ofrom.get("types"), _is_value_type):
        errors.append(
            f"{at}: options_from.types must be a non-empty list of {list(VALUE_TYPES)}"
        )
    if not _absent_or_listed(ofrom.get("units"), _is_unit_name):
        errors.append(
            f"{at}: options_from.units must be a non-empty list of unit names, "
            f"e.g. [\"dB\"]"
        )
    return errors


def _cascade_param_errors(
    at: str, ref: Any, source: Any, params: dict[str, Any]
) -> list[str]:
    """The sibling an ``options_from`` follows: one of this command's own
    params, and for ``child_schema`` the one naming which child."""
    if not _is_name(ref):
        return [f"{at}: options_from.param must name a sibling param"]
    if ref not in params:
        return [f"{at}: options_from.param '{ref}' is not a param of this command"]
    if source != "child_schema":
        return []
    sibling = params.get(ref)
    if isinstance(sibling, dict) and sibling.get("type") != "child_id":
        return [
            f"{at}: options_from.param '{ref}' must be a child_id param for "
            f"source 'child_schema'"
        ]
    return []


def _absent_or_listed(value: Any, allowed: Callable[[Any], Any]) -> bool:
    """Not given, or a non-empty list whose every item ``allowed`` accepts."""
    if value is None:
        return True
    if not isinstance(value, list) or not value:
        return False
    return all(allowed(item) for item in value)


def _is_value_type(item: Any) -> bool:
    return item in VALUE_TYPES


def _is_unit_name(item: Any) -> bool:
    return isinstance(item, str) and bool(item.strip())


def _type_from_errors(at: str, pdef: dict[str, Any], params: dict[str, Any]) -> list[str]:
    """``type_from`` names a sibling ``child_schema`` cascade, which is how it
    finds the component and control whose input type to take."""
    tfrom = pdef.get("type_from")
    if tfrom is None:
        return []
    if not isinstance(tfrom, dict):
        return [f"{at}: type_from must be a mapping with 'param'"]
    ref = tfrom.get("param")
    if not _is_name(ref):
        return [f"{at}: type_from.param must name a sibling param"]
    if ref not in params:
        return [f"{at}: type_from.param '{ref}' is not a param of this command"]
    sibling = params.get(ref)
    cascade = sibling.get("options_from") if isinstance(sibling, dict) else None
    if isinstance(cascade, dict) and cascade.get("source") == "child_schema":
        return []
    return [
        f"{at}: type_from.param '{ref}' must itself be an options_from "
        f"child_schema cascade"
    ]


_AidCheck = Callable[[str, dict[str, Any], dict[str, Any]], list[str]]

# The order a parameter's messages are reported in.
_AID_CHECKS: tuple[_AidCheck, ...] = (
    _pattern_errors,
    _bounds_errors,
    _decimals_errors,
    _trim_errors,
    _map_errors,
    _options_state_errors,
    _options_from_errors,
    _type_from_errors,
)


class ParamSite(NamedTuple):
    """One parameter a driver file declares: the command it belongs to (its
    name) or the action (its index, ``in_action``), its ``name`` and its
    ``definition`` as written. Its ``place`` in the definition and the
    ``where`` a message names it by are written out only when asked, so a
    check names a key only where it has something to say about it."""

    owner: Any
    in_action: bool
    name: Any
    definition: Any

    @property
    def place(self) -> str:
        """``commands.pair.params.code``, ``actions[0].params.code``."""
        if self.in_action:
            return f"actions[{self.owner}].params.{self.name}"
        return f"commands.{self.owner}.params.{self.name}"

    @property
    def where(self) -> str:
        """``Command 'pair' param 'code'``, ``actions[0] param 'code'``: the
        words ``param_aid_errors`` gives."""
        if self.in_action:
            return f"actions[{self.owner}] param '{self.name}'"
        return f"Command '{self.owner}' param '{self.name}'"


def param_sites(definition: Any) -> Iterator[ParamSite]:
    """Each parameter of each command, then of each action, in the order
    written. An action's place is its index in the whole list, entries that
    are not actions counted. An entry a Python driver merged in or built in
    code (``UNEVALUATED_KEY``) is passed over."""
    if not isinstance(definition, dict):
        return
    for command, cmd_def in _named(definition.get("commands")):
        yield from _sites_of(command, False, cmd_def)
    actions = definition.get("actions")
    for i, action in enumerate(actions if isinstance(actions, list) else ()):
        yield from _sites_of(i, True, action)


def _sites_of(owner: Any, in_action: bool, entry: Any) -> Iterator[ParamSite]:
    params = entry.get("params") if isinstance(entry, dict) else None
    return (ParamSite(owner, in_action, name, pdef) for name, pdef in _named(params))


def _named(block: Any) -> Iterator[tuple[Any, Any]]:
    if isinstance(block, dict):
        yield from ((name, entry) for name, entry in block.items() if name != UNEVALUATED_KEY)


def judge_every(place: str, unit: Any) -> bool:
    """The ``judged`` of a YAML definition, which holds nothing unread."""
    return True


def secret_type_errors(
    definition: Any, judged: Callable[[str, Any], bool] = judge_every,
) -> TaggedErrors:
    """Each secret parameter that takes something other than text
    (``spec.secret_off_text``), as THE sentence naming the fix, tagged with
    its place. Refused where a driver is written and named where one loads:
    the YAML walk asks it among the author's doors' parts, a Python driver's
    checker among its shared sections, where ``judged(place, definition)``
    skips a parameter holding a value built in code (and names the skip)."""
    errors = TaggedErrors()
    for site in param_sites(definition):
        if secret_off_text(site.name, site.definition) and judged(site.place, site.definition):
            errors.at(site.place, _off_text_words(site))
    return errors


def _off_text_words(site: ParamSite) -> str:
    why = (
        "is marked secret" if marked_secret(site.definition)
        else "is named password, which makes it a secret"
    )
    return (
        f"{site.where}: {why}, and a secret takes text, not type "
        f"'{site.definition.get('type')}'. A PIN or code is text: use type string"
    )
