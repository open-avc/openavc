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
    3. Response parsing — simulator responses match the driver's response patterns
    4. Poll coverage    — every polling query has a matching handler
    5. Type consistency — boolean/enum/number types are handled correctly

  Python drivers (.py + _sim.py):
    1. SIMULATOR_INFO   — required fields present (driver_id, name, initial_state)
    2. State coverage   — every DRIVER_INFO state_variable covered in initial_state
    3. driver_id match  — DRIVER_INFO.id == SIMULATOR_INFO.driver_id
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

import yaml


# ── Result types ──


class Issue:
    """A single validation issue."""

    def __init__(self, severity: str, check: str, message: str):
        self.severity = severity  # "error" or "warning"
        self.check = check  # e.g., "state_coverage", "command_coverage"
        self.message = message

    def __str__(self) -> str:
        icon = "ERROR" if self.severity == "error" else "WARN "
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
    def passed(self) -> bool:
        return len(self.errors) == 0

    def error(self, check: str, message: str) -> None:
        self.issues.append(Issue("error", check, message))

    def warning(self, check: str, message: str) -> None:
        self.issues.append(Issue("warning", check, message))


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

    # ── Check 1: State variable coverage ──
    _check_state_coverage(result, state_vars, sim_initial, sim)

    # ── Check 2: Command handler coverage ──
    _check_command_coverage(result, commands, sim_handlers, driver_def)

    # ── Check 3: Response parsing (round-trip) ──
    _check_response_parsing(result, commands, sim_handlers, responses, sim_initial, driver_def)

    # ── Check 4: Poll query coverage ──
    _check_poll_coverage(result, queries, sim_handlers, driver_def)

    # ── Check 5: Type consistency ──
    _check_type_consistency(result, state_vars, sim_initial, sim_handlers)

    return result


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
            # Check if auto-gen would provide a default
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
            # Auto-gen provides a default, but the simulator section overrides
            # initial_state completely — warn that the variable is relying on
            # auto-gen defaults which may not be appropriate.
            result.warning(
                "state_coverage",
                f"'{var_name}' not in simulator initial_state "
                f"(auto-gen default: {default!r})"
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

    # Check explicit handler responses
    for handler_def in sim_handlers:
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
            _check_single_response(result, response_text, response_patterns, handler_def)

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
                _check_single_response(result, response_text, response_patterns, handler_def)


def _check_single_response(
    result: ValidationResult,
    response_text: str,
    response_patterns: list[re.Pattern],
    handler_def: dict,
) -> None:
    """Check if a single response text matches any response pattern."""
    # Skip empty responses or pure ACKs
    if not response_text or response_text in ("+OK", "OK", "sr"):
        return

    matched = any(p.search(response_text) for p in response_patterns)
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
    config = driver_def.get("default_config", {})

    for query in queries:
        if isinstance(query, dict):
            query_text = query.get("send", "")
        else:
            query_text = str(query)

        if not query_text:
            continue

        # Substitute config variables in query text
        sample = _substitute_config(query_text, config)
        sample = sample.strip().replace("\\n", "").replace("\\r", "")
        sample = sample.rstrip("\r\n")

        matched = _find_matching_handler(sample, handler_patterns)
        if not matched:
            # Check if auto-gen query handlers would cover this
            # (auto-gen creates handlers from commands with no params)
            commands = driver_def.get("commands", {})
            auto_covered = False
            for cmd_def in commands.values():
                if cmd_def.get("send") == query_text and not cmd_def.get("params"):
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
    # handleable by the simulator. For Python sims we check the source
    # for method names or command string references.
    sim_source = sim_path.read_text(encoding="utf-8")
    driver_commands = driver_info.get("commands", {})
    for cmd_name in driver_commands:
        # Check if the command name appears in the simulator source
        if cmd_name not in sim_source and f'"{cmd_name}"' not in sim_source:
            # For Python sims, commands are handled by handle_command() which
            # processes binary/protocol data, not command names. This is just
            # a soft check.
            pass  # Python sims handle commands at the protocol level

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
    config = driver_def.get("default_config", {})
    result = template

    # Substitute config variables first
    result = _substitute_config(result, config)

    # Substitute parameters with test values
    for param_name, param_def in params.items():
        param_type = param_def.get("type", "string")
        if param_type == "integer":
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

    # Strip line endings (literal \n in YAML becomes part of the string)
    result = result.strip()
    result = result.replace("\\n", "").replace("\\r", "")
    result = result.rstrip("\r\n")

    # Check for unresolved placeholders (config vars that weren't substituted)
    if re.search(r"\{[a-z_]+\}", result):
        return None

    return result


def _substitute_config(template: str, config: dict) -> str:
    """Substitute config variables in a template string."""
    result = template
    for key, value in config.items():
        result = result.replace(f"{{{key}}}", str(value))
    return result


def _build_auto_pattern(template: str, params: dict) -> re.Pattern | None:
    """Build the regex that the auto-gen system would create for a command."""
    result = template
    for param_name, param_def in params.items():
        param_type = param_def.get("type", "string")
        if param_type == "integer":
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
    config = driver_def.get("default_config", {})
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


def find_drivers(path: Path) -> list[tuple[Path, str]]:
    """Find all driver files in a directory.

    Returns list of (path, type) tuples where type is "yaml" or "python".
    """
    drivers = []

    if path.is_file():
        if path.suffix == ".avcdriver":
            drivers.append((path, "yaml"))
        elif path.suffix == ".py" and not path.stem.endswith("_sim"):
            # Check if it has DRIVER_INFO
            source = path.read_text(encoding="utf-8")
            if "DRIVER_INFO" in source:
                drivers.append((path, "python"))
        return drivers

    # Scan directory
    for f in sorted(path.rglob("*.avcdriver")):
        drivers.append((f, "yaml"))

    for f in sorted(path.rglob("*.py")):
        if f.stem.endswith("_sim"):
            continue
        source = f.read_text(encoding="utf-8")
        if "DRIVER_INFO" in source:
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
    passed = 0
    failed = 0

    for r in results:
        if args.summary:
            status = "PASS" if r.passed else "FAIL"
            issue_str = ""
            if r.errors or r.warnings:
                parts = []
                if r.errors:
                    parts.append(f"{len(r.errors)} errors")
                if r.warnings:
                    parts.append(f"{len(r.warnings)} warnings")
                issue_str = f" ({', '.join(parts)})"
            print(f"{status} {r.driver_id} [{r.driver_type}]{issue_str}")
        else:
            if r.issues:
                status = "FAIL" if r.errors else "WARN"
                print(f"\n{status}: {r.driver_id} [{r.driver_type}] ({r.driver_path})")
                for issue in r.issues:
                    print(str(issue))
            else:
                print(f"\nPASS: {r.driver_id} [{r.driver_type}]")

        total_errors += len(r.errors)
        total_warnings += len(r.warnings)
        if r.passed:
            passed += 1
        else:
            failed += 1

    # Summary line
    print(f"\n{'='*60}")
    print(
        f"{len(results)} drivers validated: "
        f"{passed} passed, {failed} failed, "
        f"{total_errors} errors, {total_warnings} warnings"
    )

    sys.exit(1 if total_errors > 0 else 0)


if __name__ == "__main__":
    main()
