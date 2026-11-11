"""A transport hands its receive handler one message at a time, in the order
the messages arrived: a handler that awaits before it applies its message
(an acknowledgement sent back, a telnet refusal) keeps its place, and the
next message waits its turn instead of being applied first."""

from __future__ import annotations

import asyncio
from typing import Any, Callable

import pytest

from openavc.transport.handler_runs import HandlerRuns
from openavc.transport.serial_transport import SerialTransport
from openavc.transport.ssh import SSHTransport
from openavc.transport.tcp import TCPTransport
from openavc.transport.udp import UDPTransport


class PausingHandler:
    """Applies each message after awaiting once more for the first one than
    for the rest: the first message's await lets the second in."""

    def __init__(self) -> None:
        self.applied: list[bytes] = []

    async def __call__(self, data: bytes) -> None:
        for _ in range(5 if data == b"one" else 1):
            await asyncio.sleep(0)
        self.applied.append(data)


def _tcp(handler: Any) -> Callable[[bytes], None]:
    t = TCPTransport(
        "127.0.0.1", 9, on_data=handler, on_disconnect=lambda: None,
        delimiter=b"\r", timeout=1.0, inter_command_delay=0.0, name="acme_widget",
    )
    return t._deliver_message


def _serial(handler: Any) -> Callable[[bytes], None]:
    t = SerialTransport("COM9", 9600, on_data=handler, on_disconnect=lambda: None, name="acme_widget")
    return t._deliver_message


def _udp(handler: Any) -> Callable[[bytes], None]:
    t = UDPTransport("127.0.0.1", 9, on_data=handler, name="acme_widget")
    return lambda data: t._deliver_message(data, ("127.0.0.1", 9))


def _ssh(handler: Any) -> Callable[[bytes], None]:
    t = SSHTransport("127.0.0.1", 22, "acme", on_data=handler, on_disconnect=lambda: None, name="acme_widget")
    return t._deliver


@pytest.mark.parametrize("transport", [_tcp, _serial, _udp, _ssh], ids=["tcp", "serial", "udp", "ssh"])
async def test_a_handler_that_pauses_keeps_its_place(transport: Any) -> None:
    handler = PausingHandler()
    deliver = transport(handler)
    deliver(b"one")
    deliver(b"two")
    deliver(b"three")
    for _ in range(50):
        await asyncio.sleep(0)
    assert handler.applied == [b"one", b"two", b"three"]


async def test_a_handler_that_does_not_await_starts_on_the_same_turn_as_before() -> None:
    applied: list[bytes] = []

    async def handler(data: bytes) -> None:
        applied.append(data)

    runs = HandlerRuns(lambda task: None)
    runs.run(handler(b"one"))
    assert applied == []
    await asyncio.sleep(0)
    assert applied == [b"one"]


async def test_a_run_that_raises_does_not_hold_the_next() -> None:
    applied: list[bytes] = []
    failed: list[BaseException] = []

    async def handler(data: bytes) -> None:
        await asyncio.sleep(0)
        if data == b"bad":
            raise ValueError("unreadable")
        applied.append(data)

    def on_done(task: asyncio.Task[Any]) -> None:
        if not task.cancelled() and task.exception() is not None:
            failed.append(task.exception())

    runs = HandlerRuns(on_done)
    runs.run(handler(b"bad"))
    runs.run(handler(b"good"))
    for _ in range(20):
        await asyncio.sleep(0)
    assert applied == [b"good"]
    assert [str(e) for e in failed] == ["unreadable"]
    assert runs.tasks == set()


async def test_a_run_cancelled_while_it_waits_closes_its_handler_unstarted() -> None:
    started: list[bytes] = []
    release = asyncio.Event()

    async def handler(data: bytes) -> None:
        started.append(data)
        await release.wait()

    runs = HandlerRuns(lambda task: None)
    runs.run(handler(b"one"))
    second = handler(b"two")
    runs.run(second)
    await asyncio.sleep(0)
    waiting = [t for t in runs.tasks if t is runs._last]
    waiting[0].cancel()
    await asyncio.sleep(0)
    assert second.cr_frame is None  # closed, never started
    release.set()
    for _ in range(10):
        await asyncio.sleep(0)
    assert started == [b"one"]
    assert runs.tasks == set()


def test_a_handler_that_is_not_a_coroutine_is_left_alone() -> None:
    runs = HandlerRuns(lambda task: None)
    runs.run(None)
    assert runs.tasks == set()
