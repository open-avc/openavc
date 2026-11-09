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
connection: the ``auth:`` sign-in (``sign_in``), the children an
``instances:`` block declares (``declared_roster``), whether an HTTP device
is refusing the credentials (``http_credential``), and the ``liveness:``
watchdog (``liveness_block``). What goes out: the framing of every byte the
device's protocol puts on the wire, from every place that sends
(``send_framing``), and an HTTP command's request (``http_commands``). The
lines the driver sends on its own: start-up, the poll and the spacing
between their lines (``start_up_and_poll``), what a poll or start-up entry
sends and whether its gates let it go now (``query_entries``), and reading
the device again after a change, a reply rule's ``then_query`` and a
command's ``read_back:`` (``rereads``). Writing a device setting:
``setting_writes``.
"""

from __future__ import annotations

import re
from typing import Any

from openavc.drivers import compiled_protocol
from openavc.drivers.base import (
    UNKNOWN_COMMAND,
    BaseDriver,
    normalize_and_validate_command_params as _normalize_and_validate_command_params,
)
from openavc.drivers.declared_roster import DeclaredRoster
from openavc.drivers.http_commands import HttpCommands
from openavc.drivers.http_credential import HttpCredential
from openavc.drivers.json_replies import JsonReplies
from openavc.drivers.liveness_block import LivenessBlock
from openavc.drivers.osc_wire import OscWire
from openavc.drivers.query_entries import QueryEntries
from openavc.drivers.reply_gate import ReplyGate
from openavc.drivers.reply_throttle import ReplyThrottle
from openavc.drivers.reply_values import ReplyValues
from openavc.drivers.rereads import Rereads
from openavc.drivers.send_framing import SendFraming
from openavc.drivers.setting_writes import SettingWrites
from openavc.drivers.side_sends import SideSends
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
from openavc.drivers.yaml_driver_info import build_commands_meta, driver_info_for
from openavc.transport.frame_parsers import FrameParser
from openavc.utils.logger import get_logger

log = get_logger(__name__)


def _param_wire_value(name: str, value: Any, pdef: dict[str, Any]) -> dict[str, Any]:
    """A parameter as it goes on the wire, when its declaration translates
    it: its ``map:`` word, else through its ``scale`` / ``offset`` (the real
    value back to the device's own number); nothing when it declares
    neither. Only a declared conversion converts: a None with no scale stays
    None even if ``unknown`` names a code for it."""
    mapped = compiled_protocol.map_word(value, pdef.get("map"))
    if mapped is not None:
        return {name: mapped}
    if pdef.get("scale") is not None or pdef.get("offset") is not None:
        return {name: compiled_protocol.value_to_wire(value, pdef)}
    return {}


def _inline_poll_queries(commands: dict[str, Any]) -> list[str]:
    """The poll lines a device's own commands add: those the editor flagged
    "poll" are sent on the device's poll_interval to keep status values live
    (most AV gear doesn't push). A byte-stream command's query is its send
    string (line ending included); an HTTP or OSC command has no send string,
    so its name is used (poll() looks the name up)."""
    return [
        cmd["send"] if isinstance(cmd.get("send"), str) else name
        for name, cmd in commands.items()
        if cmd.get("poll")
    ]


# One {name} / {name:spec} token left unsubstituted on the way to the wire.
# Same shape the authoring check uses, so both agree on what counts as a
# placeholder and a literal JSON brace is neither.
_WIRE_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)(?::[^{}]*)?\}")


class ConfigurableDriver(
    StartUpAndPoll,
    Rereads,
    SettingWrites,
    SideSends,
    HttpCommands,
    SendFraming,
    LivenessBlock,
    SignIn,
    HttpCredential,
    QueryEntries,
    DeclaredRoster,
    ReplyThrottle,
    TextReplies,
    JsonReplies,
    OscWire,
    ReplyGate,
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

    # BaseDriver notes when each poll starts only for a poll() the class
    # defines itself (__init_subclass__ reads the class's own namespace), and
    # the poll loop reads that note after a connect. The poll lives in
    # StartUpAndPoll, so it is defined here by name.
    poll = StartUpAndPoll.poll

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
        self._init_send_framing()
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

        # The lines this driver sends on its own and their spacing
        # (start_up_and_poll), and its follow-up and read-back rounds
        # (rereads).
        self._init_start_up_and_poll()
        self._init_rereads()

        # The `auth:` sign-in's state. Raw mode only during a sign-in;
        # outside it on_data_received goes to the reply rules.
        self._init_sign_in()

        # The `liveness:` block's watchdog (liveness_block).
        self._init_liveness_block()

        # Messages a response rule read (it matched and applied, or a
        # throttle held its writes), and the last one none did: the poll
        # loop's unread-replies check (_parsed_count, BaseDriver._judge_reading).
        self._parsed_messages = 0
        self._framed_messages = 0
        self._last_unread = ""

        compiled = compiled_protocol.compile_driver(
            self._definition, self.config, device_id=self.device_id
        )
        self._compiled_responses = compiled.responses
        self._osc_responses = compiled.osc_responses
        self._json_responses = compiled.json_responses
        self._after_json_responses = compiled.after_json_responses

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
        norm_commands = normalize_config_commands(
            self.config.get("commands"),
            self._inline_suffix(),
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

        if not any((norm_commands, norm_responses, norm_state_vars, ir_commands)):
            return

        # Auto-declare params for {placeholder} tokens so parameterized commands
        # prompt in the Send Command card. config_keys are excluded — a {host}
        # token resolves from config, not a prompt.
        declare_command_params(norm_commands, set(self.config.keys()))

        self._definition = self._merged_definition(
            {**norm_commands, **ir_commands}, norm_responses, norm_state_vars,
            _inline_poll_queries(norm_commands),
        )
        info = dict(self.DRIVER_INFO)
        info["commands"] = build_commands_meta(self._definition["commands"])
        info["state_variables"] = self._definition["state_variables"]
        self.DRIVER_INFO = info

        # Re-seed state variables now that the config-added ones exist.
        # super().__init__() already seeded the file vars; re-seeding pre-poll
        # is harmless (same defaults) and gives the new vars an initial value
        # so they appear on the device card before the first matching reply.
        self._init_state_variables()

    def _merged_definition(
        self,
        commands: dict[str, Any],
        responses: list[dict[str, Any]],
        state_vars: dict[str, Any],
        poll_queries: list[str],
    ) -> dict[str, Any]:
        """The file definition with the device's own protocol over it.

        Commands merge by name (config wins), responses append after the
        file's (so a file driver's base behavior still matches first —
        first-match-wins), and state variables layer the file's, those
        auto-derived from the merged responses, then the config's explicit
        ones. Poll queries append after the file's."""
        merged = dict(self._definition)
        merged_responses = list(merged.get("responses") or []) + responses
        if poll_queries:
            file_polling = merged.get("polling") or {}
            merged["polling"] = {
                **file_polling,
                "queries": list(file_polling.get("queries", [])) + poll_queries,
            }
        merged["commands"] = {**(merged.get("commands") or {}), **commands}
        merged["responses"] = merged_responses
        merged["state_variables"] = {
            **(merged.get("state_variables") or {}),
            **derive_state_vars_from_responses(merged_responses),
            **state_vars,
        }
        return merged

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
        ``read_back:`` queries once its write is done.

        Two routes need no connection of the device's own, so they come
        before the connected gate: an IR code goes through the bound bridge's
        emitter port (a bridge-routed IR device has no transport), and a UDP
        side-send is one datagram beside the main transport (the whole point
        of ``available_offline`` on a wake is that the control link is down).
        """
        params = params or {}
        cmd_def = self._definition.get("commands", {}).get(command)

        side_send = self._side_sender(cmd_def)
        if side_send is not None:
            return await side_send(command, cmd_def, params)

        if not self._link_open():
            raise ConnectionError(f"[{self.device_id}] Not connected")

        if cmd_def is None:
            log.warning(f"[{self.device_id}] Unknown command: {command}")
            self._observe(UNKNOWN_COMMAND, command=command)
            return None

        given, wire = self._given_and_wire(command, cmd_def, params)
        result = await self._send_in_form(command, cmd_def, wire)
        # Read back whatever the device answered: an HTTP set it refused
        # shows its unchanged value. Nothing went out, nothing to read.
        if read_back and result is not None:
            self._read_back(command, cmd_def, given, wire)
        return result

    def _given_and_wire(
        self, command: str, cmd_def: dict[str, Any], params: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """A command's parameters as the caller meant them, checked against
        its declared schema (trimmed, validated; raises CommandParamError on
        a bad value), and as they go on the wire.

        A param may declare a ``map:`` translating the validated value to
        what the protocol wants (0-based channel numbers, letter codes, ...),
        applied after validation so min/max/enum checks see the user-facing
        value, and before any send so every form gets the wire value. A
        read-back that names another command passes the caller's values on,
        and that command maps them itself."""
        declared = cmd_def.get("params", {})
        given = _normalize_and_validate_command_params(command, declared, params)
        return given, self._apply_param_wire_maps(declared, given)

    async def _send_in_form(
        self, command: str, cmd_def: dict[str, Any], params: dict[str, Any],
    ) -> Any:
        """One command in the form its definition takes: an OSC message
        (``address``), an HTTP request (``path`` or ``method``), else a
        byte-stream line. None when nothing went out."""
        if self._is_osc_command(cmd_def):
            return await self._send_osc_command(command, cmd_def, params)
        if self._is_http_command(cmd_def):
            return await self._send_http_command(command, cmd_def, params)
        data = self.build_wire(command, params)
        if data is None:
            return None
        await self.transport.send(data)
        log.debug(f"[{self.device_id}] Sent command '{command}': {data!r}")
        return True

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
        changed: dict[str, Any] = {}
        for name, pdef in param_defs.items():
            if isinstance(pdef, dict) and name in params:
                changed.update(_param_wire_value(name, params[name], pdef))
        return {**params, **changed} if changed else params

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
    driver_info = driver_info_for(driver_def)
    return type(
        f"ConfigurableDriver_{driver_info['id']}",
        (ConfigurableDriver,),
        {
            "DRIVER_INFO": driver_info,
            "_definition": driver_def,
        },
    )
