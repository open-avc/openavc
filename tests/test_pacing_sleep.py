"""sleep_at_least: a device's minimum gap is never cut short by the loop."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from openavc.transport import pacing


@pytest.mark.asyncio
async def test_a_sleep_that_ends_early_is_topped_up(monkeypatch: pytest.MonkeyPatch) -> None:
    """The event loop may end a sleep before its time (on Windows before
    Python 3.13, by up to a 15.6 ms clock step). Model a loop that always
    ends sleeps at half the time asked: the full wait must still pass."""
    real_sleep = asyncio.sleep
    asked: list[float] = []

    async def short_sleep(seconds: float) -> None:
        asked.append(seconds)
        await real_sleep(seconds / 2)

    monkeypatch.setattr(pacing.asyncio, "sleep", short_sleep)
    started = pacing.pacing_clock()
    await pacing.sleep_at_least(0.1)
    assert pacing.pacing_clock() - started >= 0.1
    assert len(asked) > 1


@pytest.mark.asyncio
async def test_nothing_to_wait_returns_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[float] = []

    async def record(seconds: float) -> None:
        calls.append(seconds)

    monkeypatch.setattr(pacing.asyncio, "sleep", record)
    await pacing.sleep_at_least(0)
    await pacing.sleep_at_least(-1)
    assert calls == []


def test_every_transport_paces_through_sleep_at_least() -> None:
    """A transport that sleeps its inter-command delay with a bare
    asyncio.sleep can cut a device's stated minimum short on Windows."""
    transport_dir = Path(pacing.__file__).parent
    bare = re.compile(r"asyncio\.sleep\([^)]*inter_command_delay")
    offenders = [
        f"{path.name}:{number}"
        for path in sorted(transport_dir.glob("*.py"))
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if bare.search(line)
    ]
    assert offenders == []
    paced = [
        path.name
        for path in sorted(transport_dir.glob("*.py"))
        if "sleep_at_least(self._inter_command_delay)" in path.read_text(encoding="utf-8")
    ]
    assert paced == ["serial_transport.py", "ssh.py", "tcp.py", "udp.py"]
