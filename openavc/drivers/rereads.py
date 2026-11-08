"""A YAML driver reading the device again after a change, at once instead of
at the next poll: a reply rule's ``then_query`` (a follow-up round) and a
command's ``read_back:`` (a read-back round).

The rules worth knowing before changing anything here:

* **Each round is its own task**, tracked so the connection's close cancels
  it (``_drop_follow_ups`` / ``_drop_read_backs``, from ``_close_session``).
  Its lines are paced like a poll's and judged against the gates again just
  before they go (``_pace_line``, ``_line_gate_shut``), and nothing goes out
  while ``polling.when_state`` holds the poll: a notice or a command that put
  a unit in standby is not followed by a query that wakes it.
* **One follow-up round at a time per rule and routed child.** While one is
  going out, and for ``FOLLOW_UP_SETTLE_S`` after its last line, a match
  applies its writes and sends nothing; the replies to the round arrive
  inside that window. A cancelled round leaves no hold behind.
* **A read-back is built when its command is sent**, while the command's
  parameters are at hand, and sent once the command's write is done. A query
  already waiting to go out is not queued again, so a slider dragged through
  many values reads back once, after the last.
* **What a failure ends.** A failed line ends its round with a warning; the
  next poll reads what it missed and is the one that judges the connection,
  so an HTTP answer here is never weighed against the credential.

``Rereads`` is a mixin ``ConfigurableDriver`` inherits, so every name stays on
the driver object. A follow-up line goes out as a poll line does
(``_dispatch_query``); a read-back's raw query is the poll's raw form
(``_raw_bytes`` in ``send_framing``, ``_read_path`` in ``start_up_and_poll``).
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any, Callable

from openavc.drivers.spec import FOLLOW_UP_SETTLE_S
from openavc.transport.pacing import sleep_at_least
from openavc.utils.logger import get_logger

# These lines have always logged under the YAML driver runtime's name, which
# the IDE's log view shows as their source.
log = get_logger("openavc.drivers.configurable")

# A {child_id} / {child_id:spec} token: in a response rule's follow-up query,
# the child the rule wrote (_follow_up_lines).
_CHILD_ID_TOKEN = re.compile(r"\{child_id(?::[^{}]*)?\}")

# What a read-back sends: (key, (kind, what)). ``key`` says two queries are
# the same one.
ReadBack = tuple[str, tuple[str, Any]]


def _running_loop() -> asyncio.AbstractEventLoop | None:
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


def _track(tasks: set[asyncio.Task[None]], task: asyncio.Task[None]) -> None:
    """Hold a round's task until it ends, for the connection's close."""
    tasks.add(task)
    task.add_done_callback(tasks.discard)


def _cancel_all(tasks: set[asyncio.Task[None]]) -> None:
    for task in list(tasks):
        task.cancel()
    tasks.clear()


def _distinct_child_ids(children: list[tuple[str, Any, dict[str, Any]]]) -> list[Any]:
    """The children a rule wrote, each once, in the order it wrote them."""
    child_ids: list[Any] = []
    for _ctype, local_id, _updates in children:
        if local_id not in child_ids:
            child_ids.append(local_id)
    return child_ids


def _read_back_entries(raw: Any) -> list[str]:
    """A ``read_back:`` that is not ``true``, as the entries it names."""
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list):
        return [e for e in raw if isinstance(e, str) and e]
    return []


class Rereads:
    """Follow-up and read-back rounds, for ``ConfigurableDriver``."""

    # Supplied by BaseDriver / ConfigurableDriver / the other mixins; declared
    # here for the methods.
    device_id: str
    config: dict[str, Any]
    transport: Any
    _definition: dict[str, Any]
    _line_gap: Callable[[], float]
    _pace_line: Callable[[], Any]
    _link_open: Callable[[], bool]
    _expand_query: Callable[[Any], list[str]]
    _line_gate_shut: Callable[..., Any]
    _dispatch_query: Callable[[str], Any]
    _raw_bytes: Callable[[Any, str], bytes]
    _read_path: Callable[[str], Any]
    _send_command: Callable[..., Any]
    _push_params: Callable[[], dict[str, Any]]
    _substitute_for_wire: Callable[[str, str, dict[str, Any]], str]
    _safe_substitute: Callable[[str, dict[str, Any]], str]
    _is_osc_command: Callable[[dict[str, Any]], bool]
    _is_http_command: Callable[[dict[str, Any]], bool]

    def _init_rereads(self) -> None:
        # Response rules' then_query rounds (_follow_up): the tasks sending
        # them, and per (rule, routed child) when a round may go again
        # (infinity while one is going out).
        self._follow_up_tasks: set[asyncio.Task[None]] = set()
        self._follow_up_until: dict[tuple[int, str], float] = {}
        # Commands' read_back rounds (_read_back): the tasks sending them,
        # and the queries waiting to go out, so one is not queued twice.
        self._read_back_tasks: set[asyncio.Task[None]] = set()
        self._read_back_waiting: set[str] = set()

    # --- a reply rule's then_query ---

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
        loop = _running_loop()
        if loop is None:
            return
        child_ids = _distinct_child_ids(children)
        self._follow_up_until[key] = float("inf")
        _track(self._follow_up_tasks, loop.create_task(self._send_follow_ups(key, entries, child_ids)))

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
        lines, then hold the rule for FOLLOW_UP_SETTLE_S (_follow_up)."""
        cancelled = False
        try:
            await self._follow_up_round(entries, child_ids)
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

    async def _follow_up_round(self, entries: list[Any], child_ids: list[Any]) -> None:
        """The round's lines, in order, until one ends it."""
        polling = self._definition.get("polling") or {}
        for entry in entries:
            for line in self._follow_up_lines(entry, child_ids):
                if await self._send_follow_up_line(polling, entry, line):
                    return

    async def _send_follow_up_line(self, polling: dict[str, Any], entry: Any, line: str) -> bool:
        """One follow-up line, paced and judged against the gates; True when
        the round ends here: the link is down, or ``polling.when_state``
        holds the poll (a notice from a unit in standby must not wake it). A
        line whose own gate has shut is skipped."""
        if not self._link_open():
            return True
        await self._pace_line()
        gate = await self._line_gate_shut(polling, entry, quiet=True)
        if gate == "poll":
            return True
        if not gate:
            await self._dispatch_query(line)
        return False

    def _drop_follow_ups(self) -> None:
        """Cancel every follow-up round under way: the connection it would
        send on is going away. A round still waiting is forgotten with it, so
        the first notice on the next connection is read at once."""
        _cancel_all(self._follow_up_tasks)
        self._follow_up_until.clear()

    # --- a command's read_back ---

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
        loop = _running_loop()
        if loop is None:
            return
        for key, _what in fresh:
            self._read_back_waiting.add(key)
        _track(self._read_back_tasks, loop.create_task(self._send_read_backs(command, fresh)))

    def _read_back_sends(
        self,
        command: str,
        cmd_def: dict[str, Any],
        given: dict[str, Any],
        wire: dict[str, Any],
    ) -> list[ReadBack]:
        """A command's ``read_back:`` as what each query sends, built now,
        while this call's parameters are at hand: ``(key, (kind, what))``.

        ``kind`` is ``"command"`` (``what`` is the command's name and the
        parameters it shares with this one, as the caller gave them),
        ``"osc"`` (an address, sent with no arguments), ``"http"`` (a path,
        read with GET) or ``"raw"`` (the bytes, built as a poll query's
        are). ``key`` is what says two queries are the same one.
        """
        raw = cmd_def.get("read_back")
        all_params = {**self.config, **self._push_params(), **wire}
        if raw is True:
            return self._read_back_itself(command, cmd_def, all_params)
        return [
            self._read_back_entry(command, entry, given, all_params)
            for entry in _read_back_entries(raw)
        ]

    def _read_back_itself(
        self, command: str, cmd_def: dict[str, Any], all_params: dict[str, Any],
    ) -> list[ReadBack]:
        """``read_back: true``: the command's own address or path, filled as
        the send filled it."""
        if self._is_osc_command(cmd_def):
            address = self._substitute_for_wire(
                f"commands.{command}.address", cmd_def.get("address", ""), all_params,
            )
            return [(f"osc {address}", ("osc", address))]
        if self._is_http_command(cmd_def):
            path = self._substitute_for_wire(
                f"commands.{command}.path", cmd_def.get("path") or "/", all_params,
            )
            return [(f"http {path}", ("http", path))]
        log.debug(
            f"[{self.device_id}] read_back: true on '{command}' names "
            f"no query: only an OSC or HTTP command reads itself back"
        )
        return []

    def _read_back_entry(
        self, command: str, entry: str, given: dict[str, Any], all_params: dict[str, Any],
    ) -> ReadBack:
        """One ``read_back:`` entry: another command by name, given the
        parameters it shares with this one, else a query in the transport's
        own form (an OSC address, an HTTP path, the bytes)."""
        named = (self._definition.get("commands") or {}).get(entry)
        if isinstance(named, dict) and entry != command:
            declared = named.get("params") or {}
            shared = {k: v for k, v in given.items() if k in declared}
            key = f"command {entry} {sorted(shared.items(), key=lambda kv: kv[0])!r}"
            return key, ("command", (entry, shared))
        text = self._substitute_for_wire(f"commands.{command}.read_back", entry, all_params)
        transport_type = self._definition.get("transport")
        if transport_type in ("osc", "http"):
            return f"{transport_type} {text}", (transport_type, text)
        return f"raw {text}", ("raw", self._raw_bytes(transport_type, text))

    async def _send_read_backs(self, command: str, sends: list[ReadBack]) -> None:
        """Send one command's read-back queries the way a poll sends its
        lines (_read_back). A failed query ends the round; the next poll
        reads the value."""
        try:
            await self._read_back_round(command, sends)
        except Exception as exc:
            log.warning(
                f"[{self.device_id}] read_back after '{command}' failed; the "
                f"next poll reads the value: {exc}"
            )
        finally:
            for key, _what in sends:
                self._read_back_waiting.discard(key)

    async def _read_back_round(self, command: str, sends: list[ReadBack]) -> None:
        """The round's queries, in order.

        The command's own write counts as the line before the first query:
        on a link this driver spaces itself, the first one keeps a line's gap
        from it. With an ``inter_command_delay`` the transport holds every
        write that long after the command's, so the device answers the
        command before the query reaches it. Nothing goes out while
        ``polling.when_state`` holds the poll: a command that put the unit
        in standby is not followed by a query that wakes it."""
        polling = self._definition.get("polling") or {}
        gap = self._line_gap()
        if gap > 0:
            await sleep_at_least(gap)
        for key, (kind, what) in sends:
            if not self._link_open():
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
            await self._read_path(what)
        else:
            await self.transport.send(what)

    def _drop_read_backs(self) -> None:
        """Cancel every read-back round under way: the connection it would
        send on is going away."""
        _cancel_all(self._read_back_tasks)
        self._read_back_waiting.clear()
