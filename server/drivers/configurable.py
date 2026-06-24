"""
OpenAVC ConfigurableDriver — interprets YAML driver definitions at runtime.

This enables creating device drivers without writing Python code. A YAML
(.avcdriver) definition specifies transport, commands, response parsing, and
polling — the ConfigurableDriver reads this at runtime and produces the
same behavior as a hand-coded Python driver.

Usage:
    driver_def = load_yaml("extron_switcher.avcdriver")
    DriverClass = create_configurable_driver_class(driver_def)
    register_driver(DriverClass)
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from server.drivers.base import BaseDriver
from server.drivers.inline_protocol import (
    _derive_command_params,
    _derive_state_vars_from_responses,
    _normalize_config_commands,
    _normalize_config_responses,
    _normalize_config_state_vars,
)
from server.transport.binary_helpers import encode_escape_sequences as _safe_encode_escapes
from server.transport.frame_parsers import DEFAULT_MAX_BUFFER, FrameParser
from server.utils.logger import get_logger

log = get_logger(__name__)

# httpx transport/timeout/status errors are transport-level for missed-poll
# watchdog purposes (mirrors BaseDriver._poll_loop). poll() re-raises these so
# an unreachable HTTP device trips the watchdog instead of looking connected.
try:
    import httpx as _httpx

    _HTTP_TRANSPORT_ERRORS: tuple[type[BaseException], ...] = (_httpx.HTTPError,)
except ImportError:  # pragma: no cover - httpx is a hard dependency in practice
    _HTTP_TRANSPORT_ERRORS = ()

# Hard cap on bytes buffered during the pre-auth login handshake. The handshake
# runs in raw mode (no frame parser), so without a cap a device — or anything
# spoofing its IP — could stream forever before any prompt match, growing memory
# and forcing an O(n^2) re-scan per chunk on an unauthenticated path. Reuse the
# frame parser's buffer ceiling; a login banner + prompts never approach it.
_AUTH_MAX_BUFFER = DEFAULT_MAX_BUFFER


# Sentinel returned by _extract_json_path when a JSON string can't be parsed
# or the requested path doesn't exist — distinct from a legitimately-extracted
# None so the caller can skip the mapping instead of writing a wrong value.
_JSON_PATH_MISSING = object()


# Tracks (driver_id, legacy_key) tuples that have already been warned about,
# so a deprecation message fires once per driver type rather than per instance
# or per response handled.
_WARNED_LEGACY_KEYS: set[tuple[str, str]] = set()


def _warn_legacy_key(driver_id: str, legacy_key: str, replacement: str) -> None:
    """Emit a one-time deprecation warning for a legacy YAML driver key."""
    marker = (driver_id, legacy_key)
    if marker in _WARNED_LEGACY_KEYS:
        return
    _WARNED_LEGACY_KEYS.add(marker)
    log.warning(
        "Driver '%s' uses deprecated YAML key '%s'; use '%s' instead. "
        "Both are accepted today but the alias may be removed in a future release.",
        driver_id, legacy_key, replacement,
    )


def _warn_legacy_keys_in_definition(driver_def: dict[str, Any]) -> None:
    """Scan a driver definition for deprecated YAML keys and warn once each."""
    driver_id = driver_def.get("id", "?")

    for cmd_def in driver_def.get("commands", {}).values():
        if not isinstance(cmd_def, dict):
            continue
        if "send" not in cmd_def and "string" in cmd_def:
            _warn_legacy_key(driver_id, "string", "send")
            break  # one warning per driver_id is enough

    for resp in driver_def.get("responses", []):
        if not isinstance(resp, dict):
            continue
        if "match" not in resp and "pattern" in resp:
            _warn_legacy_key(driver_id, "pattern", "match")
            break


def _build_commands_meta(commands_def: dict[str, Any]) -> dict[str, Any]:
    """Build the DRIVER_INFO ``commands`` UI metadata from a commands map.

    Shared by the driver-class factory (file-authored commands) and the
    per-instance inline-protocol merge (device-config-authored commands) so
    both present identically in the IDE — same label, params, and
    transport-specific fields (HTTP method/path/body, OSC address/args).
    """
    commands_meta: dict[str, Any] = {}
    for cmd_name, cmd_def in (commands_def or {}).items():
        if not isinstance(cmd_def, dict):
            continue
        cmd_meta: dict[str, Any] = {
            "label": cmd_def.get("label", cmd_name),
            "params": cmd_def.get("params", {}),
        }
        for key in ("method", "path", "body", "address", "args", "help"):
            if key in cmd_def:
                cmd_meta[key] = cmd_def[key]
        commands_meta[cmd_name] = cmd_meta
    return commands_meta


class ConfigurableDriver(BaseDriver):
    """
    A driver that interprets a YAML (.avcdriver) definition at runtime.

    The definition dict must contain:
        - id, name, manufacturer, category, transport
        - commands: dict of command_name -> {string, params}
        - responses: list of {pattern, mappings} for parsing
        - polling: optional {queries}. Poll cadence is sourced from
          default_config.poll_interval — a top-level polling.interval is
          ignored.
        - state_variables, config_schema, default_config
    """

    # DRIVER_INFO is set dynamically by create_configurable_driver_class()
    DRIVER_INFO: dict[str, Any] = {}

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        # _definition is set on the class by the factory function
        self._definition: dict[str, Any] = getattr(self.__class__, "_definition", {})
        super().__init__(*args, **kwargs)

        # Inline protocol: merge any commands / responses / state_variables the
        # device authored in its project-file config over the file definition,
        # producing a per-instance _definition + DRIVER_INFO. Runs before the
        # response compile + derived-config below so the config-authored
        # responses are compiled and config commands are visible to the IDE.
        self._merge_config_protocol()

        # Compute declarative derived config values (e.g. an optional address
        # prefix) into self.config, so every downstream substitution path —
        # commands, on_connect, responses, polling — sees them. Done before the
        # responses are compiled below so a derived value can appear in a
        # response address too.
        self._compute_derived_config()

        # Pre-compile response patterns — two separate lists:
        # 1. Regex patterns for TCP/serial/UDP/HTTP responses
        # 2. OSC address patterns for OSC responses
        self._compiled_responses: list[tuple[re.Pattern[str], list[dict[str, Any]]]] = []
        self._osc_responses: list[tuple[str, list[dict[str, Any]]]] = []
        # JSON-body responses: each entry is a list of {state, key, type, map}
        # mappings applied together from one parsed JSON object (multi-field).
        self._json_responses: list[list[dict[str, Any]]] = []

        # Telnet/serial login handshake state. Active only during
        # _perform_auth_handshake() — outside that window on_data_received
        # falls through to normal response matching.
        self._auth_mode: bool = False
        self._auth_buffer: bytearray = bytearray()
        self._auth_event: asyncio.Event = asyncio.Event()
        # Set by on_data_received when _auth_buffer exceeds _AUTH_MAX_BUFFER so
        # _auth_wait_for can abort the handshake instead of growing unbounded.
        self._auth_overflow: bool = False

        for resp in self._definition.get("responses", []):
            # OSC responses use "address" key instead of "pattern"/"match"
            if "address" in resp:
                addr = self._safe_substitute(resp["address"], self.config)
                # Copy per-instance: the source list lives in the shared class
                # _definition; aliasing it would let one instance's edits leak
                # into every instance of this driver type.
                mappings = list(resp.get("mappings", []))
                self._osc_responses.append((addr, mappings))
                continue

            # JSON-body response: parse the whole body once and map many keys
            # at a time. Additive — does not change the regex first-match path.
            if resp.get("json"):
                self._json_responses.append(self._build_json_mappings(resp))
                continue

            try:
                # Canonical key is "match"; "pattern" remains accepted as an alias.
                raw_pattern = resp.get("match", "") or resp.get("pattern", "")
                if not raw_pattern:
                    continue
                resolved = self._safe_substitute(raw_pattern, self.config)
                pattern = re.compile(resolved)

                # Accept both "mappings" (detailed) and "set" (shorthand)
                # formats. Copy per-instance — the source list lives in the
                # shared class _definition, and the shorthand path below
                # appends to it; mutating the shared list would corrupt every
                # other instance of this driver type.
                mappings = list(resp.get("mappings", []))
                if not mappings and "set" in resp:
                    # Convert shorthand: {"set": {"input": "$1", "mute": "true"}}
                    # to mappings: [{"group": 1, "state": "input"}, ...]
                    state_vars = self._definition.get("state_variables", {})
                    for state_key, value_expr in resp["set"].items():
                        var_def = state_vars.get(state_key, {})
                        var_type = var_def.get("type", "string") if isinstance(var_def, dict) else "string"
                        if isinstance(value_expr, str) and value_expr.startswith("$"):
                            try:
                                group = int(value_expr[1:])
                            except ValueError:
                                # A non-numeric $-reference is an author typo.
                                # Silently capturing group 0 (the whole match)
                                # would write a wrong value with no warning, so
                                # surface it and skip this mapping. Use "$0"
                                # explicitly if the whole match is intended.
                                log.warning(
                                    "[%s] set-shorthand reference %r for state "
                                    "'%s' is not a numeric group ($1, $2, ...); "
                                    "skipping this mapping",
                                    self.device_id, value_expr, state_key,
                                )
                                continue
                            mappings.append({"group": group, "state": state_key, "type": var_type})
                        else:
                            mappings.append({"group": 0, "state": state_key, "value": value_expr})

                self._compiled_responses.append((pattern, mappings))
            except re.error as e:
                log.warning(
                    f"[{self.device_id}] Invalid response pattern "
                    f"'{resp.get('match', resp.get('pattern', ''))}': {e}"
                )

    def _merge_config_protocol(self) -> None:
        """Merge device-config-authored ``commands`` / ``responses`` /
        ``state_variables`` over the file definition (the inline-protocol
        feature) into a per-instance ``_definition`` and ``DRIVER_INFO``.

        Config *extends/overrides* the file: commands merge by name (config
        wins), responses append after the file's (so a file driver's base
        behavior still matches first — first-match-wins), and state variables
        merge (explicit config + those auto-derived from the responses). A
        no-op when the device authors none of the three keys, so file-only
        drivers keep sharing the immutable class definition unchanged.

        The per-instance ``DRIVER_INFO`` shadow is what surfaces config
        commands + state vars to the device page (``get_device_info`` reads the
        live instance), the macro command picker, and state-var seeding — the
        class attribute is shared across every device of this driver type, so
        a per-device protocol must not write to it.
        """
        # The shared line terminator: the device's configured delimiter, else
        # the driver default (DRIVER_INFO/def). Appended to each send command
        # so commands authored in the editor don't each need a literal \r.
        line_ending = self.config.get("delimiter")
        if not line_ending:
            line_ending = self.DRIVER_INFO.get("delimiter") or self._definition.get(
                "delimiter", ""
            )

        norm_commands = _normalize_config_commands(
            self.config.get("commands"), line_ending
        )
        norm_responses = _normalize_config_responses(self.config.get("responses"))
        norm_state_vars = _normalize_config_state_vars(
            self.config.get("state_variables")
        )

        if not (norm_commands or norm_responses or norm_state_vars):
            return

        # Auto-declare params for {placeholder} tokens so parameterized commands
        # prompt in the Send Command card. config_keys are excluded — a {host}
        # token resolves from config, not a prompt. Scans the send string
        # (byte-stream) plus path/body (HTTP) where placeholders can appear.
        config_keys = set(self.config.keys())
        for cmd in norm_commands.values():
            ph_src = " ".join(
                str(cmd[f]) for f in ("send", "path", "body") if isinstance(cmd.get(f), str)
            )
            if "{" in ph_src:
                cmd["params"] = _derive_command_params(
                    ph_src, config_keys, cmd.get("params")
                )

        merged = dict(self._definition)
        file_commands = merged.get("commands") or {}
        file_responses = merged.get("responses") or []
        file_state_vars = merged.get("state_variables") or {}

        merged_commands = {**file_commands, **norm_commands}
        merged_responses = list(file_responses) + norm_responses
        derived_vars = _derive_state_vars_from_responses(merged_responses)
        merged_state_vars = {**file_state_vars, **derived_vars, **norm_state_vars}

        # Commands the editor flagged "poll" are sent on the device's
        # poll_interval to keep status values live (the device must be polled —
        # most AV gear doesn't push). For a byte-stream command the query is the
        # send string (line ending included); an HTTP/OSC command has no send
        # string, so its name is used (poll() looks the name up). Appended after
        # any file-defined poll queries.
        poll_queries = [
            cmd["send"] if isinstance(cmd.get("send"), str) else name
            for name, cmd in norm_commands.items()
            if cmd.get("poll")
        ]
        if poll_queries:
            file_polling = merged.get("polling") or {}
            merged["polling"] = {
                **file_polling,
                "queries": list(file_polling.get("queries", [])) + poll_queries,
            }

        merged["commands"] = merged_commands
        merged["responses"] = merged_responses
        merged["state_variables"] = merged_state_vars
        self._definition = merged

        info = dict(self.DRIVER_INFO)
        info["commands"] = _build_commands_meta(merged_commands)
        info["state_variables"] = merged_state_vars
        self.DRIVER_INFO = info

        # Re-seed state variables now that the config-added ones exist.
        # super().__init__() already seeded the file vars; re-seeding pre-poll
        # is harmless (same defaults) and gives the new vars an initial value
        # so they appear on the device card before the first matching reply.
        self._init_state_variables()

    def _compute_derived_config(self) -> None:
        """Populate self.config with values derived from other config fields.

        Driven by the optional top-level ``config_derived`` map of
        ``{name: template}``. Each template is substituted against config; if
        any ``{field}`` it references resolves to an empty/missing value, the
        derived value is ``""`` — so an optional prefixed segment simply
        disappears. This powers patterns like an OSC workspace prefix::

            config_derived:
              ws: "/workspace/{workspace_id}"   # "" when workspace_id is blank

        so a single friendly config field drives both rootless and
        workspace-scoped addressing without conditional logic in every command.
        """
        derived = self._definition.get("config_derived")
        if not isinstance(derived, dict):
            return
        for name, template in derived.items():
            if not isinstance(template, str):
                continue
            refs = re.findall(r"\{(\w+)(?::[^{}]*)?\}", template)
            if any(not str(self.config.get(f, "") or "").strip() for f in refs):
                self.config[name] = ""
            else:
                self.config[name] = self._safe_substitute(template, self.config)

    async def connect(self) -> None:
        """Connect and send on_connect initialization commands.

        Defers polling until after on_connect and initial state queries
        complete, so the watchdog doesn't start counting before the
        device is fully initialized.
        """
        saved_poll_interval = self.config.get("poll_interval", 0)
        self.config["poll_interval"] = 0

        # Enable auth-buffering BEFORE the TCP connect so any prompt the
        # device emits the moment the connection opens lands in the auth
        # buffer instead of being run through the normal response matcher.
        # _perform_auth_handshake() turns this back off when it's done.
        auth_def = self._definition.get("auth")
        has_auth = isinstance(auth_def, dict) and self._auth_should_run(auth_def)
        if has_auth:
            self._auth_buffer = bytearray()
            self._auth_event = asyncio.Event()
            self._auth_overflow = False
            self._auth_mode = True

        try:
            await super().connect()
        except Exception:
            self._auth_mode = False
            raise

        # Many login prompts arrive without the protocol's delimiter (e.g.
        # bare "Login: "), so the transport's delimiter-based frame parser
        # would buffer them indefinitely. Drop the parser for the duration
        # of the handshake and reinstate it once login completes.
        if has_auth and self.transport is not None:
            saved_parser = getattr(self.transport, "_frame_parser", None)
            if hasattr(self.transport, "_frame_parser"):
                # If the parser had buffered any pre-auth bytes, flush them
                # into the auth buffer so we don't lose the prompt.
                if saved_parser is not None and hasattr(saved_parser, "_buffer"):
                    pending = bytes(saved_parser._buffer)
                    if pending:
                        self._auth_buffer.extend(pending)
                        self._auth_event.set()
                        saved_parser._buffer = b""
                self.transport._frame_parser = None  # type: ignore[union-attr]
            self._saved_frame_parser = saved_parser
        else:
            self._saved_frame_parser = None

        self.config["poll_interval"] = saved_poll_interval

        # Perform Telnet/serial login handshake before on_connect commands
        # if the driver definition declares an `auth:` section.
        if has_auth and self.transport and self.transport.connected:
            try:
                await self._perform_auth_handshake()
            except Exception as e:
                log.error(f"[{self.device_id}] Auth handshake failed: {e}")
                if self.transport:
                    try:
                        await self.transport.close()
                    except Exception:
                        pass
                    self.transport = None
                self._connected = False
                self.set_state("connected", False)
                raise ConnectionError(
                    f"[{self.device_id}] Authentication failed: {e}"
                ) from e
        else:
            self._auth_mode = False

        on_connect = self._definition.get("on_connect", [])
        if on_connect and self.transport and self.transport.connected:
            transport_type = self._definition.get("transport")
            delay = self.config.get("inter_command_delay", 0)

            if transport_type == "osc":
                from server.transport.osc_codec import osc_encode_message
                for item in on_connect:
                    try:
                        if isinstance(item, str):
                            address = self._safe_substitute(item, self.config) if "{" in item else item
                            data = osc_encode_message(address)
                        elif isinstance(item, dict):
                            address = item.get("address", "")
                            if "{" in address:
                                address = self._safe_substitute(address, self.config)
                            args = self._build_osc_args(item.get("args", []), self.config)
                            data = osc_encode_message(address, args)
                        else:
                            continue
                        await self.transport.send(data)
                        if delay:
                            await asyncio.sleep(delay)
                    except Exception as e:
                        log.warning(f"[{self.device_id}] on_connect OSC command failed: {e}")

                # Query all OSC state variable addresses to fetch initial state.
                # OSC convention: sending an address with no args returns the
                # current value. This populates state immediately on connect.
                query_delay = max(delay, 0.005)
                for addr_pattern, _mappings in self._osc_responses:
                    # A response address with fnmatch wildcards (e.g. QLab's
                    # push-only "/update/workspace/*/...") is a match pattern,
                    # not a queryable address — sending it literally is
                    # meaningless. Such state arrives via push or a dedicated
                    # on_connect/poll query instead.
                    if any(c in addr_pattern for c in "*?["):
                        continue
                    try:
                        addr = self._safe_substitute(addr_pattern, self.config) if "{" in addr_pattern else addr_pattern
                        await self.transport.send(osc_encode_message(addr))
                        await asyncio.sleep(query_delay)
                    except Exception as e:
                        log.warning(f"[{self.device_id}] OSC initial query failed: {e}")
            else:
                for raw in on_connect:
                    try:
                        await self._dispatch_query(raw)
                        if delay:
                            await asyncio.sleep(delay)
                    except Exception as e:
                        log.warning(f"[{self.device_id}] on_connect command failed: {e}")

        if saved_poll_interval > 0:
            await self.start_polling(saved_poll_interval)

    async def _dispatch_query(self, query: str) -> None:
        """Send one query string for the active (non-OSC) transport.

        Shared by on_connect and poll() so the two resolve queries identically.
        For HTTP/UDP a query that names a command runs as that command, so its
        response goes through the matcher; any other string is a raw path /
        payload (and an HTTP raw path's response is fed to the matcher too).
        TCP/serial send the raw protocol string. OSC is handled by the callers.
        """
        transport_type = self._definition.get("transport")
        commands = self._definition.get("commands", {})
        if transport_type == "http":
            if query in commands:
                await self.send_command(query)
            else:
                formatted = self._safe_substitute(query, self.config) if "{" in query else query
                response = await self.transport.get(formatted)
                if response.text:
                    await self.on_data_received(response.text.encode("utf-8"))
        elif transport_type == "udp":
            if query in commands:
                await self.send_command(query)
            else:
                formatted = self._safe_substitute(query, self.config) if "{" in query else query
                await self.transport.send(_safe_encode_escapes(formatted))
        else:  # tcp / serial
            formatted = self._safe_substitute(query, self.config) if "{" in query else query
            await self.transport.send(_safe_encode_escapes(formatted))

    def _auth_should_run(self, auth_def: dict[str, Any]) -> bool:
        """Quick gate used by connect() to decide whether to buffer
        incoming bytes for the handshake. Mirrors the early-exit checks
        in _perform_auth_handshake so the two stay aligned."""
        if auth_def.get("type", "telnet_login") != "telnet_login":
            return False
        # The handshake assumes a TCP/serial byte stream — it swaps out the
        # frame parser and buffers raw bytes. On udp/http/osc that breaks the
        # transport's normal data path, so never run it there. The loader also
        # rejects auth on these transports; this is the runtime backstop.
        if self._definition.get("transport") not in ("tcp", "serial"):
            return False
        if not auth_def.get("username_prompt") or not auth_def.get("password_prompt"):
            return False
        username_field = auth_def.get("username_field", "username")
        username = str(self.config.get(username_field, "") or "")
        if auth_def.get("skip_if_empty", True) and not username:
            return False
        return True

    async def _perform_auth_handshake(self) -> None:
        """Run the Telnet-style login handshake declared in `auth:` (if any).

        YAML schema (top-level `auth:` block):
            auth:
              type: telnet_login
              username_prompt: "login: "        # regex
              password_prompt: "Password: "     # regex
              success_pattern: "GNET> "         # optional regex
              failure_pattern: "Login incorrect" # optional regex
              username_field: username           # config field, default "username"
              password_field: password           # config field, default "password"
              skip_if_empty: true                # default true — empty user => skip
              timeout_seconds: 10
              line_ending: "\r\n"

        The handshake bypasses the transport's frame parser so partial
        prompts like "Login: " (no trailing newline) are visible. The
        original frame parser is restored after the handshake completes.
        """
        auth_def = self._definition.get("auth")
        if not isinstance(auth_def, dict):
            self._auth_mode = False
            return

        if not self._auth_should_run(auth_def):
            self._auth_mode = False
            return

        username_field = auth_def.get("username_field", "username")
        password_field = auth_def.get("password_field", "password")
        username = str(self.config.get(username_field, "") or "")
        password = str(self.config.get(password_field, "") or "")

        username_prompt = auth_def.get("username_prompt", "")
        password_prompt = auth_def.get("password_prompt", "")
        success_pattern = auth_def.get("success_pattern")
        failure_pattern = auth_def.get("failure_pattern")
        timeout = float(auth_def.get("timeout_seconds", 10))
        line_ending = auth_def.get("line_ending", "\r\n")

        try:
            user_re = re.compile(username_prompt)
            pass_re = re.compile(password_prompt)
            success_re = re.compile(success_pattern) if success_pattern else None
            failure_re = re.compile(failure_pattern) if failure_pattern else None
        except re.error as e:
            raise ValueError(f"Invalid auth regex pattern: {e}") from e

        # connect() already swapped the transport to raw mode and stashed
        # the original parser on self._saved_frame_parser. We just restore
        # it in the finally block below.
        saved_parser = getattr(self, "_saved_frame_parser", None)

        try:
            ending = _safe_encode_escapes(line_ending)
            log.info(f"[{self.device_id}] Starting auth handshake")

            # Stage 1: wait for username prompt, send username.
            await self._auth_wait_for(user_re, failure_re, timeout)
            await self.transport.send(username.encode("utf-8") + ending)
            log.debug(f"[{self.device_id}] Auth: sent username")

            # Stage 2: wait for password prompt, send password.
            await self._auth_wait_for(pass_re, failure_re, timeout)
            await self.transport.send(password.encode("utf-8") + ending)
            log.debug(f"[{self.device_id}] Auth: sent password")

            # Stage 3: optionally wait for a success indicator. Without one,
            # we assume success once the password is sent (the next command
            # sent will fail visibly if auth was rejected).
            if success_re is not None:
                await self._auth_wait_for(success_re, failure_re, timeout)
                log.info(f"[{self.device_id}] Auth handshake complete")
            else:
                # Drain any post-password noise so it doesn't pollute the
                # first real command's response window.
                try:
                    await asyncio.wait_for(self._auth_event.wait(), timeout=0.5)
                except asyncio.TimeoutError:
                    pass
                log.info(
                    f"[{self.device_id}] Auth handshake complete "
                    f"(no success_pattern; assuming OK)"
                )
        finally:
            self._auth_mode = False
            self._auth_buffer = bytearray()
            if hasattr(self.transport, "_frame_parser"):
                self.transport._frame_parser = saved_parser  # type: ignore[union-attr]

    async def _auth_wait_for(
        self,
        target: re.Pattern[str],
        failure: re.Pattern[str] | None,
        timeout: float,
    ) -> None:
        """Wait until `target` regex matches accumulated auth bytes.

        Raises ConnectionError if `failure` matches first or if the timeout
        elapses without a match. Patterns are string regexes — they're
        matched against the buffer's UTF-8 decoding (errors=replace).
        """
        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            # Clear the event BEFORE inspecting the buffer so we don't drop
            # a set() that arrives between check and clear. If new data
            # arrives between clear and check, the buffer already contains
            # it; if it arrives after the check, the set() will unblock
            # the wait() below.
            self._auth_event.clear()
            if self._auth_overflow:
                raise ConnectionError(
                    f"auth aborted: more than {_AUTH_MAX_BUFFER} bytes received "
                    f"before matching {target.pattern!r}"
                )
            text = self._auth_buffer.decode("utf-8", errors="replace")
            if failure is not None and failure.search(text):
                raise ConnectionError(
                    f"login rejected by device "
                    f"(matched failure pattern in {text!r})"
                )
            if target.search(text):
                return

            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                raise ConnectionError(
                    f"timeout waiting for {target.pattern!r}; got {text!r}"
                )

            try:
                await asyncio.wait_for(self._auth_event.wait(), timeout=remaining)
            except asyncio.TimeoutError:
                raise ConnectionError(
                    f"timeout waiting for {target.pattern!r}; got {text!r}"
                ) from None

    async def send_command(
        self, command: str, params: dict[str, Any] | None = None
    ) -> Any:
        """Look up command in definition, substitute params, send."""
        params = params or {}

        if not self.transport or not self.transport.connected:
            raise ConnectionError(f"[{self.device_id}] Not connected")

        commands = self._definition.get("commands", {})
        cmd_def = commands.get(command)
        if cmd_def is None:
            log.warning(f"[{self.device_id}] Unknown command: {command}")
            return None

        # Check if this is an OSC command (has 'address' key)
        if self._is_osc_command(cmd_def):
            return await self._send_osc_command(command, cmd_def, params)

        # Check if this is an HTTP transport command (has 'path' or 'method' keys)
        if self._is_http_command(cmd_def):
            return await self._send_http_command(command, cmd_def, params)

        # Canonical key is "send"; "string" remains accepted as an alias.
        raw = cmd_def.get("send", "") or cmd_def.get("string", "")
        if not raw:
            log.warning(f"[{self.device_id}] Command '{command}' has no send string")
            return None

        # Substitute {param} placeholders — merge config values so drivers
        # can use config fields like {set_id} or {level_instance_tag} in commands.
        # Uses _safe_substitute to handle JSON protocols (UDP) where literal
        # braces must be preserved — only {name} tokens matching known params
        # are replaced, all other braces are left alone.
        all_params = {**self.config, **params}
        formatted = self._safe_substitute(raw, all_params)

        # Encode (handle explicit escape sequences only — safe subset)
        data = _safe_encode_escapes(formatted)
        await self.transport.send(data)
        log.debug(f"[{self.device_id}] Sent command '{command}': {data!r}")
        return True

    def _is_osc_command(self, cmd_def: dict[str, Any]) -> bool:
        """Check if a command definition uses OSC-style fields."""
        return "address" in cmd_def

    async def _send_osc_command(
        self, command: str, cmd_def: dict[str, Any], params: dict[str, Any]
    ) -> Any:
        """Send an OSC command: encode address + typed args and send."""
        from server.transport.osc import OSCTransport
        from server.transport.osc_codec import osc_encode_message

        # Guard against silently emitting OSC bytes on a non-OSC socket when a
        # command's declared shape (an `address`) doesn't match the active
        # transport. Mirrors the HTTP path's isinstance check.
        if not isinstance(self.transport, OSCTransport):
            log.error(
                f"[{self.device_id}] Command '{command}' uses OSC fields "
                f"but transport is not OSC"
            )
            return None

        all_params = {**self.config, **params}

        raw_address = cmd_def.get("address", "")
        address = self._safe_substitute(raw_address, all_params)

        args = self._build_osc_args(cmd_def.get("args", []), all_params)
        data = osc_encode_message(address, args)
        await self.transport.send(data)
        log.debug(f"[{self.device_id}] Sent OSC command '{command}': {address}")
        return True

    @staticmethod
    def _osc_num(tag: str, value: str, converter: Any) -> Any:
        """Coerce an OSC arg value to a number, raising a clear error.

        A missing/unresolved value (e.g. ``float("")`` or an unmatched
        ``{placeholder}``) would otherwise surface as a bare ValueError with no
        context. Turn it into an actionable message naming the OSC type tag.
        """
        try:
            return converter(value)
        except (ValueError, TypeError) as e:
            raise ValueError(
                f"OSC arg of type '{tag}' requires a numeric value, got {value!r}"
            ) from e

    @staticmethod
    def _build_osc_args(
        arg_defs: list[dict[str, Any]], params: dict[str, Any]
    ) -> list[tuple[str, Any]]:
        """Build a list of typed OSC args from definition, substituting params."""
        args: list[tuple[str, Any]] = []
        for arg_def in arg_defs:
            tag = arg_def.get("type", "f")
            raw_value = str(arg_def.get("value", ""))

            # Substitute {param} placeholders
            if "{" in raw_value:
                resolved = ConfigurableDriver._safe_substitute(raw_value, params)
            else:
                resolved = raw_value

            if tag == "f":
                args.append(("f", ConfigurableDriver._osc_num("f", resolved, float)))
            elif tag == "i":
                args.append(("i", int(ConfigurableDriver._osc_num("i", resolved, float))))
            elif tag == "s":
                args.append(("s", resolved))
            elif tag == "h":
                args.append(("h", ConfigurableDriver._osc_num("h", resolved, int)))
            elif tag == "d":
                args.append(("d", ConfigurableDriver._osc_num("d", resolved, float)))
            elif tag == "T":
                args.append(("T", True))
            elif tag == "F":
                args.append(("F", False))
            elif tag == "N":
                args.append(("N", None))
            else:
                # Unsupported tag (e.g. 'b'/blob, or a typo). The loader rejects
                # these at load time; warn rather than silently drop the arg and
                # emit a malformed (short-by-one) OSC message on any path that
                # bypasses the loader.
                log.warning(
                    "OSC arg type %r is not supported (expected one of "
                    "f/i/s/h/d/T/F/N); arg dropped",
                    tag,
                )
        return args

    def _is_http_command(self, cmd_def: dict[str, Any]) -> bool:
        """Check if a command definition uses HTTP-style fields."""
        return "path" in cmd_def or "method" in cmd_def

    async def _send_http_command(
        self, command: str, cmd_def: dict[str, Any], params: dict[str, Any]
    ) -> Any:
        """
        Send an HTTP command using the HTTPClientTransport.

        HTTP commands in .avcdriver files use these fields:
            method: GET, POST, PUT, DELETE (default: GET)
            path: URL path (e.g., "/api/power")
            body: JSON body string with {param} substitution
            query_params: Query parameters dict with {param} substitution
            headers: Per-request headers dict with {param} substitution.
                     Use to set Content-Type for non-JSON bodies (e.g.
                     "text/xml" for XML APIs like Cisco xAPI), or any
                     other custom header the device requires.

        Parameter substitution uses a safe approach: only {name} tokens
        where name matches a known parameter or config key are replaced.
        Literal JSON braces are preserved.
        """
        from server.transport.http_client import HTTPClientTransport

        if not isinstance(self.transport, HTTPClientTransport):
            log.error(
                f"[{self.device_id}] Command '{command}' uses HTTP fields "
                f"but transport is not HTTP"
            )
            return None

        all_params = {**self.config, **params}

        method = cmd_def.get("method", "GET").upper()
        raw_path = cmd_def.get("path", "/")
        raw_body = cmd_def.get("body")
        headers = self._build_http_headers(cmd_def.get("headers"), all_params)

        # Substitute params in path using safe substitution
        path = self._safe_substitute(raw_path, all_params)

        # Substitute params in body
        json_body = None
        if raw_body:
            body_str = self._safe_substitute(raw_body, all_params)
            # Parse body as JSON
            try:
                json_body = json.loads(body_str)
            except (json.JSONDecodeError, ValueError):
                # Not valid JSON — send as raw string body
                log.debug(
                    f"[{self.device_id}] Body for '{command}' is not JSON, "
                    f"sending as raw content"
                )
                response = await self.transport.request(
                    method, path, content=body_str.encode("utf-8"),
                    headers=headers,
                )
                return await self._process_http_response(command, response)

        # Build query params if specified
        query_params = None
        raw_query = cmd_def.get("query_params")
        if raw_query and isinstance(raw_query, dict):
            query_params = {}
            for k, v in raw_query.items():
                if isinstance(v, str):
                    query_params[k] = self._safe_substitute(v, all_params)
                else:
                    query_params[k] = v

        response = await self.transport.request(
            method, path, params=query_params, json_body=json_body,
            headers=headers,
        )
        return await self._process_http_response(command, response)

    def _build_http_headers(
        self, raw_headers: Any, params: dict[str, Any]
    ) -> dict[str, str] | None:
        """Substitute {param} placeholders in YAML-defined HTTP headers."""
        if not raw_headers or not isinstance(raw_headers, dict):
            return None
        out: dict[str, str] = {}
        for k, v in raw_headers.items():
            if isinstance(v, str):
                out[k] = self._safe_substitute(v, params)
            else:
                out[k] = str(v)
        return out

    @staticmethod
    def _safe_substitute(template: str, params: dict[str, Any]) -> str:
        """
        Substitute {name} and {name:spec} placeholders with values from params.

        Only replaces {name} where name is a key in params. An optional
        ``:format_spec`` (Python format-spec mini-language) formats the value —
        e.g. ``{preset:02d}`` zero-pads and ``{addr:04X}`` hex-formats, both
        common in device protocols. A numeric spec applied to a numeric string
        coerces it first, so a param that arrives as ``"5"`` still pads to
        ``"05"``. Literal JSON braces and unknown placeholders are left
        untouched, and an invalid spec leaves the placeholder verbatim rather
        than raising — this avoids the problem with Python's str.format()
        choking on JSON body strings.
        """
        def replacer(match: re.Match) -> str:
            key = match.group(1)
            if key not in params:
                return match.group(0)  # Leave unmatched {name} as-is
            spec = match.group(2)
            value = params[key]
            if not spec:
                return str(value)
            try:
                return format(value, spec)
            except (ValueError, TypeError):
                # A numeric spec ('d'/'x'/'f'/...) applied to a numeric string:
                # coerce, then format. int first so '02d' works on "5".
                if isinstance(value, str):
                    for conv in (int, float):
                        try:
                            return format(conv(value), spec)
                        except (ValueError, TypeError):
                            continue
                # Unformattable — leave the placeholder verbatim so a bad spec
                # is visible to the author instead of crashing the send.
                return match.group(0)

        # {name} or {name:spec}; the spec excludes braces so it can't span tokens.
        return re.sub(r"\{(\w+)(?::([^{}]*))?\}", replacer, template)

    async def _process_http_response(
        self, command: str, response: Any
    ) -> Any:
        """
        Process an HTTP response: check status and match response patterns.

        Returns the HTTPResponse object for the caller.
        """
        log.debug(
            f"[{self.device_id}] HTTP command '{command}' -> "
            f"status={response.status_code}"
        )

        # Run response text through the standard regex-based response matching
        # so .avcdriver response patterns work with HTTP responses too
        if response.text:
            await self.on_data_received(response.text.encode("utf-8"))

        return response

    async def on_data_received(self, data: bytes) -> None:
        """Match response against pre-compiled patterns, update state."""
        # During the login handshake, capture all bytes raw and let the
        # handshake state machine decide when to send credentials. Skip
        # the normal response-matching path entirely.
        if self._auth_mode:
            self._auth_buffer.extend(data)
            if len(self._auth_buffer) > _AUTH_MAX_BUFFER:
                # Pre-auth flood: stop accumulating and signal _auth_wait_for to
                # abort the handshake. Keeps memory and per-chunk re-scan bounded
                # on this unauthenticated path.
                self._auth_overflow = True
            self._auth_event.set()
            return

        if self._definition.get("transport") == "osc":
            await self._handle_osc_response(data)
            return

        text = data.decode("utf-8", errors="replace").strip()
        if not text:
            return

        # JSON-body responses (multi-field): parse once, apply every json rule
        # key-scoped. Additive — if the body isn't a JSON object or none of the
        # declared keys are present, fall through to regex matching below.
        if self._json_responses and self._apply_json_responses(text):
            return

        for pattern, mappings in self._compiled_responses:
            match = pattern.search(text)
            if match:
                for mapping in mappings:
                    state_key = mapping.get("state")
                    if not state_key:
                        continue

                    # Static value mapping (no regex group needed)
                    if "value" in mapping:
                        static = mapping["value"]
                        coerced = self._coerce_value(str(static), mapping.get("type", "string"))
                        self.set_state(state_key, coerced)
                        continue

                    # Regex group mapping
                    group = mapping.get("group", 0)
                    value_type = mapping.get("type", "string")
                    value_map = mapping.get("map")
                    json_path = mapping.get("json_path")

                    try:
                        raw_value = match.group(group)
                    except (IndexError, re.error):
                        continue

                    if raw_value is None:
                        continue

                    # Optional: the captured group is a JSON string; pull the
                    # value at json_path before mapping/coercion (parity with
                    # the OSC path — benefits HTTP/TCP JSON replies too).
                    # Absent json_path = today's behavior exactly.
                    if json_path is not None:
                        extracted = self._extract_json_path(raw_value, json_path)
                        if extracted is _JSON_PATH_MISSING:
                            continue
                        raw_value = str(extracted)

                    # Apply value map if defined. Coerce the mapped value too
                    # (parity with the OSC path): without this the same map+type
                    # stores "5" on TCP but 5 on OSC, and str() collapses a
                    # hostile list/dict map target to a flat primitive, keeping
                    # the state store's flat-primitives invariant intact.
                    if value_map and raw_value in value_map:
                        coerced = self._coerce_value(str(value_map[raw_value]), value_type)
                    else:
                        coerced = self._coerce_value(raw_value, value_type)

                    self.set_state(state_key, coerced)

                log.debug(
                    f"[{self.device_id}] Response matched: {pattern.pattern}"
                )
                return  # Stop at first match

        log.debug(f"[{self.device_id}] Unmatched response: {text!r}")

    async def _handle_osc_response(self, data: bytes) -> None:
        """Decode incoming OSC data and match against address-based responses."""
        import fnmatch
        import struct
        from server.transport.osc_codec import osc_decode_bundle

        try:
            messages = osc_decode_bundle(data)
        except (ValueError, struct.error) as e:
            log.warning(f"[{self.device_id}] Failed to decode OSC message: {e}")
            return

        for address, args in messages:
            matched = False
            for addr_pattern, mappings in self._osc_responses:
                if not fnmatch.fnmatch(address, addr_pattern):
                    continue
                matched = True
                for mapping in mappings:
                    state_key = mapping.get("state")
                    if not state_key:
                        continue

                    arg_index = mapping.get("arg", 0)
                    value_type = mapping.get("type", "string")
                    value_map = mapping.get("map")
                    json_path = mapping.get("json_path")

                    if arg_index >= len(args):
                        continue

                    _, raw_value = args[arg_index]

                    # Optional: the arg is a JSON string (QLab /reply ...);
                    # pull the value at json_path before mapping/coercion.
                    # Absent json_path = positional behavior exactly as before.
                    if json_path is not None:
                        extracted = self._extract_json_path(raw_value, json_path)
                        if extracted is _JSON_PATH_MISSING:
                            continue
                        raw_value = extracted

                    if value_map:
                        str_val = str(raw_value)
                        if str_val in value_map:
                            coerced = self._coerce_value(
                                str(value_map[str_val]), value_type
                            )
                        else:
                            coerced = self._coerce_osc_value(raw_value, value_type)
                    else:
                        coerced = self._coerce_osc_value(raw_value, value_type)

                    self.set_state(state_key, coerced)

                log.debug(f"[{self.device_id}] OSC matched: {addr_pattern}")
                break

            if not matched:
                log.debug(f"[{self.device_id}] Unmatched OSC: {address}")

    @staticmethod
    def _extract_json_path(raw_value: Any, path: Any) -> Any:
        """Parse a JSON string and walk a dotted path to a primitive.

        Used by response mappings that declare ``json_path`` — common for OSC
        devices (QLab's ``/reply/...`` carries a single string arg holding JSON
        like ``{"status":"ok","data":"Intro Music"}``; ``json_path: data``
        extracts the useful value).

        Path syntax is dot-separated keys and integer list indices, e.g.
        ``data``, ``data.name``, ``data.0``. An empty path returns the whole
        parsed value. A path that lands on a list or dict yields its length —
        keeping the state store's flat-primitive invariant and making a
        ``data`` array usable as a boolean "anything?" or an integer count.

        Returns ``_JSON_PATH_MISSING`` when the string isn't valid JSON or the
        path doesn't resolve, so the caller skips the mapping rather than
        writing a wrong value.
        """
        if isinstance(raw_value, (dict, list)):
            obj: Any = raw_value
        else:
            try:
                obj = json.loads(raw_value)
            except (ValueError, TypeError):
                return _JSON_PATH_MISSING

        if path:
            for seg in str(path).split("."):
                if seg == "":
                    continue
                if isinstance(obj, dict):
                    if seg not in obj:
                        return _JSON_PATH_MISSING
                    obj = obj[seg]
                elif isinstance(obj, list):
                    try:
                        obj = obj[int(seg)]
                    except (ValueError, IndexError):
                        return _JSON_PATH_MISSING
                else:
                    return _JSON_PATH_MISSING

        if isinstance(obj, (list, dict)):
            return len(obj)
        return obj

    def _build_json_mappings(self, resp: dict[str, Any]) -> list[dict[str, Any]]:
        """Build {state, key, type, map} mappings for a ``json: true`` response.

        Accepts the detailed ``mappings`` list or the friendly ``set`` map. In a
        json rule a ``set`` value is the JSON key to read (string shorthand) or a
        ``{key/path, type, map}`` spec — not a regex capture ref. Types default
        to the matching state variable's declared type.
        """
        mappings: list[dict[str, Any]] = list(resp.get("mappings", []))
        set_map = resp.get("set")
        if not mappings and isinstance(set_map, dict):
            state_vars = self._definition.get("state_variables", {})
            for state_key, spec in set_map.items():
                var_def = state_vars.get(state_key, {})
                default_type = (
                    var_def.get("type", "string") if isinstance(var_def, dict) else "string"
                )
                if isinstance(spec, dict):
                    mappings.append({
                        "state": state_key,
                        "key": spec.get("key", spec.get("path", state_key)),
                        "type": spec.get("type", default_type),
                        "map": spec.get("map"),
                    })
                else:
                    mappings.append({
                        "state": state_key, "key": str(spec), "type": default_type,
                    })
        return mappings

    def _apply_json_responses(self, text: str) -> bool:
        """Apply all JSON-body response rules to one response/message body.

        Parses ``text`` as a JSON object and, for every json rule mapping whose
        key resolves, coerces and stores the value. Returns True if at least one
        state was set (so the caller stops before regex matching). json rules are
        additive: a rule whose keys are absent from this body just sets nothing.
        """
        try:
            obj = json.loads(text)
        except (ValueError, TypeError):
            return False
        if not isinstance(obj, dict):
            return False
        applied = False
        for mappings in self._json_responses:
            for mapping in mappings:
                state_key = mapping.get("state")
                key = mapping.get("key")
                if not state_key or not key:
                    continue
                value = self._extract_json_path(obj, key)
                if value is _JSON_PATH_MISSING:
                    continue
                value_map = mapping.get("map")
                if value_map and str(value) in value_map:
                    coerced = self._coerce_value(
                        str(value_map[str(value)]), mapping.get("type", "string")
                    )
                else:
                    coerced = self._coerce_json_value(value, mapping.get("type", "string"))
                self.set_state(state_key, coerced)
                applied = True
        if applied:
            log.debug(
                f"[{self.device_id}] JSON response applied "
                f"({len(self._json_responses)} rule(s))"
            )
        return applied

    @staticmethod
    def _coerce_json_value(value: Any, value_type: str) -> Any:
        """Coerce a native JSON value to the declared state type.

        Unlike ``_coerce_value`` (string in), this keeps real JSON bools / ints /
        floats instead of round-tripping through ``str`` (which would turn JSON
        ``true`` into the string ``"True"``). Non-primitive values have already
        been collapsed to a length by ``_extract_json_path``.
        """
        if value is None:
            return None
        if value_type == "boolean":
            if isinstance(value, bool):
                return value
            return str(value).strip().lower() in ("1", "true", "yes", "on")
        if value_type == "integer":
            if isinstance(value, bool):
                return int(value)
            try:
                return int(value)
            except (TypeError, ValueError):
                try:
                    return int(float(value))
                except (TypeError, ValueError):
                    log.warning("Cannot coerce %r to integer, returning string", value)
                    return str(value)
        if value_type in ("float", "number"):
            try:
                return float(value)
            except (TypeError, ValueError):
                log.warning("Cannot coerce %r to %s, returning string", value, value_type)
                return str(value)
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)

    @staticmethod
    def _coerce_osc_value(value: Any, value_type: str) -> Any:
        """Convert an already-typed OSC value to the declared state type."""
        if value_type in ("float", "number"):
            try:
                return float(value)
            except (ValueError, TypeError):
                return value
        elif value_type == "integer":
            try:
                return int(value)
            except (ValueError, TypeError):
                return value
        elif value_type == "boolean":
            if isinstance(value, bool):
                return value
            if isinstance(value, (int, float)):
                return bool(value)
            return str(value).lower() in ("1", "true", "yes", "on")
        return str(value) if value is not None else None

    async def set_device_setting(self, key: str, value: Any) -> Any:
        """
        Write a device setting using the write definition from the driver YAML.

        Supports HTTP (method/path/body) and TCP/serial (send) write formats.
        """
        settings = self._definition.get("device_settings", {})
        setting_def = settings.get(key)
        if not setting_def:
            raise ValueError(f"Unknown device setting: {key}")

        write_def = setting_def.get("write")
        if not write_def:
            raise NotImplementedError(
                f"Device setting '{key}' has no write definition"
            )

        all_params = {**self.config, "value": value}

        # OSC write
        if "address" in write_def:
            from server.transport.osc import OSCTransport
            from server.transport.osc_codec import osc_encode_message

            if not self.transport or not self.transport.connected:
                raise ConnectionError(f"[{self.device_id}] Not connected")

            # Don't emit OSC bytes on a non-OSC socket — mirrors the HTTP guard.
            if not isinstance(self.transport, OSCTransport):
                raise ConnectionError(
                    f"[{self.device_id}] Setting '{key}' uses OSC write "
                    f"but transport is not OSC"
                )

            raw_address = write_def.get("address", "")
            address = self._safe_substitute(raw_address, all_params)
            args = self._build_osc_args(write_def.get("args", []), all_params)
            data = osc_encode_message(address, args)
            await self.transport.send(data)
            log.debug(
                f"[{self.device_id}] Set device setting '{key}' = {value!r}"
            )
            return True

        # HTTP write
        if "path" in write_def or "method" in write_def:
            from server.transport.http_client import HTTPClientTransport

            if not isinstance(self.transport, HTTPClientTransport):
                raise ConnectionError(
                    f"[{self.device_id}] Setting '{key}' uses HTTP write "
                    f"but transport is not HTTP"
                )

            method = write_def.get("method", "POST").upper()
            raw_path = write_def.get("path", "/")
            raw_body = write_def.get("body")
            headers = self._build_http_headers(
                write_def.get("headers"), all_params
            )

            path = self._safe_substitute(raw_path, all_params)

            json_body = None
            if raw_body:
                import json as _json
                body_str = self._safe_substitute(raw_body, all_params)
                try:
                    json_body = _json.loads(body_str)
                except (ValueError, _json.JSONDecodeError):
                    response = await self.transport.request(
                        method, path, content=body_str.encode("utf-8"),
                        headers=headers,
                    )
                    return response

            response = await self.transport.request(
                method, path, json_body=json_body, headers=headers
            )

            # Run response through pattern matching
            if hasattr(response, "text") and response.text:
                await self.on_data_received(response.text.encode("utf-8"))

            log.debug(
                f"[{self.device_id}] Set device setting '{key}' = {value!r}"
            )
            return response

        # TCP/serial write
        raw_send = write_def.get("send", "")
        if raw_send:
            if not self.transport or not self.transport.connected:
                raise ConnectionError(f"[{self.device_id}] Not connected")

            formatted = self._safe_substitute(raw_send, all_params)
            data = _safe_encode_escapes(formatted)
            await self.transport.send(data)
            log.debug(
                f"[{self.device_id}] Set device setting '{key}' = {value!r}"
            )
            return True

        raise NotImplementedError(
            f"Device setting '{key}' write definition has no path or send"
        )

    async def poll(self) -> None:
        """
        Send query strings from definition at configured interval.

        For HTTP transport, polling queries can be:
            - Command names (e.g., "get_status") — executes that command
            - URL paths (e.g., "/api/status") — sends a GET request
        For TCP/serial, queries are raw protocol strings as before.
        """
        if not self.transport or not self.transport.connected:
            return

        polling = self._definition.get("polling", {})
        queries = polling.get("queries", [])

        transport_type = self._definition.get("transport")
        is_osc = transport_type == "osc"

        for query in queries:
            try:
                if is_osc:
                    commands = self._definition.get("commands", {})
                    if query in commands:
                        await self.send_command(query)
                    else:
                        from server.transport.osc_codec import osc_encode_message
                        address = self._safe_substitute(query, self.config) if "{" in query else query
                        msg = osc_encode_message(address)
                        await self.transport.send(msg)
                else:
                    # HTTP/UDP resolve command names (so the response is matched);
                    # TCP/serial send the raw string. Shared with on_connect via
                    # _dispatch_query so the two paths can't drift apart.
                    await self._dispatch_query(query)
            except (ConnectionError, TimeoutError, OSError):
                # Transport-level failure: propagate so BaseDriver._poll_loop's
                # missed-poll watchdog counts it and can eventually mark the
                # device disconnected. Swallowing this is what let HTTP/OSC/UDP
                # devices report connected while unreachable. (HTTP connect
                # errors arrive here as builtin ConnectionError — http_client
                # translates httpx.ConnectError before it propagates.)
                log.warning(f"[{self.device_id}] Poll query failed (transport)")
                raise
            except _HTTP_TRANSPORT_ERRORS as exc:
                # httpx timeout / status / other transport errors are also
                # transport-level for the watchdog — re-raise, don't swallow.
                log.warning(f"[{self.device_id}] Poll query failed (HTTP): {exc}")
                raise
            except Exception as exc:  # Template substitution, encoding, parse errors
                # Protocol-level: the device answered but the query/response was
                # malformed. Surface device.error, don't penalize the watchdog,
                # and continue to the next query.
                log.exception(f"[{self.device_id}] Poll query error")
                try:
                    await self.events.emit(
                        f"device.error.{self.device_id}",
                        {"device_id": self.device_id, "error": str(exc)},
                    )
                except Exception:
                    log.exception(f"[{self.device_id}] Failed to emit device.error")

    def _create_frame_parser(self) -> FrameParser | None:
        """Check definition for frame parser config."""
        parser_config = self._definition.get("frame_parser")
        if not parser_config:
            return None

        parser_type = parser_config.get("type", "")
        if parser_type == "length_prefix":
            from server.transport.frame_parsers import LengthPrefixFrameParser

            return LengthPrefixFrameParser(
                header_size=parser_config.get("header_size", 2),
                header_offset=parser_config.get("header_offset", 0),
                include_header=parser_config.get("include_header", False),
            )
        elif parser_type == "fixed_length":
            from server.transport.frame_parsers import FixedLengthFrameParser

            return FixedLengthFrameParser(
                length=parser_config.get("length", 1),
            )

        return None

    @staticmethod
    def _coerce_value(raw: str, value_type: str) -> Any:
        """Convert a raw string to the specified type."""
        if value_type == "integer":
            try:
                return int(raw)
            except ValueError:
                log.warning("Cannot coerce %r to integer, returning raw string", raw)
                return raw
        elif value_type in ("float", "number"):
            try:
                return float(raw)
            except ValueError:
                log.warning("Cannot coerce %r to %s, returning raw string", raw, value_type)
                return raw
        elif value_type == "boolean":
            return raw.lower() in ("1", "true", "yes", "on")
        return raw  # string or enum


def create_configurable_driver_class(
    driver_def: dict[str, Any],
) -> type[ConfigurableDriver]:
    """
    Factory: create a ConfigurableDriver subclass from a JSON definition.

    Returns a new class with the correct DRIVER_INFO and _definition
    attributes, ready to be registered in the driver registry.
    """
    driver_id = driver_def.get("id", "unknown")

    _warn_legacy_keys_in_definition(driver_def)

    # Build DRIVER_INFO from the definition
    driver_info: dict[str, Any] = {
        "id": driver_id,
        "name": driver_def.get("name", driver_id),
        "manufacturer": driver_def.get("manufacturer", "Generic"),
        "category": driver_def.get("category", "utility"),
        "version": driver_def.get("version", "1.0.0"),
        "author": driver_def.get("author", "Community"),
        "description": driver_def.get("description", ""),
        "transport": driver_def.get("transport", "tcp"),
        "default_config": driver_def.get("default_config", {}),
        "config_schema": driver_def.get("config_schema", {}),
        "state_variables": driver_def.get("state_variables", {}),
    }

    # Copy help from driver definition
    if "help" in driver_def:
        driver_info["help"] = driver_def["help"]

    # Copy protocol declarations from driver definition
    if "protocols" in driver_def:
        driver_info["protocols"] = driver_def["protocols"]

    # Copy discovery hints from driver definition
    if "discovery" in driver_def:
        driver_info["discovery"] = driver_def["discovery"]

    # Copy device_settings from driver definition
    if "device_settings" in driver_def:
        driver_info["device_settings"] = driver_def["device_settings"]

    # Copy child_entity_types from the YAML definition. BaseDriver reads
    # this on register_child / set_child_state to validate properties and
    # platform-inject the synthetic `online` / `label` keys. The cloud
    # state-relay also reads it to honour per-property `cloud_priority`
    # tags (high/low) for tier selection.
    if "child_entity_types" in driver_def:
        driver_info["child_entity_types"] = driver_def["child_entity_types"]

    # Copy the bridge declaration — typed serial/IR/relay ports that other
    # devices connect *through*. Read by get_driver_bridge_ports() and the
    # bridge resolver (engine.resolved_device_config).
    if "bridge" in driver_def:
        driver_info["bridge"] = driver_def["bridge"]

    # Copy the multi-transport declaration (e.g. ["tcp", "serial"]). The
    # driver's command/response strings run identically over either medium;
    # the connection selects the actual transport (BaseDriver.connect reads
    # config["transport"] first). Enables "through a bridge" for serial-protocol
    # drivers that can also speak over a raw TCP pass-through.
    if "transports" in driver_def:
        driver_info["transports"] = driver_def["transports"]

    # Copy the inline-protocol opt-in. When true, the device page surfaces the
    # friendly Commands & Responses editor that writes commands / responses /
    # state_variables into the device config (the generic drivers set it).
    if "inline_protocol" in driver_def:
        driver_info["inline_protocol"] = driver_def["inline_protocol"]

    # Copy help from each state variable
    state_vars = driver_info.get("state_variables", {})
    for var_name, var_def in state_vars.items():
        if isinstance(var_def, dict) and "help" in var_def:
            state_vars[var_name] = {**var_def}

    # Build commands metadata for DRIVER_INFO (shared with the inline-protocol
    # per-instance merge so file- and config-authored commands look identical
    # in the IDE).
    driver_info["commands"] = _build_commands_meta(driver_def.get("commands", {}))

    # Copy the Quick Action declarations (promoted-command buttons + setup
    # wizards). Stored raw on DRIVER_INFO; resolve_device_actions folds
    # quick_actions sugar into the unified list at get_device_info time, so
    # YAML and Python drivers share one resolution path.
    if "actions" in driver_def:
        driver_info["actions"] = driver_def["actions"]
    if "quick_actions" in driver_def:
        driver_info["quick_actions"] = driver_def["quick_actions"]

    # Add delimiter if specified
    if "delimiter" in driver_def:
        driver_info["delimiter"] = driver_def["delimiter"]

    # Create a new class dynamically
    cls = type(
        f"ConfigurableDriver_{driver_id}",
        (ConfigurableDriver,),
        {
            "DRIVER_INFO": driver_info,
            "_definition": driver_def,
        },
    )

    return cls
