"""The driver test: one run per driver the person chooses, and what each run did.

A session holds a list of runs. The last one is current: the person chose its
driver on "Which driver?", and every later step (the connection, connect and
listen) acts on it. "Test another driver" closes the current run, keeping its
results for the report, and goes back to the choice; the report gets a section
per run.

Each run owns at most one driver sandbox (``audit/sandbox.py``) at a time. The
session's teardown stops it, whichever way the session ends, before the
session resumes the project devices it paused, so the audited device's one
control connection is free when they reconnect.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from openavc.audit.driver_choice import (
    DriverChoice,
    driver_identity,
    model_listing,
    verdict_agreement,
)
from openavc.audit.session import AuditError
from openavc.drivers.registry import get_driver_class
from openavc.utils.logger import get_logger

if TYPE_CHECKING:
    from openavc.audit.sandbox import DriverSandbox
    from openavc.audit.session import AuditSession

log = get_logger(__name__)

NOT_INSTALLED = "Install {driver} first, then choose it again."
RUN_IN_PROGRESS = (
    "{driver} is still connected to the device. Choose Test another driver to "
    "finish with it first."
)


@dataclass
class DriverRun:
    """One driver tested against the device."""

    index: int
    choice: DriverChoice
    started_at: float | None = None
    finished_at: float | None = None
    sandbox: "DriverSandbox | None" = None
    # Filled by the later steps (the connection, connect and listen).
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def active(self) -> bool:
        return self.sandbox is not None and self.sandbox.started

    def to_dict(self) -> dict[str, Any]:
        out = {
            "index": self.index,
            "choice": self.choice.to_dict(),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "active": self.active,
        }
        for key, provider in self.extra.items():
            try:
                out[key] = provider() if callable(provider) else provider
            except Exception:
                log.debug("Audit run state provider failed", exc_info=True)
        return out

    async def stop(self) -> None:
        """Stop the sandbox, if one is running. Results stay."""
        import time

        sandbox = self.sandbox
        if sandbox is not None and sandbox.started:
            await sandbox.stop()
        if self.started_at is not None and self.finished_at is None:
            self.finished_at = time.time()


def current_run(session: "AuditSession") -> DriverRun | None:
    return session.runs[-1] if session.runs else None


def runs_state(session: "AuditSession") -> dict[str, Any]:
    """The runs as the wizard draws them (secrets never included)."""
    return {
        "device": dict(session.device_entered),
        "no_driver": session.no_driver,
        "runs": [run.to_dict() for run in session.runs],
    }


def open_runs(session: "AuditSession") -> None:
    """Make ``session`` able to hold driver runs, stopped at its teardown."""

    async def stop_runs() -> None:
        for run in session.runs:
            try:
                await run.stop()
            except Exception:
                log.warning("Stopping an audit driver run failed", exc_info=True)

    session.on_teardown(stop_runs)
    session.add_state_provider(lambda: runs_state(session))


def choose_driver(
    session: "AuditSession",
    driver_id: str | None,
    *,
    manufacturer: str = "",
    model: str = "",
    firmware: str = "",
    catalog: list[dict[str, Any]] | None = None,
) -> DriverRun | None:
    """Record the person's answer on "Which driver?".

    ``driver_id`` None means "there is no driver for this device yet": the
    device's make and model are recorded and the report covers the network
    check. Otherwise the driver must be installed. Choosing again before the
    current run has connected replaces that choice; once it has, the person
    finishes it first (``next_driver``).
    """
    entered = {
        "manufacturer": manufacturer.strip(),
        "model": model.strip(),
        "firmware": firmware.strip(),
    }
    session.device_entered = entered
    session.enter_step("driver")
    run = current_run(session)
    if driver_id is None:
        if run is not None and run.active:
            raise AuditError(RUN_IN_PROGRESS.format(driver=run.choice.identity.get("name")))
        if run is not None and run.started_at is None:
            session.runs.pop()
        session.no_driver = True
        session.add_timeline(
            "driver.none", "The person said there is no driver for this device yet.",
            **entered,
        )
        return None

    if get_driver_class(driver_id) is None:
        raise AuditError(NOT_INSTALLED.format(driver=driver_id))
    if run is not None and run.active:
        raise AuditError(RUN_IN_PROGRESS.format(driver=run.choice.identity.get("name")))

    verdict = session.footprint.verdict if session.footprint is not None else None
    choice = DriverChoice(
        driver_id=driver_id,
        manufacturer=entered["manufacturer"],
        model=entered["model"],
        firmware=entered["firmware"],
        identity=driver_identity(driver_id, catalog),
        model_listing=model_listing(driver_id, entered["manufacturer"], entered["model"], catalog),
        verdict_agreement=verdict_agreement(verdict, driver_id),
    )
    if run is not None and run.started_at is None:
        run.choice = choice  # changed their mind before connecting
    else:
        run = DriverRun(index=len(session.runs), choice=choice)
        session.runs.append(run)
    session.no_driver = False
    name = choice.identity.get("name") or driver_id
    version = choice.identity.get("version")
    session.add_timeline(
        "driver.chosen",
        f"Driver chosen: {name}{' ' + version if version else ''}.",
        driver_id=driver_id, verdict_agreement=choice.verdict_agreement,
        modified=choice.identity.get("modified"),
    )
    return run


async def next_driver(session: "AuditSession") -> None:
    """Finish the current run (its results stay) so another can be chosen."""
    run = current_run(session)
    if run is None:
        return
    await run.stop()
    session.add_timeline(
        "driver.finished",
        f"Finished testing {run.choice.identity.get('name') or run.choice.driver_id}.",
        driver_id=run.choice.driver_id,
    )
