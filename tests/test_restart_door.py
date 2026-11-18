"""The restart door (``openavc.restart.request_exit``): what it accepts and
what it refuses, the same for every asker.

Each test arms the door with a stand-in engine and records the exit it would
start instead of running it; nothing here stops an engine or ends the process.
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import pytest

from openavc import restart


@pytest.fixture
def door(monkeypatch):
    """The door armed with an engine that is running, each accepted exit
    recorded as ``(wait_seconds, engine)`` and held open until the test
    lets it go (``release``), as a real exit is until the process ends."""
    engine = SimpleNamespace(stopping=False)
    seen = SimpleNamespace(engine=engine, exits=[], release=asyncio.Event())

    async def record(wait, engine_):
        seen.exits.append((wait, engine_))
        await seen.release.wait()

    monkeypatch.setattr(restart, "_exit_after", record)
    restart.accept_restarts(engine)
    yield seen
    seen.release.set()


async def _settle():
    for _ in range(3):
        await asyncio.sleep(0)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode, wait", [("graceful", 2), ("hard", 0), (None, 2)])
async def test_each_mode_starts_the_exit_after_its_wait(door, mode, wait):
    answer = restart.request_exit(mode, "api")
    await _settle()

    assert answer == restart.ExitAnswer(
        accepted=True, mode=mode or "graceful", wait_seconds=wait,
    )
    assert door.exits == [(wait, door.engine)]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["wild-west", "Hard", "", 7, ["hard"]])
async def test_a_mode_it_does_not_know_is_refused_and_nothing_starts(door, mode, caplog):
    with caplog.at_level(logging.WARNING, logger="openavc.main"):
        answer = restart.request_exit(mode, "cloud")
    await _settle()

    assert not answer.accepted
    assert answer.refusal == restart.UNKNOWN_MODE
    assert answer.sentence == f"{mode!r} is not a restart mode. Use graceful or hard."
    assert door.exits == []
    assert f"Restart refused (source=cloud, mode={mode!r}): {answer.sentence}" in caplog.messages


@pytest.mark.asyncio
async def test_a_second_request_while_one_is_under_way_is_refused(door):
    assert restart.request_exit("graceful", "api").accepted
    await _settle()

    second = restart.request_exit("hard", "cloud")

    assert (second.accepted, second.refusal) == (False, restart.UNDER_WAY)
    assert second.sentence == "A restart is already under way."
    assert door.exits == [(2, door.engine)]


@pytest.mark.asyncio
async def test_a_request_while_the_server_is_stopping_is_refused(door):
    door.engine.stopping = True

    answer = restart.request_exit("graceful", "listen")

    assert (answer.accepted, answer.refusal) == (False, restart.UNDER_WAY)
    assert answer.sentence == "The server is already stopping."
    assert door.exits == []


@pytest.mark.asyncio
async def test_a_request_before_the_door_is_armed_is_refused_and_a_later_one_is_not(monkeypatch):
    exits = []

    async def record(wait, engine):
        exits.append(wait)

    monkeypatch.setattr(restart, "_exit_after", record)

    early = restart.request_exit("graceful", "cloud")
    assert (early.accepted, early.refusal) == (False, restart.NOT_READY)
    assert early.sentence == "The server is still starting. Try the restart again once it is up."

    # The refusal holds nothing against the next request: once the server is
    # up, the same asker's retry restarts it at once.
    restart.accept_restarts(SimpleNamespace(stopping=False))
    assert restart.request_exit("graceful", "cloud").accepted
    await _settle()
    assert exits == [2]
