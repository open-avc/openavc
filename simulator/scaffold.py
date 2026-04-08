"""
Scaffold tool — generates simulator skeleton files from Python driver DRIVER_INFO.

Usage:
    python -m simulator.scaffold path/to/driver.py
    python -m simulator.scaffold path/to/driver.py --output path/to/output_sim.py

Reads the driver's DRIVER_INFO dict and generates a ready-to-edit simulator
file with all state variables, commands, and example code pre-populated.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(
        description="Generate a simulator skeleton from a Python driver",
    )
    parser.add_argument("driver_path", help="Path to the Python driver file")
    parser.add_argument(
        "--output", "-o",
        help="Output file path (default: <driver>_sim.py alongside the driver)",
    )
    args = parser.parse_args()

    driver_path = Path(args.driver_path)
    if not driver_path.exists():
        print(f"Error: {driver_path} does not exist", file=sys.stderr)
        sys.exit(1)

    # Extract DRIVER_INFO from the driver file
    driver_info = extract_driver_info(driver_path)
    if not driver_info:
        print(f"Error: Could not find DRIVER_INFO in {driver_path}", file=sys.stderr)
        sys.exit(1)

    # Determine output path
    if args.output:
        output_path = Path(args.output)
    else:
        output_path = driver_path.parent / f"{driver_path.stem}_sim.py"

    # Generate skeleton
    skeleton = generate_skeleton(driver_info, driver_path.stem)

    output_path.write_text(skeleton, encoding="utf-8")
    print(f"Generated simulator skeleton: {output_path}")
    print("\nAfter filling in the handler, validate with:")
    print(f"  python -m simulator.validate {driver_path}")


def extract_driver_info(driver_path: Path) -> dict | None:
    """Extract DRIVER_INFO dict from a Python driver file.

    Uses AST parsing to find the DRIVER_INFO assignment without importing
    the driver (which may have dependencies we don't have).
    Falls back to regex extraction for complex cases.
    """
    source = driver_path.read_text(encoding="utf-8")

    # Try AST parsing first
    try:
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "DRIVER_INFO":
                        return ast.literal_eval(node.value)
            elif isinstance(node, ast.AnnAssign):
                if isinstance(node.target, ast.Name) and node.target.id == "DRIVER_INFO":
                    if node.value:
                        return ast.literal_eval(node.value)
    except (SyntaxError, ValueError):
        pass

    # AST didn't work (likely because DRIVER_INFO references variables like INPUT_MAP).
    # Fall back to regex-based extraction of key fields.
    info = {}

    # Extract simple string fields
    for field in ("id", "name", "manufacturer", "category", "transport"):
        m = re.search(rf'"{field}"\s*:\s*"([^"]+)"', source)
        if m:
            info[field] = m.group(1)

    # Extract default_config for port
    m = re.search(r'"port"\s*:\s*(\d+)', source)
    if m:
        info.setdefault("default_config", {})["port"] = int(m.group(1))

    # Extract state_variables block
    state_vars = {}
    # Find state_variables dict region
    sv_match = re.search(r'"state_variables"\s*:\s*\{', source)
    if sv_match:
        # Extract individual variable entries
        region_start = sv_match.end()
        # Find each variable entry
        for vm in re.finditer(
            r'"(\w+)"\s*:\s*\{[^}]*"type"\s*:\s*"(\w+)"[^}]*"label"\s*:\s*"([^"]+)"',
            source[region_start:region_start + 2000],
        ):
            state_vars[vm.group(1)] = {
                "type": vm.group(2),
                "label": vm.group(3),
            }
    info["state_variables"] = state_vars

    # Extract commands
    commands = {}
    cmd_matches = re.finditer(
        r'"(\w+)"\s*:\s*\{[^}]*"label"\s*:\s*"([^"]+)"',
        source,
    )
    # Only include entries that look like commands (have "label" and "help" or "params")
    for cm in cmd_matches:
        name = cm.group(1)
        label = cm.group(2)
        # Check if this looks like a command (near "params" or "help")
        context = source[cm.start():cm.start() + 500]
        if '"help"' in context or '"params"' in context:
            # Extract params if present
            params = {}
            param_matches = re.finditer(
                r'"(\w+)"\s*:\s*\{[^}]*"type"\s*:\s*"(\w+)"',
                context[len(cm.group(0)):],
            )
            for pm in param_matches:
                if pm.group(1) not in ("type", "label", "help", "required"):
                    params[pm.group(1)] = {"type": pm.group(2)}
            commands[name] = {"label": label, "params": params}

    info["commands"] = commands

    return info if info.get("id") else None


def generate_skeleton(info: dict, driver_stem: str) -> str:
    """Generate a simulator skeleton Python file."""
    driver_id = info.get("id", "unknown")
    name = info.get("name", "Unknown Device")
    category = info.get("category", "generic")
    transport = info.get("transport", "tcp")
    default_port = info.get("default_config", {}).get("port", 0)
    state_vars = info.get("state_variables", {})
    commands = info.get("commands", {})

    # Build class name from driver stem
    class_name = "".join(
        part.capitalize() for part in driver_stem.replace("-", "_").split("_")
    ) + "Simulator"

    # Build initial state
    initial_state_lines = []
    state_comments = []
    for var_name, var_def in state_vars.items():
        var_type = var_def.get("type", "string")
        label = var_def.get("label", var_name)
        default = _default_for_type(var_type)
        initial_state_lines.append(f'            "{var_name}": {default},')
        state_comments.append(f"            {var_name:20s} ({var_type:8s}) — {label}")

    initial_state_block = "\n".join(initial_state_lines) if initial_state_lines else '            # (no state variables found in DRIVER_INFO)'

    # Build command documentation
    command_docs = []
    for cmd_name, cmd_def in commands.items():
        label = cmd_def.get("label", cmd_name)
        params = cmd_def.get("params", {})
        if params:
            param_strs = [f"{p}: {d.get('type', '?')}" for p, d in params.items()]
            command_docs.append(f"            {cmd_name:20s} — {label} (params: {', '.join(param_strs)})")
        else:
            command_docs.append(f"            {cmd_name:20s} — {label}")

    command_doc_block = "\n".join(command_docs) if command_docs else "            (no commands found in DRIVER_INFO)"
    state_comment_block = "\n".join(state_comments) if state_comments else "            (no state variables found)"

    # Choose base class based on transport
    if transport == "http":
        base_import = "from simulator.http_simulator import HTTPSimulator"
        base_class = "HTTPSimulator"
        handler_method = _http_handler_template(commands, state_vars, command_doc_block, state_comment_block)
    else:
        base_import = "from simulator.tcp_simulator import TCPSimulator"
        base_class = "TCPSimulator"
        handler_method = _tcp_handler_template(commands, state_vars, command_doc_block, state_comment_block)

    return f'''"""
{name} — Simulator
Auto-generated skeleton. Fill in the handler method with protocol logic.

Driver: {driver_id}
Transport: {transport}
"""
{base_import}


class {class_name}({base_class}):

    SIMULATOR_INFO = {{
        "driver_id": "{driver_id}",
        "name": "{name} Simulator",
        "category": "{category}",
        "transport": "{transport}",
        "default_port": {default_port},
        "initial_state": {{
{initial_state_block}
        }},
        "delays": {{
            "command_response": 0.05,
        }},
        "error_modes": {{
            # Add error modes relevant to this device, e.g.:
            # "no_signal": {{
            #     "description": "No input signal detected",
            # }},
        }},
    }}

{handler_method}
'''


def _tcp_handler_template(commands: dict, state_vars: dict, cmd_docs: str, state_docs: str) -> str:
    return f'''    def handle_command(self, data: bytes) -> bytes | None:
        """
        Parse incoming bytes from the driver, return response bytes.

        Available helpers:
            self.state              — dict of current state values
            self.set_state(k, v)    — update state (triggers UI refresh)
            self.active_errors      — set of currently active error mode names

        Driver commands to handle:
{cmd_docs}

        State variables to maintain:
{state_docs}
        """
        # TODO: Implement protocol parsing and response generation.
        #
        # Example for a text protocol:
        #   text = data.decode().strip()
        #   if text == "POWER ON":
        #       self.set_state("power", "on")
        #       return b"OK\\r\\n"
        #
        # Example for a binary protocol:
        #   if len(data) >= 4 and data[0] == 0xAA:
        #       cmd = data[1]
        #       if cmd == 0x11:  # Power query
        #           payload = [0x01 if self.state["power"] == "on" else 0x00]
        #           return self._build_response(cmd, payload)

        return None'''


def _http_handler_template(commands: dict, state_vars: dict, cmd_docs: str, state_docs: str) -> str:
    return f'''    def handle_request(
        self,
        method: str,
        path: str,
        headers: dict[str, str],
        body: str,
    ) -> tuple[int, dict | str]:
        """
        Handle incoming HTTP request from the driver.
        Return (status_code, response_body).

        Available helpers:
            self.state              — dict of current state values
            self.set_state(k, v)    — update state (triggers UI refresh)
            self.active_errors      — set of currently active error mode names

        Driver commands to handle:
{cmd_docs}

        State variables to maintain:
{state_docs}
        """
        # TODO: Implement API endpoint handlers.
        #
        # Example for a JSON API:
        #   import json
        #   if path == "/api/power" and method == "POST":
        #       req = json.loads(body)
        #       self.set_state("power", req.get("power", "off"))
        #       return 200, {{"status": "ok"}}
        #   if path == "/api/status" and method == "GET":
        #       return 200, self.state

        return 404, {{"error": "not found"}}'''


def _default_for_type(var_type: str) -> str:
    """Return a Python literal default value for a type."""
    if var_type == "integer":
        return "0"
    elif var_type == "number":
        return "0.0"
    elif var_type == "boolean":
        return "False"
    elif var_type == "enum":
        return '"off"'
    else:
        return '""'


if __name__ == "__main__":
    main()
