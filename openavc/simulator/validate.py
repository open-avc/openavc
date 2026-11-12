"""
Simulator validation tool — verifies that driver and simulator are compatible.

Catches mismatches between a driver's commands/responses and its simulator
handlers BEFORE you run the simulator. Works with both YAML (.avcdriver)
and Python (.py + _sim.py) drivers.

Usage:
    python -m openavc.simulator.validate path/to/driver.avcdriver
    python -m openavc.simulator.validate path/to/driver.py
    python -m openavc.simulator.validate path/to/drivers/           # validate all
    python -m openavc.simulator.validate path/to/drivers/ --summary  # counts only

Checks performed:

  YAML drivers (.avcdriver):
    1. State coverage   — every state_variable has an initial_state value
    2. Command coverage — every command has a matching simulator handler
                          (OSC drivers: command/poll addresses match handler
                          address patterns, response addresses, or the
                          simulator's built-in system addresses)
    3. Response parsing — the answer to a query the driver polls matches one
                          of the driver's response patterns (a reply to a
                          write is an acknowledgement: counted, not warned)
    4. Poll coverage    — every polling query, and every response rule's
                          then_query, has a matching handler (each_child
                          queries are checked with a sample child id)
    4b. Reply framing   — every reply a real simulator built from this
                          definition sends back ends with the delimiter the
                          driver splits the stream on (byte-stream transports).
                          A reply without it never completes a frame, so the
                          driver parses nothing it carries
    5. Type consistency — boolean/enum/number types are handled correctly
    6. State machines   — simulator.state_machines structure is well-formed
    7. Handler syntax   — inline handler: Python bodies compile
    8. Explicit handlers — set_state: targets exist, {N} capture refs are in
                          range for the receive: pattern
    9. Notifications    — notifications: keys exist, boolean value keys are
                          lowercase, templates round-trip through response
                          patterns, {value} isn't used for booleans
   10. Controls         — simulator.controls entries have valid types,
                          required per-type fields, and known state keys
   11. Child entities   — drivers with child_entity_types get a heads-up that
                          per-child state is modelled only where the command /
                          poll pairing is declared

  Python drivers (.py + _sim.py):
    1. SIMULATOR_INFO   — required fields present (driver_id, name, initial_state)
    2. State coverage   — how much of the device the simulator seeds against
                          how much the driver reads (a count: the two are
                          different namespaces, so the names rarely line up)
    3. driver_id match  — DRIVER_INFO.id == SIMULATOR_INFO.driver_id
"""

from __future__ import annotations

import argparse
import ast
import dataclasses
import logging
import re
import sys
import textwrap
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import yaml

from openavc.drivers.check import check_driver_file, iter_driver_files
from openavc.simulator.capacity import capacity_problems
from openavc.simulator.control_checks import check_controls
from openavc.simulator.handler_checks import STATE_REF, check_handler_code, check_templates
from openavc.simulator.osc_coverage import (
    OscAddresses,
    check_osc_command_coverage,
    check_osc_poll_coverage,
    check_osc_read_back_coverage,
)
from openavc.simulator.single_client import single_client_problems
from openavc.simulator.state_machine_checks import check_state_machines
from openavc.drivers.compiled_protocol import state_var_default
from openavc.simulator.driver_sends import (
    effective_config,
    probed_lines,
)
from openavc.simulator.validation_result import Issue, ValidationResult
from openavc.simulator.reply_reading import (
    ReplyRules,
    frame_delimiter,
)
from openavc.simulator.send_coverage import (
    check_command_coverage,
    check_poll_coverage,
    query_answering_handlers,
)


# ── YAML driver validation ──


def validate_yaml_driver(path: Path) -> ValidationResult:
    """Validate a YAML .avcdriver file and its simulator section."""
    try:
        with open(path, encoding="utf-8") as f:
            driver_def = yaml.safe_load(f)
    except yaml.YAMLError as e:
        result = ValidationResult(str(path), path.stem, "yaml")
        result.error("yaml_parse", f"Failed to parse YAML: {e}")
        return result

    driver_id = driver_def.get("id", path.stem)
    result = ValidationResult(str(path), driver_id, "yaml")

    state_vars = driver_def.get("state_variables", {})
    commands = driver_def.get("commands", {})
    responses = driver_def.get("responses", [])
    polling = driver_def.get("polling", {})
    queries = polling.get("queries", [])
    sim = driver_def.get("simulator", {})
    sim_initial = sim.get("initial_state", {})
    sim_handlers = sim.get("command_handlers", [])
    transport = driver_def.get("transport", "tcp")
    known_keys = _known_state_keys(state_vars, sim_initial, sim)

    # ── Check 1: State variable coverage ──
    _check_state_coverage(result, state_vars, sim_initial, sim)

    if transport == "osc":
        # ── Checks 2+4 (OSC): command / poll address coverage ──
        # OSC commands send an address (+ args), not a text line, and the
        # simulator matches them against handler address patterns, response
        # addresses, and its built-in system addresses.
        addresses = OscAddresses(sim_handlers, responses, driver_def)
        check_osc_command_coverage(result, commands, addresses, driver_def)
        check_osc_poll_coverage(result, queries, commands, addresses)
        check_osc_read_back_coverage(result, commands, addresses)
    else:
        # ── Check 2: Command handler coverage ──
        check_command_coverage(result, commands, sim_handlers, driver_def)

        # ── Check 3: Response parsing (round-trip) ──
        _check_response_parsing(result, commands, sim_handlers, responses, sim_initial, driver_def)

        # ── Check 4: Poll query coverage: the poll, then_query, read_back ──
        check_poll_coverage(result, queries, sim_handlers, driver_def)

        # ── Check 4b: every reply ends where the driver splits the stream ──
        _check_reply_framing(result, commands, queries, driver_def)

    # ── Check 5: Type consistency ──
    _check_type_consistency(result, state_vars, sim_initial, sim_handlers)

    # ── Check 6: State machine structure ──
    check_state_machines(result, sim, commands)

    # ── Check 7: Handler code syntax ──
    check_handler_code(result, sim_handlers)

    # ── Check 8: Explicit handler set_state / capture references ──
    check_templates(result, sim_handlers, known_keys)

    # ── Check 9: Notification templates ──
    _check_notifications(result, sim, driver_def, known_keys)

    # ── Check 10: Declarative controls schema ──
    check_controls(result, sim, known_keys)

    # ── Check 11: Child entity simulation gap ──
    _check_child_entities(result, driver_def, sim)

    # ── Check 12: Declared capacity ──
    if "capacity" in sim:
        _check_capacity(result, sim["capacity"], "simulator.capacity")

    # ── Check 13: One connection at a time ──
    if "single_client" in sim:
        _check_single_client(result, sim["single_client"], "simulator.single_client", transport)

    return result


def _check_capacity(result: ValidationResult, value: Any, where: str) -> None:
    """A declared capacity the simulator would ignore is an error: the
    simulator logs it and falls back to its own numbers."""
    from openavc.drivers.python_info import UNEVALUATED

    if value is UNEVALUATED:
        result.info("capacity", f"{where} is computed, so it could not be checked.")
        return
    for problem in capacity_problems(value):
        result.error("capacity", f"{where}: {problem}")


def _check_single_client(
    result: ValidationResult, value: Any, where: str, transport: Any,
) -> None:
    """A declared single_client the simulator would ignore is an error (a
    mode that does not exist), or a warning (a device that is not TCP)."""
    from openavc.drivers.python_info import UNEVALUATED

    if value is UNEVALUATED:
        result.info("single_client", f"{where} is computed, so it could not be checked.")
        return
    for problem in single_client_problems(value):
        result.error("single_client", f"{where}: {problem}")
    if transport not in (None, "tcp", UNEVALUATED):
        result.warning(
            "single_client",
            f"{where} applies to a TCP device; a {transport} device answers its one connection",
        )


def _known_state_keys(state_vars: dict, sim_initial: dict, sim: dict) -> set[str]:
    """All state keys the simulator will actually have at boot: declared
    state_variables, explicit initial_state entries, and state machine names
    (each machine stores its current state under its own name)."""
    keys = set(state_vars) | set(sim_initial)
    machines = sim.get("state_machines")
    if isinstance(machines, dict):
        keys |= set(machines)
    return keys


def _check_state_coverage(
    result: ValidationResult,
    state_vars: dict,
    sim_initial: dict,
    sim: dict,
) -> None:
    """Check every state_variable has an initial_state value in the simulator."""
    if not sim:
        # No simulator section at all — auto-gen handles initial state.
        # Still check that state_variables exist.
        if not state_vars:
            result.warning("state_coverage", "No state_variables defined")
        return

    for var_name, var_def in state_vars.items():
        if var_name not in sim_initial:
            # Check what auto-gen will produce for the missing variable.
            var_type = var_def.get("type", "string")
            if var_type == "integer":
                default = var_def.get("min", 0)
            elif var_type == "number":
                default = 0.0
            elif var_type == "boolean":
                default = False
            elif var_type == "enum":
                values = var_def.get("values", [])
                default = values[0] if values else ""
            else:
                default = ""
            # The simulator section merges per-key into the auto-generated
            # initial state (see yaml_auto._merge_simulator_section), so the
            # variable is fine — it'll start with the auto-gen default. This
            # is purely informational: surface the value the simulator will
            # actually boot with so authors can spot and override anything
            # inappropriate.
            result.info(
                "state_coverage",
                f"'{var_name}' will use auto-gen default {default!r}. "
                f"Add to simulator.initial_state to override."
            )


# How many unseeded variable names the grouped state-coverage line lists.
_STATE_EXAMPLES = 6


def _check_python_state_coverage(
    result: ValidationResult,
    driver_state_vars: dict,
    sim_initial: dict,
) -> None:
    """Compare a Python driver's state variables against its simulator's seeds.

    Reported as one counted line, not as a warning per name, because the two
    sides are **different namespaces** — the same reason
    ``_check_initial_state_types`` reports at info, and the same reason the
    YAML half of this check was downgraded years earlier.

    ``SIMULATOR_INFO.initial_state`` seeds what the device puts on the wire.
    ``DRIVER_INFO.state_variables`` are what the driver publishes after
    reading it, and a driver that does any work at all between the two has
    names that cannot match. Measured over the shipped corpus on 2026-07-31,
    this check produced 268 warnings and every one inspected was that:

      viewsonic_cde   sim seeds power_code / mute_code / freeze_code /
                      hub_temp; driver publishes power / mute / freeze /
                      amb_temperature_c. 21 warnings, 21 correct pairs.
      pjlink_class1   sim answers the lamp query from one lamp_hours key;
                      the driver fans it into lamp1_hours..lamp8_hours plus
                      six error_* flags. 17 warnings, nothing to seed.
      epson_escvp     sim seeds the ESC/VP command tokens themselves (PWR,
                      LAMP, ERR); driver publishes power_state, lamp_hours,
                      error_status. 11 warnings, zero name overlap, correct.

    Telling those apart from a simulator that genuinely models nothing needs
    the driver's parsing code, which is not readable here — and every
    threshold that looked promising (name overlap, seeds-per-variable ratio)
    put pjlink_class1 on the wrong side of it. So the numbers are stated and
    the judgement is left with the author, which is the honest shape: a
    simulator seeding 3 keys against 33 state variables is visible in the
    line without the tool having to assert what it cannot know.

    The one case worth a finding needs no threshold: a simulator that seeds
    nothing at all while the driver declares state. There is no naming
    convention under which that models the device.
    """
    unseeded = [
        name
        for name in driver_state_vars
        if isinstance(name, str) and name not in sim_initial
    ]
    if not unseeded:
        return

    if not sim_initial:
        result.warning(
            "state_coverage",
            f"SIMULATOR_INFO seeds no initial_state at all, but the driver "
            f"declares {len(driver_state_vars)} state variable(s) — the "
            f"simulator models none of this device, so nothing it answers "
            f"can exercise the driver's parsing."
        )
        return

    shown = ", ".join(unseeded[:_STATE_EXAMPLES])
    rest = len(unseeded) - _STATE_EXAMPLES
    if rest > 0:
        shown += f" (+{rest} more)"
    result.info(
        "state_coverage",
        f"SIMULATOR_INFO seeds {len(sim_initial)} state key(s); "
        f"{len(unseeded)} of the driver's {len(driver_state_vars)} state "
        f"variable(s) are not among them BY NAME: {shown}. Usually correct — "
        f"the simulator seeds what goes on the wire and the driver publishes "
        f"what it computes from that, so the names differ. Worth a look only "
        f"if the counts say the simulator models far less than the driver "
        f"reads."
    )


# How many distinct acknowledgement-shaped responses the grouped info names
# before it stops listing. The count is always exact; the examples are there
# to recognise the shape, not to enumerate it.
_ACK_EXAMPLES = 4


def _check_response_parsing(
    result: ValidationResult,
    commands: dict,
    sim_handlers: list,
    responses: list,
    sim_initial: dict,
    driver_def: dict,
) -> None:
    """Check that simulator responses match driver response patterns.

    This is the round-trip check: we simulate what the handler would respond
    with, then verify the response matches at least one response pattern.

    A reply that matches nothing is only a *finding* when the driver asked a
    question and got no answer it can read. On a protocol that acknowledges
    every write — ``<cmd> ACK`` / ``<cmd> NAK <code>``, which is most of the
    AV serial and TCP world — the acknowledgement carries no state by
    construction, so no response rule exists or should. Warning on each one
    buried the real findings: the Audio-Technica mixers reported 40-odd
    apiece, and 324 of the 597 warnings over the whole shipped corpus were
    this one check firing on correct drivers. Same reasoning as the
    ``command_coverage`` downgrade below — a check that fires permanently on
    correct drivers trains an author to skim the channel that might say
    something real.

    So the finding is stated per *handler*, in terms of what the driver does
    rather than what the reply looks like — no keyword list, no per-protocol
    allowance for "ACK":

    * The handler answers a ``polling.queries`` entry (or an ``on_connect``
      entry that declares ``query_for``), and **none** of its replies matches
      a response rule. The driver polls that query to learn a value and can
      read nothing that comes back: the state variable behind it stays at its
      startup value forever. Warning.
    * Anything else: a reply to a write, or an error branch of a query
      handler whose real answer does parse. Counted and shown once, so "not
      checked" is still visible rather than implied.

    ``on_connect`` is deliberately not treated as a query list. It is a
    bring-up sequence that mixes reads with arming writes — arming push
    notifications, setting a mode — and those writes are acknowledged like
    any other. An entry that declares ``query_for`` has said it is a read.
    """
    if not responses:
        return

    rules = _reply_rules(responses, driver_def)
    answering = query_answering_handlers(sim_handlers, driver_def)
    acknowledgements: list[str] = []

    for handler_def in _text_handlers(sim_handlers):
        replies = _read_handler_replies(handler_def, sim_initial, rules)
        if not replies.unread:
            continue
        if id(handler_def) in answering and replies.none_read:
            _warn_unread_answer(result, handler_def, replies.unread[0])
        else:
            _add_unseen(acknowledgements, replies.unread)

    if acknowledgements:
        _report_acknowledgements(result, acknowledgements)


def _text_handlers(sim_handlers: list) -> Iterator[dict]:
    """The handlers that answer with text lines: OSC handlers (``address:``)
    respond with (address, args) tuples, and a malformed entry is
    ``handler_checks.check_handler_code``'s to report."""
    for handler_def in sim_handlers:
        if isinstance(handler_def, dict) and "address" not in handler_def:
            yield handler_def


def _add_unseen(seen: list[str], items: list[str]) -> None:
    """Add each item ``seen`` does not hold yet, in order."""
    for item in items:
        if item not in seen:
            seen.append(item)


# A placeholder still in a reply after its {state.key} refs are filled: a
# capture group ref ({1}, {2}) on a respond: template.
_CAPTURE_REF = re.compile(r"\{\d+\}")


def _left_unresolved(response_text: str, scripted: bool) -> bool:
    """A reply still holding a capture group ref, or (a script handler's
    literal) any ``{...}``, which this cannot resolve."""
    if _CAPTURE_REF.search(response_text):
        return True
    return scripted and _has_braces(response_text)


def _has_braces(text: str) -> bool:
    return "{" in text and "}" in text


@dataclasses.dataclass
class _HandlerReplies:
    """What one text handler answers, as the driver reads it."""

    #: Each reply line no response rule reads, once, in the order sent.
    unread: list[str] = dataclasses.field(default_factory=list)
    #: Whether a response rule reads any line it sends.
    any_read: bool = False
    #: Whether a reply could not be resolved here (built from a capture
    #: group, an f-string or a concatenation). The answer to a query is
    #: nearly always one of those, and it is not evidence either way.
    any_unresolvable: bool = False

    def take(self, response_text: str, rules: ReplyRules) -> None:
        """One resolved reply, split into the lines the driver reads."""
        for line in rules.lines(response_text):
            if rules.reads(line):
                self.any_read = True
            elif line not in self.unread:
                self.unread.append(line)

    @property
    def none_read(self) -> bool:
        """Whether "none of its replies parses" may be said of the handler:
        no reply is read, and none could not be resolved."""
        return not self.any_read and not self.any_unresolvable


def _reply_templates(handler_def: dict) -> tuple[list[str], bool]:
    """The reply templates a text handler sends, and whether one of them
    could not be read: a ``respond:`` template, else the literals a script
    ``handler:`` passes to ``respond()``, else none."""
    respond_template = handler_def.get("respond")
    handler_code = handler_def.get("handler")
    if respond_template:
        return [respond_template], False
    if handler_code:
        return _extract_respond_calls(handler_code)
    return [], False


def _read_handler_replies(
    handler_def: dict, sim_initial: dict, rules: ReplyRules
) -> _HandlerReplies:
    """Each reply a handler sends, its ``{state.key}`` refs filled from the
    initial state, split into the lines the driver reads and matched against
    its rules. A reply still holding a capture group ref, or (a script
    handler's literal) any ``{...}``, is unresolvable rather than read."""
    templates, unresolvable = _reply_templates(handler_def)
    replies = _HandlerReplies(any_unresolvable=unresolvable)
    scripted = bool(handler_def.get("handler"))
    for template in templates:
        response_text = _resolve_state_refs(template, sim_initial)
        if _left_unresolved(response_text, scripted):
            replies.any_unresolvable = True
        else:
            replies.take(response_text, rules)
    return replies


def _warn_unread_answer(result: ValidationResult, handler_def: dict, reply: str) -> None:
    """The finding: a handler answering a query the driver asks with nothing
    a response rule reads."""
    pattern_key = handler_def.get("receive") or handler_def.get("match", "?")
    result.warning(
        "response_parsing",
        f"Simulator answers a query this driver polls with a reply no "
        f"response rule matches: handler={pattern_key!r}, "
        f"response={reply!r}. Nothing reads that answer, so "
        f"the state behind the query never updates."
    )


def _report_acknowledgements(result: ValidationResult, acknowledgements: list[str]) -> None:
    """The replies no rule reads that answer no polled query, counted once:
    the shape of a write acknowledgement."""
    shown = ", ".join(repr(a) for a in acknowledgements[:_ACK_EXAMPLES])
    rest = len(acknowledgements) - _ACK_EXAMPLES
    if rest > 0:
        shown += f" (+{rest} more)"
    result.info(
        "response_parsing",
        f"{len(acknowledgements)} simulator reply/replies match no driver "
        f"response rule, none of them the only answer to a polled query — "
        f"the shape of a write acknowledgement, which carries no state: "
        f"{shown}. A polled query whose answer nothing parses warns "
        f"instead."
    )


def _check_initial_state_types(
    result: ValidationResult,
    state_vars: dict,
    sim_initial: dict,
) -> None:
    """Compare each declared state variable's type against its seeded value.

    Shared by the YAML and Python paths so the two cannot drift apart again.

    Reports at ``info``, never as a finding, because the two sides are not the
    same namespace. A state variable's declared type is the *logical* value the
    driver publishes; ``initial_state`` seeds the *wire* value the simulator
    puts on the socket, and the driver's own response rules map one to the
    other. Whenever a protocol's wire form differs from the logical form the
    two legitimately disagree, and that is the only time this check can fire at
    all — an auto-generated seed is built from the declared type by
    ``yaml_auto._build_info`` and matches by construction, so the check only
    ever sees a hand-written override, which authors write precisely because
    the wire form differs.

    Measured over the shipped corpus on 2026-07-31: 26 errors across 9 drivers,
    every one of them a correct wire value. ``at_atdm_0604`` seeds
    ``at_link_mode: 0`` because its simulator interpolates that straight into
    ``g_link 0000 00 NC {state["at_link_mode"]}`` and the driver parses it back
    with ``match: 'g_link 0000 \\S+ NC 0'``; seeding the declared ``false``
    would put ``NC False`` on the wire and match neither response rule.
    ``lg_webos`` seeds the SSAP string ``"Active"`` for a ``[off, standby, on]``
    enum for the same reason. Making these "pass" means breaking them.

    The round-trip that *can* be checked is ``_check_response_parsing``, which
    resolves a seed through the simulator's respond template and matches the
    result against the driver's own response patterns — the wire namespace,
    where the seeds actually live.

    Same reasoning as the ``command_coverage`` downgrade below: a check that
    fires permanently on correct drivers trains an author to ignore the one
    channel that might say something real.
    """
    if not isinstance(state_vars, dict) or not isinstance(sim_initial, dict):
        return

    for var_name, var_def in state_vars.items():
        if not isinstance(var_name, str) or not isinstance(var_def, dict):
            continue
        if var_name not in sim_initial:
            continue
        var_type = var_def.get("type", "string")
        initial = sim_initial[var_name]

        if var_type == "boolean" and not isinstance(initial, bool):
            result.info(
                "type_consistency",
                f"State variable '{var_name}' is boolean but initial_state "
                f"value is {type(initial).__name__}: {initial!r}. Expected when "
                f"the wire form differs from the logical type — confirm the "
                f"driver's response rules map it back."
            )

        # bool is a subclass of int, so True would satisfy an integer
        # declaration without this — a false pass on the one confusion this
        # check exists to catch.
        if var_type == "integer" and (
            isinstance(initial, bool) or not isinstance(initial, int)
        ):
            result.info(
                "type_consistency",
                f"State variable '{var_name}' is integer but initial_state "
                f"value is {type(initial).__name__}: {initial!r}. Expected when "
                f"the wire form differs from the logical type — confirm the "
                f"driver's response rules map it back."
            )

        if var_type in ("number", "float") and (
            isinstance(initial, bool) or not isinstance(initial, (int, float))
        ):
            result.info(
                "type_consistency",
                f"State variable '{var_name}' is {var_type} but initial_state "
                f"value is {type(initial).__name__}: {initial!r}. Expected when "
                f"the wire form differs from the logical type — confirm the "
                f"driver's response rules map it back."
            )

        if var_type == "enum":
            valid_values = var_def.get("values", [])
            if not isinstance(valid_values, list):
                # Declared, but built from a constant or a call, so there is
                # nothing to compare against. Say so — a silent skip here
                # reads exactly like a pass.
                result.info(
                    "type_consistency",
                    f"State variable '{var_name}' is an enum whose values are "
                    f"computed, so its initial value {initial!r} could not be "
                    f"checked against them."
                )
            elif valid_values and str(initial) not in [str(v) for v in valid_values]:
                result.info(
                    "type_consistency",
                    f"State variable '{var_name}' initial value {initial!r} "
                    f"not in enum values: {valid_values}. Expected when the wire "
                    f"form differs from the logical type — confirm the driver's "
                    f"response rules map it back."
                )


def _check_type_consistency(
    result: ValidationResult,
    state_vars: dict,
    sim_initial: dict,
    sim_handlers: list,
) -> None:
    """Check for common type consistency issues.

    One implementation for both driver formats. There used to be two — this
    one for YAML and a second inside the Python path — and they had drifted:
    the Python copy checked ``boolean`` and ``enum`` only, so a string sitting
    in an ``integer`` state variable went unreported for every Python driver
    while the same mistake was caught in a YAML one. Neither copy covered
    ``number`` or ``float``, which are both declarable types.
    """
    _check_initial_state_types(result, state_vars, sim_initial)

    # Check for boolean state refs in respond: templates that might produce
    # capitalized Python booleans (True/False instead of true/false)
    for handler_def in sim_handlers:
        if not isinstance(handler_def, dict):
            continue
        respond = handler_def.get("respond", "")
        if not respond:
            continue
        # Find {state.X} references where X is a boolean variable
        for match in STATE_REF.finditer(respond):
            ref_name = match.group(1)
            if ref_name in state_vars:
                if state_vars[ref_name].get("type") == "boolean":
                    result.error(
                        "type_consistency",
                        f"Handler uses {{state.{ref_name}}} in respond: template, "
                        f"but '{ref_name}' is boolean. Python's str(True) produces "
                        f"'True' not 'true'. Use a script handler with "
                        f"str(state[\"{ref_name}\"]).lower() instead."
                    )


# Transports the simulator serves with a server of its own — one module each
# under openavc/simulator/ (tcp_simulator, http_simulator, udp_simulator, osc_simulator,
# mqtt_simulator, websocket_simulator, snmp_simulator). Anything else is a raw byte pipe with no
# simulator server, so it is simulated over TCP instead. That substitution is
# the platform's own: SimulationManager._apply_sim_redirect flips a serial
# device's transport to tcp for the duration of a simulation run precisely
# because "the simulator has no serial server"
# (openavc/core/simulation.py::_driver_transport_is_serial). A driver declaring
# `serial` or `ssh` beside a `tcp` simulator is therefore correct, and erroring
# on it asks the author to break a working simulator to satisfy a string
# comparison.
_SIMULATOR_SERVED_TRANSPORTS = frozenset(
    {"tcp", "http", "udp", "osc", "mqtt", "websocket", "snmp"}
)


# How many unframed replies the finding quotes. The count is always exact; the
# examples are there to recognise the shape, not to enumerate it.
_FRAMING_EXAMPLES = 3


def _check_reply_framing(
    result: ValidationResult,
    commands: dict,
    queries: list,
    driver_def: dict,
) -> None:
    """Check every simulator reply ends where the driver splits the stream.

    A driver on a delimited transport reads replies by splitting incoming
    bytes on its ``delimiter``. A reply that arrives without one never
    completes a frame, so the driver parses **nothing at all** — no state, no
    liveness answer — while the simulator, the connection, and every other
    check in this file look correct. That is the whole failure: the simulator
    is right about the protocol and unreadable anyway.

    Which replies carry it is not uniform, and that is the trap. The simulator
    appends the delimiter to the handlers it generates from ``commands`` /
    ``responses``, to a query handler's answer, and to every push it sends. The
    two reply forms an author writes by hand — an explicit ``respond:``
    template and a script ``handler:`` calling ``respond()`` — are returned
    **verbatim**, so on those the terminator is theirs to add. Nothing else
    here can see it missing, because a reply that never arrives contradicts no
    response rule: ``response_parsing`` compares reply text against the
    driver's patterns, and a frame that never completes has no text to compare.

    A reply whose lines end in the delimiter and which trails text after the
    last one is read, all but that text, and is judged apart
    (``_report_trailing_text``): a prompt the device prints after each reply
    is its protocol, anything else is a line read one reply late.

    So this check asks the simulator instead of reading it. It feeds the
    driver's own liveness, poll and command strings to a real simulator built
    from this definition and looks at the bytes that come back — which is also
    why it sees replies the page cannot: one built from a capture group or an
    f-string is unresolvable as source and perfectly concrete as output.
    """
    delimiter = frame_delimiter(driver_def)
    if delimiter is None:
        return
    raw_delimiter = driver_def.get("delimiter")

    # Built lazily: importing the simulator pulls in its server bases, and a
    # caller who only wants the other checks should not pay for them.
    try:
        from openavc.simulator.yaml_auto import YAMLAutoSimulator
    except Exception:  # pragma: no cover - the simulator package is shipped
        return

    probes = probed_lines(commands, queries, driver_def)
    if not probes:
        return
    heard = _ask_the_simulator(
        lambda: YAMLAutoSimulator(
            driver_def.get("id", result.driver_id), {}, driver_def=driver_def
        ),
        probes,
        delimiter,
    )
    if heard is not None:
        _report_framing(result, heard, raw_delimiter, driver_def)


@dataclasses.dataclass
class _Heard:
    """What the simulator sent back to the probes, judged against the
    delimiter."""

    #: Probes answered with something.
    replies: int = 0
    #: Each reply with no delimiter in it at all, as an example.
    unframed: list[str] = dataclasses.field(default_factory=list)
    #: Each reply whose lines end in the delimiter but which carries something
    #: after the last one: (example, the trailing text, whether the reply
    #: also opens with the delimiter).
    trailing: list[tuple[str, bytes, bool]] = dataclasses.field(default_factory=list)

    def take(self, probe: str, reply: bytes, terminator: bytes) -> None:
        """One probe's non-empty reply."""
        self.replies += 1
        if reply.endswith(terminator):
            return
        example = f"{probe.strip()!r} -> {reply[:40]!r}"
        if terminator in reply:
            self.trailing.append(
                (example, reply.rsplit(terminator, 1)[1], reply.startswith(terminator))
            )
        else:
            self.unframed.append(example)


def _ask_the_simulator(
    build: Callable[[], Any], probes: list[str], delimiter: str,
) -> _Heard | None:
    """Each probe's reply from a simulator ``build`` makes, or None when it
    cannot be built (an unbuildable simulator is the other checks' finding).

    The simulator's log is muted while it runs. A handler that raises logs a
    traceback, and a driver whose sample values do not suit every command has
    a few. They are ``handler_syntax``'s to report, not this check's, and a
    wall of them would bury its finding.
    """
    sim_log = logging.getLogger("openavc.simulator.yaml_auto")
    previous_level = sim_log.level
    sim_log.setLevel(logging.CRITICAL)
    try:
        try:
            sim = build()
        except Exception:
            return None
        terminator = delimiter.encode()
        heard = _Heard()
        for probe in probes:
            try:
                reply = sim.handle_command(probe.encode())
            except Exception:
                continue
            if reply:
                heard.take(probe, reply, terminator)
        return heard
    finally:
        sim_log.setLevel(previous_level)


def _report_framing(
    result: ValidationResult, heard: _Heard, raw_delimiter: str, driver_def: dict,
) -> None:
    """Replies that trail text after their last delimiter are judged apart;
    replies with no delimiter at all are the error; otherwise, when every
    reply ends in it, the count is noted."""
    if heard.trailing:
        _report_trailing_text(result, heard.trailing, raw_delimiter, driver_def)
    if heard.unframed:
        _report_unframed(result, heard, raw_delimiter)
    elif heard.replies and not heard.trailing:
        replies = heard.replies
        result.info(
            "reply_framing",
            f"{replies} simulator repl{'y' if replies == 1 else 'ies'} checked, "
            f"all terminated with {raw_delimiter!r}."
        )


def _report_unframed(result: ValidationResult, heard: _Heard, raw_delimiter: str) -> None:
    """The error: replies that never complete a frame."""
    unframed = heard.unframed
    shown = "; ".join(unframed[:_FRAMING_EXAMPLES])
    more = len(unframed) - _FRAMING_EXAMPLES
    if more > 0:
        shown += f"; +{more} more"
    result.error(
        "reply_framing",
        f"{len(unframed)} of {heard.replies} simulator replies come back without the "
        f"{raw_delimiter!r} this driver splits incoming bytes on, so the driver "
        f"never completes a frame and reads nothing they carry: {shown}. "
        f"A reply written by hand — a respond: template or a respond() call in "
        f"a handler — is sent verbatim, so end it with the delimiter; only the "
        f"handlers the simulator generates are terminated for you."
    )


def _report_trailing_text(
    result: ValidationResult,
    trailing: list[tuple[str, bytes, bool]],
    raw_delimiter: str,
    driver_def: dict,
) -> None:
    """Judge replies that end with text after their last delimiter.

    Every line before that text is read at once; the text itself waits for
    the next delimiter. A device that prints a prompt after each reply
    (``volume=55`` CR LF ``>``) does exactly this, and when every reply also
    opens with the delimiter, the next reply completes the prompt as a line of
    its own, which a response rule can read (or acknowledge with ``set: {}``).
    That is the device's protocol, so it is noted, not flagged. Anything else
    is a reply line that lost its delimiter: it is read one reply late, joined
    to the start of the next one.
    """
    rules = ReplyRules.regex_only(
        driver_def.get("responses") or [], effective_config(driver_def)
    )
    shown = "; ".join(example for example, _, _ in trailing[:_FRAMING_EXAMPLES])
    more = len(trailing) - _FRAMING_EXAMPLES
    if more > 0:
        shown += f"; +{more} more"
    texts = sorted({text.decode("utf-8", errors="replace").strip() for _, text, _ in trailing})

    def read_alone(text: bytes) -> bool:
        line = text.decode("utf-8", errors="replace").strip()
        return rules.matches_a_pattern(line)

    if all(opens and read_alone(text) for _, text, opens in trailing):
        result.info(
            "reply_framing",
            f"{len(trailing)} simulator repl{'y ends' if len(trailing) == 1 else 'ies end'} "
            f"with {', '.join(repr(t) for t in texts)} after the last {raw_delimiter!r}, "
            f"as a device that prints a prompt after each reply does; every reply "
            f"opens with {raw_delimiter!r}, so the next one completes it as a line "
            f"a response rule reads: {shown}."
        )
        return
    result.warning(
        "reply_framing",
        f"{len(trailing)} simulator repl{'y ends' if len(trailing) == 1 else 'ies end'} "
        f"with text after the last {raw_delimiter!r}, which reaches the driver only "
        f"when the next reply arrives, joined to its first line: {shown}. End the "
        f"reply with the delimiter, or, for a prompt the device prints after each "
        f"reply, open every reply with the delimiter and give the prompt a "
        f"response rule of its own."
    )


# ── Simulator-section checks (notifications, children) ──


def _default_for_type(var_def: dict) -> Any:
    """The auto-gen initial value for a state variable.

    Calls the same rule the simulator seeds with rather than restating it —
    the old copy claimed to mirror yaml_auto and did not, so a driver
    declaring a fractional integer ``min`` got a round-trip warning for a
    value the simulator would never have produced.
    """
    return state_var_default(var_def)


def _check_notifications(
    result: ValidationResult, sim: dict, driver_def: dict, known_keys: set[str],
) -> None:
    """Validate the notifications: section (unsolicited push messages).

    Catches notification keys that aren't state keys (never fires), boolean
    value keys that can't match (lookup uses lowercase 'true'/'false'),
    {value} templates on boolean variables (emit Python's 'True'/'False'),
    and templates whose output no driver response pattern parses.
    """
    notifications = sim.get("notifications")
    if notifications is None:
        return
    if not isinstance(notifications, dict):
        result.error("notifications", "simulator.notifications must be a mapping")
        return
    if not notifications:
        return

    transport = driver_def.get("transport", "tcp")
    if not _notifications_reach_driver(driver_def, transport):
        result.warning(
            "notifications",
            f"notifications: has no effect for transport '{transport}' — only "
            f"TCP, serial and UDP simulators push notification messages on "
            f"the control link (unless the driver declares a multicast, SSE, "
            f"TCP dial-back, or HTTP-listener push: block)"
        )

    check = _NotificationCheck(
        result=result,
        state_vars=driver_def.get("state_variables", {}),
        sim_initial=sim.get("initial_state", {}),
        known_keys=known_keys,
        rules=_reply_rules(driver_def.get("responses", []), driver_def),
    )
    for key, value_map in notifications.items():
        check.entry(key, value_map)


# The push channels the simulator sends a notification on whatever the control
# transport (push_channels._PUSH_CHANNELS); sse is not among them, because its
# stream rides the control session and so needs HTTP.
_PUSH_ON_ANY_TRANSPORT = ("multicast", "tcp_listener", "http_listener", "udp_listener")

# Transports whose simulator pushes on the control link itself: TCP and serial
# to every connected client, a UDP device to the last peer it heard from. HTTP
# and OSC have no control-link push.
_CONTROL_LINK_PUSH = ("tcp", "serial", "udp")


def _push_carries_notifications(driver_def: dict, transport: Any) -> bool:
    """Whether the driver declares a push channel the simulator emits its
    notifications on instead of the control connection."""
    push_def = driver_def.get("push")
    if not isinstance(push_def, dict):
        return False
    kind = push_def.get("type")
    return kind in _PUSH_ON_ANY_TRANSPORT or (kind == "sse" and transport == "http")


def _notifications_reach_driver(driver_def: dict, transport: Any) -> bool:
    """Whether a ``notifications:`` message can reach the driver at all: on a
    declared push channel, or on a transport that pushes on its control link."""
    return _push_carries_notifications(driver_def, transport) or (
        transport in _CONTROL_LINK_PUSH
    )


def _is_boolean_word(value: Any) -> bool:
    """The lookup's 'true' / 'false', which a boolean renders as the runtime's bool."""
    return isinstance(value, str) and value in ("true", "false")


# A placeholder the renderer left in a message, which cannot be validated
# statically. A brace followed by a quote is JSON, not a placeholder, so a
# rendered JSON notification is still read below.
_UNRESOLVED_PLACEHOLDER = re.compile(r"\{[a-zA-Z_][\w.]*(:[^}]*)?\}")


@dataclasses.dataclass
class _NotificationCheck:
    """One ``notifications:`` section's entries: each key and its value map,
    each template's value key, and whether the driver reads the message a
    template renders."""

    result: ValidationResult
    state_vars: dict
    sim_initial: dict
    known_keys: set[str]
    rules: ReplyRules

    def entry(self, key: Any, value_map: Any) -> None:
        """One state key's notification: a template string (for any value)
        or a value -> template mapping."""
        if self.known_keys and key not in self.known_keys:
            self.result.warning(
                "notifications",
                f"notification key '{key}' is not a state variable or "
                f"initial_state key — it only fires if a handler writes that key"
            )

        var_type = self.state_vars.get(key, {}).get("type", "string")

        if isinstance(value_map, str):
            value_map = {"*": value_map}
        elif not isinstance(value_map, dict):
            self.result.error(
                "notifications",
                f"notification '{key}' must be a template string or a "
                f"value → template mapping, got: {value_map!r}"
            )
            return

        for trigger_value, template in value_map.items():
            self._template(key, var_type, str(trigger_value), str(template))

    def _template(self, key: Any, var_type: Any, trigger: str, template: str) -> None:
        """One template, under the value key that selects it (``*`` for any)."""
        self._value_key(key, var_type, trigger)
        if var_type == "boolean" and "{value}" in template:
            self.result.error(
                "notifications",
                f"notification '{key}' template uses {{value}} but '{key}' is "
                f"boolean — Python's str(True) produces 'True' not 'true'. Use "
                f"per-value templates ('true': ..., 'false': ...) instead."
            )
            return
        if self.rules.any:
            self._round_trip(key, var_type, trigger, template)

    def _value_key(self, key: Any, var_type: Any, trigger: str) -> None:
        """A value key the lookup can never select."""
        if var_type == "boolean" and trigger not in ("true", "false", "*"):
            self.result.error(
                "notifications",
                f"notification '{key}' value key {trigger!r} never matches — "
                f"boolean lookups use lowercase 'true'/'false' (quote the keys "
                f"in YAML so they stay strings)"
            )
        if var_type == "enum" and trigger != "*":
            valid = [str(v) for v in self.state_vars[key].get("values", [])]
            if valid and trigger not in valid:
                self.result.warning(
                    "notifications",
                    f"notification '{key}' value key {trigger!r} is not one of "
                    f"the enum values {valid}"
                )

    def _sample_value(self, key: Any, var_type: Any, trigger: str) -> Any:
        """The value a template is rendered with: under ``*``, the key's
        initial state (or the type's starting value); else the value key, a
        boolean's 'true'/'false' as the runtime's bool."""
        if trigger == "*":
            if key in self.sim_initial:
                sample_value = self.sim_initial[key]
            else:
                sample_value = _default_for_type(self.state_vars.get(key, {}))
        else:
            sample_value = trigger
        if var_type == "boolean" and _is_boolean_word(sample_value):
            return sample_value == "true"
        return sample_value

    def _round_trip(self, key: Any, var_type: Any, trigger: str, template: str) -> None:
        """Render the template with the runtime's own renderer, so format specs
        ({value:d} on a boolean) validate the way they will emit, and warn
        when no response rule reads the message."""
        sample_value = self._sample_value(key, var_type, trigger)
        from openavc.simulator.yaml_auto import YAMLAutoSimulator

        message = YAMLAutoSimulator._render_notification(
            template, key, sample_value, state=self.sim_initial
        )
        if _UNRESOLVED_PLACEHOLDER.search(message):
            return
        # A template wrapped in CR/LF (dial-back containers embed the payload
        # as "\r\n<response>\r\n") or ending in the driver's own ">" is
        # read the way the runtime dispatches pushed data.
        if not self.rules.reads_pushed(message):
            self.result.warning(
                "notifications",
                f"notification '{key}' emits {message!r} which matches no "
                f"driver response pattern — the driver will ignore it"
            )


def _check_child_entities(result: ValidationResult, driver_def: dict, sim: dict) -> None:
    """Surface the child-entity simulation gap.

    The simulator carries the child roster (labels/config, shown in the UI)
    but does not auto-generate per-child state or responses from
    child_entity_types — that behavior must be authored as explicit
    simulator handlers.
    """
    child_types = driver_def.get("child_entity_types")
    if not isinstance(child_types, dict) or not child_types:
        return
    names = ", ".join(sorted(child_types))
    if not sim:
        result.warning(
            "child_entities",
            f"driver declares child entity types ({names}) but has no "
            f"simulator: section — per-child state is modelled from the "
            f"declarations, so seed each child's starting values under "
            f"simulator.initial_state with dotted keys (e.g. output.2.input)."
        )
    else:
        result.info(
            "child_entities",
            f"driver declares child entity types ({names}) — per-child state "
            f"is modelled from the declarations, but only where the pairing is "
            f"declared: a child_id command needs sets: / query_for:, and an "
            f"each_child poll query needs query_for:. Anything without one "
                f"needs its own command_handlers entry."
        )


# ── Python driver validation ──


def validate_python_driver(driver_path: Path) -> ValidationResult:
    """Validate a Python driver and its companion _sim.py simulator."""
    from openavc.drivers.python_info import ExtractError
    from openavc.simulator.scaffold import extract_driver_info

    # The reader refuses a file it cannot honestly read — a syntax error, or a
    # DRIVER_INFO that is not a class attribute (the runtime loads it off a
    # BaseDriver subclass, so a module-level dict of that name is a driver
    # that will never load). Report that as a finding rather than letting it
    # come out of a validator as a traceback.
    try:
        driver_info = extract_driver_info(driver_path)
    except ExtractError as exc:
        result = ValidationResult(str(driver_path), driver_path.stem, "python")
        result.error("driver_info", str(exc))
        return result
    except SyntaxError as exc:
        result = ValidationResult(str(driver_path), driver_path.stem, "python")
        result.error("driver_info", f"{driver_path.name} does not parse: {exc}")
        return result

    driver_id = driver_info.get("id", driver_path.stem) if driver_info else driver_path.stem
    if not isinstance(driver_id, str):
        driver_id = driver_path.stem
    result = ValidationResult(str(driver_path), driver_id, "python")

    if not driver_info:
        result.error("driver_info", "Could not extract DRIVER_INFO from driver file")
        return result

    # Find companion simulator file
    sim_path = driver_path.parent / f"{driver_path.stem}_sim.py"
    if not sim_path.exists():
        result.warning("simulator_file", f"No simulator file found at {sim_path.name}")
        return result

    # Extract SIMULATOR_INFO from the simulator file
    sim_info = _extract_simulator_info(sim_path)
    if not sim_info:
        result.error("simulator_info", "Could not extract SIMULATOR_INFO from simulator file")
        return result

    # Check required SIMULATOR_INFO fields
    for field in ("driver_id", "name", "initial_state"):
        if field not in sim_info:
            result.error("simulator_info", f"SIMULATOR_INFO missing required field: '{field}'")

    if "capacity" in sim_info:
        _check_capacity(result, sim_info["capacity"], 'SIMULATOR_INFO["capacity"]')
    if "single_client" in sim_info:
        _check_single_client(
            result, sim_info["single_client"], 'SIMULATOR_INFO["single_client"]',
            driver_info.get("transport"),
        )

    # Check driver_id match
    if driver_info.get("id") and sim_info.get("driver_id"):
        if driver_info["id"] != sim_info["driver_id"]:
            result.error(
                "driver_id_match",
                f"DRIVER_INFO id ({driver_info['id']!r}) != "
                f"SIMULATOR_INFO driver_id ({sim_info['driver_id']!r})"
            )

    # Check state variable coverage. A value the reader could not resolve
    # statically arrives as the UNEVALUATED marker rather than a dict, and it
    # is named rather than skipped — the old reader's regex fallback always
    # produced *something*, so a computed block used to be checked against an
    # invention instead of reported as unreadable.
    driver_state_vars = driver_info.get("state_variables", {})
    sim_initial = sim_info.get("initial_state", {})
    sim_initial_readable = isinstance(sim_initial, dict)
    if not sim_initial_readable:
        # The mirror of the driver-side note below, which this side was
        # missing: a seed block built from a module constant
        # (`dict(DEFAULT_DISPLAY)`) collapsed silently to {} here, and an
        # unreadable block then looked exactly like an empty one.
        result.info(
            "state_coverage",
            "SIMULATOR_INFO initial_state is computed, so state coverage and "
            "initial-state types could not be checked.",
        )
        sim_initial = {}

    if not isinstance(driver_state_vars, dict):
        result.info(
            "state_coverage",
            "DRIVER_INFO state_variables are computed, so state coverage and "
            "initial-state types could not be checked.",
        )
        driver_state_vars = {}

    if sim_initial_readable:
        _check_python_state_coverage(result, driver_state_vars, sim_initial)

    # Check type consistency for initial state — the same function the YAML
    # path calls, so a Python driver is held to the same rules.
    _check_initial_state_types(result, driver_state_vars, sim_initial)

    # Check transport match
    driver_transport = driver_info.get("transport", "tcp")
    sim_transport = sim_info.get("transport", "tcp")
    if driver_transport != sim_transport:
        if driver_transport not in _SIMULATOR_SERVED_TRANSPORTS and sim_transport == "tcp":
            # Not a mismatch — the only way to simulate this device. See
            # _SIMULATOR_SERVED_TRANSPORTS.
            result.info(
                "transport_match",
                f"Driver transport {driver_transport!r} has no simulator server, "
                f"so it is simulated over TCP. That is the supported "
                f"substitution, not a mismatch."
            )
        else:
            result.error(
                "transport_match",
                f"DRIVER_INFO transport ({driver_transport!r}) != "
                f"SIMULATOR_INFO transport ({sim_transport!r})"
            )

    # Command coverage. A Python simulator's dispatch cannot be proven
    # statically — a binary protocol answers raw bytes and never writes a
    # logical command name anywhere — so this is a heads-up, not a finding,
    # and it reports at ``info``.
    #
    # It used to be a single warning gated on ``not exact_mentions``: one
    # command name appearing anywhere in the file silenced the check for
    # every other command. ``verillon_beacon`` validated with zero warnings
    # because the string ``set_bitrate`` occurred inside ``"stream.set_bitrate"``
    # and its other three commands were never looked at. Reporting which
    # names were not found is the same evidence without the cliff.
    sim_source = sim_path.read_text(encoding="utf-8")
    driver_commands = driver_info.get("commands", {})
    if not isinstance(driver_commands, dict):
        result.info(
            "command_coverage",
            "DRIVER_INFO commands are computed, so command coverage could not "
            "be checked.",
        )
        return result

    command_names = [name for name in driver_commands if isinstance(name, str)]
    unmentioned = [
        name
        for name in command_names
        # Standalone token so prefixes like ``power`` do not satisfy ``power_on``.
        if not re.search(
            rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", sim_source
        )
    ]

    if unmentioned:
        preview = ", ".join(repr(name) for name in unmentioned[:5])
        if len(unmentioned) > 5:
            preview += f", … (+{len(unmentioned) - 5} more)"
        result.info(
            "command_coverage",
            f"{len(unmentioned)} of {len(command_names)} DRIVER_INFO command "
            f"name(s) do not appear in the simulator source: {preview}. A "
            "simulator that dispatches on raw protocol bytes will never "
            "mention them, so this is only worth checking when the simulator "
            "does dispatch by name."
        )

    return result


def _extract_simulator_info(sim_path: Path) -> dict | None:
    """Extract SIMULATOR_INFO from a Python simulator file.

    Reads the source rather than importing it, through the same lenient
    evaluator the driver side uses. It used to be a ``literal_eval`` with a
    regex fallback, and that fallback's worst habit was line 1458's: a value
    it could not evaluate was stored as **its own source text**. So a
    simulator seeding ``"volume": _DEFAULT_VOLUME`` handed the type check the
    string ``"_DEFAULT_VOLUME"``, which then reported that an integer state
    variable held a str — a finding about the reader, printed as a finding
    about the driver. 16 of the 59 shipped simulators take that path.

    A value that genuinely cannot be resolved now comes back as the
    UNEVALUATED marker, which the checks skip and name instead of comparing.
    """
    import ast

    from openavc.drivers.python_info import lenient_ast_value

    try:
        tree = ast.parse(sim_path.read_text(encoding="utf-8"))
    except SyntaxError:
        return None

    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for item in node.body:
            targets = []
            if isinstance(item, ast.Assign):
                targets = item.targets
            elif isinstance(item, ast.AnnAssign) and item.value is not None:
                targets = [item.target]
            for target in targets:
                if isinstance(target, ast.Name) and target.id == "SIMULATOR_INFO":
                    value = lenient_ast_value(
                        item.value, "SIMULATOR_INFO", [],
                    )
                    return value if isinstance(value, dict) else None
    return None


# ── Helper functions ──


def _reply_rules(responses: list, driver_def: dict) -> ReplyRules:
    """The driver's reply rules, with the config the runtime would fill into
    their patterns."""
    return ReplyRules.of(responses, effective_config(driver_def), driver_def)


def _resolve_state_refs(template: str, state: dict) -> str:
    """Resolve {state.key} references in a template."""
    result = template
    for key, value in state.items():
        result = result.replace(f"{{state.{key}}}", str(value))
    return result


def _extract_respond_calls(handler_code: str) -> tuple[list[str], bool]:
    """The literal strings an inline handler responds with.

    Returns ``(literals, unreadable)`` — ``unreadable`` is True when the
    handler responds with something this cannot evaluate: an f-string, a
    concatenation, a variable, or code that does not parse at all. Both
    halves matter. The literals are what gets matched against the driver's
    response rules; ``unreadable`` is what stops "none of this handler's
    replies parses" being said about a handler whose real answer simply
    could not be read.

    Parsed rather than pattern-matched, because a regex over the source finds
    the *first* closing quote: ``respond("(PWR!" + str(x) + "\\r")`` gave it
    ``(PWR!``, which then matched no response rule and warned — 14 of the
    corpus's warnings were that, all on drivers whose replies are fine.
    """
    code = textwrap.dedent(handler_code)
    try:
        # compile(), not ast.parse(): a bare `return` parses but does not
        # compile, and the simulator skips the whole handler when it does not
        # compile. handler_checks.check_handler_code is what reports that; here it just
        # means nothing can be read, which is not the same as nothing being
        # answered.
        compile(code, "<handler>", "exec")
        tree = ast.parse(code)
    except SyntaxError:
        return [], True

    literals: list[str] = []
    unreadable = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == "respond"):
            continue
        if not node.args:
            continue
        arg = node.args[0]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            literals.append(arg.value)
        else:
            unreadable = True
    return literals, unreadable


# ── Directory scanning ──


def find_drivers(path: Path) -> list[tuple[Path, str]]:
    """Find all driver files in a path.

    Returns list of (path, type) tuples where type is "yaml" or "python".
    The whole rule is the checker's — a directory walk and its skip rules
    (which keep a repo's own test suite from being reported as 55 broken
    drivers), and its policy that a file named explicitly is always checked,
    whatever is in it. That second half matters here: naming a companion or a
    malformed driver and being told "No drivers found" hides the sentence the
    contract check would have printed. Only the yaml/python tag is this
    command's own, because only this command validates parity.
    """
    return [(f, _driver_type_tag(f)) for f in iter_driver_files(path)]


def _driver_type_tag(path: Path) -> str:
    """Which validator a path belongs to, by extension."""
    if path.suffix == ".avcdriver":
        return "yaml"
    return "python" if path.suffix == ".py" else "unknown"


# ── CLI ──


def _contract_issues(contract) -> list[Issue]:
    """The contract checker's findings, as this module's Issue type.

    This command is the one the guides name, so it is where an author who has
    never heard of ``python -m openavc.drivers.check`` still meets the contract.
    Parity is what the rest of this module checks — whether a driver and its
    simulator agree — and parity says nothing about whether a key is
    misspelled. The verdict and its wording come from the checker; nothing is
    re-derived here, so the terminal, catalog CI and the IDE cannot disagree.
    """
    issues = [Issue("error", "contract", msg) for msg in contract.errors]
    issues += [Issue("warning", "contract", msg) for msg in contract.warnings]
    issues += [Issue("info", "contract", note) for note in contract.notes]
    return issues


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(
        prog="simulator-validate",
        description="Validate driver simulator compatibility",
        epilog=(
            "Examples:\n"
            "  python -m openavc.simulator.validate drivers/audio/biamp_tesira_ttp.avcdriver\n"
            "  python -m openavc.simulator.validate drivers/projectors/pjlink_class1.py\n"
            "  python -m openavc.simulator.validate openavc-drivers/         # validate all\n"
            "  python -m openavc.simulator.validate openavc-drivers/ --summary\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "path",
        help="Driver file or directory to validate",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Show only pass/fail counts, not individual issues",
    )
    args = parser.parse_args(argv)

    path = Path(args.path)
    if not path.exists():
        print(f"Error: {path} does not exist", file=sys.stderr)
        sys.exit(1)

    drivers = find_drivers(path)
    if not drivers:
        print(f"No drivers found at {path}", file=sys.stderr)
        sys.exit(1)

    results: list[ValidationResult] = []
    for driver_path, driver_type in drivers:
        # The contract runs first, not merely prints first. Everything below
        # reads the definition as though it were well formed, so a file that
        # fails the contract takes the parity analysis down with a traceback
        # instead of the error that explains it — a parameter YAML turned
        # into a boolean (`on:`) used to end the run that way.
        contract = check_driver_file(driver_path)
        if contract.errors:
            result = ValidationResult(str(driver_path), driver_path.stem, driver_type)
            result.issues.extend(_contract_issues(contract))
            results.append(result)
            continue
        if driver_type == "yaml":
            result = validate_yaml_driver(driver_path)
        else:
            result = validate_python_driver(driver_path)
        result.issues[:0] = _contract_issues(contract)
        results.append(result)

    # Print results
    total_errors = 0
    total_warnings = 0
    total_infos = 0
    passed = 0
    failed = 0

    for r in results:
        if args.summary:
            status = "PASS" if r.passed else "FAIL"
            parts = []
            if r.errors:
                parts.append(f"{len(r.errors)} errors")
            if r.warnings:
                parts.append(f"{len(r.warnings)} warnings")
            if r.infos:
                parts.append(f"{len(r.infos)} infos")
            issue_str = f" ({', '.join(parts)})" if parts else ""
            print(f"{status} {r.driver_id} [{r.driver_type}]{issue_str}")
        else:
            if r.errors:
                status = "FAIL"
            elif r.warnings:
                status = "WARN"
            elif r.infos:
                status = "PASS"  # info-only is still a pass
            else:
                status = "PASS"
            if r.issues:
                print(f"\n{status}: {r.driver_id} [{r.driver_type}] ({r.driver_path})")
                for issue in r.issues:
                    print(str(issue))
            else:
                print(f"\nPASS: {r.driver_id} [{r.driver_type}]")

        total_errors += len(r.errors)
        total_warnings += len(r.warnings)
        total_infos += len(r.infos)
        if r.passed:
            passed += 1
        else:
            failed += 1

    # Summary line
    print(f"\n{'='*60}")
    print(
        f"{len(results)} drivers validated: "
        f"{passed} passed, {failed} failed, "
        f"{total_errors} errors, {total_warnings} warnings, "
        f"{total_infos} infos"
    )

    sys.exit(1 if total_errors > 0 else 0)


if __name__ == "__main__":
    main()
