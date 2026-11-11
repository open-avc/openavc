"""How a generated OSC simulator speaks for the device's addresses.

A response rule with an ``address:`` names, one mapping per argument, the state
variables a message to that address carries. ``yaml_auto`` answers a message
WITH arguments by setting them and one WITHOUT by reporting them, and pushes a
change made outside the protocol to the peer. This module holds what those
three share: the rules read from the definition (``address_rules``); per
variable, where a change is pushed (``push_targets``: the LAST address naming
it, its type tag, its value map turned round so a value finds the device's
token); the tag a value goes out under (``type_tag`` for a variable,
``tag_for`` for a bare value a script hands ``respond``); and the one rule a
value is cast to its tag by (``tagged``).

Two answers differ, and the pins hold them: a query of a value the reverse map
lacks answers with the device's number as the conversion gives it, uncast
(``osc_encode_message`` casts it the same way on the wire), where a push of one
sends nothing; and a query reads the reverse map of the variable's push
target, the last address naming it, while its tag is the asking mapping's own.

The console's own addresses (``system_reply``) are answered before any rule;
the validator counts them as covered from the same table.
"""

from __future__ import annotations

from typing import Any, NamedTuple

# A state variable's type -> the OSC tag its value goes out under.
_VARIABLE_TAGS = {
    "float": "f",
    "number": "f",
    "integer": "i",
    "boolean": "i",
    "string": "s",
}

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


def type_tag(value_type: str) -> str:
    """The OSC tag a state variable of ``value_type`` goes out under."""
    return _VARIABLE_TAGS.get(value_type, "f")


def tagged(value: Any, tag: str) -> Any:
    """``value`` as the tag carries it: a float for ``f``, an int for ``i``
    (a boolean 1 or 0), text for ``s``; anything else as it is."""
    if tag == "f":
        return float(value)
    if tag == "i":
        return int(value)
    if tag == "s":
        return str(value)
    return value


class PushTarget(NamedTuple):
    """Where a change to one variable is pushed: the address, the argument
    the mapping reads, its tag, and its value map turned round (the value's
    lowercase text -> the device's token), None without one."""

    address: str
    arg: Any
    tag: str
    reverse_map: dict[str, str] | None

    def device_token(self, value: Any) -> str | None:
        """The device's token for ``value``; None without a map or a match."""
        if not self.reverse_map:
            return None
        return self.reverse_map.get(str(value).lower())


def address_rules(responses: Any) -> list[tuple[str, list[dict]]]:
    """Each response rule's address with its mappings, in rule order; a rule
    with no address or no mappings is left out."""
    rules: list[tuple[str, list[dict]]] = []
    for resp_def in responses:
        address = resp_def.get("address")
        if not address:
            continue
        mappings = resp_def.get("mappings", [])
        if mappings:
            rules.append((address, mappings))
    return rules


def push_targets(rules: list[tuple[str, list[dict]]]) -> dict[str, PushTarget]:
    """Every variable a mapping names -> its PushTarget, the last mapping
    naming it winning."""
    targets: dict[str, PushTarget] = {}
    for address, mappings in rules:
        for mapping in mappings:
            state_key = mapping.get("state")
            if not state_key:
                continue
            arg = mapping.get("arg", 0)
            tag = type_tag(mapping.get("type", "float"))
            value_map = mapping.get("map")
            reverse_map = (
                {str(state_val).lower(): str(osc_val) for osc_val, state_val in value_map.items()}
                if value_map else None
            )
            targets[state_key] = PushTarget(address, arg, tag, reverse_map)
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
        f"pair — the type tag must be one of {sorted(OSC_TYPE_TAGS)}. "
        f"Pass a bare value to have its type inferred."
    )
