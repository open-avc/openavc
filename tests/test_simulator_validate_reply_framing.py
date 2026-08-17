"""A simulator reply has to end where the driver splits the stream.

The driver reads replies by splitting incoming bytes on its ``delimiter``, so a
reply that arrives without one never completes a frame and the driver parses
nothing it carries — no state, no liveness answer — while the connection and
every other check look correct.

The simulator terminates the handlers it generates, a query answer, and every
push. The two forms an author writes by hand — a ``respond:`` template and a
``respond()`` call in a handler — go out verbatim, so on those the terminator
is theirs to add, and both are checked here.

None of it is readable off the page (a reply built from a concatenation or an
f-string resolves to nothing static), so the check builds a real simulator and
reads the bytes. These pin both directions — it fires on an unterminated reply
and stays quiet on a terminated one — plus the transports that frame their own
messages and need no terminator at all.
"""

from __future__ import annotations

from openavc.simulator.validate import ValidationResult, _check_reply_framing


def _run(driver_def: dict) -> ValidationResult:
    result = ValidationResult(driver_path="x", driver_id=driver_def["id"], driver_type="yaml")
    _check_reply_framing(
        result,
        driver_def.get("commands", {}),
        (driver_def.get("polling") or {}).get("queries", []),
        driver_def,
    )
    return result


def _driver(handler: str, **over) -> dict:
    """An invented one-command device whose only reply comes from code."""
    base = {
        "id": "acme_widget",
        "name": "Acme Widget",
        "transport": "tcp",
        # What yaml.safe_load hands back for `delimiter: "\r\n"` — the real
        # control characters, not the two-character escape.
        "delimiter": "\r\n",
        "state_variables": {"power": {"type": "integer", "label": "Power"}},
        "responses": [{"match": r"PWR (\d)", "set": {"power": "$1"}}],
        "commands": {"query_power": {"label": "Query Power", "send": "PWR?\r\n"}},
        "simulator": {
            "initial_state": {"power": 1},
            "command_handlers": [{"match": r"PWR\?", "handler": handler}],
        },
    }
    base.update(over)
    return base


def test_a_reply_with_no_terminator_is_an_error():
    result = _run(_driver('respond("PWR " + str(state["power"]))'))
    assert not result.passed
    (issue,) = result.errors
    assert issue.check == "reply_framing"
    # The finding has to carry the delimiter and the offending reply, or the
    # author cannot tell which of their handlers is the one at fault.
    assert "\\r\\n" in issue.message
    assert "PWR 1" in issue.message


def test_a_terminated_reply_passes():
    result = _run(_driver('respond("PWR " + str(state["power"]) + "\\r\\n")'))
    assert result.passed
    assert not result.errors


def test_the_check_reads_a_reply_no_static_pass_could_resolve():
    """The reply is an f-string over live state — nothing to read as source."""
    unframed = _run(_driver('respond(f"PWR {state[\'power\']}")'))
    framed = _run(_driver('respond(f"PWR {state[\'power\']}\\r\\n")'))
    assert not unframed.passed
    assert framed.passed


def test_a_respond_template_is_judged_the_same_way():
    """The other hand-written reply form is also sent verbatim."""
    driver = _driver("")
    driver["simulator"]["command_handlers"] = [{"receive": r"PWR\?", "respond": "PWR 1"}]
    assert not _run(driver).passed

    driver["simulator"]["command_handlers"] = [
        {"receive": r"PWR\?", "respond": "PWR 1\r\n"}
    ]
    assert _run(driver).passed


def test_a_generated_handler_is_terminated_by_the_simulator():
    """A driver with no hand-written handler at all must never be flagged:
    the simulator builds the reply from responses: and frames it itself."""
    driver = _driver("")
    del driver["simulator"]["command_handlers"]
    assert _run(driver).passed


def test_a_datagram_transport_frames_its_own_messages():
    """One message per packet — a terminator is not what completes it."""
    driver = _driver('respond("PWR " + str(state["power"]))', transport="udp")
    assert _run(driver).passed


def test_a_driver_that_declares_no_delimiter_is_not_judged():
    driver = _driver('respond("PWR 1")')
    del driver["delimiter"]
    assert _run(driver).passed


def test_a_handler_that_raises_is_not_a_framing_finding():
    """No reply is no evidence. handler_syntax owns the broken handler."""
    result = _run(_driver('respond(1 / 0)'))
    assert result.passed
    assert not result.errors
