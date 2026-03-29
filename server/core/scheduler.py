"""
OpenAVC Scheduler — cron schedules from project.avc.

.. deprecated::
    The ``schedules`` section in project.avc is a legacy mechanism.
    New projects should use **trigger-based schedules** (type ``"schedule"``
    on a macro trigger) instead, which support guard conditions, cooldown,
    debounce, and overlap control.  See ``docs/scheduling-guide.md`` for
    migration guidance.

Two mechanisms:
1. Cron jobs — background loop checks every 30s, emits events when
   cron expressions match the current time.
2. Dynamic timers — handled by script_api (after/every/cancel_timer).

Requires `croniter` (MIT, pure Python) for cron expression parsing.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, TYPE_CHECKING

from server.utils.logger import get_logger

if TYPE_CHECKING:
    from server.core.event_bus import EventBus

log = get_logger(__name__)

try:
    from croniter import croniter

    HAS_CRONITER = True
except ImportError:
    HAS_CRONITER = False
    log.warning("croniter not installed — cron schedules disabled")


class CronJob:
    """A single cron schedule entry."""

    __slots__ = ("id", "expression", "event", "enabled", "description", "last_fire")

    def __init__(
        self,
        id: str,
        expression: str,
        event: str,
        enabled: bool = True,
        description: str = "",
    ):
        self.id = id
        self.expression = expression
        self.event = event
        self.enabled = enabled
        self.description = description
        self.last_fire: datetime | None = None


class Scheduler:
    """Manages cron-based scheduled events."""

    def __init__(self, event_bus: EventBus):
        self.events = event_bus
        self._jobs: dict[str, CronJob] = {}
        self._task: asyncio.Task | None = None

    def load_schedules(self, schedules: list[dict[str, Any]]) -> None:
        """Load schedule definitions from project config."""
        self._jobs.clear()
        for s in schedules:
            if s.get("type", "cron") != "cron":
                continue
            job = CronJob(
                id=s["id"],
                expression=s.get("expression", ""),
                event=s.get("event", f"schedule.{s['id']}"),
                enabled=s.get("enabled", True),
                description=s.get("description", ""),
            )
            self._jobs[job.id] = job
        if self._jobs:
            log.info(f"Loaded {len(self._jobs)} cron schedule(s)")

    async def start(self) -> None:
        """Start the cron check loop."""
        if not self._jobs:
            return
        if not HAS_CRONITER:
            log.warning("Cannot start scheduler — croniter not installed")
            return
        self._task = asyncio.create_task(self._cron_loop())
        log.info("Scheduler started")

    async def stop(self) -> None:
        """Stop the cron check loop."""
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        log.info("Scheduler stopped")

    async def _cron_loop(self) -> None:
        """Check cron expressions every 15 seconds."""
        try:
            while True:
                await asyncio.sleep(15)
                now = datetime.now()
                for job in self._jobs.values():
                    if not job.enabled or not job.expression:
                        continue
                    try:
                        if self._should_fire(job, now):
                            job.last_fire = now
                            log.info(f"Cron fired: {job.id} -> {job.event}")
                            await self.events.emit(
                                job.event,
                                {"schedule_id": job.id, "expression": job.expression},
                            )
                    except Exception:  # Catch-all: isolates individual cron job failures
                        log.exception(f"Error checking cron job '{job.id}'")
        except asyncio.CancelledError:
            return

    @staticmethod
    def _should_fire(job: CronJob, now: datetime) -> bool:
        """Check if a cron expression matches the current minute."""
        if not HAS_CRONITER:
            return False
        # Get the most recent match time
        cron = croniter(job.expression, now)
        prev = cron.get_prev(datetime)
        # Fire if the previous match is within the last 60 seconds (one cron period)
        # and we haven't already fired for this match
        delta = (now - prev).total_seconds()
        if delta > 60:
            return False
        if job.last_fire and (job.last_fire - prev).total_seconds() >= 0:
            return False  # Already fired for this match
        return True

    def list_schedules(self) -> list[dict[str, Any]]:
        """Return info about all loaded schedules."""
        return [
            {
                "id": job.id,
                "expression": job.expression,
                "event": job.event,
                "enabled": job.enabled,
                "description": job.description,
                "last_fire": job.last_fire.isoformat() if job.last_fire else None,
            }
            for job in self._jobs.values()
        ]
