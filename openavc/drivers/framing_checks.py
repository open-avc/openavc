"""A driver's framing blocks, checked when it loads: how the byte stream is cut
into frames on the way in (``frame_parser:``) and how each frame is wrapped on
the way out (``send_frame:``, ``command_prefix`` and ``command_suffix``).

THE frame table. Each frame parser type is one row of ``FRAME_TYPES``: the
fields it is built from (the key, the value the runtime falls back to when it
is absent, and the rule the value is held to) and the class in
``transport/frame_parsers`` built from them, which cuts frames out of the
stream (``feed``) and writes a payload back into one (``frame``). Every reader
asks the row: the checks here and push's (``push_block_checks``), the runtime,
which builds the driver's own ``frame_parser:`` and a ``push:`` tcp_listener's
through one interpreter (``frame_parsers.build_frame_parser``, refusing a
value in the row's words), and the simulator, which writes its replies and a
device's dial-backs through the same row (``frame_parsers.write_frame``).
``send_frame:`` has its own table (``SEND_FRAME_FIELDS``), built by
``compiled_protocol.build_send_frame``. A field a type gains is a row here,
beside its entry in ``spec.DEFS``, and ``tests/test_framing_tables.py`` holds
the rows to both. The blocks differ in the types they take and in how a
message is worded:

* the driver's ``frame_parser:`` takes ``spec.FRAME_PARSER_TYPES`` and words
  each field ``frame_parser: <key> must be <rule> (got <value>)``;
* ``send_frame:`` takes ``spec.SEND_FRAME_TYPES``, and its fields are judged
  only when its type is one of them;
* a push block's ``frame_parser`` takes ``spec.PUSH_FRAME_PARSER_TYPES``, every
  row's fields judged: a ``struct_frame``'s at every door by
  ``push_block_checks``, worded ``push: frame_parser <key> must be <rule>``,
  the others' at the author's doors (``push_frame_field_issues``, which the
  driver model carries), worded ``push.frame_parser.<key>: must be <rule>``.

A type is matched by ``in`` over a tuple and a rule by a type test or ``in``,
never by hashing the value, so a ``type`` YAML reads as a list is reported, not
raised on. ``in`` is equality, so ``true`` and ``2.0`` pass as sizes, as they
always have. The messages that list the choices are built from the ``spec``
tables, so they follow them.

Purity contract, as for ``avcdriver_semantic``: standard library and the
sibling ``spec`` table only. The module is vendored with the rest of the
driver contract (openavc-drivers' catalog CI, openavc-sdk).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Callable, Iterator

from openavc.drivers.spec import (
    FRAME_PARSER_TYPES,
    LENGTH_ENDIANS,
    LENGTH_HEADER_SIZES,
    SEND_FRAME_TYPES,
    STRUCT_LENGTH_SIZES,
)


def spoken_choices(values: tuple[Any, ...], *, quoted: bool) -> str:
    """The choices as a message lists them: ``a``, ``a or b``, ``a, b, or c``,
    each in single quotes when ``quoted``."""
    words = [f"'{value}'" if quoted else str(value) for value in values]
    if len(words) < 3:
        return " or ".join(words)
    return ", ".join(words[:-1]) + ", or " + words[-1]


@dataclass(frozen=True)
class FieldRule:
    """What a field must be: the words a message uses, and the test."""

    words: str
    refuses: Callable[[Any], bool]


def _not_an_integer(value: Any) -> bool:
    return isinstance(value, bool) or not isinstance(value, int)


def _not_a_count(value: Any) -> bool:
    return _not_an_integer(value) or value < 0


def _not_positive(value: Any) -> bool:
    return _not_an_integer(value) or value < 1


def _not_text_if_set(value: Any) -> bool:
    return value is not None and not isinstance(value, str)


def _one_of(values: tuple[Any, ...], *, quoted: bool) -> FieldRule:
    return FieldRule(spoken_choices(values, quoted=quoted), lambda value: value not in values)


_INTEGER = FieldRule("an integer", _not_an_integer)
_COUNT = FieldRule("a non-negative integer", _not_a_count)
_POSITIVE = FieldRule("a positive integer", _not_positive)
_TEXT = FieldRule("a string", _not_text_if_set)
_ENDIAN = _one_of(LENGTH_ENDIANS, quoted=True)


@dataclass(frozen=True)
class FrameField:
    """One field a framing block is built from. A field with no ``rule`` is a
    switch the runtime reads as true or false: the shape walk holds it to a
    boolean, and nothing here refuses it."""

    key: str
    default: Any
    rule: FieldRule | None


@dataclass(frozen=True)
class FrameType:
    """One frame parser type: its fields, in the order they are reported, and
    the name of the class in ``transport/frame_parsers`` built from them (each
    field passed by its key), which reads frames out of the stream and writes
    a payload back into one."""

    fields: tuple[FrameField, ...]
    parser: str


#: Every frame parser type the runtime builds, by its ``type``.
FRAME_TYPES: dict[str, FrameType] = {
    "length_prefix": FrameType(
        (
            FrameField("header_size", 2, _one_of(LENGTH_HEADER_SIZES, quoted=False)),
            FrameField("header_offset", 0, _INTEGER),
            # The length need not open the frame (eISCP: at offset 8, behind the
            # magic and the header size, followed by the version and reserved bytes).
            FrameField("length_offset", 0, _COUNT),
            FrameField("header_extra", 0, _COUNT),
            FrameField("length_endian", "big", _ENDIAN),
            FrameField("include_header", False, None),
        ),
        "LengthPrefixFrameParser",
    ),
    "fixed_length": FrameType((FrameField("length", 1, _POSITIVE),), "FixedLengthFrameParser"),
    "struct_frame": FrameType(
        (
            FrameField("header_reserve", 0, _COUNT),
            FrameField("mid_reserve", 0, _COUNT),
            FrameField("trailer_reserve", 0, _COUNT),
            FrameField("length_size", 2, _one_of(STRUCT_LENGTH_SIZES, quoted=False)),
            FrameField("length_adjust", 0, _INTEGER),
            FrameField("length_endian", "big", _ENDIAN),
        ),
        "StructFrameParser",
    ),
}


def frame_type(block: dict[str, Any]) -> FrameType | None:
    """The row for ``block``'s ``type``, or None when it names none (missing,
    unknown, or not text)."""
    name = block.get("type")
    return FRAME_TYPES.get(name) if isinstance(name, str) else None


def field_values(row: FrameType, block: dict[str, Any]) -> dict[str, Any]:
    """Each of ``row``'s fields as the runtime reads it from ``block``: the
    value written, or the field's default when it is absent."""
    return {field.key: block.get(field.key, field.default) for field in row.fields}


#: Each send_frame type's fields, in the order they are reported. The header
#: bytes are escape-decoded text.
SEND_FRAME_FIELDS: dict[str, tuple[FrameField, ...]] = {
    "length_prefix": (
        FrameField("length_size", 4, _POSITIVE),
        FrameField("length_endian", "big", _ENDIAN),
        FrameField("header", None, _TEXT),
        FrameField("after_length", None, _TEXT),
    ),
}

#: The constant text wrapped around every byte-stream command, as written.
COMMAND_AFFIXES: tuple[str, ...] = ("command_prefix", "command_suffix")


#: The frame types whose fields a push listener refuses at every door
#: (``push_block_checks``), as it has since push first judged them; the others'
#: are refused at the author's doors and named at load
#: (``push_frame_field_issues``).
PUSH_EVERY_DOOR_FRAME_TYPES: tuple[str, ...] = ("struct_frame",)


def field_problems(
    fields: tuple[FrameField, ...], block: dict[str, Any]
) -> Iterator[tuple[str, str, Any]]:
    """Each field of ``block`` its rule refuses, in the table's order: the key,
    what the value must be, and the value (the default when it is absent)."""
    for field in fields:
        value = block.get(field.key, field.default)
        if field.rule is not None and field.rule.refuses(value):
            yield field.key, field.rule.words, value


def push_frame_field_issues(driver_def: Mapping[str, Any]) -> list[tuple[str, str]]:
    """The fields of a push tcp_listener's ``frame_parser`` its type refuses,
    for a type push does not refuse at every door (a ``length_prefix`` or a
    ``fixed_length``): each its place and what it must be, in the table's
    order.

    The listener cannot build a parser from one, so each dial-back would reach
    the reply rules unframed. A driver that loaded with one before push judged
    these types still loads, so these are the driver model's problems
    (``driver_model.read_driver``): the author's doors refuse them and a door
    that loads a driver names them, each at its field's place, so the shape
    walk's own sentence there is left out."""
    block = driver_def.get("push")
    if not isinstance(block, dict) or block.get("type") != "tcp_listener":
        return []
    frame = block.get("frame_parser")
    if not isinstance(frame, dict) or frame.get("type") in PUSH_EVERY_DOOR_FRAME_TYPES:
        return []
    row: FrameType | None = frame_type(frame)
    if row is None:
        return []
    return [
        (f"push.frame_parser.{key}", f"must be {words}")
        for key, words, _ in field_problems(row.fields, frame)
    ]


def frame_parser_errors(driver_def: dict[str, Any]) -> list[str]:
    """The driver's ``frame_parser:`` (binary protocols).

    The runtime parsers raise on a value they cannot frame with (a header size
    other than 1, 2 or 4, a length that is not positive), which would leave the
    device in a reconnect loop, so the value is refused here, with a message.
    """
    block = driver_def.get("frame_parser")
    if block is None:
        return []
    if not isinstance(block, dict):
        return ["frame_parser: must be a mapping"]
    type_name = block.get("type", "")
    if type_name in FRAME_PARSER_TYPES:
        return [
            f"frame_parser: {key} must be {words} (got {value!r})"
            for key, words, value in field_problems(FRAME_TYPES[type_name].fields, block)
        ]
    if type_name:
        expected = spoken_choices(FRAME_PARSER_TYPES, quoted=True)
        return [f"frame_parser: unknown type '{type_name}' (expected {expected})"]
    return ["frame_parser: missing 'type'"]


def send_frame_errors(driver_def: dict[str, Any]) -> list[str]:
    """The driver's ``send_frame:``, the send twin of a ``length_prefix``
    frame parser: a header whose length field is worked out per message."""
    block = driver_def.get("send_frame")
    if block is None:
        return []
    if not isinstance(block, dict):
        return ["send_frame: must be a mapping"]
    type_name = block.get("type", "length_prefix")
    if type_name not in SEND_FRAME_TYPES:
        expected = spoken_choices(SEND_FRAME_TYPES, quoted=True)
        return [f"send_frame: unknown type '{type_name}' (expected {expected})"]
    return [
        f"send_frame: {key} must be {words} (got {value!r})"
        for key, words, value in field_problems(SEND_FRAME_FIELDS[type_name], block)
    ]


def command_affix_errors(driver_def: dict[str, Any], key: str) -> list[str]:
    """One of ``COMMAND_AFFIXES``: text when present."""
    if _not_text_if_set(driver_def.get(key)):
        return [f"{key}: must be a string"]
    return []
