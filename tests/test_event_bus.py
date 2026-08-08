"""Tests for EventBus."""

import asyncio

from openavc.core import event_bus




async def test_basic_emit_on(events):
    results = []

    async def handler(event, payload):
        results.append((event, payload))

    events.on("test.event", handler)
    await events.emit("test.event", {"key": "value"})

    assert len(results) == 1
    assert results[0] == ("test.event", {"key": "value"})


async def test_glob_pattern(events):
    results = []
    events.on("device.connected.*", lambda e, p: results.append(e))

    await events.emit("device.connected.proj1")
    await events.emit("device.connected.switcher1")
    await events.emit("device.disconnected.proj1")  # Should not match

    assert len(results) == 2
    assert "device.connected.proj1" in results
    assert "device.connected.switcher1" in results


async def test_wildcard_all(events):
    results = []
    events.on("*", lambda e, p: results.append(e))

    await events.emit("a")
    await events.emit("b.c")
    await events.emit("d.e.f")

    assert len(results) == 3


async def test_once(events):
    results = []
    events.once("test.once", lambda e, p: results.append(e))

    await events.emit("test.once")
    await events.emit("test.once")

    assert len(results) == 1


async def test_off(events):
    results = []
    handler_id = events.on("test.off", lambda e, p: results.append(e))

    await events.emit("test.off")
    assert len(results) == 1

    events.off(handler_id)
    await events.emit("test.off")
    assert len(results) == 1  # No new call


async def test_multiple_handlers(events):
    results = []
    events.on("test.multi", lambda e, p: results.append("a"))
    events.on("test.multi", lambda e, p: results.append("b"))

    await events.emit("test.multi")
    assert sorted(results) == ["a", "b"]


async def test_handler_exception_doesnt_kill_others(events):
    results = []

    def bad_handler(e, p):
        raise RuntimeError("boom")

    events.on("test.err", bad_handler)
    events.on("test.err", lambda e, p: results.append("ok"))

    await events.emit("test.err")  # Should not raise
    assert results == ["ok"]


async def test_emit_no_handlers(events):
    # Should be a silent no-op
    await events.emit("nobody.listens")


async def test_handler_count(events):
    assert events.handler_count() == 0
    events.on("a", lambda e, p: None)
    events.on("b", lambda e, p: None)
    assert events.handler_count() == 2


async def test_async_handler(events):
    results = []

    async def async_handler(event, payload):
        await asyncio.sleep(0.01)
        results.append(event)

    events.on("test.async", async_handler)
    await events.emit("test.async")
    assert results == ["test.async"]


async def test_payload_defaults_to_empty_dict(events):
    payloads = []
    events.on("test.default", lambda e, p: payloads.append(p))
    await events.emit("test.default")
    assert payloads == [{}]


async def test_concurrent_emits_not_dropped_by_depth_guard(events):
    """High-fan-out CONCURRENT emits (more than MAX_EMIT_DEPTH) whose handlers
    await must all be delivered. The depth guard bounds recursion per chain, not
    how many independent emits are in flight at once — the old shared counter
    conflated the two and silently dropped legitimate events."""
    received = []

    async def handler(event, payload):
        # Await so every emit holds its depth bump across this window at once,
        # which is exactly when the old shared counter over-counted.
        await asyncio.sleep(0.01)
        received.append(event)

    events.on("fan.*", handler)

    n = events.MAX_EMIT_DEPTH + 4
    await asyncio.gather(*[events.emit(f"fan.{i}") for i in range(n)])

    assert sorted(received) == sorted(f"fan.{i}" for i in range(n))


async def test_recursive_emit_chain_still_bounded(events):
    """A genuine recursive chain (a handler that re-emits the same event) is
    still capped at MAX_EMIT_DEPTH so runaway recursion can't spin forever."""
    fires = []

    async def recursive(event, payload):
        fires.append(event)
        await events.emit("recurse")  # re-emit -> recursion

    events.on("recurse", recursive)
    await events.emit("recurse")

    # Handler fires once per depth level up to the cap, then the next emit drops.
    assert len(fires) == events.MAX_EMIT_DEPTH


async def test_once_does_not_double_fire_under_concurrent_emit(events):
    """A once-handler must fire exactly once even when the same event is emitted
    concurrently — the handler is unregistered before the await, so a second
    concurrent emit can't find it still registered and fire it again."""
    fires = []

    async def once_handler(event, payload):
        await asyncio.sleep(0.01)  # keep the first emit awaiting while the 2nd runs
        fires.append(event)

    events.once("test.once.race", once_handler)

    await asyncio.gather(
        events.emit("test.once.race"),
        events.emit("test.once.race"),
    )

    assert fires == ["test.once.race"]


async def test_a_long_lived_loop_spawned_from_a_handler_ratchets_the_depth(events):
    """The mechanism behind the bug detach_emit_chain exists to fix.

    ``create_task`` copies the context, which is what makes the depth counter
    measure recursion rather than breadth. But a task that OUTLIVES the chain
    that spawned it keeps the inherited depth forever, so each cycle of
    "handler spawns loop, loop emits, that emit's handler spawns the next loop"
    lands one level deeper — until the guard drops the events entirely.

    Pinned as a characteristic so the fix below is measured against it rather
    than asserted.
    """
    depths: list[int] = []

    async def spawn_next(event, payload):
        cycle = payload["cycle"]
        if cycle >= events.MAX_EMIT_DEPTH + 2:
            return

        async def loop() -> None:
            # A device-lifetime loop, standing in for the reconnect/health loop:
            # spawned inside this handler, then emitting on its own schedule.
            depths.append(event_bus._emit_depth.get())
            await events.emit("ratchet", {"cycle": cycle + 1})

        await asyncio.create_task(loop())

    events.on("ratchet", spawn_next)
    await events.emit("ratchet", {"cycle": 0})

    # Each spawned loop starts one level deeper than the last.
    assert depths[:3] == [1, 2, 3]
    # And it stops early: once the inherited depth hits the cap the emits are
    # dropped, so the chain never reaches the cycle it was told to run to.
    assert len(depths) < events.MAX_EMIT_DEPTH + 2


async def test_detach_emit_chain_stops_the_ratchet(events):
    """Same shape, with the loop declaring itself a root.

    This is what keeps a flapping device reporting its connection state: the
    watchdog drops the link from inside an emit chain, the reconnect loop a
    handler spawns detaches, and its own disconnect/connect events are charged
    to a fresh chain instead of the one that started it.
    """
    depths: list[int] = []
    cycles = events.MAX_EMIT_DEPTH + 3

    async def spawn_next(event, payload):
        cycle = payload["cycle"]
        if cycle >= cycles:
            return

        async def loop() -> None:
            event_bus.detach_emit_chain()
            depths.append(event_bus._emit_depth.get())
            await events.emit("detached", {"cycle": cycle + 1})

        await asyncio.create_task(loop())

    events.on("detached", spawn_next)
    await events.emit("detached", {"cycle": 0})

    # Every loop starts from zero, and no cycle is lost to the depth guard.
    assert depths == [0] * cycles


async def test_detach_only_affects_its_own_task(events):
    """A detach inside one task must not clear the depth of the chain it was
    spawned from — that chain still needs its recursion bound."""
    inner_depth: list[int] = []

    async def detacher() -> None:
        event_bus.detach_emit_chain()

    async def handler(event, payload):
        await asyncio.create_task(detacher())
        inner_depth.append(event_bus._emit_depth.get())

    events.on("scoped", handler)
    await events.emit("scoped")

    assert inner_depth == [1]
