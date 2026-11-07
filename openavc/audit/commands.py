"""Commands: what each of the driver's commands does to the device.

The person sends the driver's commands one at a time, each through the
sandbox's ``DeviceManager.send_command``: the door a panel press, a macro step
and the REST API all go through, so every gate they meet applies here too.
The command must be one the driver declares, child ids are coerced, the
parameters are checked against the driver's own schema, a device that is not
connected is refused (a command that declares ``available_offline`` passes),
and so is one inside a commanded restart. Nothing reaches the device that the
person did not press Send for, except the status queries they ask to run
together and, as a setting is written, the status query that reads its value
first (``audit/settings.py``).

**The command list** is the live driver's own declaration, read by
``audit/command_catalog.py`` (which commands are status queries, which are
suggested, the confirmation each asks for).

A command refused before anything was sent (its parameters, the driver's own
check) closes its window at once: there is nothing on the device to watch
for. One that fails after sending is watched like any other.

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
``MAX_WINDOW_SECONDS``; "Stop watching" ends it). A command that declares what
it sets is watched until the driver's next poll has had time to report it (a
device that reports a value only when polled), and ends at the usual time once
every declared value has come back. In the window the audit keeps
every byte both ways, every status value that changed, and every sign that
the device refused it: an error the driver raised or published
(``device.error``), a ``last_error`` it wrote, a reply no response rule
matched. Once the window closes, the command's declared effect and a status
query's answer are judged against what the device reports then
(``audit/effects.py``). A command the driver said succeeded while no byte left
for the device is flagged (``command_sent_nothing``): the gate nothing else
catches.

**A command that restarts the device** (``restarts_device_for``) is watched
for its declared window and ``RESTART_MARGIN_SECONDS`` more, ending
``AFTER_RECONNECT_SECONDS`` after the driver is back. The record says when
OpenAVC noticed the device go, when the driver reconnected, and whether that
was inside the declared window: the measurement a driver's figure is set from.

**Try again** sends the same command with the same values (held on the
server, so a secret one never goes back to the browser). Every command
records how long after the previous one it was sent, which is how a device
that refuses a command for a few seconds after a related one shows up.

A command that asks for a confirmation is sent either way: asking is the
wizard's, as on the device page.

**"Did it happen?"** Then the person says what they saw on the device
itself (``yes``, ``no``, ``partly``, ``cant_tell``) with an optional note. It
is their answer, kept beside what the device reported, never merged into it:
a display that said "input hdmi2" while the screen stayed black is exactly
the disagreement a report exists to show.

**What changed**, per command and over the run, is ``audit/changes.py``; what
a command did, in words, is ``audit/command_text.py``.

Every command sent is kept for the report (:meth:`CommandPass.report_record`):
what was asked, what the driver's ``send_command`` returned or raised, the
traffic from the moment it was sent to the end of its window, and what the
window showed.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from openavc.audit.changes import changed_values, current_values, moved_values
from openavc.audit.command_catalog import command_catalog
from openavc.audit.command_text import ANSWERS, params_text, trial_sentence
from openavc.audit.effects import (
    Readout,
    declared_effects,
    judged_effects,
    query_answer,
    same_value,
)
from openavc.audit.observe import REPLY_WINDOW_SECONDS, command_sent_nothing, recorded
from openavc.audit.session import AuditError
from openavc.core.device_traffic import RX, TX, serialize_entry
from openavc.drivers.command_files import CommandFile, DeviceFile
from openavc.drivers.spec import is_secret_setting
from openavc.utils.log_redaction import is_secret_key

if TYPE_CHECKING:
    from openavc.audit.passes import DriverRun
    from openavc.audit.session import AuditSession

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
# keeps them all). The traffic keeps LIVE_HEAD entries from the command's own
# message on, and the newest: a poll that lands in the window would otherwise
# push what the command sent, and what came straight back, out of view.
LIVE_ENTRIES = 40
LIVE_HEAD = 20
LIVE_CHANGES = 20
# A value that changed, with nothing being watched, this long before a
# command was sent was already moving (a meter, a clock): its changes in the
# command's window are shown apart from what the command moved.
ALREADY_MOVING_SECONDS = 60.0

SENDING = "sending"
WATCHING = "watching"
DONE = "done"

NOT_CONNECTED_YET = "Connect the driver first, on the Connect and listen step."
BUSY = "Wait for {label} to finish before sending another command."
BATCH_BUSY = "Wait for the status queries to finish before sending another command."
NO_QUERIES = "{driver} has no status queries that can run without a value."
NOT_WATCHING = "No command is being watched."
WATCH_CAP = "A command is watched for {span} at most."
NO_SUCH_TRIAL = "There is no command number {number} to send again."
# A file is used once and deleted, so a command that takes one is sent again
# from the list, with the file chosen again.
FILE_NOT_AGAIN = "Choose the file again and send {label} from the list."
NO_TRIAL_TO_ANSWER = "There is no command number {number} to answer for."
NOT_SENT_YET = "Wait for {label} to be sent before saying what the device did."


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
    # The sandbox store's write number when the call returned: what the
    # device reported after it, exactly.
    returned_seq: int = 0
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
    # The status values already changing on their own when it was sent.
    already_moving: list[str] = field(default_factory=list)
    # Errors the driver published for the device while it was watched.
    device_errors: list[dict[str, Any]] = field(default_factory=list)
    # Every last_error the driver wrote while it was watched: {"t", "error"}.
    error_writes: list[dict[str, Any]] = field(default_factory=list)
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
    # Every drop inside the window, in order, with when the driver was back:
    # [went, back or None]. A device rebooting can reset the first connection
    # back too, so there can be more than one.
    gone_and_back: list[list[float | None]] = field(default_factory=list)
    # The drops inside the window of a command that does not declare a
    # restart: [{"after", "back_after"}], seconds after the send and after
    # the drop.
    drops: list[dict[str, Any]] = field(default_factory=list)
    # The person's answer to "Did it happen?": {"answer", "note", "at"}.
    answer: dict[str, Any] | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    # Where the observer's traffic and event lists stood as the command was
    # sent, and as its window closed. The window is cut by position, not by
    # clock: the lists only grow, while ``time.time()`` ticks every 15.6 ms
    # on Windows before Python 3.13, so a reply that landed just before the
    # send can carry the send's own timestamp.
    marks: dict[str, int] = field(default_factory=dict)
    end_marks: dict[str, int] = field(default_factory=dict)
    # Set once the driver's call has returned (or failed): what ``send``
    # waits for before answering, so its answer already holds a refusal.
    returned: asyncio.Event = field(default_factory=asyncio.Event, repr=False, compare=False)
    # A command that takes or hands back a file (its params hold CommandFile
    # objects, shown as name and size): sent with files allowed, answered as
    # soon as it starts, never sent again by "Try again".
    file_command: bool = False
    # Run once the driver's call has returned: the staged uploads go.
    on_finished: Any = field(default=None, repr=False, compare=False)
    # Keeps a file the command handed back, for the wizard's download:
    # (DeviceFile) -> {"id", "name", "size"}.
    keep_file: Any = field(default=None, repr=False, compare=False)
    # The file it handed back: {"id", "name", "size"} (the id is the download's).
    returned_file: dict[str, Any] | None = None


    def mark(self, observer: Any, into: dict[str, int]) -> None:
        into["traffic"] = len(observer.traffic)
        into["events"] = len(observer.events)

    def traffic_in(self, observer: Any) -> list[Any]:
        """The observer's traffic from the send to the end of the window."""
        return observer.traffic[self.marks.get("traffic", 0):self.end_marks.get("traffic")]

    def events_in(self, observer: Any) -> list[Any]:
        """The contract events from the send to the end of the window."""
        return observer.events[self.marks.get("events", 0):self.end_marks.get("events")]

    def to_dict(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "command": self.command,
            "label": self.label,
            "params": {
                k: "***" if k in self.secret_params else recorded(v)
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
            "already_moving": list(self.already_moving),
            "device_errors": list(self.device_errors),
            "error_writes": list(self.error_writes),
            "effects": list(self.effects),
            "query": self.query,
            "refusals": dict(self.refusals),
            "sent_nothing": self.sent_nothing,
            "restart": self.restart,
            "drops": list(self.drops),
            "answer": dict(self.answer) if self.answer else None,
            "file_command": self.file_command,
            "returned_file": (
                {"name": self.returned_file["name"], "size": self.returned_file["size"]}
                if self.returned_file else None
            ),
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
        # The command list, the picker values and "what changed", as the
        # wizard last heard them.
        self._catalog_sent: list[dict[str, Any]] | None = None
        self._pickers_sent: dict[str, Any] | None = None
        self._changed_sent: list[dict[str, Any]] | None = None

    # -- where it runs --------------------------------------------------------

    def _live(self):
        """The current attempt, while its driver is connected to the device."""
        listen = self.run.listen
        if listen is None or not listen.sandbox.started:
            raise AuditError(NOT_CONNECTED_YET)
        return listen

    def _attempt(self, trial: CommandTrial) -> Any:
        """The connect-and-listen attempt whose connection carried ``trial``."""
        return self.run.listens[trial.connect_attempt] if self.run.listens else None

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
        return {key: recorded(state.get(key)) for key in sorted(keys)}

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

    async def send(
        self,
        name: str,
        params: dict[str, Any] | None = None,
        *,
        files: dict[str, CommandFile] | None = None,
        on_finished: Any = None,
        keep_file: Any = None,
    ) -> CommandTrial:
        """Send one command and watch what it does (in the background).

        ``files`` fills a command's file params; ``on_finished`` runs once
        the driver's call has returned (the door's staged uploads go), and
        ``keep_file`` keeps a file the command hands back for the wizard's
        download.
        """
        try:
            self._refuse_if_busy()
            listen = self._live()
        except AuditError:
            if on_finished is not None:
                on_finished()
            raise
        entry = next((c for c in self.catalog() if c["name"] == name), None)
        if entry is None:
            # Still sent: the manager's own refusal is part of what the
            # audit records about how the driver treats an unknown name.
            entry = {"name": name, "label": name}
        listen.end_window_now("A command was sent")
        self.session.enter_step("commands")
        values = dict(params or {})
        values.update(files or {})
        trial = self._open_trial(entry, values, batch=False)
        trial.file_command = bool(entry.get("takes_file") or entry.get("returns_file") or files)
        trial.on_finished = on_finished
        trial.keep_file = keep_file
        task = self._task = self.session.track_task(asyncio.create_task(self._send_and_watch(trial)))
        if trial.file_command:
            # A file can take minutes to reach a device, longer than a request
            # may wait (30 s through the cloud tunnel): answered as soon as it
            # starts, and followed over audit.subscribe like every send.
            return trial
        # Answer once the driver's call has returned, not as soon as it is
        # queued: a command refused at once is then already recorded, and the
        # caller's copy of the state is not older than what was published.
        returned = asyncio.ensure_future(trial.returned.wait())
        try:
            await asyncio.wait({task, returned}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            returned.cancel()
        return trial

    async def send_again(self, number: int, name: str | None = None) -> CommandTrial:
        """"Try again": the same command with the same values as command
        ``number`` (secret ones included, which never left the server).
        ``name``, when given, must be that command's."""
        earlier = next((t for t in self.trials if t.number == number), None)
        if earlier is None or (name is not None and earlier.command != name):
            raise AuditError(NO_SUCH_TRIAL.format(number=number))
        if earlier.file_command:
            raise AuditError(FILE_NOT_AGAIN.format(label=earlier.label))
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
        def runnable(c: dict[str, Any]) -> bool:
            return not (c["needs_input"] or c.get("takes_file") or c.get("returns_file"))

        queries = [c for c in catalog if c["query"] and runnable(c)]
        skipped = [c["name"] for c in catalog if c["query"] and not runnable(c)]
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
        # A value typed into a secret parameter (a password a command sets,
        # or one named like a credential) is masked in every record, the
        # traffic included.
        secret_names = {
            k for k, pdef in (entry.get("params") or {}).items()
            if isinstance(pdef, dict) and (is_secret_setting(k, pdef) or is_secret_key(k))
        }
        secret = {str(params[k]) for k in secret_names if params.get(k) not in (None, "")}
        if secret:
            self.run.secrets |= secret
            self.run.listen.sandbox.observer.add_secrets(secret)
        previous = self.trials[-1] if self.trials else None
        now = time.time()
        # Read before the trial is added: from then on something is watched.
        moving = self.run.moving_since(now - ALREADY_MOVING_SECONDS)
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
            already_moving=moving,
        )
        self.trials.append(trial)
        self.session.add_timeline(
            "command.sent",
            f"Sent {trial.label}{params_text(trial.to_dict()['params'], entry.get('params'))}.",
            run=self.run.index, trial=trial.number, command=name,
        )
        self._publish(trial)
        return trial

    def _readback_span(self, trial: CommandTrial) -> float:
        """How long a command that declares what it sets may be watched for
        the device to report it: until the driver's next poll has had time to
        answer (the poll interval and ``REPLY_WINDOW_SECONDS``), within the
        ceiling. 0 for a command that declares nothing, or a driver that does
        not poll. A device that reports a value only when polled, more slowly
        than the window, otherwise reads as never having taken a command that
        worked."""
        if not trial.declared.get("sets"):
            return 0.0
        attempt = self._attempt(trial)
        poll = float(getattr(attempt, "poll_interval", 0.0) or 0.0)
        if poll <= 0:
            return 0.0
        return min(poll + REPLY_WINDOW_SECONDS, self.max_window_seconds)

    def _read_back(self, trial: CommandTrial, sandbox: Any) -> bool:
        """Has the device reported each value the command declares since the
        call returned, or does it already read what it should? (A reply on
        its way when the command was sent can still answer with the old
        value.)"""
        entry = {**trial.declared, "params": self._params_of(trial.command)}
        state = sandbox.device_state()
        fresh = sandbox.written_after(trial.returned_seq)
        for effect in declared_effects(entry, trial.params, self._driver()):
            if not effect["has_value"]:
                continue
            key = effect["state_key"]
            if key not in fresh and not same_value(effect["expected"], state.get(key)):
                return False
        return True

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
                "old": recorded(old), "new": recorded(new),
            })

        def on_error(_event: str, payload: Any = None) -> None:
            if trial.status != DONE:
                error = (payload or {}).get("error") if isinstance(payload, dict) else payload
                trial.device_errors.append({
                    "t": time.time(), "error": str(error or ""),
                    "position": len(sandbox.observer.traffic),
                })

        def on_gone(_event: str, _payload: Any = None) -> None:
            if trial.status == DONE:
                return
            now = time.time()
            if trial.went_away_at is None:
                trial.went_away_at = now
            if not trial.gone_and_back or trial.gone_and_back[-1][1] is not None:
                trial.gone_and_back.append([now, None])

        def on_back(_event: str, _payload: Any = None) -> None:
            if trial.status == DONE or not trial.gone_and_back:
                return
            if trial.gone_and_back[-1][1] is None:
                trial.gone_and_back[-1][1] = time.time()
            if trial.back_at is not None:
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
        error_writes = getattr(sandbox.state, "error_writes", [])
        errors_from = len(error_writes)
        trial.mark(sandbox.observer, trial.marks)
        try:
            try:
                result = await sandbox.manager.send_command(
                    sandbox.device_id, trial.command, trial.params or None,
                    files_allowed=trial.file_command,
                )
                trial.result = recorded(result)
                if isinstance(result, DeviceFile) and trial.keep_file is not None:
                    trial.returned_file = trial.keep_file(result)
            except asyncio.CancelledError:
                trial.error = "The audit stopped before the command finished."
                raise
            except Exception as exc:
                trial.error = error_sentence(exc)
                trial.error_type = type(exc).__name__
            finally:
                if trial.on_finished is not None:
                    trial.on_finished()
                    trial.on_finished = None
                trial.returned_at = time.time()
                trial.marks["returned"] = len(sandbox.observer.traffic)
                trial.returned_seq = getattr(sandbox.state, "seq", 0)
                span = self.window_seconds if window is None else window
                if not trial.error:
                    span = max(span, self._restart_span(trial))
                    if window is None:
                        span = max(span, self._readback_span(trial))
                elif command_sent_nothing(
                    trial.traffic_in(sandbox.observer), trial.sent_at, trial.returned_at,
                    grace=0.0, started_inclusive=True,
                ):
                    # Refused before a byte left for the device: nothing to watch.
                    span = 0.0
                trial.ends_at = trial.returned_at + span
                trial.status = WATCHING
                trial.returned.set()
                self._publish(trial)
            # Watched past the usual window only to hear the device report what
            # the command declares: once it has (or when nothing was sent, so
            # nothing can come back), the window ends as usual.
            waiting_for_readback = (
                window is None and not trial.error and not self._restart_span(trial)
                and self._readback_span(trial) > self.window_seconds
            )
            usual_end = (trial.returned_at or 0) + self.window_seconds
            while time.time() < (trial.ends_at or 0) and sandbox.started:
                await asyncio.sleep(
                    min(self.flush_seconds, max(0.0, (trial.ends_at or 0) - time.time()))
                )
                if (
                    waiting_for_readback and not trial.extended and time.time() >= usual_end
                    and (self._read_back(trial, sandbox) or command_sent_nothing(
                        trial.traffic_in(sandbox.observer), trial.sent_at,
                        trial.returned_at or trial.sent_at, started_inclusive=True,
                    ))
                ):
                    trial.ends_at = time.time()
                    waiting_for_readback = False
                self._publish(trial)
        finally:
            # Every last_error the driver wrote while it was watched, with
            # when, a rewrite of the same text included.
            own_key = f"device.{sandbox.device_id}.last_error"
            trial.error_writes = [
                {"t": t, "error": recorded(value), "position": position}
                for t, key, value, position in error_writes[errors_from:] if key == own_key
            ]
            self._unwatch(sandbox, handles)
            self._finish(trial)

    def _finish(self, trial: CommandTrial) -> None:
        if trial.status == DONE:
            return
        trial.finished_at = time.time()
        attempt = self._attempt(trial)
        if attempt is not None:
            trial.mark(attempt.sandbox.observer, trial.end_marks)
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
                f"{trial.label}: the driver said it succeeded, but nothing was sent to the device.",
                run=self.run.index, trial=trial.number, command=trial.command,
            )
        self._publish(trial)

    def _conclude(self, trial: CommandTrial) -> None:
        """What the window showed: the declared effect against what the
        device reports now, a status query's answer, the signs of a refusal,
        whether anything was sent, and how a drop or a restart went."""
        attempt = self._attempt(trial)
        traffic = self._window_traffic(trial)
        if not trial.error:
            readout = Readout(
                state=current_values(attempt) if attempt is not None else {},
                changed={c["key"] for c in trial.changes},
                driver=self._driver(),
            )
            entry = {**trial.declared, "params": self._params_of(trial.command)}
            trial.effects.extend(judged_effects(entry, trial, readout))
            if trial.declared.get("query_for"):
                answered = any(e.direction == RX for e in traffic)
                trial.query = query_answer(entry, trial, readout, answered)
        trial.refusals.update(self._refusals(trial, traffic))
        trial.refusals.update(_unmatched_replies(trial, attempt))
        if not trial.error and attempt is not None:
            trial.sent_nothing = _sent_nothing(trial, attempt)
        if trial.went_away_at and not trial.declared.get("restarts_device_for"):
            # The connection dropped while it was watched: say so, every time,
            # rather than letting the values a reconnect rewrites read as the
            # command's.
            trial.drops = _drops(trial)
        if trial.declared.get("restarts_device_for") and not trial.error:
            trial.restart = _restart(trial)

    def _refusals(self, trial: CommandTrial, traffic: list[Any]) -> dict[str, Any]:
        """Which signs of a refusal (an error the driver published, a
        ``last_error`` it wrote) answer the command, and which a request the
        driver sent on its own after it (a poll, a follow-up query).

        The command's own exchange runs from the send until its reply could
        still be on the way, ``REPLY_WINDOW_SECONDS`` after the call returned,
        and ends early at the driver's next request: what is refused after
        that answers that request, so it is kept apart, with what the driver
        had just sent, and never put on the command."""
        from openavc.audit.report_text import traffic_text as _traffic_text

        returned = trial.returned_at or trial.sent_at
        # The window's traffic with each entry's place in the device's list,
        # the order things happened in whatever the clock says.
        start = trial.marks.get("traffic", 0)
        returned_at = trial.marks.get("returned", start)
        attempt = self._attempt(trial)
        window = trial.traffic_in(attempt.sandbox.observer) if attempt is not None else []
        placed = [
            (start + i, e) for i, e in enumerate(window)
            if e.direction == TX and not e.chunk
        ]
        after = [(at, e) for at, e in placed if at >= returned_at]
        if after and len(after) == len(placed):
            after = after[1:]  # sent once the call had returned: the command's own
        # A refusal belongs to the command when it came before the driver's
        # next request and while its reply could still be on the way.
        next_request = after[0][0] if after else None
        deadline = returned + REPLY_WINDOW_SECONDS

        def own(sign: dict[str, Any]) -> bool:
            if next_request is not None and sign.get("position", 0) > next_request:
                return False
            return sign["t"] <= deadline

        own_errors = [e for e in trial.device_errors if own(e)]
        own_writes = [w for w in trial.error_writes if own(w) and w.get("error")]
        said = (
            own_writes[-1]["error"] if own_writes
            else next((e["error"] for e in reversed(own_errors) if e.get("error")), None)
        )

        later: dict[str, dict[str, Any]] = {}
        redactor = self._redactor(trial) if self.run.listens else None
        signs = sorted(
            [(e.get("position", 0), e["t"], e.get("error") or "")
             for e in trial.device_errors if not own(e)]
            + [(w.get("position", 0), w["t"], w["error"]) for w in trial.error_writes
               if not own(w) and w.get("error")],
            key=lambda s: (s[0], s[1]),
        )
        for position, t, text in signs:
            if str(text) in later:
                later[str(text)]["count"] += 1
                continue
            # The request it answered: the last one sent before it.
            request = next((e for at, e in reversed(placed) if at < position), None)
            later[str(text)] = {
                "error": text,
                "after": round(t - trial.sent_at, 1),
                "count": 1,
                "request": (
                    _traffic_text(serialize_entry(request, redactor), 80)
                    if request is not None and redactor is not None else ""
                ),
            }
        return {
            "device_errors": len(own_errors),
            "last_error_writes": len(own_writes),
            "last_error": said,
            "later": list(later.values()),
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

    def _own_send(self, trial: CommandTrial, traffic: list[Any]) -> int:
        """Where in ``traffic`` (the window without its raw chunks) the
        command's own message went out: the last one sent before the call
        returned, else the first after it (a command queued behind a poll goes
        out after polling messages already on the way). 0 when none did."""
        attempt = self._attempt(trial)
        if attempt is None:
            return 0
        start = trial.marks.get("traffic", 0)
        returned = trial.marks.get("returned", start)
        sends = [
            (start + i, e) for i, e in enumerate(trial.traffic_in(attempt.sandbox.observer))
            if e.direction == TX and not e.chunk
        ]
        before = [e for at, e in sends if at < returned]
        after = [e for at, e in sends if at >= returned]
        own = before[-1] if before else (after[0] if after else None)
        return next((i for i, e in enumerate(traffic) if e is own), 0)

    def _window_traffic(self, trial: CommandTrial) -> list[Any]:
        attempt = self._attempt(trial)
        if attempt is None:
            return []
        return [e for e in trial.traffic_in(attempt.sandbox.observer) if not e.chunk]

    def _redactor(self, trial: CommandTrial):
        return self.run.listens[trial.connect_attempt].sandbox.observer.redactor()

    def _trial_view(self, trial: CommandTrial, *, every_entry: bool) -> dict[str, Any]:
        traffic = self._window_traffic(trial) if self.run.listens else []
        redactor = self._redactor(trial) if self.run.listens else None
        if every_entry:
            shown, gaps = traffic, []
        else:
            shown, gaps = live_traffic(traffic, self._own_send(trial, traffic))
        out = trial.to_dict()
        out["traffic"] = {
            "sent": sum(1 for e in traffic if e.direction == TX),
            "received": sum(1 for e in traffic if e.direction == RX),
            "entries": [serialize_entry(e, redactor) for e in shown] if redactor else [],
        }
        if gaps:
            # What only the report carries: how many, and where each run of
            # them would go among the entries shown.
            out["traffic"]["left_out"] = sum(g["count"] for g in gaps)
            out["traffic"]["gaps"] = gaps
        if redactor is not None:
            out["params"] = redactor.value(dict(out["params"]))
            out["changes"] = redactor.value(out["changes"])
            out["device_errors"] = redactor.value(out["device_errors"])
            out["refusals"] = redactor.value(out["refusals"])
        out["moved"] = moved_values(
            out["changes"], trial.already_moving, getattr(self._driver(), "DRIVER_INFO", {}) or {},
        )
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
            "changed": changed_values(self.run),
        }

    def report_record(self) -> dict[str, Any]:
        """The commands as the report keeps them: the list the driver
        declared, every command sent with all of its window's traffic, and
        what changed."""
        return {
            "catalog": self.catalog(),
            "batch": dict(self.batch) if self.batch else None,
            "trials": [self._trial_view(t, every_entry=True) for t in self.trials],
            "changed": changed_values(self.run),
        }

    def publish_update(self) -> None:
        """Tell the wizard the batch, what is current and what changed (a
        setting written elsewhere moves "what changed" too)."""
        self._publish()

    def refresh(self) -> None:
        """Tell the wizard whatever the device's own reports have moved since
        it last heard: the command list (a Python driver can add commands
        once connected), the picker values, "what changed". Called as the
        connection's state moves; sends nothing when none of them did."""
        update = self._lists_update()
        changed = changed_values(self.run)
        if changed != self._changed_sent:
            update["changed"] = changed
            self._changed_sent = changed
        if update:
            self.session.publish({"type": "audit.commands", "run": self.run.index, "commands": update})

    def _lists_update(self) -> dict[str, Any]:
        """The command list and the picker values, each only if it changed
        since the wizard last heard it."""
        update: dict[str, Any] = {}
        catalog = self.catalog()
        if catalog != self._catalog_sent:
            update["catalog"] = catalog
            self._catalog_sent = catalog
        pickers = self.picker_state()
        if pickers != self._pickers_sent:
            update["picker_state"] = pickers
            self._pickers_sent = pickers
        return update

    def _publish(self, trial: CommandTrial | None = None) -> None:
        """Tell the wizard what moved: the trial that changed (merged by its
        number), the batch, and the command list and picker values only when
        they changed."""
        current = self.current()
        changed = changed_values(self.run)
        self._changed_sent = changed
        update: dict[str, Any] = {
            "batch": dict(self.batch) if self.batch else None,
            "current": current.number if current is not None else None,
            "trials": [self._trial_view(trial, every_entry=False)] if trial is not None else [],
            "changed": changed,
            **self._lists_update(),
        }
        self.session.publish({"type": "audit.commands", "run": self.run.index, "commands": update})


def _unmatched_replies(trial: CommandTrial, attempt: Any) -> dict[str, Any]:
    """The replies in the window no response rule matched: how many, and the
    first few as they read."""
    events = []
    if attempt is not None:
        events = [
            e for e in trial.events_in(attempt.sandbox.observer)
            if e.kind == "unmatched_response"
        ]
    return {
        "unmatched": len(events),
        "unmatched_examples": [str(e.detail.get("text", ""))[:200] for e in events[:5]],
    }


def _sent_nothing(trial: CommandTrial, attempt: Any) -> bool | None:
    """Did no byte leave for the device while the driver's call ran? None
    when the driver's traffic is not captured at all."""
    observer = attempt.sandbox.observer
    if not observer.frames():
        return None
    return command_sent_nothing(
        trial.traffic_in(observer), trial.sent_at,
        trial.returned_at or trial.sent_at, started_inclusive=True,
    )


def _drops(trial: CommandTrial) -> list[dict[str, Any]]:
    """Each drop in the window, seconds after the send, and how long the
    driver took to come back (None when it had not)."""
    return [
        {
            "after": round(gone - trial.sent_at, 1),
            "back_after": round(again - gone, 1) if again else None,
        }
        for gone, again in trial.gone_and_back
    ]


def _restart(trial: CommandTrial) -> dict[str, Any]:
    """How a declared restart went: when the device went and came back,
    timed from the send, and whether that was inside the declared window."""
    declared = trial.declared["restarts_device_for"]
    went, back = trial.went_away_at, trial.back_at
    return {
        "declared_seconds": declared,
        "went_away_after": round(went - trial.sent_at, 1) if went else None,
        "back_after": round(back - trial.sent_at, 1) if back else None,
        "away_for": round(back - went, 1) if went and back else None,
        "within_declared": (back - trial.sent_at) <= declared if back else None,
    }


def live_traffic(traffic: list[Any], own: int) -> tuple[list[Any], list[dict[str, int]]]:
    """A command's traffic cut to ``LIVE_ENTRIES`` for the live view: from its
    own message (at ``own``) for ``LIVE_HEAD`` entries, then the newest. Each
    run left out is ``{"before", "count"}``, ``before`` the index among the
    entries shown that it would come before."""
    if len(traffic) <= LIVE_ENTRIES:
        return traffic, []
    own = max(0, min(own, len(traffic) - LIVE_ENTRIES))
    head_end = own + LIVE_HEAD
    tail_start = len(traffic) - (LIVE_ENTRIES - LIVE_HEAD)
    shown = traffic[own:head_end] + traffic[tail_start:]
    gaps = []
    if own > 0:
        gaps.append({"before": 0, "count": own})
    if tail_start > head_end:
        gaps.append({"before": LIVE_HEAD, "count": tail_start - head_end})
    return shown, gaps


def _duration(seconds: float) -> str:
    """"2 minutes", "40 seconds", "1 minute"."""
    if seconds >= 60 and seconds % 60 == 0:
        minutes = int(seconds // 60)
        return f"{minutes} minute{'' if minutes == 1 else 's'}"
    whole = round(seconds)
    return f"{whole} second{'' if whole == 1 else 's'}"


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
