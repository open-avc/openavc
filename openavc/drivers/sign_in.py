"""A YAML driver's ``auth:`` sign-in: a Telnet-style login over TCP or serial.

The rules worth knowing before changing anything here:

* **Connected means signed in.** The sign-in runs in ``_post_connect``,
  before ``BaseDriver`` reports the device connected, so ``connected`` (and
  ``device.connected``, and everything riding on it) never means "socket
  open, login pending". A refused login fails the connect attempt instead of
  flapping the device online and offline.
* **The line is read raw.** Many prompts arrive without the protocol's
  delimiter (a bare ``Login: ``), so the frame parser comes off the
  transport for the sign-in and goes back on afterwards, on every path once
  the sign-in has started (an ``auth:`` block that does not read fails the
  connect before that, and the connect closes the transport).
  Bytes the parser had already buffered move into the sign-in's buffer.
  Raw mode is switched on before the TCP connect, so a prompt sent the
  moment the connection opens is never run through the reply rules.
* **Each stage reads only what came after the last.** A banner mentioning
  "password", or an echoed credential, cannot satisfy a later stage.
* **The offline reason is precise.** ``auth_failed`` when the device
  refused the login (a failure pattern matched, or no success indicator
  after the credentials went out); ``no_response`` when the expected prompt
  never came at all (wrong host, port or protocol, not a credential).
* **A blank credential still listens.** The sign-in is skipped when the
  credential it types first is blank (``skip_if_empty``, the default),
  which is right for a unit with no password set. A unit that has one still
  prompts, so on TCP the first moments after connect are held raw: a prompt
  among them fails the connect as ``auth_failed`` naming the blank setting;
  otherwise they replay through the frame parser into the reply rules.
* **The flood cap.** Once the raw buffer passes ``_AUTH_MAX_BUFFER`` the
  stage waiting aborts as ``no_response`` (the blank-credential listen ends
  and replays what came): this is an unauthenticated path, and a device (or
  anything spoofing it) could otherwise stream forever before any prompt
  matched.

``SignIn`` is a mixin ``ConfigurableDriver`` inherits, so every name stays
on the driver object. From its host it needs ``device_id``, ``_definition``,
``config``, ``transport``, ``on_data_received``, ``redact_in_log`` and
``_effective_transport``; the host calls ``_init_sign_in()`` in
``__init__`` and ``_arm_sign_in()`` before each dial, and hands every byte
to ``_take_sign_in_bytes`` while ``_auth_mode`` is on.

``drivers/dry_run.py`` previews the sign-in without a device: it arms it
with ``_arm_sign_in()``, sets ``_auth_event`` so the drain after the
password does not wait, and runs ``_perform_auth_handshake`` with its own
``_auth_wait_for`` standing in. So every stage waits through that method.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, NamedTuple

from openavc.drivers.base import ConnectionFaultError
from openavc.drivers.param_labels import param_label
from openavc.transport.binary_helpers import encode_escape_sequences as _safe_encode_escapes
from openavc.transport.frame_parsers import DEFAULT_MAX_BUFFER
from openavc.utils.logger import get_logger

# These lines have always logged under the YAML driver runtime's name, which
# is where a device's log view files them.
log = get_logger("openavc.drivers.configurable")

# Hard cap on bytes buffered during the sign-in. A login banner and its
# prompts never approach the frame parser's own ceiling, and without a cap
# every chunk would re-scan a growing buffer on an unauthenticated path.
_AUTH_MAX_BUFFER = DEFAULT_MAX_BUFFER

# A TCP sign-in skipped because the credential it types first is blank still
# listens for the device's first prompt for up to this long after connect, so
# a device that wants one fails as auth_failed instead of taking the start-up
# lines as its password. It stops early once bytes have arrived and the line
# has been quiet for _BLANK_SIGN_IN_QUIET_S with no prompt among them. Sized
# from a measured unit that prints its banner up to a second after connect and
# its prompt as a separate write straight after it.
_BLANK_SIGN_IN_WINDOW_S = 2.5
_BLANK_SIGN_IN_QUIET_S = 0.5

# The handshake's own default, and what a zero or negative timeout falls back
# to: one emptied to 0 (the Driver Builder's box cleared) would abort on the
# first look and the device could never connect.
_DEFAULT_TIMEOUT_S = 10.0


class _Login(NamedTuple):
    """One sign-in, read from the ``auth:`` block and the device's config."""

    username: str
    password: str
    user_re: re.Pattern[str]
    pass_re: re.Pattern[str]
    success_re: re.Pattern[str] | None
    failure_re: re.Pattern[str] | None
    timeout: float
    line_ending: str


def _compile(pattern: Any) -> re.Pattern[str]:
    try:
        return re.compile(pattern)
    except re.error as e:
        raise ValueError(f"Invalid auth regex pattern: {e}") from e


def _compile_optional(pattern: Any) -> re.Pattern[str] | None:
    return _compile(pattern) if pattern else None


def _stage_timed_out(
    target: re.Pattern[str], text: str, creds_sent: bool,
) -> ConnectionFaultError:
    """The fault for a stage whose pattern never came."""
    if creds_sent:
        # The prompts flowed and the credentials went out, but the
        # success indicator never came: a refused login.
        return ConnectionFaultError(
            f"no login confirmation after sending credentials (timeout "
            f"waiting for {target.pattern!r}; got {text!r}) — check the "
            f"username and password.",
            code="auth_failed",
        )
    return ConnectionFaultError(
        f"the device never presented the expected login prompt (timeout "
        f"waiting for {target.pattern!r}; got {text!r}) — wrong host, "
        f"port, or protocol for this device?",
        code="no_response",
    )


class SignIn:
    """The ``auth:`` login handshake and the listen for a blank credential."""

    def _init_sign_in(self) -> None:
        """The sign-in's state, outside any connect."""
        # Raw mode: on_data_received hands every byte to the sign-in.
        self._auth_mode: bool = False
        self._auth_buffer: bytearray = bytearray()
        self._auth_event: asyncio.Event = asyncio.Event()
        # Set once _auth_buffer passes _AUTH_MAX_BUFFER, so the stage waiting
        # aborts instead of the buffer growing without end.
        self._auth_overflow: bool = False
        # Decoded-text offset just past the last stage's match.
        self._auth_search_pos: int = 0
        # What connect() planned (_arm_sign_in): run the handshake, or listen
        # for a prompt a blank credential cannot answer.
        self._auth_pending: bool = False
        self._auth_watch_blank: bool = False

    def _arm_sign_in(self) -> None:
        """Plan this connect's sign-in, and read the line raw from the first
        byte when there is one."""
        auth_def = self._definition.get("auth")
        plan = self._auth_plan(auth_def) if isinstance(auth_def, dict) else None
        self._auth_pending = plan == "run"
        self._auth_watch_blank = plan == "blank"
        if self._auth_pending or self._auth_watch_blank:
            self._auth_buffer = bytearray()
            self._auth_event = asyncio.Event()
            self._auth_overflow = False
            self._auth_search_pos = 0
            self._auth_mode = True

    def _auth_plan(self, auth_def: dict[str, Any]) -> str | None:
        """What connect() does about the `auth:` block: "run" the handshake,
        "blank" (skipped because the credential it types first is blank, so
        on TCP only listen for a prompt nobody can answer), or None (no
        handshake applies). Mirrors the early-exit checks in _perform_auth_handshake
        so the two stay aligned."""
        if auth_def.get("type", "telnet_login") != "telnet_login":
            return None
        # The handshake assumes a TCP/serial byte stream — it swaps out the
        # frame parser and buffers raw bytes. On udp/http/osc that breaks the
        # transport's normal data path, so never run it there. The loader also
        # rejects auth on these transports; this is the runtime backstop.
        if self._definition.get("transport") not in ("tcp", "serial"):
            return None
        if not auth_def.get("username_prompt") or not auth_def.get("password_prompt"):
            return None
        username_field = auth_def.get("username_field", "username")
        username = str(self.config.get(username_field, "") or "")
        if auth_def.get("skip_if_empty", True) and not username:
            # A serial line has no connect for a device to prompt on.
            return "blank" if self._effective_transport() == "tcp" else None
        return "run"

    def _auth_should_run(self, auth_def: dict[str, Any]) -> bool:
        """True when connect() runs the login handshake."""
        return self._auth_plan(auth_def) == "run"

    async def _post_connect(self) -> None:
        """Sign in before BaseDriver reports the device connected.

        A raise here propagates to BaseDriver.connect(), which stashes the
        transport error, closes the transport, and fails the attempt — so a
        device with a rejected login never sets `connected` or emits
        device.connected.
        """
        await super()._post_connect()
        if not self._sign_in_can_start():
            self._auth_mode = False
            return
        self._saved_frame_parser = self._read_line_raw(self.transport)
        try:
            if self._auth_pending:
                await self._perform_auth_handshake()
            else:
                await self._refuse_unanswerable_prompt()
        except Exception as e:
            log.error(f"[{self.device_id}] Auth handshake failed: {e}")
            raise

    def _sign_in_can_start(self) -> bool:
        """A sign-in is planned and the transport is still up. One that died
        between create and here is BaseDriver's failure paths' to clean up."""
        if not (self._auth_pending or self._auth_watch_blank):
            return False
        transport = self.transport
        return transport is not None and bool(getattr(transport, "connected", False))

    def _read_line_raw(self, transport: Any) -> Any:
        """Take the frame parser off the transport for the sign-in, moving
        anything it had buffered into the sign-in's buffer; the parser taken
        off. The handshake and the listen put it back on every path."""
        saved_parser = getattr(transport, "_frame_parser", None)
        if hasattr(transport, "_frame_parser"):
            self._take_parser_backlog(saved_parser)
            transport._frame_parser = None
        return saved_parser

    def _take_parser_backlog(self, parser: Any) -> None:
        if parser is None or not hasattr(parser, "_buffer"):
            return
        pending = bytes(parser._buffer)
        if pending:
            self._auth_buffer.extend(pending)
            self._auth_event.set()
            parser._buffer = b""

    def _end_raw_read(self, saved_parser: Any) -> None:
        """Out of raw mode, the buffer emptied, the frame parser back on."""
        self._auth_mode = False
        self._auth_buffer = bytearray()
        if hasattr(self.transport, "_frame_parser"):
            self.transport._frame_parser = saved_parser  # type: ignore[union-attr]

    def _take_sign_in_bytes(self, data: bytes) -> None:
        """During the sign-in, every byte goes to its buffer; the stage
        waiting decides what it means."""
        self._auth_buffer.extend(data)
        if len(self._auth_buffer) > _AUTH_MAX_BUFFER:
            # A pre-auth flood: the stage waiting sees the flag and aborts.
            self._auth_overflow = True
        self._auth_event.set()

    async def _refuse_unanswerable_prompt(self) -> None:
        """Listen for a prompt the blank credential cannot answer.

        For a moment after connect, before anything is sent, the bytes are
        held raw: the first prompt among them fails the connection as
        auth_failed, naming the blank setting. Otherwise they go through the
        normal response matching, as they would have without the wait.
        """
        auth_def = self._definition.get("auth") or {}
        field = auth_def.get("username_field", "username")
        saved_parser = getattr(self, "_saved_frame_parser", None)
        replay: list[bytes] = []
        try:
            prompt = _compile(auth_def.get("username_prompt", ""))
            await self._listen_for_prompt(prompt, field)
            # No prompt: what arrived meanwhile is the device's own traffic.
            pending = bytes(self._auth_buffer)
            if pending:
                replay = saved_parser.feed(pending) if saved_parser is not None else [pending]
        finally:
            self._end_raw_read(saved_parser)
        for frame in replay:
            await self.on_data_received(frame)

    async def _listen_for_prompt(self, prompt: re.Pattern[str], field: str) -> None:
        """Until the window ends, a flood, or bytes then quiet: raise the
        moment the prompt is among the bytes."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _BLANK_SIGN_IN_WINDOW_S
        while True:
            self._auth_event.clear()
            text = self._auth_buffer.decode("utf-8", errors="replace")
            if prompt.search(text):
                raise self._blank_sign_in_fault(field)
            if self._auth_overflow:
                return
            remaining = deadline - loop.time()
            if remaining <= 0:
                return
            if await self._went_quiet(remaining):
                return

    async def _went_quiet(self, remaining: float) -> bool:
        """Wait for more bytes: True when some had come and none followed."""
        wait = min(remaining, _BLANK_SIGN_IN_QUIET_S) if self._auth_buffer else remaining
        try:
            await asyncio.wait_for(self._auth_event.wait(), timeout=wait)
        except asyncio.TimeoutError:
            return bool(self._auth_buffer)
        return False

    def _blank_sign_in_fault(self, field: str) -> ConnectionFaultError:
        schema = (self._definition.get("config_schema") or {}).get(field)
        label = param_label(field, schema if isinstance(schema, dict) else None)
        return ConnectionFaultError(
            f"The device asks for a sign-in, and the {label} setting is blank.",
            code="auth_failed",
        )

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

        Runs with the line already raw (``_post_connect`` took the frame
        parser off); the parser goes back on once the stages end, however
        they end.
        """
        auth_def = self._definition.get("auth")
        if not isinstance(auth_def, dict) or not self._auth_should_run(auth_def):
            self._auth_mode = False
            return
        login = self._read_login(auth_def)
        saved_parser = getattr(self, "_saved_frame_parser", None)
        try:
            await self._sign_in_stages(login)
        finally:
            self._end_raw_read(saved_parser)

    def _read_login(self, auth_def: dict[str, Any]) -> _Login:
        """The credentials, patterns, timeout and line ending one sign-in
        uses. The password is masked in this device's log lines from here
        on, whatever its field is called."""
        username = str(self.config.get(auth_def.get("username_field", "username"), "") or "")
        password = str(self.config.get(auth_def.get("password_field", "password"), "") or "")
        if password:
            self.redact_in_log(password)
        timeout = float(auth_def.get("timeout_seconds", _DEFAULT_TIMEOUT_S))
        line_ending = auth_def.get("line_ending", "\r\n")
        return _Login(
            username=username,
            password=password,
            user_re=_compile(auth_def.get("username_prompt", "")),
            pass_re=_compile(auth_def.get("password_prompt", "")),
            success_re=_compile_optional(auth_def.get("success_pattern")),
            failure_re=_compile_optional(auth_def.get("failure_pattern")),
            timeout=_DEFAULT_TIMEOUT_S if timeout <= 0 else timeout,
            line_ending=line_ending,
        )

    async def _sign_in_stages(self, login: _Login) -> None:
        """Username at its prompt, password at its prompt, then the success
        indicator if the block declares one."""
        ending = _safe_encode_escapes(login.line_ending)
        log.info(f"[{self.device_id}] Starting auth handshake")
        self._auth_search_pos = 0
        await self._auth_wait_for(
            login.user_re, login.failure_re, login.timeout, stage="username_prompt",
        )
        await self.transport.send(login.username.encode("utf-8") + ending)
        log.debug(f"[{self.device_id}] Auth: sent username")
        await self._auth_wait_for(
            login.pass_re, login.failure_re, login.timeout, stage="password_prompt",
        )
        await self.transport.send(login.password.encode("utf-8") + ending)
        log.debug(f"[{self.device_id}] Auth: sent password")
        await self._confirm_sign_in(login)

    async def _confirm_sign_in(self, login: _Login) -> None:
        """Without a success indicator, success is assumed once the password
        is sent (the next command fails visibly if the login was refused)."""
        if login.success_re is not None:
            await self._auth_wait_for(
                login.success_re, login.failure_re, login.timeout, stage="success",
            )
            log.info(f"[{self.device_id}] Auth handshake complete")
            return
        # Drain any post-password noise so it doesn't pollute the first real
        # command's response window.
        try:
            await asyncio.wait_for(self._auth_event.wait(), timeout=0.5)
        except asyncio.TimeoutError:
            pass
        log.info(
            f"[{self.device_id}] Auth handshake complete "
            f"(no success_pattern; assuming OK)"
        )

    async def _auth_wait_for(
        self,
        target: re.Pattern[str],
        failure: re.Pattern[str] | None,
        timeout: float,
        stage: str = "prompt",
    ) -> None:
        """Wait until `target` regex matches the auth bytes received since
        the previous stage's match.

        Patterns are string regexes matched against the buffer's UTF-8
        decoding (errors=replace). Raises a typed ConnectionFaultError so
        the offline reason is precise (the module docstring).
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
            self._refuse_auth_flood(target)
            text = self._auth_buffer.decode("utf-8", errors="replace")[
                self._auth_search_pos:
            ]
            if self._stage_reached(target, failure, text):
                return
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                break
            try:
                await asyncio.wait_for(self._auth_event.wait(), timeout=remaining)
            except asyncio.TimeoutError:
                break
        raise _stage_timed_out(target, text, creds_sent)

    def _refuse_auth_flood(self, target: re.Pattern[str]) -> None:
        if self._auth_overflow:
            raise ConnectionFaultError(
                f"auth aborted: more than {_AUTH_MAX_BUFFER} bytes "
                f"received before matching {target.pattern!r} — wrong "
                f"protocol for this device?",
                code="no_response",
            )

    def _stage_reached(
        self,
        target: re.Pattern[str],
        failure: re.Pattern[str] | None,
        text: str,
    ) -> bool:
        """The failure pattern first (raised as auth_failed), then the target;
        a match consumes through itself, so the next stage cannot be
        satisfied by bytes that arrived before this one."""
        if failure is not None and failure.search(text):
            raise ConnectionFaultError(
                f"login rejected by the device "
                f"(matched failure pattern in {text!r})",
                code="auth_failed",
            )
        m = target.search(text)
        if m is None:
            return False
        self._auth_search_pos += m.end()
        return True
