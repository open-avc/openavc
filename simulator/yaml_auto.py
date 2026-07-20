"""
YAMLAutoSimulator — auto-generates a working simulator from .avcdriver files.

Reverses the driver's command/response definitions to create a simulator
that handles incoming commands, updates state, and generates responses.

Works at two levels:
  Level 0: Pure auto-gen from commands + responses + state_variables
  Level 1: Enhanced with explicit simulator: section (merged on top)

Supports TCP (default), UDP, OSC, and HTTP transports. UDP/OSC drivers
get a datagram server instead of a TCP stream server. HTTP drivers get
an aiohttp web server; incoming requests are synthesized to a text command
line ("METHOD /path?query") and dispatched through the same handler
machinery as TCP.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import math
import re
import socket
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import yaml

from server.drivers.compiled_protocol import (
    apply_send_frame,
    build_send_frame,
    decode_delimiter,
    safe_substitute,
    send_param_specs,
    send_regex,
    spec_int_base,
    split_send_frames,
)
from server.drivers.inline_protocol import (
    _derive_state_vars_from_responses,
    _normalize_config_commands,
    _normalize_config_responses,
    _normalize_config_state_vars,
)
from server.transport.binary_helpers import encode_escape_sequences
from simulator.tcp_simulator import TCPSimulator

logger = logging.getLogger(__name__)


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
    norm_cmds = _normalize_config_commands(cfg.get("commands"))  # no line-ending
    canonical = _normalize_config_responses(cfg.get("responses"))
    norm_vars = _normalize_config_state_vars(cfg.get("state_variables"))

    if not (norm_cmds or canonical or norm_vars):
        return driver_def, False

    merged = dict(driver_def)
    merged["commands"] = {**(driver_def.get("commands") or {}), **norm_cmds}
    sim_responses = [
        {"match": r["match"], "set": _mappings_to_set(r.get("mappings", []))}
        for r in canonical
    ]
    merged["responses"] = list(driver_def.get("responses") or []) + sim_responses
    derived = _derive_state_vars_from_responses(canonical)
    merged["state_variables"] = {
        **(driver_def.get("state_variables") or {}), **derived, **norm_vars,
    }
    if isinstance(cfg.get("delimiter"), str):
        merged["delimiter"] = cfg["delimiter"]
    return merged, True


class YAMLAutoSimulator(TCPSimulator):
    """Auto-generated simulator from a .avcdriver definition.

    Inherits from TCPSimulator for TCP/serial drivers. For UDP drivers,
    start() and stop() are overridden to use a UDP datagram server instead.
    The handle_command() logic is identical for both transports.
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

        # UDP/OSC/HTTP transport support — overrides TCP server with the
        # appropriate alternate server in start()/stop().
        transport = driver_def.get("transport", "tcp")
        self._is_udp = transport == "udp"
        self._is_osc = transport == "osc"
        self._is_http = transport == "http"
        self._udp_transport: asyncio.DatagramTransport | None = None
        self._http_runner: Any = None  # aiohttp.web.AppRunner when HTTP
        self._http_site: Any = None    # aiohttp.web.TCPSite when HTTP

        # Build handlers from driver definition
        self._command_handlers: list[CommandHandler] = []
        self._query_handlers: list[QueryHandler] = []
        self._state_responses: dict[str, StateResponse] = {}

        self._build_state_responses()
        self._build_command_handlers()
        self._build_query_handlers()

        # Inline (Generic) devices: also match an incoming command against the
        # response patterns and apply their state changes. Many such devices use
        # the same string to set and to report a value (e.g. "PWR ON"), so the
        # string the panel sends is also the device's status string.
        self._inline_response_handlers: list[tuple[re.Pattern, dict]] = []
        if self._inline_protocol:
            self._build_inline_response_handlers()

        # OSC-specific: build address-based handlers from responses
        self._osc_address_handlers: list[tuple[str, list[dict]]] = []
        self._osc_script_handlers: list[OSCScriptHandler] = []
        # Push notification tracking: prevent echo during command processing
        self._handling_command = False
        self._handling_osc = False
        # UDP push: track last client for unsolicited responses
        self._last_udp_client: tuple[str, int] | None = None
        # OSC push: track client and build reverse state→address map
        self._last_osc_client: tuple[str, int] | None = None
        self._osc_state_to_address: dict[str, tuple[str, int, str, dict[str, str] | None]] = {}
        if self._is_osc:
            self._build_osc_address_handlers()
            self._build_osc_state_reverse_map()

        # Merge explicit simulator: section if present
        sim_section = driver_def.get("simulator", {})
        if sim_section:
            self._merge_simulator_section(sim_section)

        # push_state: whether this simulator pushes state changes to connected
        # drivers (matching real device behavior). Set in simulator: section.
        # Inline (Generic) devices push by default so driving a variable from
        # the simulator UI emits the matching string to the panel.
        self._push_state = sim_section.get("push_state", False) or self._inline_protocol

        # Multicast push emission: when the driver declares
        # `push: {type: multicast}`, notification templates (the simulator
        # `notifications:` map) are emitted to the resolved group:port as UDP
        # multicast — and NOT to connected control clients, matching real
        # devices whose notice channel is multicast-only. Sender socket is
        # opened in start() / closed in stop().
        self._push_multicast = self._resolve_push_multicast(driver_def, config)
        self._mcast_sock: socket.socket | None = None

        # SSE push emission: when an HTTP driver declares `push: {type: sse}`,
        # a GET on one of its declared paths with Accept: text/event-stream is
        # held open and the `notifications:` templates are delivered there as
        # SSE events (data: <msg>\n\n) — the HTTP analogue of the multicast
        # channel above.
        self._push_sse_paths = self._resolve_push_sse(driver_def, config)
        self._sse_clients: set[asyncio.Queue] = set()

        # Dial-out push emission: when the driver declares
        # `push: {type: tcp_listener}`, the simulator watches for the
        # driver's register/unregister commands (recognized by their
        # {listener_port} templates), tracks the registered subscribers, and
        # on each notification dials out a TCP connection per subscriber and
        # sends the `notifications:` template wrapped in the declared frame
        # container — matching real devices that push one framed notification
        # per outbound connection.
        self._push_tcp = self._resolve_push_tcp_listener(driver_def)
        # (host, port) -> consecutive delivery failures. Real devices prune
        # subscribers they can't reach; the simulator drops one after 3
        # straight failures so a departed platform doesn't get dialed forever.
        self._push_tcp_subscribers: dict[tuple[str, int], int] = {}
        self._push_tcp_tasks: set[asyncio.Task] = set()
        # Peer address of the request currently being dispatched — the
        # dial-back target host for a registration command (HTTP fills it
        # from the request; plain-TCP sims fall back to loopback).
        self._last_peer_ip = "127.0.0.1"
        # HTTP-callback push emission: when the driver declares
        # `push: {type: http_listener}`, script handlers record the callback
        # URL the (simulated) controller registers — via register_callback()
        # in the handler namespace — and the `notifications:` templates are
        # POSTed to every registered URL, exactly like a real device's
        # webhook delivery.
        push = driver_def.get("push")
        self._push_http = (
            isinstance(push, dict) and push.get("type") == "http_listener"
        )
        self._http_push_callbacks: list[str] = []

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
        in this device's config, the driver never authenticates — if the
        simulator prompted anyway it would eat the first two real commands
        as credentials.
        """
        auth_def = self._driver_def.get("auth")
        if not isinstance(auth_def, dict):
            return True
        if auth_def.get("type", "telnet_login") != "telnet_login":
            return True

        username_field = auth_def.get("username_field", "username")
        configured_user = str(self.config.get(username_field, "") or "")
        if auth_def.get("skip_if_empty", True) and not configured_user:
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

    # ── UDP / OSC / HTTP transport overrides ──

    @staticmethod
    def _resolve_push_multicast(
        driver_def: dict, config: dict | None
    ) -> tuple[str, int] | None:
        """Resolve the driver's `push: {type: multicast}` block to a concrete
        (group, port) emission target, or None. `{config_field}` templates
        resolve against default_config overlaid with the instance config —
        the same fields the real driver resolves them from."""
        push = driver_def.get("push")
        if not isinstance(push, dict) or push.get("type") != "multicast":
            return None
        merged = dict(driver_def.get("default_config") or {})
        merged.update(config or {})

        def resolve(value: Any) -> Any:
            if isinstance(value, str) and "{" in value:
                return safe_substitute(value, merged)
            return value

        group = str(resolve(push.get("group", "")) or "").strip()
        try:
            port = int(str(resolve(push.get("port"))).strip())
        except (TypeError, ValueError):
            port = 0
        try:
            is_mcast = ipaddress.IPv4Address(group).is_multicast
        except (ipaddress.AddressValueError, ValueError):
            is_mcast = False
        if not is_mcast or not (0 < port < 65536):
            logger.warning(
                "push block did not resolve to a multicast group/port "
                "(group=%r port=%r) — simulator will not emit notifications",
                group, push.get("port"),
            )
            return None
        return (group, port)

    @staticmethod
    def _resolve_push_sse(driver_def: dict, config: dict | None) -> list[str]:
        """Resolve the driver's `push: {type: sse}` block to the list of
        event-stream paths the simulator should serve. `{config_field}`
        templates resolve against default_config overlaid with the instance
        config — the same fields the real driver resolves them from."""
        push = driver_def.get("push")
        if not isinstance(push, dict) or push.get("type") != "sse":
            return []
        merged = dict(driver_def.get("default_config") or {})
        merged.update(config or {})

        def resolve(value: Any) -> str:
            if isinstance(value, str) and "{" in value:
                return safe_substitute(value, merged)
            return str(value)

        raw = push.get("path")
        raw_paths = [raw] if isinstance(raw, str) else list(raw or [])
        paths = [resolve(p).strip() for p in raw_paths]
        good = [p for p in paths if p.startswith("/")]
        if not good:
            logger.warning(
                "push block did not resolve to event-stream path(s) "
                "(path=%r) — simulator will not emit notifications",
                push.get("path"),
            )
        return good

    def _resolve_push_tcp_listener(self, driver_def: dict) -> dict | None:
        """Resolve a `push: {type: tcp_listener}` block for dial-out emission.

        Returns ``{"frame": <frame_parser cfg or None>, "register": <compiled
        regex or None>, "unregister": ...}``. The register/unregister matchers
        are built from the named commands' own path/send templates, with the
        ``{listener_port}`` token becoming a port capture group — so the
        simulator recognizes the registration exactly as the driver sends it.
        """
        push = driver_def.get("push")
        if not isinstance(push, dict) or push.get("type") != "tcp_listener":
            return None
        commands = driver_def.get("commands") or {}

        def build_matcher(cmd_name: Any) -> re.Pattern | None:
            cmd = commands.get(cmd_name) if isinstance(cmd_name, str) else None
            if not isinstance(cmd, dict):
                return None
            if "path" in cmd or "method" in cmd:
                template = (
                    f"{cmd.get('method', 'GET').upper()} {cmd.get('path', '/')}"
                )
            else:
                template = str(cmd.get("send", "") or cmd.get("string", ""))
            if not template:
                return None
            pattern = re.escape(template)
            pattern = pattern.replace(
                re.escape("{listener_port}"), r"(?P<lport>\d+)"
            )
            # Other {tokens} in the template match any non-separator run.
            pattern = re.sub(r"\\\{\w+\\\}", r"[^&\\s|]+", pattern)
            # Allow a trailing "|body" (HTTP POST synthesis) or whitespace.
            return re.compile(r"^" + pattern + r"\s*(?:\|.*)?$")

        return {
            "frame": push.get("frame_parser")
            if isinstance(push.get("frame_parser"), dict)
            else None,
            "register": build_matcher(push.get("register")),
            "unregister": build_matcher(push.get("unregister")),
        }

    def _watch_push_registration(self, text: str) -> bool:
        """Track dial-back subscribers from register/unregister commands.

        Observe-only bookkeeping run before normal dispatch; returns True
        when ``text`` was a registration command (callers use that to give
        an empty-success default response if no handler answers it).
        """
        if not self._push_tcp:
            return False
        for key, add in (("register", True), ("unregister", False)):
            matcher = self._push_tcp.get(key)
            m = matcher.match(text) if matcher else None
            if not m:
                continue
            try:
                port = int(m.group("lport"))
            except (IndexError, ValueError):
                # Template carried no {listener_port}; nothing to track.
                return True
            target = (self._last_peer_ip or "127.0.0.1", port)
            if add:
                self._push_tcp_subscribers.setdefault(target, 0)
                self._push_tcp_subscribers[target] = 0
                self.log_protocol(
                    "in", f"(push subscriber registered: {target[0]}:{port})"
                )
            else:
                self._push_tcp_subscribers.pop(target, None)
                self.log_protocol(
                    "in", f"(push subscriber removed: {target[0]}:{port})"
                )
            return True
        return False

    def _wrap_push_tcp_frame(self, payload: bytes) -> bytes:
        """Wrap a notification payload in the declared frame container.

        Inverse of the platform's StructFrameParser: zeroed reserve regions
        around a length field that counts ``len(payload) - length_adjust``.
        Without a struct_frame declaration the payload goes out raw.
        """
        frame = (self._push_tcp or {}).get("frame")
        if not frame or frame.get("type") != "struct_frame":
            return payload
        length_size = int(frame.get("length_size", 2))
        endian = "little" if frame.get("length_endian") == "little" else "big"
        length_value = len(payload) - int(frame.get("length_adjust", 0))
        return (
            bytes(int(frame.get("header_reserve", 0)))
            + max(0, length_value).to_bytes(length_size, endian)
            + bytes(int(frame.get("mid_reserve", 0)))
            + payload
            + bytes(int(frame.get("trailer_reserve", 0)))
        )

    def _emit_push_tcp(self, payload: bytes) -> None:
        """Dial each registered subscriber and push one framed notification."""
        framed = self._wrap_push_tcp_frame(payload)
        for target in list(self._push_tcp_subscribers):
            task = asyncio.ensure_future(self._dial_push_tcp(target, framed))
            self._push_tcp_tasks.add(task)
            task.add_done_callback(self._push_tcp_tasks.discard)

    async def _dial_push_tcp(self, target: tuple[str, int], framed: bytes) -> None:
        """One outbound notification connection: connect, send, close.

        Mirrors real dial-back devices, which prune subscribers they cannot
        reach — three consecutive failed deliveries drop the registration.
        """
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(target[0], target[1]), timeout=2.0
            )
            writer.write(framed)
            await writer.drain()
            writer.close()
            if target in self._push_tcp_subscribers:
                self._push_tcp_subscribers[target] = 0
            self.log_protocol("out", framed)
        except (OSError, asyncio.TimeoutError):
            failures = self._push_tcp_subscribers.get(target)
            if failures is None:
                return
            if failures + 1 >= 3:
                del self._push_tcp_subscribers[target]
                logger.info(
                    "%s: push subscriber %s:%d unreachable 3 times — removed",
                    self.name, target[0], target[1],
                )
            else:
                self._push_tcp_subscribers[target] = failures + 1
    # ── HTTP-callback push (push: {type: http_listener}) ──

    def register_callback(self, url: Any) -> None:
        """Record a notification callback URL (script-handler namespace).

        A handler matching the device's registration command calls this with
        the URL the controller supplied; subsequent state-change
        notifications POST there. Registering an already-known URL is a
        no-op, matching re-registration on reconnect.
        """
        url = str(url or "").strip()
        if url and url not in self._http_push_callbacks:
            self._http_push_callbacks.append(url)
            self.log_protocol("in", f"(callback registered: {url})")

    def unregister_callback(self, url: Any) -> None:
        """Drop a previously registered callback URL (script-handler
        namespace — for handlers matching the device's deregistration
        command)."""
        url = str(url or "").strip()
        if url in self._http_push_callbacks:
            self._http_push_callbacks.remove(url)
            self.log_protocol("in", f"(callback unregistered: {url})")

    async def _post_http_callback(self, url: str, msg: str) -> None:
        """Deliver one rendered notification to a registered callback URL.

        Content type follows the payload shape (XML vs JSON) purely for
        protocol-log realism — the platform's dispatch reads only the body.
        Delivery failure is a debug-level event: a real device silently
        drops feedback its subscriber stopped answering.
        """
        import aiohttp

        body = msg.encode("utf-8")
        stripped = msg.lstrip()
        if stripped.startswith("<"):
            ctype = "text/xml"
        elif stripped.startswith(("{", "[")):
            ctype = "application/json"
        else:
            ctype = "text/plain"
        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    url, data=body, headers={"Content-Type": ctype}
                ) as resp:
                    self.log_protocol(
                        "out", f"POST {url} -> {resp.status} | {msg[:200]}"
                    )
        except Exception as e:
            logger.debug(
                "%s: callback POST to %s failed: %s", self.name, url, e
            )

    def _open_multicast_sender(self) -> None:
        """Open the multicast emission socket (TTL 1, loopback enabled so a
        same-host platform listener receives the frames)."""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
            sock.setblocking(False)
            self._mcast_sock = sock
            logger.info(
                "%s multicast notifications to %s:%d",
                self.name, self._push_multicast[0], self._push_multicast[1],
            )
        except OSError as e:
            self._mcast_sock = None
            logger.warning("Could not open multicast sender: %s", e)

    async def start(self, port: int) -> None:
        """Start the simulator server (TCP, UDP, OSC, or HTTP based on driver transport)."""
        if self._push_multicast:
            self._open_multicast_sender()
        if self._is_http:
            await self._start_http(port)
            return
        if not self._is_udp and not self._is_osc:
            await super().start(port)
            return

        # UDP/OSC mode — start a datagram server instead of TCP
        self._port = port
        loop = asyncio.get_running_loop()
        self._udp_transport, _ = await loop.create_datagram_endpoint(
            lambda: _YAMLAutoUDPProtocol(self),
            local_addr=("127.0.0.1", port),
        )
        self._running = True
        proto = "OSC" if self._is_osc else "UDP"
        logger.info(
            "%s started on %s port %d (driver: %s)",
            self.name, proto, port, self.driver_id,
        )

    async def stop(self) -> None:
        """Stop the simulator server."""
        self._cancel_state_machine_timers()
        self._cancel_push_tcp_tasks()
        if self._mcast_sock is not None:
            try:
                self._mcast_sock.close()
            except OSError:
                pass
            self._mcast_sock = None
        if self._is_http:
            await self._stop_http()
            return
        if not self._is_udp and not self._is_osc:
            await super().stop()
            return

        self._running = False
        if self._udp_transport:
            self._udp_transport.close()
            self._udp_transport = None
        logger.info("%s stopped", self.name)

    # ── HTTP transport ──

    async def _start_http(self, port: int) -> None:
        """Start an aiohttp server that synthesizes incoming requests into
        text commands and dispatches them through the existing handler chain.

        The synthesized command line format is:
            "METHOD /path?query|<body>"   (body section omitted when empty)
        with the path + query URL-decoded so command_handlers regex can match
        the original (un-encoded) characters from the driver's `path:` field.
        """
        from aiohttp import web

        self._port = port
        app = web.Application()
        app.router.add_route("*", "/{path:.*}", self._handle_http_request)

        # handler_cancellation: an event-stream subscriber that disconnects
        # (driver reconnect, network drop) must release its handler — without
        # it the handler blocks on its queue until the next notification
        # write fails.
        self._http_runner = web.AppRunner(app, handler_cancellation=True)
        await self._http_runner.setup()
        self._http_site = web.TCPSite(self._http_runner, "127.0.0.1", port)
        await self._http_site.start()
        self._running = True
        logger.info(
            "%s started on HTTP port %d (driver: %s)",
            self.name, port, self.driver_id,
        )

    async def _stop_http(self) -> None:
        """Stop the HTTP server."""
        self._running = False
        # Unblock held event-stream handlers first — cleanup() waits for
        # active handlers, and an SSE subscription blocks on its queue.
        self._close_sse_clients()
        self._cancel_push_tcp_tasks()
        if self._http_runner:
            await self._http_runner.cleanup()
            self._http_runner = None
            self._http_site = None
        logger.info("%s stopped", self.name)

    def _cancel_push_tcp_tasks(self) -> None:
        """Abandon in-flight dial-out notification connections."""
        for task in list(self._push_tcp_tasks):
            task.cancel()
        self._push_tcp_tasks.clear()

    def _http_response_delay(self) -> float:
        """Resolve the HTTP response delay: ``command_response`` when the
        author set it (0 included — an explicit 0 means an instant reply),
        falling back to the ``request_response`` alias, then no delay."""
        delay = self._delays.get("command_response")
        if delay is None:
            delay = self._delays.get("request_response", 0)
        return delay

    async def _handle_http_request(self, request: Any) -> Any:
        """Convert an aiohttp request to a synthesized command line, dispatch
        it through the standard handler machinery, and wrap the response.

        Status code is 200 when a handler matched, 404 when no handler matched.
        Response body comes from whatever the handler returned via respond().
        """
        from aiohttp import web

        method = request.method
        raw_path = "/" + request.match_info.get("path", "")
        if request.query_string:
            raw_path += "?" + request.query_string
        # URL-decode so handlers can match the un-encoded form (e.g. "#" not "%23")
        decoded_path = unquote(raw_path)

        # Event-stream subscription (push: {type: sse}): a GET on a declared
        # push path with Accept: text/event-stream is held open and fed the
        # notifications: templates instead of a one-shot response.
        if (
            method == "GET"
            and self._push_sse_paths
            and decoded_path.split("?")[0] in self._push_sse_paths
            and "text/event-stream" in request.headers.get("Accept", "")
        ):
            return await self._serve_sse(request, decoded_path)

        body_text = await request.text()
        # Dial-back registrations record the requester as the push target.
        self._last_peer_ip = str(request.remote or "127.0.0.1")

        log_text = f"{method} {decoded_path}"
        if body_text:
            log_text += f" | {body_text[:200]}"
        self.log_protocol("in", log_text)

        # Network conditions and error injection (same as HTTPSimulator base)
        if self._network_layer and self._network_layer.should_drop(self.device_id):
            await asyncio.sleep(30)
            return web.Response(status=504, text="Gateway Timeout")
        if self.has_error_behavior("no_response"):
            await asyncio.sleep(30)
            return web.Response(status=504, text="Gateway Timeout")
        if self._network_layer:
            await self._network_layer.apply_latency(self.device_id)
        delay = self._http_response_delay()
        if delay > 0:
            await asyncio.sleep(delay)

        # Synthesize the command line that handlers regex against. Body is
        # appended after a "|" delimiter so handlers can reference it when
        # they need to (POST/PUT bodies).
        if body_text:
            command_line = f"{method} {decoded_path}|{body_text}"
        else:
            command_line = f"{method} {decoded_path}"

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

    async def _serve_sse(self, request: Any, path: str) -> Any:
        """Hold an event-stream subscription open and relay notifications.

        Each subscriber gets its own queue; set_state() enqueues rendered
        notification templates, and this handler writes them out as
        ``data: <msg>\\n\\n`` frames until the client disconnects or the
        simulator stops (a ``None`` sentinel unblocks the queue wait so
        stop() never hangs on an open subscription).
        """
        from aiohttp import web

        response = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
            },
        )
        await response.prepare(request)
        queue: asyncio.Queue = asyncio.Queue()
        self._sse_clients.add(queue)
        self.log_protocol("in", f"GET {path} (event-stream subscribed)")
        try:
            while self._running:
                msg = await queue.get()
                if msg is None:
                    break
                await response.write(f"data: {msg}\n\n".encode("utf-8"))
        except (ConnectionResetError, ConnectionError, asyncio.CancelledError):
            pass
        finally:
            self._sse_clients.discard(queue)
            self.log_protocol("in", f"GET {path} (event-stream closed)")
        return response

    def _close_sse_clients(self) -> None:
        """Unblock every open event-stream handler so stop() can finish."""
        for queue in list(self._sse_clients):
            queue.put_nowait(None)

    def _handle_udp_datagram(self, data: bytes, addr: tuple[str, int]) -> None:
        """Process an incoming UDP datagram and send response."""
        self.log_protocol("in", data)

        if self._network_layer and self._network_layer.should_drop(self.device_id):
            return
        if self.has_error_behavior("no_response"):
            return

        if self._is_osc:
            asyncio.ensure_future(self._handle_osc_datagram_async(data, addr))
        else:
            asyncio.ensure_future(self._handle_udp_datagram_async(data, addr))

    async def _handle_udp_datagram_async(
        self, data: bytes, addr: tuple[str, int]
    ) -> None:
        """Async handler for UDP datagram processing (supports delays)."""
        self._last_udp_client = addr

        if self._network_layer:
            await self._network_layer.apply_latency(self.device_id)

        delay = self._delays.get("command_response", 0)
        if delay > 0:
            await asyncio.sleep(delay)

        try:
            response = self.handle_command(data)
        except Exception:
            logger.exception("%s: error in handle_command", self.name)
            response = None

        if response and self.has_error_behavior("corrupt_response"):
            from simulator.tcp_simulator import _corrupt_bytes
            response = _corrupt_bytes(response)

        if response and self._udp_transport:
            self._udp_transport.sendto(response, addr)
            self.log_protocol("out", response)

    async def _handle_osc_datagram_async(
        self, data: bytes, addr: tuple[str, int]
    ) -> None:
        """Handle an incoming OSC datagram: decode, match, respond."""
        from server.transport.osc_codec import osc_decode_bundle, osc_encode_message

        self._last_osc_client = addr

        if self._network_layer:
            await self._network_layer.apply_latency(self.device_id)

        delay = self._delays.get("command_response", 0)
        if delay > 0:
            await asyncio.sleep(delay)

        try:
            messages = osc_decode_bundle(data)
        except (ValueError, Exception) as e:
            logger.warning("%s: OSC decode error: %s", self.device_id, e)
            return

        self._handling_osc = True
        try:
            for osc_address, osc_args in messages:
                try:
                    responses = self._handle_osc_message(osc_address, osc_args)
                except Exception:
                    logger.exception("%s: error handling OSC %s", self.device_id, osc_address)
                    continue
                if responses and self._udp_transport:
                    for resp_addr, resp_args in responses:
                        resp_data = osc_encode_message(resp_addr, resp_args)
                        if self.has_error_behavior("corrupt_response"):
                            from simulator.osc_simulator import _corrupt_bytes
                            resp_data = _corrupt_bytes(resp_data)
                        self._udp_transport.sendto(resp_data, addr)
                        self.log_protocol("out", resp_data)
        finally:
            self._handling_osc = False

    def _handle_osc_message(
        self, address: str, args: list[tuple[str, Any]]
    ) -> list[tuple[str, list[tuple[str, Any]]]] | None:
        """Handle a decoded OSC message using the response address mappings.

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
                        if value_map:
                            mapped = value_map.get(str(value))
                            if mapped is not None:
                                value = mapped
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
                        if reverse_info:
                            _, _, _, reverse_map = reverse_info
                            if reverse_map:
                                raw = reverse_map.get(str(value).lower())
                                if raw is not None:
                                    value = int(raw) if tag == "i" else float(raw) if tag == "f" else raw
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

        def respond(resp_address: str, resp_args: list[tuple[str, Any]] | None = None) -> None:
            response_data.append((resp_address, resp_args or []))

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
                return self._execute_explicit_handler(handler, m)

        # Try script handlers (match: + handler: with inline Python)
        for handler in self._script_handlers:
            m = handler.pattern.match(text)
            if m:
                return self._execute_script_handler(handler, m)

        # Inline (Generic) devices: match the incoming string against the
        # response patterns and apply their state changes + echo a confirmation.
        for pattern, set_dict in self._inline_response_handlers:
            m = pattern.match(text)
            if m:
                return self._apply_inline_response(text, m, set_dict)

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

        # A registration command with no handler of its own still succeeds —
        # the empty body means "204-style ack" (real dial-back devices answer
        # registrations with No Content), so drivers don't need boilerplate
        # simulator handlers for the subscribe/unsubscribe pair.
        if was_registration:
            return b""

        logger.debug("%s: unrecognized command: %r", self.device_id, text)
        return None

    def _execute_command_handler(self, handler: CommandHandler, m: re.Match) -> bytes | None:
        """Execute a command handler: update state and generate response."""
        delimiter = self._get_delimiter()

        # Drive any state machines with this command name (the documented
        # trigger contract). A machine that rejects the command in its current
        # state (e.g. during cooldown) suppresses the response entirely.
        if self._state_machines and self._fire_state_machine_triggers(handler.name):
            return None

        # Apply state changes
        for state_key, source in handler.state_changes.items():
            if isinstance(source, int) and not isinstance(source, bool):
                # Capture group index
                value: Any = m.group(source)
                base = handler.group_bases.get(source)
                if base:
                    # The wire value was formatted with a non-decimal spec —
                    # decode it back before coercion, or an integer var would
                    # end up holding the raw hex string.
                    try:
                        value = int(value, base)
                    except ValueError:
                        pass
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
        # Match the framed form the real driver puts on the wire: a driver-level
        # command_prefix is prepended to every command (the trailing
        # command_suffix/terminator is whitespace, already stripped from the
        # incoming line in _dispatch_command). A raw: true command is unframed.
        prefix = self._driver_def.get("command_prefix") or ""

        for cmd_name, cmd_def in commands.items():
            send_template = cmd_def.get("send", "")
            if not send_template:
                continue
            if (
                prefix
                and not cmd_def.get("raw")
                and not send_template.startswith(prefix)
            ):
                send_template = prefix + send_template

            params = cmd_def.get("params", {})

            # Convert send template to regex — the shared inversion of the
            # same placeholder shapes the runtime substitutes on send.
            pattern_str = send_regex(send_template, params)
            try:
                pattern = re.compile(f"^{pattern_str}$")
            except re.error:
                logger.warning("Invalid regex from command '%s': %s", cmd_name, pattern_str)
                continue

            # Determine state changes
            state_changes: dict[str, int | Any] = {}
            response_var: str | None = None
            specs = send_param_specs(send_template, params)
            group_bases: dict[int, int] = {}

            # Heuristic 1: param name matches state variable
            group_idx = 1
            for param_name, param_def in params.items():
                ptype = (
                    param_def.get("type", "string")
                    if isinstance(param_def, dict)
                    else "string"
                )
                if ptype in ("integer", "child_id"):
                    # A non-decimal format spec ({level:02X}) put hex on the
                    # wire; remember the base so the capture decodes back to
                    # the number the driver sent.
                    base = spec_int_base(specs.get(param_name, ""))
                    if base:
                        group_bases[group_idx] = base
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
                group_bases=group_bases,
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
            else:
                value = src
            self.set_state(state_key, self._coerce_value(state_key, value))
        return (text + self._get_delimiter()).encode()

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

        # Build state machines. Skip malformed entries with a warning rather
        # than letting a missing key raise a KeyError that aborts the whole
        # device's construction (the validator flags these up front).
        from simulator.base import StateMachine
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

        # Parse notification templates — maps state changes to unsolicited messages
        # Format: notifications: { "state_key": { "value": "message template", ... } }
        # Template variables: {key} = state key name, {value} = new value
        self._notification_map: dict[str, dict[str, str]] = {}
        for key, value_map in sim.get("notifications", {}).items():
            if isinstance(value_map, dict):
                self._notification_map[key] = {
                    str(k): str(v) for k, v in value_map.items()
                }
            elif isinstance(value_map, str):
                # Simple template for any value: notifications: { key: "template with {value}" }
                self._notification_map[key] = {"*": value_map}

    # Ensure handler lists exist even without simulator: section
    _explicit_handlers: list[ExplicitHandler] = []
    _script_handlers: list[ScriptHandler] = []
    _notification_map: dict[str, dict[str, str]] = {}

    # ── State change notifications ──

    def set_state(self, key: str, value: Any) -> None:
        """Override to broadcast state changes to connected clients (TCP, UDP, and OSC)."""
        # Clamp numeric values to declared min/max before storing. Bounds are
        # typed as numbers, but a malformed driver could author a non-numeric
        # scalar — _as_number returns None for those so we skip clamping rather
        # than raise on `value < min`. For an integer value, round a fractional
        # bound inward (ceil a lower bound, floor an upper bound) so the clamped
        # value still respects the bound instead of truncating past it.
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            state_vars = self._driver_def.get("state_variables", {})
            var_def = state_vars.get(key, {})
            v_min = _as_number(var_def.get("min"))
            v_max = _as_number(var_def.get("max"))
            if v_min is not None and value < v_min:
                value = math.ceil(v_min) if isinstance(value, int) else v_min
            if v_max is not None and value > v_max:
                value = math.floor(v_max) if isinstance(value, int) else v_max

        old = self._state.get(key)
        super().set_state(key, value)
        if old == value:
            return

        # Push state changes to connected drivers — only when push_state is
        # enabled (matching real device behavior) and the change came from a
        # non-protocol source (simulator UI, API, error injection).
        if not self._push_state:
            pass
        elif (
            self._is_osc
            and not self._handling_osc
            and self._last_osc_client
            and self._udp_transport
            and key in self._osc_state_to_address
        ):
            self._push_osc_state(key, value)
        elif (
            not self._is_udp
            and not self._is_osc
            and not self._handling_command
            and self._clients
            and key in self._state_responses
        ):
            resp = self._state_responses[key]
            response_text = resp.format(value)
            delimiter = self._get_delimiter()
            push_data = (response_text + delimiter).encode()
            asyncio.ensure_future(self.push(push_data))
        elif (
            self._is_udp
            and not self._is_osc
            and not self._handling_command
            and self._last_udp_client
            and self._udp_transport
            and key in self._state_responses
        ):
            resp = self._state_responses[key]
            response_text = resp.format(value)
            delimiter = self._get_delimiter()
            push_data = (response_text + delimiter).encode()
            self._udp_transport.sendto(push_data, self._last_udp_client)
            self.log_protocol("out", push_data)

        # Manual TCP notification (legacy: explicit notifications: section in simulator YAML)
        if not self._notification_map or key not in self._notification_map:
            return

        value_map = self._notification_map[key]
        # Normalize the value for lookup
        if isinstance(value, bool):
            lookup = str(value).lower()
        else:
            lookup = str(value)

        template = value_map.get(lookup) or value_map.get("*")
        if not template:
            return

        msg = self._render_notification(template, key, value)
        delimiter = self._get_delimiter()
        data = (msg + delimiter).encode()

        # A driver with a multicast, SSE, or dial-back push channel gets its
        # notifications on that channel ONLY — real devices with a dedicated
        # notice feed never send those frames on the control connection.
        if self._push_multicast:
            if self._mcast_sock is not None:
                try:
                    self._mcast_sock.sendto(data, self._push_multicast)
                    self.log_protocol("out", data)
                except OSError:
                    logger.debug(
                        "Multicast notification send failed", exc_info=True
                    )
        elif self._push_sse_paths:
            # SSE events are self-framed — no delimiter appended.
            if self._sse_clients:
                for queue in list(self._sse_clients):
                    queue.put_nowait(msg)
                self.log_protocol("out", f"data: {msg[:200]}")
        elif self._push_tcp:
            # One outbound connection per registered subscriber, each
            # carrying one framed notification (the dial-back pattern).
            if self._push_tcp_subscribers:
                self._emit_push_tcp(data)
        elif self._push_http:
            # HTTP-callback bodies are self-framed — no delimiter appended.
            # No registered callback (controller never sent its registration
            # command) means no delivery, matching a real device.
            for url in list(self._http_push_callbacks):
                asyncio.ensure_future(self._post_http_callback(url, msg))
        elif self._clients:
            asyncio.ensure_future(self.push(data))

    @staticmethod
    def _render_notification(template: str, key: str, value: Any) -> str:
        """Render a notification template. `{value}` / `{key}` substitute as
        before; an optional format spec (`{value:d}`, `{value:04X}`) formats
        the value — booleans coerce to int first so `{value:d}` renders a
        protocol's 0/1 instead of 'True'/'False'."""

        def repl(m: re.Match) -> str:
            val: Any = value if m.group(1) == "value" else key
            spec = m.group(2)
            if not spec:
                return str(val)
            if isinstance(val, bool):
                val = int(val)
            try:
                return format(val, spec)
            except (ValueError, TypeError):
                # A numeric spec on a numeric string: coerce, then format.
                if isinstance(val, str):
                    for conv in (int, float):
                        try:
                            return format(conv(val), spec)
                        except (ValueError, TypeError):
                            continue
                return str(val)

        return re.sub(r"\{(value|key)(?::([^{}]*))?\}", repl, template)

    def _push_osc_state(self, key: str, value: Any) -> None:
        """Send an OSC message to the connected driver for a state change."""
        from server.transport.osc_codec import osc_encode_message

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
            if tag == "f":
                osc_value = float(value)
            elif tag == "i":
                osc_value = int(value) if not isinstance(value, bool) else (1 if value else 0)
            elif tag == "s":
                osc_value = str(value)
            else:
                osc_value = value

        data = osc_encode_message(addr, [(tag, osc_value)])
        self._udp_transport.sendto(data, self._last_osc_client)
        self.log_protocol("out", data)

    # ── Helpers ──

    def _get_delimiter(self) -> str:
        """Get the line delimiter as a string.

        HTTP responses don't have a line delimiter — the response body is
        whatever the handler returned, with no trailing CRLF.
        """
        if self._is_http:
            return ""
        return decode_delimiter(self._driver_def.get("delimiter", "\r\n"))

    def _coerce_value(self, state_key: str, value: Any) -> Any:
        """Coerce a value to the state variable's declared type and clamp to range."""
        state_vars = self._driver_def.get("state_variables", {})
        var_def = state_vars.get(state_key, {})
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
                # An integer var must start as an int even if min is authored
                # fractional; ceil keeps the start value >= min.
                min_num = _as_number(var_def.get("min"))
                initial_state[key] = math.ceil(min_num) if min_num is not None else 0
                v_min = var_def.get("min")
                v_max = var_def.get("max")
                if v_min is not None and v_max is not None:
                    ctl = {"type": "slider", "key": key, "label": label,
                           "min": v_min, "max": v_max}
                    if var_def.get("step") is not None:
                        ctl["step"] = var_def["step"]
                    if var_def.get("unit"):
                        ctl["unit"] = var_def["unit"]
                    controls.append(ctl)
                else:
                    controls.append({"type": "indicator", "key": key, "label": label})
            elif var_type == "number":
                initial_state[key] = 0.0
                v_min = var_def.get("min")
                v_max = var_def.get("max")
                if v_min is not None and v_max is not None:
                    ctl = {"type": "slider", "key": key, "label": label,
                           "min": v_min, "max": v_max,
                           "step": var_def.get("step", 0.1)}
                    if var_def.get("unit"):
                        ctl["unit"] = var_def["unit"]
                    controls.append(ctl)
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
    def __init__(
        self,
        name: str,
        pattern: re.Pattern,
        state_changes: dict,
        response_var: str | None,
        group_bases: dict[int, int] | None = None,
    ):
        self.name = name
        self.pattern = pattern
        self.state_changes = state_changes
        self.response_var = response_var
        # Capture groups whose wire form is non-decimal ({level:02X}),
        # mapped to the int base that decodes them.
        self.group_bases = group_bases or {}


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


class _YAMLAutoUDPProtocol(asyncio.DatagramProtocol):
    """Internal protocol handler that routes UDP datagrams to YAMLAutoSimulator."""

    def __init__(self, simulator: YAMLAutoSimulator):
        self._simulator = simulator

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self._simulator._handle_udp_datagram(data, addr)

    def error_received(self, exc: Exception) -> None:
        logger.warning("YAML auto UDP simulator error: %s", exc)

    def connection_lost(self, exc: Exception | None) -> None:
        if exc:
            logger.debug("YAML auto UDP connection lost: %s", exc)
