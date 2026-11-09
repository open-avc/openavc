"""The page review's single-element checks, pinned sentence for sentence.

A matrix's lists, a style measurement against its box, a property its type's
renderer never reads, the box a master draws in and the controls drawn over it:
``tests/fixtures/page_review_check_cases.json`` holds each case and what the
review says about it, and the Builder's copy is held to the same file by
``pageReviewChecks.test.ts``. The parity corpus compares the two sides over
whole projects; this corpus reaches the branches those projects do not (one
lock against several, a coordinate written as text, a container answering for
the control inside it).

The range and rounding checks are here alone: the Builder offers its range
match in the device value picker instead, so they have no twin to share a file
with.

Every element here is invented.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openavc.ui.page_review import (
    buried_master_findings,
    master_box,
    matrix_findings,
    precision_review,
    property_findings,
    range_review,
    style_finding,
)

CORPUS = json.loads(
    (Path(__file__).parent / "fixtures" / "page_review_check_cases.json").read_text(
        encoding="utf-8",
    ),
)


def _said(findings) -> list[dict]:
    return [
        {
            "element_id": f.element_id,
            "kind": f.kind,
            "message": f.message,
            "key": "|".join(str(part) for part in f.key),
        }
        for f in findings
    ]


def _cases(section: str):
    return [pytest.param(case, id=case["name"]) for case in CORPUS[section]]


@pytest.mark.parametrize("case", _cases("matrix_findings"))
def test_matrix_findings(case) -> None:
    assert _said(matrix_findings(case["element"])) == case["expected"]


@pytest.mark.parametrize("case", _cases("style_finding"))
def test_style_finding(case) -> None:
    found = style_finding(case["element"], tuple(case["box_px"]))
    assert _said([found] if found else []) == case["expected"]


@pytest.mark.parametrize("case", _cases("property_findings"))
def test_property_findings(case) -> None:
    assert _said(property_findings(case["element"])) == case["expected"]


@pytest.mark.parametrize("case", _cases("master_box"))
def test_master_box(case) -> None:
    assert master_box(case["master"], case["orientation"]) == case["expected"]


@pytest.mark.parametrize("case", _cases("buried_master_findings"))
def test_buried_master_findings(case) -> None:
    scope = case["in_scope"]
    findings = buried_master_findings(
        case["boxes"], case["dumps"], case["parents"], case["masters"],
        case["page_id"], case["orientation"],
        (lambda _el_id: True) if scope is None else (lambda el_id: el_id in scope),
    )
    assert _said(findings) == case["expected"]


def test_the_corpus_reaches_every_finding_these_checks_make() -> None:
    """A corpus whose expectations are all empty passes for free."""
    kinds = {
        finding["kind"]
        for cases in CORPUS.values()
        for case in cases
        if isinstance(case["expected"], list)
        for finding in case["expected"]
    }
    assert kinds == {
        "matrix_not_configured", "matrix_config_unread", "matrix_default_size",
        "matrix_duplicate_values", "matrix_lock_unbacked", "matrix_no_route_feedback",
        "style_too_large", "property_not_rendered", "covers_master",
    }


# --- Ranges and rounding (the AI side only) ---------------------------------

DECLARED = {"min": -80, "max": 10, "step": 0.5, "unit": "dB"}
KEY = "device.amp.volume"


def _fills(adjustments) -> list[tuple]:
    return [(a.element_id, a.field, a.value, a.message) for a in adjustments]


def test_an_empty_fader_takes_every_bound_the_driver_declares_in_order() -> None:
    fills, findings = range_review({"id": "f1", "type": "fader"}, DECLARED, KEY)
    assert _fills(fills) == [
        ("f1", "min", -80, "f1: min was not set, so it was filled in as -80 from what the "
                           "driver declares for device.amp.volume."),
        ("f1", "max", 10, "f1: max was not set, so it was filled in as 10 from what the "
                          "driver declares for device.amp.volume."),
        ("f1", "step", 0.5, "f1: step was not set, so it was filled in as 0.5 from what the "
                            "driver declares for device.amp.volume."),
        ("f1", "unit", "dB", "f1: unit was not set, so it was filled in as dB from what the "
                             "driver declares for device.amp.volume."),
    ]
    assert findings == []


def test_each_type_takes_only_the_bounds_it_reads() -> None:
    gauge, _ = range_review({"id": "g", "type": "gauge"}, DECLARED, KEY)
    meter, _ = range_review({"id": "m", "type": "level_meter"}, DECLARED, KEY)
    slider, _ = range_review({"id": "s", "type": "slider"}, DECLARED, KEY)
    assert [a.field for a in gauge] == ["min", "max", "unit"]
    assert [a.field for a in meter] == ["min", "max"]
    assert [a.field for a in slider] == ["min", "max", "step", "unit"]


def test_a_declared_value_of_the_wrong_kind_fills_nothing() -> None:
    declared = {"min": True, "max": "10", "step": None, "unit": 5}
    fills, _ = range_review({"id": "f1", "type": "fader"}, declared, KEY)
    assert fills == []


def test_a_declared_zero_is_a_bound() -> None:
    fills, _ = range_review({"id": "f1", "type": "fader"}, {"min": 0}, KEY)
    assert _fills(fills) == [
        ("f1", "min", 0, "f1: min was not set, so it was filled in as 0 from what the "
                         "driver declares for device.amp.volume."),
    ]


def test_a_bound_the_author_wrote_as_zero_is_kept() -> None:
    fills, _ = range_review({"id": "f1", "type": "fader", "min": 0, "step": 0}, DECLARED, KEY)
    assert [a.field for a in fills] == ["max", "unit"]


def test_a_fader_wider_than_the_device_is_warned_bottom_then_top() -> None:
    fills, findings = range_review(
        {"id": "f1", "type": "fader", "min": -100, "max": 12.5}, DECLARED, KEY,
    )
    assert [a.field for a in fills] == ["step", "unit"]
    assert [(f.element_id, f.kind, f.message, f.key) for f in findings] == [
        ("f1", "range_wider_than_device",
         "f1: min -100 is below the -80 the driver declares for device.amp.volume. The "
         "bottom of its travel commands a value the device refuses.",
         ("range_wider_than_device", "f1", "min")),
        ("f1", "range_wider_than_device",
         "f1: max 12.5 is above the 10 the driver declares for device.amp.volume. The "
         "top of its travel commands a value the device refuses.",
         ("range_wider_than_device", "f1", "max")),
    ]


def test_a_range_inside_or_equal_to_the_device_or_not_a_number_is_left_alone() -> None:
    for element in (
        {"id": "s", "type": "slider", "min": -80, "max": 10},
        {"id": "s", "type": "slider", "min": -40, "max": 0},
        {"id": "s", "type": "slider", "min": "-100", "max": "12"},
    ):
        assert range_review(element, DECLARED, KEY)[1] == []


def test_a_gauge_wider_than_the_device_is_not_warned() -> None:
    _, findings = range_review({"id": "g", "type": "gauge", "min": -100}, DECLARED, KEY)
    assert findings == []


def test_a_declared_true_reads_as_a_bound_of_one_in_the_width_check() -> None:
    """Pinned as it is: the fill skips a boolean bound, the width check does not."""
    _, findings = range_review(
        {"id": "f1", "type": "fader", "min": -1, "max": 0}, {"min": True}, KEY,
    )
    assert [f.message for f in findings] == [
        "f1: min -1 is below the True the driver declares for device.amp.volume. The "
        "bottom of its travel commands a value the device refuses.",
    ]


def test_no_declaration_or_a_type_with_no_range_says_nothing() -> None:
    assert range_review({"id": "f1", "type": "fader"}, None, KEY) == ([], [])
    assert range_review({"id": "f1", "type": "fader"}, {}, KEY) == ([], [])
    assert range_review({"id": "b1", "type": "button"}, DECLARED, KEY) == ([], [])


def test_a_label_bound_to_a_float_is_told_to_round() -> None:
    found = precision_review({"id": "v1", "type": "label"}, {"type": " Float ", "unit": "V"}, KEY)
    assert (found.element_id, found.kind, found.key) == (
        "v1", "unrounded_number", ("unrounded_number", "v1"),
    )
    assert found.message == (
        "v1 (label) shows device.amp.volume, which the driver declares as a float, and sets "
        "no display_decimals. A label prints a number exactly as it arrives, so 0.06 V "
        "reads as 0.06000000238418579. Every other type that draws a number picks its own "
        "rounding; a label cannot, because most are bound to text. Set display_decimals."
    )
    unitless = precision_review({"id": "v1", "type": "label"}, {"type": "number"}, KEY)
    assert "so 0.06 reads as" in unitless.message


def test_rounding_set_or_a_value_that_is_not_a_float_says_nothing() -> None:
    assert precision_review(
        {"id": "v1", "type": "label", "display_decimals": 0}, {"type": "float"}, KEY,
    ) is None
    assert precision_review({"id": "v1", "type": "label"}, {"type": "integer"}, KEY) is None
    assert precision_review({"id": "v1", "type": "gauge"}, {"type": "float"}, KEY) is None
    assert precision_review({"id": "v1", "type": "label"}, None, KEY) is None
