"""The framing checks' field tables against the contract and the runtime.

``framing_checks`` judges each frame type's fields from one table, with the
value the runtime falls back to when a field is absent. These hold that every
type the contract names has its table, and that the table's fallbacks build
the same parser or send header the runtime builds from an empty block, so a
default changed on one side and not the other fails here.
"""
from __future__ import annotations

import pytest

from openavc.drivers.compiled_protocol import build_send_frame
from openavc.drivers.framing_checks import FRAME_FIELDS, SEND_FRAME_FIELDS
from openavc.drivers.spec import (
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
