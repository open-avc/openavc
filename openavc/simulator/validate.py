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
   12. Settings         — a declared capacity and single_client the simulator
                          would take (settings_checks, as for a Python driver)

  Python drivers (.py + _sim.py), in python_parity:
    1. SIMULATOR_INFO   — required fields present (driver_id, name, initial_state)
    2. Settings         — as for a YAML driver (settings_checks)
    3. driver_id match  — DRIVER_INFO.id == SIMULATOR_INFO.driver_id
    4. State coverage   — how much of the device the simulator seeds against
                          how much the driver reads (a count: the two are
                          different namespaces, so the names rarely line up)
    5. Type consistency — each seed against its declared type, as for YAML
    6. Transport match  — the same transport, or TCP standing in for one the
                          simulator has no server for
    7. Command names    — each command's name appears in the simulator's
                          source (a heads-up: a binary simulator never
                          writes one)
"""

from __future__ import annotations

import argparse
import dataclasses
import re
import sys
from pathlib import Path
from typing import Any

import yaml

from openavc.drivers.check import check_driver_file, iter_driver_files
from openavc.simulator.control_checks import check_controls
from openavc.simulator.handler_replies import check_response_parsing
from openavc.simulator.reply_framing import check_reply_framing
from openavc.simulator.handler_checks import (
    check_boolean_refs,
    check_handler_code,
    check_templates,
)
from openavc.simulator.settings_checks import check_declared_settings, check_initial_state_types
from openavc.simulator.python_parity import validate_python_driver
from openavc.simulator.osc_coverage import (
    OscAddresses,
    check_osc_command_coverage,
    check_osc_poll_coverage,
    check_osc_read_back_coverage,
)
from openavc.simulator.state_machine_checks import check_state_machines
from openavc.drivers.compiled_protocol import state_var_default
from openavc.simulator.driver_sends import (
    effective_config,
)
from openavc.simulator.validation_result import Issue, ValidationResult
from openavc.simulator.reply_reading import (
    ReplyRules,
)
from openavc.simulator.send_coverage import (
    check_command_coverage,
    check_poll_coverage,
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
        check_response_parsing(result, sim_handlers, sim_initial, driver_def)

        # ── Check 4: Poll query coverage: the poll, then_query, read_back ──
        check_poll_coverage(result, queries, sim_handlers, driver_def)

        # ── Check 4b: every reply ends where the driver splits the stream ──
        check_reply_framing(result, commands, queries, driver_def)

    # ── Check 5: Type consistency: each seed, then a boolean in a respond: ──
    check_initial_state_types(result, state_vars, sim_initial)
    check_boolean_refs(result, sim_handlers, state_vars)

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

    # ── Checks 12-13: Declared capacity, one connection at a time ──
    check_declared_settings(result, sim, "simulator.{}", transport)

    return result


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


# ── Helper functions ──


def _reply_rules(responses: list, driver_def: dict) -> ReplyRules:
    """The driver's reply rules, with the config the runtime would fill into
    their patterns."""
    return ReplyRules.of(responses, effective_config(driver_def), driver_def)


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
    args = _parse_args(argv)
    path = Path(args.path)
    if not path.exists():
        print(f"Error: {path} does not exist", file=sys.stderr)
        sys.exit(1)

    drivers = find_drivers(path)
    if not drivers:
        print(f"No drivers found at {path}", file=sys.stderr)
        sys.exit(1)

    results = [_validate_file(driver_path, driver_type) for driver_path, driver_type in drivers]
    for r in results:
        if args.summary:
            print(_summary_line(r))
        else:
            _print_issues(r)
    total_errors = _print_totals(results)
    sys.exit(1 if total_errors > 0 else 0)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
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
    return parser.parse_args(argv)


def _validate_file(driver_path: Path, driver_type: str) -> ValidationResult:
    """One driver file: the contract, then the parity checks, its findings first.

    The contract runs first, not merely prints first. Everything after it
    reads the definition as though it were well formed, so a file that fails
    the contract takes the parity analysis down with a traceback instead of
    the error that explains it — a parameter YAML turned into a boolean
    (`on:`) used to end the run that way.
    """
    contract = check_driver_file(driver_path)
    if contract.errors:
        result = ValidationResult(str(driver_path), driver_path.stem, driver_type)
        result.issues.extend(_contract_issues(contract))
        return result
    if driver_type == "yaml":
        result = validate_yaml_driver(driver_path)
    else:
        result = validate_python_driver(driver_path)
    result.issues[:0] = _contract_issues(contract)
    return result


def _summary_line(r: ValidationResult) -> str:
    """``--summary``'s line for one driver: its verdict and how many of each."""
    counts = [
        f"{len(found)} {word}"
        for word, found in (("errors", r.errors), ("warnings", r.warnings), ("infos", r.infos))
        if found
    ]
    issue_str = f" ({', '.join(counts)})" if counts else ""
    return f"{'PASS' if r.passed else 'FAIL'} {r.driver_id} [{r.driver_type}]{issue_str}"


def _print_issues(r: ValidationResult) -> None:
    """One driver's verdict and every issue, or one line when it has none."""
    if not r.issues:
        print(f"\nPASS: {r.driver_id} [{r.driver_type}]")
        return
    print(f"\n{_verdict(r)}: {r.driver_id} [{r.driver_type}] ({r.driver_path})")
    for issue in r.issues:
        print(str(issue))


def _verdict(r: ValidationResult) -> str:
    """FAIL on an error, WARN on a warning; a driver with only infos passes."""
    if r.errors:
        return "FAIL"
    if r.warnings:
        return "WARN"
    return "PASS"


def _print_totals(results: list[ValidationResult]) -> int:
    """The closing line's counts; returns how many errors there were."""
    passed = sum(1 for r in results if r.passed)
    total_errors = sum(len(r.errors) for r in results)
    total_warnings = sum(len(r.warnings) for r in results)
    total_infos = sum(len(r.infos) for r in results)
    print(f"\n{'='*60}")
    print(
        f"{len(results)} drivers validated: "
        f"{passed} passed, {len(results) - passed} failed, "
        f"{total_errors} errors, {total_warnings} warnings, "
        f"{total_infos} infos"
    )
    return total_errors


if __name__ == "__main__":
    main()
