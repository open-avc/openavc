"""Tests for EventBus."""

import asyncio




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
