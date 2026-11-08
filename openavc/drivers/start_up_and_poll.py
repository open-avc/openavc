"""The lines a YAML driver sends on its own: its start-up steps
(``on_connect``) and its poll (``polling.queries``).

The rules worth knowing before changing anything here:

* **One way to read a line, on every transport.** A line that names a
  declared command runs as that command, so send-side framing applies and
  the reply goes through the reply rules. Anything else is the transport's
  own raw form: a URL path on HTTP (its answer read the same way), an OSC
  address on OSC, the protocol bytes on TCP, serial and UDP (TCP and serial
  ones inside the ``send_frame`` header, a no-op unless declared).
  ``_dispatch_query`` is that rule for every transport but OSC; a response
  rule's ``then_query`` sends through it too. Every start-up and poll answer
  on HTTP is weighed against the credential (``http_credential``).
* **Spacing.** TCP and serial lines are spaced by ``inter_command_delay``
  (the transport waits it after every write) or, with none set, by
  ``_pace_line``. UDP, OSC and HTTP lines are spaced only by
  ``inter_command_delay``; on HTTP, from one response to the next request.
* **The first poll skips what start-up just asked.** Every start-up line
  that went out is noted in ``_start_up_lines``; a connect that polls hands
  them to the first poll, which follows at once, as lines not to send again.
  A line whose send failed is not noted, so the first poll asks it again.
* **What a failure ends.** A failed start-up line is logged and the next one
  sent, except a refused credential (a typed ``auth_failed``), which ends
  start-up and fails the connect. A poll line that fails on the transport
  ends the poll and is raised, so ``BaseDriver``'s poll loop counts a missed
  poll (or, on ``auth_failed``, drops the connection at once): swallowing it
  is how an unreachable HTTP, OSC or UDP device once stayed "connected". Any
  other failure (a template, an encoding, a parse) is reported as
  ``device.error`` and the poll goes on.
* **OSC start-up then asks every reply address** with no arguments, OSC's
  way to read a value, so state fills in at once. A pattern with wildcards
  is a match, not an address, and is skipped; the ask is held back by
  ``polling.when_state`` as the poll is (any message wakes a unit in
  standby), and is spaced 5 ms apart unless an ``inter_command_delay``
  spaces it.

``StartUpAndPoll`` is a mixin ``ConfigurableDriver`` inherits, so every name
stays on the driver object; it answers ``BaseDriver``'s ``poll``. The device
audit and the Driver Builder's dry run run ``_run_on_connect`` against a
transport that records (``drivers/dry_run.py``).
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable

from openavc.drivers.base import ConnectionFaultError
from openavc.transport.binary_helpers import encode_escape_sequences as _safe_encode_escapes
from openavc.utils.logger import get_logger

# These lines have always logged under the YAML driver runtime's name, which
# the IDE's log view shows as their source.
log = get_logger("openavc.drivers.configurable")

# httpx's transport, timeout and status errors fail a poll line the way a
# socket error does (BaseDriver._poll_loop counts them as a missed poll).
try:
    import httpx as _httpx

    _HTTP_TRANSPORT_ERRORS: tuple[type[BaseException], ...] = (_httpx.HTTPError,)
except ImportError:  # pragma: no cover - httpx is a hard dependency in practice
    _HTTP_TRANSPORT_ERRORS = ()

# A poll line that fails on the transport (HTTP connect errors arrive as the
# builtin ConnectionError: http_client translates httpx.ConnectError).
_TRANSPORT_FAILURES = (ConnectionError, TimeoutError, OSError)

# A pattern character that makes an OSC reply address a match, not an address.
_OSC_WILDCARDS = "*?["


def _osc_message(address: str, args: list[Any] | None = None) -> bytes:
    from openavc.transport.osc_codec import osc_encode_message

    return osc_encode_message(address, args)


class StartUpAndPoll:
    """Start-up and poll lines, for ``ConfigurableDriver``."""

    # Supplied by BaseDriver / ConfigurableDriver / the other mixins; declared
    # here for the methods.
    device_id: str
    config: dict[str, Any]
    transport: Any
    events: Any
    _definition: dict[str, Any]
    _osc_responses: list[Any]
    _pace_line: Callable[[], Any]
    _expand_query: Callable[[Any], list[str]]
    _query_enabled: Callable[[dict[str, Any]], bool]
    _line_gate_shut: Callable[..., Any]
    _poll_gate_shut: Callable[[dict[str, Any]], bool]
    _send_command: Callable[..., Any]
    _process_http_response: Callable[..., Any]
    _judge_http_answer: Callable[[str, Any], None]
    _open_http_cycle: Callable[[bool], None]
    _drop_http_cycle: Callable[[], None]
    _close_http_cycle: Callable[[], None]
    _substitute_for_wire: Callable[[str, str, dict[str, Any]], str]
    _safe_substitute: Callable[[str, dict[str, Any]], str]
    _build_osc_args: Callable[[list[Any], dict[str, Any]], list[Any]]
    _apply_send_frame: Callable[[bytes], bytes]

    def _init_start_up_and_poll(self) -> None:
        # The on_connect lines the latest start-up sent, and those the first
        # poll after a connect skips because start-up has just sent them.
        self._start_up_lines: set[str] = set()
        self._first_poll_skips: set[str] | None = None

    def _skip_on_first_poll(self, polls: bool) -> None:
        """At connect: the first poll follows straight on, so what start-up
        just asked for is not asked again (none when the device polls
        nothing)."""
        self._first_poll_skips = set(self._start_up_lines) if polls else None

    def _osc_address(self, template: str) -> str:
        """An OSC address the driver sends on its own, its {config}
        placeholders filled."""
        return self._safe_substitute(template, self.config) if "{" in template else template

    # --- start-up ---

    async def _run_on_connect(self) -> None:
        """Send the declared start-up steps (``on_connect``).

        ``inter_command_delay`` is applied by the transport after every write
        (tcp, serial, udp, osc, which writes through one of those, and http,
        after each response), so nothing waits for it here.
        """
        self._start_up_lines = set()
        on_connect = self._definition.get("on_connect", [])
        if not on_connect or not self._link_open():
            return
        if self._definition.get("transport") == "osc":
            await self._run_osc_on_connect(on_connect)
        else:
            await self._run_line_on_connect(on_connect)

    def _link_open(self) -> bool:
        return bool(self.transport and self.transport.connected)

    async def _run_line_on_connect(self, on_connect: list[Any]) -> None:
        for raw in on_connect:
            for query in self._expand_query(raw):
                await self._send_start_up_line(raw, query)

    async def _run_osc_on_connect(self, on_connect: list[Any]) -> None:
        delay = self.config.get("inter_command_delay", 0)
        for item in on_connect:
            await self._send_osc_start_up_item(item)
        await self._ask_osc_reply_addresses(delay)

    async def _send_start_up_line(self, raw: Any, query: str) -> None:
        """One start-up line of a byte-stream or HTTP driver."""
        try:
            await self._pace_line()
            # A step's when_state gate is judged again once the step before
            # it has been answered.
            if await self._line_gate_shut(None, raw):
                return
            await self._dispatch_query(query)
            self._start_up_lines.add(query)
        except Exception as e:
            if isinstance(e, ConnectionFaultError) and e.fault_code == "auth_failed":
                raise
            log.warning(f"[{self.device_id}] on_connect command failed: {e}")

    async def _send_osc_start_up_item(self, item: Any) -> None:
        """One OSC start-up entry: a bare address (a string, or ``send``), an
        ``{address, args}`` message, or an ``each_child`` expansion."""
        try:
            if isinstance(item, dict) and "each_child" in item:
                for expanded in self._expand_query(item):
                    await self.transport.send(_osc_message(self._osc_address(expanded)))
                    self._start_up_lines.add(expanded)
                return
            built = self._osc_start_up_message(item)
            if built is None or await self._line_gate_shut(None, item):
                return
            data, line = built
            await self.transport.send(data)
            if line:
                self._start_up_lines.add(line)
        except Exception as e:
            log.warning(f"[{self.device_id}] on_connect OSC command failed: {e}")

    def _osc_start_up_message(self, item: Any) -> tuple[bytes, str | None] | None:
        """An OSC start-up entry's message, and the line it notes (none for a
        message with args: that writes a value rather than reading one).
        None for an entry that is not sent."""
        if isinstance(item, str):
            return _osc_message(self._osc_address(item)), item
        if not isinstance(item, dict) or not self._query_enabled(item):
            return None
        # Keyed "address" (the {address, args} form) or "send" (a plain
        # gated entry), so a gated subscription is never an empty address.
        address = item.get("address") or item.get("send") or ""
        line = None if item.get("args") else address
        address = self._osc_address(address)
        args = self._build_osc_args(item.get("args", []), self.config)
        return _osc_message(address, args), line

    async def _ask_osc_reply_addresses(self, delay: Any) -> None:
        """Ask every OSC reply address once, with no arguments."""
        polling = self._definition.get("polling") or {}
        for addr_pattern, *_rest in self._osc_responses:
            if any(c in addr_pattern for c in _OSC_WILDCARDS):
                continue
            if await self._line_gate_shut(polling, None):
                break
            try:
                await self.transport.send(_osc_message(self._osc_address(addr_pattern)))
                self._start_up_lines.add(addr_pattern)
                if not delay:
                    await asyncio.sleep(0.005)
            except Exception as e:
                log.warning(f"[{self.device_id}] OSC initial query failed: {e}")

    # --- one line, any transport but OSC ---

    async def _dispatch_query(self, query: str) -> None:
        """Send one start-up, poll or follow-up line on a transport that is
        not OSC: a declared command by name, else the transport's raw form."""
        transport_type = self._definition.get("transport")
        if query in self._definition.get("commands", {}):
            response = await self._send_command(query)
        else:
            response = await self._send_raw_line(transport_type, self._line_text(query))
        if transport_type == "http":
            # Both forms are weighed: a raw path is the most common HTTP
            # polling form, a command name the other.
            self._judge_http_answer(query, response)

    def _line_text(self, query: str) -> str:
        if "{" not in query:
            return query
        return self._substitute_for_wire("polling query", query, self.config)

    async def _send_raw_line(self, transport_type: Any, text: str) -> Any:
        """A line that names no command, as the transport takes it; on HTTP,
        the answer (read by the reply rules, as a command's is)."""
        if transport_type == "http":
            response = await self.transport.get(text)
            await self._process_http_response(text, response)
            return response
        data = _safe_encode_escapes(text)
        if transport_type != "udp":
            data = self._apply_send_frame(data)
        await self.transport.send(data)
        return None

    # --- the poll ---

    async def poll(self) -> None:
        """Send the poll's lines (``polling.queries``), in order."""
        if not self._link_open():
            return
        polling = self._definition.get("polling", {})
        queries = polling.get("queries", [])
        # The whole poll waits while the device's own state says so
        # (polling.when_state). A cycle that sends nothing is neither silent
        # nor unread to BaseDriver._poll_loop, so the connection stays up.
        if self._poll_gate_shut(polling):
            self._first_poll_skips = None
            return
        transport_type = self._definition.get("transport")
        self._open_http_cycle(transport_type == "http")
        skips = self._first_poll_skips
        self._first_poll_skips = None
        is_osc = transport_type == "osc"
        for raw_query in queries:
            if await self._poll_entry(polling, raw_query, skips, is_osc):
                break
        # Every request this cycle refused is the credentials themselves.
        self._close_http_cycle()

    async def _poll_entry(
        self, polling: dict[str, Any], raw_query: Any, skips: set[str] | None, is_osc: bool,
    ) -> bool:
        """One poll entry's lines; True when the poll's gate shut and the
        rest of the poll is held."""
        for query in self._expand_query(raw_query):
            if skips and query in skips:
                continue
            if await self._poll_line(polling, raw_query, query, is_osc) == "poll":
                return True
        return False

    async def _poll_line(
        self, polling: dict[str, Any], raw_query: Any, query: str, is_osc: bool,
    ) -> str | None:
        """One poll line, and what its failure does to the poll: the gate
        that held it, else None."""
        try:
            return await self._send_poll_line(polling, raw_query, query, is_osc)
        except _TRANSPORT_FAILURES as exc:
            # A refused credential ends the cycle the same way, and drops the
            # connection at once.
            if getattr(exc, "fault_code", None) != "auth_failed":
                log.warning(f"[{self.device_id}] Poll query failed (transport)")
            self._drop_http_cycle()
            raise
        except _HTTP_TRANSPORT_ERRORS as exc:
            log.warning(f"[{self.device_id}] Poll query failed (HTTP): {exc}")
            self._drop_http_cycle()
            raise
        except Exception as exc:
            log.exception(f"[{self.device_id}] Poll query error")
            await self._report_poll_error(exc)
        return None

    async def _send_poll_line(
        self, polling: dict[str, Any], raw_query: Any, query: str, is_osc: bool,
    ) -> str | None:
        """Send one poll line, unless a gate shut since the cycle began (a
        reply to an earlier line, Enter Standby's answer say, and every line
        sent after it would undo it): the gate that shut, else None."""
        if not is_osc:
            await self._pace_line()
        gate = await self._line_gate_shut(polling, raw_query)
        if gate:
            return gate
        if is_osc:
            await self._send_osc_poll_line(query)
        else:
            await self._dispatch_query(query)
        return None

    async def _send_osc_poll_line(self, query: str) -> None:
        if query in self._definition.get("commands", {}):
            await self._send_command(query)
        else:
            await self.transport.send(_osc_message(self._osc_address(query)))

    async def _report_poll_error(self, exc: Exception) -> None:
        """A poll line the device answered but that could not be built or
        read: ``device.error``, and the watchdog is not told."""
        try:
            await self.events.emit(
                f"device.error.{self.device_id}",
                {"device_id": self.device_id, "error": str(exc)},
            )
        except Exception:
            log.exception(f"[{self.device_id}] Failed to emit device.error")
