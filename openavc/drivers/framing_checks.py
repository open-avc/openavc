"""A driver's framing blocks, checked when it loads: how the byte stream is cut
into frames on the way in (``frame_parser:``) and how each frame is wrapped on
the way out (``send_frame:``, ``command_prefix`` and ``command_suffix``).

A frame parser is read the same way wherever one is declared: the runtime
builds the driver's own ``frame_parser:`` and a ``push:`` tcp_listener's
through one interpreter (``transport/frame_parsers.build_frame_parser``), and
``send_frame:`` through ``compiled_protocol.build_send_frame``. So each framing
type's fields are one table here (``FRAME_FIELDS``, ``SEND_FRAME_FIELDS``):
the key, the value the runtime falls back to when it is absent, and the rule
the value is held to. A field a type gains is a row here, beside its entry in
``spec.DEFS`` and the runtime's read of it, and ``tests/test_framing_tables.py``
holds the rows to both. The blocks differ in the types they take and in how a
message is worded, and today each reads its own rows; what they share is the
rules:

* the driver's ``frame_parser:`` takes ``spec.FRAME_PARSER_TYPES`` and words
  each field ``frame_parser: <key> must be <rule> (got <value>)``;
* ``send_frame:`` takes ``spec.SEND_FRAME_TYPES``, and its fields are judged
  only when its type is one of them;
* a push block's ``frame_parser`` takes ``spec.PUSH_FRAME_PARSER_TYPES`` and
  judges only a ``struct_frame``'s fields (``push_block_checks``, worded
  ``push: frame_parser <key> must be <rule>``): a ``length_prefix`` or
  ``fixed_length`` there loads unjudged, and judging them is one condition
  there.

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
    key: str
    default: Any
    rule: FieldRule


#: Each frame parser type's fields, in the order they are reported.
FRAME_FIELDS: dict[str, tuple[FrameField, ...]] = {
    "length_prefix": (
        FrameField("header_size", 2, _one_of(LENGTH_HEADER_SIZES, quoted=False)),
        FrameField("header_offset", 0, _INTEGER),
        # The length need not open the frame (eISCP: at offset 8, behind the
        # magic and the header size, followed by the version and reserved bytes).
        FrameField("length_offset", 0, _COUNT),
        FrameField("header_extra", 0, _COUNT),
        FrameField("length_endian", "big", _ENDIAN),
    ),
    "fixed_length": (FrameField("length", 1, _POSITIVE),),
    "struct_frame": (
        FrameField("header_reserve", 0, _COUNT),
        FrameField("mid_reserve", 0, _COUNT),
        FrameField("trailer_reserve", 0, _COUNT),
        FrameField("length_size", 2, _one_of(STRUCT_LENGTH_SIZES, quoted=False)),
        FrameField("length_adjust", 0, _INTEGER),
        FrameField("length_endian", "big", _ENDIAN),
    ),
}

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


def field_problems(
    fields: tuple[FrameField, ...], block: dict[str, Any]
) -> Iterator[tuple[str, str, Any]]:
    """Each field of ``block`` its rule refuses, in the table's order: the key,
    what the value must be, and the value (the default when it is absent)."""
    for field in fields:
        value = block.get(field.key, field.default)
        if field.rule.refuses(value):
            yield field.key, field.rule.words, value


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
    frame_type = block.get("type", "")
    if frame_type in FRAME_PARSER_TYPES:
        return [
            f"frame_parser: {key} must be {words} (got {value!r})"
            for key, words, value in field_problems(FRAME_FIELDS[frame_type], block)
        ]
    if frame_type:
        expected = spoken_choices(FRAME_PARSER_TYPES, quoted=True)
        return [f"frame_parser: unknown type '{frame_type}' (expected {expected})"]
    return ["frame_parser: missing 'type'"]


def send_frame_errors(driver_def: dict[str, Any]) -> list[str]:
    """The driver's ``send_frame:``, the send twin of a ``length_prefix``
    frame parser: a header whose length field is worked out per message."""
    block = driver_def.get("send_frame")
    if block is None:
        return []
    if not isinstance(block, dict):
        return ["send_frame: must be a mapping"]
    frame_type = block.get("type", "length_prefix")
    if frame_type not in SEND_FRAME_TYPES:
        expected = spoken_choices(SEND_FRAME_TYPES, quoted=True)
        return [f"send_frame: unknown type '{frame_type}' (expected {expected})"]
    return [
        f"send_frame: {key} must be {words} (got {value!r})"
        for key, words, value in field_problems(SEND_FRAME_FIELDS[frame_type], block)
    ]


def command_affix_errors(driver_def: dict[str, Any], key: str) -> list[str]:
    """One of ``COMMAND_AFFIXES``: text when present."""
    if _not_text_if_set(driver_def.get(key)):
        return [f"{key}: must be a string"]
    return []
