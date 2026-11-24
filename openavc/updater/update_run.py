"""THE update run record: the newest update or rollback this system ran or was
sent, kept on disk as ``update-run.json`` in the data folder.

One run at a time: its target (the version, and for a target the cloud sent,
the address, the checksum and its rollback mark), where the target came from
(``SOURCES``), the step it is on and how it ended (``ENDS``, the update
history's words, which the cloud reads back). A target the cloud staged waits
here (step ``WAITING``) and becomes the run when it goes ahead; another run
takes its place.

The manager (``updater/manager.py``) holds one ``RunRecord`` and writes it
where it writes the staged record and the history row: a run begins, with a
pending history row of its own, as an update or rollback starts, takes each
step the Updates page shows, and ends failed where a failed apply is cleaned
up. Any other run is settled at the next start (``settle``).

**In flight** (``RunRecord.in_flight``): THE answer to whether an update or
rollback is under way, from its start until it fails or the process ends,
applied or not. Every door that starts one asks it through the manager
(``update_in_progress``, ``busy_refusal``: Install, a rollback, the cloud's
push and staged target, the maintenance window), and so does the restart
door (``restart.py``, through the manager's ``restart_refusal``) until the run
reaches its exit (``RunRecord.installing``): a restart would cut it off, and
from there its own exit ends the process. Each refusal names the run
(``RunRecord.in_flight_sentence``).

**Read back at start** (``RunRecord.read_at_start``): a staged record is the
run waiting; otherwise the run file, while the newest history row is its own
(every run writes its row as it begins, so a newest row of another run is a
later one, which an earlier release ran: it writes the history, never this
file); otherwise the newest history row.

**Settled at start** (``settle``): THE answer to how the last run ended,
decided once, at the next start, from the version that runs: applied; failed,
with the update helper's reason when it set the update aside, or because the
run was cut off before its exit (its step short of ``RESTARTING``), or because
the version did not move; rolled back, when an update that applied is undone
(the automatic rollback settles the run on the new version before it leaves,
so the version it goes back to reads it as reverted). An update the helper
will try again stays pending, with the helper's reason. The history writes
each run's end on the run's own row (the manager's ``_write_end``), and the
Updates page and the cloud read it there.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openavc.updater.checker import same_version, update_applied
from openavc.utils.fileio import atomic_write_text

log = logging.getLogger(__name__)

RUN_FILE = "update-run.json"
#: The shape of ``update-run.json``. A file of any other format is not read.
FORMAT = 1

# Where a run's target came from.
CHECKED = "checked"          # the release the update check found
STAGED = "staged"            # staged by the cloud, installed when asked for
PUSHED = "pushed"            # sent by the cloud to install now
PREVIOUS = "previous"        # the earlier release this system kept: a rollback
UNRECORDED = "unrecorded"    # read from an earlier release's history row
SOURCES = (CHECKED, STAGED, PUSHED, PREVIOUS, UNRECORDED)

#: The step of a staged target that has not gone ahead. A run that has goes
#: through the Updates page's words (``system.update_status``): backing_up,
#: downloading, verifying, applying, restarting. "" is a run read from an
#: earlier release's history row, which does not say.
WAITING = "waiting"
#: The step of a run that is applied and ends the process.
RESTARTING = "restarting"
#: The steps short of ``RESTARTING``, as a run cut off at one says it.
_STEPS_BEFORE_THE_EXIT = {
    "backing_up": "backing up", "downloading": "downloading",
    "verifying": "verifying", "applying": "applying",
}

# How a run ended: the update history's words.
PENDING = "pending"          # not ended yet
SUCCESS = "success"
FAILED = "failed"
ROLLED_BACK = "rolled_back"
ENDS = (PENDING, SUCCESS, FAILED, ROLLED_BACK)

#: A history row's status as a run's end ("applied" is an early row's success).
_ROW_ENDS = {
    "pending": PENDING, "success": SUCCESS, "applied": SUCCESS,
    "failed": FAILED, "rolled_back": ROLLED_BACK,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class UpdateRun:
    """One run: what it installs, from where, how far it got, how it ended."""

    target: str
    source: str
    step: str = ""
    end: str = PENDING
    #: The version the run started from ("" while it waits).
    from_version: str = ""
    error: str = ""
    rollback: bool = False
    update_url: str = ""
    checksum_sha256: str | None = None
    #: When it began, or was staged (UTC, ISO 8601).
    started: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return {"format": FORMAT, **asdict(self)}

    @classmethod
    def from_dict(cls, data: Any) -> UpdateRun | None:
        """The run a file holds, or None when it is not a run of this format."""
        if not isinstance(data, dict) or data.get("format") != FORMAT:
            return None
        fields = {k: data[k] for k in cls.__dataclass_fields__ if k in data}
        try:
            run = cls(**fields)
        except TypeError:
            return None
        return run if run._readable() else None

    def _readable(self) -> bool:
        """A run names its target, except a rollback whose target was not known."""
        return (bool(self.target) or self.rollback) and self.source in SOURCES and self.end in ENDS

    @property
    def restores_previous(self) -> bool:
        """A rollback to the earlier release this system kept: begun here, or
        read from its history row (marked ``rollback``, which only a rollback's
        row is; a downgrade the cloud sent is marked ``rollback`` as a target)."""
        return self.source == PREVIOUS or (self.source == UNRECORDED and self.rollback)

    def stages(self, other: UpdateRun) -> bool:
        """This is the cloud's staged target, waiting or tried before, and
        ``other`` installs it."""
        return (
            self.source == STAGED
            and (self.target, self.update_url) == (other.target, other.update_url)
        )


def _staged_run(staged: dict[str, Any]) -> UpdateRun:
    """The run a staged record (``staged-update.json``) waits as."""
    return UpdateRun(
        target=str(staged["target_version"]), source=STAGED, step=WAITING,
        rollback=bool(staged.get("rollback")), update_url=str(staged["update_url"]),
        checksum_sha256=staged.get("checksum_sha256"), started="",
    )


def row_end(row: dict[str, Any]) -> str:
    """A history row's status as a run's end (``ENDS``); a status no release
    writes reads as pending."""
    return _ROW_ENDS.get(row.get("status"), PENDING)


def _row_error(row: dict[str, Any]) -> str:
    return str(row.get("error") or "")


def _row_run(row: dict[str, Any] | None) -> UpdateRun | None:
    """The run an earlier release's history row describes, ended as it says
    (a rollback's row may not know its target)."""
    if row is None or not (row.get("to_version") or row.get("rollback")):
        return None
    return UpdateRun(
        target=str(row.get("to_version") or ""), source=UNRECORDED,
        from_version=str(row.get("from_version") or ""), end=row_end(row), error=_row_error(row),
        rollback=bool(row.get("rollback")), started=str(row.get("timestamp") or ""),
    )


def _describes(row: dict[str, Any] | None, run: UpdateRun) -> bool:
    """``row`` is the history's row for ``run``."""
    return row is not None and (row.get("from_version"), row.get("to_version")) == (
        run.from_version, run.target,
    )


def ends_on(row: dict[str, Any] | None, run: UpdateRun | None) -> bool:
    """``row`` is ``run``'s own history row and says another end than it: the
    row the history rewrites to read the run's end. A staged target that
    waits has no row."""
    if run is None or run.step == WAITING:
        return False
    return _describes(row, run) and (row_end(row), _row_error(row)) != (run.end, run.error)


def _newest_row(history: list[Any]) -> dict[str, Any] | None:
    row = history[0] if history else None
    return row if isinstance(row, dict) else None


def _still_saved(saved: UpdateRun | None, row: dict[str, Any] | None) -> bool:
    """The run file's run is still the last run: one that went ahead, whose
    own row is the newest (or the history has none). A run the released
    v0.41.0 failed or cut off has no row of its own, and the newest row is
    read in its place."""
    if saved is None or saved.step == WAITING:
        return False
    return row is None or _describes(row, saved)


def _kept_at_start(
    last: UpdateRun | None, saved: UpdateRun | None, staged: dict[str, Any] | None,
) -> UpdateRun | None:
    """The run a start keeps: a staged target waits (as the last run, when
    that was a try at it, or as the run file has it waiting); otherwise the
    last run."""
    if not staged:
        return last
    waiting = _staged_run(staged)
    return next((run for run in (last, saved) if run is not None and run.stages(waiting)), waiting)


def settle(run: UpdateRun | None, running: str, helper: dict[str, Any] | None = None) -> UpdateRun | None:
    """How ``run``, read back at a start, ended, on a system running
    ``running`` (the module's docstring says how). ``helper`` is the update
    the update helper set aside to try again (``manager.get_deferred_update``),
    or None. A staged target that waits, and a run already ended that was not
    undone since, come back as they are."""
    if run is None or run.step == WAITING:
        return run
    if run.end == PENDING:
        return replace(run, **_pending_end(run, running, helper))
    if _reverted(run, running):
        return replace(run, end=ROLLED_BACK, error=f"Update was reverted; running v{running}")
    return run


def _pending_end(run: UpdateRun, running: str, helper: dict[str, Any] | None) -> dict[str, str]:
    """The end and sentence of a run that had not ended when the process did."""
    if update_applied(running, run.from_version, run.target):
        return {"end": SUCCESS, "error": ""}
    set_aside = _set_aside(run, helper)
    if set_aside:
        return set_aside
    kind = "Rollback" if run.restores_previous else "Update"
    step = _STEPS_BEFORE_THE_EXIT.get(run.step)
    if step:
        return {"end": FAILED, "error": f"{kind} stopped while {step}: OpenAVC restarted before it was applied"}
    if run.restores_previous:
        return {"end": FAILED, "error": f"Rollback did not take effect: still running v{running}"}
    return {"end": FAILED, "error": f"Update did not apply: expected v{run.target}, running v{running}"}


def _set_aside(run: UpdateRun, helper: dict[str, Any] | None) -> dict[str, str] | None:
    """The end of an update the helper set aside, in its reason; pending while
    it tries again. None when it set aside no update to this run's target."""
    if not helper:
        return None
    if not same_version(str(helper.get("to_version", "")), run.target):
        return None
    reason = helper.get("reason") or "the reason was not recorded"
    if helper.get("final"):
        return {"end": FAILED, "error": f"Update did not apply ({reason}); install v{run.target} again once that is fixed"}
    return {"end": PENDING, "error": f"Update not applied yet ({reason}); it is tried again at the next start"}


def _reverted(run: UpdateRun, running: str) -> bool:
    """An update that applied runs the version it left again: undone. Strictly
    back where it started, never merely off its target (a release's tag and
    the version it bundles may differ), and never a rollback to the previous
    release (a downgrade the cloud sent is an update)."""
    if run.end != SUCCESS or run.restores_previous:
        return False
    if same_version(run.from_version, run.target):
        return False
    return bool(run.from_version) and same_version(running, run.from_version)


class RunRecord:
    """This system's update run, held and written to ``update-run.json``.

    A run begun here is in flight until it fails or the process ends; a
    staged target never takes the place of one in flight.
    """

    def __init__(self, data_dir: Path):
        self._path = Path(data_dir) / RUN_FILE
        self.run: UpdateRun | None = None
        self._in_flight = False

    def read_at_start(
        self, staged: dict[str, Any] | None, history: list[Any], running: str,
        helper: dict[str, Any] | None = None,
    ) -> UpdateRun | None:
        """Read the last run back and settle it (the module's docstring says
        how), keep it, and return it settled, for its history row.

        ``staged`` is the staged record still to install (``None`` when there
        is none), which then waits as the run kept; ``history`` the update
        history, newest row first; ``running`` the version this start runs;
        ``helper`` the update the helper set aside (``settle``).
        """
        saved = self._read()
        row = _newest_row(history)
        last = settle(saved if _still_saved(saved, row) else _row_run(row), running, helper)
        run = _kept_at_start(last, saved, staged)
        if run != saved:
            self._keep(run)
        self.run = run
        return last

    @property
    def in_flight(self) -> bool:
        """A run began in this process and has not failed. It stays in flight
        until the process ends, applied or not."""
        return self._in_flight

    @property
    def installing(self) -> bool:
        """The run in flight has not reached its exit (``RESTARTING``)."""
        return self._in_flight and self.run.step != RESTARTING

    def in_flight_sentence(self) -> str:
        """The run in flight in a sentence, for a door it refuses ("" when
        none is)."""
        if not self._in_flight:
            return ""
        run = self.run
        kind = "rollback" if run.rollback else "update"
        to = f"v{run.target}" if run.target else "the previous version"
        if self.installing:
            article = "A" if run.rollback else "An"
            return f"{article} {kind} to {to} is being installed."
        return f"The {kind} to {to} is applied and takes effect when OpenAVC restarts."

    def refusal(self) -> str:
        """What another update, a rollback or a staged target is refused with
        while a run is in flight ("" when none is)."""
        sentence = self.in_flight_sentence()
        return f"{sentence} Try again once it has finished." if self.installing else sentence

    def restart_refusal(self) -> str:
        """What a restart is refused with while the run is being installed,
        since a restart would cut it off; "" otherwise, its exit included
        (that exit ends the process)."""
        if not self.installing:
            return ""
        return f"{self.in_flight_sentence()} OpenAVC restarts by itself when it is done."

    def begin(self, run: UpdateRun) -> None:
        """``run`` begins in this process: the staged run going ahead (again,
        after a failed try) when it installs the target staged. Written with
        its first step."""
        if self.run is not None and self.run.stages(run):
            run = replace(run, source=STAGED)
        self.run = run
        self._in_flight = True

    def step(self, step: str) -> None:
        """The run in flight is on ``step``."""
        if self._in_flight:
            self._keep(replace(self.run, step=step))

    def fail(self, error: str) -> None:
        """The run in flight ended failed, with why."""
        if self._in_flight:
            self._in_flight = False
            self._keep(replace(self.run, end=FAILED, error=error))

    def stage(self, run: UpdateRun) -> None:
        """A staged target waits, in place of the run before it."""
        if not self._in_flight:
            self._keep(run)

    def drop_waiting(self) -> None:
        """The staged target waiting is gone: installed, replaced or dropped."""
        if self.run is not None and self.run.step == WAITING:
            self._keep(None)

    def _read(self) -> UpdateRun | None:
        try:
            run = UpdateRun.from_dict(json.loads(self._path.read_text(encoding="utf-8")))
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            run = None
        if run is None:
            log.warning("The update run record at %s is not one this version reads; ignoring it", self._path)
        return run

    def _keep(self, run: UpdateRun | None) -> None:
        """Hold ``run`` and write it; no run removes the file. A write that
        fails is logged: the run goes on."""
        self.run = run
        try:
            if run is None:
                self._path.unlink(missing_ok=True)
            else:
                atomic_write_text(self._path, json.dumps(run.to_dict(), indent=2))
        except OSError as e:
            log.warning("Could not write the update run record (%s)", e)
