"""
YAMLAutoSimulator — auto-generates a working simulator from .avcdriver files.

Reverses the driver's command/response definitions to create a simulator
that handles incoming commands, updates state, and generates responses.

Works at two levels:
  Level 0: Pure auto-gen from commands + responses + state_variables
  Level 1: Enhanced with explicit simulator: section (merged on top)

Supports TCP (default), UDP, OSC, and HTTP transports. Each runs on the same
server the Python simulator bases run on — the TCP one by inheritance, the
other three through the mixins those bases are built from — so a YAML driver
and a Python driver of the same transport are simulated by the same code.
UDP/OSC drivers get a datagram server instead of a TCP stream server. HTTP
drivers get an aiohttp web server; incoming requests are synthesized to a text
command line ("METHOD /path?query") and dispatched through the same handler
machinery as TCP.

How a state value is written back on the wire (the reply tables this module
fills from the response rules) is state_replies; where the device's
notifications go (its push: block and the notifications: templates) is
the PushChannels mixin in push_channels.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
from pathlib import Path
from typing import Any, NamedTuple
from urllib.parse import unquote

import yaml

from openavc.drivers.compiled_protocol import (
    apply_send_frame,
    build_json_mappings,
    build_send_frame,
    conversion_of,
    decode_delimiter,
    reading_from_wire,
    safe_substitute,
    value_for_map_word,
    split_send_frames,
    state_var_default,
    value_to_wire,
)
from openavc.drivers.child_ids import coerce_child_local_id, page_starts
from openavc.drivers.spec import placeholder_names
from openavc.drivers.inline_protocol import (
    declare_command_params,
    derive_state_vars_from_responses,
    normalize_config_commands,
    normalize_config_responses,
    normalize_config_state_vars,
)
from openavc.transport.binary_helpers import encode_escape_sequences
from openavc.simulator.command_names import infer_state_var, name_switch
from openavc.simulator.http_simulator import HTTPServerMixin
from openavc.simulator.osc_simulator import OSCDispatchMixin
from openavc.simulator.push_channels import PushChannels
from openavc.simulator.reply_templates import (
    emit_literal,
    emit_template,
    emit_template_multi,
    fill_template,
)
from openavc.simulator.reread_queries import (
    every_query_entry,
    follow_up_queries,
    read_back_queries,
)
from openavc.simulator.state_replies import (
    ChildListAnswer,
    ChildStateResponse,
    StateResponse,
    map_key,
    rule_pattern,
)
from openavc.simulator.send_templates import (
    is_passthrough_send,
    send_param_groups,
    send_param_specs,
    send_regex,
    spec_int_base,
)
from openavc.simulator.tcp_simulator import TCPSimulator

logger = logging.getLogger(__name__)

# How many numbers start(0) tries before a side receiver's refusal stands.
_SIDE_PORT_ATTEMPTS = 5

#: The error mode a device offers when its driver skips the sign-in for a
#: blank credential (``skip_if_empty``): switched on, the device prompts for a
#: sign-in whatever its settings say, the way a unit with a password set does,
#: so what the driver does with one it cannot answer can be tried.
SIGN_IN_REQUIRED_MODE = "sign_in_required"


# Names exposed to inline `handler:` scripts (both TCP and OSC). This is the
# documented contract (writing-simulators.md "Script Handlers"): the same set
# for every transport. The exec sandbox empties __builtins__, so exception
# types must be injected explicitly — otherwise a handler's `try/except
# ValueError` raises NameError and the intended error branch is silently lost.
_SAFE_HANDLER_BUILTINS: dict[str, Any] = {
    "int": int, "float": float, "str": str, "bool": bool,
    "max": max, "min": min, "round": round, "abs": abs, "len": len,
    "re": re, "format": format, "range": range, "list": list,
    "dict": dict, "set": set, "tuple": tuple, "sorted": sorted,
    "enumerate": enumerate,
    # A JSON protocol is the one shape a handler cannot fake with `re`:
    # nesting, escaping and number formats all have to come out right, so
    # without these a JSON device's simulator carries its own reader and
    # writer -- eighty lines of parser standing between a driver and the
    # replies it is being tested against. `isinstance` comes with them
    # because walking a parsed body means asking what each node is.
    "json": json, "isinstance": isinstance,
    "True": True, "False": False, "None": None,
    # Common exception types — make try/except branches work under the
    # emptied __builtins__ sandbox.
    "Exception": Exception, "ValueError": ValueError, "TypeError": TypeError,
    "KeyError": KeyError, "IndexError": IndexError,
    "AttributeError": AttributeError, "ZeroDivisionError": ZeroDivisionError,
    "RuntimeError": RuntimeError, "StopIteration": StopIteration,
}


def _as_number(value: Any) -> float | None:
    """Return value as a float, or None if it isn't numeric.

    Used for min/max bounds, which the schema types as numbers but a malformed
    driver could author as a non-numeric scalar. Returning None lets the caller
    skip clamping rather than crash on `value < min` (TypeError) or int('abc').
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _encode_line_ending(value: str | bytes) -> bytes:
    """Encode an auth line_ending the way the driver does.

    A YAML double-quoted "\\r\\n" arrives as the real control characters,
    but a single-quoted '\\r\\n' arrives as literal backslash text — the
    driver runs it through encode_escape_sequences, so use the same
    decoder for byte-exact parity.
    """
    if isinstance(value, bytes):
        return value
    return encode_escape_sequences(value)


def _mappings_to_set(mappings: list[dict]) -> dict[str, Any]:
    """Convert canonical response mappings to the simulator's ``set`` shorthand.

    ``_build_state_responses`` understands ``match`` + ``set`` (state → a
    literal value or a ``$N`` capture), so an inline response (which normalizes
    to ``match`` + ``mappings``) is remapped here: a fixed ``value`` becomes the
    literal, a ``group`` becomes ``$<n>``.
    """
    out: dict[str, Any] = {}
    for m in mappings:
        if not isinstance(m, dict):
            continue
        state = m.get("state")
        if not state:
            continue
        out[str(state)] = m["value"] if "value" in m else f"${m.get('group', 1)}"
    return out


def _merge_inline_protocol(driver_def: dict, config: dict | None) -> tuple[dict, bool]:
    """Merge a Generic device's inline protocol (config-authored commands /
    responses / state_variables) into the definition the auto-simulator builds
    from. Returns ``(merged_def, had_inline)``.

    Responses are translated to the simulator's ``match`` + ``set`` shorthand so
    the existing auto-sim machinery (state responses, query handlers) drives
    them. Commands keep their ``send`` templates with NO line ending appended —
    the simulator matches the incoming command line after the delimiter is
    stripped.
    """
    cfg = config or {}
    norm_cmds = normalize_config_commands(cfg.get("commands"))  # no line-ending
    canonical = normalize_config_responses(cfg.get("responses"))
    norm_vars = normalize_config_state_vars(cfg.get("state_variables"))

    if not (norm_cmds or canonical or norm_vars):
        return driver_def, False

    # A {value} in a command is a parameter, as the driver declares it, so the
    # incoming line is matched with the value in it rather than the braces.
    declare_command_params(norm_cmds, set(cfg))

    merged = dict(driver_def)
    merged["commands"] = {**(driver_def.get("commands") or {}), **norm_cmds}
    sim_responses: list[dict] = []
    for r in canonical:
        if r.get("json"):
            # A JSON-field row reads a key out of a reply body, with no line
            # to match: it is kept in the form a driver file writes it.
            sim_responses.append(r)
        elif "match" in r:
            sim_responses.append(
                {"match": r["match"], "set": _mappings_to_set(r.get("mappings", []))}
            )
    merged["responses"] = list(driver_def.get("responses") or []) + sim_responses
    derived = derive_state_vars_from_responses(canonical)
    merged["state_variables"] = {
        **(driver_def.get("state_variables") or {}), **derived, **norm_vars,
    }
    if isinstance(cfg.get("delimiter"), str):
        merged["delimiter"] = cfg["delimiter"]
    return merged, True


#: A template handler's placeholders: a capture group (`{1}`) or another state
#: value (`{state.key}`). Either brings the device's own number into a
#: `set_state` value; a value with neither is the author's, as written.
_HANDLER_PLACEHOLDER = re.compile(r"\{(?:\d+|state\.[^{}]+)\}")
_HANDLER_STATE_REF = re.compile(r"\{state\.([^{}]+)\}")

#: A setting's declared type, read as the command-parameter type whose capture
#: matches the same wire form. `enum` and `float` have no param equivalent: an
#: enum's members are wire tokens of any shape, so it captures like a string,
#: and a float captures like a number.
_SETTING_PARAM_TYPE: dict[str, str] = {
    "string": "string",
    "integer": "integer",
    "number": "number",
    "float": "number",
    "boolean": "boolean",
    "enum": "string",
}


def _settings_write_template(write: dict) -> str | None:
    """The wire line a ``device_settings`` write puts on this transport.

    Asked in the runtime's own order (``set_device_setting``): an ``address``
    write is OSC and wins over everything, then ``path``/``method`` is HTTP,
    and ``send`` is the byte-stream form last. Checking ``send`` first would
    build a handler for a write the driver never sends that way.

    **No ``command_prefix``.** A command is framed with the driver's prefix and
    suffix on the way out; ``set_device_setting`` does no such thing -- it
    substitutes, escape-decodes, applies ``send_frame`` and sends. Drivers know
    it: the one prefixed driver in the catalog that declares settings spells
    its prefix into every write string by hand. Adding one here would build a
    pattern the wire never carries.

    An HTTP write is built into the exact line ``respond_http`` synthesizes --
    ``"METHOD /path|body"``, no body section when there is no body -- so one
    handler chain still serves both transports, with POST the default method
    the runtime uses.

    Returns None for a write this chain never sees: OSC, and an empty one.
    """
    if write.get("address"):
        # OSC never reaches the command chain; handle_message answers from the
        # response address mappings instead.
        return None
    path = write.get("path")
    if (isinstance(path, str) and path) or write.get("method"):
        method = str(write.get("method") or "POST").upper()
        line = f"{method} {path if isinstance(path, str) and path else '/'}"
        body = write.get("body")
        return f"{line}|{body}" if isinstance(body, str) and body else line
    send = write.get("send")
    if isinstance(send, str) and send:
        return send
    return None


class YAMLAutoSimulator(PushChannels, HTTPServerMixin, OSCDispatchMixin, TCPSimulator):
    """Auto-generated simulator from a .avcdriver definition.

    The driver's ``transport:`` field decides which server start() runs, and
    every one of them is the server the matching Python simulator base runs:
    TCPSimulator by inheritance, and the UDP / OSC / HTTP servers through the
    same mixins ``UDPSimulator``, ``OSCSimulator`` and ``HTTPSimulator`` are
    built from. What stays here is the part that is actually this class's job —
    turning a driver definition into handlers — plus the two places a
    generated simulator answers differently from a hand-written one: HTTP
    requests become synthesized command lines (respond_http), and OSC messages
    resolve through the driver's response address map (handle_message).
    """

    # Set dynamically per instance (not class-level)
    SIMULATOR_INFO: dict = {}

    def __init__(
        self,
        device_id: str,
        config: dict | None = None,
        *,
        driver_def: dict,
    ):
        # Inline protocol: a Generic device authors its commands / responses /
        # state_variables in the device config (the no-code Commands & Responses
        # editor). Merge them into the definition the simulator builds from so
        # the auto-sim machinery drives a config-authored protocol.
        driver_def, self._inline_protocol = _merge_inline_protocol(driver_def, config)

        # Build SIMULATOR_INFO from driver definition before calling super().__init__
        self.SIMULATOR_INFO = self._build_info(driver_def)
        super().__init__(device_id, config)

        # YAML drivers are text-based — always use flexible line reading
        # so the simulator accepts \r, \n, or \r\n regardless of the
        # response delimiter configured in the driver definition.
        self._line_mode = True

        # Send-side packet framing (send_frame): a driver whose commands ride
        # inside a computed-length binary header (e.g. eISCP) sends frames the
        # line reader would mis-split (the length byte can be 0x0a/0x0d). When
        # present, read length-prefixed frames instead and strip the header
        # before matching / re-wrap it on every response, so a simulated device
        # answers the framed commands exactly as real hardware does.
        self._send_frame = self._build_sim_send_frame(driver_def.get("send_frame"))
        if self._send_frame:
            self._line_mode = False

        self._driver_def = driver_def

        # UDP/OSC/HTTP transport support — start()/stop() run the datagram or
        # HTTP server from the mixins instead of the inherited TCP one.
        transport = driver_def.get("transport", "tcp")
        self._is_udp = transport == "udp"
        self._is_osc = transport == "osc"
        self._is_http = transport == "http"

        # Commands that send a datagram BESIDE the main transport (a udp:
        # block: a wake-on-LAN magic packet, a message to a presentation's
        # UDP receiver). A TCP or HTTP device that declares one also gets a
        # UDP receiver on its own port number, so the datagram shows in the
        # protocol log and its declared state effect applies, instead of
        # vanishing into a port nothing listens on.
        self._udp_side_commands: dict[str, dict] = {
            name: cmd["udp"]
            for name, cmd in (driver_def.get("commands") or {}).items()
            if isinstance(cmd, dict) and isinstance(cmd.get("udp"), dict)
        }
        self._side_receiver = bool(self._udp_side_commands) and not (
            self._is_udp or self._is_osc
        )

        # HTTPS-only devices: the driver dials https:// when its config says
        # ssl, so the simulator terminates TLS and the driver connects here
        # exactly as it connects in the field (see self_signed_tls.py). The
        # platform reads this back out of to_info_dict() and leaves such a
        # device's ssl setting alone while it is redirected.
        if self._is_http and self._resolve_ssl(driver_def, config):
            self.SIMULATOR_INFO["tls"] = True

        # Build handlers from driver definition
        self._command_handlers: list[CommandHandler] = []
        self._query_handlers: list[QueryHandler] = []
        self._state_responses: dict[str, StateResponse] = {}

        self._build_state_responses()
        self._build_command_handlers()
        # After the commands, so a command and a setting whose wire forms
        # collide resolve to the command -- the thing the driver named.
        self._build_settings_handlers()
        self._build_query_handlers()

        # Inline (Generic) devices: also match an incoming command against the
        # response patterns and apply their state changes. Many such devices use
        # the same string to set and to report a value (e.g. "PWR ON"), so the
        # string the panel sends is also the device's status string.
        self._inline_response_handlers: list[tuple[re.Pattern, dict]] = []
        if self._inline_protocol:
            self._build_inline_response_handlers()

        # Inline HTTP devices: a command marked Poll is the device's status
        # request, answered with every JSON-field response's current value.
        self._inline_json_polls: list[re.Pattern] = []
        self._inline_json_fields: list[dict] = []
        if self._inline_protocol and self._is_http:
            self._build_inline_json_polls()

        # OSC-specific: build address-based handlers from responses
        self._osc_address_handlers: list[tuple[str, list[dict]]] = []
        self._osc_script_handlers: list[OSCScriptHandler] = []
        # Push notification tracking: prevent echo during command processing
        self._handling_command = False
        self._handling_osc = False
        # OSC push: build the reverse state→address map (the client to push to
        # is the datagram server's _last_client_addr)
        self._osc_state_to_address: dict[str, tuple[str, int, str, dict[str, str] | None]] = {}
        if self._is_osc:
            self._build_osc_address_handlers()
            self._build_osc_state_reverse_map()

        # Merge explicit simulator: section if present
        sim_section = driver_def.get("simulator", {})
        if sim_section:
            self._merge_simulator_section(sim_section)

        if self._skips_blank_sign_in():
            self._error_modes.setdefault(SIGN_IN_REQUIRED_MODE, {
                "behavior": SIGN_IN_REQUIRED_MODE,
                "description": "Device asks for a sign-in (even with none set)",
                "builtin": True,
            })

        # v0.5.0 child entities: per-child state lives in the flat state dict
        # under dotted keys "<type>.<id>.<prop>" (the sim-side mirror of the
        # runtime's device.<id>.<type>.<id>.<prop> convention). The roster
        # unions three sources: declared instances: blocks resolved against
        # this instance's config, dotted keys seeded by the simulator:
        # section, and the project child_entities the manager delivers via
        # set_child_entities() after construction.
        self._child_roster: dict[str, list[str]] = {}
        self._child_state_responses: dict[tuple[str, str], ChildStateResponse] = {}
        self._child_list_answers: dict[tuple[str, str], ChildListAnswer] = {}
        self._child_query_handlers: list[ChildQueryHandler | ListQueryHandler] = []
        self._build_child_state_responses()
        self._build_child_list_answers()
        self._seed_declared_children()
        self._adopt_dotted_state_children()
        self._build_child_query_handlers()

        # push_state: whether this simulator pushes state changes to connected
        # drivers (matching real device behavior). Set in simulator: section.
        # Inline (Generic) devices push by default so driving a variable from
        # the simulator UI emits the matching string to the panel. An HTTP one
        # holds no connection to push on: its driver sees a change at its
        # next poll.
        self._push_state = sim_section.get("push_state", False) or (
            self._inline_protocol and not self._is_http
        )

        # Where the device's notifications go (push_channels.py).
        self._set_up_push()

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

    async def authenticate_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        client_id: str,
    ) -> bool:
        """Mirror the driver-side login handshake declared in `auth:`.

        For round-trip testing: send the prompts in order, read the
        credential lines, then send the success_pattern (if defined) and
        admit the client. Credentials aren't validated against a store —
        with one exception: a username or password of ``"invalid"`` is the
        designated bad credential, and makes the simulator play out the
        failure path (emit ``failure_pattern`` when declared, otherwise
        re-prompt for the username the way a real telnet daemon does) so
        drivers' auth-rejection handling can be exercised end-to-end.

        The handshake is also skipped when the *driver* would skip it:
        with ``skip_if_empty`` (default true) and a blank/absent username
        in this device's config, the driver signs in to nothing and only
        listens for a prompt it cannot answer. The ``sign_in_required``
        error mode prompts anyway, which is that unit with a password set.
        """
        auth_def = self._driver_def.get("auth")
        if not isinstance(auth_def, dict):
            return True
        if auth_def.get("type", "telnet_login") != "telnet_login":
            return True

        username_field = auth_def.get("username_field", "username")
        configured_user = str(self.config.get(username_field, "") or "")
        if (
            auth_def.get("skip_if_empty", True)
            and not configured_user
            and not self.has_error_behavior(SIGN_IN_REQUIRED_MODE)
        ):
            return True

        username_prompt = auth_def.get("username_prompt", "")
        password_prompt = auth_def.get("password_prompt", "")
        success_pattern = auth_def.get("success_pattern")
        failure_pattern = auth_def.get("failure_pattern")
        line_ending = auth_def.get("line_ending", "\r\n")
        timeout = float(auth_def.get("timeout_seconds", 10))

        # Strip regex anchors / escapes for emission — what the driver
        # sees on the wire is the literal string the device would print.
        # The driver matches it with regex so we just send a sensible
        # rendering. For typical prompts ("login: ", "Password: ") the
        # YAML carries the literal text and no regex metachars.
        prompt_user = self._render_prompt_literal(username_prompt)
        prompt_pass = self._render_prompt_literal(password_prompt)
        success_text = self._render_prompt_literal(success_pattern) if success_pattern else None
        failure_text = self._render_prompt_literal(failure_pattern) if failure_pattern else None

        ending = _encode_line_ending(line_ending)

        async def read_credential() -> str:
            # The driver terminates each credential with the declared
            # line_ending. readline() only returns on "\n", so for an
            # ending like "\r" it would hang until the timeout — read
            # up to the actual terminator instead.
            if ending.endswith(b"\n"):
                raw = await asyncio.wait_for(reader.readline(), timeout=timeout)
            else:
                try:
                    raw = await asyncio.wait_for(
                        reader.readuntil(ending), timeout=timeout
                    )
                except asyncio.IncompleteReadError as e:
                    raw = e.partial
                except asyncio.LimitOverrunError:
                    raw = b""
            return raw.decode("utf-8", errors="replace").strip("\r\n")

        async def emit(data: bytes) -> None:
            if self._client_silenced(client_id):
                return
            writer.write(data)
            await writer.drain()
            self.log_protocol("out", data, client_id)

        try:
            # Send username prompt, read the username line.
            await emit(prompt_user.encode("utf-8"))
            username = await read_credential()

            # Send password prompt, read the password line.
            await emit(prompt_pass.encode("utf-8"))
            password = await read_credential()

            if username == "invalid" or password == "invalid":
                if failure_text:
                    await emit(failure_text.encode("utf-8") + ending)
                else:
                    # No declared failure banner: re-prompt for the
                    # username like a real telnet daemon. The driver
                    # treats a missing success banner / post-password
                    # re-prompt as a rejected login.
                    await emit(ending + prompt_user.encode("utf-8"))
                logger.info(
                    "%s: client %s sent the designated bad credential — "
                    "rejecting login",
                    self.name,
                    client_id,
                )
                return False

            # Send success indicator if declared.
            if success_text:
                await emit(success_text.encode("utf-8") + ending)

            return True
        except asyncio.TimeoutError:
            logger.warning("%s: client %s auth timed out", self.name, client_id)
            return False
        except (ConnectionError, OSError):
            return False

    @staticmethod
    def _render_prompt_literal(pattern: str) -> str:
        """Best-effort regex→literal: strip common regex metacharacters.

        For YAML auth prompts we expect literal text in 95% of cases
        ("login: ", "Password: "). If a driver author writes a fancy
        regex, the simulator will still emit something readable; the
        exact wire format isn't important since we accept any input.
        """
        if pattern is None:
            return ""
        # Strip regex anchors and escapes.
        out = pattern
        out = re.sub(r"\\[brnt]", " ", out)
        out = re.sub(r"^\^", "", out)
        out = re.sub(r"\$$", "", out)
        out = out.replace("\\", "")
        return out

    # ── Transport resolution ──

    @staticmethod
    def _resolve_ssl(driver_def: dict, config: dict | None) -> bool:
        """True when this device's HTTP connection is https.

        Same layering the platform resolves the device's config with — the
        instance's own setting wins over the driver's default — so the
        simulator serves the scheme the driver is about to dial.
        """
        defaults = driver_def.get("default_config") or {}
        cfg = config or {}
        return bool(cfg.get("ssl", defaults.get("ssl", False)))

    def _instance_config(self) -> dict:
        """The driver's default_config overlaid with this instance's config:
        the fields the driver fills its templates from. A new dict each call,
        so a caller may change its copy."""
        merged = dict(self._driver_def.get("default_config") or {})
        merged.update(self.config or {})
        return merged

    async def start(self, port: int) -> None:
        """Start the server the driver's transport calls for (TCP, UDP, OSC, HTTP).

        A device with udp: side-send commands and a stream transport also
        listens for datagrams on the same port number (UDP and TCP ports are
        separate namespaces, so nothing collides).
        """
        self._open_push_channels()
        if self._is_udp or self._is_osc:
            await self.start_datagram_server(port)
            return
        # start(0) promises one number both servers answer on. Windows keeps
        # whole blocks of numbers for itself on one protocol and not the other
        # (WinError 10013), and it hands out free numbers in order, so when the
        # stream server's pick sits in a block UDP refuses, so do its next
        # several picks. Asking the same side again only walks the block. The
        # side that refused picks the next number instead: a number a protocol
        # hands out itself is never one it keeps. A number the caller named is
        # never traded.
        attempts = _SIDE_PORT_ATTEMPTS if port == 0 and self._side_receiver else 1
        stream_picks = True
        for attempt in range(attempts):
            last = attempt == attempts - 1
            if stream_picks:
                await self._start_stream_server(port)
                if not self._side_receiver:
                    return
                try:
                    # The stream server's port, read back after start(0)
                    # resolved it: side-sends go to the number the driver
                    # connects to, and binding the argument again would put
                    # them on a second ephemeral port.
                    await self.start_datagram_server(self.port)
                except OSError:
                    if last:
                        raise
                    await self._stop_stream_server()
                    stream_picks = False
                    continue
            else:
                await self.start_datagram_server(0)
                try:
                    await self._start_stream_server(self.port)
                except OSError:
                    if last:
                        raise
                    await self.stop_datagram_server()
                    stream_picks = True
                    continue
            logger.info(
                "%s also receives UDP side-sends on port %d", self.name, self.port
            )
            return

    async def _start_stream_server(self, port: int) -> None:
        if self._is_http:
            await self.start_http_server(port)
        else:
            await super().start(port)

    async def _stop_stream_server(self) -> None:
        if self._is_http:
            await self.stop_http_server()
        else:
            await super().stop()

    async def stop(self) -> None:
        """Stop whichever server start() ran."""
        self._close_push_channels()
        if self._side_receiver:
            await self.stop_datagram_server()
        if self._is_http:
            await self.stop_http_server()
        elif self._is_udp or self._is_osc:
            await self.stop_datagram_server()
        else:
            await super().stop()

    # ── HTTP transport ──

    def decode_request_path(self, path: str) -> str:
        """URL-decode the path so handlers match the un-encoded form.

        Command handlers are regexes built from the driver's own `path:`
        fields, which are written the way a human writes them ("#" not "%23").
        """
        return unquote(path)

    async def respond_http(
        self,
        request: Any,
        method: str,
        path: str,
        headers: dict[str, str],
        body: str,
    ) -> Any:
        """Answer an HTTP request through the same handler chain TCP uses.

        The request is synthesized into a command line:
            "METHOD /path?query|<body>"   (body section omitted when empty)
        so one set of generated handlers serves every transport. Status is 200
        when a handler matched and 404 when none did; the body is whatever the
        handler returned via respond().
        """
        from aiohttp import web

        command_line = f"{method} {path}|{body}" if body else f"{method} {path}"

        try:
            response_bytes = self.handle_command(command_line.encode("utf-8"))
        except Exception:
            logger.exception("%s: error in handle_command", self.name)
            return web.Response(status=500, text="Simulator error")

        if response_bytes is None:
            self.log_protocol("out", "404")
            return web.Response(status=404, text="")

        response_text = response_bytes.decode("utf-8", errors="replace")
        self.log_protocol("out", f"200 | {response_text[:200]}")
        return web.Response(status=200, text=response_text)

    # ── UDP / OSC transport ──

    #: What every Wake-on-LAN magic packet starts with.
    _MAGIC_PACKET_PREFIX = b"\xff" * 6

    async def dispatch_datagram(self, data: bytes, addr: tuple[str, int]) -> None:
        """Route a datagram to the OSC or the plain command pipeline.

        Which one is a per-instance fact here (the driver's transport), where
        for a Python simulator it is the class it subclassed. A datagram that
        arrives beside a stream transport is a side-send: it is applied and
        never answered, because the sender's socket closed with the send and a
        real device answers on its control link, not the side channel.
        """
        if self._side_receiver:
            self._apply_side_datagram(data)
            return
        if not self._is_osc:
            await self.dispatch_command_datagram(data, addr)
            return
        # Suppress the push echo for the whole OSC exchange: state written
        # while answering a message is a reply, not an unsolicited notice.
        self._handling_osc = True
        try:
            await self.dispatch_osc_datagram(data, addr)
        finally:
            self._handling_osc = False

    def _apply_side_datagram(self, data: bytes) -> None:
        """Apply one udp: side-send to state. Already in the protocol log.

        A magic packet applies the ``sets`` of every magic_packet command (a
        wake declares ``sets: {power: on}`` and the simulated display comes
        on, like the real one); a payload runs through the command pipeline
        the way a line on the stream would, its reply discarded.
        """
        if data.startswith(self._MAGIC_PACKET_PREFIX) and len(data) >= 102:
            for cmd_name, udp_def in self._udp_side_commands.items():
                if not udp_def.get("magic_packet"):
                    continue
                cmd_def = (self._driver_def.get("commands") or {}).get(cmd_name) or {}
                sets = cmd_def.get("sets")
                if isinstance(sets, dict):
                    for var_name, value in sets.items():
                        if isinstance(value, str) and re.fullmatch(r"\{[^{}]+\}", value):
                            continue  # a magic packet carries no params
                        self.set_state(var_name, value)
            return
        try:
            self.handle_command(data)
        except Exception:
            logger.exception("%s: error applying UDP side-send", self.name)

    def handle_message(
        self, address: str, args: list[tuple[str, Any]]
    ) -> list[tuple[str, list[tuple[str, Any]]]] | None:
        """Handle a decoded OSC message using the response address mappings.

        The OSC override point a Python simulator implements by hand; here it
        is answered from the driver's own response definitions.

        Two behaviors:
        - Message WITH args: update state from arg values, echo back
        - Message WITHOUT args: respond with current state values (query)

        Also checks script handlers from the simulator: section.
        """
        import fnmatch

        # Special system addresses (handle before address handlers so they
        # aren't intercepted by response patterns)
        if address == "/xremote":
            # Real X32 starts pushing state changes after /xremote. Send back
            # a heartbeat so the connection watchdog sees activity.
            return [("/xremote", [])]
        if address == "/info":
            model = self._state.get("console_model", "Simulator")
            firmware = self._state.get("firmware_version", "1.0.0")
            return [("/info", [
                ("s", "V2.07"), ("s", "osc-server"),
                ("s", model), ("s", firmware),
            ])]
        if address == "/status":
            return [("/status", [
                ("s", "active"), ("s", "127.0.0.1"), ("s", "osc-server"),
            ])]
        if address.startswith("/-action/") or address.startswith("/-show/"):
            return [(address, args)] if args else [(address, [])]

        # Try script handlers first (from simulator: command_handlers with address:)
        for handler in self._osc_script_handlers:
            if fnmatch.fnmatch(address, handler.address_pattern):
                return self._execute_osc_script_handler(handler, address, args)

        # Match against response address patterns
        for addr_pattern, mappings in self._osc_address_handlers:
            if not fnmatch.fnmatch(address, addr_pattern):
                continue

            if args:
                # SET: update state from incoming args
                for mapping in mappings:
                    arg_idx = mapping.get("arg", 0)
                    state_key = mapping.get("state")
                    if state_key and arg_idx < len(args):
                        _, value = args[arg_idx]
                        value_map = mapping.get("map")
                        mapped = value_map.get(str(value)) if value_map else None
                        if mapped is not None:
                            value = mapped
                        else:
                            value = self._from_wire(state_key, value)
                        self.set_state(state_key, self._coerce_value(state_key, value))
                # Echo the message back (standard OSC feedback pattern)
                return [(address, args)]
            else:
                # QUERY: respond with current state values
                resp_args: list[tuple[str, Any]] = []
                for mapping in mappings:
                    state_key = mapping.get("state")
                    value_type = mapping.get("type", "float")
                    if state_key:
                        value = self._state.get(state_key, 0)
                        tag = _type_to_osc_tag(value_type)
                        # Apply reverse value map (e.g., mute false → OSC 1)
                        reverse_info = self._osc_state_to_address.get(state_key)
                        raw = None
                        if reverse_info:
                            _, _, _, reverse_map = reverse_info
                            if reverse_map:
                                raw = reverse_map.get(str(value).lower())
                                if raw is not None:
                                    value = int(raw) if tag == "i" else float(raw) if tag == "f" else raw
                        if raw is None:
                            value = self._to_wire(state_key, value)
                        resp_args.append((tag, value))
                if resp_args:
                    return [(address, resp_args)]
                return [(address, [])]

        logger.debug("%s: unmatched OSC address: %s", self.device_id, address)
        return None

    def _execute_osc_script_handler(
        self, handler: OSCScriptHandler, address: str, args: list[tuple[str, Any]]
    ) -> list[tuple[str, list[tuple[str, Any]]]] | None:
        """Execute a script handler for OSC messages."""
        response_data: list[tuple[str, list[tuple[str, Any]]]] = []

        def respond(resp_address: str, resp_args: Any = None) -> None:
            response_data.append((resp_address, _normalize_osc_args(resp_args)))

        state_proxy = _StateProxy(self._state, self.set_state)

        namespace = {
            "address": address,
            "args": args,
            "state": state_proxy,
            "config": self.config,
            "respond": respond,
            **_SAFE_HANDLER_BUILTINS,
        }

        try:
            exec_ns = {"__builtins__": {}, **namespace}
            exec(handler.code, exec_ns, exec_ns)  # noqa: S102
        except Exception:
            logger.exception(
                "%s: OSC script handler error for %s",
                self.device_id, handler.address_pattern,
            )
            return None

        return response_data if response_data else None

    # ── Protocol handling ──

    # Precompute send_frame header bytes for build/strip — the same shared
    # builder the driver side uses, so the simulator frames exactly what the
    # driver's frame_parser expects.
    _build_sim_send_frame = staticmethod(build_send_frame)

    def _wrap_send_frame(self, data: bytes) -> bytes:
        """Wrap a response body in the send_frame packet header (computed length)."""
        return apply_send_frame(self._send_frame, data)

    async def _read_send_frame_messages(
        self, reader: asyncio.StreamReader, buffer: bytearray | None,
    ) -> list[bytes] | None:
        """Read length-prefixed send_frame frames; return the stripped bodies.

        The send_frame binary header can carry 0x0a/0x0d bytes in its length
        field, which the line reader would treat as terminators and mis-split —
        so length-framed drivers read here instead. Skips the fixed header,
        reads the computed-length body, and hands the bare payload (e.g. the
        ISCP "!1PWR01\\r") to the normal dispatch, exactly as the driver's
        receive-side LengthPrefixFrameParser does.
        """
        if buffer is None:
            buffer = bytearray()
        try:
            raw = await asyncio.wait_for(reader.read(4096), timeout=30.0)
        except asyncio.TimeoutError:
            return []
        if not raw:
            return None
        buffer.extend(raw)
        return split_send_frames(self._send_frame, buffer)

    def _first_message_len(self, buf: bytearray) -> int | None:
        if not self._send_frame:
            return super()._first_message_len(buf)
        sf = self._send_frame
        header_len = len(sf["header"])
        total_header = header_len + sf["length_size"] + len(sf["after_length"])
        if len(buf) < total_header:
            return None
        body = int.from_bytes(
            buf[header_len: header_len + sf["length_size"]], sf["length_endian"]
        )
        total = total_header + body
        return total if len(buf) >= total else None

    @property
    def capacity_kind(self) -> str | None:
        if self._is_http:
            return None
        return "datagram" if self._is_udp or self._is_osc else "stream"

    async def _read_messages(
        self, reader: asyncio.StreamReader, buffer: bytearray | None = None,
    ) -> list[bytes] | None:
        if self._send_frame:
            return await self._read_send_frame_messages(reader, buffer)
        return await super()._read_messages(reader, buffer)

    def handle_command(self, data: bytes) -> bytes | None:
        self._handling_command = True
        try:
            resp = self._dispatch_command(data)
            # Re-wrap the reply in the send_frame packet header so the driver's
            # receive-side length-prefix parser can read it (build/strip parity).
            if resp is not None and self._send_frame:
                resp = self._wrap_send_frame(resp)
            return resp
        finally:
            self._handling_command = False

    def _dispatch_command(self, data: bytes) -> bytes | None:
        text = data.decode("utf-8", errors="replace").strip()
        if not text:
            return None

        # Dial-back subscriber bookkeeping (push: {type: tcp_listener}) —
        # observe-only; the command still dispatches normally below so the
        # driver YAML can shape the response with an explicit handler.
        was_registration = self._watch_push_registration(text)

        # Try explicit handlers first (from simulator: command_handlers)
        for handler in self._explicit_handlers:
            m = handler.pattern.match(text)
            if m:
                return self._with_remainder(text, m, self._execute_explicit_handler(handler, m))

        # Try script handlers (match: + handler: with inline Python)
        for handler in self._script_handlers:
            m = handler.pattern.match(text)
            if m:
                return self._with_remainder(text, m, self._execute_script_handler(handler, m))

        for pattern in self._inline_json_polls:
            if pattern.match(text):
                return self._inline_json_reply()

        # Inline (Generic) devices: match the incoming string against the
        # response patterns and apply their state changes + echo a confirmation.
        for pattern, set_dict in self._inline_response_handlers:
            m = pattern.match(text)
            if m:
                return self._with_remainder(text, m, self._apply_inline_response(text, m, set_dict))

        # Child-addressed poll queries answer from the addressed child's own
        # state. They dispatch ahead of the auto command handlers on purpose:
        # a protocol whose bare per-output view collides with a single-output
        # command form (SIS "1Z") resolves by roster — children modeled means
        # the matrix dialect wins, exactly like the real hardware.
        for handler in self._child_query_handlers:
            m = handler.pattern.match(text)
            if m:
                return self._with_remainder(
                    text, m, self._execute_child_query_handler(handler)
                )

        # Try auto-generated command handlers
        for handler in self._command_handlers:
            m = handler.pattern.match(text)
            if m:
                return self._with_remainder(text, m, self._execute_command_handler(handler, m))

        # Try query handlers
        for handler in self._query_handlers:
            m = handler.pattern.match(text)
            if m:
                return self._with_remainder(text, m, self._execute_query_handler(handler))

        # A registration command with no handler of its own still succeeds —
        # the empty body means "204-style ack" (real dial-back devices answer
        # registrations with No Content), so drivers don't need boilerplate
        # simulator handlers for the subscribe/unsubscribe pair.
        if was_registration:
            return b""

        # Nothing matched the whole line. Unterminated protocols are
        # self-delimiting, and with no terminator on the wire back-to-back
        # writes can coalesce into one line ("N?*R4") whose first command's
        # end-anchored pattern then never matches. If the entire line
        # decomposes into known commands, dispatch each piece; plain garbage
        # does not decompose and stays unrecognized.
        segments = self._plan_segments(text) if len(text) <= 256 else None
        if segments and len(segments) > 1:
            replies = [self._dispatch_command(s.encode()) for s in segments]
            parts = [r for r in replies if r]
            return b"".join(parts) if parts else b""

        logger.debug("%s: unrecognized command: %r", self.device_id, text)
        return None

    def _with_remainder(self, text: str, m: re.Match, head: bytes | None) -> bytes | None:
        """Dispatch whatever a prefix-matching handler left unconsumed.

        Handlers whose patterns aren't end-anchored (an explicit `match: 'I'`)
        legitimately claim just the front of a coalesced line; the rest of the
        line is the next command, not junk to swallow. Recursing keeps
        first-match-wins order per piece. Trailing text that matches nothing
        dispatches to None and the head's reply stands alone, exactly as
        before.
        """
        end = m.end()
        if end <= 0 or end >= len(text):
            return head
        rest = text[end:].strip()
        if not rest:
            return head
        tail = self._dispatch_command(rest.encode())
        if head and tail:
            return head + tail
        return tail if tail else head

    def _plan_segments(self, text: str, _depth: int = 0) -> list[str] | None:
        """Decompose a line into a sequence of fully-matchable commands.

        Longest-prefix-first with backtracking; every byte must land in some
        segment (full decomposition or None). fullmatch keeps a segment from
        claiming trailing characters a shorter split needs.
        """
        if not text:
            return []
        if _depth > 16:
            return None
        for end in range(len(text), 0, -1):
            prefix = text[:end]
            if self._any_handler_fullmatch(prefix):
                rest = self._plan_segments(text[end:], _depth + 1)
                if rest is not None:
                    return [prefix, *rest]
        return None

    def _any_handler_fullmatch(self, text: str) -> bool:
        for handler in self._explicit_handlers:
            if handler.pattern.fullmatch(text):
                return True
        for handler in self._script_handlers:
            if handler.pattern.fullmatch(text):
                return True
        for pattern, _set_dict in self._inline_response_handlers:
            if pattern.fullmatch(text):
                return True
        for handler in self._child_query_handlers:
            if handler.pattern.fullmatch(text):
                return True
        for handler in self._command_handlers:
            if handler.pattern.fullmatch(text):
                return True
        return any(h.pattern.fullmatch(text) for h in self._query_handlers)

    def _execute_command_handler(self, handler: CommandHandler, m: re.Match) -> bytes | None:
        """Execute a command handler: update state and generate response."""
        delimiter = self._get_delimiter()

        # Drive any state machines with this command name (the documented
        # trigger contract). A machine that rejects the command in its current
        # state (e.g. during cooldown) suppresses the response entirely.
        if self._state_machines and self._fire_state_machine_triggers(handler.name):
            return None

        # A child-addressed command resolves the captured id to a rostered
        # child; an id the roster doesn't know goes unanswered (the device
        # analog of addressing a port that isn't there).
        child_id: str | None = None
        if handler.child_type:
            try:
                raw_id = m.group(handler.child_id_group)
            except (IndexError, re.error):
                raw_id = None
            if raw_id is None:
                return None
            raw_id = str(raw_id).strip()
            if handler.child_wire_map is not None:
                raw_id = handler.child_wire_map.get(raw_id, raw_id)
            child_id = str(int(raw_id)) if raw_id.isdigit() else raw_id
            if child_id not in self._child_roster.get(handler.child_type, []):
                logger.debug(
                    "%s: %s addressed unknown %s %r",
                    self.device_id, handler.name, handler.child_type, child_id,
                )
                return None

        # Apply state changes
        for state_key, source in handler.state_changes.items():
            target_key = (
                f"{handler.child_type}.{child_id}.{state_key}"
                if child_id is not None and state_key in handler.child_state_keys
                else state_key
            )
            if isinstance(source, tuple) and source[0] == "literal":
                # Declared literal from a command's sets: block — wrapped so
                # an integer literal can't read as a capture-group index.
                value: Any = self._coerce_value(target_key, source[1])
            elif isinstance(source, int) and not isinstance(source, bool):
                # Capture group index
                value = m.group(source)
                found, setting_value = value_for_map_word(
                    value, handler.group_value_maps.get(source)
                )
                if found:
                    # A device setting's own word (ON) stands for a setting
                    # value (true), which no base or conversion applies to.
                    # A word the map does not name is read as it is, below.
                    value = setting_value
                else:
                    base = handler.group_bases.get(source)
                    if base and self._numeric_state_var(target_key):
                        # The wire value was formatted with a non-decimal spec —
                        # decode it back before coercion, or an integer var would
                        # end up holding the raw hex string. String-typed vars
                        # keep the wire form: hex-code protocols (eISCP) declare
                        # the code itself as the state value.
                        try:
                            value = int(value, base)
                        except ValueError:
                            pass
                    conversion = handler.group_conversions.get(source)
                    if conversion:
                        var_type = self._var_def_for_key(target_key).get("type", "string")
                        handled, real = reading_from_wire(value, conversion, var_type)
                        if handled:
                            value = real
                value = self._coerce_value(target_key, value)
            else:
                # Literal value (heuristic booleans arrive bare)
                value = source
            self.set_state(target_key, value)

        # Generate response for the primary state variable
        if handler.response_var and handler.response_is_child and child_id is not None:
            resp = self._child_state_responses.get(
                (handler.child_type, handler.response_var)
            )
            if resp is not None:
                value = self._state.get(
                    f"{handler.child_type}.{child_id}.{handler.response_var}"
                )
                response_text = resp.format(child_id, value)
                if response_text is not None:
                    return (response_text + delimiter).encode()
            return None
        if handler.response_var and handler.response_var in self._state_responses:
            resp = self._state_responses[handler.response_var]
            response_text = resp.format(self._state.get(handler.response_var))
            return (response_text + delimiter).encode()

        return None

    def _fire_state_machine_triggers(self, trigger_name: str) -> bool:
        """Drive every state machine with a command-name trigger.

        Returns True if any machine rejects the command in its current state,
        in which case the caller suppresses the response and no machine
        transitions (reject is all-or-nothing).
        """
        if any(
            sm.is_rejected(trigger_name) for sm in self._state_machines.values()
        ):
            return True
        for sm in self._state_machines.values():
            sm.trigger(trigger_name)
        return False

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
            template = str(val)
            resolved: Any = self._resolve_template(template, m)
            if _HANDLER_PLACEHOLDER.search(template):
                resolved = self._from_wire(key, resolved)
            self.set_state(key, self._coerce_value(key, resolved))

        # Generate response
        if handler.respond:
            response_text = self._resolve_template(handler.respond, m)
            return response_text.encode()

        return None

    def _execute_script_handler(self, handler: ScriptHandler, m: re.Match) -> bytes | None:
        """Execute a script handler (match: + handler: with inline Python).

        Scripts get: match (regex match), state (proxy dict that notifies on
        writes), config (device config), respond(text) (send response),
        notify(text) (send an unsolicited message on the device's push
        channel — see _deliver_notification) and reply(key) (the device's
        own report of a state key — see _script_reply).
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
            # An unsolicited message on the device's push channel — the
            # frame a real device emits AFTER acknowledging a write. respond()
            # answers the request; notify() is everything else the device
            # says because of it.
            "notify": self._deliver_notification,
            # The text the device sends to report a state key, in its own
            # numbers: what a "report everything" handler strings together.
            "reply": self._script_reply,
            # http_listener push: a handler matching the device's
            # registration command records where to deliver notifications
            # (e.g. the ServerUrl a codec's feedback registration carries).
            "register_callback": self.register_callback,
            "unregister_callback": self.unregister_callback,
            **_SAFE_HANDLER_BUILTINS,
        }

        try:
            # Use a single dict for both globals and locals so nested function
            # definitions (def get_int, etc.) can see variables set by earlier
            # lines in the handler (e.g., 'text'). Python closures in exec
            # only see globals, not exec-locals.
            exec_ns = {"__builtins__": {}, **namespace}
            exec(handler.code, exec_ns, exec_ns)  # noqa: S102
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
            # JSON-body responses populate state from a parsed object, not a
            # regex template, so there's no literal to reconstruct here.
            # Auto-generating JSON query replies is a separate follow-on;
            # drivers that need JSON simulation ship an explicit simulator:
            # section. Skip so json rules don't create empty response entries.
            if resp_def.get("json"):
                continue
            match_pattern = resp_def.get("match", "")
            set_dict = resp_def.get("set", {})

            # Canonical mappings: form (what the Driver Builder persists) —
            # derive the set shorthand so those rules feed the same loop
            # below. OSC address rules carry no match pattern (they're built
            # into address handlers instead), so they're skipped here.
            map_reversals: list[dict] = []
            mappings = resp_def.get("mappings")
            if (
                not set_dict
                and match_pattern
                and not resp_def.get("address")
                and isinstance(mappings, list)
            ):
                set_dict = _mappings_to_set(mappings)
                map_reversals = [
                    m for m in mappings
                    if isinstance(m, dict)
                    and m.get("state")
                    and "value" not in m
                    and isinstance(m.get("map"), dict)
                ]

            for state_key, set_value in set_dict.items():
                # The key every value map lookup uses, or a YAML bare `true`
                # (Python True) stores a "True" key that a boolean state value
                # can never hit.
                set_value_str = map_key(set_value)

                if state_key not in self._state_responses:
                    self._state_responses[state_key] = StateResponse(
                        state_key, conversion_of(self._var_def_for_key(state_key))
                    )

                sr = self._state_responses[state_key]

                # Check if the response has capture groups (template-based)
                # vs fixed text (value-mapped)
                has_groups = bool(re.search(r"\(.*\)", match_pattern))

                if has_groups and set_value_str.startswith("$"):
                    # Template-based: In(\d+) All with set: { input: "$1" }
                    # → template: In{value} All. The $N picks which capture
                    # group becomes {value}, so a multi-group pattern puts
                    # the value where this state key actually reads it.
                    try:
                        group = int(set_value_str[1:])
                    except ValueError:
                        group = 0
                    template = (
                        emit_template(match_pattern, group) if group > 0 else None
                    )
                    if template:
                        sr.add_template(template, rule_pattern(match_pattern, group))
                else:
                    # Value-mapped: Amt1 with set: { mute: "true" }
                    # → value "true" maps to response text "Amt1"
                    # Reconstruct the literal response text from the regex
                    literal = emit_literal(match_pattern)
                    if literal:
                        sr.value_map[set_value_str] = literal

            # A mapping's map: stores the friendly value in state (raw wire
            # token → friendly), so emitting the template with the state value
            # would put the friendly text on the wire. Reverse each pair
            # instead: friendly value → the wire text with the raw token at
            # the mapping's group. First writer wins, like the template guard.
            for mapping in map_reversals:
                try:
                    group = int(mapping.get("group", 1))
                except (TypeError, ValueError):
                    continue
                template = (
                    emit_template(match_pattern, group) if group > 0 else None
                )
                if not template:
                    continue
                sr = self._state_responses.get(str(mapping["state"]))
                if sr is None:
                    continue
                for raw, friendly in mapping["map"].items():
                    sr.value_map.setdefault(
                        map_key(friendly), fill_template(template, {"value": raw})
                    )

    def _build_command_handlers(self) -> None:
        """A handler for each command in the commands: section: the pattern
        its send template matches, then what it does, as declared (``sets:``
        / ``query_for:``) or, without a declaration, read from its parameter
        names and its own name."""
        state_vars = set(self._driver_def.get("state_variables", {}).keys())
        # Match the framed form the real driver puts on the wire: a driver-level
        # command_prefix is prepended to every command (the trailing
        # command_suffix/terminator is whitespace, already stripped from the
        # incoming line in _dispatch_command). A raw: true command is unframed.
        prefix = self._driver_def.get("command_prefix") or ""

        for cmd_name, cmd_def in self._driver_def.get("commands", {}).items():
            wire = self._command_wire(cmd_name, cmd_def, prefix)
            if wire is None:
                continue
            send_template, params, pattern = wire
            read = _CommandRead(send_template, params, self._child_address(params))
            read.read(cmd_name, cmd_def, state_vars)
            handler = read.handler(cmd_name, pattern)
            self._command_handlers.append(handler)
            if _infers_nothing(handler):
                logger.debug(
                    "Auto-sim %s: could not infer behavior for command '%s'",
                    self.driver_id, cmd_name,
                )

    @staticmethod
    def _command_wire(
        cmd_name: str, cmd_def: dict, prefix: str
    ) -> tuple[str, Any, re.Pattern] | None:
        """A command's send template as the wire carries it, its params, and
        the pattern that matches it; None for a command this chain cannot
        match (no send, a raw passthrough, a pattern that will not compile)."""
        send_template = _framed_send(cmd_def, prefix)
        if not send_template:
            return None
        params = cmd_def.get("params", {})

        # A raw passthrough ("{command}\r") would match every line, and
        # setting nothing it would answer every line meant for a handler
        # after it with silence: the device settings' write handlers, and
        # any command declared below it.
        if is_passthrough_send(send_template, params):
            return None

        # Convert send template to regex — the shared inversion of the
        # same placeholder shapes the runtime substitutes on send.
        pattern_str = send_regex(send_template, params)
        try:
            pattern = re.compile(f"^{pattern_str}$")
        except re.error:
            logger.warning("Invalid regex from command '%s': %s", cmd_name, pattern_str)
            return None
        return send_template, params, pattern

    def _child_address(
        self, params: Any
    ) -> _ChildAddress:
        """Which child a command addresses: its one child_id param's name and
        child type, the param map's wire -> local id table, and the type's
        variables. A command carrying exactly one child_id param is addressed
        to that child: its state effect routes to the child's own dotted
        state, with the flat variables as fallback for anything the child
        type doesn't declare. Two child_id params (a router's input AND
        output) are ambiguous — no child routing."""
        child_id_params = [
            (pname, pdef) for pname, pdef in params.items()
            if isinstance(pdef, dict)
            and pdef.get("type") == "child_id"
            and pdef.get("child_type") in self._child_types()
        ]
        if len(child_id_params) != 1:
            return _ChildAddress(None, None, None, {})
        param_name, cpdef = child_id_params[0]
        ctype = cpdef.get("child_type")
        child_vars = self._child_vars(ctype)
        raw_map = cpdef.get("map")
        # Param map: local id -> wire token; the sim sees the wire token and
        # needs the local id back.
        wire_map = (
            {str(v): str(k) for k, v in raw_map.items()}
            if isinstance(raw_map, dict) and raw_map
            else None
        )
        return _ChildAddress(param_name, ctype, wire_map, child_vars)

    def _settings_wire_line(self, template: str) -> str | None:
        """The one line of a settings write that actually carries the value.

        Two things stand between the authored template and the line the
        simulator sees, and both are visible in the catalog today.

        A write may be more than one command: a set followed by a read-back
        query, joined by the terminator, sent as one blob and arriving as
        separate lines. Only the segment holding ``{value}`` is the write --
        the query is a query, answered by the handlers that answer queries --
        so a pattern built from the whole string could never match either
        line.

        And a write may carry ``{config_field}`` placeholders (a monitor id, a
        unit address). The driver substitutes those before sending, so the
        simulator has to as well or the pattern would look for the literal
        braces. Same substitution ``_query_wire_line`` does for a raw poll
        query, from the same two sources; ``{value}`` is not a config key and
        is left alone for ``send_regex`` to turn into the capture.
        """
        segments = [
            s for s in decode_delimiter(template).replace("\r", "\n").split("\n")
            if s.strip()
        ]
        wire = next(
            # `value` is the one name the runtime always substitutes with the
            # value being written, with or without a format spec.
            (s for s in segments if "value" in placeholder_names(s)), None
        )
        if wire is None:
            return None
        if "{" in wire:
            fields = self._instance_config()
            fields.pop("value", None)
            wire = safe_substitute(wire, fields)
        return wire.strip() or None

    def _build_settings_handlers(self) -> None:
        """Answer a ``device_settings`` write the way a command is answered.

        A setting's ``write`` carries the same templates a command's ``send``
        does and its ``state_key`` names where the value lands, so the pair is
        everything a handler needs -- and without one, a setting write reached
        the end of the dispatch chain, logged *unrecognized command* and was
        dropped. Nothing in the catalog shows it, because every driver that
        declares settings also hand-writes a ``simulator:`` section whose
        handlers happen to match its own write strings; a NEW driver written
        without one got settings that did nothing in simulation.

        **Reporting the value back is the point, not a nicety.** A setting
        write now waits for the device to say the value took, so a handler
        that changed state silently would leave the setting pending forever
        and re-sent on every connect. ``response_var`` is the state key, and
        the sentence comes from the same ``responses:`` rule the real driver
        parses the device's report with -- which every such driver has, or it
        could never learn the value either.

        **Two of the three write forms are built here.** ``send`` is the wire
        string. An HTTP write is built from ``method``/``path``/``body`` into
        the line ``respond_http`` synthesizes, so one handler chain still
        serves both transports. An OSC write (``address``/``args``) is NOT
        built: OSC never reaches this chain -- ``handle_message`` answers from
        the response address mappings instead -- and no driver in the catalog
        writes a setting over OSC. A driver that does needs a rule there, and
        gets today's behaviour until then rather than a handler that silently
        never matches.
        """
        settings = self._driver_def.get("device_settings") or {}
        if not isinstance(settings, dict):
            return
        built = 0

        for name, entry in settings.items():
            if not isinstance(entry, dict):
                continue
            write = entry.get("write")
            if not isinstance(write, dict):
                continue
            template = _settings_write_template(write)
            if not template:
                continue
            template = self._settings_wire_line(template)
            if not template:
                # Every segment was a query, or config substitution left
                # nothing addressable. A handler with no {value} capture would
                # set the state key to nothing on every match.
                continue

            # One parameter, always called `value` -- that is the placeholder
            # the runtime substitutes -- typed from the setting so the capture
            # matches what went on the wire. `enum` and `float` are settings
            # types with no param equivalent: an enum's members are wire
            # tokens of any shape, and a float captures like a number. A
            # setting with a `map` writes the device's own words (ON for
            # true), which the type's capture would not match, so it captures
            # like a string and the word is mapped back below.
            value_map = entry.get("map")
            if not (isinstance(value_map, dict) and value_map):
                value_map = None
            param_type = (
                "string" if value_map
                else _SETTING_PARAM_TYPE.get(str(entry.get("type", "")), "string")
            )
            params = {"value": {"type": param_type}}

            pattern_str = send_regex(template, params)
            try:
                pattern = re.compile(f"^{pattern_str}$")
            except re.error:
                logger.warning(
                    "Invalid regex from setting '%s': %s", name, pattern_str
                )
                continue

            state_key = str(entry.get("state_key") or name)
            group_bases: dict[int, int] = {}
            base = spec_int_base(send_param_specs(template, params).get("value", ""))
            if base:
                group_bases[1] = base
            conversion = conversion_of(self._var_def_for_key(state_key))

            self._command_handlers.append(CommandHandler(
                name=f"setting:{name}",
                pattern=pattern,
                state_changes={state_key: 1},
                response_var=state_key,
                group_bases=group_bases,
                group_conversions={1: conversion} if conversion else None,
                group_value_maps={1: value_map} if value_map else None,
            ))
            built += 1

        if built:
            logger.debug(
                "Auto-sim %s: %d device_settings write handlers",
                self.driver_id, built,
            )

    def _build_query_handlers(self) -> None:
        """Build query handlers from polling.queries, the response rules'
        then_query and the commands' read_back (+ declared on_connect).

        A mapping entry's declared ``query_for`` names the state variable
        the reply reports and wins outright. Otherwise the query text is
        matched to a command with the same send template and the variable
        comes from that command's declared ``query_for`` or, failing that,
        its name (the shared inference fallback).
        """
        polling = self._driver_def.get("polling", {})
        queries = list(polling.get("queries", []))
        queries += follow_up_queries(self._driver_def)
        queries += read_back_queries(self._driver_def)
        state_vars = set(self._driver_def.get("state_variables", {}).keys())

        # Also check commands for query-like commands without params
        commands = self._driver_def.get("commands", {})

        for query in queries:
            declared = None
            if isinstance(query, dict):
                if "each_child" in query:
                    # Per-child queries build per-child handlers instead
                    # (_build_child_query_handlers) — a flat handler from a
                    # {child_id} template could never match a real wire.
                    continue
                query_text = query.get("send", "")
                qf = query.get("query_for")
                if isinstance(qf, str) and "." in qf:
                    # A list read (<child type>.<variable>) is answered
                    # from every child: _build_list_query_handlers.
                    continue
                if isinstance(qf, str) and qf:
                    declared = qf
            else:
                query_text = str(query)

            if not query_text:
                continue

            response_var = declared
            if not response_var:
                # Look for a command with this exact send template
                for cmd_name, cmd_def in commands.items():
                    if cmd_def.get("send") == query_text and not cmd_def.get("params"):
                        cmd_qf = cmd_def.get("query_for")
                        target = (
                            cmd_qf
                            if isinstance(cmd_qf, str) and cmd_qf
                            else infer_state_var(cmd_name, state_vars)
                        )
                        if target:
                            response_var = target
                            break

            if response_var:
                self._query_handlers.append(QueryHandler(
                    pattern=re.compile(f"^{re.escape(self._query_wire_line(query_text))}$"),
                    response_var=response_var,
                ))

        # on_connect entries participate only when they declare query_for —
        # an initial-status query the device should answer in simulation too.
        # No name inference here: on_connect never fed the handler table, so
        # only declared semantics add to it.
        for entry in self._driver_def.get("on_connect", []) or []:
            if not isinstance(entry, dict) or "each_child" in entry:
                continue
            qf = entry.get("query_for")
            send = entry.get("send", "")
            if (
                isinstance(qf, str) and qf and "." not in qf
                and isinstance(send, str) and send
                and "address" not in entry
            ):
                self._query_handlers.append(QueryHandler(
                    pattern=re.compile(f"^{re.escape(self._query_wire_line(send))}$"),
                    response_var=qf,
                ))

    def _query_wire_line(self, query_text: str) -> str:
        """The stripped line a raw poll query actually arrives as.

        The driver sends raw queries with ``{config_field}`` placeholders
        substituted and backslash escape sequences encoded to real bytes;
        inbound dispatch then strips terminators and surrounding whitespace
        (``_dispatch_command``). A query handler built from the as-authored
        text (``"Status_Audio 1 \\r"``) could therefore never match the line
        it arrives as (``"Status_Audio 1"``) — normalize the same way here.
        """
        if "{" in query_text:
            query_text = safe_substitute(query_text, self._instance_config())
        return decode_delimiter(query_text).strip()

    # ── Child-entity modeling ──

    def _child_types(self) -> dict[str, dict]:
        types = self._driver_def.get("child_entity_types")
        return types if isinstance(types, dict) else {}

    def _child_vars(self, ctype: str) -> dict[str, dict]:
        tdef = self._child_types().get(ctype)
        cvars = tdef.get("state_variables") if isinstance(tdef, dict) else None
        return cvars if isinstance(cvars, dict) else {}

    @staticmethod
    def _default_child_value(var_def: dict) -> Any:
        """Default for a child state variable — the same call _build_info
        makes for the flat initial_state, so the two cannot drift."""
        return state_var_default(var_def)

    def _register_sim_children(self, ctype: str, ids: list) -> None:
        """Add children to the sim roster and seed their state defaults.

        Ids are stored as unpadded strings — the wire form a regex capture
        or an expanded {child_id} query carries. Existing state (a dotted
        simulator: initial_state seed) is never overwritten.
        """
        cvars = self._child_vars(ctype)
        roster = self._child_roster.setdefault(ctype, [])
        for local_id in ids:
            cid = str(local_id)
            if cid not in roster:
                roster.append(cid)
            for var, var_def in cvars.items():
                key = f"{ctype}.{cid}.{var}"
                if key not in self._state:
                    self._state[key] = self._default_child_value(var_def)

    def _seed_declared_children(self) -> None:
        """Resolve each child type's ``instances:`` roster against this
        instance's config — the sim-side twin of the driver's
        _register_declared_children (count_from_state is skipped: the sim IS
        the device, there is no reported count to read yet)."""
        merged = self._instance_config()
        for ctype, tdef in self._child_types().items():
            if not isinstance(tdef, dict):
                continue
            inst = tdef.get("instances")
            if not isinstance(inst, dict):
                continue
            ids: list[Any] = []
            if isinstance(inst.get("ids"), list):
                ids = [i for i in inst["ids"] if isinstance(i, (str, int)) and not isinstance(i, bool)]
            elif "count" in inst or "count_from" in inst or "count_from_state" in inst:
                raw = inst.get("count")
                if raw is None and inst.get("count_from"):
                    raw = merged.get(inst.get("count_from"), "")
                try:
                    n = int(str(raw).strip()) if str(raw).strip() != "" else 0
                except (TypeError, ValueError):
                    n = 0
                ids = list(range(1, n + 1))
            elif isinstance(inst.get("ids_from"), str):
                raw_list = str(merged.get(inst["ids_from"], "") or "")
                ids = [t.strip() for t in raw_list.split(",") if t.strip()]
            if ids:
                self._register_sim_children(ctype, ids)

    def _adopt_dotted_state_children(self) -> None:
        """A simulator: section that seeds dotted child keys
        ("output.2.input: 5") implies those children exist — adopt them into
        the roster so their queries answer even with default config counts."""
        child_types = self._child_types()
        if not child_types:
            return
        for key in list(self._state):
            parts = key.split(".")
            if len(parts) != 3:
                continue
            ctype, cid, prop = parts
            if ctype in child_types and prop in self._child_vars(ctype):
                self._register_sim_children(ctype, [cid])

    def set_child_entities(self, child_entities: dict[str, dict[str, dict]]) -> None:
        """Project children extend the roster (labels ride along for the UI).

        The manager calls this after construction with the padded ids the
        platform uses in state keys; the roster stores the unpadded wire form.
        """
        super().set_child_entities(child_entities)
        for ctype, entries in (child_entities or {}).items():
            if ctype not in self._child_types() or not isinstance(entries, dict):
                continue
            tdef = self._child_types().get(ctype)
            ids = []
            for padded in entries:
                # The shared rule returns None for "can't be that kind of id",
                # leaving each caller its own failure. Here an id the driver
                # couldn't route still names a child in the roster, so keep
                # the raw text rather than dropping it. `is not None` and not
                # `or`: local id 0 is a real id and falsy.
                local = coerce_child_local_id(tdef, padded)
                ids.append(local if local is not None else str(padded))
            self._register_sim_children(ctype, ids)
        self._build_child_query_handlers()

    def to_info_dict(self) -> dict:
        """Extend the base payload with the modeled child roster so the UI
        can group the dotted per-child state keys into a Children section.
        Values are NOT duplicated here — the UI reads them live from the
        state dict, which the per-key WS updates keep current."""
        info = super().to_info_dict()
        # The Simulator UI card reads this as "Pushes state changes". A device
        # that sends `notifications:` pushes whatever `push_state` says.
        info["push_state"] = bool(info.get("push_state")) or self._sends_notifications()
        if not self._child_roster:
            return info
        children: dict[str, Any] = {}
        for ctype, ids in self._child_roster.items():
            tdef = self._child_types().get(ctype) or {}
            cvars = self._child_vars(ctype)
            inst = tdef.get("instances") if isinstance(tdef.get("instances"), dict) else {}
            label_template = inst.get("label") if isinstance(inst.get("label"), str) else None
            type_label = tdef.get("label") or ctype.replace("_", " ").title()
            # Project labels are keyed by the padded id form; normalize
            # integer ids so "001" finds roster entry "1".
            project_labels: dict[str, str] = {}
            for pid, meta in (self._child_entities.get(ctype) or {}).items():
                label = meta.get("label") if isinstance(meta, dict) else None
                if not label:
                    continue
                pid_norm = str(pid).strip()
                if pid_norm.isdigit():
                    pid_norm = str(int(pid_norm))
                project_labels[pid_norm] = str(label)
            entries = []
            for cid in ids:
                label = project_labels.get(cid)
                if not label and label_template:
                    label = label_template.replace("{id}", cid)
                entries.append({"id": cid, "label": label or f"{type_label} {cid}"})
            children[ctype] = {
                "label": type_label,
                "entries": entries,
                "props": list(cvars.keys()),
                # The declared shape of each property, so the UI can draw a
                # mute as a switch instead of a box you type "true" into. The
                # driver has always said `type: boolean`; only the name of the
                # property was being sent, so every one of an amplifier's 240
                # child values arrived untyped and rendered as raw text.
                # `props` stays as it is -- it is the key list the device panel
                # uses to tell child state from device state.
                "prop_defs": {
                    name: {
                        k: v for k, v in {
                            "type": (cdef or {}).get("type", "string"),
                            "label": (cdef or {}).get("label"),
                            "min": (cdef or {}).get("min"),
                            "max": (cdef or {}).get("max"),
                            "step": (cdef or {}).get("step"),
                            "unit": (cdef or {}).get("unit"),
                            "values": (cdef or {}).get("values"),
                        }.items() if v is not None
                    }
                    for name, cdef in cvars.items()
                },
            }
        info["children"] = children
        return info

    def _build_child_state_responses(self) -> None:
        """Derive per-(child type, variable) reply formatting from the
        driver's ``child_set:`` response rules — the child twin of
        _build_state_responses.

        Two passes: rules whose child_set touches exactly one property bind
        first (a per-property view reply like "Out2 In1 Vid"), then broader
        rules fill remaining gaps ("Out2 In1 All" sets input AND
        audio_input). Only capture-ref ids derive anything — a literal id
        rule serves one hardwired child and has no generic form.
        """
        responses = self._driver_def.get("responses", [])
        child_types = self._child_types()
        if not child_types or not isinstance(responses, list):
            return
        for exact_only in (True, False):
            for resp in responses:
                if not isinstance(resp, dict):
                    continue
                raw = resp.get("child_set")
                pattern = resp.get("match", "")
                if not isinstance(raw, list) or not pattern:
                    continue
                for entry in raw:
                    if not isinstance(entry, dict):
                        continue
                    ctype = entry.get("type")
                    state_map = entry.get("state")
                    if ctype not in child_types or not isinstance(state_map, dict):
                        continue
                    if exact_only != (len(state_map) == 1):
                        continue
                    id_group, wire_id_map = self._child_set_id_group(entry)
                    if id_group is None:
                        continue
                    for prop, expr in state_map.items():
                        self._absorb_child_rule(
                            ctype, prop, pattern, id_group, wire_id_map, expr
                        )

    @staticmethod
    def _child_set_id_group(entry: dict) -> tuple[int | None, dict[str, str] | None]:
        """The capture group holding a child_set rule's child id, plus the
        local-id -> wire-id map (the reverse of the rule's wire -> local
        ``map:``) a reply must apply to put the device's own id form back on
        the wire."""
        cid = entry.get("id")
        wire_map: dict[str, str] | None = None
        if isinstance(cid, dict):
            gref = cid.get("group")
            if isinstance(gref, str) and gref.startswith("$"):
                gref = gref[1:]
            try:
                group = int(gref)
            except (TypeError, ValueError):
                return None, None
            raw_map = cid.get("map")
            if isinstance(raw_map, dict) and raw_map:
                wire_map = {str(v): str(k) for k, v in raw_map.items()}
            return group, wire_map
        if isinstance(cid, str) and cid.startswith("$"):
            try:
                return int(cid[1:]), None
            except ValueError:
                return None, None
        return None, None

    def _absorb_child_rule(
        self,
        ctype: str,
        prop: str,
        pattern: str,
        id_group: int,
        wire_id_map: dict[str, str] | None,
        expr: Any,
    ) -> None:
        """Fold one child_set property expression into the (ctype, prop)
        reply table: a capture ref becomes the {value} template, a mapped
        capture becomes one value_map entry per mapped value, and a static
        value becomes a whole-reply value_map entry."""
        key = (ctype, prop)
        if key not in self._child_state_responses:
            self._child_state_responses[key] = ChildStateResponse(
                ctype, prop, conversion_of(self._child_vars(ctype).get(prop))
            )
        sr = self._child_state_responses[key]

        if isinstance(expr, str) and expr.startswith("$"):
            try:
                group = int(expr[1:])
            except ValueError:
                return
            template = emit_template_multi(
                pattern, {id_group: "{child_id}", group: "{value}"}
            )
            if template:
                sr.add_template(template, rule_pattern(pattern, group), wire_id_map)
            return
        if isinstance(expr, dict):
            if "group" in expr:
                try:
                    group = int(expr["group"])
                except (TypeError, ValueError):
                    return
                value_map = expr.get("map")
                if isinstance(value_map, dict) and value_map:
                    for wire_tok, value in value_map.items():
                        vkey = map_key(value)
                        if vkey in sr.value_map:
                            continue
                        reply = emit_template_multi(
                            pattern,
                            {id_group: "{child_id}", group: str(wire_tok)},
                        )
                        if reply:
                            sr.value_map[vkey] = reply
                            if sr.id_wire_map is None:
                                sr.id_wire_map = wire_id_map
                else:
                    template = emit_template_multi(
                        pattern, {id_group: "{child_id}", group: "{value}"}
                    )
                    if template:
                        sr.add_template(template, rule_pattern(pattern, group), wire_id_map)
                return
            if "value" in expr:
                expr = expr["value"]
            else:
                return
        # Static value (bare or {value: ...}): the whole reply is a literal
        # carrying only the child id slot.
        vkey = map_key(expr)
        if vkey not in sr.value_map:
            reply = emit_template_multi(pattern, {id_group: "{child_id}"})
            if reply:
                sr.value_map[vkey] = reply
                if sr.id_wire_map is None:
                    sr.id_wire_map = wire_id_map

    def _build_child_list_answers(self) -> None:
        """Read each ``child_list`` rule back into how the device writes the
        list, per child variable it fills: the reply with the list (and the
        page's start) as slots, one item with the value (and its child id)
        as slots. A variable a rule fills from a static, or a rule whose
        patterns the emitter cannot model (a lookaround), gets nothing, and
        a list read for it goes unanswered."""
        child_types = self._child_types()
        for resp in self._driver_def.get("responses") or []:
            spec = resp.get("child_list") if isinstance(resp, dict) else None
            pattern = resp.get("match") if isinstance(resp, dict) else None
            if not isinstance(spec, dict) or not isinstance(pattern, str):
                continue
            ctype = spec.get("type")
            element = spec.get("element")
            state_map = spec.get("state")
            if (
                ctype not in child_types
                or not isinstance(element, str)
                or not isinstance(state_map, dict)
            ):
                continue
            list_group = _ref_number(spec.get("group", 1))
            cid = spec.get("id")
            id_group = _ref_number(cid.get("group") if isinstance(cid, dict) else cid)
            id_wire_map: dict[str, str] | None = None
            if isinstance(cid, dict) and isinstance(cid.get("map"), dict) and cid["map"]:
                id_wire_map = {str(v): str(k) for k, v in cid["map"].items()}
            raw_start = spec.get("start", 1)
            start_group = _ref_number(raw_start) if isinstance(raw_start, str) else None
            start = raw_start if isinstance(raw_start, int) and not isinstance(raw_start, bool) else 1
            if list_group is None or (cid is not None and id_group is None):
                continue
            outer_slots = {list_group: "{list}"}
            if cid is None and start_group is not None:
                outer_slots[start_group] = "{start}"
            outer = emit_template_multi(pattern, outer_slots)
            if outer is None:
                continue
            for var, expr in state_map.items():
                key = (ctype, var)
                if key in self._child_list_answers:
                    continue
                value_words: dict[str, str] | None = None
                if isinstance(expr, str) and expr.startswith("$"):
                    group = _ref_number(expr)
                elif isinstance(expr, dict) and "group" in expr:
                    group = _ref_number(expr.get("group"))
                    if isinstance(expr.get("map"), dict) and expr["map"]:
                        value_words = {}
                        for word, value in expr["map"].items():
                            value_words.setdefault(map_key(value), str(word))
                else:
                    continue
                if group is None or group == id_group:
                    continue
                item_slots = {group: "{value}"}
                if id_group is not None:
                    item_slots[id_group] = "{child_id}"
                item = emit_template_multi(element, item_slots)
                if item is None:
                    continue
                self._child_list_answers[key] = ChildListAnswer(
                    ctype, var, outer, item, rule_pattern(element, group),
                    spec.get("separator") or "", cid is None, start,
                    value_words, id_wire_map,
                    conversion_of(self._child_vars(ctype).get(var)),
                )

    def _build_list_query_handlers(self) -> None:
        """Handlers for list reads: a query sent once whose ``query_for`` is
        ``<child type>.<variable>``, and each page of a paged per-child query,
        for every variable a ``child_list`` rule fills."""
        for q in every_query_entry(self._driver_def):
            if not isinstance(q, dict):
                continue
            template = q.get("send")
            qf = q.get("query_for")
            if not (isinstance(template, str) and template and isinstance(qf, str)):
                continue
            ctype = q.get("each_child")
            if ctype is None:
                ctype, _, var = qf.partition(".")
                if not var or (ctype, var) not in self._child_list_answers:
                    continue
                wire = self._query_wire_line(template)
                if wire:
                    self._child_query_handlers.append(ListQueryHandler(
                        re.compile(f"^{re.escape(wire)}$"), ctype, var,
                    ))
                continue
            page = q.get("page_size")
            if (
                not isinstance(page, int) or isinstance(page, bool) or page < 2
                or (ctype, qf) not in self._child_list_answers
            ):
                continue
            roster = [int(c) if c.isdigit() else c for c in self._child_roster.get(ctype, [])]
            for first in page_starts(roster, page):
                wire = self._query_wire_line(
                    safe_substitute(template, {"child_id": first})
                )
                if wire:
                    self._child_query_handlers.append(ListQueryHandler(
                        re.compile(f"^{re.escape(wire)}$"), ctype, qf,
                        str(first), page,
                    ))

    def _execute_list_query_handler(self, handler: ListQueryHandler) -> bytes | None:
        """Answer a list read with every child it covers, from each child's
        own state. By place, the list runs without a gap from its first child
        (a child the roster lacks reads as its variable's default, the way a
        device lists outputs nobody configured); by id, it lists the rostered
        children the read covers."""
        answer = self._child_list_answers.get((handler.child_type, handler.response_var))
        if answer is None:
            return None
        ctype, var = handler.child_type, handler.response_var
        roster = self._child_roster.get(ctype, [])
        ids: list[str]
        if answer.by_place:
            numbers = sorted(int(c) for c in roster if c.isdigit())
            first = int(handler.first) if handler.first and handler.first.isdigit() else (
                answer.start if handler.first is None else None
            )
            if first is None or not numbers:
                return None
            last = numbers[-1] if handler.count is None else min(
                numbers[-1], first + handler.count - 1
            )
            ids = [str(n) for n in range(first, last + 1)]
            start: int | str = first
        else:
            ids = list(roster)
            if handler.first is not None and handler.count is not None and handler.first.isdigit():
                low = int(handler.first)
                ids = [c for c in ids if c.isdigit() and low <= int(c) < low + handler.count]
            start = handler.first or ""
        if not ids:
            return None
        var_def = self._child_vars(ctype).get(var) or {}
        items = []
        for cid in ids:
            key = f"{ctype}.{cid}.{var}"
            value = self._state[key] if key in self._state else state_var_default(var_def)
            items.append(answer.render_item(cid, value))
        return (answer.render(start, items) + self._get_delimiter()).encode()

    def _build_child_query_handlers(self) -> None:
        """One handler per (each_child query x rostered child). Declared
        semantics only: the entry's ``query_for`` names which child variable
        the reply reports — without it there is nothing to derive. List reads
        (``_build_list_query_handlers``) come first: a paged query's page
        names its first child the way a per-child query does."""
        self._child_query_handlers = []
        self._build_list_query_handlers()
        for q in every_query_entry(self._driver_def):
            if not isinstance(q, dict) or "each_child" not in q:
                continue
            if "page_size" in q:
                continue
            ctype = q.get("each_child")
            template = q.get("send")
            qf = q.get("query_for")
            if not (
                isinstance(ctype, str)
                and isinstance(template, str) and template
                and isinstance(qf, str) and qf
            ):
                continue
            for cid in self._child_roster.get(ctype, []):
                sub: Any = int(cid) if cid.isdigit() else cid
                wire = self._query_wire_line(
                    safe_substitute(template, {"child_id": sub})
                )
                if not wire:
                    continue
                self._child_query_handlers.append(ChildQueryHandler(
                    pattern=re.compile(f"^{re.escape(wire)}$"),
                    child_type=ctype,
                    child_id=cid,
                    response_var=qf,
                ))

    def _execute_child_query_handler(
        self, handler: ChildQueryHandler | ListQueryHandler
    ) -> bytes | None:
        """Answer a per-child query from the addressed child's own state."""
        if isinstance(handler, ListQueryHandler):
            return self._execute_list_query_handler(handler)
        resp = self._child_state_responses.get(
            (handler.child_type, handler.response_var)
        )
        if resp is None:
            return None
        value = self._state.get(
            f"{handler.child_type}.{handler.child_id}.{handler.response_var}"
        )
        text = resp.format(handler.child_id, value)
        if text is None:
            return None
        return (text + self._get_delimiter()).encode()

    def _build_inline_response_handlers(self) -> None:
        """Compile (pattern, set) handlers from the merged inline responses.

        Used only by Generic (inline-protocol) devices to react to an incoming
        command that matches one of the device's own response patterns.
        """
        for resp in self._driver_def.get("responses", []):
            match_pattern = resp.get("match", "")
            set_dict = resp.get("set", {})
            if not match_pattern or not set_dict:
                continue
            try:
                pattern = re.compile(f"^{match_pattern}$")
            except re.error:
                logger.warning(
                    "%s: skipping bad inline response pattern %r",
                    self.driver_id, match_pattern,
                )
                continue
            self._inline_response_handlers.append((pattern, set_dict))

    def _apply_inline_response(
        self, text: str, m: re.Match, set_dict: dict
    ) -> bytes | None:
        """Apply an inline response's state changes for an incoming command and
        echo the command back as the device's confirmation."""
        for state_key, src in set_dict.items():
            if isinstance(src, str) and src.startswith("$"):
                try:
                    value: Any = m.group(int(src[1:]))
                except (ValueError, IndexError):
                    continue
                value = self._from_wire(state_key, value)
            else:
                value = src
            self.set_state(state_key, self._coerce_value(state_key, value))
        return (text + self._get_delimiter()).encode()

    def _build_inline_json_polls(self) -> None:
        """Match each HTTP command marked Poll, when the device reads any JSON
        field. Nothing is answered for a device without one: a body none of
        its responses read would only look like the wrong driver."""
        state_vars = self._driver_def.get("state_variables") or {}
        for resp in self._driver_def.get("responses") or []:
            if isinstance(resp, dict) and resp.get("json"):
                self._inline_json_fields.extend(
                    m for m in build_json_mappings(resp, state_vars)
                    if m.get("state") and m.get("key") and "contains" not in m
                )
        if not self._inline_json_fields:
            return
        for cmd in (self._driver_def.get("commands") or {}).values():
            if not isinstance(cmd, dict) or not cmd.get("poll") or "send" in cmd:
                continue
            method = str(cmd.get("method") or "GET").upper()
            pattern = re.escape(f"{method} {cmd.get('path') or '/'}")
            # A {placeholder} in the path matches whatever the driver filled in.
            pattern = re.sub(r"\\\{\w+\\\}", r"[^\\s|]+", pattern)
            self._inline_json_polls.append(re.compile(f"^{pattern}(?:\\|.*)?$"))

    def _inline_json_reply(self) -> bytes:
        """A JSON body holding each JSON field's current value, a dotted key
        nested the way the driver reads it."""
        body: dict[str, Any] = {}
        for mapping in self._inline_json_fields:
            value = self.get_state(mapping["state"])
            segments = [s for s in str(mapping["key"]).split(".") if s]
            if value is None or not segments:
                continue
            node = body
            for seg in segments[:-1]:
                if not isinstance(node.get(seg), dict):
                    node[seg] = {}
                node = node[seg]
            node[segments[-1]] = value
        return json.dumps(body).encode()

    def _build_osc_address_handlers(self) -> None:
        """Build OSC address → state mapping from responses with 'address' key."""
        responses = self._driver_def.get("responses", [])
        for resp_def in responses:
            address = resp_def.get("address")
            if not address:
                continue
            mappings = resp_def.get("mappings", [])
            if mappings:
                self._osc_address_handlers.append((address, mappings))

        logger.info(
            "Auto-gen OSC simulator for %s: %d address handlers",
            self.driver_id, len(self._osc_address_handlers),
        )

    def _build_osc_state_reverse_map(self) -> None:
        """Build reverse map: state_key → (osc_address, arg_idx, tag, reverse_value_map).

        Used to push state changes from the simulator UI to the connected driver
        via OSC messages (the OSC equivalent of TCP notifications).
        """
        for addr_pattern, mappings in self._osc_address_handlers:
            for mapping in mappings:
                state_key = mapping.get("state")
                if not state_key:
                    continue
                arg_idx = mapping.get("arg", 0)
                value_type = mapping.get("type", "float")
                tag = _type_to_osc_tag(value_type)

                reverse_map: dict[str, str] | None = None
                value_map = mapping.get("map")
                if value_map:
                    reverse_map = {
                        str(state_val).lower(): str(osc_val)
                        for osc_val, state_val in value_map.items()
                    }

                self._osc_state_to_address[state_key] = (
                    addr_pattern, arg_idx, tag, reverse_map,
                )

    # ── Merge explicit simulator: section ──

    def _skips_blank_sign_in(self) -> bool:
        """True when the driver skips its telnet sign-in for a blank
        credential, the case the sign_in_required mode plays out."""
        auth_def = self._driver_def.get("auth")
        return (
            isinstance(auth_def, dict)
            and auth_def.get("type", "telnet_login") == "telnet_login"
            and self._driver_def.get("transport", "tcp") == "tcp"
            and bool(auth_def.get("username_prompt"))
            and bool(auth_def.get("password_prompt"))
            and bool(auth_def.get("skip_if_empty", True))
        )

    def _merge_simulator_section(self, sim: dict) -> None:
        """Merge explicit simulator: enhancements onto auto-generated behavior."""
        # Override initial state
        for key, value in sim.get("initial_state", {}).items():
            self._state[key] = value

        # Override delays
        for key, value in sim.get("delays", {}).items():
            self._delays[key] = value

        # How much the device can take in (off until switched on per device).
        if "capacity" in sim:
            self._capacity.declare(sim["capacity"])

        # Whether the device serves one connection at a time (in force at once).
        if "single_client" in sim:
            self._single_client.declare(sim["single_client"])

        # Add error modes
        for mode, mode_def in sim.get("error_modes", {}).items():
            self._error_modes[mode] = mode_def

        # Add declarative controls schema
        controls = sim.get("controls")
        if controls:
            self.SIMULATOR_INFO["controls"] = controls

        # Build state machines. Skip malformed entries with a warning rather
        # than letting a missing key raise a KeyError that aborts the whole
        # device's construction (the validator flags these up front).
        from openavc.simulator.base import StateMachine
        for name, sm_def in sim.get("state_machines", {}).items():
            if not (isinstance(sm_def, dict)
                    and {"states", "initial", "transitions"} <= sm_def.keys()):
                logger.warning(
                    "%s: skipping malformed state_machine '%s' "
                    "(needs states, initial, transitions)", self.driver_id, name,
                )
                continue
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
            # OSC handlers use "address" key (fnmatch pattern)
            if "address" in handler_def and "handler" in handler_def:
                try:
                    code = compile(handler_def["handler"], f"<sim:{self.driver_id}>", "exec")
                except SyntaxError as e:
                    logger.warning(
                        "%s: skipping OSC handler for %s — handler code has a "
                        "syntax error: %s",
                        self.driver_id, handler_def["address"], e,
                    )
                    continue
                self._osc_script_handlers.append(OSCScriptHandler(
                    address_pattern=handler_def["address"],
                    code=code,
                ))
                continue

            # TCP/UDP handlers use "receive" or "match" as regex pattern
            pattern_str = handler_def.get("receive") or handler_def.get("match", "")
            if not pattern_str:
                continue
            try:
                pattern = re.compile(f"^{pattern_str}$")
            except re.error:
                logger.warning("Invalid regex in simulator command_handler: %s", pattern_str)
                continue

            if "handler" in handler_def:
                try:
                    code = compile(handler_def["handler"], f"<sim:{self.driver_id}>", "exec")
                except SyntaxError as e:
                    logger.warning(
                        "%s: skipping command handler %r — handler code has a "
                        "syntax error: %s", self.driver_id, pattern_str, e,
                    )
                    continue
                self._script_handlers.append(ScriptHandler(
                    pattern=pattern,
                    code=code,
                ))
            else:
                self._explicit_handlers.append(ExplicitHandler(
                    pattern=pattern,
                    respond=handler_def.get("respond"),
                    set_state=handler_def.get("set_state", {}),
                ))

        # The device's own wording for state changes (push_channels.py).
        self._read_notifications(sim.get("notifications", {}))

    # Ensure handler lists exist even without simulator: section
    _explicit_handlers: list[ExplicitHandler] = []
    _script_handlers: list[ScriptHandler] = []
    # ...and the child tables before __init__ reaches their assignment
    # (set_state can fire during construction).
    _child_roster: dict[str, list[str]] = {}
    _child_state_responses: dict[tuple[str, str], ChildStateResponse] = {}
    _child_query_handlers: list[ChildQueryHandler] = []

    # ── State change notifications ──

    def set_state(self, key: str, value: Any) -> None:
        """Store a value within its declared bounds and, when it changed,
        tell the driver the way the device would."""
        value = self._within_bounds(key, value)
        old = self._state.get(key)
        super().set_state(key, value)
        if old == value:
            return

        # A `notifications:` template is the device's own wording for this
        # change. When one covers the new value and can reach the driver, it
        # replaces the standard push instead of following it, so one change
        # is one frame, as on the device.
        template = self._notification_template(key, value)
        replaced = template is not None and self._sends_notifications()
        if self._push_state and not replaced:
            self._push_changed_value(key, value)
        if template is not None:
            self._send_notification(template, key, value)

    def _within_bounds(self, key: str, value: Any) -> Any:
        """A number clamped to its variable's declared min/max.

        Bounds are typed as numbers, but a malformed driver could author a
        non-numeric scalar -- _as_number returns None for those, so clamping is
        skipped rather than raising on `value < min`. For an integer value a
        fractional bound is rounded inward (ceil a lower bound, floor an upper
        bound) so the clamped value still respects the bound instead of
        truncating past it."""
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return value
        var_def = self._var_def_for_key(key)
        v_min = _as_number(var_def.get("min"))
        v_max = _as_number(var_def.get("max"))
        return _lowered_to(_raised_to(value, v_min), v_max)

    def _push_changed_value(self, key: str, value: Any) -> None:
        """The standard push (``push_state``): the changed value's reply on
        the control link, or as an OSC message. Only for a change from
        outside the protocol (the Simulator UI, the API, an error injection):
        state written while answering a command or a message is the reply,
        not news."""
        if self._is_osc:
            if self._osc_push_wanted(key):
                self._push_osc_state(key, value)
            return
        if self._handling_command or not self._control_link_open():
            return
        text = self._format_state_reply(key, value)
        if text is not None:
            self._send_on_control_link((text + self._get_delimiter()).encode())

    def _osc_push_wanted(self, key: str) -> bool:
        """An OSC change goes out when no message is being answered, the key
        has an address to push on, and a peer has spoken to the device."""
        if self._handling_osc or key not in self._osc_state_to_address:
            return False
        return self._peer_heard()

    def _push_osc_state(self, key: str, value: Any) -> None:
        """Send an OSC message to the connected driver for a state change."""
        from openavc.transport.osc_codec import osc_encode_message

        addr, _arg_idx, tag, reverse_map = self._osc_state_to_address[key]

        if reverse_map:
            lookup = str(value).lower()
            raw = reverse_map.get(lookup)
            if raw is None:
                return
            if tag == "i":
                osc_value: Any = int(raw)
            elif tag == "f":
                osc_value = float(raw)
            else:
                osc_value = raw
        else:
            value = self._to_wire(key, value)
            if tag == "f":
                osc_value = float(value)
            elif tag == "i":
                osc_value = int(value) if not isinstance(value, bool) else (1 if value else 0)
            elif tag == "s":
                osc_value = str(value)
            else:
                osc_value = value

        self.push_datagram(osc_encode_message(addr, [(tag, osc_value)]))

    def _script_reply(self, key: str) -> str | None:
        """A script handler's ``reply(key)``: the text this device sends to
        report ``key``'s current value, the same text a query or a push of it
        carries (the device's own number, padded the way its response rule
        reads it), without the delimiter. None when no response rule
        describes the key. State holds real values; this is how a handler
        answering several keys at once speaks the device's numbers without
        repeating every conversion."""
        return self._format_state_reply(key, self._state.get(key))

    def _format_state_reply(self, key: str, value: Any) -> str | None:
        """Reply/push text for a state key — flat keys through their
        StateResponse, dotted child keys through the (type, prop) child
        table. None when nothing derivable represents the value."""
        if key in self._state_responses:
            return self._state_responses[key].format(value)
        parts = key.split(".")
        if len(parts) == 3:
            resp = self._child_state_responses.get((parts[0], parts[2]))
            if resp is not None and parts[1] in self._child_roster.get(parts[0], []):
                return resp.format(parts[1], value)
        return None

    # ── Helpers ──

    def _get_delimiter(self) -> str:
        """Get the line delimiter as a string.

        HTTP responses don't have a line delimiter — the response body is
        whatever the handler returned, with no trailing CRLF.
        """
        if self._is_http:
            return ""
        return decode_delimiter(self._driver_def.get("delimiter", "\r\n"))

    def _var_def_for_key(self, state_key: str) -> dict:
        """Variable definition for a flat key, or for a dotted child key
        ("<type>.<id>.<prop>") the child type's own declaration."""
        parts = state_key.split(".")
        if len(parts) == 3:
            var_def = self._child_vars(parts[0]).get(parts[2])
            if isinstance(var_def, dict):
                return var_def
        var_def = self._driver_def.get("state_variables", {}).get(state_key)
        return var_def if isinstance(var_def, dict) else {}

    def _from_wire(self, state_key: str, value: Any) -> Any:
        """The device's own number as the real value the state variable
        holds, through its scale / offset / unknown. Unchanged when it
        declares none."""
        var_def = self._var_def_for_key(state_key)
        handled, real = reading_from_wire(
            value, conversion_of(var_def), var_def.get("type", "string")
        )
        return real if handled else value

    def _to_wire(self, state_key: str, value: Any) -> Any:
        """The real value back to the device's own number (see _from_wire)."""
        return value_to_wire(value, conversion_of(self._var_def_for_key(state_key)))

    def _numeric_state_var(self, state_key: str) -> bool:
        """True when the state variable's declared type is numeric."""
        vtype = self._var_def_for_key(state_key).get("type", "string")
        return vtype in ("integer", "number", "float")

    def _coerce_value(self, state_key: str, value: Any) -> Any:
        """Coerce a value to the state variable's declared type and clamp to range."""
        var_def = self._var_def_for_key(state_key)
        var_type = var_def.get("type", "string")

        if var_type == "integer":
            try:
                result = int(value)
            except (ValueError, TypeError):
                # Parity with ConfigurableDriver._coerce_value: a non-integer
                # value is returned raw (so bindings/conditions see what the
                # real device would send) rather than silently coerced to 0.
                return value
            v_min = _as_number(var_def.get("min"))
            v_max = _as_number(var_def.get("max"))
            if v_min is not None:
                result = max(math.ceil(v_min), result)
            if v_max is not None:
                result = min(math.floor(v_max), result)
            return result
        elif var_type == "number":
            try:
                result = float(value)
            except (ValueError, TypeError):
                return value
            v_min = _as_number(var_def.get("min"))
            v_max = _as_number(var_def.get("max"))
            if v_min is not None:
                result = max(v_min, result)
            if v_max is not None:
                result = min(v_max, result)
            return result
        elif var_type == "boolean":
            if isinstance(value, bool):
                return value
            return str(value).lower() in ("true", "1", "on", "yes")
        else:
            return str(value)

    def _resolve_template(self, template: str, match: re.Match | None = None) -> str:
        """Resolve {1}, {2}, {state.key} placeholders in a template.

        A state value goes in as the device's own number (``_to_wire``), as
        every other reply the simulator sends does."""
        result = template

        # Replace capture group references {1}, {2}
        if match:
            for i in range(1, match.lastindex + 1 if match.lastindex else 1):
                try:
                    result = result.replace(f"{{{i}}}", match.group(i) or "")
                except IndexError:
                    pass

        # Replace state references {state.key}
        for key in set(_HANDLER_STATE_REF.findall(result)):
            if key in self._state:
                result = result.replace(
                    f"{{state.{key}}}", str(self._to_wire(key, self._state[key]))
                )

        return result

    @staticmethod
    def _slider_step(v_min: float, v_max: float, declared, integral: bool):
        """The step for an auto-generated slider, when the driver names none.

        A declared step is the author's call and is returned untouched. What is
        left is the step WE invent, and inventing 0.1 for every number was
        wrong in a way that only shows up on a real driver: an amplifier
        reporting AC line voltage over 0-300 V got 3000 stops, and its power
        draw over 0-5000 W got 50,000. A range input fires a change per pixel
        of travel, so those became a control that could not be placed and a
        flood of writes on the way.

        So pick the finest step off a 1/2/5 ladder that keeps the slider under
        MAX_STOPS. 0-300 V lands on 1 V, 0-5000 W on 10 W, -80-0 dBFS on 0.5 dB
        -- each the resolution a person would have chosen. Integers never go
        below 1. Exactness is not lost: the readout beside the slider is
        typeable, which is where an exact 440 Hz comes from.
        """
        if isinstance(declared, (int, float)) and not isinstance(declared, bool):
            return declared
        span = float(v_max) - float(v_min)
        if span <= 0:
            return 1 if integral else 0.1
        MAX_STOPS = 500
        ladder = [1, 2, 5]
        exp = -2 if not integral else 0
        while exp <= 9:
            for mult in ladder:
                step = mult * (10 ** exp)
                if step < 1 and integral:
                    continue
                if span / step <= MAX_STOPS:
                    # Keep integer ranges on integer steps, and trim binary
                    # float dust (3 * 0.1 is not 0.3) so the value that reaches
                    # the UI is the one written here.
                    return int(step) if integral or step >= 1 else round(step, 10)
            exp += 1
        return int(span) or 1

    @staticmethod
    def _build_info(driver_def: dict) -> dict:
        """Build SIMULATOR_INFO from driver definition."""
        state_vars = driver_def.get("state_variables", {})
        initial_state = {}
        controls = []
        for key, var_def in state_vars.items():
            var_type = var_def.get("type", "string")
            label = var_def.get("label", key.replace("_", " ").title())
            # The seed value is the shared rule; the branches below decide
            # only which control the UI gets.
            initial_state[key] = state_var_default(var_def)
            if var_type == "integer":
                v_min = var_def.get("min")
                v_max = var_def.get("max")
                if v_min is not None and v_max is not None:
                    ctl = {"type": "slider", "key": key, "label": label,
                           "min": v_min, "max": v_max,
                           "step": YAMLAutoSimulator._slider_step(
                               v_min, v_max, var_def.get("step"), integral=True)}
                    if var_def.get("unit"):
                        ctl["unit"] = var_def["unit"]
                    controls.append(ctl)
                else:
                    controls.append({"type": "indicator", "key": key, "label": label})
            elif var_type == "number":
                v_min = var_def.get("min")
                v_max = var_def.get("max")
                if v_min is not None and v_max is not None:
                    ctl = {"type": "slider", "key": key, "label": label,
                           "min": v_min, "max": v_max,
                           "step": YAMLAutoSimulator._slider_step(
                               v_min, v_max, var_def.get("step"), integral=False)}
                    if var_def.get("unit"):
                        ctl["unit"] = var_def["unit"]
                    controls.append(ctl)
                else:
                    controls.append({"type": "indicator", "key": key, "label": label})
            elif var_type == "boolean":
                controls.append({"type": "toggle", "key": key, "label": label})
            elif var_type == "enum":
                values = var_def.get("values", [])
                if key == "power":
                    controls.append({"type": "power", "key": key})
                elif values:
                    controls.append({"type": "select", "key": key, "label": label,
                                     "options": values})
                else:
                    controls.append({"type": "indicator", "key": key, "label": label})
            else:
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
    def __init__(
        self,
        name: str,
        pattern: re.Pattern,
        state_changes: dict,
        response_var: str | None,
        group_bases: dict[int, int] | None = None,
        child_type: str | None = None,
        child_id_group: int = 0,
        group_conversions: dict[int, dict] | None = None,
        child_wire_map: dict[str, str] | None = None,
        child_state_keys: set[str] | None = None,
        response_is_child: bool = False,
        group_value_maps: dict[int, dict] | None = None,
    ):
        self.name = name
        self.pattern = pattern
        self.state_changes = state_changes
        self.response_var = response_var
        # Capture groups whose wire form is non-decimal ({level:02X}),
        # mapped to the int base that decodes them.
        self.group_bases = group_bases or {}
        # Capture groups carrying a param (or setting) with scale / offset:
        # the device's number, converted back to the real value it stores.
        self.group_conversions = group_conversions or {}
        # Capture groups carrying a device setting with a map: the setting
        # value -> device word table, read in reverse.
        self.group_value_maps = group_value_maps or {}
        # Child-addressed command (exactly one child_id param): the capture
        # group holding the child id, the wire->local id translation from the
        # param's map:, and which state_changes keys are the child's own
        # variables (routed to its dotted state) vs flat fallbacks.
        self.child_type = child_type
        self.child_id_group = child_id_group
        self.child_wire_map = child_wire_map
        self.child_state_keys = child_state_keys or set()
        self.response_is_child = response_is_child


def _framed_send(cmd_def: dict, prefix: str) -> Any:
    """The text a command puts on the wire, framed with the driver's prefix
    unless it is raw, already starts with it, or is a udp: payload (the side
    channel never carries the main protocol's prefix); falsy when it sends
    nothing."""
    send_template = cmd_def.get("send", "")
    if not send_template:
        udp_def = cmd_def.get("udp")
        return (udp_def.get("payload") or "") if isinstance(udp_def, dict) else send_template
    if not prefix or cmd_def.get("raw"):
        return send_template
    return send_template if send_template.startswith(prefix) else prefix + send_template


def _raised_to(value: Any, low: float | None) -> Any:
    """``value`` raised to a lower bound; an integer to the bound rounded up,
    so it still respects a fractional bound."""
    if low is None or not value < low:
        return value
    return math.ceil(low) if isinstance(value, int) else low


def _lowered_to(value: Any, high: float | None) -> Any:
    """``value`` lowered to an upper bound; an integer to the bound rounded
    down."""
    if high is None or not value > high:
        return value
    return math.floor(high) if isinstance(value, int) else high


class _ChildAddress(NamedTuple):
    """The child a command addresses: its one child_id param, that param's
    child type, the wire -> local id map, and the type's variables."""

    param_name: str | None
    child_type: str | None
    wire_map: dict[str, str] | None
    child_vars: dict[str, dict]


def _infers_nothing(handler: CommandHandler) -> bool:
    """A handler read as writing nothing and reporting nothing."""
    return not handler.state_changes and not handler.response_var


class _CommandRead:
    """What one command does, as it is read: the child it addresses, which
    capture writes which variable and how each capture decodes, and the
    variable its reply reports."""

    def __init__(self, send_template: str, params: Any, address: _ChildAddress) -> None:
        self.send_template = send_template
        self.params = params
        self.specs = send_param_specs(send_template, params)
        self.child_param_name = address.param_name
        self.child_type = address.child_type
        self.child_wire_map = address.wire_map
        self.child_vars = address.child_vars
        self.child_id_group = 0
        self.state_changes: dict[str, Any] = {}
        self.child_state_keys: set[str] = set()
        self.response_var: str | None = None
        self.response_is_child = False
        self.group_bases: dict[int, int] = {}
        self.group_conversions: dict[int, dict] = {}

    def read(self, cmd_name: str, cmd_def: dict, state_vars: set[str]) -> None:
        """Read the command as it declares itself, or by its names when it
        declares nothing."""
        if isinstance(cmd_def.get("sets"), dict) or cmd_def.get("query_for"):
            self.read_declared(cmd_def)
        else:
            self.read_from_names(cmd_name, state_vars)

    def handler(self, name: str, pattern: re.Pattern) -> CommandHandler:
        """The handler, with no child routing when no capture holds the
        child id: a child-addressed handler needs the id capture to route
        by, so without it the child's own writes are dropped and the rest
        behaves as a plain command."""
        if not self.child_id_group:
            self._drop_child_routing()
        routes_child = self.child_state_keys or self.response_is_child
        return CommandHandler(
            name=name,
            pattern=pattern,
            state_changes=self.state_changes,
            response_var=self.response_var,
            group_bases=self.group_bases,
            group_conversions=self.group_conversions,
            child_type=self.child_type if routes_child else None,
            child_id_group=self.child_id_group,
            child_wire_map=self.child_wire_map,
            child_state_keys=self.child_state_keys,
            response_is_child=self.response_is_child,
        )

    def _drop_child_routing(self) -> None:
        self.child_type = None
        self.child_wire_map = None
        if not self.child_state_keys:
            return
        for var_name in self.child_state_keys:
            self.state_changes.pop(var_name, None)
        self.child_state_keys = set()
        self.response_is_child = False

    def _decode_captures(self, group_of: dict[str, int]) -> None:
        """How each param's capture decodes back to the value the driver
        sent: a non-decimal format spec's base (``{level:02X}`` put hex on
        the wire) and the param's scale / offset. ``group_of`` names each
        param's capture."""
        for param_name, param_def in self.params.items():
            group = group_of.get(param_name)
            ptype = (
                param_def.get("type", "string") if isinstance(param_def, dict) else "string"
            )
            if ptype in ("integer", "child_id"):
                base = spec_int_base(self.specs.get(param_name, ""))
                if base and group is not None:
                    self.group_bases[group] = base
            conversion = conversion_of(param_def)
            if conversion and group is not None:
                self.group_conversions[group] = conversion

    # ── A command that declares what it does ──

    def read_declared(self, cmd_def: dict) -> None:
        """Wired exactly as authored, no name-guessing. Group indices come
        from placeholder order in the template (that's how send_regex
        numbers its captures)."""
        declared_sets = cmd_def.get("sets")
        declared_query_for = cmd_def.get("query_for")
        param_groups = send_param_groups(self.send_template, self.params)
        if self.child_param_name:
            self.child_id_group = param_groups.get(self.child_param_name, 0)
        self._decode_captures(param_groups)
        if isinstance(declared_sets, dict):
            for var_name, set_value in declared_sets.items():
                self._declared_set(var_name, set_value, param_groups)
        self.response_var = declared_query_for or next(iter(self.state_changes), None)
        self.response_is_child = bool(
            self.child_type
            and self.response_var is not None
            and self._reports_child_variable(declared_query_for)
        )

    def _declared_set(self, var_name: str, set_value: Any, param_groups: dict[str, int]) -> None:
        """One ``sets:`` entry: a ``{param}`` reference writes that param's
        capture, anything else is a literal."""
        # On a child-addressed command the child type's own variables win
        # the name; flat vars stay reachable for names the child type
        # doesn't declare.
        if self.child_type and var_name in self.child_vars:
            self.child_state_keys.add(var_name)
        ref = re.fullmatch(r"\{([^{}]+)\}", set_value) if isinstance(set_value, str) else None
        if not ref:
            # Literal, wrapped so an integer literal can't be mistaken for a
            # capture-group index.
            self.state_changes[var_name] = ("literal", set_value)
            return
        group = param_groups.get(ref.group(1))
        if group:
            self.state_changes[var_name] = group  # capture group
        # An unresolvable "{param}" reference declares nothing (the
        # validator flags it at load).

    def _reports_child_variable(self, declared_query_for: Any) -> bool:
        if self.response_var in self.child_state_keys:
            return True
        return bool(declared_query_for) and self.response_var in self.child_vars

    # ── A command that declares nothing: read from its names ──

    def read_from_names(self, cmd_name: str, state_vars: set[str]) -> None:
        """A param named after a variable writes it (the addressed child
        type's variables first, flat second), else the command's own name
        says the variable it reports and what it sets. Captures are numbered
        in the order the params are declared."""
        self._decode_captures({name: idx for idx, name in enumerate(self.params, start=1)})
        for group_idx, param_name in enumerate(self.params, start=1):
            self._param_by_name(param_name, group_idx, state_vars)
        if not self.state_changes:
            # Also how a command with no params is read as a query for the
            # variable its name says.
            self._read_command_name(cmd_name, state_vars)

    def _param_by_name(self, param_name: str, group: int, state_vars: set[str]) -> None:
        if param_name == self.child_param_name:
            self.child_id_group = group
            return
        is_child = bool(self.child_type) and param_name in self.child_vars
        if not is_child and param_name not in state_vars:
            return
        self.state_changes[param_name] = group  # capture group
        if is_child:
            self.child_state_keys.add(param_name)
        if not self.response_var:
            # Only a child variable marks the reply as the child's; a flat
            # one leaves it as it was (an earlier child variable named ""
            # left the reply variable falsy and the mark set).
            self.response_var = param_name
            if is_child:
                self.response_is_child = True

    def _read_command_name(self, cmd_name: str, state_vars: set[str]) -> None:
        """What a command's name says it does: the variable it reports, and
        the value a switching word sets, nothing for a toggle, or the first
        param that is not the child id."""
        target = self._name_target(cmd_name, state_vars)
        if not target:
            return
        self.response_var = target
        self.response_is_child = bool(self.child_type and target in self.child_vars)
        if self.response_is_child:
            self.child_state_keys.add(target)
        switches, value = name_switch(cmd_name)
        if value is not None:
            self.state_changes[target] = value
        elif not switches and self.params:
            # set_X with params: the first param that is not the child id
            self._write_first_value_param(target)
        # A toggle reports the current value and sets nothing.

    def _name_target(self, cmd_name: str, state_vars: set[str]) -> str | None:
        """The variable a command's name says, against the addressed child
        type's variables when it targets a child, then the flat ones."""
        if not self.child_type:
            return infer_state_var(cmd_name, state_vars)
        return (
            infer_state_var(cmd_name, set(self.child_vars))
            or infer_state_var(cmd_name, state_vars)
        )

    def _write_first_value_param(self, target: str) -> None:
        value_group = next(
            (
                idx for idx, pname in enumerate(self.params, start=1)
                if pname != self.child_param_name
            ),
            0,
        )
        if value_group:
            self.state_changes[target] = value_group
        else:
            self.state_changes.pop(target, None)
            self.child_state_keys.discard(target)


class QueryHandler:
    """Auto-generated handler for a polling query."""
    def __init__(self, pattern: re.Pattern, response_var: str):
        self.pattern = pattern
        self.response_var = response_var


class ChildQueryHandler:
    """Auto-generated handler for one child's each_child poll query."""
    def __init__(
        self,
        pattern: re.Pattern,
        child_type: str,
        child_id: str,
        response_var: str,
    ):
        self.pattern = pattern
        self.child_type = child_type
        self.child_id = child_id          # unpadded id, as the wire carries it
        self.response_var = response_var  # a child state variable


class ListQueryHandler:
    """Auto-generated handler for a list read: a query sent once whose
    ``query_for`` names ``<child type>.<variable>``, or one page of a paged
    per-child query. Answered with every child the reply covers, in the shape
    of the driver's ``child_list`` rule."""

    def __init__(
        self,
        pattern: re.Pattern,
        child_type: str,
        response_var: str,
        first: str | None = None,
        count: int | None = None,
    ):
        self.pattern = pattern
        self.child_type = child_type
        self.response_var = response_var
        self.first = first    # the page's first child (unpadded); None = the whole list
        self.count = count    # children on a page; None = the whole list


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


def _ref_number(ref: Any) -> int | None:
    """``"$2"`` or ``2`` as 2; None for anything else."""
    if isinstance(ref, bool):
        return None
    if isinstance(ref, int):
        return ref if ref >= 1 else None
    if isinstance(ref, str) and re.fullmatch(r"\$\d+", ref):
        return int(ref[1:]) or None
    return None


# ── Utility functions ──

class OSCScriptHandler:
    """Script handler for OSC simulator: command_handlers with address pattern."""
    def __init__(self, address_pattern: str, code: Any):
        self.address_pattern = address_pattern
        self.code = code


def _type_to_osc_tag(value_type: str) -> str:
    """Map a state variable type to an OSC type tag."""
    return {
        "float": "f",
        "number": "f",
        "integer": "i",
        "boolean": "i",
        "string": "s",
    }.get(value_type, "f")


# The type tags osc_encode_message understands.
_OSC_TYPE_TAGS = frozenset("fishdbTFN")


def _osc_tag_for(value: Any) -> str:
    """The OSC type tag a bare Python value implies.

    Booleans send ``i`` as 1/0 rather than OSC's payload-free ``T``/``F``,
    because that is what AV gear expects and what ``_type_to_osc_tag`` already
    picks for a boolean state variable.
    """
    if isinstance(value, bool):
        return "i"
    if isinstance(value, int):
        return "i"
    if isinstance(value, float):
        return "f"
    if isinstance(value, (bytes, bytearray)):
        return "b"
    if value is None:
        return "N"
    return "s"


def _normalize_osc_args(args: Any) -> list[tuple[str, Any]]:
    """Coerce a script handler's ``respond`` arguments into (tag, value) pairs.

    ``osc_encode_message`` wants ``(type_tag, value)`` pairs, but the author
    guide describes the OSC handler contract as "``respond(address, args)``
    sends an OSC message" with an ``args`` *list* — which reads as a list of
    values, so handlers get written as ``respond(addr, [cue])``. That used to
    reach the encoder untouched, where ``for tag, value in args`` unpacked the
    two-character string ``"12"`` into tag ``"1"`` and value ``"2"``: no branch
    matched the bogus tag, so the message went out with a garbage type tag and
    **no payload at all**. The driver's ``arg: 0`` mapping then found nothing,
    and a simulator that answers every request with an empty reply is
    indistinguishable from one that works.

    So accept both shapes. An already-typed pair passes through; a bare value
    gets the tag its Python type implies. A sequence that is neither raises,
    which the caller's ``except Exception`` logs — loud beats silent here.

    One ambiguity is unavoidable and deliberate: ``["s", "hello"]`` reads as a
    typed pair, not as two strings. The typed form is the documented one, and a
    bare data value that happens to be a lone type-tag letter is far less
    likely than a handler passing a pair.
    """
    if args is None:
        return []
    if not isinstance(args, (list, tuple)):
        args = [args]

    normalized: list[tuple[str, Any]] = []
    for arg in args:
        if isinstance(arg, (tuple, list)):
            if len(arg) == 2 and isinstance(arg[0], str) and arg[0] in _OSC_TYPE_TAGS:
                normalized.append((arg[0], arg[1]))
                continue
            raise ValueError(
                f"OSC respond() argument {arg!r} is not a (type_tag, value) "
                f"pair — the type tag must be one of {sorted(_OSC_TYPE_TAGS)}. "
                f"Pass a bare value to have its type inferred."
            )
        normalized.append((_osc_tag_for(arg), arg))
    return normalized
