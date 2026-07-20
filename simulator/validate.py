"""
Simulator validation tool — verifies that driver and simulator are compatible.

Catches mismatches between a driver's commands/responses and its simulator
handlers BEFORE you run the simulator. Works with both YAML (.avcdriver)
and Python (.py + _sim.py) drivers.

Usage:
    python -m simulator.validate path/to/driver.avcdriver
    python -m simulator.validate path/to/driver.py
    python -m simulator.validate path/to/drivers/           # validate all
    python -m simulator.validate path/to/drivers/ --summary  # counts only

Checks performed:

  YAML drivers (.avcdriver):
    1. State coverage   — every state_variable has an initial_state value
    2. Command coverage — every command has a matching simulator handler
                          (OSC drivers: command/poll addresses match handler
                          address patterns, response addresses, or the
                          simulator's built-in system addresses)
    3. Response parsing — simulator responses match the driver's response patterns
    4. Poll coverage    — every polling query has a matching handler
                          (each_child queries are checked with a sample child id)
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
                          per-child state is not auto-generated

  Python drivers (.py + _sim.py):
    1. SIMULATOR_INFO   — required fields present (driver_id, name, initial_state)
    2. State coverage   — every DRIVER_INFO state_variable covered in initial_state
    3. driver_id match  — DRIVER_INFO.id == SIMULATOR_INFO.driver_id
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

from server.drivers.compiled_protocol import derive_config, safe_substitute


# ── Result types ──


class Issue:
    """A single validation issue."""

    def __init__(self, severity: str, check: str, message: str):
        self.severity = severity  # "error" | "warning" | "info"
        self.check = check  # e.g., "state_coverage", "command_coverage"
        self.message = message

    def __str__(self) -> str:
        if self.severity == "error":
            icon = "ERROR"
        elif self.severity == "warning":
            icon = "WARN "
        else:
            icon = "INFO "
        return f"  {icon} [{self.check}] {self.message}"


class ValidationResult:
    """Validation result for a single driver."""

    def __init__(self, driver_path: str, driver_id: str, driver_type: str):
        self.driver_path = driver_path
        self.driver_id = driver_id
        self.driver_type = driver_type  # "yaml" or "python"
        self.issues: list[Issue] = []

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def infos(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "info"]

    @property
    def passed(self) -> bool:
        return len(self.errors) == 0

    def error(self, check: str, message: str) -> None:
        self.issues.append(Issue("error", check, message))

    def warning(self, check: str, message: str) -> None:
        self.issues.append(Issue("warning", check, message))

    def info(self, check: str, message: str) -> None:
        self.issues.append(Issue("info", check, message))


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
        _check_osc_command_coverage(result, commands, sim_handlers, responses, driver_def)
        _check_osc_poll_coverage(result, queries, commands, sim_handlers, responses, driver_def)
    else:
        # ── Check 2: Command handler coverage ──
        _check_command_coverage(result, commands, sim_handlers, driver_def)

        # ── Check 3: Response parsing (round-trip) ──
        _check_response_parsing(result, commands, sim_handlers, responses, sim_initial, driver_def)

        # ── Check 4: Poll query coverage ──
        _check_poll_coverage(result, queries, sim_handlers, driver_def)

    # ── Check 5: Type consistency ──
    _check_type_consistency(result, state_vars, sim_initial, sim_handlers)

    # ── Check 6: State machine structure ──
    _check_state_machines(result, sim, commands)

    # ── Check 7: Handler code syntax ──
    _check_handler_syntax(result, sim_handlers)

    # ── Check 8: Explicit handler set_state / capture references ──
    _check_explicit_handlers(result, sim_handlers, known_keys)

    # ── Check 9: Notification templates ──
    _check_notifications(
        result, sim, state_vars, sim_initial, responses, driver_def, known_keys, transport
    )

    # ── Check 10: Declarative controls schema ──
    _check_controls(result, sim, known_keys)

    # ── Check 11: Child entity simulation gap ──
    _check_child_entities(result, driver_def, sim)

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


def _check_state_machines(result: ValidationResult, sim: dict, commands: dict) -> None:
    """Validate the simulator.state_machines structure.

    Catches the malformed entries that would otherwise raise a KeyError at
    simulation-launch time (missing states/initial/transitions, off-list
    states), and warns when a transition can never fire (trigger names no
    command, or no trigger/after_seconds at all).
    """
    machines = sim.get("state_machines")
    if not machines:
        return
    if not isinstance(machines, dict):
        result.error("state_machine", "simulator.state_machines must be a mapping")
        return

    command_names = set(commands.keys())
    for name, sm in machines.items():
        if not isinstance(sm, dict):
            result.error("state_machine", f"state_machine '{name}' must be a mapping")
            continue

        states = sm.get("states")
        if not isinstance(states, list) or not states:
            result.error(
                "state_machine",
                f"state_machine '{name}' needs a non-empty 'states' list",
            )
            states = states if isinstance(states, list) else []

        initial = sm.get("initial")
        if initial is None:
            result.error("state_machine", f"state_machine '{name}' needs an 'initial' state")
        elif states and initial not in states:
            result.error(
                "state_machine",
                f"state_machine '{name}' initial '{initial}' is not one of {states}",
            )

        transitions = sm.get("transitions")
        if not isinstance(transitions, list):
            result.error(
                "state_machine",
                f"state_machine '{name}' needs a 'transitions' list",
            )
            continue

        seen_triggers: set[tuple] = set()
        for i, t in enumerate(transitions):
            label = f"state_machine '{name}' transition {i}"
            if not isinstance(t, dict):
                result.error("state_machine", f"{label} must be a mapping")
                continue

            # Two entries for the same (from, trigger) pair: the first listed
            # wins at runtime, so the later one is unreachable.
            if "trigger" in t:
                pair = (t.get("from"), t.get("trigger"))
                if pair in seen_triggers:
                    result.warning(
                        "state_machine",
                        f"{label} duplicates an earlier transition for "
                        f"from '{pair[0]}' trigger '{pair[1]}' — the first "
                        f"listed wins, this one is unreachable",
                    )
                seen_triggers.add(pair)

            frm = t.get("from")
            if frm is None:
                result.error("state_machine", f"{label} is missing 'from'")
            elif states and frm not in states:
                result.error("state_machine", f"{label} 'from' state '{frm}' is not one of {states}")

            is_reject = bool(t.get("reject"))
            to = t.get("to")
            if not is_reject:
                if to is None:
                    result.error("state_machine", f"{label} is missing 'to'")
                elif states and to not in states:
                    result.error("state_machine", f"{label} 'to' state '{to}' is not one of {states}")

            if "after_seconds" in t and not isinstance(t["after_seconds"], (int, float)):
                result.error("state_machine", f"{label} 'after_seconds' must be a number")

            if "trigger" not in t and "after_seconds" not in t and not is_reject:
                result.warning(
                    "state_machine",
                    f"{label} has neither 'trigger' nor 'after_seconds' — it can never fire",
                )

            trig = t.get("trigger")
            if trig and trig != "*" and command_names and trig not in command_names:
                result.warning(
                    "state_machine",
                    f"{label} trigger '{trig}' matches no command name — it will never fire",
                )


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


def _check_command_coverage(
    result: ValidationResult,
    commands: dict,
    sim_handlers: list,
    driver_def: dict,
) -> None:
    """Check every command has a matching handler in the simulator."""
    # Build compiled patterns from simulator handlers
    handler_patterns = _compile_handler_patterns(sim_handlers)

    for cmd_name, cmd_def in commands.items():
        send_template = cmd_def.get("send", "")
        if not send_template:
            continue

        params = cmd_def.get("params", {})

        # Generate a sample command string with test values
        sample = _generate_sample_command(send_template, params, driver_def)
        if sample is None:
            result.warning(
                "command_coverage",
                f"Could not generate sample for command '{cmd_name}' "
                f"(send: {send_template!r})"
            )
            continue

        # Check if any handler matches
        matched = _find_matching_handler(sample, handler_patterns)
        if not matched:
            # Also check auto-gen command handlers
            auto_pattern = _build_auto_pattern(send_template, params)
            if auto_pattern and auto_pattern.match(sample):
                # Auto-gen would handle it — that's fine
                continue
            result.error(
                "command_coverage",
                f"No simulator handler matches command '{cmd_name}' "
                f"(sample: {sample!r})"
            )


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
    """
    if not responses:
        return

    # Compile response patterns (with config variable substitution)
    response_patterns = _compile_response_patterns(responses, driver_def)
    json_keys = _json_rule_keys(responses)

    # Check explicit handler responses
    for handler_def in sim_handlers:
        if not isinstance(handler_def, dict) or "address" in handler_def:
            # OSC handlers respond with (address, args) tuples, not text lines
            continue
        respond_template = handler_def.get("respond")
        handler_code = handler_def.get("handler")

        if respond_template:
            # Explicit handler with respond: template
            # Resolve {state.key} with initial state values
            response_text = _resolve_state_refs(respond_template, sim_initial)
            # Strip delimiter chars that may be in the template
            response_text = response_text.replace("\\r", "").replace("\\n", "")
            response_text = response_text.rstrip("\r\n")
            # Skip if it has unresolved capture group refs ({1}, {2}, etc.)
            if re.search(r"\{\d+\}", response_text):
                continue
            _check_single_response(
                result, response_text, response_patterns, handler_def, json_keys
            )

        elif handler_code:
            # Script handler — extract respond() calls and try to resolve them
            respond_calls = _extract_respond_calls(handler_code)
            for call_template in respond_calls:
                # Best-effort resolution with initial state
                response_text = _resolve_state_refs(call_template, sim_initial)
                response_text = response_text.replace("\\r", "").replace("\\n", "")
                response_text = response_text.rstrip("\r\n")
                # Skip if it still has unresolved variables (match groups, etc.)
                if "{" in response_text and "}" in response_text:
                    # Has unresolved template vars — can't validate statically
                    continue
                _check_single_response(
                    result, response_text, response_patterns, handler_def, json_keys
                )


def _check_single_response(
    result: ValidationResult,
    response_text: str,
    response_patterns: list[re.Pattern],
    handler_def: dict,
    json_keys: set[str] = frozenset(),
) -> None:
    """Check if a single response text matches any response pattern."""
    # Skip empty responses or pure ACKs
    if not response_text or response_text in ("+OK", "OK", "sr"):
        return

    matched = any(
        p.search(response_text) for p in response_patterns
    ) or _matches_json_rules(response_text, json_keys)
    if not matched:
        pattern_key = handler_def.get("receive") or handler_def.get("match", "?")
        result.warning(
            "response_parsing",
            f"Handler response may not match any driver response pattern: "
            f"handler={pattern_key!r}, response={response_text!r}"
        )


def _check_poll_coverage(
    result: ValidationResult,
    queries: list,
    sim_handlers: list,
    driver_def: dict,
) -> None:
    """Check every polling query has a matching simulator handler."""
    handler_patterns = _compile_handler_patterns(sim_handlers)
    config = _effective_config(driver_def)
    commands = driver_def.get("commands", {})
    transport = driver_def.get("transport", "tcp")

    for query in queries:
        if isinstance(query, dict):
            query_text = query.get("send", "")
        else:
            query_text = str(query)

        if not query_text:
            continue

        # HTTP/UDP queries that name a command run as that command —
        # command coverage checks those.
        if transport in ("http", "udp") and query_text in commands:
            continue

        # Substitute config variables in query text; each_child queries get a
        # sample child id (the runtime expands them per registered child).
        sample = _substitute_config(query_text, config)
        sample = sample.replace("{child_id}", "1")
        sample = sample.strip().replace("\\n", "").replace("\\r", "")
        sample = sample.rstrip("\r\n").strip()

        # An HTTP raw-path query (not a command name) is fetched with GET, and
        # the HTTP simulator dispatches it as the synthesized line
        # "GET /path?query" — so compare handlers against that form, not the
        # bare path, or every raw-path poll looks uncovered.
        if transport == "http" and sample.startswith("/"):
            sample = f"GET {sample}"

        matched = _find_matching_handler(sample, handler_patterns)
        if not matched:
            # Check if auto-gen handlers would cover this. The simulator
            # builds a handler for every command's send template, so any
            # query matching one of those patterns is answered too.
            auto_covered = False
            for cmd_def in commands.values():
                if not cmd_def.get("send"):
                    continue
                if cmd_def.get("send") == query_text and not cmd_def.get("params"):
                    auto_covered = True
                    break
                auto_pattern = _build_auto_pattern(
                    cmd_def["send"], cmd_def.get("params", {})
                )
                if auto_pattern and auto_pattern.match(sample):
                    auto_covered = True
                    break

            if not auto_covered:
                result.error(
                    "poll_coverage",
                    f"No simulator handler matches polling query: {sample!r}"
                )


def _check_type_consistency(
    result: ValidationResult,
    state_vars: dict,
    sim_initial: dict,
    sim_handlers: list,
) -> None:
    """Check for common type consistency issues."""
    for var_name, var_def in state_vars.items():
        var_type = var_def.get("type", "string")
        if var_name not in sim_initial:
            continue
        initial = sim_initial[var_name]

        # Check boolean variables
        if var_type == "boolean" and not isinstance(initial, bool):
            result.error(
                "type_consistency",
                f"State variable '{var_name}' is boolean but initial_state "
                f"value is {type(initial).__name__}: {initial!r}"
            )

        # Check integer variables
        if var_type == "integer" and not isinstance(initial, int):
            result.warning(
                "type_consistency",
                f"State variable '{var_name}' is integer but initial_state "
                f"value is {type(initial).__name__}: {initial!r}"
            )

        # Check enum variables
        if var_type == "enum":
            valid_values = var_def.get("values", [])
            if valid_values and str(initial) not in [str(v) for v in valid_values]:
                result.error(
                    "type_consistency",
                    f"State variable '{var_name}' initial value {initial!r} "
                    f"not in enum values: {valid_values}"
                )

    # Check for boolean state refs in respond: templates that might produce
    # capitalized Python booleans (True/False instead of true/false)
    for handler_def in sim_handlers:
        if not isinstance(handler_def, dict):
            continue
        respond = handler_def.get("respond", "")
        if not respond:
            continue
        # Find {state.X} references where X is a boolean variable
        for match in re.finditer(r"\{state\.(\w+)\}", respond):
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


# ── OSC coverage (address-based) ──

# Addresses the simulator answers without any handler (yaml_auto
# _handle_osc_message): subscription renewal, console info/status, and the
# action/show namespaces it echoes back.
_OSC_SPECIAL_ADDRESSES = {"/xremote", "/info", "/status"}
_OSC_SPECIAL_PREFIXES = ("/-action/", "/-show/")


def _collect_osc_patterns(sim_handlers: list, responses: list) -> list[str]:
    """Address patterns (fnmatch) the OSC simulator matches against: script
    handlers with address:, plus response entries with address: (the sim
    auto-answers those with current state)."""
    patterns = []
    for h in sim_handlers:
        if isinstance(h, dict) and h.get("address"):
            patterns.append(str(h["address"]))
    for resp in responses:
        if isinstance(resp, dict) and resp.get("address"):
            patterns.append(str(resp["address"]))
    return patterns


def _osc_address_covered(address: str, patterns: list[str]) -> bool:
    if address in _OSC_SPECIAL_ADDRESSES or address.startswith(_OSC_SPECIAL_PREFIXES):
        return True
    return any(fnmatch.fnmatch(address, p) for p in patterns)


# Marker for param placeholders while building a template regex; plain ASCII
# so re.escape leaves it alone, unusual enough not to appear in addresses.
_PARAM_MARKER = "Q0PARAMQ0"


def _osc_template_regex(template: str, params: dict) -> re.Pattern | None:
    """Regex matching every address the template can produce (params match
    any single address segment)."""
    pat = template
    for param_name in params:
        pat = re.sub(r"\{" + re.escape(param_name) + r"(:[^}]*)?\}", _PARAM_MARKER, pat)
    if "{" in pat:
        return None  # unresolved config placeholders — can't model statically
    escaped = re.escape(pat).replace(_PARAM_MARKER, "[^/]+")
    try:
        return re.compile(f"^{escaped}$")
    except re.error:
        return None


def _osc_template_covered(template: str, params: dict, patterns: list[str]) -> bool:
    """Check whether the set of addresses a parameterized command template can
    produce overlaps the simulator's address surface.

    A command like /mtx/{mtx}/mix/on with a string param is covered by literal
    response addresses /mtx/01/mix/on … /mtx/06/mix/on even though no single
    sample value proves it. Wildcard handler patterns are probed with their
    * / ? / [...] parts collapsed to a plain segment token.
    """
    rx = _osc_template_regex(template, params)
    if rx is None:
        return False
    for pattern in patterns:
        if any(ch in pattern for ch in "*?["):
            probe = re.sub(r"\[[^\]]*\]", "x", pattern)
            probe = re.sub(r"[*?]+", "x", probe)
        else:
            probe = pattern
        if rx.match(probe):
            return True
    return False


def _check_osc_command_coverage(
    result: ValidationResult,
    commands: dict,
    sim_handlers: list,
    responses: list,
    driver_def: dict,
) -> None:
    """Check every OSC command's address is answered by the simulator."""
    patterns = _collect_osc_patterns(sim_handlers, responses)
    config = _effective_config(driver_def)

    for cmd_name, cmd_def in commands.items():
        address_template = cmd_def.get("address", "")
        if not address_template:
            continue

        params = cmd_def.get("params", {})
        sample = _generate_sample_command(address_template, params, driver_def)
        if sample is not None and _osc_address_covered(sample, patterns):
            continue
        # A string param's sample value may miss literal response addresses
        # that do cover the command (e.g. /mtx/{mtx}/mix/on vs /mtx/01/mix/on),
        # so fall back to matching the template's address shape.
        if _osc_template_covered(
            _substitute_config(address_template, config), params, patterns
        ):
            continue
        if sample is None:
            result.warning(
                "command_coverage",
                f"Could not generate sample address for command '{cmd_name}' "
                f"(address: {address_template!r})"
            )
            continue
        result.error(
            "command_coverage",
            f"No simulator handler matches OSC command '{cmd_name}' "
            f"(sample address: {sample!r}) — add a command_handlers entry "
            f"with a matching address: pattern or a responses entry for it"
        )


def _check_osc_poll_coverage(
    result: ValidationResult,
    queries: list,
    commands: dict,
    sim_handlers: list,
    responses: list,
    driver_def: dict,
) -> None:
    """Check every OSC polling query is answered by the simulator.

    OSC poll queries are either command names (run as that command — covered
    by command coverage) or raw addresses.
    """
    patterns = _collect_osc_patterns(sim_handlers, responses)
    config = _effective_config(driver_def)

    for query in queries:
        if isinstance(query, dict):
            query_text = str(query.get("send", ""))
        else:
            query_text = str(query)
        if not query_text:
            continue

        if query_text in commands:
            continue  # runs as that command; command coverage checks it

        sample = _substitute_config(query_text, config)
        sample = sample.replace("{child_id}", "1")
        if not _osc_address_covered(sample, patterns):
            result.error(
                "poll_coverage",
                f"No simulator handler matches OSC polling query: {sample!r}"
            )


# ── Simulator-section checks (handlers, notifications, controls, children) ──


def _check_handler_syntax(result: ValidationResult, sim_handlers: list) -> None:
    """Check inline handler: Python bodies compile.

    The simulator skips a handler whose code has a syntax error (with only a
    server-side log line), so a typo silently kills the handler at runtime.
    """
    for h in sim_handlers:
        if not isinstance(h, dict):
            result.error("command_handlers", f"command_handlers entries must be mappings, got: {h!r}")
            continue
        code = h.get("handler")
        if code is None:
            continue
        label = h.get("address") or h.get("receive") or h.get("match") or "?"
        try:
            compile(str(code), "<handler>", "exec")
        except SyntaxError as e:
            result.error(
                "handler_syntax",
                f"handler for {label!r} has a Python syntax error "
                f"(line {e.lineno}): {e.msg} — the simulator will skip this handler"
            )


def _check_explicit_handlers(
    result: ValidationResult,
    sim_handlers: list,
    known_keys: set[str],
) -> None:
    """Check template-based handlers (receive: + respond:/set_state:).

    Catches set_state: targets that aren't state keys (the sim would write a
    junk key), {N} capture references beyond what the receive: pattern
    captures (left as literal text at runtime), and {state.X} references to
    unknown keys (also left literal).
    """
    capture_ref = re.compile(r"\{(\d+)\}")
    state_ref = re.compile(r"\{state\.(\w+)\}")

    for h in sim_handlers:
        if not isinstance(h, dict) or "address" in h:
            continue  # OSC handlers have no receive:/set_state: surface

        pattern_str = h.get("receive") or h.get("match", "")
        set_state = h.get("set_state")
        respond = h.get("respond")

        if "handler" in h:
            if set_state or respond:
                result.warning(
                    "command_handlers",
                    f"handler {pattern_str!r}: set_state:/respond: are ignored "
                    f"when handler: is present — move that logic into the handler code"
                )
            continue

        if not pattern_str:
            if set_state or respond:
                result.warning(
                    "command_handlers",
                    "handler with respond:/set_state: has no receive: or match: "
                    "pattern — it can never fire"
                )
            continue

        try:
            group_count = re.compile(pattern_str).groups
        except re.error as e:
            result.error(
                "command_handlers",
                f"invalid regex in handler pattern {pattern_str!r}: {e} — "
                f"the simulator will skip this handler"
            )
            continue

        templates: list[str] = []
        if isinstance(set_state, dict):
            for key, value in set_state.items():
                if known_keys and key not in known_keys:
                    result.warning(
                        "set_state",
                        f"handler {pattern_str!r}: set_state target '{key}' is not "
                        f"a state variable or initial_state key — the value will be "
                        f"written to a key nothing reads"
                    )
                templates.append(str(value))
        if isinstance(respond, str):
            templates.append(respond)

        for template in templates:
            for m in capture_ref.finditer(template):
                n = int(m.group(1))
                if n > group_count:
                    result.error(
                        "set_state",
                        f"handler {pattern_str!r}: template {template!r} references "
                        f"capture group {{{n}}} but the pattern has only "
                        f"{group_count} group(s) — the placeholder stays literal at runtime"
                    )
            for m in state_ref.finditer(template):
                if known_keys and m.group(1) not in known_keys:
                    result.warning(
                        "set_state",
                        f"handler {pattern_str!r}: template {template!r} references "
                        f"{{state.{m.group(1)}}} which is not a state key — the "
                        f"placeholder stays literal at runtime"
                    )


def _default_for_type(var_def: dict) -> Any:
    """The auto-gen initial value for a state variable (mirrors yaml_auto)."""
    var_type = var_def.get("type", "string")
    if var_type == "integer":
        return var_def.get("min", 0)
    if var_type == "number":
        return 0.0
    if var_type == "boolean":
        return False
    if var_type == "enum":
        values = var_def.get("values", [])
        return values[0] if values else ""
    return ""


def _check_notifications(
    result: ValidationResult,
    sim: dict,
    state_vars: dict,
    sim_initial: dict,
    responses: list,
    driver_def: dict,
    known_keys: set[str],
    transport: str,
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

    # A driver with a push channel emits its notifications there instead of
    # the control connection — multicast, tcp_listener (dial-back) and
    # http_listener (webhooks) are valid on any transport; SSE needs HTTP
    # (the subscription rides the control session).
    push_def = driver_def.get("push")
    has_push_channel = isinstance(push_def, dict) and (
        push_def.get("type") in ("multicast", "tcp_listener", "http_listener")
        or (push_def.get("type") == "sse" and transport == "http")
    )
    if transport not in ("tcp", "serial") and not has_push_channel:
        result.warning(
            "notifications",
            f"notifications: has no effect for transport '{transport}' — only "
            f"line-based TCP/serial simulators push notification messages "
            f"(unless the driver declares a multicast, SSE, TCP dial-back, "
            f"or HTTP-listener "
            f"push: block)"
        )

    response_patterns = _compile_response_patterns(responses, driver_def)
    json_keys = _json_rule_keys(responses)

    for key, value_map in notifications.items():
        if known_keys and key not in known_keys:
            result.warning(
                "notifications",
                f"notification key '{key}' is not a state variable or "
                f"initial_state key — it only fires if a handler writes that key"
            )

        var_type = state_vars.get(key, {}).get("type", "string")

        if isinstance(value_map, str):
            value_map = {"*": value_map}
        elif not isinstance(value_map, dict):
            result.error(
                "notifications",
                f"notification '{key}' must be a template string or a "
                f"value → template mapping, got: {value_map!r}"
            )
            continue

        for trigger_value, template in value_map.items():
            trigger = str(trigger_value)
            template = str(template)

            if var_type == "boolean" and trigger not in ("true", "false", "*"):
                result.error(
                    "notifications",
                    f"notification '{key}' value key {trigger!r} never matches — "
                    f"boolean lookups use lowercase 'true'/'false' (quote the keys "
                    f"in YAML so they stay strings)"
                )
            if var_type == "enum" and trigger != "*":
                valid = [str(v) for v in state_vars[key].get("values", [])]
                if valid and trigger not in valid:
                    result.warning(
                        "notifications",
                        f"notification '{key}' value key {trigger!r} is not one of "
                        f"the enum values {valid}"
                    )

            if var_type == "boolean" and "{value}" in template:
                result.error(
                    "notifications",
                    f"notification '{key}' template uses {{value}} but '{key}' is "
                    f"boolean — Python's str(True) produces 'True' not 'true'. Use "
                    f"per-value templates ('true': ..., 'false': ...) instead."
                )
                continue

            if not response_patterns and not json_keys:
                continue

            # Round-trip: the emitted message should parse via responses:
            if trigger == "*":
                if key in sim_initial:
                    sample_value = sim_initial[key]
                else:
                    sample_value = _default_for_type(state_vars.get(key, {}))
            else:
                sample_value = trigger
            # Render with the runtime's own template renderer so format specs
            # ({value:d} on a boolean) validate the way they will emit. A
            # boolean var's per-value trigger key is the runtime bool, not the
            # 'true'/'false' lookup string.
            if var_type == "boolean" and isinstance(sample_value, str) and (
                sample_value in ("true", "false")
            ):
                sample_value = sample_value == "true"
            from simulator.yaml_auto import YAMLAutoSimulator

            message = YAMLAutoSimulator._render_notification(
                str(template), key, sample_value
            )
            # Unresolved {placeholder} tokens can't be validated statically.
            # A brace followed by a quote is JSON, not a placeholder — a
            # rendered JSON notification must still round-trip below.
            if re.search(r"\{[a-zA-Z_]\w*(:[^}]*)?\}", message):
                continue
            # Mirror runtime dispatch: pushed data is split on line endings
            # (driver delimiter) and stripped before matching, so a template
            # wrapped in CR/LF (dial-back containers embed the payload as
            # "\r\n<response>\r\n") still round-trips.
            candidates = [message.strip()] + [
                part.strip()
                for part in re.split(r"[\r\n]+", message)
                if part.strip()
            ]
            if not any(
                p.search(c) for c in candidates for p in response_patterns
            ) and not _matches_json_rules(message, json_keys):
                result.warning(
                    "notifications",
                    f"notification '{key}' emits {message!r} which matches no "
                    f"driver response pattern — the driver will ignore it"
                )


# Required fields per control type, beyond the always-required "type".
# "label" requirements are warned, not errored (the panel renders, just ugly).
_CONTROL_REQUIRED: dict[str, tuple[str, ...]] = {
    "power": ("key",),
    "select": ("key", "options"),
    "slider": ("key", "min", "max"),
    "toggle": ("key",),
    "matrix": ("inputs", "outputs", "state_pattern"),
    "meters": ("channels", "key_pattern"),
    "presets": ("key",),
    "group": ("controls",),
    "indicator": ("key",),
}
_CONTROL_LABEL_REQUIRED = ("toggle", "indicator", "group")


def _check_controls(result: ValidationResult, sim: dict, known_keys: set[str]) -> None:
    """Validate the explicit simulator.controls schema.

    Explicit controls replace the auto-generated set, so a typo'd type or a
    missing per-type field silently yields a blank or broken panel.
    """
    controls = sim.get("controls")
    if controls is None:
        return
    if not isinstance(controls, list):
        result.error("controls", "simulator.controls must be a list")
        return
    for i, control in enumerate(controls):
        _check_one_control(result, control, f"controls[{i}]", known_keys)


def _check_one_control(
    result: ValidationResult, control: Any, where: str, known_keys: set[str]
) -> None:
    if not isinstance(control, dict):
        result.error("controls", f"{where}: must be a mapping, got: {control!r}")
        return

    ctype = control.get("type")
    if ctype not in _CONTROL_REQUIRED:
        valid = ", ".join(sorted(_CONTROL_REQUIRED))
        result.error(
            "controls",
            f"{where}: unknown control type {ctype!r} — the panel renders "
            f"nothing for it (valid types: {valid})"
        )
        return

    for field in _CONTROL_REQUIRED[ctype]:
        if field not in control:
            result.error(
                "controls",
                f"{where} ({ctype}): missing required field '{field}'"
            )
    if ctype in _CONTROL_LABEL_REQUIRED and "label" not in control:
        result.warning(
            "controls",
            f"{where} ({ctype}): missing 'label' — the panel shows 'undefined'"
        )

    def _warn_unknown_key(key: Any, field: str) -> None:
        if isinstance(key, str) and known_keys and key not in known_keys:
            result.warning(
                "controls",
                f"{where} ({ctype}): {field} '{key}' is not a state variable or "
                f"initial_state key — the control shows nothing and writes to a "
                f"key nothing reads"
            )

    if ctype in ("power", "select", "slider", "toggle", "presets", "indicator"):
        _warn_unknown_key(control.get("key"), "key")

    if ctype == "select":
        options = control.get("options")
        if options is not None and not isinstance(options, list):
            result.error("controls", f"{where} (select): 'options' must be a list")
        elif isinstance(options, list) and not options:
            result.warning("controls", f"{where} (select): 'options' is empty")

    if ctype == "slider":
        for field in ("min", "max"):
            value = control.get(field)
            if value is not None and not isinstance(value, (int, float)):
                result.error(
                    "controls",
                    f"{where} (slider): '{field}' must be a number, got: {value!r}"
                )

    if ctype == "matrix":
        for field in ("inputs", "outputs"):
            value = control.get(field)
            if value is not None and (not isinstance(value, int) or value <= 0):
                result.error(
                    "controls",
                    f"{where} (matrix): '{field}' must be a positive integer, "
                    f"got: {value!r}"
                )
        pattern = control.get("state_pattern")
        if isinstance(pattern, str):
            if "{output}" not in pattern:
                result.warning(
                    "controls",
                    f"{where} (matrix): state_pattern {pattern!r} has no "
                    f"{{output}} placeholder — every output binds the same key"
                )
            else:
                _warn_unknown_key(pattern.replace("{output}", "1"), "state_pattern")

    if ctype == "meters":
        channels = control.get("channels")
        if channels is not None and (not isinstance(channels, int) or channels <= 0):
            result.error(
                "controls",
                f"{where} (meters): 'channels' must be a positive integer, "
                f"got: {channels!r}"
            )
        for field in ("key_pattern", "mute_pattern"):
            pattern = control.get(field)
            if isinstance(pattern, str):
                if "{ch}" not in pattern:
                    result.warning(
                        "controls",
                        f"{where} (meters): {field} {pattern!r} has no {{ch}} "
                        f"placeholder — every channel binds the same key"
                    )
                else:
                    _warn_unknown_key(pattern.replace("{ch}", "1"), field)

    if ctype == "presets":
        if "count" not in control and "names" not in control:
            result.warning(
                "controls",
                f"{where} (presets): neither 'count' nor 'names' given — the "
                f"panel renders no preset buttons"
            )
        count = control.get("count")
        if count is not None and not isinstance(count, int):
            result.error(
                "controls", f"{where} (presets): 'count' must be an integer"
            )
        names = control.get("names")
        if names is not None and not isinstance(names, list):
            result.error(
                "controls", f"{where} (presets): 'names' must be a list"
            )

    if ctype == "group":
        children = control.get("controls")
        if isinstance(children, list):
            for i, child in enumerate(children):
                _check_one_control(result, child, f"{where}.controls[{i}]", known_keys)
        elif children is not None:
            result.error("controls", f"{where} (group): 'controls' must be a list")


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
            f"simulator: section — the auto-generated simulator only models "
            f"top-level state_variables, so child-addressed commands and polls "
            f"get no realistic responses. Add command_handlers covering them."
        )
    else:
        result.info(
            "child_entities",
            f"driver declares child entity types ({names}) — per-child state "
            f"is not auto-generated, so make sure command_handlers cover "
            f"child-addressed commands and each_child polling queries."
        )


# ── Python driver validation ──


def validate_python_driver(driver_path: Path) -> ValidationResult:
    """Validate a Python driver and its companion _sim.py simulator."""
    from simulator.scaffold import extract_driver_info

    driver_info = extract_driver_info(driver_path)
    driver_id = driver_info.get("id", driver_path.stem) if driver_info else driver_path.stem
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

    # Check driver_id match
    if driver_info.get("id") and sim_info.get("driver_id"):
        if driver_info["id"] != sim_info["driver_id"]:
            result.error(
                "driver_id_match",
                f"DRIVER_INFO id ({driver_info['id']!r}) != "
                f"SIMULATOR_INFO driver_id ({sim_info['driver_id']!r})"
            )

    # Check state variable coverage
    driver_state_vars = driver_info.get("state_variables", {})
    sim_initial = sim_info.get("initial_state", {})

    for var_name, var_def in driver_state_vars.items():
        if var_name not in sim_initial:
            result.warning(
                "state_coverage",
                f"DRIVER_INFO state variable '{var_name}' not in "
                f"SIMULATOR_INFO initial_state"
            )

    # Check type consistency for initial state
    for var_name, var_def in driver_state_vars.items():
        if var_name not in sim_initial:
            continue
        var_type = var_def.get("type", "string")
        initial = sim_initial[var_name]

        if var_type == "boolean" and not isinstance(initial, bool):
            result.error(
                "type_consistency",
                f"State variable '{var_name}' is boolean but initial_state "
                f"value is {type(initial).__name__}: {initial!r}"
            )
        if var_type == "enum":
            valid_values = var_def.get("values", [])
            if valid_values and str(initial) not in [str(v) for v in valid_values]:
                result.error(
                    "type_consistency",
                    f"State variable '{var_name}' initial value {initial!r} "
                    f"not in enum values: {valid_values}"
                )

    # Check transport match
    driver_transport = driver_info.get("transport", "tcp")
    sim_transport = sim_info.get("transport", "tcp")
    if driver_transport != sim_transport:
        result.error(
            "transport_match",
            f"DRIVER_INFO transport ({driver_transport!r}) != "
            f"SIMULATOR_INFO transport ({sim_transport!r})"
        )

    # Check command coverage: every command in DRIVER_INFO should be
    # handleable by the simulator. For Python sims we can't prove the
    # protocol dispatch statically, but we can at least flag sims that
    # never mention any command names exactly.
    sim_source = sim_path.read_text(encoding="utf-8")
    driver_commands = driver_info.get("commands", {})
    exact_mentions: set[str] = set()
    for cmd_name in driver_commands:
        # Look for the command name as a standalone token so prefixes like
        # ``power`` do not accidentally satisfy ``power_on``.
        if re.search(rf"(?<![A-Za-z0-9_]){re.escape(cmd_name)}(?![A-Za-z0-9_])", sim_source):
            exact_mentions.add(cmd_name)

    if not exact_mentions and driver_commands:
        preview = ", ".join(repr(name) for name in list(driver_commands)[:5])
        if len(driver_commands) > 5:
            preview += f", … (+{len(driver_commands) - 5} more)"
        result.warning(
            "command_coverage",
            "Python simulator source does not mention any DRIVER_INFO "
            f"commands exactly: {preview}. The simulator may still handle them at "
            "the protocol level, but this is worth checking."
        )

    return result


def _extract_simulator_info(sim_path: Path) -> dict | None:
    """Extract SIMULATOR_INFO dict from a Python simulator file using AST.

    Falls back to regex extraction if AST literal_eval fails (e.g., when
    the dict references module-level variables).
    """
    import ast

    source = sim_path.read_text(encoding="utf-8")

    # Try AST parsing first (handles clean dicts)
    try:
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for item in node.body:
                    if isinstance(item, ast.Assign):
                        for target in item.targets:
                            if isinstance(target, ast.Name) and target.id == "SIMULATOR_INFO":
                                return ast.literal_eval(item.value)
    except (SyntaxError, ValueError):
        pass

    # Fallback: regex extraction of key fields
    info: dict[str, Any] = {}

    m = re.search(r'"driver_id"\s*:\s*"([^"]+)"', source)
    if m:
        info["driver_id"] = m.group(1)

    m = re.search(r'"name"\s*:\s*"([^"]+)"', source)
    if m:
        info["name"] = m.group(1)

    m = re.search(r'"transport"\s*:\s*"([^"]+)"', source)
    if m:
        info["transport"] = m.group(1)

    # Extract initial_state keys (not full values, but enough for validation)
    initial_state = {}
    in_initial = False
    brace_depth = 0
    for line in source.split("\n"):
        if '"initial_state"' in line and "{" in line:
            in_initial = True
            brace_depth = 1
            continue
        if in_initial:
            brace_depth += line.count("{") - line.count("}")
            m = re.search(r'"(\w+)"\s*:\s*(.+?)(?:,\s*$|$)', line.strip())
            if m:
                key = m.group(1)
                val_str = m.group(2).strip().rstrip(",")
                try:
                    initial_state[key] = ast.literal_eval(val_str)
                except (ValueError, SyntaxError):
                    initial_state[key] = val_str  # Store raw string
            if brace_depth <= 0:
                in_initial = False

    if initial_state:
        info["initial_state"] = initial_state

    return info if info else None


# ── Helper functions ──


def _compile_handler_patterns(handlers: list) -> list[tuple[re.Pattern, dict]]:
    """Compile regex patterns from simulator command handlers."""
    patterns = []
    for h in handlers:
        if not isinstance(h, dict):
            continue  # _check_handler_syntax reports the malformed entry
        pattern_str = h.get("receive") or h.get("match", "")
        if not pattern_str:
            continue
        try:
            patterns.append((re.compile(f"^{pattern_str}$"), h))
        except re.error:
            pass
    return patterns


def _find_matching_handler(
    text: str, patterns: list[tuple[re.Pattern, dict]]
) -> dict | None:
    """Find the first handler whose pattern matches the text."""
    for pattern, handler in patterns:
        if pattern.match(text):
            return handler
    return None


def _generate_sample_command(
    template: str, params: dict, driver_def: dict
) -> str | None:
    """Generate a sample command string from a send template.

    Substitutes config variables with default values and parameters
    with type-appropriate test values.
    """
    config = _effective_config(driver_def)
    result = template

    # Substitute config variables first
    result = _substitute_config(result, config)

    # Substitute parameters with test values
    for param_name, param_def in params.items():
        param_type = param_def.get("type", "string")
        if param_type in ("integer", "child_id"):
            test_val = str(param_def.get("default", param_def.get("min", 1)))
        elif param_type == "number":
            test_val = str(param_def.get("default", param_def.get("min", 1.0)))
        elif param_type == "boolean":
            test_val = "true"
        elif param_type == "enum":
            values = param_def.get("values", ["test"])
            test_val = str(values[0])
        else:
            test_val = param_def.get("default", "test")

        # Handle format specifiers like {level:02x}
        placeholder_pattern = re.compile(
            r"\{" + re.escape(param_name) + r"(:[^}]*)?\}"
        )
        match = placeholder_pattern.search(result)
        if match:
            fmt_spec = match.group(1)
            if fmt_spec:
                # Apply Python format spec
                try:
                    if "x" in fmt_spec or "X" in fmt_spec:
                        formatted = format(int(test_val), fmt_spec[1:])
                    elif "d" in fmt_spec:
                        formatted = format(int(test_val), fmt_spec[1:])
                    elif "f" in fmt_spec:
                        formatted = format(float(test_val), fmt_spec[1:])
                    else:
                        formatted = format(test_val, fmt_spec[1:])
                except (ValueError, TypeError):
                    formatted = str(test_val)
                result = result[:match.start()] + formatted + result[match.end():]
            else:
                result = result.replace(f"{{{param_name}}}", str(test_val))
        else:
            result = result.replace(f"{{{param_name}}}", str(test_val))

    # Strip line endings (literal \n in YAML becomes part of the string).
    # The final strip mirrors the simulator's dispatch, which strips each
    # incoming line before matching — without it a template with trailing
    # whitespace before its \r false-fails handler patterns.
    result = result.strip()
    result = result.replace("\\n", "").replace("\\r", "")
    result = result.rstrip("\r\n").strip()

    # Check for unresolved placeholders (config vars that weren't substituted)
    if re.search(r"\{[a-z_]+\}", result):
        return None

    return result


def _substitute_config(template: str, config: dict) -> str:
    """Substitute config variables in a template string (shared runtime rules)."""
    return safe_substitute(template, config)


def _effective_config(driver_def: dict) -> dict:
    """default_config plus config_derived values.

    Uses the same shared derivation the runtime runs, so a derived template
    whose referenced fields are all non-empty is substituted; otherwise the
    derived value is "" (so an optional prefix segment simply disappears).
    """
    config = dict(driver_def.get("default_config", {}))
    derive_config(config, driver_def.get("config_derived"))
    return config


def _build_auto_pattern(template: str, params: dict) -> re.Pattern | None:
    """Build the regex that the auto-gen system would create for a command."""
    result = template
    for param_name, param_def in params.items():
        param_type = param_def.get("type", "string")
        if param_type in ("integer", "child_id"):
            # child_id values are integer child-entity IDs (mirrors the
            # simulator's _send_template_to_regex)
            capture = r"(\d+)"
        elif param_type == "number":
            capture = r"([\d.]+)"
        elif param_type == "boolean":
            capture = r"(true|false|0|1)"
        else:
            capture = r"(.+)"

        # Handle format specifiers
        placeholder_pattern = re.compile(
            r"\{" + re.escape(param_name) + r"(:[^}]*)?\}"
        )
        result = placeholder_pattern.sub(lambda _: capture, result)

    # Escape regex specials outside captures
    escaped = ""
    in_group = 0
    for char in result:
        if char == "(":
            in_group += 1
            escaped += char
        elif char == ")":
            in_group -= 1
            escaped += char
        elif in_group > 0:
            escaped += char
        elif char in r"*+?.[]{}|^$":
            escaped += "\\" + char
        else:
            escaped += char

    # Strip line endings from the pattern
    escaped = escaped.rstrip("\\r\\n\r\n")

    try:
        return re.compile(f"^{escaped}$")
    except re.error:
        return None


def _compile_response_patterns(
    responses: list, driver_def: dict
) -> list[re.Pattern]:
    """Compile driver response patterns with config variable substitution."""
    config = _effective_config(driver_def)
    patterns = []
    for resp in responses:
        pattern_str = resp.get("match", "")
        if not pattern_str:
            continue
        # Substitute config variables in response patterns
        pattern_str = _substitute_config(pattern_str, config)
        try:
            patterns.append(re.compile(pattern_str))
        except re.error:
            pass
    return patterns


def _json_rule_keys(responses: list) -> set[str]:
    """Top-level JSON keys mapped by ``json: true`` response rules.

    Used by the round-trip checks: a simulated body that parses as a JSON
    object carrying one of these keys IS parsed by the driver (via
    _apply_json_responses), even though it matches no regex pattern.
    """
    keys: set[str] = set()
    for resp in responses:
        if not isinstance(resp, dict) or not resp.get("json"):
            continue
        for spec in (resp.get("set") or {}).values():
            key = (
                spec.get("key", spec.get("path")) if isinstance(spec, dict)
                else spec
            )
            if isinstance(key, str) and key:
                keys.add(key.split(".")[0].split("[")[0])
        for mapping in resp.get("mappings") or []:
            key = mapping.get("key") if isinstance(mapping, dict) else None
            if isinstance(key, str) and key:
                keys.add(key.split(".")[0].split("[")[0])
    return keys


def _matches_json_rules(message: str, json_keys: set[str]) -> bool:
    """True when ``message`` is a JSON object carrying a json-rule key
    (mirroring _apply_json_responses, single-element array unwrap included)."""
    if not json_keys:
        return False
    try:
        obj = json.loads(message)
    except (ValueError, TypeError):
        return False
    if isinstance(obj, list) and len(obj) == 1 and isinstance(obj[0], dict):
        obj = obj[0]
    return isinstance(obj, dict) and any(k in obj for k in json_keys)


def _resolve_state_refs(template: str, state: dict) -> str:
    """Resolve {state.key} references in a template."""
    result = template
    for key, value in state.items():
        result = result.replace(f"{{state.{key}}}", str(value))
    return result


def _extract_respond_calls(handler_code: str) -> list[str]:
    """Extract respond() call arguments from inline Python handler code.

    This is a best-effort extraction — it handles simple f-string and
    string literal arguments but not complex expressions.
    """
    results = []
    for match in re.finditer(r'respond\(f?["\'](.+?)["\'](?:\s*\))', handler_code):
        results.append(match.group(1))
    return results


# ── Directory scanning ──


def _is_python_driver(path: Path) -> bool:
    """True only when the file has a `DRIVER_INFO = {...}` assignment, either at
    module level or as a class attribute.

    Python drivers almost always define `DRIVER_INFO` as a class attribute on
    their `BaseDriver` subclass (`class FooDriver(BaseDriver): DRIVER_INFO =
    {...}`), so we check both module-level statements and the bodies of
    module-level classes. Function-local assignments are deliberately ignored
    (a helper that builds a throwaway dict named DRIVER_INFO is not a driver).

    A plain substring match for "DRIVER_INFO" would also catch build scripts,
    docs, and helpers that mention the name in a comment or string literal
    (e.g. `scripts/build_index.py`), then the validator reports them as broken
    drivers. Parsing the AST keeps the check honest.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, OSError, UnicodeDecodeError):
        return False
    # Module-level statements plus the bodies of module-level classes.
    candidates = list(tree.body)
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            candidates.extend(node.body)
    for node in candidates:
        if isinstance(node, ast.Assign):
            if any(
                isinstance(t, ast.Name) and t.id == "DRIVER_INFO"
                for t in node.targets
            ):
                return True
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == "DRIVER_INFO":
                return True
    return False


def find_drivers(path: Path) -> list[tuple[Path, str]]:
    """Find all driver files in a directory.

    Returns list of (path, type) tuples where type is "yaml" or "python".
    """
    drivers = []

    if path.is_file():
        if path.suffix == ".avcdriver":
            drivers.append((path, "yaml"))
        elif path.suffix == ".py" and not path.stem.endswith("_sim"):
            if _is_python_driver(path):
                drivers.append((path, "python"))
        return drivers

    # Scan directory
    for f in sorted(path.rglob("*.avcdriver")):
        drivers.append((f, "yaml"))

    for f in sorted(path.rglob("*.py")):
        if f.stem.endswith("_sim"):
            continue
        if _is_python_driver(f):
            drivers.append((f, "python"))

    return drivers


# ── CLI ──


def main():
    parser = argparse.ArgumentParser(
        prog="simulator-validate",
        description="Validate driver simulator compatibility",
        epilog=(
            "Examples:\n"
            "  python -m simulator.validate drivers/audio/biamp_tesira_ttp.avcdriver\n"
            "  python -m simulator.validate drivers/projectors/pjlink_class1.py\n"
            "  python -m simulator.validate openavc-drivers/         # validate all\n"
            "  python -m simulator.validate openavc-drivers/ --summary\n"
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
    args = parser.parse_args()

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
        if driver_type == "yaml":
            results.append(validate_yaml_driver(driver_path))
        else:
            results.append(validate_python_driver(driver_path))

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
