"""Commands: what each of the driver's commands does to the device.

The person sends the driver's commands one at a time, each through the
sandbox's ``DeviceManager.send_command``: the door a panel press, a macro step
and the REST API all go through, so every gate they meet applies here too.
The command must be one the driver declares, child ids are coerced, the
parameters are checked against the driver's own schema, a device that is not
connected is refused (a command that declares ``available_offline`` passes),
and so is one inside a commanded restart. Nothing reaches the device that the
person did not press Send for, except the status queries they ask to run
together.

**The command list** is the live driver's own (``DRIVER_INFO["commands"]`` on
the instance, so commands a Python driver builds once connected are there
too), in the order the driver declares them, with what the driver says about
each: its label, help and parameters; whether it is a **status query**
(``query_for``, the variable its reply reports, or a command the driver's
polling runs by name); what it says it changes (``sets``); whether it works
with the device offline (``available_offline``); how long it takes the
device away (``restarts_device_for``).

**One command at a time.** A second Send while a command is still being
watched is refused in words, so every reply and every status change in a
command's window belongs to that command and to nothing the audit sent after
it.

**Status queries run as one batch**: each in turn, then a short wait for its
reply before the next. A query that needs a value nobody has given (a
required parameter) is left for its own Send.

Sending a command ends the connect-and-listen window first if it is still
open, because that window promises to send none of the driver's commands.

**What a command did** is read from its **window**: from the moment it is
sent until ``WINDOW_SECONDS`` after it returns ("Wait longer" adds more, up to
``MAX_WINDOW_SECONDS``; "Stop watching" ends it). In the window the audit keeps
every byte both ways, every status value that changed, and every sign that
the device refused it: an error the driver raised or published
(``device.error``), a ``last_error`` it wrote, a reply no response rule
matched. Then, per ``driver-roadmap`` "An acknowledgement is not an
application", it checks the command's declared effect against what the
device reports once the window closes: each ``sets`` entry is confirmed, still
different, unchanged, or never reported (and "it already had that value"
says the audit cannot tell). A status query's ``query_for`` value is read the
same way. A command the driver said succeeded while no byte left for the
device is flagged (``command_sent_nothing``): the gate nothing else catches.

**A command that restarts the device** (``restarts_device_for``) is watched
for its declared window and ``RESTART_MARGIN_SECONDS`` more, ending
``AFTER_RECONNECT_SECONDS`` after the driver is back. The record says when
OpenAVC noticed the device go, when the driver reconnected, and whether that
was inside the declared window: the measurement a driver's figure is set from.

**Try again** sends the same command with the same values (held on the
server, so a secret one never goes back to the browser). Every command
records how long after the previous one it was sent, which is how a device
that refuses a command for a few seconds after a related one shows up.

The driver's own confirmation text for a command (an ``actions`` entry's
``confirm``) is part of the command list, for the wizard to show first.

**"Did the device do it?"** Then the person says what they saw on the device
itself (``yes``, ``no``, ``partly``, ``cant_tell``) with an optional note. It
is their answer, kept beside what the device reported, never merged into it:
a display that said "input hdmi2" while the screen stayed black is exactly
the disagreement a report exists to show.

Every command sent is kept for the report (:meth:`CommandPass.report_record`):
what was asked, what the driver's ``send_command`` returned or raised, the
traffic from the moment it was sent to the end of its window, and what the
window showed.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from openavc.audit.observe import REPLY_WINDOW_SECONDS, command_sent_nothing
from openavc.audit.session import AuditError
from openavc.core.device_traffic import RX, TX, serialize_entry
from openavc.core.state_store import is_flat_primitive
from openavc.drivers.base import missing_required_params
from openavc.drivers.compiled_protocol import coerce_bool_token, is_bool_token
from openavc.utils.logger import get_logger

if TYPE_CHECKING:
    from openavc.audit.passes import DriverRun
    from openavc.audit.session import AuditSession

log = get_logger(__name__)

# How long a command is watched after it returns.
WINDOW_SECONDS = 10.0
# A status query sent with the others waits only for its reply.
QUERY_WINDOW_SECONDS = REPLY_WINDOW_SECONDS
# What "Wait longer" adds, and the most a window can be held, from the return.
EXTEND_SECONDS = 10.0
MAX_WINDOW_SECONDS = 120.0
# A command that restarts the device: watched for its declared window and
# this much more, and ended this long after the driver is back.
RESTART_MARGIN_SECONDS = 60.0
AFTER_RECONNECT_SECONDS = 10.0
# State changes a window keeps.
CHANGES_KEPT = 500
# How often the wizard hears about a command being watched.
FLUSH_SECONDS = 0.5
# Traffic entries and state changes each command shows live (the report
# keeps them all).
LIVE_ENTRIES = 40
LIVE_CHANGES = 20

SENDING = "sending"
WATCHING = "watching"
DONE = "done"

NOT_CONNECTED_YET = "Connect the driver first, on the Connect and listen step."
BUSY = "Wait for {label} to finish before sending another command."
BATCH_BUSY = "Wait for the status queries to finish before sending another command."
NO_SUCH_COMMAND = "{driver} has no command named {name}."
NO_QUERIES = "{driver} declares no status queries that can run without a value."
NOT_WATCHING = "No command is being watched."
WATCH_CAP = "A command is watched for {span} at most."
NO_SUCH_TRIAL = "There is no command number {number} to send again."
NO_TRIAL_TO_ANSWER = "There is no command number {number} to answer for."
NOT_SENT_YET = "Wait for {label} to be sent before saying what the device did."

# The person's answers to "Did the device do it?", in words for the timeline.
ANSWERS = {
    "yes": "The person said the device did it.",
    "no": "The person said the device did not do it.",
    "partly": "The person said the device partly did it.",
    "cant_tell": "The person could not tell from where they were.",
}

# The keys the platform writes for every device: not what a command changed.
_PLATFORM_KEYS = frozenset({
    "connected", "name", "enabled", "offline_reason", "offline_detail", "paused",
    "restarting", "web_ui_url", "orphaned", "orphan_reason",
})
_PARAM_REF = re.compile(r"\{([^{}]+)\}")


def _shown(value: Any) -> Any:
    """A value as a record keeps it: as is when it is a flat primitive, else
    its repr (a driver's ``send_command`` may return anything)."""
    return value if is_flat_primitive(value) else repr(value)


def error_sentence(exc: BaseException) -> str:
    """What the person reads when a Send raised.

    The platform's own refusals (a parameter out of range, a command the
    driver does not have) are already sentences. A connection failure is put
    in the words a panel uses. Anything else is the driver's own message.
    """
    from openavc.api.error_messages import friendly_error

    if isinstance(exc, (ConnectionError, TimeoutError)):
        return friendly_error(exc, device="The device")
    return str(exc) or type(exc).__name__


def _polled_command_names(driver: Any) -> set[str]:
    """The commands a YAML driver's polling runs by name (its status queries)."""
    definition = getattr(driver, "_definition", None) or {}
    queries = (definition.get("polling") or {}).get("queries") or []
    commands = (getattr(driver, "DRIVER_INFO", {}) or {}).get("commands") or {}
    names = set()
    for query in queries:
        text = query.get("send") if isinstance(query, dict) else query
        if isinstance(text, str) and text in commands:
            names.add(text)
    return names


def _confirm_texts(driver: Any) -> dict[str, str]:
    """The confirmation each command's action asks for before it runs: the
    driver's own words, or a plain one for ``confirm: true``."""
    from openavc.drivers.actions import resolve_device_actions

    info = getattr(driver, "DRIVER_INFO", {}) or {}
    config = getattr(driver, "config", None)
    out: dict[str, str] = {}
    for action in resolve_device_actions(info, config if isinstance(config, dict) else None):
        confirm = action.get("confirm")
        name = action.get("command")
        if action.get("kind") != "command" or not confirm or not isinstance(name, str):
            continue
        out.setdefault(
            name,
            confirm if isinstance(confirm, str)
            else f"The driver asks for a confirmation before {action.get('label') or name} runs.",
        )
    return out


def command_catalog(driver: Any) -> list[dict[str, Any]]:
    """The driver's commands, in its own order, with what it says about each."""
    info = getattr(driver, "DRIVER_INFO", {}) or {}
    commands = info.get("commands") or {}
    if not isinstance(commands, dict):
        return []
    polled = _polled_command_names(driver)
    confirms = _confirm_texts(driver)
    out = []
    for name, cdef in commands.items():
        cdef = cdef if isinstance(cdef, dict) else {}
        params = cdef.get("params") if isinstance(cdef.get("params"), dict) else {}
        query_for = cdef.get("query_for") if isinstance(cdef.get("query_for"), str) else ""
        try:
            restarts = int(cdef.get("restarts_device_for") or 0)
        except (TypeError, ValueError):
            restarts = 0
        out.append({
            "name": name,
            "label": str(cdef.get("label") or name),
            "help": str(cdef.get("help") or ""),
            "params": params,
            "query": bool(query_for) or name in polled,
            "query_for": query_for,
            "polled": name in polled,
            "sets": dict(cdef["sets"]) if isinstance(cdef.get("sets"), dict) else {},
            "available_offline": bool(cdef.get("available_offline")),
            "restarts_device_for": restarts,
            "needs_input": bool(missing_required_params(params, {})),
            "confirm": confirms.get(name, ""),
        })
    return out


def same_value(expected: Any, actual: Any) -> bool:
    """Does a reported value say what a command's declared effect says?

    The device may say it its own way (``"on"`` or ``1`` for ``true``, ``"40"``
    for ``40``, another case), so a flag is read as a flag, a number as a
    number, and anything else as text.
    """
    if actual is None:
        return False
    if actual == expected:
        return True
    if isinstance(expected, bool) or isinstance(actual, bool):
        if is_bool_token(expected) and is_bool_token(actual):
            return coerce_bool_token(expected) == coerce_bool_token(actual)
        return False
    try:
        return float(expected) == float(actual)
    except (TypeError, ValueError):
        pass
    return str(expected).strip().lower() == str(actual).strip().lower()


def declared_effects(
    entry: dict[str, Any], params: dict[str, Any], driver: Any,
) -> list[dict[str, Any]]:
    """The state a command says it sets (``sets``), as keys of the device's
    state, each with the value it should now read (``has_value`` false when
    it names a parameter nobody gave).

    On a command with exactly one ``child_id`` parameter, a key the child
    type declares means that child's variable (the child's own name wins, as
    in the contract); anything else is the device's own.
    """
    sets = entry.get("sets") or {}
    if not isinstance(sets, dict) or not sets:
        return []
    child = _addressed_child(entry, params, driver)
    out = []
    for key, value in sets.items():
        state_key = f"{child[0]}.{child[1]}.{key}" if child and key in child[2] else key
        ref = _PARAM_REF.fullmatch(value) if isinstance(value, str) else None
        if ref:
            given = params.get(ref.group(1))
            has_value = given not in (None, "")
            expected = given if has_value else None
        else:
            has_value, expected = True, value
        out.append({
            "state": key, "state_key": state_key, "expected": _shown(expected),
            "has_value": has_value,
        })
    return out


def _addressed_child(
    entry: dict[str, Any], params: dict[str, Any], driver: Any,
) -> tuple[str, str, set[str]] | None:
    """(child type, its id as state keys spell it, its variables) for a
    command with exactly one ``child_id`` parameter that was given a value."""
    pdefs = entry.get("params") or {}
    child_params = [
        n for n, p in pdefs.items() if isinstance(p, dict) and p.get("type") == "child_id"
    ]
    if len(child_params) != 1:
        return None
    name = child_params[0]
    ctype = pdefs[name].get("child_type")
    types = (getattr(driver, "DRIVER_INFO", {}) or {}).get("child_entity_types") or {}
    local = params.get(name)
    if not isinstance(ctype, str) or ctype not in types or local in (None, ""):
        return None
    try:
        padded = str(driver.format_child_id(ctype, local))
    except Exception:
        padded = str(local)
    variables = set((types[ctype] or {}).get("state_variables") or {})
    return ctype, padded, variables


@dataclass
class CommandTrial:
    """One command sent, and what came of it."""

    number: int
    command: str
    label: str
    params: dict[str, Any]
    # The how-manyth time this command was sent in this run (1 = first).
    attempt: int
    # Sent by "Run all status queries" rather than its own Send.
    batch: bool
    # Which connect-and-listen attempt's connection carried it.
    connect_attempt: int
    sent_at: float
    returned_at: float | None = None
    ends_at: float | None = None
    finished_at: float | None = None
    status: str = SENDING
    result: Any = None
    error: str = ""
    error_type: str = ""
    # Parameters the driver marks secret: shown as *** wherever the trial is.
    secret_params: set[str] = field(default_factory=set)
    # What the driver declares about the command, as the catalog said.
    declared: dict[str, Any] = field(default_factory=dict)
    # The previous command sent in this run: {"number", "command", "label", "seconds"}.
    since_previous: dict[str, Any] | None = None
    # The device's state when it was sent, and what changed in its window.
    before: dict[str, Any] = field(default_factory=dict)
    changes: list[dict[str, Any]] = field(default_factory=list)
    # Errors the driver published for the device while it was watched.
    device_errors: list[dict[str, Any]] = field(default_factory=list)
    # When OpenAVC saw the device go and the driver come back (a restart).
    went_away_at: float | None = None
    back_at: float | None = None
    # "Wait longer" seconds added; "Stop watching" pressed.
    extended: float = 0.0
    stopped_early: bool = False
    # Filled in when the window closes (see ``CommandPass._conclude``).
    effects: list[dict[str, Any]] = field(default_factory=list)
    query: dict[str, Any] | None = None
    refusals: dict[str, Any] = field(default_factory=dict)
    sent_nothing: bool | None = None
    restart: dict[str, Any] | None = None
    # The person's answer to "Did the device do it?": {"answer", "note", "at"}.
    answer: dict[str, Any] | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def window_end(self) -> float:
        return self.finished_at or time.time()

    def in_window(self, t: float) -> bool:
        return self.sent_at <= t <= self.window_end()

    def to_dict(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "command": self.command,
            "label": self.label,
            "params": {
                k: "***" if k in self.secret_params else _shown(v)
                for k, v in self.params.items()
            },
            "attempt": self.attempt,
            "batch": self.batch,
            "connect_attempt": self.connect_attempt,
            "sent_at": self.sent_at,
            "returned_at": self.returned_at,
            "ends_at": self.ends_at,
            "finished_at": self.finished_at,
            "status": self.status,
            "result": self.result,
            "error": self.error,
            "error_type": self.error_type,
            "since_previous": self.since_previous,
            "extended": self.extended,
            "stopped_early": self.stopped_early,
            "changes": list(self.changes),
            "device_errors": list(self.device_errors),
            "effects": list(self.effects),
            "query": self.query,
            "refusals": dict(self.refusals),
            "sent_nothing": self.sent_nothing,
            "restart": self.restart,
            "answer": dict(self.answer) if self.answer else None,
            **self.extra,
        }


class CommandPass:
    """The commands step for one driver run."""

    def __init__(
        self,
        session: "AuditSession",
        run: "DriverRun",
        *,
        window_seconds: float = WINDOW_SECONDS,
        query_window_seconds: float = QUERY_WINDOW_SECONDS,
        max_window_seconds: float = MAX_WINDOW_SECONDS,
        restart_margin_seconds: float = RESTART_MARGIN_SECONDS,
        after_reconnect_seconds: float = AFTER_RECONNECT_SECONDS,
        flush_seconds: float = FLUSH_SECONDS,
    ) -> None:
        self.session = session
        self.run = run
        self.window_seconds = window_seconds
        self.query_window_seconds = query_window_seconds
        self.max_window_seconds = max_window_seconds
        self.restart_margin_seconds = restart_margin_seconds
        self.after_reconnect_seconds = after_reconnect_seconds
        self.flush_seconds = flush_seconds
        self.trials: list[CommandTrial] = []
        # "Run all status queries": {"status", "total", "sent", "skipped"}.
        self.batch: dict[str, Any] | None = None
        self._task: asyncio.Task | None = None
        # The command list as it stood when the driver stopped, for the report.
        self._final_catalog: list[dict[str, Any]] | None = None
        # The command list, and the picker values, as the wizard last heard them.
        self._catalog_sent: list[dict[str, Any]] | None = None
        self._pickers_sent: dict[str, Any] | None = None

    # -- where it runs --------------------------------------------------------

    def _live(self):
        """The current attempt, while its driver is connected to the device."""
        listen = self.run.listen
        if listen is None or not listen.sandbox.started:
            raise AuditError(NOT_CONNECTED_YET)
        return listen

    def _driver(self) -> Any:
        listen = self.run.listen
        driver = listen.sandbox.driver if listen is not None else None
        if driver is None:
            from openavc.drivers.registry import get_driver_class

            driver = get_driver_class(self.run.choice.driver_id)
        return driver

    def catalog(self) -> list[dict[str, Any]]:
        listen = self.run.listen
        if self._final_catalog is not None and (listen is None or not listen.sandbox.started):
            return self._final_catalog
        return command_catalog(self._driver())

    def picker_state(self) -> dict[str, Any]:
        """The status values the command list's parameter pickers read
        (``options_state``), from the device as it reports them now."""
        keys = {
            pdef["options_state"]
            for entry in self.catalog()
            for pdef in (entry.get("params") or {}).values()
            if isinstance(pdef, dict) and isinstance(pdef.get("options_state"), str)
        }
        listen = self.run.listen
        state = listen.sandbox.device_state() if listen is not None and listen.sandbox.started else {}
        return {key: _shown(state.get(key)) for key in sorted(keys)}

    def _driver_name(self) -> str:
        return self.run.choice.identity.get("name") or self.run.choice.driver_id

    def current(self) -> CommandTrial | None:
        """The command being sent or watched, if any."""
        if self.trials and self.trials[-1].status != DONE:
            return self.trials[-1]
        return None

    def _refuse_if_busy(self) -> None:
        if self.batch is not None and self.batch.get("status") == "running":
            raise AuditError(BATCH_BUSY)
        busy = self.current()
        if busy is None and self.run.settings is not None:
            busy = self.run.settings.current()
        if busy is not None:
            raise AuditError(BUSY.format(label=busy.label))

    # -- sending ----------------------------------------------------------------

    async def send(self, name: str, params: dict[str, Any] | None = None) -> CommandTrial:
        """Send one command and watch what it does (in the background)."""
        self._refuse_if_busy()
        listen = self._live()
        entry = next((c for c in self.catalog() if c["name"] == name), None)
        if entry is None:
            # Still sent: the manager's own refusal is part of what the
            # audit records about how the driver treats an unknown name.
            entry = {"name": name, "label": name}
        listen.end_window_now("A command was sent")
        self.session.enter_step("commands")
        trial = self._open_trial(entry, dict(params or {}), batch=False)
        self._task = self.session.track_task(asyncio.create_task(self._send_and_watch(trial)))
        return trial

    async def send_again(self, number: int, name: str | None = None) -> CommandTrial:
        """"Try again": the same command with the same values as command
        ``number`` (secret ones included, which never left the server).
        ``name``, when given, must be that command's."""
        earlier = next((t for t in self.trials if t.number == number), None)
        if earlier is None or (name is not None and earlier.command != name):
            raise AuditError(NO_SUCH_TRIAL.format(number=number))
        return await self.send(earlier.command, dict(earlier.params))

    def answer(self, number: int, answer: str, note: str = "") -> CommandTrial:
        """Record what the person saw the device do for command ``number``.
        Answering again replaces the answer (the timeline keeps both)."""
        trial = next((t for t in self.trials if t.number == number), None)
        if trial is None:
            raise AuditError(NO_TRIAL_TO_ANSWER.format(number=number))
        if trial.status == SENDING:
            raise AuditError(NOT_SENT_YET.format(label=trial.label))
        trial.answer = {"answer": answer, "note": note, "at": time.time()}
        self.session.add_timeline(
            "command.answer",
            f"{trial.label}: {ANSWERS[answer]}{' Note: ' + note if note else ''}",
            run=self.run.index, trial=trial.number, command=trial.command, answer=answer,
        )
        self._publish(trial)
        return trial

    def extend(self, seconds: float = EXTEND_SECONDS) -> None:
        """"Wait longer": keep watching the current command."""
        trial = self.current()
        if trial is None or trial.status != WATCHING or trial.ends_at is None:
            raise AuditError(NOT_WATCHING)
        cap = (trial.returned_at or trial.sent_at) + max(
            self.max_window_seconds, self._restart_span(trial),
        )
        if trial.ends_at >= cap - 0.001:
            raise AuditError(WATCH_CAP.format(span=_duration(cap - (trial.returned_at or trial.sent_at))))
        new_end = min(trial.ends_at + seconds, cap)
        trial.extended += new_end - trial.ends_at
        trial.ends_at = new_end
        self._publish(trial)

    def end_now(self) -> None:
        """"Stop watching": close the current command's window now."""
        trial = self.current()
        if trial is None or trial.status != WATCHING:
            raise AuditError(NOT_WATCHING)
        trial.stopped_early = True
        trial.ends_at = time.time()
        self._publish(trial)

    async def run_queries(self) -> None:
        """Send every status query that needs no value, one after another."""
        self._refuse_if_busy()
        listen = self._live()
        catalog = self.catalog()
        queries = [c for c in catalog if c["query"] and not c["needs_input"]]
        skipped = [c["name"] for c in catalog if c["query"] and c["needs_input"]]
        if not queries:
            raise AuditError(NO_QUERIES.format(driver=self._driver_name()))
        listen.end_window_now("The status queries were sent")
        self.session.enter_step("commands")
        self.batch = {"status": "running", "total": len(queries), "sent": 0, "skipped": skipped}
        self.session.add_timeline(
            "commands.queries",
            f"Running {len(queries)} status "
            f"{'query' if len(queries) == 1 else 'queries'} one after another.",
            run=self.run.index,
        )
        self._task = self.session.track_task(asyncio.create_task(self._run_batch(queries)))

    async def _run_batch(self, queries: list[dict[str, Any]]) -> None:
        try:
            for entry in queries:
                if self.run.listen is None or not self.run.listen.sandbox.started:
                    break
                trial = self._open_trial(entry, {}, batch=True)
                await self._send_and_watch(trial, window=self.query_window_seconds)
                self.batch["sent"] += 1
        finally:
            if self.batch is not None:
                self.batch["status"] = "done"
            self._publish()

    def _open_trial(self, entry: dict[str, Any], params: dict[str, Any], *, batch: bool) -> CommandTrial:
        name = entry["name"]
        # A value typed into a secret parameter (a password a command sets)
        # is masked in every record, the traffic included.
        secret_names = {
            k for k, pdef in (entry.get("params") or {}).items()
            if isinstance(pdef, dict) and (pdef.get("secret") or pdef.get("type") == "password")
        }
        secret = {str(params[k]) for k in secret_names if params.get(k) not in (None, "")}
        if secret:
            self.run.secrets |= secret
            self.run.listen.sandbox.observer.add_secrets(secret)
        previous = self.trials[-1] if self.trials else None
        now = time.time()
        trial = CommandTrial(
            number=len(self.trials) + 1,
            command=name,
            label=str(entry.get("label") or name),
            params=params,
            attempt=1 + sum(1 for t in self.trials if t.command == name),
            batch=batch,
            connect_attempt=max(0, len(self.run.listens) - 1),
            sent_at=now,
            secret_params=secret_names,
            declared={
                k: entry.get(k) for k in (
                    "sets", "query_for", "restarts_device_for", "available_offline", "confirm",
                ) if entry.get(k)
            },
            since_previous={
                "number": previous.number, "command": previous.command,
                "label": previous.label, "seconds": round(now - previous.sent_at, 3),
            } if previous is not None else None,
            before=dict(self.run.listen.sandbox.device_state()),
        )
        self.trials.append(trial)
        self.session.add_timeline(
            "command.sent",
            f"Sent {trial.label}{_params_text(trial.to_dict()['params'])}.",
            run=self.run.index, trial=trial.number, command=name,
        )
        self._publish(trial)
        return trial

    def _restart_span(self, trial: CommandTrial) -> float:
        declared = trial.declared.get("restarts_device_for") or 0
        return float(declared) + self.restart_margin_seconds if declared else 0.0

    def _watch(self, trial: CommandTrial, sandbox: Any) -> tuple[str, list[str]]:
        """Start hearing what the device does while ``trial`` is open."""
        prefix = f"device.{sandbox.device_id}."

        def on_state(key: str, old: Any, new: Any, _source: str = "") -> None:
            if trial.status == DONE or len(trial.changes) >= CHANGES_KEPT:
                return
            trial.changes.append({
                "t": time.time(), "key": key[len(prefix):] if key.startswith(prefix) else key,
                "old": _shown(old), "new": _shown(new),
            })

        def on_error(_event: str, payload: Any = None) -> None:
            if trial.status != DONE:
                error = (payload or {}).get("error") if isinstance(payload, dict) else payload
                trial.device_errors.append({"t": time.time(), "error": str(error or "")})

        def on_gone(_event: str, _payload: Any = None) -> None:
            if trial.status != DONE and trial.went_away_at is None:
                trial.went_away_at = time.time()

        def on_back(_event: str, _payload: Any = None) -> None:
            if trial.status == DONE or trial.went_away_at is None or trial.back_at is not None:
                return
            trial.back_at = time.time()
            if trial.declared.get("restarts_device_for") and trial.ends_at is not None:
                trial.ends_at = min(trial.ends_at, trial.back_at + self.after_reconnect_seconds)

        device_id = sandbox.device_id
        sub = sandbox.state.subscribe(f"{prefix}*", on_state)
        handlers = [
            sandbox.events.on(f"device.error.{device_id}", on_error),
            sandbox.events.on(f"device.disconnected.{device_id}", on_gone),
            sandbox.events.on(f"device.connected.{device_id}", on_back),
        ]
        return sub, handlers

    @staticmethod
    def _unwatch(sandbox: Any, handles: tuple[str, list[str]]) -> None:
        sub, handlers = handles
        sandbox.state.unsubscribe(sub)
        for handler in handlers:
            sandbox.events.off(handler)

    async def _send_and_watch(self, trial: CommandTrial, *, window: float | None = None) -> None:
        sandbox = self.run.listen.sandbox
        handles = self._watch(trial, sandbox)
        driver = sandbox.driver
        errors_before = getattr(driver, "_last_error_writes", 0)
        try:
            try:
                result = await sandbox.manager.send_command(
                    sandbox.device_id, trial.command, trial.params or None,
                )
                trial.result = _shown(result)
            except asyncio.CancelledError:
                trial.error = "The audit stopped before the command finished."
                raise
            except Exception as exc:
                trial.error = error_sentence(exc)
                trial.error_type = type(exc).__name__
            finally:
                trial.returned_at = time.time()
                span = self.window_seconds if window is None else window
                if not trial.error:
                    span = max(span, self._restart_span(trial))
                trial.ends_at = trial.returned_at + span
                trial.status = WATCHING
                self._publish(trial)
            while time.time() < (trial.ends_at or 0) and sandbox.started:
                await asyncio.sleep(
                    min(self.flush_seconds, max(0.0, (trial.ends_at or 0) - time.time()))
                )
                self._publish(trial)
        finally:
            trial.refusals["last_error_writes"] = max(
                0, getattr(driver, "_last_error_writes", 0) - errors_before,
            )
            self._unwatch(sandbox, handles)
            self._finish(trial)

    def _finish(self, trial: CommandTrial) -> None:
        if trial.status == DONE:
            return
        trial.finished_at = time.time()
        trial.status = DONE
        self._conclude(trial)
        self.session.add_timeline(
            "command.done",
            f"{trial.label}: {trial_sentence(self._trial_view(trial, every_entry=False))}",
            run=self.run.index, trial=trial.number, command=trial.command,
        )
        if trial.sent_nothing:
            self.session.add_timeline(
                "contract.command_sent_nothing",
                f"{trial.label} returned success, but nothing was sent to the device.",
                run=self.run.index, trial=trial.number, command=trial.command,
            )
        self._publish(trial)

    def _conclude(self, trial: CommandTrial) -> None:
        """What the window showed: the declared effect against what the
        device reports now, a status query's answer, the signs of a refusal,
        whether anything was sent, and how a restart went."""
        attempt = self.run.listens[trial.connect_attempt] if self.run.listens else None
        if attempt is None:
            state: dict[str, Any] = {}
        elif attempt.sandbox.started:
            state = attempt.sandbox.device_state()
        else:
            state = _final_values(attempt.status_table())
        traffic = self._window_traffic(trial)
        changed = {c["key"] for c in trial.changes}
        driver = self._driver()
        entry = {**trial.declared, "params": self._params_of(trial.command)}

        if not trial.error:
            for effect in declared_effects(entry, trial.params, driver):
                key = effect["state_key"]
                now, before = state.get(key), trial.before.get(key)
                if not effect["has_value"]:
                    outcome = "no_value"
                elif same_value(effect["expected"], now):
                    outcome = "already" if (
                        key not in changed and same_value(effect["expected"], before)
                    ) else "confirmed"
                elif now is None:
                    outcome = "not_reported"
                elif key in changed:
                    outcome = "different"
                else:
                    outcome = "unchanged"
                trial.effects.append({**effect, "value": _shown(now), "outcome": outcome})

        query_for = trial.declared.get("query_for")
        if query_for and not trial.error:
            child = _addressed_child(entry, trial.params, driver)
            key = (
                f"{child[0]}.{child[1]}.{query_for}"
                if child and query_for in child[2] else query_for
            )
            answered = any(e.direction == RX for e in traffic)
            value = state.get(key)
            trial.query = {
                "state": query_for, "state_key": key, "value": _shown(value),
                "changed": key in changed,
                "outcome": "no_reply" if not answered
                else ("reported" if value is not None else "not_reported"),
            }

        events = []
        if attempt is not None:
            events = [
                e for e in attempt.sandbox.observer.events
                if e.kind == "unmatched_response" and trial.in_window(e.t)
            ]
        last_error_changes = [c for c in trial.changes if c["key"] == "last_error" and c["new"]]
        trial.refusals.update({
            "device_errors": len(trial.device_errors),
            "last_error": last_error_changes[-1]["new"] if last_error_changes else (
                state.get("last_error") if trial.refusals.get("last_error_writes") else None
            ),
            "unmatched": len(events),
            "unmatched_examples": [str(e.detail.get("text", ""))[:200] for e in events[:5]],
        })

        if not trial.error and attempt is not None:
            if not attempt.sandbox.observer.frames():
                trial.sent_nothing = None  # its traffic is not captured at all
            else:
                trial.sent_nothing = command_sent_nothing(
                    attempt.sandbox.observer.traffic, trial.sent_at,
                    trial.returned_at or trial.sent_at,
                )

        declared = trial.declared.get("restarts_device_for")
        if declared and not trial.error:
            went = trial.went_away_at
            back = trial.back_at
            trial.restart = {
                "declared_seconds": declared,
                "went_away_after": round(went - trial.sent_at, 1) if went else None,
                "back_after": round(back - trial.sent_at, 1) if back else None,
                "away_for": round(back - went, 1) if went and back else None,
                "within_declared": (back - trial.sent_at) <= declared if back else None,
            }

    def _params_of(self, command: str) -> dict[str, Any]:
        entry = next((c for c in self.catalog() if c["name"] == command), None)
        return dict((entry or {}).get("params") or {})

    async def stop(self) -> None:
        """End what is being sent or watched; keep every record."""
        task = self._task
        if task is not None and not task.done() and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        busy = self.current()
        if busy is not None:
            if busy.returned_at is None:
                busy.returned_at = time.time()
            self._finish(busy)
        if self.batch is not None and self.batch.get("status") == "running":
            self.batch["status"] = "done"
            self._publish()
        listen = self.run.listen
        if listen is not None and listen.sandbox.started:
            self._final_catalog = command_catalog(self._driver())

    # -- reading -----------------------------------------------------------------

    def _window_traffic(self, trial: CommandTrial) -> list[Any]:
        attempt = self.run.listens[trial.connect_attempt] if self.run.listens else None
        if attempt is None:
            return []
        return [e for e in attempt.sandbox.observer.frames() if trial.in_window(e.t)]

    def _redactor(self, trial: CommandTrial):
        return self.run.listens[trial.connect_attempt].sandbox.observer.redactor()

    def _trial_view(self, trial: CommandTrial, *, every_entry: bool) -> dict[str, Any]:
        traffic = self._window_traffic(trial) if self.run.listens else []
        redactor = self._redactor(trial) if self.run.listens else None
        shown = traffic if every_entry else traffic[-LIVE_ENTRIES:]
        out = trial.to_dict()
        out["traffic"] = {
            "sent": sum(1 for e in traffic if e.direction == TX),
            "received": sum(1 for e in traffic if e.direction == RX),
            "entries": [serialize_entry(e, redactor) for e in shown] if redactor else [],
        }
        if redactor is not None:
            out["params"] = redactor.value(dict(out["params"]))
            out["changes"] = redactor.value(out["changes"])
            out["device_errors"] = redactor.value(out["device_errors"])
            out["refusals"] = redactor.value(out["refusals"])
        # What it did, in words, once its window has closed.
        out["summary"] = trial_sentence(out) if trial.status == DONE else ""
        if not every_entry:
            out["changes"] = out["changes"][-LIVE_CHANGES:]
            out["device_errors"] = out["device_errors"][-LIVE_CHANGES:]
        return out

    def to_dict(self) -> dict[str, Any]:
        current = self.current()
        return {
            "catalog": self.catalog(),
            "picker_state": self.picker_state(),
            "batch": dict(self.batch) if self.batch else None,
            "current": current.number if current is not None else None,
            "trials": [self._trial_view(t, every_entry=False) for t in self.trials],
        }

    def report_record(self) -> dict[str, Any]:
        """The commands as the report keeps them: the list the driver
        declared, and every command sent with all of its window's traffic."""
        return {
            "catalog": self.catalog(),
            "batch": dict(self.batch) if self.batch else None,
            "trials": [self._trial_view(t, every_entry=True) for t in self.trials],
        }

    def _publish(self, trial: CommandTrial | None = None) -> None:
        """Tell the wizard what moved: the trial that changed (merged by its
        number), the batch, and the command list and picker values only when
        they changed."""
        current = self.current()
        update: dict[str, Any] = {
            "batch": dict(self.batch) if self.batch else None,
            "current": current.number if current is not None else None,
            "trials": [self._trial_view(trial, every_entry=False)] if trial is not None else [],
        }
        catalog = self.catalog()
        if catalog != self._catalog_sent:
            update["catalog"] = catalog
            self._catalog_sent = catalog
        pickers = self.picker_state()
        if pickers != self._pickers_sent:
            update["picker_state"] = pickers
            self._pickers_sent = pickers
        self.session.publish({"type": "audit.commands", "run": self.run.index, "commands": update})


def _final_values(table: dict[str, Any]) -> dict[str, Any]:
    """A kept status table's values, as device state keys."""
    out = {v["name"]: v["value"] for v in table.get("variables", []) if v.get("reported")}
    for ctype, by_id in (table.get("children") or {}).items():
        for local_id, props in by_id.items():
            for prop, value in props.items():
                out[f"{ctype}.{local_id}.{prop}"] = value
    return out


def _duration(seconds: float) -> str:
    """"2 minutes", "40 seconds", "1 minute"."""
    if seconds >= 60 and seconds % 60 == 0:
        minutes = int(seconds // 60)
        return f"{minutes} minute{'' if minutes == 1 else 's'}"
    whole = round(seconds)
    return f"{whole} second{'' if whole == 1 else 's'}"


def _value_text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


EFFECT_TEXT = {
    "confirmed": "{state} is now {expected}, as the driver says it should be",
    "already": "{state} was already {expected}, so the audit cannot tell whether this command set it",
    "different": "{state} changed to {value}, not {expected}",
    "unchanged": "{state} is still {value}, not {expected}",
    "not_reported": "the device has not reported {state}",
    "no_value": "{state} takes a value this command was not given",
}


def effect_sentence(effect: dict[str, Any]) -> str:
    return EFFECT_TEXT[effect["outcome"]].format(
        state=effect["state"], expected=_value_text(effect["expected"]),
        value=_value_text(effect.get("value")),
    )


def trial_sentence(trial: dict[str, Any]) -> str:
    """What one command did, as the timeline, the wizard and the summary say
    it. Takes the trial as its record holds it (``report.json`` included)."""
    if trial.get("error"):
        return f"not accepted: {trial['error']}"
    parts = []
    refusals = trial.get("refusals") or {}
    if refusals.get("device_errors") or refusals.get("last_error"):
        said = refusals.get("last_error") or next(
            (e["error"] for e in trial.get("device_errors") or [] if e.get("error")), "",
        )
        parts.append(f"the device refused it{': ' + str(said) if said else ''}")
    if trial.get("sent_nothing"):
        parts.append("the driver said it succeeded, but nothing was sent")
    parts.extend(effect_sentence(e) for e in trial.get("effects") or [])
    if trial.get("query"):
        q = trial["query"]
        parts.append(
            f"it reported {q['state']} as {_value_text(q['value'])}" if q["outcome"] == "reported"
            else "nothing came back" if q["outcome"] == "no_reply"
            else f"a reply came, but {q['state']} was not reported"
        )
    if trial.get("restart"):
        r = trial["restart"]
        if r["back_after"] is not None:
            parts.append(
                f"the device came back {r['back_after']} s after the command "
                f"(the driver declares {r['declared_seconds']} s)"
            )
        elif r["went_away_after"] is not None:
            parts.append("the device went away and had not come back when the audit stopped watching")
        else:
            parts.append("the connection stayed up")
    if refusals.get("unmatched"):
        n = refusals["unmatched"]
        parts.append(
            f"{n} {'reply' if n == 1 else 'replies'} while it was watched matched none of "
            "the driver's rules"
        )
    if not parts:
        received = (trial.get("traffic") or {}).get("received", 0)
        parts.append(
            f"the device sent {received} {'reply' if received == 1 else 'replies'}"
            if received else "nothing came back"
        )
    return "; ".join(parts) + "."


def _params_text(params: dict[str, Any]) -> str:
    if not params:
        return ""
    return " (" + ", ".join(f"{k} {v}" for k, v in params.items()) + ")"


def commands_for(session: "AuditSession", run: "DriverRun", **timings: Any) -> CommandPass:
    """The run's command pass, made the first time it is needed (when the
    driver first connects, so the wizard has the command list before the
    first Send). ``timings`` (``window_seconds``, ``flush_seconds``) are for
    tests."""
    if run.commands is None:
        run.commands = CommandPass(session, run)
        run.extra["commands"] = run.commands.to_dict
    for name, value in timings.items():
        setattr(run.commands, name, value)
    return run.commands
