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

Whole jobs live in mixins ``ConfigurableDriver`` inherits, so every name
stays on the driver object. Reading the device's replies: the regex rules
over a line of text (``text_replies``), the JSON-body rules and the
``json_path`` walk (``json_replies``), OSC commands and reply rules
(``osc_wire``), what a reply's value becomes and which child it belongs to
(``reply_values``), and a rule's throttle (``reply_throttle``). Around the
connection: the ``auth:`` sign-in (``sign_in``) and the children an
``instances:`` block declares (``declared_roster``).
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any

from openavc.core.condition_eval import eval_operator, reads_as_on
from openavc.core.state_store import VALID_KEY_PREFIXES
from openavc.drivers import compiled_protocol
from openavc.drivers.base import (
    UNKNOWN_COMMAND,
    WAKE_ON_LAN_PORT,
    BaseDriver,
    ConnectionFaultError,
    UnknownDeviceSettingError,
    WakeAddressError,
    normalize_and_validate_command_params as _normalize_and_validate_command_params,
    normalize_mac,
)
from openavc.drivers.child_ids import page_starts
from openavc.drivers.param_labels import param_label
from openavc.drivers.spec import (
    DEFAULT_LINE_GAP_S,
    FOLLOW_UP_SETTLE_S,
    LINE_GAP_TRANSPORTS,
)
from openavc.drivers.declared_roster import DeclaredRoster
from openavc.drivers.json_replies import JsonReplies
from openavc.drivers.osc_wire import OscWire
from openavc.drivers.reply_throttle import ReplyThrottle
from openavc.drivers.reply_values import ReplyValues
from openavc.drivers.sign_in import SignIn
from openavc.drivers.text_replies import TextReplies
from openavc.drivers.inline_protocol import (
    declare_command_params,
    derive_state_vars_from_responses,
    normalize_config_commands,
    normalize_config_responses,
    normalize_config_state_vars,
    normalize_ir_codes,
)
from openavc.transport.binary_helpers import encode_escape_sequences as _safe_encode_escapes
from openavc.transport.frame_parsers import FrameParser
from openavc.utils.logger import get_logger
from openavc.transport.pacing import pacing_clock, sleep_at_least

log = get_logger(__name__)

# httpx transport/timeout/status errors are transport-level for missed-poll
# watchdog purposes (mirrors BaseDriver._poll_loop). poll() re-raises these so
# an unreachable HTTP device trips the watchdog instead of looking connected.
try:
    import httpx as _httpx

    _HTTP_TRANSPORT_ERRORS: tuple[type[BaseException], ...] = (_httpx.HTTPError,)
except ImportError:  # pragma: no cover - httpx is a hard dependency in practice
    _HTTP_TRANSPORT_ERRORS = ()

# HTTP statuses that mean "the device refused this request's credentials": 401
# from the device itself, 407 from a proxy in front of it, 403 for the devices
# that answer a bad login with Forbidden rather than Unauthorized. One of these
# on its own is not a verdict: devices refuse an endpoint the account may not
# read while the credentials are fine. _judge_http_answer() and poll() decide.
_HTTP_AUTH_REJECT_STATUS = frozenset({401, 403, 407})

# The two of those that say the credential itself did not authenticate. A
# query that answered on this connection and now gets one of them means the
# password changed or the account went, so nothing more is sent. A 403 there
# names a resource the account may no longer use and is left to the
# whole-cycle check in poll().
_HTTP_CREDENTIAL_REFUSED_STATUS = frozenset({401, 407})

# Distinct queries refused, with nothing yet answered on the connection, that
# settle a wrong password. Every refused request counts toward a device's
# lockout, so this is kept low; it is above one because an account is often
# refused an endpoint or two it may not read while the password is right, and
# those may come first in a driver's start-up or poll list.
_HTTP_REFUSALS_BEFORE_ANY_ANSWER = 3



def _build_commands_meta(commands_def: dict[str, Any]) -> dict[str, Any]:
    """Build the DRIVER_INFO ``commands`` UI metadata from a commands map.

    Shared by the driver-class factory (file-authored commands) and the
    per-instance inline-protocol merge (device-config-authored commands) so
    both present identically in the IDE — same label, params, and
    transport-specific fields (HTTP method/path/body, OSC address/args).

    ``available_offline`` is carried for a different reason than the rest,
    and the difference is why it was missed: every other command field is
    read off the raw definition at send time, but the connected-gate and a
    promoted button's availability both read DRIVER_INFO
    (``command_gates.available_offline``,
    ``actions._command_availability``). A field dropped here is therefore
    inert for a YAML driver while working for a Python one, which sets
    DRIVER_INFO itself. Anything else those two ever read must be added here
    too. ``restarts_device_for`` is the next one: the commanded-restart window
    (``DeviceManager._arm_restart_window``) reads it off DRIVER_INFO, and until
    it was carried here a YAML driver's declared window never opened. ``sets``
    and ``query_for`` are read off the live driver by the device audit, which
    checks a command's declared effect against what the device then reports.
    ``confirm`` is read by every door a person sends a command from by hand
    (the device page and its promoted buttons, the device audit), which asks
    first.
    """
    commands_meta: dict[str, Any] = {}
    for cmd_name, cmd_def in (commands_def or {}).items():
        if not isinstance(cmd_def, dict):
            continue
        cmd_meta: dict[str, Any] = {
            "label": cmd_def.get("label", cmd_name),
            "params": cmd_def.get("params", {}),
        }
        for key in (
            "method", "path", "body", "address", "args", "help",
            "available_offline", "restarts_device_for", "sleeps_device", "sets", "query_for",
            "confirm",
        ):
            if key in cmd_def:
                cmd_meta[key] = cmd_def[key]
        commands_meta[cmd_name] = cmd_meta
    return commands_meta


# One {name} / {name:spec} token left unsubstituted on the way to the wire.
# Same shape the authoring check uses, so both agree on what counts as a
# placeholder and a literal JSON brace is neither.
_WIRE_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)(?::[^{}]*)?\}")

# A {child_id} / {child_id:spec} token: in a response rule's follow-up query,
# the child the rule wrote (_follow_up_lines).
_CHILD_ID_TOKEN = re.compile(r"\{child_id(?::[^{}]*)?\}")

# DEFAULT_LINE_GAP_S (imported above from spec.py, where the reason for its
# value lives) is the spacing _pace_line keeps between this driver's own lines
# over LINE_GAP_TRANSPORTS when no ``inter_command_delay`` is set.


class ConfigurableDriver(
    SignIn,
    DeclaredRoster,
    ReplyThrottle,
    TextReplies,
    JsonReplies,
    OscWire,
    ReplyValues,
    BaseDriver,
):
    """
    A driver that interprets a YAML (.avcdriver) definition at runtime.

    The definition dict must contain:
        - id, name, manufacturer, category, transport
        - commands: dict of command_name -> {send, params}
        - responses: list of {match, mappings} for parsing
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
        # (template location, token) pairs already warned about — see
        # _substitute_for_wire.
        self._warned_unresolved: set[tuple[str, str]] = set()
        self._merge_config_protocol()

        # Child rosters can size themselves from a device-reported state var
        # (`instances.count_from_state`, e.g. num_outputs) as well as a config
        # field, so a driver's channel count tracks what the hardware reports.
        self._init_declared_roster()

        # Compute declarative derived config values (e.g. an optional address
        # prefix) into self.config, so every downstream substitution path —
        # commands, on_connect, responses, polling — sees them. Done before the
        # responses are compiled below so a derived value can appear in a
        # response address too.
        self._compute_derived_config()

        # Pre-compile the receive side (regex / OSC-address / JSON-body rule
        # tables, incl. throttle state and compiled child_set routing) — the
        # shared compile in compiled_protocol, run per instance so the tables
        # embed this instance's config substitutions. Done below, after the
        # derived config is computed.

        # How the HTTP credential has fared on this connection's start-up and
        # poll queries (_judge_http_answer): the queries that answered, the
        # distinct queries refused while none had, and the poll cycle in
        # flight as [requests, refusals] (None outside one). The first two
        # are reset on every connect.
        self._http_answered: set[str] = set()
        self._http_refused_unanswered: set[str] = set()
        self._http_cycle: list[int] | None = None

        # Pacing for the lines this driver sends on its own (see _pace_line):
        # when the next may go, and whether to pace at all (a connection
        # preview records what would be sent and must not sit out the gaps).
        self._next_line_at: float = 0.0
        self._pace_lines: bool = True
        # The on_connect lines the latest start-up sent, and those the first
        # poll after a connect skips because start-up has just sent them.
        self._start_up_lines: set[str] = set()
        self._first_poll_skips: set[str] | None = None
        # Whether the last poll was held back by polling.when_state, so the
        # log says once when the poll pauses and once when it resumes.
        self._poll_held = False
        # Response rules' then_query rounds (_follow_up): the tasks sending
        # them, and per (rule, routed child) when a round may go again
        # (infinity while one is going out).
        self._follow_up_tasks: set[asyncio.Task[None]] = set()
        self._follow_up_until: dict[tuple[int, str], float] = {}
        # Commands' read_back rounds (_read_back): the tasks sending them,
        # and the queries waiting to go out, so one is not queued twice.
        self._read_back_tasks: set[asyncio.Task[None]] = set()
        self._read_back_waiting: set[str] = set()

        # The `auth:` sign-in's state. Raw mode only during a sign-in;
        # outside it on_data_received goes to the reply rules.
        self._init_sign_in()

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
        # Messages a response rule read (it matched and applied, or a
        # throttle held its writes), and the last one none did: the poll
        # loop's unread-replies check (_parsed_count, BaseDriver._judge_reading).
        self._parsed_messages = 0
        self._framed_messages = 0
        self._last_unread = ""
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

        compiled = compiled_protocol.compile_driver(
            self._definition, self.config, device_id=self.device_id
        )
        self._compiled_responses = compiled.responses
        self._osc_responses = compiled.osc_responses
        self._json_responses = compiled.json_responses
        self._after_json_responses = compiled.after_json_responses

    def _condition_key(self, key: str) -> str:
        """One condition key, as a state-store key.

        A bare name is this device's own state variable, which is what a driver
        author writing ``key: examx_mode`` means and saves them spelling out an
        id they do not know at authoring time. Anything already carrying a
        namespace is passed through, so a gate can read a variable or another
        device, and ``$id`` still expands the way it does in ``visible_when``.
        """
        resolved = key.replace("$id", self.device_id)
        if resolved.startswith(VALID_KEY_PREFIXES):
            return resolved
        return f"device.{self.device_id}.{resolved}"

    def _condition_holds(self, cond: dict[str, Any]) -> bool:
        """One ``{key, operator, value}`` leaf."""
        key = cond.get("key")
        if not isinstance(key, str) or not key:
            return True
        actual = self.state.get(self._condition_key(key))
        return eval_operator(
            str(cond.get("operator") or "eq"), actual, cond.get("value")
        )

    def _rule_applies(self, condition: dict[str, Any] | None) -> bool:
        """Whether a response rule's ``only_when:`` gate is open right now.

        No gate is the overwhelming case and answers first. A malformed gate
        opens rather than closes: a driver whose condition is nonsense should
        report what it always reported, not go silent on the one state variable
        somebody was watching.
        """
        if not condition:
            return True
        for group_key, require_all in (("all", True), ("any", False)):
            group = condition.get(group_key)
            if isinstance(group, list) and group:
                results = [
                    self._condition_holds(c)
                    for c in group
                    if isinstance(c, dict)
                ]
                if not results:
                    return True
                return all(results) if require_all else any(results)
        return self._condition_holds(condition)

    async def _close_session(self) -> None:
        self._drop_all_held()
        self._drop_follow_ups()
        self._drop_read_backs()
        await super()._close_session()

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

        norm_commands = normalize_config_commands(
            self.config.get("commands"),
            self._command_suffix or line_ending,
            prefix=self._command_prefix,
        )
        # File commands (not in this set) are the ones send_command frames.
        self._inline_command_names: set[str] = set(norm_commands)
        norm_responses = normalize_config_responses(self.config.get("responses"))
        norm_state_vars = normalize_config_state_vars(
            self.config.get("state_variables")
        )
        # IR code-set → commands. The effective code-set is the driver-declared
        # default_config.ir_codes (a community IR driver's shipped codes, already
        # layered into self.config by resolved_device_config) overlaid with any
        # device-authored ir_codes. Each becomes an IR command emitted through
        # the bound bridge (see send_command). Config codes win by name.
        ir_commands = normalize_ir_codes(self.config.get("ir_codes"))

        if not (norm_commands or norm_responses or norm_state_vars or ir_commands):
            return

        # Auto-declare params for {placeholder} tokens so parameterized commands
        # prompt in the Send Command card. config_keys are excluded — a {host}
        # token resolves from config, not a prompt.
        declare_command_params(norm_commands, set(self.config.keys()))

        merged = dict(self._definition)
        file_commands = merged.get("commands") or {}
        file_responses = merged.get("responses") or []
        file_state_vars = merged.get("state_variables") or {}

        merged_commands = {**file_commands, **norm_commands, **ir_commands}
        merged_responses = list(file_responses) + norm_responses
        derived_vars = derive_state_vars_from_responses(merged_responses)
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
        compiled_protocol.derive_config(
            self.config, self._definition.get("config_derived")
        )

    async def _connect_once(self, attempt: object) -> None:
        """Connect and send on_connect initialization commands.

        Defers polling until after on_connect and initial state queries
        complete, so the watchdog doesn't start counting before the
        device is fully initialized.

        This is the body BaseDriver.connect() runs under its lock, so one
        attempt's setup is never undone by another's. Held back in
        connect(), a second connect started while the first still dialed
        read the held-back 0 as the poll interval and, finishing last, put 0
        back: the device polled nothing again until it was edited.

        The declarative `auth:` login handshake runs inside _post_connect()
        — i.e. BEFORE BaseDriver reports the device connected — so
        `connected` (and the device.connected event, and every trigger or
        panel indicator riding on it) means "logged in", never "socket open
        with a pending login". A wrong credential fails the connect attempt
        outright instead of flapping the device online/offline through the
        reconnect backoff.

        The child rosters and on_connect run in _initial_sync(), so an HTTP
        credential the device refuses during start-up fails the attempt the
        same way, with the connection closed by BaseDriver.connect().
        """
        saved_poll_interval = self.config.get("poll_interval", 0)
        self.config["poll_interval"] = 0

        # A new connection: what answered on the last one proves nothing
        # about the credential on this one.
        self._http_answered = set()
        self._http_refused_unanswered = set()

        # Enable auth-buffering BEFORE the TCP connect so any prompt the
        # device emits the moment the connection opens lands in the auth
        # buffer instead of being run through the normal response matcher.
        # _perform_auth_handshake() turns this back off when it's done.
        self._arm_sign_in()

        try:
            await super()._connect_once(attempt)
        except Exception:
            self._auth_mode = False
            raise
        finally:
            # Restore even when the attempt fails: leaving the zeroed value
            # in config would make the NEXT connect save 0 as the interval
            # to restore, permanently disabling polling after one bad attempt.
            self.config["poll_interval"] = saved_poll_interval

        if saved_poll_interval > 0:
            # The first poll follows straight on; what start-up just asked
            # for is not asked again (see poll()).
            self._first_poll_skips = set(self._start_up_lines)
            await self.start_polling(saved_poll_interval)
        else:
            self._first_poll_skips = None

    async def _initial_sync(self) -> None:
        """Register the declared child rosters, then send on_connect.

        Rosters (`instances:` blocks) come first so routed responses land on
        registered children from the first query. A credential refused during
        start-up (_judge_http_answer) is raised from here, which fails the
        connect attempt.
        """
        try:
            self._register_declared_children()
        except Exception:
            log.exception(
                f"[{self.device_id}] Failed to register declared children"
            )

        await self._run_on_connect()

    def _line_gap(self) -> float:
        """Seconds _pace_line() keeps between two lines, 0 for no pacing.

        Only a tcp or serial link with no ``inter_command_delay`` is paced
        here. With a delay set, the transport already waits that long after
        every write; datagram and HTTP links are not paced by this.
        """
        if not self._pace_lines:
            return 0.0
        transport = self.config.get("transport") or self._definition.get("transport")
        if transport not in LINE_GAP_TRANSPORTS:
            return 0.0
        try:
            if float(self.config.get("inter_command_delay") or 0) > 0:
                return 0.0
        except (TypeError, ValueError):
            pass
        return DEFAULT_LINE_GAP_S

    async def _pace_line(self) -> None:
        """Wait until the next on_connect or poll line may go.

        The wait sits here, between sends, never inside the transport's send
        lock: a command somebody presses meanwhile goes straight out, behind
        at most the one line being written, never behind the rest of a poll.
        The spacing runs from the previous line whichever of the two sent it,
        so the last start-up line and the first poll line are spaced as well.
        A response rule's follow-up queries (_send_follow_ups) can go out
        while a poll is under way; each sender waits again after its sleep
        until the slot is its own, so the two never share one.
        """
        gap = self._line_gap()
        if gap <= 0:
            return
        wait = self._next_line_at - pacing_clock()
        while wait > 0:
            await sleep_at_least(wait)
            wait = self._next_line_at - pacing_clock()
        self._next_line_at = pacing_clock() + gap

    async def _run_on_connect(self) -> None:
        """Send the declared start-up steps (``on_connect``).

        Its own method so a device audit's connection preview can run exactly
        these steps against a transport that records (``drivers/dry_run.py``).

        ``inter_command_delay`` is applied by the transport after every write
        (tcp, serial, udp, osc, which writes through one of those, and http,
        after each response), so nothing waits for it here. Every line sent
        is noted in ``_start_up_lines`` for connect() (see poll()).

        A failed line is logged and the next one sent, except a refused
        credential (a typed ``auth_failed``), which ends start-up and is
        raised.
        """
        self._start_up_lines = set()
        on_connect = self._definition.get("on_connect", [])
        if on_connect and self.transport and self.transport.connected:
            transport_type = self._definition.get("transport")
            delay = self.config.get("inter_command_delay", 0)

            if transport_type == "osc":
                from openavc.transport.osc_codec import osc_encode_message
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
                                self._start_up_lines.add(expanded)
                            continue
                        line: str | None = None
                        if isinstance(item, str):
                            address = self._safe_substitute(item, self.config) if "{" in item else item
                            data = osc_encode_message(address)
                            line = item
                        elif isinstance(item, dict):
                            if not self._query_enabled(item):
                                continue
                            # The address may be keyed "address" (the {address,
                            # args} form) or "send" (a plain {send, when} gated
                            # entry) — accept either so a gated OSC subscription
                            # isn't silently sent as an empty address.
                            address = item.get("address") or item.get("send") or ""
                            if not item.get("args"):
                                line = address
                            if "{" in address:
                                address = self._safe_substitute(address, self.config)
                            args = self._build_osc_args(item.get("args", []), self.config)
                            data = osc_encode_message(address, args)
                        else:
                            continue
                        if await self._line_gate_shut(None, item):
                            continue
                        await self.transport.send(data)
                        if line:
                            self._start_up_lines.add(line)
                    except Exception as e:
                        log.warning(f"[{self.device_id}] on_connect OSC command failed: {e}")

                # Query all OSC state variable addresses to fetch initial state.
                # OSC convention: sending an address with no args returns the
                # current value. This populates state immediately on connect.
                # With no inter_command_delay, 5 ms apart; with one, the
                # transport's own wait spaces them. It is a poll in all but
                # name, so polling.when_state holds it back too: any message
                # wakes a unit that a reconnect finds in standby.
                polling = self._definition.get("polling") or {}
                for addr_pattern, *_rest in self._osc_responses:
                    # A response address with fnmatch wildcards (e.g. QLab's
                    # push-only "/update/workspace/*/...") is a match pattern,
                    # not a queryable address — sending it literally is
                    # meaningless. Such state arrives via push or a dedicated
                    # on_connect/poll query instead.
                    if any(c in addr_pattern for c in "*?["):
                        continue
                    if await self._line_gate_shut(polling, None):
                        break
                    try:
                        addr = self._safe_substitute(addr_pattern, self.config) if "{" in addr_pattern else addr_pattern
                        await self.transport.send(osc_encode_message(addr))
                        self._start_up_lines.add(addr_pattern)
                        if not delay:
                            await asyncio.sleep(0.005)
                    except Exception as e:
                        log.warning(f"[{self.device_id}] OSC initial query failed: {e}")
            else:
                for raw in on_connect:
                    for query in self._expand_query(raw):
                        try:
                            await self._pace_line()
                            # A step's when_state gate is judged again once
                            # the step before it has been answered.
                            if await self._line_gate_shut(None, raw):
                                continue
                            await self._dispatch_query(query)
                            self._start_up_lines.add(query)
                        except ConnectionFaultError as e:
                            if e.fault_code == "auth_failed":
                                raise
                            log.warning(f"[{self.device_id}] on_connect command failed: {e}")
                        except Exception as e:
                            log.warning(f"[{self.device_id}] on_connect command failed: {e}")

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
                response = await self._send_command(query)
            else:
                formatted = (
                    self._substitute_for_wire("polling query", query, self.config)
                    if "{" in query else query
                )
                response = await self.transport.get(formatted)
                # Same handler as the command path: response matching.
                await self._process_http_response(formatted, response)
            # Both forms are judged against the credential: a raw path is the
            # most common HTTP polling form, a command name the other.
            self._judge_http_answer(query, response)
        elif transport_type == "udp":
            if query in commands:
                await self._send_command(query)
            else:
                formatted = (
                    self._substitute_for_wire("polling query", query, self.config)
                    if "{" in query else query
                )
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
                await self._send_command(query)
            else:
                formatted = (
                    self._substitute_for_wire("polling query", query, self.config)
                    if "{" in query else query
                )
                await self.transport.send(
                    self._apply_send_frame(_safe_encode_escapes(formatted))
                )

    def _judge_http_answer(self, query: str, response: Any) -> None:
        """Weigh one start-up or poll answer against the HTTP credential.

        Every refused request is a failed login on the device, and devices
        lock an account or the controller's address after a few, so this
        raises the typed ``auth_failed`` (and nothing more is sent) as soon
        as the refusal is the credential's:

        - a query that answered on this connection is now refused 401/407:
          the device stopped accepting the credential;
        - the _HTTP_REFUSALS_BEFORE_ANY_ANSWER-th distinct query refused
          with nothing answered yet: the device never accepted it.

        A query refused from the start once something has answered is an
        endpoint this account may not use and never counts, in the poll
        cycle's tally either. A poll cycle whose every counted request was
        refused is poll()'s to judge.
        """
        status = getattr(response, "status_code", None)
        if not isinstance(status, int):
            return
        cycle = self._http_cycle
        if status not in _HTTP_AUTH_REJECT_STATUS:
            self._http_answered.add(query)
            if cycle is not None:
                cycle[0] += 1
            return
        if query in self._http_answered:
            if cycle is not None:
                cycle[0] += 1
                cycle[1] += 1
            if status not in _HTTP_CREDENTIAL_REFUSED_STATUS:
                return
            log.warning(
                f"[{self.device_id}] '{query}' answered earlier on this "
                f"connection and is now refused (HTTP {status}); dropping "
                f"the connection"
            )
            raise ConnectionFaultError(
                "The device stopped accepting the username and password. "
                "Check them in the device settings.",
                code="auth_failed",
            )
        if self._http_answered:
            log.debug(
                f"[{self.device_id}] '{query}' refused (HTTP {status}); "
                f"other queries answer, so the account may not read it"
            )
            return
        if cycle is not None:
            cycle[0] += 1
            cycle[1] += 1
        self._http_refused_unanswered.add(query)
        if len(self._http_refused_unanswered) >= _HTTP_REFUSALS_BEFORE_ANY_ANSWER:
            log.warning(
                f"[{self.device_id}] {len(self._http_refused_unanswered)} "
                f"queries refused and none answered; dropping the connection"
            )
            raise ConnectionFaultError(
                "The device refused the username and password. "
                "Check them in the device settings.",
                code="auth_failed",
            )

    def _state_gate_open(self, gate: Any) -> bool:
        """Whether a ``when_state:`` gate lets its entry (or the whole poll)
        run now.

        ``when_state: {<state var>: <value>}`` or ``{<state var>: [<values>]}``:
        every variable named must hold one of its values. A variable the
        device has not reported yet (unset since start-up) does not hold the
        entry back, so a first connect still asks the device for the state the
        gate reads. Values compare in the variable's declared type. A gate
        that is not a mapping opens, as a malformed ``only_when`` does.
        """
        if not isinstance(gate, dict) or not gate:
            return True
        declared = self._definition.get("state_variables") or {}
        for var, wanted in gate.items():
            actual = self.get_state(var)
            if actual is None:
                continue
            var_def = declared.get(var)
            var_type = (
                var_def.get("type", "string") if isinstance(var_def, dict) else "string"
            )
            values = wanted if isinstance(wanted, list) else [wanted]
            allowed = [
                compiled_protocol.coerce_json_value(v, var_type) for v in values
            ]
            if compiled_protocol.coerce_json_value(actual, var_type) not in allowed:
                return False
        return True

    def _describe_state_gate(self, gate: dict[str, Any]) -> tuple[str, str]:
        """A shut ``when_state`` gate in words, as the Driver Builder shows
        it: what it waits for ("Standby is false"), and what the device
        reports now for each variable that holds it shut ("Standby true")."""
        declared = self._definition.get("state_variables") or {}

        def label(var: str) -> str:
            var_def = declared.get(var)
            return (var_def.get("label") if isinstance(var_def, dict) else None) or var

        def word(value: Any) -> str:
            if isinstance(value, bool):
                return "true" if value else "false"
            return str(value)

        wanted: list[str] = []
        reported: list[str] = []
        for var, values in gate.items():
            values = values if isinstance(values, list) else [values]
            wanted.append(f"{label(var)} is {' or '.join(word(v) for v in values)}")
            if not self._state_gate_open({var: values}):
                reported.append(f"{label(var)} {word(self.get_state(var))}")
        return " and ".join(wanted), ", ".join(reported)

    def _poll_gate_shut(self, polling: dict[str, Any]) -> bool:
        """Whether ``polling.when_state`` holds the poll back now. Says so in
        the log once when the poll pauses and once when it resumes."""
        held = not self._state_gate_open(polling.get("when_state"))
        if held != self._poll_held:
            self._poll_held = held
            if held:
                wanted, reported = self._describe_state_gate(polling.get("when_state"))
                log.info(
                    f"[{self.device_id}] Polling paused: it polls only while "
                    f"{wanted}, and the device reports {reported}"
                )
            else:
                log.info(f"[{self.device_id}] Polling resumed")
        return held

    async def _line_gate_shut(
        self, gates: dict[str, Any] | None, entry: Any, *, quiet: bool = False,
    ) -> str | None:
        """Judge the ``when_state`` gates again just before a line goes out:
        ``"poll"`` when the whole poll's gate has shut (``gates`` is the
        polling block; None for start-up), ``"entry"`` when this entry's has,
        else None. ``quiet`` judges the poll's gate without the poll's
        paused / resumed log, for a line that is not part of the poll (a
        response rule's follow-up query).

        A gated line first waits for the transport to finish any write under
        way or waiting (a command somebody sent, and its
        ``inter_command_delay``), one sent while this line was already waiting
        included, so a reply to it that shuts the gate is read before the
        decision; between the decision and the send nothing yields. Without an
        ``inter_command_delay`` the line follows a command at once, and only
        a reply that has already arrived counts.
        """
        poll_gate = gates.get("when_state") if gates is not None else None
        entry_gate = entry.get("when_state") if isinstance(entry, dict) else None
        if not poll_gate and not entry_gate:
            return None
        wait_idle = getattr(self.transport, "wait_idle", None)
        if wait_idle is not None:
            await wait_idle()
        if gates is not None and (
            not self._state_gate_open(poll_gate) if quiet
            else self._poll_gate_shut(gates)
        ):
            return "poll"
        if isinstance(entry, dict) and not self._state_gate_open(
            entry.get("when_state")
        ):
            return "entry"
        return None

    def _query_enabled(self, query: dict[str, Any]) -> bool:
        """Whether a ``when:``- or ``when_state:``-gated polling/on_connect
        entry should run.

        ``when: <config_field>`` runs the entry only while that config field is
        truthy. It exists so a device whose chatty stream must be *armed* by the
        queries themselves (a per-channel ``subscribe`` to a level meter, say)
        can offer that as an integrator checkbox instead of forcing the choice
        on every site. ``when_state:`` runs it only while the device's own
        state allows (_state_gate_open): a unit that any command wakes from
        standby is left in standby. An entry with neither always runs.
        """
        if not self._state_gate_open(query.get("when_state")):
            return False
        field = query.get("when")
        if field is None:
            return True
        if not isinstance(field, str) or not field:
            log.warning(
                f"[{self.device_id}] query 'when' must name a config field; "
                f"skipping entry {query!r}"
            )
            return False
        return reads_as_on(self.config.get(field))

    def _expand_query(self, query: Any) -> list[str]:
        """Expand one polling/on_connect entry. Strings pass through
        unchanged; an ``{each_child: <type>, send: <template>}`` dict yields
        one query per registered child of that type with ``{child_id}``
        substituted (the unpadded local id). ``{child_id:spec}`` format specs
        work too (``{child_id:02d}`` zero-pads for padded-address protocols),
        and ``page_size: N`` sends it once per page of N children instead
        (``child_ids.page_starts``) for a list read that answers a page;
        other ``{placeholders}`` pass through untouched for the downstream
        config substitution. A ``{send: <string>}`` dict is a plain query that
        only exists in dict form so it can carry ``when:``. Either dict form may
        declare ``when: <config_field>`` to run only while that field is truthy,
        and ``when_state:`` to run only while the device's state allows
        (_state_gate_open). Anything else is skipped with a warning.
        """
        if isinstance(query, str):
            return [query]
        if isinstance(query, dict):
            if not self._query_enabled(query):
                return []
            if "each_child" in query:
                ctype = query.get("each_child")
                template = query.get("send")
                if not isinstance(ctype, str) or not isinstance(template, str) or not template:
                    log.warning(
                        f"[{self.device_id}] each_child query needs 'each_child' "
                        f"+ 'send'; skipping"
                    )
                    return []
                children = page_starts(
                    self.list_children(ctype), query.get("page_size")
                )
                return [
                    self._safe_substitute(template, {"child_id": local_id})
                    for local_id in children
                ]
            send = query.get("send")
            if isinstance(send, str) and send:
                return [send]
        log.warning(
            f"[{self.device_id}] Unrecognized query entry {query!r}; skipping"
        )
        return []

    def _follow_up(
        self,
        rule: object,
        entries: list[Any],
        scope: str,
        children: list[tuple[str, Any, dict[str, Any]]],
    ) -> None:
        """Send a response rule's ``then_query`` now that it has applied.

        A device that announces a change without saying what changed ("a tie
        was made at the front panel") is read again at once instead of at the
        next poll. The round runs as its own task: its lines are paced and may
        wait on a gate, and a reply read meanwhile has to reach the matcher.

        One round at a time per rule and routed child (``scope``, the
        throttle's): a match that arrives while one is going out, or within
        FOLLOW_UP_SETTLE_S of its last line, applies its writes and sends
        nothing. The replies to the round arrive inside that window, so a
        rule they match reads once and stops; a second notice inside it is
        read by the next poll.
        """
        key = (id(rule), scope)
        if self._follow_up_until.get(key, 0.0) > time.monotonic():
            log.debug(
                f"[{self.device_id}] then_query not sent: this rule's queries "
                f"went out less than {FOLLOW_UP_SETTLE_S:g} s ago"
            )
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        child_ids: list[Any] = []
        for _ctype, local_id, _updates in children:
            if local_id not in child_ids:
                child_ids.append(local_id)
        self._follow_up_until[key] = float("inf")
        task = loop.create_task(self._send_follow_ups(key, entries, child_ids))
        self._follow_up_tasks.add(task)
        task.add_done_callback(self._follow_up_tasks.discard)

    def _follow_up_lines(self, entry: Any, child_ids: list[Any]) -> list[str]:
        """One ``then_query`` entry as the lines it sends: expanded as a poll
        entry is (``each_child``, ``when``, ``when_state``, ``page_size``),
        then, outside ``each_child``, ``{child_id}`` filled with each child the
        rule wrote. With none written (an id the roster does not hold), such
        a line is not sent."""
        lines = self._expand_query(entry)
        if isinstance(entry, dict) and "each_child" in entry:
            return lines
        out: list[str] = []
        for line in lines:
            if _CHILD_ID_TOKEN.search(line):
                out.extend(
                    self._safe_substitute(line, {"child_id": local_id})
                    for local_id in child_ids
                )
            else:
                out.append(line)
        return out

    async def _send_follow_ups(
        self, key: tuple[int, str], entries: list[Any], child_ids: list[Any],
    ) -> None:
        """Send one round of follow-up queries the way a poll sends its
        lines, then hold the rule for FOLLOW_UP_SETTLE_S (_follow_up).

        Nothing goes out while ``polling.when_state`` holds the poll: a
        notice from a unit in standby must not wake it. A failed line ends
        the round; the poll that follows reads what it missed and is the one
        that judges the connection."""
        polling = self._definition.get("polling") or {}
        cancelled = False
        try:
            for entry in entries:
                for line in self._follow_up_lines(entry, child_ids):
                    if not self.transport or not self.transport.connected:
                        return
                    await self._pace_line()
                    gate = await self._line_gate_shut(polling, entry, quiet=True)
                    if gate == "poll":
                        return
                    if gate:
                        continue
                    await self._dispatch_query(line)
        except asyncio.CancelledError:
            cancelled = True
            raise
        except Exception as exc:
            log.warning(
                f"[{self.device_id}] then_query failed; the next poll reads "
                f"the values: {exc}"
            )
        finally:
            if cancelled:
                # Dropped with its connection (_drop_follow_ups): no hold is
                # left for the next one.
                self._follow_up_until.pop(key, None)
            else:
                self._follow_up_until[key] = time.monotonic() + FOLLOW_UP_SETTLE_S

    def _drop_follow_ups(self) -> None:
        """Cancel every follow-up round under way: the connection it would
        send on is going away. A round still waiting is forgotten with it, so
        the first notice on the next connection is read at once."""
        for task in list(self._follow_up_tasks):
            task.cancel()
        self._follow_up_tasks.clear()
        self._follow_up_until.clear()

    def _read_back_sends(
        self,
        command: str,
        cmd_def: dict[str, Any],
        given: dict[str, Any],
        wire: dict[str, Any],
    ) -> list[tuple[str, tuple[str, Any]]]:
        """A command's ``read_back:`` as what each query sends, built now,
        while this call's parameters are at hand: ``(key, (kind, what))``.

        ``kind`` is ``"command"`` (``what`` is the command's name and the
        parameters it shares with this one, as the caller gave them),
        ``"osc"`` (an address, sent with no arguments), ``"http"`` (a path,
        read with GET) or ``"raw"`` (the bytes, built as a poll query's
        are). ``key`` is what says two queries are the same one.
        """
        raw = cmd_def.get("read_back")
        transport_type = self._definition.get("transport")
        commands = self._definition.get("commands") or {}
        all_params = {**self.config, **self._push_params(), **wire}
        where = f"commands.{command}.read_back"
        out: list[tuple[str, tuple[str, Any]]] = []

        if raw is True:
            # The command's own address or path, filled as the send filled it.
            if self._is_osc_command(cmd_def):
                address = self._substitute_for_wire(
                    f"commands.{command}.address", cmd_def.get("address", ""),
                    all_params,
                )
                out.append((f"osc {address}", ("osc", address)))
            elif self._is_http_command(cmd_def):
                path = self._substitute_for_wire(
                    f"commands.{command}.path", cmd_def.get("path") or "/",
                    all_params,
                )
                out.append((f"http {path}", ("http", path)))
            else:
                log.debug(
                    f"[{self.device_id}] read_back: true on '{command}' names "
                    f"no query: only an OSC or HTTP command reads itself back"
                )
            return out
        if isinstance(raw, str):
            entries = [raw]
        elif isinstance(raw, list):
            entries = [e for e in raw if isinstance(e, str) and e]
        else:
            return out

        for entry in entries:
            named = commands.get(entry)
            if isinstance(named, dict) and entry != command:
                declared = named.get("params") or {}
                shared = {k: v for k, v in given.items() if k in declared}
                key = f"command {entry} {sorted(shared.items(), key=lambda kv: kv[0])!r}"
                out.append((key, ("command", (entry, shared))))
                continue
            text = self._substitute_for_wire(where, entry, all_params)
            if transport_type == "osc":
                out.append((f"osc {text}", ("osc", text)))
            elif transport_type == "http":
                out.append((f"http {text}", ("http", text)))
            elif transport_type == "udp":
                out.append((f"raw {text}", ("raw", _safe_encode_escapes(text))))
            else:  # tcp / serial: wrapped in send_frame like a poll query
                data = self._apply_send_frame(_safe_encode_escapes(text))
                out.append((f"raw {text}", ("raw", data)))
        return out

    def _read_back(
        self,
        command: str,
        cmd_def: dict[str, Any],
        given: dict[str, Any],
        wire: dict[str, Any],
    ) -> None:
        """Send a command's ``read_back:`` queries now that its write is done.

        A device whose answer to a set carries no value, or that never
        reports a client's own change back to it, has its new value read at
        once instead of at the next poll. The round runs as its own task, so
        the caller does not wait for it: its lines are paced like a poll's
        and may wait on a gate.

        A query already waiting to go out is not queued again. That one goes
        out after this command's write (scheduled only once the write is
        done), so it reads this command's value: a slider dragged through
        many values reads back after the last one, not once per value.
        """
        if not cmd_def.get("read_back"):
            return
        try:
            sends = self._read_back_sends(command, cmd_def, given, wire)
        except Exception as exc:
            log.warning(
                f"[{self.device_id}] read_back after '{command}' not sent; the "
                f"next poll reads the value: {exc}"
            )
            return
        fresh = [s for s in sends if s[0] not in self._read_back_waiting]
        if not fresh:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        for key, _what in fresh:
            self._read_back_waiting.add(key)
        task = loop.create_task(self._send_read_backs(command, fresh))
        self._read_back_tasks.add(task)
        task.add_done_callback(self._read_back_tasks.discard)

    async def _send_read_backs(
        self, command: str, sends: list[tuple[str, tuple[str, Any]]],
    ) -> None:
        """Send one command's read-back queries the way a poll sends its
        lines (_read_back).

        The command's own write counts as the line before the first query:
        on a link this driver spaces itself, the first one keeps a line's gap
        from it. With an ``inter_command_delay`` the transport holds every
        write that long after the command's, so the device answers the
        command before the query reaches it. Nothing goes out while
        ``polling.when_state`` holds the poll: a command that put the unit
        in standby is not followed by a query that wakes it. A failed query
        ends the round; the next poll reads the value."""
        polling = self._definition.get("polling") or {}
        try:
            gap = self._line_gap()
            if gap > 0:
                await sleep_at_least(gap)
            for key, (kind, what) in sends:
                if not self.transport or not self.transport.connected:
                    return
                await self._pace_line()
                if await self._line_gate_shut(polling, None, quiet=True) == "poll":
                    log.debug(
                        f"[{self.device_id}] read_back after '{command}' not "
                        f"sent: polling.when_state holds the poll"
                    )
                    return
                # From here a second press queues its own query: this one may
                # already be on its way ahead of that press's write.
                self._read_back_waiting.discard(key)
                await self._send_read_back(kind, what)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning(
                f"[{self.device_id}] read_back after '{command}' failed; the "
                f"next poll reads the value: {exc}"
            )
        finally:
            for key, _what in sends:
                self._read_back_waiting.discard(key)

    async def _send_read_back(self, kind: str, what: Any) -> None:
        """Send one read-back query (_read_back_sends). A reply goes through
        the matcher like any other. An HTTP answer is not weighed against the
        credential here: the poll that follows judges the connection."""
        if kind == "command":
            name, params = what
            await self._send_command(name, params)
        elif kind == "osc":
            from openavc.transport.osc_codec import osc_encode_message

            await self.transport.send(osc_encode_message(what))
        elif kind == "http":
            response = await self.transport.get(what)
            await self._process_http_response(what, response)
        else:
            await self.transport.send(what)

    def _drop_read_backs(self) -> None:
        """Cancel every read-back round under way: the connection it would
        send on is going away."""
        for task in list(self._read_back_tasks):
            task.cancel()
        self._read_back_tasks.clear()
        self._read_back_waiting.clear()

    async def send_command(
        self, command: str, params: dict[str, Any] | None = None
    ) -> Any:
        """Look up command in definition, substitute params, send.

        A command that declares ``read_back:`` then reads its new value with
        its own queries (_read_back). The driver's own lines (start-up, poll,
        a response rule's follow-ups, a read-back that names a command) send
        through _send_command without one: they are reads, or the read
        itself.
        """
        return await self._send_command(command, params, read_back=True)

    async def _send_command(
        self,
        command: str,
        params: dict[str, Any] | None = None,
        *,
        read_back: bool = False,
    ) -> Any:
        """send_command's body; ``read_back`` schedules the command's
        ``read_back:`` queries once its write is done."""
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

        # UDP side-send: one datagram beside the main transport (a Wake-on-LAN
        # magic packet from a display driver, a message to a presentation's
        # UDP receiver). It needs no connection, so it too is handled before
        # the connected gate: the whole point of ``available_offline`` on a
        # wake is that the control link is down. Params are validated and
        # wire-mapped exactly as on the transport branches below.
        if isinstance(cmd_def, dict) and isinstance(cmd_def.get("udp"), dict):
            params = _normalize_and_validate_command_params(
                command, cmd_def.get("params", {}), params
            )
            params = self._apply_param_wire_maps(cmd_def.get("params", {}), params)
            return await self._send_udp_command(command, cmd_def, params)

        if not self.transport or not self.transport.connected:
            raise ConnectionError(f"[{self.device_id}] Not connected")

        if cmd_def is None:
            log.warning(f"[{self.device_id}] Unknown command: {command}")
            self._observe(UNKNOWN_COMMAND, command=command)
            return None

        # Runtime gate: trim + validate the supplied params against the
        # command's declared schema (covers the TCP/serial, OSC, and HTTP
        # branches below). Raises CommandParamError on a bad value.
        params = _normalize_and_validate_command_params(
            command, cmd_def.get("params", {}), params
        )
        # The values as the caller meant them: a read-back that names another
        # command passes these on, and that command maps them itself.
        given = params

        # Wire-value translation (opt-in): a param may declare a ``map:``
        # translating the validated user-facing value to what the protocol
        # wants on the wire (0-based channel numbers, letter codes, ...).
        # Applied once, here — after validation so min/max/enum checks see
        # the user-facing value, before any transport branch so TCP, OSC,
        # and HTTP sends all get the wire form. Values the map doesn't
        # cover pass through unchanged.
        params = self._apply_param_wire_maps(cmd_def.get("params", {}), params)

        # Check if this is an OSC command (has 'address' key)
        if self._is_osc_command(cmd_def):
            result = await self._send_osc_command(command, cmd_def, params)
            if read_back and result:
                self._read_back(command, cmd_def, given, params)
            return result

        # Check if this is an HTTP transport command (has 'path' or 'method' keys)
        if self._is_http_command(cmd_def):
            response = await self._send_http_command(command, cmd_def, params)
            # Read back whatever the device answered: a set it refused shows
            # its unchanged value.
            if read_back and response is not None:
                self._read_back(command, cmd_def, given, params)
            return response

        data = self.build_wire(command, params)
        if data is None:
            return None

        await self.transport.send(data)
        log.debug(f"[{self.device_id}] Sent command '{command}': {data!r}")
        if read_back:
            self._read_back(command, cmd_def, given, params)
        return True

    def build_wire(
        self, command: str, params: dict[str, Any] | None = None
    ) -> bytes | None:
        """Build the exact bytes a byte-stream command puts on the wire.

        The whole send-side build in one place: command_prefix/suffix framing,
        placeholder substitution, escape decoding, and the computed send_frame
        header. Returns None when the command is unknown or declares no send
        string (the two cases that make a byte-stream send a no-op).

        Needs no transport, so the send path is testable — and previewable —
        without opening a socket. Two limits on calling it directly: it is the
        byte-stream route only (OSC and HTTP commands are built by their own
        senders), and it takes params as given. send_command validates params
        against the command's declared schema and applies their ``map:``
        translations first, so anything that must match a real send has to go
        through send_command, not here.
        """
        params = params or {}
        cmd_def = self._definition.get("commands", {}).get(command)
        if not isinstance(cmd_def, dict):
            return None

        raw = cmd_def.get("send", "")
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
        all_params = {**self.config, **self._push_params(), **params}
        formatted = self._substitute_for_wire(f"commands.{command}.send", raw, all_params)

        # Encode (handle explicit escape sequences only — safe subset), then
        # wrap in the send_frame packet header (no-op unless declared).
        return self._apply_send_frame(_safe_encode_escapes(formatted))

    async def _send_udp_command(
        self, command: str, cmd_def: dict[str, Any], params: dict[str, Any]
    ) -> Any:
        """Send a command's ``udp:`` block as one datagram.

        Two forms. ``payload`` is substituted ({param}, {config}) and
        escape-decoded exactly like ``send``, but NOT framed: the driver's
        command_prefix / command_suffix and send_frame belong to the main
        transport's protocol, and the side channel speaks its own.
        ``magic_packet`` names the state variable or config field holding the
        device's MAC (state first, because a MAC the device reported on its
        last connection beats one typed months ago; a reported value that is
        not a MAC, such as the all-zero one, gives way to the typed one) and
        sends the Wake-on-LAN packet through :meth:`BaseDriver.wake_on_lan`.
        With no usable MAC it raises :class:`WakeAddressError`.

        ``host`` defaults to the device's own host and ``port`` may be a
        literal or a {config} placeholder, so a receiver port that is a
        device setting (a presentation's UDP port) stays a config field.
        """
        udp = cmd_def["udp"]
        where = f"commands.{command}.udp"
        all_params = {**self.config, **self._push_params(), **params}

        host_raw = udp.get("host")
        host = (
            self._substitute_for_wire(f"{where}.host", str(host_raw), all_params)
            if host_raw
            else None
        )

        mac_field = str(udp.get("magic_packet") or "")
        if mac_field:
            port = self._resolve_udp_port(
                where, udp.get("port"), all_params, default=WAKE_ON_LAN_PORT
            )
            typed = str(self.config.get(mac_field) or "").strip()
            mac = normalize_mac(self.get_state(mac_field)) or normalize_mac(typed)
            if mac is None:
                raise self._wake_address_error(mac_field, typed)
            await self.wake_on_lan(mac, host=host, port=port)
            log.debug(f"[{self.device_id}] Sent command '{command}': magic packet")
            return True

        port = self._resolve_udp_port(where, udp.get("port"), all_params)
        formatted = self._substitute_for_wire(
            f"{where}.payload", str(udp.get("payload") or ""), all_params
        )
        data = _safe_encode_escapes(formatted)
        await self.send_udp(
            data, host=host, port=port, broadcast=bool(udp.get("broadcast"))
        )
        log.debug(f"[{self.device_id}] Sent command '{command}' over UDP: {data!r}")
        return True

    def _wake_address_error(self, mac_field: str, typed: str) -> WakeAddressError:
        """The refusal for a wake with no usable MAC in ``mac_field``.

        Names the field as Edit Device does, and says the MAC fills itself in
        only when the driver does read it from the device: a state variable of
        the same name, or a field the device's report is saved into.
        """
        from openavc.core.learned_config import learned_fields

        field_def = (self._definition.get("config_schema") or {}).get(mac_field)
        field_def = field_def if isinstance(field_def, dict) else {}
        reported = mac_field in learned_fields(self) or mac_field in (
            self._definition.get("state_variables") or {}
        )
        return WakeAddressError(
            field_label=param_label(mac_field, field_def),
            value=typed,
            reported=reported,
        )

    def _resolve_udp_port(
        self,
        where: str,
        raw: Any,
        all_params: dict[str, Any],
        default: int | None = None,
    ) -> int:
        """The port a ``udp:`` block names: a number, or a {config} placeholder."""
        if raw is None or raw == "":
            if default is not None:
                return default
            raise ValueError(f"[{self.device_id}] {where}: no port to send to")
        text = self._substitute_for_wire(f"{where}.port", str(raw), all_params)
        try:
            port = int(str(text).strip())
        except ValueError:
            raise ValueError(
                f"[{self.device_id}] {where}: port {text!r} is not a number"
            ) from None
        if not 1 <= port <= 65535:
            raise ValueError(
                f"[{self.device_id}] {where}: port {port} is not between 1 and "
                f"65535"
            )
        return port

    @staticmethod
    def _apply_param_wire_maps(
        param_defs: dict[str, Any], params: dict[str, Any]
    ) -> dict[str, Any]:
        """Translate param values to what goes on the wire: through their
        declared ``map:`` lookup, else through their ``scale`` / ``offset``
        (the real value back to the device's own number).

        Keys match through ``compiled_protocol.map_word``, the rule a device
        setting's map uses: a coerced ``child_id`` int hits its string key, an
        unquoted YAML integer key still matches, and a boolean matches a true /
        false key in any case (``"true"``, ``True``). The mapped value
        substitutes as a string (it is a wire token, not a number to
        re-coerce)."""
        if not isinstance(param_defs, dict) or not params:
            return params
        out = params
        for name, pdef in param_defs.items():
            if not isinstance(pdef, dict) or name not in params:
                continue
            mapped = compiled_protocol.map_word(params[name], pdef.get("map"))
            if mapped is not None:
                if out is params:
                    out = dict(params)
                out[name] = mapped
            elif pdef.get("scale") is not None or pdef.get("offset") is not None:
                if out is params:
                    out = dict(params)
                out[name] = compiled_protocol.value_to_wire(params[name], pdef)
        return out

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
        from openavc.transport.http_client import HTTPClientTransport

        if not isinstance(self.transport, HTTPClientTransport):
            log.error(
                f"[{self.device_id}] Command '{command}' uses HTTP fields "
                f"but transport is not HTTP"
            )
            return None

        all_params = {**self.config, **self._push_params(), **params}

        method = cmd_def.get("method", "GET").upper()
        raw_path = cmd_def.get("path", "/")
        raw_body = cmd_def.get("body")
        headers = self._build_http_headers(cmd_def.get("headers"), all_params)

        # Substitute params in path using safe substitution
        path = self._substitute_for_wire(f"commands.{command}.path", raw_path, all_params)

        # Substitute params in body. A body that parses as JSON is sent as
        # JSON; anything else (XML, form text, a bare token) goes out as raw
        # content. Both are decided here rather than in two separate sends —
        # a second send site is how query_params came to be dropped from the
        # raw-content path while the JSON path kept them.
        json_body = None
        content = None
        if raw_body:
            body_str = self._substitute_for_wire(
                f"commands.{command}.body", raw_body, all_params
            )
            try:
                json_body = json.loads(body_str)
            except (json.JSONDecodeError, ValueError):
                log.debug(
                    f"[{self.device_id}] Body for '{command}' is not JSON, "
                    f"sending as raw content"
                )
                content = body_str.encode("utf-8")

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
            content=content, headers=headers,
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

    def _push_params(self) -> dict[str, Any]:
        """Platform-provided substitution values for command templates.

        ``{push_callback_url}`` resolves to the http_listener callback URL so
        a registration command can hand it to the device. Injected only while
        a subscription is active — otherwise the token stays verbatim in the
        outgoing payload, which is louder (and greppable in the device log)
        than silently registering an empty URL.
        """
        params: dict[str, Any] = {}
        if self._push_callback_url:
            params["push_callback_url"] = self._push_callback_url
        if self._push_session:
            # sse shape with a session block: the id the device gave the
            # open stream, for the registration and unregister commands.
            params["push_session"] = self._push_session
        return params

    # Shared with the simulator/validator interpreters — one substitution
    # implementation for every surface (see compiled_protocol module docs).
    # Kept as a staticmethod so existing callers (API routes, tests) work.
    _safe_substitute = staticmethod(compiled_protocol.safe_substitute)

    def _substitute_for_wire(
        self, where: str, template: str, params: dict[str, Any]
    ) -> str:
        """Substitute, and say so when a placeholder survives it.

        ``safe_substitute`` leaves an unknown ``{name}`` in place rather than
        raising, so a JSON body full of braces cannot crash a send. The token
        then goes to the device as literal text: the command looks like it ran
        and the device ignores it, or a poll query is never answered and the
        device eventually drops on its watchdog. Nothing said a word.

        The authoring check (``validate_substitutions``) catches this before a
        driver is saved, but only where the file alone is wrong. It cannot
        cover a command that declares no params, because the caller may
        legitimately supply one at call time — which is exactly the case only
        the send path can settle, since here the params are known.

        Warned once per template and token: polls run on a timer, and a warning
        that repeats every few seconds is one nobody reads. Only the template's
        own tokens count: a protocol that wraps a value in braces of its own
        (``{{name}}`` sends ``{Lead Vox}``) put that text there on purpose.
        """
        out = self._safe_substitute(template, params)
        if "{" not in out:
            return out
        own = set(_WIRE_PLACEHOLDER.findall(template))
        for name in _WIRE_PLACEHOLDER.findall(out):
            if name not in own or (where, name) in self._warned_unresolved:
                continue
            self._warned_unresolved.add((where, name))
            log.warning(
                "[%s] %s: '{%s}' was not substituted and goes to the device "
                "literally — it names no parameter passed to this call and no "
                "config field. Declare it, or pass it when calling the command.",
                self.device_id, where, name,
            )
        return out

    async def _process_http_response(
        self, command: str, response: Any
    ) -> Any:
        """
        Process an HTTP response: check status and match response patterns.

        ``command`` is the command name, or the raw path when a polling query
        addressed one directly — it only labels the log line. Returns the
        HTTPResponse object for the caller.
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

    def _reads_replies(self) -> bool:
        """A definition reads what the device sends when it declares a
        response rule of any kind. One with none is a device that is only
        told things, so the poll loop's silence check leaves it alone."""
        return bool(
            self._compiled_responses or self._json_responses or self._osc_responses
        )

    def _parsed_count(self) -> int | None:
        """Messages this definition's response rules have read; None for a
        definition with no rules (it reads nothing, so there is nothing to
        judge)."""
        return self._parsed_messages if self._reads_replies() else None

    def _framed_count(self) -> int | None:
        """Complete messages that reached the response rules, read or not."""
        return self._framed_messages if self._reads_replies() else None

    def _poll_waits_on_device(self) -> bool:
        """The last poll was held back by ``polling.when_state``."""
        return self._poll_held

    def _connect_check_held(self) -> bool:
        """``polling.when_state`` holds the poll back now (the state the
        device reported last, kept from before the reconnect). Read without
        touching the poll's own paused/resumed bookkeeping, which is the
        poll loop's."""
        gate = (self._definition.get("polling") or {}).get("when_state")
        if not isinstance(gate, dict) or self._state_gate_open(gate):
            return False
        wanted, reported = self._describe_state_gate(gate)
        log.info(
            f"[{self.device_id}] Connect check skipped: it polls only while "
            f"{wanted}, and the device reports {reported}"
        )
        return True

    def _unread_sample(self) -> str:
        return self._last_unread

    async def _liveness_held(self) -> bool:
        """``polling.when_state`` holds the poll back, so it holds the
        probe back too: a unit that any message would wake is left in
        standby. Judged as a poll line is (_line_gate_shut), after a command
        sent just before has had its answer read, and without the poll's own
        paused/resumed log, which is the poll loop's."""
        polling = self._definition.get("polling")
        if not isinstance(polling, dict):
            return False
        return await self._line_gate_shut(polling, None, quiet=True) == "poll"

    async def _liveness_probe(self) -> None:
        """Send the declared probe and wait for a qualifying reply.

        Any inbound frame counts as alive — a poll reply or an unsolicited
        push arriving during the wait window proves the device is there just
        as well as a direct answer. An `expect` regex narrows that to matching
        frames only, so a definition's `expect` matches the device's error
        reply to the probe as well as the good one: an error is still an
        answer, and a miss here drops the link. The reply deadline is
        enforced by the BaseDriver loop (HEALTH_TIMEOUT_S wraps this
        coroutine, HEALTH_INTERVAL_S on a held reconnect), which cancels the
        await on timeout; the finally clears the waiter either way.
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
            from openavc.transport.osc_codec import osc_encode_message

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
        """One frame from the device: the sign-in's while it runs, else the
        reply rules' (OSC by address, anything else as a line of text)."""
        if self._auth_mode:
            self._take_sign_in_bytes(data)
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
        self._framed_messages += 1
        self._read_text_reply(text)

    async def set_device_setting(self, key: str, value: Any) -> Any:
        """
        Write a device setting using the write definition from the driver YAML.

        Supports HTTP (method/path/body) and TCP/serial (send) write formats.
        """
        settings = self._definition.get("device_settings", {})
        setting_def = settings.get(key)
        if not setting_def:
            raise UnknownDeviceSettingError(f"Unknown device setting: {key}")

        write_def = setting_def.get("write")
        if not write_def:
            raise NotImplementedError(
                f"Device setting '{key}' has no write definition"
            )

        # A setting's map names the device's own word for the value (ON for
        # true) and is written as declared. Otherwise the setting writes its
        # state variable's real value: the variable's scale / offset turn it
        # back into the device's own number.
        word = compiled_protocol.map_word(value, setting_def.get("map"))
        if word is None:
            state_var = self._definition.get("state_variables", {}).get(
                setting_def.get("state_key") or key
            )
            wire_value = compiled_protocol.value_to_wire(value, state_var)
        else:
            wire_value = word
        all_params = {**self.config, "value": wire_value}

        # OSC write
        if "address" in write_def:
            from openavc.transport.osc import OSCTransport
            from openavc.transport.osc_codec import osc_encode_message

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
            from openavc.transport.http_client import HTTPClientTransport

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

        A query that names a declared command runs as that command on every
        transport, so its reply goes through response matching and send-side
        framing applies. Anything else is a raw string for the transport: a URL
        path on HTTP, an OSC address on OSC, the protocol bytes on TCP/serial/UDP.

        TCP and serial lines are spaced by ``inter_command_delay`` (in the
        transport) or, when none is set, by DEFAULT_LINE_GAP_S (_pace_line).
        UDP, OSC and HTTP lines are spaced only by ``inter_command_delay``
        (on HTTP, from one response to the next request).
        """
        if not self.transport or not self.transport.connected:
            return

        polling = self._definition.get("polling", {})
        queries = polling.get("queries", [])

        # The whole poll waits while the device's own state says so
        # (polling.when_state): a unit that any command wakes is left in
        # standby. A cycle that sends nothing is neither silent nor unread to
        # BaseDriver._poll_loop, so the connection stays up meanwhile.
        if self._poll_gate_shut(polling):
            self._first_poll_skips = None
            return

        transport_type = self._definition.get("transport")
        is_osc = transport_type == "osc"
        is_http = transport_type == "http"

        # Count this cycle's HTTP answers for the whole-cycle check below
        # (_judge_http_answer fills it in).
        self._http_cycle = [0, 0] if is_http else None

        # The first poll after a connect follows on_connect at once, so a line
        # start-up has just sent is not sent again. A line whose send failed
        # was not noted (_run_on_connect), so it is asked again here.
        skips = self._first_poll_skips
        self._first_poll_skips = None

        cycle_held = False
        for raw_query in queries:
            if cycle_held:
                break
            # each_child entries expand to one query per registered child.
            for query in self._expand_query(raw_query):
                if skips and query in skips:
                    continue
                try:
                    if is_osc:
                        # A reply read since the cycle began can shut a
                        # when_state gate: judged again before each line.
                        gate = await self._line_gate_shut(polling, raw_query)
                        if gate == "poll":
                            cycle_held = True
                            break
                        if gate:
                            continue
                        commands = self._definition.get("commands", {})
                        if query in commands:
                            await self._send_command(query)
                        else:
                            from openavc.transport.osc_codec import osc_encode_message
                            address = self._safe_substitute(query, self.config) if "{" in query else query
                            msg = osc_encode_message(address)
                            await self.transport.send(msg)
                    else:
                        # Every transport resolves a command name (so framing
                        # applies and the response is matched) and falls back to
                        # the raw string. Shared with on_connect via
                        # _dispatch_query so the two paths can't drift apart.
                        await self._pace_line()
                        # A reply read since the cycle began (Enter Standby's
                        # answer, or the gated query's own) can shut a
                        # when_state gate, and every line sent after it would
                        # undo it: judged again just before each line goes out.
                        gate = await self._line_gate_shut(polling, raw_query)
                        if gate == "poll":
                            cycle_held = True
                            break
                        if gate:
                            continue
                        await self._dispatch_query(query)
                except (ConnectionError, TimeoutError, OSError) as exc:
                    # Transport-level failure: propagate so BaseDriver._poll_loop's
                    # missed-poll watchdog counts it and can eventually mark the
                    # device disconnected. Swallowing this is what let HTTP/OSC/UDP
                    # devices report connected while unreachable. (HTTP connect
                    # errors arrive here as builtin ConnectionError — http_client
                    # translates httpx.ConnectError before it propagates.) A
                    # refused credential (_judge_http_answer) ends the cycle the
                    # same way and drops the connection at once.
                    if getattr(exc, "fault_code", None) != "auth_failed":
                        log.warning(f"[{self.device_id}] Poll query failed (transport)")
                    self._http_cycle = None  # cycle aborted, count is moot
                    raise
                except _HTTP_TRANSPORT_ERRORS as exc:
                    # httpx timeout / status / other transport errors are also
                    # transport-level for the watchdog — re-raise, don't swallow.
                    log.warning(f"[{self.device_id}] Poll query failed (HTTP): {exc}")
                    self._http_cycle = None  # cycle aborted, count is moot
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

        cycle = self._http_cycle
        self._http_cycle = None
        if cycle is not None and cycle[0] > 0 and cycle[1] == cycle[0]:
            # Every request this cycle came back refused: not one privileged
            # endpoint, the credentials themselves. This catches what
            # _judge_http_answer leaves alone: a list shorter than its count,
            # and a device that answers a changed password with 403. An
            # endpoint the account may not read is not in the tally, so a
            # cycle that sent only such a page (start-up just read the rest,
            # or a state condition held it back) is no verdict. Raise
            # typed so the fault reaches the device card as auth_failed
            # instead of the device sitting "connected" with stale state.
            # BaseDriver's poll loop drops the connection on it at once rather
            # than counting it as a missed poll, so the credentials are not
            # sent for more cycles, and the platform then waits for them to
            # change.
            raise ConnectionFaultError(
                "The device refused the username and password. "
                "Check them in the device settings.",
                code="auth_failed",
            )

    # Shared send_frame framing (see compiled_protocol module docs).
    _build_send_frame = staticmethod(compiled_protocol.build_send_frame)

    def _apply_send_frame(self, data: bytes) -> bytes:
        """Wrap escape-decoded byte-stream payload in the send_frame header.

        No-op unless the driver declares a send_frame block. The data-length
        field is computed from ``len(data)`` per message — the piece a static
        command_prefix can't express.
        """
        return compiled_protocol.apply_send_frame(self._send_frame, data)

    def _create_frame_parser(self) -> FrameParser | None:
        """Check definition for frame parser config.

        Delegates to the shared declarative builder (length_prefix,
        fixed_length, struct_frame) — the same interpreter the push
        channel's per-subscription framing uses.
        """
        from openavc.transport.frame_parsers import build_frame_parser

        parser_config = self._definition.get("frame_parser")
        if not parser_config:
            return None
        return build_frame_parser(parser_config)


def create_configurable_driver_class(
    driver_def: dict[str, Any],
) -> type[ConfigurableDriver]:
    """
    Factory: create a ConfigurableDriver subclass from a JSON definition.

    Returns a new class with the correct DRIVER_INFO and _definition
    attributes, ready to be registered in the driver registry.
    """
    driver_id = driver_def.get("id", "unknown")

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

    # Copy the models the driver lists, so a driver installed from a file
    # (not the catalog) still says which models it covers.
    if "compatible_models" in driver_def:
        driver_info["compatible_models"] = driver_def["compatible_models"]

    # Copy child_entity_types from the YAML definition. BaseDriver reads
    # this on register_child / set_child_state to validate properties and
    # platform-inject the synthetic `online` / `label` keys. The cloud
    # state-relay also reads it to honour per-property `cloud_priority`
    # tags (high/low) for tier selection.
    if "child_entity_types" in driver_def:
        driver_info["child_entity_types"] = driver_def["child_entity_types"]

    # Copy the bridge declaration — typed serial/IR/relay ports that other
    # devices connect *through*. Read by get_driver_bridge_ports() and the
    # bridge resolver (core.device_config).
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

    # Copy the routing declaration — where this driver's routing lives, in its
    # own words, so the Matrix picker reads the device instead of guessing at
    # it. Read by ui.matrix_inference off the LIVE driver, which is why it has
    # to be here and not only in the file: a definition on disk is not what the
    # picker asks.
    if "routing" in driver_def:
        driver_info["routing"] = driver_def["routing"]

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

    # Copy the web-interface declaration (true, or a URL template). Read by
    # resolve_device_actions to auto-add the Open Web UI link action.
    if "web_ui" in driver_def:
        driver_info["web_ui"] = driver_def["web_ui"]

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
