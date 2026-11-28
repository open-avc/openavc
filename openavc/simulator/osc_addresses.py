"""How a generated OSC simulator speaks for the device's addresses.

A response rule with an ``address:`` names, one mapping per argument, the state
variables a message to that address carries. ``yaml_auto`` answers a message
WITH arguments by setting them and one WITHOUT by reporting them, and pushes a
change made outside the protocol to the peer. This module holds what those
three share, read once per address from the rule as the driver reads it
(``reply_rule.read_reply_rule``: a mapping with no type takes its variable's):

* ``address_rules``: each rule's address, its mappings, and the arguments of
  the message it reads (``AddressArg``: the variable, the position the driver
  reads it at, the tag, the value map both ways).
* THE message an address carries (``OscAddress.message``), which a query
  answers and a push sends alike: every variable it names at its own
  position, in its own map's words, a position no mapping reads sent as nil.
* ``push_targets``: per variable, which address a change is pushed on (the
  LAST address naming it).
* the tag a value goes out under (``type_tag`` for a mapping's type,
  ``tag_for`` for a bare value a script hands ``respond``), and the one rule a
  value is cast to its tag by (``tagged``).

What a value goes out as is what the driver reads back as that value: the
device's token when the mapping's map names one (``"A1"`` for ``on``), else
the device's own number from the variable's conversion, since the driver reads
a value its map does not name as it came (a level of -20 beside a map naming
only ``-inf``). A value no argument can carry that way (text where the tag is
a number, or a number the map reads as another value) holds the whole message
back: the query is not answered and the change is not pushed.

The console's own addresses (``system_reply``) are answered before any rule;
the validator counts them as covered from the same table.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from functools import partial
from typing import Any, NamedTuple

from openavc.core.condition_eval import value_words
from openavc.drivers.compiled_protocol import map_entry
from openavc.drivers.reply_rule import OSC, read_reply_rule
from openavc.drivers.spec import value_type_traits_or_text

# The type tags osc_encode_message understands.
OSC_TYPE_TAGS = frozenset("fishdbTFN")

SYSTEM_PREFIXES = ("/-action/", "/-show/")


def _info(state: dict) -> list[tuple[str, Any]]:
    return [
        ("s", "V2.07"), ("s", "osc-server"),
        ("s", state.get("console_model", "Simulator")),
        ("s", state.get("firmware_version", "1.0.0")),
    ]


# The console's own addresses and what each answers. /xremote is the
# subscription renewal a console starts pushing after; its empty answer is
# the heartbeat the driver's connection watchdog waits for.
_SYSTEM_ANSWERS = {
    "/xremote": lambda state: [],
    "/info": _info,
    "/status": lambda state: [("s", "active"), ("s", "127.0.0.1"), ("s", "osc-server")],
}

SYSTEM_ADDRESSES = frozenset(_SYSTEM_ANSWERS)


def system_reply(
    address: str, args: list[tuple[str, Any]], state: dict
) -> list[tuple[str, list[tuple[str, Any]]]] | None:
    """The console's answer to one of its own addresses; None for any other.
    The action and show namespaces echo what they are sent."""
    answer = _SYSTEM_ANSWERS.get(address)
    if answer is not None:
        return [(address, answer(state))]
    if address.startswith(SYSTEM_PREFIXES):
        return [(address, args)] if args else [(address, [])]
    return None


def type_tag(value_type: object) -> str:
    """The OSC tag a value of ``value_type`` goes out under: the value type
    table's ``osc_tag``. The type is a reply mapping's, as the driver reads
    it; a mapping with none, a null one, or one the strict check refuses
    (which still loads) the driver reads as text, so it goes out as text."""
    return value_type_traits_or_text(value_type).osc_tag


def tagged(value: Any, tag: str) -> Any:
    """``value`` as the tag carries it: a float for ``f``, an int for ``i``
    (a boolean 1 or 0), text for ``s``; anything else as it is. Raises
    (ValueError, TypeError, OverflowError) for a value the tag cannot carry."""
    if tag == "f":
        return float(value)
    if tag == "i":
        return int(value)
    if tag == "s":
        return str(value)
    return value


# A value an argument's tag cannot carry.
_UNCARRIED = (ValueError, TypeError, OverflowError)

# What a position of a message that no mapping reads is sent as.
NIL = ("N", None)


class AddressArg(NamedTuple):
    """One argument of an address's message: the variable it carries, the
    position the driver reads it at (``arg``, 0 when the mapping names none),
    the tag it goes out under, and the mapping's value map as written (the
    device's token -> the value) and turned round (the value's lowercase words
    -> the device's token), None without one. ``json_path`` is the path's
    segments for a mapping that reads the argument as JSON text (none for its
    whole value), None for one that reads the argument itself."""

    state: str
    arg: int
    tag: str
    value_map: Mapping[Any, Any] | None
    reverse_map: dict[str, str] | None
    json_path: tuple[str, ...] | None = None

    def device_token(self, value: Any) -> str | None:
        """The device's token for ``value``, filed and read under the value's
        words apart from case (``value_words``); None without a map or a
        match."""
        if not self.reverse_map:
            return None
        return self.reverse_map.get(value_words(value).lower())

    def wire_value(self, value: Any, device_number: Callable[[], Any]) -> tuple[bool, Any]:
        """What this argument carries for ``value``, as (True, the value cast
        to the tag): the device's token, else ``device_number()``, the
        device's own number for it. (False, None) when no value on the wire
        reads back as ``value``: the tag cannot carry it, or the number is a
        key of the map, which the driver reads as that key's value."""
        token = self.device_token(value)
        raw = token if token is not None else device_number()
        try:
            wire = tagged(raw, self.tag)
        except _UNCARRIED:
            return False, None
        if token is None and map_entry(self.value_map, wire)[0]:
            return False, None
        return True, wire


class OscAddress(NamedTuple):
    """One response rule's address: its mappings as the driver reads them
    (what a message with arguments sets) and the arguments of the message it
    reads, in the order the rule names them."""

    address: str
    mappings: tuple[Any, ...]
    args: tuple[AddressArg, ...]

    def message(
        self, value_of: Callable[[str], Any], to_wire: Callable[[str, Any], Any]
    ) -> list[tuple[str, Any]] | None:
        """The message's (tag, value) arguments for the variables' values
        (``value_of``; ``to_wire`` gives a value's device number): each at its
        own position, the first mapping naming a position answering it, and
        a position no mapping reads sent as nil. Mappings that read one
        position as JSON (``json_path``) share it: it goes out as the JSON
        text holding each one's value at its path. None when a value has no
        wire form (``AddressArg.wire_value``): the device says nothing."""
        slots: dict[int, Any] = {}
        for arg in self.args:
            if not _answers(slots, arg):
                continue
            value = value_of(arg.state)
            carried, wire = arg.wire_value(value, partial(to_wire, arg.state, value))
            if not carried:
                return None
            _place(slots, arg, wire)
        width = max(slots, default=-1) + 1
        return [_sent(slots.get(position)) for position in range(width)]


def _answers(slots: dict[int, Any], arg: AddressArg) -> bool:
    """Whether ``arg`` answers its position: it is the first to name it, or
    it reads the position as JSON beside the JSON mappings before it."""
    held = slots.get(arg.arg)
    return held is None or (isinstance(held, dict) and bool(arg.json_path))


def _place(slots: dict[int, Any], arg: AddressArg, wire: Any) -> None:
    """``wire`` into its position: the (tag, value) argument, the whole JSON
    value for an empty path, or the value at its path in the position's JSON
    document (a path through a value another path set is left out)."""
    if arg.json_path is None:
        slots[arg.arg] = (arg.tag, wire)
        return
    if not arg.json_path:
        slots[arg.arg] = ("s", json.dumps(wire))
        return
    node = slots.setdefault(arg.arg, {})
    for segment in arg.json_path[:-1]:
        node = node.setdefault(segment, {})
        if not isinstance(node, dict):
            return
    node[arg.json_path[-1]] = wire


def _sent(held: Any) -> tuple[str, Any]:
    """A position as it goes out: nil for none, a JSON document as its
    text, an argument as it is."""
    if held is None:
        return NIL
    if isinstance(held, dict):
        return ("s", json.dumps(held))
    return held


def _position(mapping: Mapping[str, Any]) -> int | None:
    """The argument a mapping reads (0 when it names none); None for one that
    is not a whole number from 0, which the strict check refuses."""
    arg = mapping.get("arg", 0)
    if isinstance(arg, int) and not isinstance(arg, bool) and arg >= 0:
        return arg
    return None


def _address_arg(mapping: Any) -> AddressArg | None:
    """One mapping as an argument of its address's message; None for one that
    names no variable or no usable position."""
    if not isinstance(mapping, dict) or not mapping.get("state"):
        return None
    position = _position(mapping)
    if position is None:
        return None
    value_map = mapping.get("map")
    value_map = value_map if isinstance(value_map, dict) and value_map else None
    reverse_map = (
        {value_words(value).lower(): str(token) for token, value in value_map.items()}
        if value_map else None
    )
    # The runtime's reply reader, imported here: the validator loads this
    # module for its console addresses, and needs none of what it brings.
    from openavc.drivers.json_replies import path_segments

    json_path = mapping.get("json_path")
    return AddressArg(
        mapping["state"], position, type_tag(mapping.get("type")), value_map, reverse_map,
        tuple(path_segments(json_path)) if json_path is not None else None,
    )


def address_rules(responses: Any, state_variables: Any = None) -> list[OscAddress]:
    """Each response rule with an address, as an ``OscAddress``, in rule
    order, its mappings read as the driver reads them (typed by
    ``state_variables``); a rule with no address or no mappings is left
    out."""
    rules: list[OscAddress] = []
    for resp_def in responses:
        if not isinstance(resp_def, dict) or not resp_def.get("address"):
            continue
        read = read_reply_rule(resp_def, state_variables)
        if read.form != OSC or not read.mappings:
            continue
        args = tuple(a for a in map(_address_arg, read.mappings) if a is not None)
        rules.append(OscAddress(resp_def["address"], read.mappings, args))
    return rules


def push_targets(rules: list[OscAddress]) -> dict[str, OscAddress]:
    """Every variable an address carries -> the address a change to it is
    pushed on, the last rule naming it winning."""
    targets: dict[str, OscAddress] = {}
    for rule in rules:
        for arg in rule.args:
            targets[arg.state] = rule
    return targets


def tag_for(value: Any) -> str:
    """The OSC type tag a bare Python value implies.

    Booleans send ``i`` as 1/0 rather than OSC's payload-free ``T``/``F``,
    because that is what AV gear expects and what ``type_tag`` already picks
    for a boolean state variable.
    """
    if isinstance(value, bool):
        return "i"
    if isinstance(value, int):
        return "i"
    if isinstance(value, float):
        return "f"
    if isinstance(value, (bytes, bytearray)):
        return "b"
    if value is None:
        return "N"
    return "s"


def normalize_osc_args(args: Any) -> list[tuple[str, Any]]:
    """Coerce a script handler's ``respond`` arguments into (tag, value) pairs.

    ``osc_encode_message`` wants ``(type_tag, value)`` pairs, but the author
    guide describes the OSC handler contract as "``respond(address, args)``
    sends an OSC message" with an ``args`` *list* — which reads as a list of
    values, so handlers get written as ``respond(addr, [cue])``. That used to
    reach the encoder untouched, where ``for tag, value in args`` unpacked the
    two-character string ``"12"`` into tag ``"1"`` and value ``"2"``: no branch
    matched the bogus tag, so the message went out with a garbage type tag and
    **no payload at all**. The driver's ``arg: 0`` mapping then found nothing,
    and a simulator that answers every request with an empty reply is
    indistinguishable from one that works.

    So accept both shapes. An already-typed pair passes through; a bare value
    gets the tag its Python type implies. A sequence that is neither raises,
    which the caller's ``except Exception`` logs — loud beats silent here.

    One ambiguity is unavoidable and deliberate: ``["s", "hello"]`` reads as a
    typed pair, not as two strings. The typed form is the documented one, and a
    bare data value that happens to be a lone type-tag letter is far less
    likely than a handler passing a pair.
    """
    if args is None:
        return []
    if not isinstance(args, (list, tuple)):
        args = [args]

    return [
        _typed_pair(arg) if isinstance(arg, (tuple, list)) else (tag_for(arg), arg)
        for arg in args
    ]


def _typed_pair(arg: tuple | list) -> tuple[str, Any]:
    """An already-typed (tag, value) pair; anything else raises."""
    tag = arg[0] if len(arg) == 2 else None
    if isinstance(tag, str) and tag in OSC_TYPE_TAGS:
        return (tag, arg[1])
    raise ValueError(
        f"OSC respond() argument {arg!r} is not a (type_tag, value) "
        f"pair. The type tag must be one of {sorted(OSC_TYPE_TAGS)}. "
        f"Pass a bare value to have its type inferred."
    )
