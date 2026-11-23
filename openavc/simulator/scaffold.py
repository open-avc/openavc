"""
Scaffold tool — generates simulator skeleton files from Python driver DRIVER_INFO.

Usage:
    python -m openavc.simulator.scaffold path/to/driver.py
    python -m openavc.simulator.scaffold path/to/driver.py --output path/to/output_sim.py

Reads the driver's DRIVER_INFO dict and generates a ready-to-edit simulator
file with all state variables, commands, and example code pre-populated.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path

from openavc.drivers.spec import ValueTypeTraits, transport_traits, value_type_traits


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
    try:
        skeleton = generate_skeleton(driver_info, driver_path.stem)
    except ValueError as refused:
        print(f"Error: {refused}", file=sys.stderr)
        sys.exit(1)

    output_path.write_text(skeleton, encoding="utf-8")
    print(f"Generated simulator skeleton: {output_path}")
    print("\nAfter filling in the handler, validate with:")
    print(f"  python -m openavc.simulator.validate {driver_path}")


def extract_driver_info(driver_path: Path) -> dict | None:
    """Extract DRIVER_INFO dict from a Python driver file.

    Reads the driver's source rather than importing it (a driver may need
    dependencies this tool does not have, and importing runs arbitrary code).

    This is the platform's own reader, not a second one. It used to be a
    ``literal_eval`` that fell back to scanning the whole file with regexes
    the moment a driver referenced a module constant — which is most of them.
    That fallback did not merely miss things, it invented: measured across the
    61 shipped Python drivers it disagreed with the real declarations on 42,
    reporting section names like ``state_variables`` and ``commands`` as
    command names and pairing labels with the wrong entries. Everything
    downstream — this scaffold's generated skeleton, and the Python half of
    ``openavc.simulator.validate``, which reads through this same function — was
    working from that.

    Values the reader genuinely cannot resolve statically (a constant, a call,
    a comprehension) come back as the ``UNEVALUATED`` marker rather than a
    guess, so a caller can tell "not declared" from "declared, not readable"
    and say so instead of quietly checking nothing.
    """
    from openavc.drivers.python_info import extract_python_driver_info_full

    info, _opaque = extract_python_driver_info_full(driver_path)
    if not info or not info.get("id"):
        return None
    return info


def generate_skeleton(info: dict, driver_stem: str) -> str:
    """Generate a simulator skeleton Python file."""
    def _plain(value, fallback):
        """Values the reader could not resolve statically must not be printed.

        ``extract_driver_info`` returns an UNEVALUATED marker where a driver
        built a value from a constant or a call. Rendering that marker into
        the generated file would put ``<unevaluated>`` in a Python literal.
        """
        return value if isinstance(value, type(fallback)) else fallback

    driver_id = _plain(info.get("id"), "unknown")
    name = _plain(info.get("name"), "Unknown Device")
    category = _plain(info.get("category"), "generic")
    transport = _plain(info.get("transport"), "tcp")
    default_config = _plain(info.get("default_config"), {})
    default_port = _plain(default_config.get("port"), 0)
    state_vars = _plain(info.get("state_variables"), {})
    commands = _plain(info.get("commands"), {})

    # Build class name from driver stem
    class_name = "".join(
        part.capitalize() for part in driver_stem.replace("-", "_").split("_")
    ) + "Simulator"

    # Build initial state
    initial_state_lines = []
    state_comments = []
    for var_name, var_def in state_vars.items():
        if not isinstance(var_def, dict):
            continue
        var_type = var_def.get("type", "string")
        label = var_def.get("label", var_name)
        default = _initial_value_for(var_name, var_def)
        if var_type == "enum" and not isinstance(var_def.get("values"), list):
            initial_state_lines.append(
                "            # TODO: this enum's values are computed — pick one "
                "the driver accepts.",
            )
        initial_state_lines.append(f'            "{var_name}": {default},')
        state_comments.append(f"            {var_name:20s} ({var_type:8s}) — {label}")

    initial_state_block = "\n".join(initial_state_lines) if initial_state_lines else '            # (no state variables found in DRIVER_INFO)'

    # Build command documentation
    command_docs = []
    for cmd_name, cmd_def in commands.items():
        if not isinstance(cmd_name, str) or not isinstance(cmd_def, dict):
            continue
        label = _plain(cmd_def.get("label"), cmd_name)
        params = _plain(cmd_def.get("params"), {})
        param_strs = [
            f"{p}: {_plain(d.get('type'), '?') if isinstance(d, dict) else '?'}"
            for p, d in params.items()
            if isinstance(p, str)
        ]
        if param_strs:
            command_docs.append(f"            {cmd_name:20s} — {label} (params: {', '.join(param_strs)})")
        else:
            command_docs.append(f"            {cmd_name:20s} — {label}")

    command_doc_block = "\n".join(command_docs) if command_docs else "            (no commands found in DRIVER_INFO)"
    state_comment_block = "\n".join(state_comments) if state_comments else "            (no state variables found)"

    base_import, base_class, handler = _server_template(transport)
    handler_method = handler(commands, state_vars, command_doc_block, state_comment_block)

    # Framing. The simulator never reads the driver's delimiter — it uses its
    # own — so a line-framed driver paired with a simulator that declares none
    # gets raw chunks and has to reassemble them by hand, with nothing saying
    # why. Carry it across when the driver declared one.
    # Only where the driver sends raw lines (the transport table's line_form):
    # an HTTP, OSC, MQTT or SNMP simulator frames by its protocol.
    traits = transport_traits(transport)
    framed = traits is None or traits.line_form == "raw"
    delimiter_literal = _delimiter_literal(info)
    if not framed:
        delimiter_block = ""
    elif delimiter_literal:
        delimiter_block = (
            f'        "delimiter": {delimiter_literal},'
            "  # carried from the driver's DRIVER_INFO\n"
        )
    else:
        delimiter_block = (
            '        # "delimiter": "\\r\\n",  # the driver declares none. If it '
            'sets one\n'
            "        # in _transport_kwargs (code, which this cannot read), "
            "declare the same\n"
            "        # value here — otherwise this simulator receives raw "
            "chunks.\n"
        )

    child_block = _child_entity_block(info)
    checks = [
        "initial_state below holds plausible values, not this device's real ones.",
        *(["The framing line in SIMULATOR_INFO matches how the driver actually frames."] if framed else []),
        "Every command in the list below gets an answer.",
    ]
    checks = "".join(f"  {number}. {text}\n" for number, text in enumerate(checks, 1))

    return f'''"""
{name} — Simulator
Generated skeleton. Fill in the handler method with protocol logic.

Before you go further, check these — they are starting points, not
finished work:
{checks}
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
{delimiter_block}        "initial_state": {{
{initial_state_block}
        }},
        "delays": {{
            "command_response": 0.05,
        }},
        "error_modes": {{
            # "communication_timeout" (the device stops responding) is
            # offered without being declared. Add the ones particular to
            # this device, e.g.:
            # "no_signal": {{
            #     "description": "No input signal detected",
            # }},
        }},
    }}
{child_block}
{handler_method}
'''


def _server_template(transport: str) -> tuple[str, str, Callable[[dict, dict, str, str], str]]:
    """The import, base class and handler a simulator for ``transport`` is
    written on: the server the simulator serves it on (the transport table's
    ``simulator`` column), so a byte pipe with no server of its own (serial,
    ssh) is written on TCP and a UDP, MQTT or SNMP driver on its own.

    Raises ValueError for a device that opens no connection itself.
    """
    traits = transport_traits(transport)
    server = traits.simulator if traits is not None else "tcp"
    if server is None:
        raise ValueError(
            f"A {transport} device opens no connection of its own: its commands "
            "go out through its bridge, which is simulated instead, so it has "
            "no simulator file."
        )
    return _SERVER_TEMPLATES[server]


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


def _osc_handler_template(commands: dict, state_vars: dict, cmd_docs: str, state_docs: str) -> str:
    return f'''    def handle_message(
        self,
        address: str,
        args: list[tuple[str, Any]],
    ) -> list[tuple[str, list[tuple[str, Any]]]] | None:
        """
        Handle incoming OSC message from the driver.
        Return list of (address, args) response tuples, or None.

        Available helpers:
            self.state              — dict of current state values
            self.set_state(k, v)    — update state (triggers UI refresh)
            self.active_errors      — set of currently active error mode names

        Driver commands to handle:
{cmd_docs}

        State variables to maintain:
{state_docs}
        """
        # Example OSC handler:
        #   if address == "/ch/01/mix/fader" and args:
        #       self.set_state("ch01_fader", args[0][1])
        #       return [(address, args)]  # Echo back
        #   if address == "/ch/01/mix/fader":
        #       return [(address, [("f", self.state.get("ch01_fader", 0.0))])]
        #   if address == "/xremote":
        #       return None  # Subscription renewal, no response

        return None'''


def _mqtt_handler_template(commands: dict, state_vars: dict, cmd_docs: str, state_docs: str) -> str:
    return f'''    async def on_publish(self, client_id: str, topic: str, payload: bytes) -> None:
        """
        A message the driver published to this simulated broker. Update state,
        and publish what the device would answer.

        Available helpers:
            self.state              — dict of current state values
            self.set_state(k, v)    — update state (triggers UI refresh)
            self.active_errors      — set of currently active error mode names
            await self.broadcast(topic, payload)          — to every subscriber
            await self.publish_to(client_id, topic, payload)

        Driver commands to handle:
{cmd_docs}

        State variables to maintain:
{state_docs}
        """
        # Example:
        #   if topic == "device/power/set":
        #       self.set_state("power", payload.decode())
        #       await self.broadcast("device/power/state", payload)'''


def _snmp_handler_template(commands: dict, state_vars: dict, cmd_docs: str, state_docs: str) -> str:
    return f'''    # A simulated SNMP agent is its MIB: the base answers GET, GETNEXT, SET
    # and walks from OIDS. Each entry is (type_name, value, writable); a
    # two-item entry is read-only.
    #
    # Driver commands to handle:
{_commented(cmd_docs)}
    #
    # State variables to maintain:
{_commented(state_docs)}
    OIDS = {{
        # "1.3.6.1.2.1.1.1.0": ("string", "Sample device", False),
    }}

    def write_oid(self, oid: str, type_name: str, value: object) -> int:
        """A SET from the driver: apply it, and keep self.state in step."""
        status = super().write_oid(oid, type_name, value)
        # Example:
        #   if status == 0 and oid == "1.3.6.1.4.1.99999.1.1.0":
        #       self.set_state("power", "on" if value else "off")
        return status'''


def _commented(block: str) -> str:
    """A documentation block's lines as comments in a class body."""
    return "\n".join(f"    # {line.strip()}" for line in block.splitlines())


# The import, base class and handler each simulator server is written on,
# keyed by the transport table's ``simulator`` column. UDPSimulator takes the
# same handle_command as TCPSimulator.
_SERVER_TEMPLATES: dict[str, tuple[str, str, Callable[[dict, dict, str, str], str]]] = {
    "tcp": (
        "from openavc.simulator.tcp_simulator import TCPSimulator",
        "TCPSimulator", _tcp_handler_template,
    ),
    "udp": (
        "from openavc.simulator.udp_simulator import UDPSimulator",
        "UDPSimulator", _tcp_handler_template,
    ),
    "http": (
        "from openavc.simulator.http_simulator import HTTPSimulator",
        "HTTPSimulator", _http_handler_template,
    ),
    # The OSC handler uses `Any` in its signature (args: list[tuple[str, Any]]),
    # which is evaluated at runtime under PEP 604/585, so it is imported.
    "osc": (
        "from typing import Any\n\nfrom openavc.simulator.osc_simulator import OSCSimulator",
        "OSCSimulator", _osc_handler_template,
    ),
    "mqtt": (
        "from openavc.simulator.mqtt_simulator import MQTTSimulator",
        "MQTTSimulator", _mqtt_handler_template,
    ),
    "snmp": (
        "from openavc.simulator.snmp_simulator import SNMPSimulator",
        "SNMPSimulator", _snmp_handler_template,
    ),
}


def _default_for_type(var_type: str) -> str:
    """The type's zero (the value type table's) as a Python literal."""
    zero = value_type_traits(var_type).zero
    return f'"{zero}"' if isinstance(zero, str) else repr(zero)


# Names whose plausible resting value is not the type's zero. A projector that
# boots reporting 0 lamp hours and an empty model string is not a device
# anyone recognises, and the simulator guide's own Best Practice #4 says to
# start from something realistic — while this generator seeded type-zeros and
# presented them as done work. These are starting points an author edits, not
# claims about any particular product, so they key on the declared name and
# type only.
_REALISTIC_BY_NAME: dict[str, str] = {
    "lamp_hours": "1240",
    "lamp1_hours": "1240",
    "lamp2_hours": "980",
    "filter_hours": "310",
    "temperature": "42",
    "serial_number": '"SN-0001234"',
    "model": '"Model X"',
    "model_name": '"Model X"',
    "firmware_version": '"1.0.0"',
    "firmware": '"1.0.0"',
    "version": '"1.0.0"',
    "mac_address": '"00:11:22:33:44:55"',
    "ip_address": '"192.168.1.50"',
    "name": '"Device 1"',
    "device_name": '"Device 1"',
    "volume": "35",
    "brightness": "50",
    "contrast": "50",
    "sharpness": "50",
    "saturation": "50",
    "hue": "50",
    "backlight": "50",
    "signal_stable": "True",
}


def _declared_traits(var_name: str, var_type: object) -> ValueTypeTraits:
    """The value type table's row for a variable, or a refusal naming it."""
    try:
        return value_type_traits(var_type)
    except ValueError as refused:
        raise ValueError(f"state variable {var_name!r}: {refused}") from None


def _initial_value_for(var_name: str, var_def: dict) -> str:
    """Pick a plausible starting value for a declared state variable.

    Order: an enum's first declared value, then a known-realistic value for
    the name, then the declared range's low end for a bounded number, then the
    type's zero. Every one of these is a guess the author is expected to
    correct — the point is that the generated file starts from something a
    device could actually report rather than from a wall of zeros the guide
    then tells you not to use.
    """
    var_type = var_def.get("type", "string")
    traits = _declared_traits(var_name, var_type)

    if var_type == "enum":
        values = var_def.get("values")
        if isinstance(values, list) and values:
            first = values[0]
            return f'"{first}"' if isinstance(first, str) else repr(first)
        # Declared as an enum but the values are computed — say so rather than
        # seeding "off", which may not be one of them. The note goes on its own
        # line, never trailing the value: an inline comment here would swallow
        # the dict entry's comma and the generated file would not parse.
        return '""'

    known = _REALISTIC_BY_NAME.get(var_name)
    if known is not None:
        if var_type == "boolean":
            return known if known in ("True", "False") else "False"
        if traits.numeric:
            return known if known.lstrip("-").replace(".", "", 1).isdigit() else _default_for_type(var_type)
        return known

    if traits.numeric:
        low = var_def.get("min")
        high = var_def.get("max")
        if isinstance(low, (int, float)) and isinstance(high, (int, float)) and high > low:
            midpoint = low + (high - low) / 2
            if not traits.real:
                return str(int(midpoint))
            return str(round(float(midpoint), 2))
        if isinstance(low, (int, float)):
            return str(low)

    return _default_for_type(var_type)


def _delimiter_literal(info: dict) -> str | None:
    """The driver's declared frame delimiter, as a Python literal.

    Only what ``DRIVER_INFO`` declares. Measured across the shipped corpus,
    that is 4 of 61 Python drivers — 25 more set ``kwargs["delimiter"]``
    inside ``_transport_kwargs``, which is code, not a declaration, and no
    reader of declarations can see it. So the generated file carries what was
    declared and says nothing when nothing was, rather than guessing; the
    checklist comment below is what covers the other case.
    """
    delimiter = info.get("delimiter")
    if isinstance(delimiter, str) and delimiter:
        return repr(delimiter)
    if isinstance(delimiter, bytes) and delimiter:
        return repr(delimiter)
    return None


def _child_entity_block(info: dict) -> str:
    """A starting point for a driver that declares child entities.

    ``self.child_entities`` is deliberately not what this points at: it is fed
    from the device's *project* entry, so a driver that registers its roster at
    runtime finds it empty, and no shipped simulator uses it. What a simulator
    with children owes the driver is the enumeration reply, so that is what
    gets stubbed.
    """
    child_types = info.get("child_entity_types")
    if not isinstance(child_types, dict) or not child_types:
        return ""

    names = [k for k in child_types if isinstance(k, str)]
    if not names:
        return ""

    lines = [
        "",
        "    # ── Child entities ──",
        "    #",
        f"    # This driver declares {len(names)} child entity type(s): "
        f"{', '.join(names)}.",
        "    # The driver discovers its roster by asking the device, so this",
        "    # simulator has to be able to answer that question. Keep the roster",
        "    # here and answer the enumeration command from it.",
        "    #",
        "    # Do NOT reach for self.child_entities — it is populated from the",
        "    # device's project entry, so a driver that registers children at",
        "    # runtime will find it empty.",
        "",
    ]
    for name in names:
        definition = child_types[name] if isinstance(child_types.get(name), dict) else {}
        id_format = definition.get("id_format") if isinstance(definition, dict) else None
        low, high = 1, 4
        if isinstance(id_format, dict):
            if isinstance(id_format.get("min"), int):
                low = id_format["min"]
            if isinstance(id_format.get("max"), int):
                high = min(id_format["max"], low + 3)
        lines.append(f"    # {name}: ids {low}..{high} in this simulator")
    lines += [
        "    _CHILDREN = {",
    ]
    for name in names:
        lines.append(f'        "{name}": {{}},   # id -> {{property: value}}')
    lines += [
        "    }",
        "",
        "    # def _enumerate(self) -> bytes:",
        '    #     """Answer whatever command the driver uses to list children."""',
        "    #     rows = [f\"{cid},{props.get('name', '')}\"",
        '    #             for cid, props in self._CHILDREN["'
        + names[0]
        + '"].items()]',
        '    #     return ("\\r\\n".join(rows) + "\\r\\n").encode()',
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
