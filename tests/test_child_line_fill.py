"""A query line filled for one child (openavc/drivers/query_entry.py,
``fill_child_line``): the one fill the runtime's per-child poll lines and
follow-ups, the simulator's per-child handlers and the simulator validator's
samples all use.

The fill gives ``{child_id}`` the child's id through its format spec, as the
runtime sends it, and leaves every other placeholder for the sender. The
validator's checks used to sample a child id three ways of their own (a bare
``{child_id}`` only, or every placeholder as ``1`` with the spec dropped), so
an OSC driver whose poll or read-back names ``{child_id:02d}`` was reported
as unanswered though the runtime sends ``/ch/01/mute`` and the simulator
answers it.
"""

from __future__ import annotations

import pytest
import yaml

from openavc.drivers.query_entry import fill_child_line
from openavc.simulator.driver_sends import SAMPLE_CHILD_ID, child_sample, query_line
from openavc.simulator.validate import validate_yaml_driver


# ── The fill ──


@pytest.mark.parametrize(
    ("line", "local_id", "sent"),
    [
        ("/ch/{child_id}/mute", 1, "/ch/1/mute"),
        ("/ch/{child_id:02d}/mute", 1, "/ch/01/mute"),
        ("/ch/{child_id:02d}/mute", 12, "/ch/12/mute"),
        ("OUT{child_id:03d}?\r", 7, "OUT007?\r"),
        # A string id the spec cannot format stays as typed, as the
        # runtime sends it (and warns about at dispatch).
        ("ZQ{child_id:02d}\r", "main", "ZQ{child_id:02d}\r"),
        ("ZQ{child_id}\r", "main", "ZQmain\r"),
        # Every other placeholder is left for the sender's own fill.
        ("{zone}/ch/{child_id}/{level:02d}", 3, "{zone}/ch/3/{level:02d}"),
        ("NAME?\r", 2, "NAME?\r"),
    ],
)
def test_the_fill_gives_child_id_the_childs_id_through_its_format_spec(line, local_id, sent) -> None:
    assert fill_child_line(line, local_id) == sent


def test_the_validators_sample_child_is_the_first_and_samples_through_the_fill() -> None:
    assert SAMPLE_CHILD_ID == 1
    assert child_sample("/ch/{child_id}/mute") == "/ch/1/mute"
    assert child_sample("/ch/{child_id:02d}/mute") == "/ch/01/mute"
    # A text line's sample is the same child, config filled first.
    assert query_line("OUT{child_id:02d}?\\r", {}, "tcp") == "OUT01?"


# ── The simulator validator's OSC checks ──


def _osc_driver(**changes) -> dict:
    definition = {
        "id": "acme_widget",
        "name": "Acme Widget",
        "manufacturer": "Acme",
        "category": "audio",
        "transport": "osc",
        "default_config": {"port": 10023},
        "config_schema": {},
        "state_variables": {"mute": {"type": "integer", "label": "Mute"}},
        "child_entity_types": {
            "channel": {
                "label": "Channel",
                "id_format": {"type": "integer", "min": 1, "max": 2},
                "instances": {"count": 2},
            },
        },
        # The simulator answers literal, zero-padded addresses.
        "responses": [
            {"address": "/ch/01/mute", "set": {"mute": "$1"}},
            {"address": "/ch/1/level", "set": {"mute": "$1"}},
        ],
        "commands": {
            "mute": {
                "address": "/ch/{child_id:02d}/mute",
                "params": {"child_id": {"type": "child_id", "child_type": "channel"}},
                "read_back": "/ch/{child_id:02d}/mute",
            },
        },
    }
    definition.update(changes)
    return definition


def _coverage(tmp_path, definition: dict) -> list[str]:
    path = tmp_path / "acme_widget.avcdriver"
    path.write_text(yaml.safe_dump(definition, sort_keys=False), encoding="utf-8")
    result = validate_yaml_driver(path)
    return [
        f"{i.severity}: {i.message}"
        for i in result.issues
        if i.check in ("poll_coverage", "command_coverage")
    ]


def test_an_osc_poll_naming_a_formatted_child_id_is_answered(tmp_path) -> None:
    definition = _osc_driver(polling={"interval": 5, "queries": [
        {"each_child": "channel", "send": "/ch/{child_id:02d}/mute"},
        "/ch/{child_id}/level",
    ]})
    assert _coverage(tmp_path, definition) == []


def test_an_osc_read_back_naming_a_formatted_child_id_is_answered(tmp_path) -> None:
    assert _coverage(tmp_path, _osc_driver()) == []


def test_an_osc_poll_the_simulator_does_not_answer_is_named_as_the_runtime_sends_it(tmp_path) -> None:
    definition = _osc_driver(polling={"interval": 5, "queries": ["/ch/{child_id:02d}/level"]})
    assert _coverage(tmp_path, definition) == [
        "error: No simulator handler matches OSC polling query: '/ch/01/level'",
    ]


def test_an_osc_read_back_the_simulator_does_not_answer_is_named_as_the_runtime_sends_it(tmp_path) -> None:
    definition = _osc_driver()
    definition["commands"]["mute"]["read_back"] = "/ch/{child_id:02d}/level"
    assert _coverage(tmp_path, definition) == [
        "error: No simulator handler matches OSC read_back query: '/ch/01/level'",
    ]
