"""THE update run record: the newest update or rollback this system ran or was
sent, kept on disk as ``update-run.json`` in the data folder.

One run at a time: its target (the version, and for a target the cloud sent,
the address, the checksum and its rollback mark), where the target came from
(``SOURCES``), the step it is on and how it ended (``ENDS``, the update
history's words, which the cloud reads back). A target the cloud staged waits
here (step ``WAITING``) and becomes the run when it goes ahead; another run
takes its place.

The manager (``updater/manager.py``) holds one ``RunRecord`` and writes it
where it writes the staged record, the history row and the in-progress flag:
a run begins where the flag is set, takes each step the Updates page shows,
and ends failed where a failed apply is cleaned up. A run that reached its
exit is ended at the next start, as the update history ends its row.

**Read back at start** (``RunRecord.read_at_start``): a staged record is the
run waiting; otherwise the run file, unless a newer history row describes a
different run, which is what an earlier release leaves (it writes the staged
record and the history, never this file); otherwise the newest history row.
The run's end is the newest row's when that row is this run. What is read is
written back when it differs, so the file and the history agree from then on.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
        return bool(self.target) and self.source in SOURCES and self.end in ENDS

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


def _row_run(row: dict[str, Any] | None) -> UpdateRun | None:
    """The run an earlier release's history row describes."""
    if row is None or not row.get("to_version"):
        return None
    return UpdateRun(
        target=str(row["to_version"]), source=UNRECORDED,
        from_version=str(row.get("from_version") or ""),
        rollback=bool(row.get("rollback")), started=str(row.get("timestamp") or ""),
    )


def _describes(row: dict[str, Any] | None, run: UpdateRun) -> bool:
    """``row`` is the history's row for ``run``."""
    return row is not None and (row.get("from_version"), row.get("to_version")) == (
        run.from_version, run.target,
    )


def _moment(text: Any) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(text))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _replaced_by(row: dict[str, Any] | None, run: UpdateRun) -> bool:
    """A later run than ``run`` wrote ``row``: an earlier release ran it,
    which writes no run file. A moment that cannot be read replaces nothing."""
    if row is None or _describes(row, run):
        return False
    written, started = _moment(row.get("timestamp")), _moment(run.started)
    return written is not None and started is not None and written > started


def _own_row(row: dict[str, Any] | None, run: UpdateRun) -> bool:
    """``row`` is ``run``'s own: a staged target that waits has none."""
    return run.step != WAITING and _describes(row, run)


def _ended_as(run: UpdateRun | None, row: dict[str, Any] | None) -> UpdateRun | None:
    """``run`` with the end its history row says, when the row is its own."""
    if run is None or not _own_row(row, run):
        return run
    end = _ROW_ENDS.get(row.get("status"), run.end)
    return replace(run, end=end, error=str(row.get("error") or ""))


def _newest_row(history: list[Any]) -> dict[str, Any] | None:
    row = history[0] if history else None
    return row if isinstance(row, dict) else None


def _still_saved(saved: UpdateRun | None, row: dict[str, Any] | None) -> bool:
    """The run file's run is still the last run: one that went ahead, with no
    later run's row after it."""
    if saved is None or saved.step == WAITING:
        return False
    return not _replaced_by(row, saved)


def _read_back(
    saved: UpdateRun | None, staged: dict[str, Any] | None, row: dict[str, Any] | None,
) -> UpdateRun | None:
    """Which run a start reads back (the module's docstring says why)."""
    if staged:
        waiting = _staged_run(staged)
        return saved if saved is not None and saved.stages(waiting) else waiting
    if _still_saved(saved, row):
        return saved
    return _row_run(row)


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
        self, staged: dict[str, Any] | None, history: list[Any],
    ) -> UpdateRun | None:
        """Read the run back (the module's docstring says how) and keep it.

        ``staged`` is the staged record still to install (``None`` when there
        is none); ``history`` the update history, newest row first, with its
        newest row already ended for this start.
        """
        saved = self._read()
        row = _newest_row(history)
        run = _ended_as(_read_back(saved, staged, row), row)
        if run != saved:
            self._keep(run)
        self.run = run
        return run

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
