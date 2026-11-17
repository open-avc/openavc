"""One stop's stages against one deadline (``core/stop_stages.StopRun``)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from openavc.core import stop_stages
from openavc.core.stop_stages import EQUIPMENT_SECONDS, StopRun


@pytest.mark.asyncio
async def test_once_a_stage_is_left_at_its_bound_the_next_is_not_started(monkeypatch):
    # The event loop may fire a timeout up to one clock tick early (on Windows
    # a tick is about 15 ms), so the clock can still read a moment before the
    # bound when the stop moves on. A frozen clock just short of the bound is
    # that moment held still: the stage left at its bound has used it up.
    now = 1000.0
    monkeypatch.setattr(stop_stages, "time", SimpleNamespace(monotonic=lambda: now))
    run = StopRun(deadline=now + EQUIPMENT_SECONDS + 0.05)
    release = asyncio.Event()
    started: list[str] = []

    async def hangs():
        started.append("hangs")
        await release.wait()

    async def quick():
        started.append("quick")
        await asyncio.sleep(0)

    try:
        await run.run("the first stage", hangs)
        await run.run("the second stage", quick)
    finally:
        release.set()

    assert started == ["hangs"]
    assert run.problems == [
        "the first stage: did not finish in time and was left",
        "the second stage: not started, the stop ran out of time",
    ]


@pytest.mark.asyncio
async def test_the_equipment_stages_still_run_after_the_run_up_was_used_up(monkeypatch):
    now = 1000.0
    monkeypatch.setattr(stop_stages, "time", SimpleNamespace(monotonic=lambda: now))
    run = StopRun(deadline=now + EQUIPMENT_SECONDS + 0.05)
    release = asyncio.Event()
    started: list[str] = []

    async def hangs():
        await release.wait()

    async def lets_go():
        started.append("devices")

    try:
        await run.run("the triggers", hangs)
        run.hand_over()
        await run.run("the devices", lets_go)
    finally:
        release.set()

    assert started == ["devices"]
    assert run.problems == ["the triggers: did not finish in time and was left"]
