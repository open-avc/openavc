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
import time
from typing import Any

from server.drivers.base import (
    BaseDriver,
    ConnectionFaultError,
    normalize_and_validate_command_params as _normalize_and_validate_command_params,
)
from server.drivers.inline_protocol import (
    _derive_command_params,
    _derive_state_vars_from_responses,
    _normalize_config_commands,
    _normalize_config_responses,
    _normalize_config_state_vars,
    _normalize_ir_codes,
)
from server.transport.binary_helpers import encode_escape_sequences as _safe_encode_escapes
from server.transport.binary_helpers import pack_length_prefix
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
        #    (each with flat state mappings + compiled child_set entries)
        # 2. OSC address patterns for OSC responses
        # Every entry carries an optional throttle state ({window, last} or
        # None) — a rule with `throttle: <seconds>` skips re-fires inside its
        # window (drop-style; built for continuous push telemetry like audio
        # level meters, where every skipped frame is superseded by the next).
        self._compiled_responses: list[
            tuple[
                re.Pattern[str],
                list[dict[str, Any]],
                list[dict[str, Any]],
                dict[str, float] | None,
            ]
        ] = []
        self._osc_responses: list[
            tuple[str, list[dict[str, Any]], dict[str, float] | None]
        ] = []
        # JSON-body responses: each entry is a list of {state, key, type, map}
        # mappings applied together from one parsed JSON object (multi-field).
        self._json_responses: list[
            tuple[list[dict[str, Any]], dict[str, float] | None]
        ] = []

        # Telnet/serial login handshake state. Active only during
        # _perform_auth_handshake() — outside that window on_data_received
        # falls through to normal response matching.
        self._auth_mode: bool = False
        self._auth_buffer: bytearray = bytearray()
        self._auth_event: asyncio.Event = asyncio.Event()
        # Set by on_data_received when _auth_buffer exceeds _AUTH_MAX_BUFFER so
        # _auth_wait_for can abort the handshake instead of growing unbounded.
        self._auth_overflow: bool = False
        # Decoded-text offset of the last stage's match: each handshake stage
        # searches only bytes AFTER the previous stage's match, so a banner
        # containing "password" or an echoed credential can't satisfy a later
        # stage.
        self._auth_search_pos: int = 0
        # Computed by connect() (auth block present + _auth_should_run);
        # consumed by _post_connect to run the handshake pre-`connected`.
        self._auth_pending: bool = False

        # Declarative liveness watchdog (`liveness:` block): "send X every N,
        # await a reply within T, reconnect after K misses". Backs the
        # BaseDriver watchdog hook — _health_enabled()/_liveness_probe() below.
        # Needed for transports that can't self-detect a dead peer: UDP (a
        # fire-and-forget poll never notices silence), OSC, and push-mostly TCP
        # (no FIN when the device vanishes). The loader validates the block;
        # runtime parsing stays defensive so a hand-installed file can't crash
        # the driver.
        self._liveness_def: dict[str, Any] | None = None
        self._liveness_expect: re.Pattern[str] | None = None
        self._liveness_waiter: asyncio.Future[None] | None = None
        lv = self._definition.get("liveness")
        if isinstance(lv, dict) and isinstance(lv.get("send"), str) and lv["send"]:
            try:
                if lv.get("expect"):
                    self._liveness_expect = re.compile(str(lv["expect"]))
                self.HEALTH_INTERVAL_S = float(lv.get("interval", 30.0))
                self.HEALTH_TIMEOUT_S = float(lv.get("timeout", 5.0))
                self.HEALTH_MAX_FAILURES = int(lv.get("max_failures", 2))
                self._liveness_def = lv
            except (re.error, TypeError, ValueError) as e:
                log.warning(
                    f"[{self.device_id}] Invalid liveness block — watchdog "
                    f"disabled: {e}"
                )
                self._liveness_expect = None

        for resp in self._definition.get("responses", []):
            # OSC responses use "address" key instead of "pattern"/"match"
            if "address" in resp:
                addr = self._safe_substitute(resp["address"], self.config)
                # Copy per-instance: the source list lives in the shared class
                # _definition; aliasing it would let one instance's edits leak
                # into every instance of this driver type.
                mappings = list(resp.get("mappings", []))
                self._osc_responses.append(
                    (addr, mappings, self._build_throttle(resp))
                )
                continue

            # JSON-body response: parse the whole body once and map many keys
            # at a time. Additive — does not change the regex first-match path.
            # Optional `require:` scopes the rule to bodies carrying the named
            # key(s) — different endpoints on one REST device often reuse a
            # field name (`status`, `id`, `serialNumber`) with different
            # meanings, and an unscoped rule would misapply across them.
            if resp.get("json"):
                raw_require = resp.get("require")
                if isinstance(raw_require, str):
                    require = (raw_require,)
                elif isinstance(raw_require, list):
                    require = tuple(str(k) for k in raw_require if k)
                else:
                    require = ()
                self._json_responses.append(
                    (
                        self._build_json_mappings(resp),
                        self._build_throttle(resp),
                        require,
                    )
                )
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
                            # Static values coerce by the state var's declared
                            # type too — without it a boolean var fed
                            # `set: {mute: "true"}` stored the string "True".
                            mappings.append({
                                "group": 0,
                                "state": state_key,
                                "value": value_expr,
                                "type": var_type,
                            })

                child_mappings = self._compile_child_set(resp)
                self._compiled_responses.append(
                    (pattern, mappings, child_mappings, self._build_throttle(resp))
                )
            except re.error as e:
                log.warning(
                    f"[{self.device_id}] Invalid response pattern "
                    f"'{resp.get('match', resp.get('pattern', ''))}': {e}"
                )

    @staticmethod
    def _build_throttle(resp: dict[str, Any]) -> dict[str, float] | None:
        """Compile a response entry's optional ``throttle: <seconds>`` into a
        per-instance {window, last} state dict (None when absent/invalid).
        The loader validates the value up-front; runtime parsing stays
        defensive so a hand-installed file can't crash the driver."""
        raw = resp.get("throttle")
        if raw is None:
            return None
        try:
            window = float(raw)
        except (TypeError, ValueError):
            log.warning("Invalid response throttle %r ignored", raw)
            return None
        if window <= 0:
            return None
        return {"window": window, "last": float("-inf")}

    @staticmethod
    def _throttle_skip(tstate: dict[str, float] | None) -> bool:
        """Check-and-stamp: True when the rule fired inside its throttle
        window (the caller skips this application); otherwise records now as
        the last-fire time and returns False."""
        if not tstate:
            return False
        now = time.monotonic()
        if now - tstate["last"] < tstate["window"]:
            return True
        tstate["last"] = now
        return False

    def _compile_child_set(self, resp: dict[str, Any]) -> list[dict[str, Any]]:
        """Compile a response entry's ``child_set:`` list — route regex
        captures into child-entity state. Each entry is
        ``{type, id: $N | literal, state: {prop: $N | literal | {group/value/map/type}}}``.
        Value coercion uses the child type's declared ``state_variables``,
        mirroring the flat ``set:`` shorthand (static values coerce too).
        Malformed entries are skipped with a warning; the loader validates
        the same shape up-front so a catalog driver never gets here wrong.
        """
        raw = resp.get("child_set")
        if not isinstance(raw, list):
            return []
        child_types = self._definition.get("child_entity_types") or {}
        compiled: list[dict[str, Any]] = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            ctype = entry.get("type")
            tdef = child_types.get(ctype)
            if not isinstance(tdef, dict):
                log.warning(
                    f"[{self.device_id}] child_set: unknown child type "
                    f"{ctype!r}; skipping entry"
                )
                continue
            cvars = tdef.get("state_variables") or {}
            cid = entry.get("id")
            idspec: tuple[str, Any]
            if isinstance(cid, str) and cid.startswith("$"):
                try:
                    idspec = ("group", int(cid[1:]))
                except ValueError:
                    log.warning(
                        f"[{self.device_id}] child_set: id {cid!r} is not a "
                        f"numeric capture ref ($1, $2, ...); skipping entry"
                    )
                    continue
            elif cid is not None:
                idspec = ("literal", cid)
            else:
                log.warning(
                    f"[{self.device_id}] child_set: missing 'id'; skipping entry"
                )
                continue
            state_map = entry.get("state")
            if not isinstance(state_map, dict):
                continue
            props: list[dict[str, Any]] = []
            for prop, expr in state_map.items():
                var_def = cvars.get(prop, {})
                var_type = (
                    var_def.get("type", "string")
                    if isinstance(var_def, dict)
                    else "string"
                )
                if isinstance(expr, dict):
                    pm: dict[str, Any] = {"prop": prop, "type": expr.get("type", var_type)}
                    if "group" in expr:
                        try:
                            pm["group"] = int(expr["group"])
                        except (TypeError, ValueError):
                            continue
                    elif "value" in expr:
                        pm["value"] = expr["value"]
                    else:
                        continue
                    if isinstance(expr.get("map"), dict):
                        pm["map"] = expr["map"]
                    props.append(pm)
                elif isinstance(expr, str) and expr.startswith("$"):
                    try:
                        group = int(expr[1:])
                    except ValueError:
                        log.warning(
                            f"[{self.device_id}] child_set: state '{prop}' ref "
                            f"{expr!r} is not a numeric capture ref; skipping"
                        )
                        continue
                    props.append({"prop": prop, "group": group, "type": var_type})
                else:
                    # Static value — coerce by declared type like flat set:.
                    props.append({"prop": prop, "value": expr, "type": var_type})
            if props:
                compiled.append({"type": ctype, "id": idspec, "props": props})
        return compiled

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

        # Send-side command framing (opt-in, byte-stream only). A constant
        # command_prefix / command_suffix wraps every command so a text protocol
        # with a fixed packet header + terminator is authored once, not per
        # command. File-authored commands are wrapped at send time (send_command,
        # gated on _inline_command_names); inline/device-config commands are
        # wrapped here so their poll queries + the auto-simulator see the framed
        # form. command_suffix falls back to the delimiter for inline commands
        # only — never for file commands, whose send strings already carry their
        # terminator (a delimiter fallback there would double-terminate them).
        self._command_prefix = (
            self.config.get("command_prefix")
            or self._definition.get("command_prefix")
            or ""
        )
        self._command_suffix = (
            self.config.get("command_suffix")
            or self._definition.get("command_suffix")
            or ""
        )

        # Send-side packet framing (opt-in, byte-stream only). A length_prefix
        # send_frame wraps the fully-framed command payload (command_prefix +
        # send + command_suffix, escape-decoded) in a binary packet header whose
        # data-length field is COMPUTED per message — the send analog of a
        # length_prefix frame_parser. Needed for protocols like eISCP whose
        # 16-byte header carries a computed data-length a static command_prefix
        # can't express (the length varies once feedback queries and step
        # commands of different byte lengths are in the set). Applied at every
        # byte-stream send origin (command, raw query, liveness probe, device
        # setting write); absent means today's behavior exactly.
        self._send_frame = self._build_send_frame(self._definition.get("send_frame"))

        norm_commands = _normalize_config_commands(
            self.config.get("commands"),
            self._command_suffix or line_ending,
            prefix=self._command_prefix,
        )
        # File commands (not in this set) are the ones send_command frames.
        self._inline_command_names: set[str] = set(norm_commands)
        norm_responses = _normalize_config_responses(self.config.get("responses"))
        norm_state_vars = _normalize_config_state_vars(
            self.config.get("state_variables")
        )
        # IR code-set → commands. The effective code-set is the driver-declared
        # default_config.ir_codes (a community IR driver's shipped codes, already
        # layered into self.config by resolved_device_config) overlaid with any
        # device-authored ir_codes. Each becomes an IR command emitted through
        # the bound bridge (see send_command). Config codes win by name.
        ir_commands = _normalize_ir_codes(self.config.get("ir_codes"))

        if not (norm_commands or norm_responses or norm_state_vars or ir_commands):
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

        merged_commands = {**file_commands, **norm_commands, **ir_commands}
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

        The declarative `auth:` login handshake runs inside _post_connect()
        — i.e. BEFORE BaseDriver reports the device connected — so
        `connected` (and the device.connected event, and every trigger or
        panel indicator riding on it) means "logged in", never "socket open
        with a pending login". A wrong credential fails the connect attempt
        outright instead of flapping the device online/offline through the
        reconnect backoff.
        """
        saved_poll_interval = self.config.get("poll_interval", 0)
        self.config["poll_interval"] = 0

        # Enable auth-buffering BEFORE the TCP connect so any prompt the
        # device emits the moment the connection opens lands in the auth
        # buffer instead of being run through the normal response matcher.
        # _perform_auth_handshake() turns this back off when it's done.
        auth_def = self._definition.get("auth")
        self._auth_pending = isinstance(auth_def, dict) and self._auth_should_run(auth_def)
        if self._auth_pending:
            self._auth_buffer = bytearray()
            self._auth_event = asyncio.Event()
            self._auth_overflow = False
            self._auth_search_pos = 0
            self._auth_mode = True

        try:
            await super().connect()
        except Exception:
            self._auth_mode = False
            raise
        finally:
            # Restore even when the attempt fails: leaving the zeroed value
            # in config would make the NEXT connect() save 0 as the interval
            # to restore, permanently disabling polling after one bad attempt.
            self.config["poll_interval"] = saved_poll_interval

        # Declarative child rosters (`instances:` blocks): register/reconcile
        # before on_connect and polling so routed responses land on
        # registered children from the first query.
        try:
            self._register_declared_children()
        except Exception:
            log.exception(
                f"[{self.device_id}] Failed to register declared children"
            )

        on_connect = self._definition.get("on_connect", [])
        if on_connect and self.transport and self.transport.connected:
            transport_type = self._definition.get("transport")
            delay = self.config.get("inter_command_delay", 0)

            if transport_type == "osc":
                from server.transport.osc_codec import osc_encode_message
                for item in on_connect:
                    try:
                        if isinstance(item, dict) and "each_child" in item:
                            for expanded in self._expand_query(item):
                                address = (
                                    self._safe_substitute(expanded, self.config)
                                    if "{" in expanded
                                    else expanded
                                )
                                await self.transport.send(osc_encode_message(address))
                                if delay:
                                    await asyncio.sleep(delay)
                            continue
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
                for addr_pattern, _mappings, _tstate in self._osc_responses:
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
                    for query in self._expand_query(raw):
                        try:
                            await self._dispatch_query(query)
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
        TCP/serial resolve a command name the same way (so send-side framing
        applies and the response is matched), else send the raw protocol string.
        OSC is handled by the callers.
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
            if query in commands:
                # A query that names a command runs as that command, so send-side
                # framing (command_prefix/suffix + send_frame) applies and the
                # response goes through the matcher — matches the HTTP/UDP
                # branches above. A raw protocol string (not a command name) is
                # sent as authored, still wrapped in the send_frame packet header
                # (no-op unless declared) so a length-framed protocol's raw
                # queries reach the device correctly.
                await self.send_command(query)
            else:
                formatted = self._safe_substitute(query, self.config) if "{" in query else query
                await self.transport.send(
                    self._apply_send_frame(_safe_encode_escapes(formatted))
                )

    def _expand_query(self, query: Any) -> list[str]:
        """Expand one polling/on_connect entry. Strings pass through
        unchanged; an ``{each_child: <type>, send: <template>}`` dict yields
        one query per registered child of that type with ``{child_id}``
        substituted (the unpadded local id). Anything else is skipped with a
        warning.
        """
        if isinstance(query, str):
            return [query]
        if isinstance(query, dict) and "each_child" in query:
            ctype = query.get("each_child")
            template = query.get("send")
            if not isinstance(ctype, str) or not isinstance(template, str) or not template:
                log.warning(
                    f"[{self.device_id}] each_child query needs 'each_child' "
                    f"+ 'send'; skipping"
                )
                return []
            return [
                template.replace("{child_id}", str(local_id))
                for local_id in self.list_children(ctype)
            ]
        log.warning(
            f"[{self.device_id}] Unrecognized query entry {query!r}; skipping"
        )
        return []

    def _coerce_child_local_id(self, child_type: str, raw: Any) -> int | str | None:
        """Coerce a routed child id (regex capture or literal) to the type's
        declared ``id_format`` — int for integer ids, stripped string
        otherwise. Returns None when it can't be coerced."""
        child_types = self._definition.get("child_entity_types") or {}
        tdef = child_types.get(child_type) or {}
        id_format = tdef.get("id_format") or {}
        if id_format.get("type", "integer") == "integer":
            if isinstance(raw, bool):
                return None
            if isinstance(raw, int):
                return raw
            try:
                return int(str(raw).strip())
            except (TypeError, ValueError):
                return None
        text = str(raw).strip()
        return text or None

    def _resolve_instance_ids(
        self, ctype: str, tdef: dict[str, Any], inst: dict[str, Any]
    ) -> list[int | str] | None:
        """Resolve an ``instances:`` block to the wanted local-id list.
        ``count`` = fixed ids 1..N; ``count_from`` = an integer config field;
        ``ids_from`` = a comma-separated config field (sparse / string ids).
        Returns None when the declaration can't be resolved (warned)."""
        id_format = tdef.get("id_format") or {}
        id_type = id_format.get("type", "integer")
        if "count" in inst or "count_from" in inst:
            if id_type != "integer":
                log.warning(
                    f"[{self.device_id}] instances: count/count_from require "
                    f"integer ids ({ctype} declares string ids — use ids_from)"
                )
                return None
            if "count" in inst:
                raw = inst["count"]
            else:
                raw = self.config.get(inst.get("count_from"), "")
            try:
                n = int(str(raw).strip())
            except (TypeError, ValueError):
                log.warning(
                    f"[{self.device_id}] instances: {ctype} count {raw!r} is "
                    f"not an integer"
                )
                return None
            if n < 0:
                return None
            return list(range(1, n + 1))
        field = inst.get("ids_from")
        if not isinstance(field, str) or not field:
            return None
        raw_list = str(self.config.get(field, "") or "")
        ids: list[int | str] = []
        for token in raw_list.split(","):
            token = token.strip()
            if not token:
                continue
            if id_type == "integer":
                try:
                    ids.append(int(token))
                except ValueError:
                    log.warning(
                        f"[{self.device_id}] instances: {ctype} id {token!r} "
                        f"from '{field}' is not an integer; skipping"
                    )
            else:
                ids.append(token)
        return ids

    def _register_declared_children(self) -> dict[str, int]:
        """Register/reconcile children declared via an ``instances:`` block on
        a ``child_entity_types`` entry. Reconcile is config-want-set: register
        the wanted ids, deregister anything registered but no longer wanted.
        Idempotent per connect (re-registering an existing child is a no-op).
        An ``instances.label`` template seeds the initial label, but never
        overrides a label the user set in the project."""
        counts: dict[str, int] = {}
        child_types = self._definition.get("child_entity_types") or {}
        for ctype, tdef in child_types.items():
            if not isinstance(tdef, dict):
                continue
            inst = tdef.get("instances")
            if not isinstance(inst, dict):
                continue
            ids = self._resolve_instance_ids(ctype, tdef, inst)
            if ids is None:
                continue
            wanted = set(ids)
            for local_id in list(self.list_children(ctype)):
                if local_id not in wanted:
                    self.deregister_child(ctype, local_id)
            label_template = inst.get("label")
            registered = 0
            for local_id in ids:
                initial: dict[str, Any] | None = None
                if isinstance(label_template, str) and label_template:
                    try:
                        padded = self._format_child_id(ctype, local_id)
                    except (TypeError, ValueError):
                        padded = None
                    project_entry = (
                        self._project_child_entities.get(ctype, {}).get(padded)
                        if padded is not None
                        else None
                    )
                    if not (project_entry and project_entry.get("label")):
                        initial = {
                            "label": label_template.replace("{id}", str(local_id))
                        }
                try:
                    self.register_child(ctype, local_id, initial_state=initial)
                    registered += 1
                except (TypeError, ValueError) as e:
                    log.warning(
                        f"[{self.device_id}] instances: could not register "
                        f"{ctype} {local_id!r}: {e}"
                    )
            counts[ctype] = registered
        return counts

    async def refresh_children(self) -> dict[str, int]:
        """Re-derive declared child rosters from config (the IDE's "Refresh
        from Device" button). Only meaningful when at least one child type
        declares ``instances:``; otherwise defer to BaseDriver (raises
        NotImplementedError so the API reports refresh as unsupported)."""
        child_types = self._definition.get("child_entity_types") or {}
        has_instances = any(
            isinstance(tdef, dict) and isinstance(tdef.get("instances"), dict)
            for tdef in child_types.values()
        )
        if not has_instances:
            return await super().refresh_children()
        return self._register_declared_children()

    async def _post_connect(self) -> None:
        """Run the declarative `auth:` login handshake before BaseDriver
        reports the device connected.

        A raise here propagates to BaseDriver.connect(), which stashes the
        transport error, closes the transport, and fails the attempt — so a
        device with a rejected login never sets `connected` or emits
        device.connected.
        """
        await super()._post_connect()
        if not self._auth_pending:
            self._auth_mode = False
            return
        transport = self.transport
        if transport is None or not getattr(transport, "connected", False):
            # Transport died between create and here; BaseDriver's failure
            # paths own the cleanup. Nothing to authenticate against.
            self._auth_mode = False
            return
        # Many login prompts arrive without the protocol's delimiter (e.g.
        # bare "Login: "), so the transport's delimiter-based frame parser
        # would buffer them indefinitely. Drop the parser for the duration
        # of the handshake; _perform_auth_handshake's finally restores it
        # on every path (success, rejection, timeout, exception).
        saved_parser = getattr(transport, "_frame_parser", None)
        if hasattr(transport, "_frame_parser"):
            # If the parser had buffered any pre-auth bytes, flush them
            # into the auth buffer so we don't lose the prompt.
            if saved_parser is not None and hasattr(saved_parser, "_buffer"):
                pending = bytes(saved_parser._buffer)
                if pending:
                    self._auth_buffer.extend(pending)
                    self._auth_event.set()
                    saved_parser._buffer = b""
            transport._frame_parser = None  # type: ignore[union-attr]
        self._saved_frame_parser = saved_parser
        try:
            await self._perform_auth_handshake()
        except Exception as e:
            log.error(f"[{self.device_id}] Auth handshake failed: {e}")
            raise

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

        # _post_connect() already swapped the transport to raw mode and
        # stashed the original parser on self._saved_frame_parser. We just
        # restore it in the finally block below.
        saved_parser = getattr(self, "_saved_frame_parser", None)

        try:
            ending = _safe_encode_escapes(line_ending)
            log.info(f"[{self.device_id}] Starting auth handshake")
            self._auth_search_pos = 0

            # Stage 1: wait for username prompt, send username.
            await self._auth_wait_for(user_re, failure_re, timeout, stage="username_prompt")
            await self.transport.send(username.encode("utf-8") + ending)
            log.debug(f"[{self.device_id}] Auth: sent username")

            # Stage 2: wait for password prompt, send password.
            await self._auth_wait_for(pass_re, failure_re, timeout, stage="password_prompt")
            await self.transport.send(password.encode("utf-8") + ending)
            log.debug(f"[{self.device_id}] Auth: sent password")

            # Stage 3: optionally wait for a success indicator. Without one,
            # we assume success once the password is sent (the next command
            # sent will fail visibly if auth was rejected).
            if success_re is not None:
                await self._auth_wait_for(success_re, failure_re, timeout, stage="success")
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
        stage: str = "prompt",
    ) -> None:
        """Wait until `target` regex matches the auth bytes received since
        the previous stage's match.

        Each stage searches only text AFTER the previous match
        (self._auth_search_pos), so a banner that mentions "password", or an
        echoed credential, can't falsely satisfy a later stage. Patterns are
        string regexes matched against the buffer's UTF-8 decoding
        (errors=replace).

        Raises a typed ConnectionFaultError so the offline reason is
        precise: auth_failed when the device rejected the login (failure
        pattern matched, or silence only AFTER the credentials were sent),
        no_response when the device never presented the expected prompt at
        all (wrong host, port, or protocol — not a credential problem).
        """
        creds_sent = stage == "success"
        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            # Clear the event BEFORE inspecting the buffer so we don't drop
            # a set() that arrives between check and clear. If new data
            # arrives between clear and check, the buffer already contains
            # it; if it arrives after the check, the set() will unblock
            # the wait() below.
            self._auth_event.clear()
            if self._auth_overflow:
                raise ConnectionFaultError(
                    f"auth aborted: more than {_AUTH_MAX_BUFFER} bytes "
                    f"received before matching {target.pattern!r} — wrong "
                    f"protocol for this device?",
                    code="no_response",
                )
            text = self._auth_buffer.decode("utf-8", errors="replace")[
                self._auth_search_pos:
            ]
            if failure is not None and failure.search(text):
                raise ConnectionFaultError(
                    f"login rejected by the device "
                    f"(matched failure pattern in {text!r})",
                    code="auth_failed",
                )
            m = target.search(text)
            if m is not None:
                # Consume through the match so the next stage can't be
                # satisfied by bytes that arrived before this one.
                self._auth_search_pos += m.end()
                return

            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                break
            try:
                await asyncio.wait_for(self._auth_event.wait(), timeout=remaining)
            except asyncio.TimeoutError:
                break

        if creds_sent:
            # The prompts flowed and the credentials went out, but the
            # success indicator never came: treat as a rejected login.
            raise ConnectionFaultError(
                f"no login confirmation after sending credentials (timeout "
                f"waiting for {target.pattern!r}; got {text!r}) — check the "
                f"username and password.",
                code="auth_failed",
            )
        raise ConnectionFaultError(
            f"the device never presented the expected login prompt (timeout "
            f"waiting for {target.pattern!r}; got {text!r}) — wrong host, "
            f"port, or protocol for this device?",
            code="no_response",
        )

    async def send_command(
        self, command: str, params: dict[str, Any] | None = None
    ) -> Any:
        """Look up command in definition, substitute params, send."""
        params = params or {}

        commands = self._definition.get("commands", {})
        cmd_def = commands.get(command)

        # IR code-set command: routes through the bound bridge's emitter port,
        # not a transport of its own. Handle before the transport-connected gate
        # below (a bridge-routed IR device has no transport). The bridge driver
        # converts the Pronto code to its wire format and confirms the emit.
        if isinstance(cmd_def, dict) and cmd_def.get("ir"):
            ir = cmd_def["ir"]
            return await self.emit_via_bridge(
                "ir",
                {"pronto": ir.get("pronto", ""), "repeat": ir.get("repeat", 1)},
            )

        if not self.transport or not self.transport.connected:
            raise ConnectionError(f"[{self.device_id}] Not connected")

        if cmd_def is None:
            log.warning(f"[{self.device_id}] Unknown command: {command}")
            return None

        # Runtime gate: trim + validate the supplied params against the
        # command's declared schema (covers the TCP/serial, OSC, and HTTP
        # branches below). Raises CommandParamError on a bad value.
        params = _normalize_and_validate_command_params(
            command, cmd_def.get("params", {}), params
        )

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

        # Send-side framing (opt-in): wrap a file-authored command with the
        # driver's command_prefix / command_suffix so a fixed packet header +
        # terminator is declared once, not per command. Inline/device-config
        # commands are already framed at merge time (skipped via
        # _inline_command_names); a command may opt out with raw: true to go on
        # the wire exactly as written.
        prefix = getattr(self, "_command_prefix", "")
        suffix = getattr(self, "_command_suffix", "")
        if (
            (prefix or suffix)
            and command not in getattr(self, "_inline_command_names", ())
            and not cmd_def.get("raw")
        ):
            raw = f"{prefix}{raw}{suffix}"

        # Substitute {param} placeholders — merge config values so drivers
        # can use config fields like {set_id} or {level_instance_tag} in commands.
        # Uses _safe_substitute to handle JSON protocols (UDP) where literal
        # braces must be preserved — only {name} tokens matching known params
        # are replaced, all other braces are left alone.
        all_params = {**self.config, **params}
        formatted = self._safe_substitute(raw, all_params)

        # Encode (handle explicit escape sequences only — safe subset), then
        # wrap in the send_frame packet header (no-op unless declared).
        data = self._apply_send_frame(_safe_encode_escapes(formatted))
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
                # An integer spec ('d'/'x'/'X'/'o'/'b') rejects a float even when
                # it's whole (26.0), which is exactly what a scaled slider value
                # is. Coerce a whole-number float to int and retry so {vol:d}
                # renders "26".
                if isinstance(value, float) and not isinstance(value, bool) and value.is_integer():
                    try:
                        return format(int(value), spec)
                    except (ValueError, TypeError):
                        pass
                # A numeric spec applied to a numeric string: coerce, then
                # format. int first so '02d' works on "5".
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

    # --- Declarative liveness watchdog (`liveness:` block) ---

    def _health_enabled(self) -> bool:
        """The BaseDriver watchdog runs only when the YAML declares a
        `liveness:` block (this class always overrides the probe, so the
        default is-overridden check would wrongly enable it for every
        declarative driver)."""
        return self._liveness_def is not None

    async def _liveness_probe(self) -> None:
        """Send the declared probe and wait for a qualifying reply.

        Any inbound frame counts as alive — a poll reply or an unsolicited
        push arriving during the wait window proves the device is there just
        as well as a direct answer. An `expect` regex narrows that to matching
        frames only. The reply deadline is enforced by the BaseDriver loop
        (HEALTH_TIMEOUT_S wraps this coroutine), which cancels the await on
        timeout; the finally clears the waiter either way.
        """
        lv = self._liveness_def
        if lv is None or self.transport is None:
            return
        waiter: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._liveness_waiter = waiter
        try:
            await self._send_liveness_probe(lv)
            await waiter
        finally:
            if self._liveness_waiter is waiter:
                self._liveness_waiter = None

    async def _send_liveness_probe(self, lv: dict[str, Any]) -> None:
        """Transmit the probe payload for this driver's transport type.

        `send` follows the same conventions as `polling.queries`: a raw
        protocol string (escape sequences + {config} substitution, terminator
        included) for tcp/serial/udp, an OSC address (with optional `args`)
        for osc.
        """
        if self._definition.get("transport") == "osc":
            from server.transport.osc_codec import osc_encode_message

            address = lv["send"]
            if "{" in address:
                address = self._safe_substitute(address, self.config)
            args = self._build_osc_args(lv.get("args", []), self.config)
            await self.transport.send(osc_encode_message(address, args))
        else:
            payload = lv["send"]
            if "{" in payload:
                payload = self._safe_substitute(payload, self.config)
            # command_prefix/suffix are NOT applied (the probe is a raw string —
            # the author writes any application-layer framing into it), but the
            # send_frame packet header IS wrapped (no-op unless declared): a
            # length-framed transport like eISCP needs its header on every
            # message, or the probe never elicits a reply and liveness fails.
            await self.transport.send(
                self._apply_send_frame(_safe_encode_escapes(payload))
            )

    def _liveness_note_data(self, data: bytes) -> None:
        """Resolve a waiting liveness probe when qualifying data arrives.

        Called from on_data_received before normal dispatch; never consumes
        the data. The expect regex (if any) is matched against a permissive
        text decode so it works for binary-ish payloads too (an OSC packet
        leads with its ASCII address, so address patterns still match).
        """
        waiter = self._liveness_waiter
        if waiter is None or waiter.done():
            return
        if self._liveness_expect is not None:
            text = data.decode("utf-8", errors="replace")
            if not self._liveness_expect.search(text):
                return
        waiter.set_result(None)

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

        # A waiting liveness probe is satisfied by any qualifying inbound
        # frame; the data still flows through normal dispatch below.
        self._liveness_note_data(data)

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

        for pattern, mappings, child_mappings, tstate in self._compiled_responses:
            match = pattern.search(text)
            if match:
                # A throttled rule consumes its frame without applying it —
                # falling through would let a later rule match the same frame.
                if self._throttle_skip(tstate):
                    log.debug(
                        f"[{self.device_id}] Response throttled: "
                        f"{pattern.pattern}"
                    )
                    return
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

                if child_mappings:
                    self._apply_child_mappings(match, child_mappings)

                log.debug(
                    f"[{self.device_id}] Response matched: {pattern.pattern}"
                )
                return  # Stop at first match

        log.debug(f"[{self.device_id}] Unmatched response: {text!r}")

    def _apply_child_mappings(
        self, match: re.Match[str], child_mappings: list[dict[str, Any]]
    ) -> None:
        """Route a matched response's captures into child-entity state
        (``child_set:``). Each entry resolves its child id (capture ref or
        literal), coerces each prop by the child schema's declared type, and
        applies one ``set_child_state_batch`` per child. Unregistered ids are
        skipped quietly — devices legitimately answer for ports beyond a
        user-configured roster."""
        for cm in child_mappings:
            ctype = cm["type"]
            id_kind, id_val = cm["id"]
            if id_kind == "group":
                try:
                    raw_id = match.group(id_val)
                except (IndexError, re.error):
                    continue
                if raw_id is None:
                    continue
            else:
                raw_id = id_val
            local_id = self._coerce_child_local_id(ctype, raw_id)
            if local_id is None:
                continue
            if not self.is_child_registered(ctype, local_id):
                log.debug(
                    f"[{self.device_id}] child_set: {ctype} {local_id!r} not "
                    f"registered — skipping"
                )
                continue
            updates: dict[str, Any] = {}
            for pm in cm["props"]:
                value_type = pm.get("type", "string")
                if "value" in pm:
                    updates[pm["prop"]] = self._coerce_value(
                        str(pm["value"]), value_type
                    )
                    continue
                try:
                    raw_value = match.group(pm.get("group", 0))
                except (IndexError, re.error):
                    continue
                if raw_value is None:
                    continue
                value_map = pm.get("map")
                if value_map and raw_value in value_map:
                    updates[pm["prop"]] = self._coerce_value(
                        str(value_map[raw_value]), value_type
                    )
                else:
                    updates[pm["prop"]] = self._coerce_value(raw_value, value_type)
            if updates:
                self.set_child_state_batch(ctype, local_id, updates)

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
            for addr_pattern, mappings, tstate in self._osc_responses:
                if not fnmatch.fnmatch(address, addr_pattern):
                    continue
                matched = True
                if self._throttle_skip(tstate):
                    log.debug(
                        f"[{self.device_id}] OSC response throttled: "
                        f"{addr_pattern}"
                    )
                    break
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

        A single-element top-level array (``[{...}]``) is unwrapped to its one
        object — several device protocols wrap every reply that way (per-unit
        addressing with one unit per datagram). Multi-element arrays are
        ambiguous and still fall through to regex matching.
        """
        try:
            obj = json.loads(text)
        except (ValueError, TypeError):
            return False
        if isinstance(obj, list) and len(obj) == 1 and isinstance(obj[0], dict):
            obj = obj[0]
        if not isinstance(obj, dict):
            return False
        applied = False
        throttled = False
        for mappings, tstate, require in self._json_responses:
            # A rule scoped by `require:` applies only to bodies carrying all
            # of the named keys — without the scope, endpoints that reuse a
            # field name (a paired peripheral's `status` vs the main unit's)
            # would cross-write each other's state.
            if require and not all(
                self._extract_json_path(obj, key) is not _JSON_PATH_MISSING
                for key in require
            ):
                continue
            # Resolve first so a body without this rule's keys neither applies
            # nor stamps the rule's throttle window.
            resolved: list[tuple[dict[str, Any], Any]] = []
            for mapping in mappings:
                state_key = mapping.get("state")
                key = mapping.get("key")
                if not state_key or not key:
                    continue
                value = self._extract_json_path(obj, key)
                if value is _JSON_PATH_MISSING:
                    continue
                resolved.append((mapping, value))
            if not resolved:
                continue
            if self._throttle_skip(tstate):
                # Matched but inside the throttle window: consume the body
                # (return True below) without writing state.
                throttled = True
                continue
            for mapping, value in resolved:
                value_map = mapping.get("map")
                if value_map and str(value) in value_map:
                    coerced = self._coerce_value(
                        str(value_map[str(value)]), mapping.get("type", "string")
                    )
                else:
                    coerced = self._coerce_json_value(value, mapping.get("type", "string"))
                self.set_state(mapping["state"], coerced)
                applied = True
        if applied:
            log.debug(
                f"[{self.device_id}] JSON response applied "
                f"({len(self._json_responses)} rule(s))"
            )
        return applied or throttled

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
            data = self._apply_send_frame(_safe_encode_escapes(formatted))
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

        for raw_query in queries:
            # each_child entries expand to one query per registered child.
            for query in self._expand_query(raw_query):
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

    @staticmethod
    def _build_send_frame(cfg: Any) -> dict[str, Any] | None:
        """Precompute a send_frame block's constant header bytes, or None.

        The header/after_length strings are escape-decoded once here (they carry
        raw bytes like eISCP's ``ISCP\\x00\\x00\\x00\\x10`` magic + header-size).
        Only ``length_prefix`` is supported; any other type is ignored with a
        warning so an unknown block never silently breaks sends.
        """
        if not cfg or not isinstance(cfg, dict):
            return None
        frame_type = cfg.get("type", "length_prefix")
        if frame_type != "length_prefix":
            log.warning(
                "Unsupported send_frame type %r; ignoring (only 'length_prefix')",
                frame_type,
            )
            return None
        return {
            "header": _safe_encode_escapes(str(cfg.get("header", "") or "")),
            "after_length": _safe_encode_escapes(str(cfg.get("after_length", "") or "")),
            "length_size": int(cfg.get("length_size", 4)),
            "length_endian": "little" if cfg.get("length_endian") == "little" else "big",
        }

    def _apply_send_frame(self, data: bytes) -> bytes:
        """Wrap escape-decoded byte-stream payload in the send_frame header.

        No-op unless the driver declares a send_frame block. The data-length
        field is computed from ``len(data)`` per message — the piece a static
        command_prefix can't express. Output is
        ``header + packed_length + after_length + data``.
        """
        sf = self._send_frame
        if not sf:
            return data
        length = pack_length_prefix(len(data), sf["length_size"], sf["length_endian"])
        return sf["header"] + length + sf["after_length"] + data

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
                length_offset=parser_config.get("length_offset", 0),
                header_extra=parser_config.get("header_extra", 0),
                length_endian=parser_config.get("length_endian", "big"),
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

    # Copy the push-notification declaration (e.g. a device that multicasts
    # state-change frames). BaseDriver reads this on connect to subscribe the
    # shared listener; frames feed the normal response dispatch.
    if "push" in driver_def:
        driver_info["push"] = driver_def["push"]

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

    # Copy the IR code-set opt-in. When true, the device page surfaces the IR
    # Codes editor (learn / paste Pronto / type sendir / DB search / test emit),
    # writing the code-set into the device config's ir_codes map. generic_ir
    # sets it for build-your-own devices; a community IR driver sets it too and
    # ships its codes in default_config.ir_codes.
    if "ir_codes" in driver_def:
        driver_info["ir_codes"] = driver_def["ir_codes"]

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
