"""The round-trip check tells a missing response rule from an acknowledgement.

``_check_response_parsing`` compares what the simulator answers against what
the driver's response rules can read, and used to warn on every mismatch.
On any protocol that acknowledges writes — ``<cmd> ACK`` / ``<cmd> NAK
<code>``, which most AV serial and TCP protocols do — that is one warning per
write command, permanently, on a completely correct driver. Measured over the
shipped community catalog it was 324 of 597 warnings, and a real finding in
that pile is one nobody reads.

The separation is what the driver *does* with the reply, not what the reply
looks like: a query the driver polls has to come back readable or the state
behind it never updates; anything else is a write being acknowledged. These
pin both halves, including the one that must keep firing.
"""

from __future__ import annotations

from openavc.simulator.validate import (
    ValidationResult,
    _check_response_parsing,
    _extract_respond_calls,
)


def _result() -> ValidationResult:
    return ValidationResult(driver_path="x", driver_id="acme_widget", driver_type="yaml")


def _run(driver_def: dict) -> ValidationResult:
    result = _result()
    sim = driver_def.get("simulator", {})
    _check_response_parsing(
        result,
        driver_def.get("commands", {}),
        sim.get("command_handlers", []),
        driver_def.get("responses", []),
        sim.get("initial_state", {}),
        driver_def,
    )
    return result


def _driver(**over) -> dict:
    base = {
        "id": "acme_widget",
        "name": "Acme Widget",
        "transport": "tcp",
        "state_variables": {"power": {"type": "integer", "label": "Power"}},
        "responses": [{"match": r"PWR (\d)", "set": {"power": "$1"}}],
    }
    base.update(over)
    return base


# --- the acknowledgement half -----------------------------------------------


def test_a_write_acknowledgement_is_not_a_warning():
    """The driver never asks this question, so nothing is waiting on a reply."""
    result = _run(
        _driver(
            commands={"power_on": {"send": "SET PWR 1\r"}},
            simulator={
                "command_handlers": [
                    {"match": "SET PWR 1", "respond": "PWR ACK\r"},
                ]
            },
        )
    )
    assert not result.warnings, [i.message for i in result.warnings]


def test_the_acknowledgements_are_counted_rather_than_dropped():
    """"Not checked" has to look different from "checked and clean"."""
    result = _run(
        _driver(
            commands={"power_on": {"send": "SET PWR 1\r"}},
            simulator={
                "command_handlers": [
                    {"match": "SET PWR 1", "respond": "PWR ACK\r"},
                    {"match": "SET VOL (\\d+)", "respond": "VOL ACK\r"},
                ]
            },
        )
    )
    (info,) = [i for i in result.infos if i.check == "response_parsing"]
    assert "2 simulator" in info.message
    assert "PWR ACK" in info.message and "VOL ACK" in info.message


# --- the half that must keep firing ------------------------------------------


def test_a_polled_query_whose_answer_nothing_parses_still_warns():
    """The deliberate negative case: a real defect, in the same shape.

    The driver polls for power and the device answers something no response
    rule reads. Nothing about the reply says "acknowledgement" — and nothing
    about it says otherwise either, which is why the check asks the driver.
    """
    result = _run(
        _driver(
            polling={"queries": ["GET PWR\r"]},
            simulator={
                "command_handlers": [
                    {"match": "GET PWR", "respond": "power is on\r"},
                ]
            },
        )
    )
    (warning,) = result.warnings
    assert warning.check == "response_parsing"
    assert "power is on" in warning.message
    assert "GET PWR" in warning.message


def test_a_polled_query_answered_readably_is_silent():
    result = _run(
        _driver(
            polling={"queries": ["GET PWR\r"]},
            simulator={
                "command_handlers": [
                    {"match": "GET PWR", "respond": "PWR 1\r"},
                ]
            },
        )
    )
    assert not result.warnings and not result.infos


def test_a_query_handler_that_also_answers_out_of_range_is_silent():
    """A NAK branch beside a real answer is not a missing response rule."""
    result = _run(
        _driver(
            polling={"queries": ["GET PWR 1\r"]},
            simulator={
                "command_handlers": [
                    {
                        "match": r"GET PWR (\d)",
                        "handler": (
                            "if int(match.group(1)) > 2:\n"
                            "    respond('PWR NAK 04\\r')\n"
                            "else:\n"
                            "    respond('PWR 1\\r')\n"
                        ),
                    },
                ]
            },
        )
    )
    assert not result.warnings, [i.message for i in result.warnings]


def test_a_query_handler_whose_answer_cannot_be_read_is_not_accused():
    """An f-string answer is unreadable here, which is not the same as absent.

    Nearly every real query handler builds its reply from state, so treating
    "could not read it" as "there is no readable answer" would put the
    warning back on exactly the drivers it was taken off.
    """
    result = _run(
        _driver(
            polling={"queries": ["GET PWR 1\r"]},
            simulator={
                "command_handlers": [
                    {
                        "match": r"GET PWR (\d)",
                        "handler": (
                            "if int(match.group(1)) > 2:\n"
                            "    respond('PWR NAK 04\\r')\n"
                            "else:\n"
                            "    respond(f'PWR {state[\"power\"]}\\r')\n"
                        ),
                    },
                ]
            },
        )
    )
    assert not result.warnings, [i.message for i in result.warnings]


def test_on_connect_alone_does_not_make_a_handler_a_query():
    """on_connect is a bring-up sequence, not a question list.

    Arming push notifications is a write, and its acknowledgement is not
    something a response rule should have to match.
    """
    result = _run(
        _driver(
            on_connect=["ARM NOTIFY 1\r"],
            simulator={
                "command_handlers": [
                    {"match": "ARM NOTIFY 1", "respond": "NOTIFY ACK\r"},
                ]
            },
        )
    )
    assert not result.warnings, [i.message for i in result.warnings]


def test_an_on_connect_entry_declaring_query_for_is_a_query():
    result = _run(
        _driver(
            on_connect=[{"send": "GET PWR\r", "query_for": "power"}],
            simulator={
                "command_handlers": [
                    {"match": "GET PWR", "respond": "power is on\r"},
                ]
            },
        )
    )
    (warning,) = result.warnings
    assert "power is on" in warning.message


# --- reading a handler's replies ---------------------------------------------


def test_a_concatenated_reply_is_reported_as_unreadable_not_as_its_first_piece():
    """Regex extraction stopped at the first quote and matched the fragment.

    ``respond("(PWR!" + str(x) + "\\r")`` came back as ``(PWR!``, which
    matched no response rule and warned — on a driver whose reply is fine.
    """
    literals, unreadable = _extract_respond_calls(
        'respond("(PWR!" + str(state["power"]) + "\\r")\n'
    )
    assert literals == []
    assert unreadable is True


def test_plain_literals_are_read_and_flagged_readable():
    literals, unreadable = _extract_respond_calls(
        "respond('PWR ACK\\r')\nrespond('PWR NAK 04\\r')\n"
    )
    assert literals == ["PWR ACK\r", "PWR NAK 04\r"]
    assert unreadable is False


def test_code_the_simulator_would_refuse_reads_as_unreadable():
    """A bare `return` parses but does not compile, and the simulator drops
    the whole handler when it does not compile — so there is nothing here to
    have an opinion about. _check_handler_syntax is what reports it."""
    literals, unreadable = _extract_respond_calls("return respond('X')\n")
    assert literals == []
    assert unreadable is True
