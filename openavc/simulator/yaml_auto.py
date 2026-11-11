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
the PushChannels mixin in push_channels; which of the driver's reads gets a
handler, and what a list read covers, is query_handlers. The order a line is
offered to the handlers is ``_HANDLER_CHAIN``, at the bottom of this module.
The device's half of the driver's ``auth:`` sign-in is telnet_sign_in; what
the Simulator UI is told about the device (each variable's control, the
children section) is ui_info; the ``simulator:`` section's settings are read
by base.py's ``_read_device_settings``, as a Python simulator's are. The
handler a command or a setting is read into is command_handler, a device
setting's write is read into one by setting_handlers, and a Generic device's
inline protocol (its merge, its own response patterns, an HTTP one's JSON
status poll) is inline_device. Construction runs in named stages, each saying
what it must follow.
"""

from __future__ import annotations

import asyncio
import fnmatch
import json
import logging
import math
import re
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any, NamedTuple
from urllib.parse import unquote

import yaml

from openavc.drivers.compiled_protocol import (
    apply_send_frame,
    build_send_frame,
    conversion_of,
    decode_delimiter,
    reading_from_wire,
    safe_substitute,
    split_send_frames,
    state_var_default,
    value_to_wire,
)
from openavc.drivers.child_ids import coerce_child_local_id
from openavc.simulator.command_handler import CommandHandler
from openavc.simulator.command_names import infer_state_var, name_switch
from openavc.simulator.http_simulator import HTTPServerMixin
from openavc.simulator.inline_device import json_body, json_fields, json_polls, response_handlers
from openavc.simulator.inline_device import merge_inline_protocol as _merge_inline_protocol
from openavc.simulator.osc_addresses import (
    PushTarget,
    address_rules,
    normalize_osc_args,
    push_targets,
    system_reply,
    tagged,
    type_tag,
)
from openavc.simulator.osc_simulator import OSCDispatchMixin
from openavc.simulator.push_channels import PushChannels
from openavc.simulator.query_handlers import (
    ChildQueryHandler,
    ListQueryHandler,
    QueryHandler,
    child_handlers,
    flat_handlers,
)
from openavc.simulator.reply_rules import (
    child_replies,
    flat_replies,
    list_answers,
    reversed_ids,
)
from openavc.simulator.reread_queries import every_query_entry
from openavc.simulator.state_replies import (
    ChildListAnswer,
    ChildStateResponse,
    StateResponse,
)
from openavc.simulator.setting_handlers import setting_handlers
from openavc.simulator.send_templates import (
    is_passthrough_send,
    send_param_groups,
    send_param_specs,
    send_regex,
    spec_int_base,
)
from openavc.simulator.tcp_simulator import TCPSimulator
from openavc.simulator.ui_info import children_info, variable_control
from openavc.simulator.telnet_sign_in import (
    SIGN_IN_REQUIRED_DEFINITION,
    SIGN_IN_REQUIRED_MODE,
    TelnetSignIn,
    driver_skips,
    offers_sign_in_required,
    telnet_login,
)

logger = logging.getLogger(__name__)

# How many numbers start(0) tries before a side receiver's refusal stands.
_SIDE_PORT_ATTEMPTS = 5

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


#: A template handler's placeholders: a capture group (`{1}`) or another state
#: value (`{state.key}`). Either brings the device's own number into a
#: `set_state` value; a value with neither is the author's, as written.
_HANDLER_PLACEHOLDER = re.compile(r"\{(?:\d+|state\.[^{}]+)\}")
_HANDLER_STATE_REF = re.compile(r"\{state\.([^{}]+)\}")

def _udp_side_commands(driver_def: dict) -> dict[str, dict]:
    """Each command's udp: block, by command name."""
    return {
        name: cmd["udp"]
        for name, cmd in (driver_def.get("commands") or {}).items()
        if isinstance(cmd, dict) and isinstance(cmd.get("udp"), dict)
    }


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
        """The device a definition describes, built in stages whose order is
        part of what it means; each stage says what it must follow."""
        # A Generic device's inline protocol is merged first: it is the
        # definition every stage reads (inline_device.py).
        driver_def, self._inline_protocol = _merge_inline_protocol(driver_def, config)
        # Before the base, which reads the device's settings from it.
        self.SIMULATOR_INFO = self._build_info(driver_def)
        super().__init__(device_id, config)
        self._read_framing(driver_def)
        self._driver_def = driver_def
        self._read_transport(driver_def, config)
        self._build_generated_handlers()
        sim_section = driver_def.get("simulator", {})
        self._apply_section(sim_section)
        self._build_children()
        self._set_up_pushing(sim_section)
        logger.info(
            "Auto-gen simulator for %s: %d command handlers, %d query handlers, %d state responses",
            self.driver_id,
            len(self._command_handlers),
            len(self._query_handlers),
            len(self._state_responses),
        )

    def _read_framing(self, driver_def: dict) -> None:
        """How lines are read, after the base set its own default.

        YAML drivers are text-based, so the simulator reads flexible lines
        (\\r, \\n or \\r\\n, whatever the driver's delimiter). A driver whose
        commands ride inside a computed-length binary header (send_frame,
        e.g. eISCP) sends frames the line reader would mis-split (the length
        byte can be 0x0a or 0x0d): those are read as length-prefixed frames,
        the header stripped before matching and put back on every reply, as
        the real device answers.
        """
        self._line_mode = True
        self._send_frame = self._build_sim_send_frame(driver_def.get("send_frame"))
        if self._send_frame:
            self._line_mode = False

    def _read_transport(self, driver_def: dict, config: dict | None) -> None:
        """Which server start() runs, and what listens beside it.

        UDP, OSC and HTTP run the datagram or HTTP server from the mixins in
        place of the inherited TCP one. A command that sends a datagram
        BESIDE the main transport (a udp: block: a wake-on-LAN magic packet,
        a message to a presentation's UDP receiver) gives a TCP or HTTP
        device a UDP receiver on its own port number, so the datagram shows
        in the protocol log and its declared state effect applies. An HTTPS
        device is served over TLS, as the driver dials it in the field
        (self_signed_tls.py); the platform reads that back out of
        to_info_dict() and leaves its ssl setting alone while redirected.
        """
        transport = driver_def.get("transport", "tcp")
        self._is_udp = transport == "udp"
        self._is_osc = transport == "osc"
        self._is_http = transport == "http"
        self._udp_side_commands: dict[str, dict] = _udp_side_commands(driver_def)
        self._side_receiver = bool(self._udp_side_commands) and not (
            self._is_udp or self._is_osc
        )
        if self._is_http and self._resolve_ssl(driver_def, config):
            self.SIMULATOR_INFO["tls"] = True

    def _build_generated_handlers(self) -> None:
        """Every handler the definition itself implies: how each variable
        goes back on the wire (reply_rules.py), the commands, then the
        settings (after the commands, so a command and a setting whose wire
        forms collide resolve to the command, the thing the driver named),
        the flat reads (query_handlers.py), a Generic device's own handlers,
        and an OSC device's addresses."""
        self._command_handlers: list[CommandHandler] = []
        self._query_handlers: list[QueryHandler] = []
        self._state_responses: dict[str, StateResponse] = flat_replies(
            self._driver_def.get("responses", []), self._var_def_for_key
        )
        self._build_command_handlers()
        self._build_settings_handlers()
        self._query_handlers = flat_handlers(self._driver_def, self._query_wire_line)
        self._build_inline_handlers()
        self._build_osc_handlers()

    def _build_osc_handlers(self) -> None:
        """An OSC device's address rules and where each variable's change is
        pushed (the client is the datagram server's _last_client_addr), with
        the flags that keep a reply from echoing as a push."""
        self._osc_address_handlers: list[tuple[str, list[dict]]] = []
        self._osc_script_handlers: list[OSCScriptHandler] = []
        self._handling_command = False
        self._handling_osc = False
        self._osc_state_to_address: dict[str, PushTarget] = {}
        if self._is_osc:
            self._build_osc_address_handlers()
            self._osc_state_to_address = push_targets(self._osc_address_handlers)

    def _apply_section(self, sim_section: dict) -> None:
        """The simulator: section over the generated device, then the error
        mode a driver's sign-in offers, which a section that defines the
        same mode keeps."""
        if sim_section:
            self._merge_simulator_section(sim_section)
        if offers_sign_in_required(self._driver_def):
            self._error_modes.setdefault(
                SIGN_IN_REQUIRED_MODE, dict(SIGN_IN_REQUIRED_DEFINITION)
            )

    def _build_children(self) -> None:
        """The child roster and its reads, after the section, whose dotted
        state seeds name children too.

        Per-child state lives in the flat state dict under dotted keys
        "<type>.<id>.<prop>" (the simulator's mirror of the runtime's
        device.<id>.<type>.<id>.<prop>). The roster unions three sources:
        the declared instances: blocks resolved against this instance's
        config, the dotted keys the section seeds, and the project's
        children, which the manager delivers through set_child_entities()
        after construction.
        """
        self._child_roster: dict[str, list[str]] = {}
        self._child_state_responses: dict[tuple[str, str], ChildStateResponse] = (
            child_replies(self._driver_def.get("responses", []), self._child_types(), self._child_vars)
        )
        self._child_list_answers: dict[tuple[str, str], ChildListAnswer] = list_answers(
            self._driver_def.get("responses") or [], self._child_types(), self._child_vars
        )
        self._child_query_handlers: list[ChildQueryHandler | ListQueryHandler] = []
        self._seed_declared_children()
        self._adopt_dotted_state_children()
        self._build_child_query_handlers()

    def _set_up_pushing(self, sim_section: dict) -> None:
        """Whether the device pushes its changes to a connected driver, as a
        real one does (the section's push_state), then where its
        notifications go (push_channels.py). A Generic device pushes by
        default, so driving a variable from the Simulator UI sends its
        string to the panel; an HTTP one holds no connection to push on,
        and its driver sees a change at its next poll."""
        self._push_state = sim_section.get("push_state", False) or (
            self._inline_protocol and not self._is_http
        )
        self._set_up_push()

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
        """Play the device's half of the driver's ``auth:`` sign-in
        (telnet_sign_in.py). The client is admitted at once when the driver
        signs in to nothing, unless the ``sign_in_required`` error mode is on.
        """
        auth_def = telnet_login(self._driver_def)
        if auth_def is None:
            return True
        if driver_skips(auth_def, self.config) and not self.has_error_behavior(
            SIGN_IN_REQUIRED_MODE
        ):
            return True
        sign_in = TelnetSignIn(auth_def)
        return await sign_in.run(
            reader, partial(self._emit_sign_in, writer, client_id), self.name, client_id
        )

    async def _emit_sign_in(
        self, writer: asyncio.StreamWriter, client_id: str, data: bytes,
    ) -> None:
        """One piece of the sign-in, unless the client is silenced."""
        if self._client_silenced(client_id):
            return
        writer.write(data)
        await writer.drain()
        self.log_protocol("out", data, client_id)

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
        is answered from the driver's own definition: the console's own
        addresses first (osc_addresses.system_reply), then a script handler
        from the simulator: section, then the first response rule whose
        address matches. A message WITH args sets the variables its mappings
        name and is echoed back (the standard OSC feedback pattern); one
        WITHOUT reports them.
        """
        system = system_reply(address, args, self._state)
        if system is not None:
            return system
        script = next(
            (h for h in self._osc_script_handlers
             if fnmatch.fnmatch(address, h.address_pattern)),
            None,
        )
        if script is not None:
            return self._execute_osc_script_handler(script, address, args)
        for addr_pattern, mappings in self._osc_address_handlers:
            if fnmatch.fnmatch(address, addr_pattern):
                return self._osc_rule_answer(address, args, mappings)
        logger.debug("%s: unmatched OSC address: %s", self.device_id, address)
        return None

    def _osc_rule_answer(
        self, address: str, args: list[tuple[str, Any]], mappings: list[dict]
    ) -> list[tuple[str, list[tuple[str, Any]]]]:
        """A message to a rule's address: with args it sets and is echoed
        back, without it is answered with the variables' values."""
        if args:
            self._osc_set(mappings, args)
            return [(address, args)]
        return [(address, self._osc_query(mappings))]

    def _osc_set(self, mappings: list[dict], args: list[tuple[str, Any]]) -> None:
        """Each mapping's argument into its variable: through the mapping's
        map, else the variable's conversion, then its type."""
        for mapping in mappings:
            arg_idx = mapping.get("arg", 0)
            state_key = mapping.get("state")
            if state_key and arg_idx < len(args):
                _, value = args[arg_idx]
                value_map = mapping.get("map")
                mapped = value_map.get(str(value)) if value_map else None
                if mapped is None:
                    mapped = self._from_wire(state_key, value)
                self.set_state(state_key, self._coerce_value(state_key, mapped))

    def _osc_query(self, mappings: list[dict]) -> list[tuple[str, Any]]:
        """Each variable the mappings name, as an argument in its mapping's
        tag."""
        answer: list[tuple[str, Any]] = []
        for mapping in mappings:
            state_key = mapping.get("state")
            value_type = mapping.get("type", "float")
            if state_key:
                tag = type_tag(value_type)
                answer.append((tag, self._osc_answer(state_key, tag)))
        return answer

    def _osc_answer(self, state_key: str, tag: str) -> Any:
        """A variable's value as a query answers it: the device's token from
        the variable's push target (e.g. mute false -> 1) cast to ``tag``,
        else the device's number, uncast."""
        value = self._state.get(state_key, 0)
        target = self._osc_state_to_address.get(state_key)
        raw = target.device_token(value) if target else None
        if raw is not None:
            return tagged(raw, tag)
        return self._to_wire(state_key, value)

    def _execute_osc_script_handler(
        self, handler: OSCScriptHandler, address: str, args: list[tuple[str, Any]]
    ) -> list[tuple[str, list[tuple[str, Any]]]] | None:
        """Execute a script handler for OSC messages."""
        response_data: list[tuple[str, list[tuple[str, Any]]]] = []

        def respond(resp_address: str, resp_args: Any = None) -> None:
            response_data.append((resp_address, normalize_osc_args(resp_args)))

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
        """Answer one line: the first handler in ``_HANDLER_CHAIN`` order
        whose pattern matches its front, then whatever that handler left of
        the line; a registration nothing answers with an empty
        acknowledgement; else the line read as several commands run
        together."""
        text = data.decode("utf-8", errors="replace").strip()
        if not text:
            return None

        # Dial-back subscriber bookkeeping (push: {type: tcp_listener}) —
        # observe-only; the command still dispatches normally below so the
        # driver YAML can shape the response with an explicit handler.
        was_registration = self._watch_push_registration(text)

        found = self._first_handler(text)
        if found is not None:
            family, handler, m = found
            reply = family.answer(self, handler, text, m)
            return self._with_remainder(text, m, reply) if family.takes_the_rest else reply

        # A registration command with no handler of its own still succeeds —
        # the empty body means "204-style ack" (real dial-back devices answer
        # registrations with No Content), so drivers don't need boilerplate
        # simulator handlers for the subscribe/unsubscribe pair.
        if was_registration:
            return b""
        return self._dispatch_run_together(text)

    def _first_handler(self, text: str) -> tuple[_HandlerFamily, Any, re.Match] | None:
        """The first handler whose pattern matches the front of the line,
        with its family and the match."""
        for family in _HANDLER_CHAIN:
            for handler in getattr(self, family.handlers):
                m = family.pattern(handler).match(text)
                if m:
                    return family, handler, m
        return None

    def _dispatch_run_together(self, text: str) -> bytes | None:
        """A line nothing matched whole, read as commands run together.

        Unterminated protocols are self-delimiting, and with no terminator on
        the wire back-to-back writes can coalesce into one line ("N?*R4")
        whose first command's end-anchored pattern then never matches. If the
        entire line decomposes into known commands, dispatch each piece;
        plain garbage does not decompose and stays unrecognized.
        """
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
        """Whether a handler the planner counts matches all of ``text``."""
        return any(
            family.pattern(handler).fullmatch(text)
            for family in _HANDLER_CHAIN if family.planned
            for handler in getattr(self, family.handlers)
        )

    def _execute_command_handler(self, handler: CommandHandler, m: re.Match) -> bytes | None:
        """Run a matched command: the state machines it drives, the child it
        names, each state change it makes, then its reply."""
        delimiter = self._get_delimiter()

        # Drive any state machines with this command name (the documented
        # trigger contract). A machine that rejects the command in its current
        # state (e.g. during cooldown) suppresses the response entirely.
        if self._state_machines and self._fire_state_machine_triggers(handler.name):
            return None

        child_id: str | None = None
        if handler.child_type:
            child_id = self._addressed_child(handler, m)
            if child_id is None:
                return None

        for state_key, source in handler.state_changes.items():
            target_key = handler.target_key(state_key, child_id)
            self.set_state(target_key, self._state_change_value(handler, source, m, target_key))

        text = self._command_reply(handler, child_id)
        return None if text is None else (text + delimiter).encode()

    def _addressed_child(self, handler: CommandHandler, m: re.Match) -> str | None:
        """The rostered child a child-addressed command names: its captured
        id, through the param's map, unpadded. None when the capture is
        missing or names a child the roster doesn't know (the device analog
        of addressing a port that isn't there), and the command goes
        unanswered."""
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
        return child_id

    def _state_change_value(
        self, handler: CommandHandler, source: Any, m: re.Match, target_key: str
    ) -> Any:
        """The value one state change stores: a declared literal from a
        command's sets: (wrapped so an integer literal can't read as a
        capture-group index), a capture group read back to a value, or a
        bare literal (heuristic booleans), stored as it is."""
        if isinstance(source, tuple) and source[0] == "literal":
            return self._coerce_value(target_key, source[1])
        if isinstance(source, int) and not isinstance(source, bool):
            raw = m.group(source)
            value = handler.read_capture(source, raw, self._var_def_for_key(target_key))
            return self._coerce_value(target_key, value)
        return source

    def _command_reply(self, handler: CommandHandler, child_id: str | None) -> str | None:
        """The reply to a command, without the delimiter: the addressed
        child's report of its variable (never the flat one in its place),
        else the flat variable's."""
        var = handler.response_var
        if not var:
            return None
        if handler.response_is_child and child_id is not None:
            return self._child_reply_text(handler.child_type, child_id, var)
        return self._flat_reply_text(var)

    def _flat_reply_text(self, var: str) -> str | None:
        """How the device reports a flat variable's current value; None when
        no response rule describes it."""
        resp = self._state_responses.get(var)
        return None if resp is None else resp.format(self._state.get(var))

    def _child_reply_text(self, ctype: str, child_id: str, var: str) -> str | None:
        """How the device reports one child's variable; None when no rule
        describes it or nothing derivable represents the value."""
        resp = self._child_state_responses.get((ctype, var))
        if resp is None:
            return None
        return resp.format(child_id, self._state.get(f"{ctype}.{child_id}.{var}"))

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
        text = self._flat_reply_text(handler.response_var)
        return None if text is None else (text + delimiter).encode()

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
        # Param map: local id -> wire token; the sim sees the wire token and
        # needs the local id back.
        return _ChildAddress(param_name, ctype, reversed_ids(cpdef.get("map")), child_vars)

    def _build_settings_handlers(self) -> None:
        """Answer a ``device_settings`` write the way a command is answered
        (setting_handlers.py), after the commands' handlers."""
        before = len(self._command_handlers)
        self._command_handlers.extend(setting_handlers(
            self._driver_def, self._instance_config, self._var_def_for_key
        ))
        built = len(self._command_handlers) - before
        if built:
            logger.debug(
                "Auto-sim %s: %d device_settings write handlers",
                self.driver_id, built,
            )

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
        """Extend the base payload with whether the device pushes and its
        child roster (ui_info.children_info), so the UI can group the dotted
        per-child state keys into a Children section.
        Values are NOT duplicated here — the UI reads them live from the
        state dict, which the per-key WS updates keep current."""
        info = super().to_info_dict()
        # The Simulator UI card reads this as "Pushes state changes". A device
        # that sends `notifications:` pushes whatever `push_state` says.
        info["push_state"] = bool(info.get("push_state")) or self._sends_notifications()
        if self._child_roster:
            info["children"] = children_info(
                self._child_roster, self._child_types(), self._child_vars,
                self._child_entities,
            )
        return info

    def _execute_list_query_handler(self, handler: ListQueryHandler) -> bytes | None:
        """Answer a list read with every child it covers (its handler's
        ``covers``), from each child's own state; a child the roster lacks
        reads as its variable's default, the way a device lists outputs
        nobody configured."""
        answer = self._child_list_answers.get((handler.child_type, handler.response_var))
        if answer is None:
            return None
        ctype, var = handler.child_type, handler.response_var
        ids, start = handler.covers(answer, self._child_roster.get(ctype, []))
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
        """The list, page and per-child reads for the current roster
        (query_handlers.py), rebuilt when project children arrive."""
        self._child_query_handlers = []
        self._child_query_handlers.extend(child_handlers(
            every_query_entry(self._driver_def), self._child_roster,
            self._child_list_answers, self._query_wire_line,
        ))

    def _execute_child_query_handler(
        self, handler: ChildQueryHandler | ListQueryHandler
    ) -> bytes | None:
        """Answer a per-child query from the addressed child's own state."""
        if isinstance(handler, ListQueryHandler):
            return self._execute_list_query_handler(handler)
        text = self._child_reply_text(
            handler.child_type, handler.child_id, handler.response_var
        )
        if text is None:
            return None
        return (text + self._get_delimiter()).encode()

    def _build_inline_handlers(self) -> None:
        """A Generic device's own response patterns, and an HTTP one's JSON
        status poll (inline_device.py); all three tables empty for a file
        driver."""
        self._inline_response_handlers: list[tuple[re.Pattern, dict]] = []
        self._inline_json_polls: list[re.Pattern] = []
        self._inline_json_fields: list[dict] = []
        if not self._inline_protocol:
            return
        self._inline_response_handlers = response_handlers(
            self._driver_def.get("responses", []), self.driver_id
        )
        if self._is_http:
            self._inline_json_fields = json_fields(self._driver_def)
            if self._inline_json_fields:
                self._inline_json_polls = json_polls(self._driver_def)

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

    def _inline_json_reply(self) -> bytes:
        """The JSON status poll's answer (inline_device.json_body)."""
        return json_body(self._inline_json_fields, self.get_state)

    def _build_osc_address_handlers(self) -> None:
        """The address rules a message is matched against (osc_addresses)."""
        self._osc_address_handlers = address_rules(self._driver_def.get("responses", []))
        logger.info(
            "Auto-gen OSC simulator for %s: %d address handlers",
            self.driver_id, len(self._osc_address_handlers),
        )

    # ── Merge explicit simulator: section ──

    def _merge_simulator_section(self, sim: dict) -> None:
        """Merge explicit simulator: enhancements onto auto-generated behavior:
        the device's settings and state machines (base.py's reader, the one a
        Python simulator's SIMULATOR_INFO goes through), its controls, its
        hand-written handlers, then its own wording for state changes."""
        self._read_device_settings(sim)
        # Declared controls replace the ones read from the state variables.
        controls = sim.get("controls")
        if controls:
            self.SIMULATOR_INFO["controls"] = controls
        self._read_section_handlers(sim.get("command_handlers", []))
        # The device's own wording for state changes (push_channels.py).
        self._read_notifications(sim.get("notifications", {}))

    def _skip_malformed_machine(self, name: str) -> None:
        """A section's malformed machine, said in this module's log (which the
        validator mutes while it builds a simulator) and naming the driver."""
        logger.warning(
            "%s: skipping malformed state_machine '%s' "
            "(needs states, initial, transitions)", self.driver_id, name,
        )

    def _read_section_handlers(self, handler_defs: list) -> None:
        """The section's hand-written command handlers, in their order: an
        ``address`` with a ``handler`` is an OSC script; otherwise ``receive``
        (or ``match``) is the line's pattern, with a ``handler`` an inline
        Python script, without one a template (``respond:`` / ``set_state:``).
        One whose pattern or code will not compile is skipped with a
        warning."""
        self._explicit_handlers: list[ExplicitHandler] = []
        self._script_handlers: list[ScriptHandler] = []
        for handler_def in handler_defs:
            if "address" in handler_def and "handler" in handler_def:
                self._read_osc_script(handler_def)
                continue
            pattern_str = handler_def.get("receive") or handler_def.get("match", "")
            if pattern_str:
                self._read_line_handler(handler_def, pattern_str)

    def _read_osc_script(self, handler_def: dict) -> None:
        code = self._handler_code(
            handler_def["handler"], f"OSC handler for {handler_def['address']}"
        )
        if code is not None:
            self._osc_script_handlers.append(OSCScriptHandler(
                address_pattern=handler_def["address"], code=code,
            ))

    def _read_line_handler(self, handler_def: dict, pattern_str: str) -> None:
        try:
            pattern = re.compile(f"^{pattern_str}$")
        except re.error:
            logger.warning("Invalid regex in simulator command_handler: %s", pattern_str)
            return
        if "handler" not in handler_def:
            self._explicit_handlers.append(ExplicitHandler(
                pattern=pattern,
                respond=handler_def.get("respond"),
                set_state=handler_def.get("set_state", {}),
            ))
            return
        code = self._handler_code(handler_def["handler"], f"command handler {pattern_str!r}")
        if code is not None:
            self._script_handlers.append(ScriptHandler(pattern=pattern, code=code))

    def _handler_code(self, source: Any, what: str) -> Any:
        """A handler's Python compiled, or None (with a warning naming
        ``what``) when it has a syntax error."""
        try:
            return compile(source, f"<sim:{self.driver_id}>", "exec")
        except SyntaxError as e:
            logger.warning(
                "%s: skipping %s — handler code has a syntax error: %s",
                self.driver_id, what, e,
            )
            return None

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
        """Send an OSC message to the connected driver for a state change:
        the device's token from the reverse map (nothing when the map has none
        for the value), else the device's number, cast to the target's tag."""
        from openavc.transport.osc_codec import osc_encode_message

        target = self._osc_state_to_address[key]
        if target.reverse_map:
            raw = target.device_token(value)
            if raw is None:
                return
            osc_value = tagged(raw, target.tag)
        else:
            osc_value = tagged(self._to_wire(key, value), target.tag)
        self.push_datagram(osc_encode_message(target.address, [(target.tag, osc_value)]))

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
    def _build_info(driver_def: dict) -> dict:
        """Build SIMULATOR_INFO from driver definition."""
        state_vars = driver_def.get("state_variables", {})
        initial_state = {}
        controls = []
        for key, var_def in state_vars.items():
            var_type = var_def.get("type", "string")
            label = var_def.get("label", key.replace("_", " ").title())
            # The seed value is the shared rule; which control the UI gets is
            # ui_info's.
            initial_state[key] = state_var_default(var_def)
            controls.append(variable_control(key, var_def, var_type, label))

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


# ── The order a line is offered to the handlers ──


class _HandlerFamily(NamedTuple):
    """One family of handlers in the order a line is offered to them.

    ``handlers`` names the simulator attribute holding them, read when the
    line arrives (a table is rebuilt when project children arrive, and the
    simulator: section's tables are a class default until it is read).
    ``answer(sim, handler, text, match)`` is the reply. A family that
    ``takes_the_rest`` dispatches what its pattern left of the line as the
    next command; one the coalesced-line planner counts is ``planned``.
    """

    handlers: str
    pattern: Callable[[Any], re.Pattern]
    answer: Callable[[YAMLAutoSimulator, Any, str, re.Match], bytes | None]
    takes_the_rest: bool = True
    planned: bool = True


def _own_pattern(handler: Any) -> re.Pattern:
    return handler.pattern


def _pair_pattern(pair: tuple[re.Pattern, dict]) -> re.Pattern:
    return pair[0]


def _bare_pattern(pattern: re.Pattern) -> re.Pattern:
    return pattern


#: First match wins. The simulator: section's hand-written handlers come
#: first, then an inline device's JSON status poll (its whole reply, so the
#: rest of the line is not read and the planner does not count it) and its
#: response patterns, then the generated reads and commands. Child-addressed
#: poll queries come ahead of the generated commands on purpose: a protocol
#: whose bare per-output view collides with a single-output command form
#: (SIS "1Z") resolves by roster — children modeled means the matrix dialect
#: wins, exactly like the real hardware.
_HANDLER_CHAIN: tuple[_HandlerFamily, ...] = (
    _HandlerFamily("_explicit_handlers", _own_pattern,
                   lambda sim, h, text, m: sim._execute_explicit_handler(h, m)),
    _HandlerFamily("_script_handlers", _own_pattern,
                   lambda sim, h, text, m: sim._execute_script_handler(h, m)),
    _HandlerFamily("_inline_json_polls", _bare_pattern,
                   lambda sim, h, text, m: sim._inline_json_reply(),
                   takes_the_rest=False, planned=False),
    _HandlerFamily("_inline_response_handlers", _pair_pattern,
                   lambda sim, h, text, m: sim._apply_inline_response(text, m, h[1])),
    _HandlerFamily("_child_query_handlers", _own_pattern,
                   lambda sim, h, text, m: sim._execute_child_query_handler(h)),
    _HandlerFamily("_command_handlers", _own_pattern,
                   lambda sim, h, text, m: sim._execute_command_handler(h, m)),
    _HandlerFamily("_query_handlers", _own_pattern,
                   lambda sim, h, text, m: sim._execute_query_handler(h)),
)


# ── Data classes ──

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


# ── Utility functions ──

class OSCScriptHandler:
    """Script handler for OSC simulator: command_handlers with address pattern."""
    def __init__(self, address_pattern: str, code: Any):
        self.address_pattern = address_pattern
        self.code = code
