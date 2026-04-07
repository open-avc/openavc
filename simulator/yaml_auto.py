"""
YAMLAutoSimulator — auto-generates a working simulator from .avcdriver files.

Reverses the driver's command/response definitions to create a simulator
that handles incoming commands, updates state, and generates responses.

Works at two levels:
  Level 0: Pure auto-gen from commands + responses + state_variables
  Level 1: Enhanced with explicit simulator: section (merged on top)
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import yaml

from simulator.tcp_simulator import TCPSimulator

logger = logging.getLogger(__name__)


class YAMLAutoSimulator(TCPSimulator):
    """Auto-generated simulator from a .avcdriver definition."""

    # Set dynamically per instance (not class-level)
    SIMULATOR_INFO: dict = {}

    def __init__(
        self,
        device_id: str,
        config: dict | None = None,
        *,
        driver_def: dict,
    ):
        # Build SIMULATOR_INFO from driver definition before calling super().__init__
        self.SIMULATOR_INFO = self._build_info(driver_def)
        super().__init__(device_id, config)

        # YAML drivers are text-based — always use flexible line reading
        # so the simulator accepts \r, \n, or \r\n regardless of the
        # response delimiter configured in the driver definition.
        self._line_mode = True

        self._driver_def = driver_def

        # Build handlers from driver definition
        self._command_handlers: list[CommandHandler] = []
        self._query_handlers: list[QueryHandler] = []
        self._state_responses: dict[str, StateResponse] = {}

        self._build_state_responses()
        self._build_command_handlers()
        self._build_query_handlers()

        # Merge explicit simulator: section if present
        sim_section = driver_def.get("simulator", {})
        if sim_section:
            self._merge_simulator_section(sim_section)

        logger.info(
            "Auto-gen simulator for %s: %d command handlers, %d query handlers, %d state responses",
            self.driver_id,
            len(self._command_handlers),
            len(self._query_handlers),
            len(self._state_responses),
        )

    @classmethod
    def from_avcdriver(
        cls,
        path: Path,
        device_id: str,
        config: dict | None = None,
    ) -> YAMLAutoSimulator:
        """Create a simulator from an .avcdriver file path."""
        with open(path, encoding="utf-8") as f:
            driver_def = yaml.safe_load(f)
        return cls(device_id=device_id, config=config, driver_def=driver_def)

    # ── Protocol handling ──

    def handle_command(self, data: bytes) -> bytes | None:
        text = data.decode("utf-8", errors="replace").strip()
        if not text:
            return None

        # Try explicit handlers first (from simulator: command_handlers)
        for handler in self._explicit_handlers:
            m = handler.pattern.match(text)
            if m:
                return self._execute_explicit_handler(handler, m)

        # Try script handlers (match: + handler: with inline Python)
        for handler in self._script_handlers:
            m = handler.pattern.match(text)
            if m:
                return self._execute_script_handler(handler, m)

        # Try auto-generated command handlers
        for handler in self._command_handlers:
            m = handler.pattern.match(text)
            if m:
                return self._execute_command_handler(handler, m)

        # Try query handlers
        for handler in self._query_handlers:
            m = handler.pattern.match(text)
            if m:
                return self._execute_query_handler(handler)

        logger.debug("%s: unrecognized command: %r", self.device_id, text)
        return None

    def _execute_command_handler(self, handler: CommandHandler, m: re.Match) -> bytes | None:
        """Execute a command handler: update state and generate response."""
        delimiter = self._get_delimiter()

        # Apply state changes
        for state_key, source in handler.state_changes.items():
            if isinstance(source, int) and not isinstance(source, bool):
                # Capture group index
                value = m.group(source)
                value = self._coerce_value(state_key, value)
            else:
                # Literal value
                value = source
            self.set_state(state_key, value)

        # Generate response for the primary state variable
        if handler.response_var and handler.response_var in self._state_responses:
            resp = self._state_responses[handler.response_var]
            response_text = resp.format(self._state.get(handler.response_var))
            return (response_text + delimiter).encode()

        return None

    def _execute_query_handler(self, handler: QueryHandler) -> bytes | None:
        """Execute a query handler: respond with current state."""
        delimiter = self._get_delimiter()
        resp = self._state_responses.get(handler.response_var)
        if resp:
            value = self._state.get(handler.response_var)
            response_text = resp.format(value)
            return (response_text + delimiter).encode()
        return None

    def _execute_explicit_handler(self, handler: ExplicitHandler, m: re.Match) -> bytes | None:
        """Execute an explicit command_handler from the simulator: section."""
        # Apply state changes
        for key, val in handler.set_state.items():
            resolved = self._resolve_template(str(val), m)
            self.set_state(key, self._coerce_value(key, resolved))

        # Generate response
        if handler.respond:
            response_text = self._resolve_template(handler.respond, m)
            return response_text.encode()

        return None

    def _execute_script_handler(self, handler: ScriptHandler, m: re.Match) -> bytes | None:
        """Execute a script handler (match: + handler: with inline Python).

        Scripts get: match (regex match), state (proxy dict that notifies on
        writes), config (device config), respond(text) (send response).
        """
        response_data: list[str] = []

        def respond(text: str) -> None:
            response_data.append(text)

        # Wrap state in a proxy so writes go through set_state (triggers UI)
        state_proxy = _StateProxy(self._state, self.set_state)

        namespace = {
            "match": m,
            "state": state_proxy,
            "config": self.config,
            "respond": respond,
            "int": int,
            "float": float,
            "str": str,
            "bool": bool,
            "max": max,
            "min": min,
            "format": format,
            "round": round,
            "abs": abs,
            "len": len,
            "range": range,
            "list": list,
            "dict": dict,
            "set": set,
            "tuple": tuple,
            "sorted": sorted,
            "enumerate": enumerate,
            "True": True,
            "False": False,
            "None": None,
        }

        try:
            exec(handler.code, {"__builtins__": {}}, namespace)  # noqa: S102
        except Exception:
            logger.exception(
                "%s: script handler error for pattern %s",
                self.device_id, handler.pattern.pattern,
            )
            return None

        if response_data:
            return response_data[0].encode()
        return None

    # ── Build handlers from driver definition ──

    def _build_state_responses(self) -> None:
        """Build state_var → response format mapping from responses: section."""
        responses = self._driver_def.get("responses", [])

        for resp_def in responses:
            match_pattern = resp_def.get("match", "")
            set_dict = resp_def.get("set", {})

            for state_key, set_value in set_dict.items():
                set_value_str = str(set_value)

                if state_key not in self._state_responses:
                    self._state_responses[state_key] = StateResponse(state_key)

                sr = self._state_responses[state_key]

                # Check if the response has capture groups (template-based)
                # vs fixed text (value-mapped)
                has_groups = bool(re.search(r"\(.*\)", match_pattern))

                if has_groups and set_value_str.startswith("$"):
                    # Template-based: In(\d+) All with set: { input: "$1" }
                    # → template: In{value} All
                    template = _regex_to_template(match_pattern)
                    if not sr.template:
                        sr.template = template
                else:
                    # Value-mapped: Amt1 with set: { mute: "true" }
                    # → value "true" maps to response text "Amt1"
                    # Reconstruct the literal response text from the regex
                    literal = _regex_to_literal(match_pattern)
                    if literal:
                        sr.value_map[set_value_str] = literal

    def _build_command_handlers(self) -> None:
        """Build command handlers from commands: section."""
        commands = self._driver_def.get("commands", {})
        state_vars = set(self._driver_def.get("state_variables", {}).keys())

        for cmd_name, cmd_def in commands.items():
            send_template = cmd_def.get("send", "")
            if not send_template:
                continue

            params = cmd_def.get("params", {})

            # Convert send template to regex
            pattern_str = _send_template_to_regex(send_template, params)
            try:
                pattern = re.compile(f"^{pattern_str}$")
            except re.error:
                logger.warning("Invalid regex from command '%s': %s", cmd_name, pattern_str)
                continue

            # Determine state changes
            state_changes: dict[str, int | Any] = {}
            response_var: str | None = None

            # Heuristic 1: param name matches state variable
            group_idx = 1
            for param_name in params:
                if param_name in state_vars:
                    state_changes[param_name] = group_idx  # capture group
                    if not response_var:
                        response_var = param_name
                group_idx += 1

            # Heuristic 2: command name patterns
            if not state_changes:
                target = _infer_state_var(cmd_name, state_vars)
                if target:
                    response_var = target
                    if cmd_name.endswith("_on") or cmd_name.startswith("enable_"):
                        state_changes[target] = True
                    elif cmd_name.endswith("_off") or cmd_name.startswith("disable_"):
                        state_changes[target] = False
                    elif cmd_name.endswith("_toggle"):
                        # Toggle handled specially — for now, just report current
                        pass
                    elif params:
                        # set_X with params → first param is the value
                        state_changes[target] = 1  # capture group 1

            # Heuristic 3: if still no response var, try command name for queries
            if not response_var and not params:
                target = _infer_state_var(cmd_name, state_vars)
                if target:
                    response_var = target

            handler = CommandHandler(
                name=cmd_name,
                pattern=pattern,
                state_changes=state_changes,
                response_var=response_var,
            )
            self._command_handlers.append(handler)

            if not state_changes and not response_var:
                logger.debug(
                    "Auto-sim %s: could not infer behavior for command '%s'",
                    self.driver_id, cmd_name,
                )

    def _build_query_handlers(self) -> None:
        """Build query handlers from polling.queries section."""
        polling = self._driver_def.get("polling", {})
        queries = polling.get("queries", [])
        state_vars = set(self._driver_def.get("state_variables", {}).keys())

        # Also check commands for query-like commands without params
        commands = self._driver_def.get("commands", {})

        for query in queries:
            if isinstance(query, dict):
                query_text = query.get("send", "")
            else:
                query_text = str(query)

            if not query_text:
                continue

            # Find which state variable this query is for
            # Look for a command with this exact send template
            response_var = None
            for cmd_name, cmd_def in commands.items():
                if cmd_def.get("send") == query_text and not cmd_def.get("params"):
                    target = _infer_state_var(cmd_name, state_vars)
                    if target:
                        response_var = target
                        break

            # If we couldn't match via command name, try matching the query text
            # to a response pattern
            if not response_var:
                response_var = self._infer_query_response_var(query_text)

            if response_var:
                pattern = re.compile(f"^{re.escape(query_text)}$")
                self._query_handlers.append(QueryHandler(
                    pattern=pattern,
                    response_var=response_var,
                ))

    def _infer_query_response_var(self, query_text: str) -> str | None:
        """Try to figure out which state var a query returns.

        Simple heuristic: if a single-character query exists and a response
        pattern sets a state var, see if there's a conventional mapping.
        """
        # Common AV protocol query mappings
        query_map = {
            "I": "input",
            "V": "volume",
            "Z": "mute",
            "P": "power",
        }
        return query_map.get(query_text.strip())

    # ── Merge explicit simulator: section ──

    def _merge_simulator_section(self, sim: dict) -> None:
        """Merge explicit simulator: enhancements onto auto-generated behavior."""
        # Override initial state
        for key, value in sim.get("initial_state", {}).items():
            self._state[key] = value

        # Override delays
        for key, value in sim.get("delays", {}).items():
            self._delays[key] = value

        # Add error modes
        for mode, mode_def in sim.get("error_modes", {}).items():
            self._error_modes[mode] = mode_def

        # Add declarative controls schema
        controls = sim.get("controls")
        if controls:
            self.SIMULATOR_INFO["controls"] = controls

        # Build state machines
        from simulator.base import StateMachine
        for name, sm_def in sim.get("state_machines", {}).items():
            self._state_machines[name] = StateMachine(
                name=name,
                states=sm_def["states"],
                initial=sm_def["initial"],
                transitions=sm_def["transitions"],
                on_change=lambda key, val: self.set_state(key, val),
            )
            self._state[name] = sm_def["initial"]

        # Build command handlers from simulator: section
        # Two formats supported:
        #   receive: + respond:/set_state: → ExplicitHandler (template-based)
        #   match: + handler: → ScriptHandler (inline Python)
        self._explicit_handlers: list[ExplicitHandler] = []
        self._script_handlers: list[ScriptHandler] = []
        for handler_def in sim.get("command_handlers", []):
            # Accept both "receive" and "match" as the pattern key
            pattern_str = handler_def.get("receive") or handler_def.get("match", "")
            if not pattern_str:
                continue
            try:
                pattern = re.compile(f"^{pattern_str}$")
            except re.error:
                logger.warning("Invalid regex in simulator command_handler: %s", pattern_str)
                continue

            if "handler" in handler_def:
                # Script handler — inline Python code
                code = compile(handler_def["handler"], f"<sim:{self.driver_id}>", "exec")
                self._script_handlers.append(ScriptHandler(
                    pattern=pattern,
                    code=code,
                ))
            else:
                # Template handler — respond/set_state
                self._explicit_handlers.append(ExplicitHandler(
                    pattern=pattern,
                    respond=handler_def.get("respond"),
                    set_state=handler_def.get("set_state", {}),
                ))

    # Ensure handler lists exist even without simulator: section
    _explicit_handlers: list[ExplicitHandler] = []
    _script_handlers: list[ScriptHandler] = []

    # ── Helpers ──

    def _get_delimiter(self) -> str:
        """Get the line delimiter as a string."""
        delim = self._driver_def.get("delimiter", "\r\n")
        # Handle escaped sequences
        return delim.replace("\\r", "\r").replace("\\n", "\n")

    def _coerce_value(self, state_key: str, value: Any) -> Any:
        """Coerce a value to the state variable's declared type."""
        state_vars = self._driver_def.get("state_variables", {})
        var_def = state_vars.get(state_key, {})
        var_type = var_def.get("type", "string")

        if var_type == "integer":
            try:
                return int(value)
            except (ValueError, TypeError):
                return 0
        elif var_type == "number":
            try:
                return float(value)
            except (ValueError, TypeError):
                return 0.0
        elif var_type == "boolean":
            if isinstance(value, bool):
                return value
            return str(value).lower() in ("true", "1", "on", "yes")
        else:
            return str(value)

    def _resolve_template(self, template: str, match: re.Match | None = None) -> str:
        """Resolve {1}, {2}, {state.key} placeholders in a template."""
        result = template

        # Replace capture group references {1}, {2}
        if match:
            for i in range(1, match.lastindex + 1 if match.lastindex else 1):
                try:
                    result = result.replace(f"{{{i}}}", match.group(i) or "")
                except IndexError:
                    pass

        # Replace state references {state.key}
        for key, value in self._state.items():
            result = result.replace(f"{{state.{key}}}", str(value))

        return result

    @staticmethod
    def _build_info(driver_def: dict) -> dict:
        """Build SIMULATOR_INFO from driver definition."""
        state_vars = driver_def.get("state_variables", {})
        initial_state = {}
        controls = []
        for key, var_def in state_vars.items():
            var_type = var_def.get("type", "string")
            label = var_def.get("label", key.replace("_", " ").title())
            if var_type == "integer":
                initial_state[key] = var_def.get("min", 0)
                v_min = var_def.get("min")
                v_max = var_def.get("max")
                if v_min is not None and v_max is not None:
                    controls.append({"type": "slider", "key": key, "label": label,
                                     "min": v_min, "max": v_max})
                else:
                    controls.append({"type": "indicator", "key": key, "label": label})
            elif var_type == "number":
                initial_state[key] = 0.0
                v_min = var_def.get("min")
                v_max = var_def.get("max")
                if v_min is not None and v_max is not None:
                    controls.append({"type": "slider", "key": key, "label": label,
                                     "min": v_min, "max": v_max, "step": 0.1})
                else:
                    controls.append({"type": "indicator", "key": key, "label": label})
            elif var_type == "boolean":
                initial_state[key] = False
                controls.append({"type": "toggle", "key": key, "label": label})
            elif var_type == "enum":
                values = var_def.get("values", [])
                initial_state[key] = values[0] if values else ""
                if key == "power":
                    controls.append({"type": "power", "key": key})
                elif values:
                    controls.append({"type": "select", "key": key, "label": label,
                                     "options": values})
                else:
                    controls.append({"type": "indicator", "key": key, "label": label})
            else:
                initial_state[key] = ""
                controls.append({"type": "indicator", "key": key, "label": label})

        info: dict = {
            "driver_id": driver_def.get("id", "unknown"),
            "name": driver_def.get("name", "Unknown") + " Simulator",
            "category": driver_def.get("category", "generic"),
            "transport": driver_def.get("transport", "tcp"),
            "default_port": driver_def.get("default_config", {}).get("port", 0),
            "delimiter": driver_def.get("delimiter"),
            "initial_state": initial_state,
            "delays": {
                "command_response": driver_def.get("default_config", {}).get(
                    "inter_command_delay", 0.05
                ),
            },
        }
        # Auto-generated controls from state_variables. These are the Level 0
        # default — if the simulator: section defines explicit controls, those
        # replace these in _merge_simulator_section().
        if controls:
            info["controls"] = controls
        return info


# ── Data classes ──

class CommandHandler:
    """Auto-generated handler for a driver command."""
    def __init__(self, name: str, pattern: re.Pattern, state_changes: dict, response_var: str | None):
        self.name = name
        self.pattern = pattern
        self.state_changes = state_changes
        self.response_var = response_var


class QueryHandler:
    """Auto-generated handler for a polling query."""
    def __init__(self, pattern: re.Pattern, response_var: str):
        self.pattern = pattern
        self.response_var = response_var


class ExplicitHandler:
    """Explicit handler from simulator: command_handlers section."""
    def __init__(self, pattern: re.Pattern, respond: str | None, set_state: dict):
        self.pattern = pattern
        self.respond = respond
        self.set_state = set_state


class ScriptHandler:
    """Script handler from simulator: command_handlers with inline Python."""
    def __init__(self, pattern: re.Pattern, code: Any):
        self.pattern = pattern
        self.code = code


class _StateProxy(dict):
    """Dict proxy that routes writes through set_state for change notification."""

    def __init__(self, state: dict, set_state_fn: Any):
        super().__init__(state)
        self._real = state
        self._set_state = set_state_fn

    def __setitem__(self, key: str, value: Any) -> None:
        self._set_state(key, value)
        super().__setitem__(key, value)

    def __getitem__(self, key: str) -> Any:
        return self._real[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self._real.get(key, default)

    def __contains__(self, key: object) -> bool:
        return key in self._real


class StateResponse:
    """Tracks how to format a response for a state variable."""
    def __init__(self, state_key: str):
        self.state_key = state_key
        self.template: str | None = None  # e.g., "In{value} All"
        self.value_map: dict[str, str] = {}  # e.g., {"true": "Amt1", "false": "Amt0"}

    def format(self, value: Any) -> str:
        """Generate response text for the given value."""
        value_str = str(value).lower() if isinstance(value, bool) else str(value)

        # Check value map first (for boolean/enum values)
        if value_str in self.value_map:
            return self.value_map[value_str]

        # Use template
        if self.template:
            return self.template.replace("{value}", str(value))

        # Fallback
        return str(value)


# ── Utility functions ──

def _send_template_to_regex(template: str, params: dict) -> str:
    """Convert a command send template to a regex for matching incoming data.

    Examples:
        "{input}!" with params {input: {type: integer}} → "(\\d+)!"
        "{level}V" with params {level: {type: integer}} → "(\\d+)V"
        "1Z" with no params → "1Z"
        "{input}*{output}!" → "(\\d+)\\*(\\d+)!"
    """
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
        result = result.replace(f"{{{param_name}}}", capture)

    # Escape regex special chars that aren't part of our captures
    # We need to be careful: only escape chars outside of capture groups
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

    return escaped


def _regex_to_template(pattern: str) -> str:
    """Convert a response regex to a response template.

    Replaces the first capture group with {value}.
    Example: 'In(\\d+) All' → 'In{value} All'
    """
    # Remove the capture group and replace with {value}
    result = re.sub(r"\([^)]*\)", "{value}", pattern, count=1)
    # Remove remaining regex escapes
    result = result.replace("\\d", "").replace("\\S", "").replace("\\w", "")
    result = result.replace("+", "").replace("*", "").replace("?", "")
    return result


def _regex_to_literal(pattern: str) -> str | None:
    """Convert a simple regex (no capture groups) to a literal string.

    Returns None if the pattern is too complex.
    Example: 'Amt1' → 'Amt1'
    """
    # If it has capture groups, it's not a literal
    if "(" in pattern:
        return None
    # Remove simple regex escapes
    result = pattern.replace("\\", "")
    # If it still has regex metacharacters, it's too complex
    if any(c in result for c in "[]{}*+?.^$|"):
        return None
    return result


def _infer_state_var(cmd_name: str, state_vars: set[str]) -> str | None:
    """Infer which state variable a command targets from its name.

    Examples:
        "set_volume" → "volume"
        "mute_on" → "mute"
        "power_off" → "power"
        "query_input" → "input"
        "route_all" → "input" (if "input" in state_vars, best guess for routing)
    """
    # Strip common prefixes/suffixes
    name = cmd_name
    for prefix in ("set_", "get_", "query_", "enable_", "disable_"):
        if name.startswith(prefix):
            name = name[len(prefix):]
            break
    for suffix in ("_on", "_off", "_toggle"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break

    # Direct match
    if name in state_vars:
        return name

    # Common AV aliases
    aliases = {
        "route": "input",
        "route_all": "input",
        "unmute": "mute",
        "vol": "volume",
        "video_mute": "video_mute",
        "audio_mute": "mute",
    }
    alias_target = aliases.get(name) or aliases.get(cmd_name)
    if alias_target and alias_target in state_vars:
        return alias_target

    return None
