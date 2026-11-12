"""A simulator: section's template handler (``receive:`` with ``set_state:`` /
``respond:``, no script), pinned as it behaves: how ``{N}`` and
``{state.key}`` fill, which written value is read as the device's own
number, the order the writes happen in, and the reply.

Invented devices only (platform-feature tests).
"""

import datetime
import re

import pytest

from openavc.simulator.yaml_auto import ExplicitHandler, YAMLAutoSimulator


def _sim(handlers: list, state_variables: dict | None = None) -> YAMLAutoSimulator:
    return YAMLAutoSimulator(
        "dev1",
        config={},
        driver_def={
            "id": "acme_mixer",
            "name": "Acme Mixer",
            "transport": "tcp",
            "delimiter": "\r",
            "state_variables": state_variables or {
                "level": {"type": "number", "label": "Level", "scale": 0.01, "min": -100, "max": 10},
                "name": {"type": "string", "label": "Name"},
                "count": {"type": "integer", "label": "Count"},
                "mute": {"type": "boolean", "label": "Mute"},
            },
            "commands": {},
            "responses": [],
            "simulator": {"command_handlers": handlers},
        },
    )


def _run(sim: YAMLAutoSimulator, line: str) -> bytes | None:
    for handler in sim._explicit_handlers:
        m = handler.pattern.match(line)
        if m:
            return sim._execute_explicit_handler(handler, m)
    raise AssertionError(f"no handler matched {line!r}")


# ── the reply ──


def test_a_reply_fills_captures_and_state_in_the_devices_numbers():
    sim = _sim([{"receive": r"Q(\d) (\w+)", "respond": "A{1} {2} L{state.level} N{state.name}"}])
    sim._state.update({"level": -12.0, "name": "Lectern"})
    assert _run(sim, "Q3 go") == b"A3 go L-1200 NLectern"


def test_a_group_that_did_not_match_is_empty_before_the_last_match_and_literal_after_it():
    sim = _sim([{"receive": r"X(a)?(b)?(c)?", "respond": "<{1}|{2}|{3}>"}])
    assert _run(sim, "Xb") == b"<|b|{3}>"
    assert _run(sim, "Xa") == b"<a|{2}|{3}>"
    assert _run(sim, "X") == b"<{1}|{2}|{3}>"


def test_a_pattern_with_no_groups_leaves_every_capture_reference_as_written():
    sim = _sim([{"receive": "PING", "respond": "PONG {1} {0}"}])
    assert _run(sim, "PING") == b"PONG {1} {0}"


def test_a_state_reference_the_device_does_not_hold_stays_as_written():
    sim = _sim([{"receive": "S", "respond": "{state.nope} {state.name}"}])
    sim._state.pop("nope", None)
    sim._state["name"] = "x"
    assert _run(sim, "S") == b"{state.nope} x"


def test_a_reference_to_a_childs_state_is_filled_like_a_flat_one():
    sim = _sim([{"receive": "S", "respond": "{state.zone.1.level}|{state.zone-2}"}])
    sim._state.update({"zone.1.level": 7, "zone-2": "on"})
    assert _run(sim, "S") == b"7|on"


def test_a_reference_written_twice_is_filled_twice():
    sim = _sim([{"receive": "S", "respond": "{state.name}/{state.name}"}])
    sim._state["name"] = "Z"
    assert _run(sim, "S") == b"Z/Z"


def test_a_capture_that_reads_like_a_state_reference_is_filled_too():
    sim = _sim([{"receive": r"E(.+)", "respond": "{1}"}])
    sim._state["name"] = "Hall"
    assert _run(sim, "E{state.name}") == b"Hall"


def test_a_state_value_of_none_goes_out_as_the_word():
    sim = _sim([{"receive": "S", "respond": "[{state.name}]"}])
    sim._state["name"] = None
    assert _run(sim, "S") == b"[None]"


@pytest.mark.parametrize("respond", [None, ""])
def test_no_reply_without_respond_text(respond):
    handlers = [{"receive": "S", "set_state": {"name": "x"}}]
    if respond is not None:
        handlers[0]["respond"] = respond
    sim = _sim(handlers)
    assert _run(sim, "S") is None
    assert sim._state["name"] == "x"


# ── the writes ──


def test_a_value_made_from_a_capture_is_read_as_the_devices_number():
    sim = _sim([{"receive": r"L(-?\d+)", "set_state": {"level": "{1}"}}])
    _run(sim, "L-1200")
    assert sim._state["level"] == -12.0


def test_a_value_with_no_placeholder_is_the_authors_as_written():
    sim = _sim([{"receive": "L", "set_state": {"level": "-5"}}])
    _run(sim, "L")
    assert sim._state["level"] == -5.0


def test_a_value_made_from_state_is_read_back_from_the_devices_number():
    sim = _sim([{"receive": "C", "set_state": {"level": "{state.level}"}}])
    sim._state["level"] = -12.0
    _run(sim, "C")
    assert sim._state["level"] == -12.0


def test_a_zero_reference_counts_as_a_placeholder_and_stays_as_written():
    sim = _sim([{"receive": r"N(\d)", "set_state": {"name": "n{0}{1}", "count": "{0}"}}])
    _run(sim, "N7")
    assert sim._state["name"] == "n{0}7"
    assert sim._state["count"] == "{0}"


def test_a_value_that_is_not_text_is_written_as_its_text_first():
    sim = _sim([{"receive": "W", "set_state": {"count": 5, "mute": True, "name": None}}])
    _run(sim, "W")
    assert sim._state["count"] == 5
    assert sim._state["mute"] is True
    assert sim._state["name"] == "None"


def test_the_writes_happen_in_order_and_a_later_one_reads_an_earlier_one():
    sim = _sim([{"receive": r"P(\w+)", "set_state": {
        "name": "{1}", "count": "3", "level": "{state.count}00",
    }, "respond": "{state.name} {state.level}"}])
    sim._state.update({"name": "old", "count": 9, "level": 0.0})
    seen = []
    real_set = sim.set_state

    def record(key, value):
        seen.append((key, value))
        real_set(key, value)

    sim.set_state = record
    assert _run(sim, "Pnew") == b"new 300"
    assert seen == [("name", "new"), ("count", 3), ("level", 3.0)]


def test_a_value_the_device_reports_as_unknown_is_read_back_before_its_type():
    sim = _sim([{"receive": r"L(-?\d+)", "set_state": {"level": "{1}"}}], {
        "level": {"type": "number", "label": "Level", "scale": 0.01, "unknown": ["-32768"]},
    })
    sim._state["level"] = 1.0
    _run(sim, "L-32768")
    assert sim._state["level"] is None


def test_a_date_literal_is_written_as_its_text():
    sim = _sim([{"receive": "D", "set_state": {"name": datetime.date(2024, 1, 2)}}])
    _run(sim, "D")
    assert sim._state["name"] == "2024-01-02"


def test_a_write_outside_the_bounds_is_clamped():
    sim = _sim([{"receive": r"L(-?\d+)", "set_state": {"level": "{1}"}}])
    _run(sim, "L5000")
    assert sim._state["level"] == 10


# ── reading the section ──


def test_the_section_is_read_into_handlers_in_order():
    sim = _sim([
        {"receive": "A", "respond": "a"},
        {"match": "B", "set_state": {"name": "b"}},
        {"receive": "C", "handler": "respond('c')"},
        {"receive": "D", "respond": ""},
        {"receive": "(", "respond": "bad"},
        {"receive": "", "respond": "never"},
    ])
    assert [(h.pattern.pattern, h.respond, h.set_state) for h in sim._explicit_handlers] == [
        ("^A$", "a", {}), ("^B$", None, {"name": "b"}), ("^D$", "", {}),
    ]
    assert isinstance(sim._explicit_handlers[0], ExplicitHandler)


def test_a_handler_answers_through_the_chain_without_a_delimiter_of_its_own():
    sim = _sim([{"receive": r"Q(\d)", "respond": "A{1}"}])
    assert sim.handle_command(b"Q4\r") == b"A4"


def test_a_handler_built_by_hand_runs_the_same_way():
    sim = _sim([])
    handler = ExplicitHandler(re.compile(r"^H(\d)$"), "h{1}", {"count": "{1}"})
    assert sim._execute_explicit_handler(handler, handler.pattern.match("H2")) == b"h2"
    assert sim._state["count"] == 2
