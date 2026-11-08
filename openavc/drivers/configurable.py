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

from openavc.core.condition_eval import eval_operator
from openavc.core.state_store import VALID_KEY_PREFIXES
from openavc.drivers import compiled_protocol
from openavc.drivers.base import (
    UNKNOWN_COMMAND,
    WAKE_ON_LAN_PORT,
    BaseDriver,
    WakeAddressError,
    normalize_and_validate_command_params as _normalize_and_validate_command_params,
    normalize_mac,
)
from openavc.drivers.param_labels import param_label
from openavc.drivers.spec import (
    DEFAULT_LINE_GAP_S,
    FOLLOW_UP_SETTLE_S,
    LINE_GAP_TRANSPORTS,
)
from openavc.drivers.declared_roster import DeclaredRoster
from openavc.drivers.http_credential import HttpCredential
from openavc.drivers.json_replies import JsonReplies
from openavc.drivers.osc_wire import OscWire
from openavc.drivers.query_entries import QueryEntries
from openavc.drivers.reply_throttle import ReplyThrottle
from openavc.drivers.reply_values import ReplyValues
from openavc.drivers.setting_writes import SettingWrites
from openavc.drivers.sign_in import SignIn
from openavc.drivers.start_up_and_poll import StartUpAndPoll
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
    StartUpAndPoll,
    SettingWrites,
    SignIn,
    HttpCredential,
    QueryEntries,
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
        # poll queries (http_credential).
        self._init_http_credential()

        # Pacing for the lines this driver sends on its own (see _pace_line):
        # when the next may go, and whether to pace at all (a connection
        # preview records what would be sent and must not sit out the gaps).
        self._next_line_at: float = 0.0
        self._pace_lines: bool = True
        self._init_start_up_and_poll()
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

        self._reset_http_credential()

        # Enable auth-buffering BEFORE the TCP connect so any prompt the
        # device emits the moment the connection opens lands in the auth
        # buffer instead of being run through the normal response matcher.
        # The sign-in turns this back off when it's done, and an attempt that
        # fails before then turns it off below.
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

        self._skip_on_first_poll(saved_poll_interval > 0)
        if saved_poll_interval > 0:
            await self.start_polling(saved_poll_interval)

    async def _initial_sync(self) -> None:
        """Register the declared child rosters, then send on_connect.

        Rosters (`instances:` blocks) come first so routed responses land on
        registered children from the first query. A credential refused during
        start-up (_judge_http_answer) is raised from here, which fails the
        connect attempt.
        """
        self._build_declared_roster()

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

    def _unread_sample(self) -> str:
        return self._last_unread

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


# Definition fields copied onto DRIVER_INFO as written, in the order they land
# there, because the platform reads them off a live driver rather than its
# file (tests/test_driver_field_reaches_runtime.py fails a contract field
# that reaches neither this table nor its list of fields read elsewhere).
_INFO_FIELDS = (
    "help",
    "protocols",
    "discovery",
    "device_settings",
    # So a driver installed from a file, not the catalog, still says which
    # models it covers.
    "compatible_models",
    # BaseDriver validates child properties against it and adds the `online`
    # and `label` keys; the cloud state relay reads each property's
    # `cloud_priority`.
    "child_entity_types",
    # The typed ports other devices connect through (get_driver_bridge_ports,
    # the bridge resolver in core.device_config).
    "bridge",
    # Read on connect to subscribe the shared listener.
    "push",
    # The media one protocol runs over (["tcp", "serial"]); the connection
    # picks one, config["transport"] first.
    "transports",
    # The device page's Commands & Responses editor.
    "inline_protocol",
    # The device page's IR Codes editor.
    "ir_codes",
    # Where the routing lives, for the Matrix picker, which asks the live
    # driver (ui.matrix_inference).
    "routing",
)

# The same, landing after ``commands``.
_INFO_FIELDS_AFTER_COMMANDS = (
    # Promoted-command buttons and setup wizards, kept raw: the device info
    # (resolve_device_actions) folds quick_actions into one list, for YAML
    # and Python drivers alike.
    "actions",
    "quick_actions",
    # True, or a URL template: the Open Web UI action.
    "web_ui",
    "delimiter",
)


def _copy_fields(
    driver_info: dict[str, Any], driver_def: dict[str, Any], fields: tuple[str, ...],
) -> None:
    for field in fields:
        if field in driver_def:
            driver_info[field] = driver_def[field]


def create_configurable_driver_class(
    driver_def: dict[str, Any],
) -> type[ConfigurableDriver]:
    """
    Factory: create a ConfigurableDriver subclass from a JSON definition.

    Returns a new class with the correct DRIVER_INFO and _definition
    attributes, ready to be registered in the driver registry.
    """
    driver_id = driver_def.get("id", "unknown")
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
    _copy_fields(driver_info, driver_def, _INFO_FIELDS)

    # Copy help from each state variable
    state_vars = driver_info.get("state_variables", {})
    for var_name, var_def in state_vars.items():
        if isinstance(var_def, dict) and "help" in var_def:
            state_vars[var_name] = {**var_def}

    # Shared with the inline-protocol per-instance merge, so file- and
    # config-authored commands look identical in the IDE.
    driver_info["commands"] = _build_commands_meta(driver_def.get("commands", {}))
    _copy_fields(driver_info, driver_def, _INFO_FIELDS_AFTER_COMMANDS)

    return type(
        f"ConfigurableDriver_{driver_id}",
        (ConfigurableDriver,),
        {
            "DRIVER_INFO": driver_info,
            "_definition": driver_def,
        },
    )
