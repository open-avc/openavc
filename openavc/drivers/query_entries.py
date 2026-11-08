"""What an entry in a YAML driver's ``polling.queries``, ``on_connect`` or a
response rule's ``then_query`` sends, and whether it may go now.

The rules worth knowing before changing anything here:

* **An entry is a string or a dict.** A string goes as written. ``{send}`` is
  a plain line in dict form so it can carry a gate; ``{each_child: <type>,
  send}`` is one line per registered child of that type, ``{child_id}``
  filled with its unpadded local id (``{child_id:02d}`` pads), or one per page
  of children with ``page_size``. Other ``{placeholders}`` are left for the
  sender's config substitution. Anything else is skipped with a warning
  (OSC start-up skips one silently, below).
* **Two gates.** ``when: <config field>`` runs an entry only while that
  field reads as on (an integrator's checkbox for a chatty subscription), and
  is judged when the entry expands. ``when_state: {<var>: <value or
  values>}`` runs it only while the device's own state allows (a unit any
  message wakes from standby is left there), and ``polling.when_state`` holds
  the whole poll the same way. The entry's is judged when it expands, the
  poll's when the poll starts, and both again just before each line goes
  (``_line_gate_shut``), once any write under way has finished, so the reply
  to the line before it is read first.
  An OSC start-up ``each_child`` line is not judged again.
* **OSC start-up reads entries of its own too**
  (``start_up_and_poll._osc_start_up_message``: a string, ``{send}``, and the
  ``{address, args}`` message only start-up takes), so a new entry form is
  added there as well as here.
* **A variable not reported yet holds nothing back**, so a first connect
  still asks for the state a gate reads. Values compare in the variable's
  declared type, and a gate that is not a mapping opens, as a malformed
  ``only_when`` does.
* **The poll's pause is said once.** ``_poll_gate_shut`` logs when the poll
  pauses and when it resumes, and keeps ``_poll_held`` for the poll loop. The
  poll judges its gate through it, and so does OSC start-up's ask of every
  reply address, a poll in all but name. Every other reader (a follow-up, a
  read-back, the liveness probe, the connect check) judges the gate quietly
  and leaves that alone.

``QueryEntries`` is a mixin ``ConfigurableDriver`` inherits, so every name
stays on the driver object. It answers three ``BaseDriver`` hooks:
``_poll_waits_on_device``, ``_connect_check_held`` and ``_liveness_held``.
"""

from __future__ import annotations

from typing import Any, Callable

from openavc.core.condition_eval import reads_as_on
from openavc.drivers import compiled_protocol
from openavc.drivers.child_ids import page_starts
from openavc.utils.logger import get_logger

# These lines have always logged under the YAML driver runtime's name, which
# the IDE's log view shows as their source.
log = get_logger("openavc.drivers.configurable")


def _nonblank(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def _var_def(declared: dict[str, Any], var: str) -> dict[str, Any]:
    var_def = declared.get(var)
    return var_def if isinstance(var_def, dict) else {}


def _gate_values(wanted: Any) -> list[Any]:
    return wanted if isinstance(wanted, list) else [wanted]


def _word(value: Any) -> str:
    """A gate value as the log says it: true / false, not True / False."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


class QueryEntries:
    """Poll and start-up entries and their gates, for ``ConfigurableDriver``."""

    # Supplied by BaseDriver / ConfigurableDriver; declared here for the
    # methods.
    device_id: str
    config: dict[str, Any]
    transport: Any
    _definition: dict[str, Any]
    get_state: Callable[[str], Any]
    list_children: Callable[[str], list[Any]]
    _safe_substitute: Callable[[str, dict[str, Any]], str]

    # Whether the last poll was held back by polling.when_state, so the log
    # says once when the poll pauses and once when it resumes.
    _poll_held: bool = False

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
        """One entry as the lines it sends now (the module docstring has the
        forms): none while its ``when:`` or ``when_state:`` holds it."""
        if isinstance(query, str):
            return [query]
        if isinstance(query, dict):
            if not self._query_enabled(query):
                return []
            if "each_child" in query:
                return self._each_child_lines(query)
            send = query.get("send")
            if isinstance(send, str) and send:
                return [send]
        log.warning(
            f"[{self.device_id}] Unrecognized query entry {query!r}; skipping"
        )
        return []

    def _each_child_lines(self, query: dict[str, Any]) -> list[str]:
        """An ``each_child`` entry: its ``send`` once per registered child of
        the type (or once per page of ``page_size`` children)."""
        ctype = query.get("each_child")
        template = query.get("send")
        if not isinstance(ctype, str) or not _nonblank(template):
            log.warning(
                f"[{self.device_id}] each_child query needs 'each_child' "
                f"+ 'send'; skipping"
            )
            return []
        children = page_starts(self.list_children(ctype), query.get("page_size"))
        return [
            self._safe_substitute(template, {"child_id": local_id})
            for local_id in children
        ]

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
        return all(
            self._state_allows(declared, var, wanted) for var, wanted in gate.items()
        )

    def _state_allows(self, declared: dict[str, Any], var: str, wanted: Any) -> bool:
        """One variable of a gate: not reported yet, or holding one of the
        wanted values, compared in its declared type."""
        actual = self.get_state(var)
        if actual is None:
            return True
        var_type = _var_def(declared, var).get("type", "string")
        allowed = [
            compiled_protocol.coerce_json_value(v, var_type) for v in _gate_values(wanted)
        ]
        return compiled_protocol.coerce_json_value(actual, var_type) in allowed

    def _describe_state_gate(self, gate: dict[str, Any]) -> tuple[str, str]:
        """A shut ``when_state`` gate in words, as the Driver Builder shows
        it: what it waits for ("Standby is false"), and what the device
        reports now for each variable that holds it shut ("Standby true")."""
        declared = self._definition.get("state_variables") or {}
        wanted: list[str] = []
        reported: list[str] = []
        for var, values in gate.items():
            values = _gate_values(values)
            label = _var_def(declared, var).get("label") or var
            wanted.append(f"{label} is {' or '.join(_word(v) for v in values)}")
            if not self._state_gate_open({var: values}):
                reported.append(f"{label} {_word(self.get_state(var))}")
        return " and ".join(wanted), ", ".join(reported)

    def _poll_gate_shut(self, polling: dict[str, Any]) -> bool:
        """Whether ``polling.when_state`` holds the poll back now. Says so in
        the log once when the poll pauses and once when it resumes."""
        held = not self._state_gate_open(polling.get("when_state"))
        if held != self._poll_held:
            self._poll_held = held
            self._say_poll_held(held, polling.get("when_state"))
        return held

    def _say_poll_held(self, held: bool, gate: Any) -> None:
        if not held:
            log.info(f"[{self.device_id}] Polling resumed")
            return
        wanted, reported = self._describe_state_gate(gate)
        log.info(
            f"[{self.device_id}] Polling paused: it polls only while "
            f"{wanted}, and the device reports {reported}"
        )

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
        await self._wait_for_write()
        if gates is not None and self._poll_gate_judged_shut(gates, quiet):
            return "poll"
        if not self._state_gate_open(entry_gate):
            return "entry"
        return None

    async def _wait_for_write(self) -> None:
        """Wait out the transport's write under way or waiting, if it can
        say (``wait_idle``)."""
        wait_idle = getattr(self.transport, "wait_idle", None)
        if wait_idle is not None:
            await wait_idle()

    def _poll_gate_judged_shut(self, gates: dict[str, Any], quiet: bool) -> bool:
        if quiet:
            return not self._state_gate_open(gates.get("when_state"))
        return self._poll_gate_shut(gates)

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
