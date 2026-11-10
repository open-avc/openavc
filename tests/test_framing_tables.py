"""The framing checks' field tables against the contract and the runtime.

``framing_checks`` judges each frame type's fields from one table, with the
value the runtime falls back to when a field is absent. These hold that every
type the contract names has its table; that every row is a field the contract
declares (``spec.DEFS``), held to at least what it declares (its enum, its
minimum) and that every declared field but the type and ``include_header``
has a row; and that the table's fallbacks build the same parser or send
header the runtime builds from an empty block. A field or default changed on
one side and not the other fails here.
"""
from __future__ import annotations

import pytest

from openavc.drivers.compiled_protocol import build_send_frame
from openavc.drivers.framing_checks import FRAME_FIELDS, SEND_FRAME_FIELDS
from openavc.drivers.spec import (
    DEFS,
    FRAME_PARSER_TYPES,
    PUSH_FRAME_PARSER_TYPES,
    SEND_FRAME_TYPES,
)
from openavc.transport.frame_parsers import build_frame_parser


def test_every_frame_type_the_contract_names_has_its_fields() -> None:
    assert set(FRAME_FIELDS) == set(FRAME_PARSER_TYPES) | set(PUSH_FRAME_PARSER_TYPES)
    assert set(SEND_FRAME_FIELDS) == set(SEND_FRAME_TYPES)


@pytest.mark.parametrize("frame_type", sorted(FRAME_FIELDS))
def test_a_frame_tables_fallbacks_are_the_runtimes(frame_type: str) -> None:
    fallbacks = {field.key: field.default for field in FRAME_FIELDS[frame_type]}
    from_table = build_frame_parser({"type": frame_type, **fallbacks})
    from_nothing = build_frame_parser({"type": frame_type})
    assert from_table is not None and from_nothing is not None
    assert type(from_table) is type(from_nothing)
    assert vars(from_table) == vars(from_nothing)


@pytest.mark.parametrize("frame_type", sorted(SEND_FRAME_FIELDS))
def test_a_send_frame_tables_fallbacks_are_the_runtimes(frame_type: str) -> None:
    fallbacks = {field.key: field.default for field in SEND_FRAME_FIELDS[frame_type]}
    assert build_send_frame({"type": frame_type, **fallbacks}) == build_send_frame(
        {"type": frame_type}
    )


# The declared fields no row judges: the type picks the table, and
# include_header is a switch the runtime reads as truthy.
_UNJUDGED = {"type", "include_header"}

_TABLES = [
    ("frameParser", FRAME_FIELDS),
    ("sendFrame", SEND_FRAME_FIELDS),
]


def _rows(tables: dict) -> list:
    return [(frame_type, row) for frame_type, rows in tables.items() for row in rows]


@pytest.mark.parametrize("def_name, tables", _TABLES)
def test_every_declared_field_has_a_row_and_every_row_is_declared(def_name, tables) -> None:
    declared = set(DEFS[def_name]["fields"]) - _UNJUDGED
    assert {row.key for _, row in _rows(tables)} == declared


@pytest.mark.parametrize("def_name, tables", _TABLES)
def test_every_row_holds_at_least_what_the_contract_declares(def_name, tables) -> None:
    fields = DEFS[def_name]["fields"]
    for frame_type, row in _rows(tables):
        decl = fields[row.key]
        where = f"{frame_type}.{row.key}"
        not_its_type = "x" if decl["type"] == "integer" else 5
        assert row.rule.refuses(not_its_type), where
        assert not row.rule.refuses(row.default), where
        for value in decl.get("enum", ()):
            assert not row.rule.refuses(value), where
        if "enum" in decl:
            outside = max(decl["enum"]) + 1 if decl["type"] == "integer" else "middle"
            assert row.rule.refuses(outside), where
        if "min" in decl:
            assert not row.rule.refuses(decl["min"]), where
            assert row.rule.refuses(decl["min"] - 1), where
