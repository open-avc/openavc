"""What a start that failed does next: THE rule.

A start that a stage stopped (the run-state record's failed phase,
middleware/startup_splash.py) leaves the splash page showing its reason, and
the restart door is armed (openavc/restart.py), so Start again on that page
and a restart from the cloud both work. Then, through ``after_failure``:

- Under a service manager (``restart.is_service_managed``: NSSM, systemd,
  launchd, a container's restart policy, appliance hardware's device
  supervisor), the page shows the reason for ``HOLD_SECONDS`` and the process
  ends (``restart.exit_to_retry``) for the manager to start it again. With an
  update pending, that next start is the second, which rolls the update back
  (updater/rollback.py counts them), so a release that cannot start goes back
  by itself on every install type.
- The retries are bounded: a failed start that is already the
  ``MAX_AUTOMATIC_RESTARTS``-th automatic restart in a row stays on the page
  with its reason and Start again (``RunState.hold``), and so does any failed
  start with no service manager (nothing would start it again).

Which starts are automatic restarts: a failed start that ends for a retry
leaves a record in the status folder (status_folder.write_start_retry) of how
many it has made. The next start takes it (``automatic_restarts``: read and
removed, so no later start counts it again), and is that many restarts into
the run when it began within ``RESTART_GAP_SECONDS`` of the record, on the same
boot. Any other start (a new boot, a manual service restart, Start again, a
start long after) has the whole run ahead of it, so a power cycle still helps.

A start that finishes clears the failed start's reason
(status_folder.clear_startup_error, from ``openavc.main``), which stays
until then: the tray and the menu bar keep showing it through the retries.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from openavc import restart, status_folder
from openavc.utils.logger import get_logger

log = get_logger("openavc.main")

# How long the splash page shows a failed start's reason before the process
# ends for its service manager to start it again.
HOLD_SECONDS = 60
# How many times in a row a failed start is started again by itself before
# it stays on the splash page for someone to press Start again.
MAX_AUTOMATIC_RESTARTS = 3
# How soon after a retry's record the next start must begin to be that
# retry: the stop (restart.LONGEST_EXIT_SECONDS) and the service manager's
# 5 s, with a start's import and bind well inside it.
RESTART_GAP_SECONDS = 120
# How far apart two readings of when this computer started may be and still
# be the same boot (the clocks drift, and a sleep moves one of them).
SAME_BOOT_SLACK_SECONDS = 30

# The refusal for a Start again with no failed start to start again.
NOT_FAILED = "not_failed"


def _computer_started() -> float:
    """When this computer started, as near as the clocks tell: the wall
    clock less the time since the boot."""
    return time.time() - time.monotonic()


def _retry_record(restarts: int) -> dict[str, Any]:
    return {
        "restarts": restarts,
        "monotonic": time.monotonic(),
        "computer_started": _computer_started(),
    }


def automatic_restarts() -> int:
    """How many automatic restarts in a row this start is: the record the
    failed start before it left as it ended for a retry, taken so no later
    start counts it again. 0 for any other start."""
    record = status_folder.take_start_retry()
    if record is None:
        return 0
    try:
        restarts = int(record["restarts"])
        since = time.monotonic() - float(record["monotonic"])
        same_boot = abs(_computer_started() - float(record["computer_started"])) <= SAME_BOOT_SLACK_SECONDS
    except (KeyError, TypeError, ValueError):
        return 0
    if not same_boot or not 0 <= since <= RESTART_GAP_SECONDS:
        return 0
    return max(restarts, 0)


async def after_failure(engine, run_state, restarts: int) -> None:
    """What the start that just failed does next (this module's docstring):
    ``system.started`` reads False whichever stage stopped it
    (``Engine.start_failed``), the restart door is armed, and the splash page
    holds for good, or for ``HOLD_SECONDS`` before the process ends for a
    retry. ``restarts`` is ``automatic_restarts()`` as this start began. A
    stop during the hold cancels it, and nothing is recorded: the next start
    is one of its own. Logged, never raised."""
    try:
        engine.start_failed()
        restart.accept_restarts(engine, run_state)
        await _hold_or_retry(run_state, restarts)
    except Exception:
        log.exception("Could not settle what follows a failed start")


async def _hold_or_retry(run_state, restarts: int) -> None:
    if not restart.is_service_managed():
        run_state.hold()
        log.warning("Startup failed: nothing restarts this process, so it waits for Start again")
        return
    if restarts >= MAX_AUTOMATIC_RESTARTS:
        run_state.hold()
        log.warning(
            "Startup failed after %d automatic restarts in a row; waiting for Start again",
            restarts,
        )
        return
    run_state.retrying(HOLD_SECONDS)
    log.warning(
        "Startup failed; trying again in %d s (automatic restart %d of %d)",
        HOLD_SECONDS, restarts + 1, MAX_AUTOMATIC_RESTARTS,
    )
    await asyncio.sleep(HOLD_SECONDS)
    if restart.exit_to_retry():
        status_folder.write_start_retry(_retry_record(restarts + 1))


def start_again(run_state) -> restart.ExitAnswer:
    """Start again, pressed on the splash page of a start that failed: a
    restart through the door, so the next start makes a whole attempt with
    every retry ahead of it. Refused unless the start failed: a server that
    is starting, running or stopping is never restarted from here."""
    from openavc.middleware.startup_splash import FAILED, STOPPING

    if run_state is not None and run_state.phase == FAILED:
        return restart.request_exit(restart.DEFAULT_MODE, "start again")
    if run_state is not None and run_state.phase == STOPPING:
        refusal, sentence = restart.UNDER_WAY, "OpenAVC is already stopping."
    else:
        refusal, sentence = NOT_FAILED, "OpenAVC has not failed to start, so there is nothing to start again."
    log.warning("Start again refused: %s", sentence)
    return restart.ExitAnswer(
        accepted=False, mode=restart.DEFAULT_MODE, refusal=refusal, sentence=sentence,
    )
