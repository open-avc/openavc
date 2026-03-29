"""Tests for Scheduler."""

import asyncio
from datetime import datetime

import pytest

from server.core.event_bus import EventBus
from server.core.scheduler import CronJob, Scheduler
from server.core import script_api


@pytest.fixture
def events():
    return EventBus()


@pytest.fixture
async def scheduler(events):
    sched = Scheduler(events)
    yield sched
    # Ensure background task is cleaned up even if test fails
    await sched.stop()


# --- Tests ---


async def test_load_schedules(scheduler):
    """load_schedules populates the jobs dict."""
    scheduler.load_schedules([
        {
            "id": "morning",
            "type": "cron",
            "expression": "0 8 * * *",
            "event": "schedule.morning",
            "enabled": True,
        },
        {
            "id": "evening",
            "type": "cron",
            "expression": "0 18 * * *",
            "event": "schedule.evening",
            "enabled": False,
        },
    ])

    schedules = scheduler.list_schedules()
    assert len(schedules) == 2
    assert schedules[0]["id"] == "morning"
    assert schedules[1]["enabled"] is False


async def test_load_clears_previous(scheduler):
    """Loading schedules replaces previous ones."""
    scheduler.load_schedules([{"id": "a", "expression": "* * * * *", "event": "e.a"}])
    assert len(scheduler.list_schedules()) == 1

    scheduler.load_schedules([{"id": "b", "expression": "* * * * *", "event": "e.b"}])
    assert len(scheduler.list_schedules()) == 1
    assert scheduler.list_schedules()[0]["id"] == "b"


async def test_start_stop(scheduler):
    """Scheduler starts and stops without error."""
    scheduler.load_schedules([
        {"id": "test", "expression": "* * * * *", "event": "schedule.test"},
    ])
    await scheduler.start()
    assert scheduler._task is not None
    assert not scheduler._task.done()

    await scheduler.stop()
    assert scheduler._task is None


async def test_start_no_jobs(scheduler):
    """Scheduler doesn't start a task when there are no jobs."""
    await scheduler.start()
    assert scheduler._task is None


async def test_should_fire_matching():
    """_should_fire returns True when cron matches current time."""
    pytest.importorskip("croniter")
    # Expression "* * * * *" matches every minute
    job = CronJob(id="t", expression="* * * * *", event="e", enabled=True)
    now = datetime(2026, 3, 15, 10, 30, 5)
    assert Scheduler._should_fire(job, now) is True


async def test_should_fire_no_double():
    """_should_fire returns False if already fired for this match."""
    pytest.importorskip("croniter")
    job = CronJob(id="t", expression="* * * * *", event="e", enabled=True)
    now = datetime(2026, 3, 15, 10, 30, 5)
    job.last_fire = now  # Already fired
    assert Scheduler._should_fire(job, now) is False


async def test_should_fire_non_matching():
    """_should_fire returns False when cron doesn't match recent time."""
    pytest.importorskip("croniter")
    # Expression for midnight only — should not match 10:30
    job = CronJob(id="t", expression="0 0 * * *", event="e", enabled=True)
    now = datetime(2026, 3, 15, 10, 30, 5)
    assert Scheduler._should_fire(job, now) is False


async def test_list_schedules_empty(scheduler):
    """list_schedules returns empty list when nothing loaded."""
    assert scheduler.list_schedules() == []


# --- Timer function tests (from script_api) ---


async def test_after_timer():
    """script_api.after() runs callback after delay."""
    results = []
    script_api.after(0.05, lambda: results.append("fired"))
    assert results == []
    await asyncio.sleep(0.15)
    assert results == ["fired"]


async def test_every_timer():
    """script_api.every() runs callback repeatedly."""
    results = []
    timer_id = script_api.every(0.05, lambda: results.append("tick"))
    await asyncio.sleep(0.18)
    script_api.cancel_timer(timer_id)
    assert len(results) >= 2


async def test_cancel_timer():
    """cancel_timer stops a pending timer."""
    results = []
    timer_id = script_api.after(0.1, lambda: results.append("nope"))
    assert script_api.cancel_timer(timer_id) is True
    await asyncio.sleep(0.15)
    assert results == []


async def test_cancel_nonexistent():
    """cancel_timer returns False for unknown ID."""
    assert script_api.cancel_timer("no_such_timer") is False
