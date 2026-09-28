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

Every command sent is kept for the report (:meth:`CommandPass.report_record`):
what was asked, what the driver's ``send_command`` returned or raised, and
the traffic from the moment it was sent to the end of its window.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from openavc.audit.observe import REPLY_WINDOW_SECONDS
from openavc.audit.session import AuditError
from openavc.core.device_traffic import RX, TX, serialize_entry
from openavc.core.state_store import is_flat_primitive
from openavc.drivers.base import missing_required_params
from openavc.utils.logger import get_logger

if TYPE_CHECKING:
    from openavc.audit.passes import DriverRun
    from openavc.audit.session import AuditSession

log = get_logger(__name__)

# How long after a command returns its replies are still its own.
WINDOW_SECONDS = REPLY_WINDOW_SECONDS
# How often the wizard hears about a command being watched.
FLUSH_SECONDS = 0.5
# Traffic entries each command shows live (the report keeps them all).
LIVE_ENTRIES = 40

SENDING = "sending"
WATCHING = "watching"
DONE = "done"

NOT_CONNECTED_YET = "Connect the driver first, on the Connect and listen step."
BUSY = "Wait for {label} to finish before sending another command."
BATCH_BUSY = "Wait for the status queries to finish before sending another command."
NO_SUCH_COMMAND = "{driver} has no command named {name}."
NO_QUERIES = "{driver} declares no status queries that can run without a value."


def _shown(value: Any) -> Any:
    """A value as a record keeps it: as is when it is a flat primitive, else
    its repr (a driver's ``send_command`` may return anything)."""
    return value if is_flat_primitive(value) else repr(value)


def _error_sentence(exc: BaseException) -> str:
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


def command_catalog(driver: Any) -> list[dict[str, Any]]:
    """The driver's commands, in its own order, with what it says about each."""
    info = getattr(driver, "DRIVER_INFO", {}) or {}
    commands = info.get("commands") or {}
    if not isinstance(commands, dict):
        return []
    polled = _polled_command_names(driver)
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
        })
    return out


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
        flush_seconds: float = FLUSH_SECONDS,
    ) -> None:
        self.session = session
        self.run = run
        self.window_seconds = window_seconds
        self.flush_seconds = flush_seconds
        self.trials: list[CommandTrial] = []
        # "Run all status queries": {"status", "total", "sent", "skipped"}.
        self.batch: dict[str, Any] | None = None
        self._task: asyncio.Task | None = None
        # The command list as it stood when the driver stopped, for the report.
        self._final_catalog: list[dict[str, Any]] | None = None

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
                await self._send_and_watch(trial)
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
        trial = CommandTrial(
            number=len(self.trials) + 1,
            command=name,
            label=str(entry.get("label") or name),
            params=params,
            attempt=1 + sum(1 for t in self.trials if t.command == name),
            batch=batch,
            connect_attempt=max(0, len(self.run.listens) - 1),
            sent_at=time.time(),
            secret_params=secret_names,
        )
        self.trials.append(trial)
        self.session.add_timeline(
            "command.sent",
            f"Sent {trial.label}{_params_text(trial.to_dict()['params'])}.",
            run=self.run.index, trial=trial.number, command=name,
        )
        self._publish()
        return trial

    async def _send_and_watch(self, trial: CommandTrial) -> None:
        sandbox = self.run.listen.sandbox
        try:
            result = await sandbox.manager.send_command(
                sandbox.device_id, trial.command, trial.params or None,
            )
            trial.result = _shown(result)
        except asyncio.CancelledError:
            trial.error = "The audit stopped before the command finished."
            raise
        except Exception as exc:
            trial.error = _error_sentence(exc)
            trial.error_type = type(exc).__name__
        finally:
            trial.returned_at = time.time()
            trial.ends_at = trial.returned_at + self.window_seconds
            trial.status = WATCHING
            self._publish()
        try:
            while time.time() < (trial.ends_at or 0) and sandbox.started:
                await asyncio.sleep(min(self.flush_seconds, max(0.0, trial.ends_at - time.time())))
                self._publish()
        finally:
            self._finish(trial)

    def _finish(self, trial: CommandTrial) -> None:
        if trial.status == DONE:
            return
        trial.finished_at = time.time()
        trial.status = DONE
        traffic = self._window_traffic(trial)
        received = sum(1 for e in traffic if e.direction == RX)
        if trial.error:
            text = f"{trial.label} was not accepted: {trial.error}"
        elif received:
            text = (
                f"{trial.label}: the device sent {received} "
                f"{'reply' if received == 1 else 'replies'}."
            )
        else:
            text = f"{trial.label}: nothing came back."
        self.session.add_timeline(
            "command.done", text, run=self.run.index, trial=trial.number,
            command=trial.command,
        )
        self._publish()

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
        return out

    def to_dict(self) -> dict[str, Any]:
        current = self.current()
        return {
            "catalog": self.catalog(),
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

    def _publish(self) -> None:
        self.session.publish({
            "type": "audit.commands", "run": self.run.index, "commands": self.to_dict(),
        })


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
