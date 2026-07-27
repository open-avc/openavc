"""Tests for WSHub state batching: state.update vs state.delete."""

import asyncio

import pytest

from server.core.engine import Engine


@pytest.fixture
def engine(tmp_path):
    """Engine with no project loaded — only state subsystems wired up.

    Subscribes the WS hub's on_state_change to the wildcard so calls into
    state.set() / state.delete() route into _state_batch / _state_deleted_keys
    just like they do at runtime, without needing the full start() lifecycle.
    """
    eng = Engine(str(tmp_path / "no_project.avc"))
    eng.state.subscribe("*", eng.ws.on_state_change)
    return eng


@pytest.fixture
def captured_broadcasts(engine):
    """Replace the hub's broadcast with a capture; flushes go into a list."""
    sent: list[dict] = []

    async def fake_broadcast(msg):
        sent.append(msg)

    engine.ws.broadcast = fake_broadcast
    return sent


# --- Routing tests (sync; no flush required) ---


def test_set_routes_to_state_batch(engine):
    engine.state.set("var.x", 1)
    assert engine.ws._state_batch == {"var.x": 1}
    assert engine.ws._state_deleted_keys == set()


def test_delete_routes_to_deleted_keys(engine):
    engine.state.set("var.x", 1)
    engine.ws._state_batch.clear()  # ignore the set we used for setup
    engine.ws._state_deleted_keys.clear()
    engine.state.delete("var.x")
    assert engine.ws._state_batch == {}
    assert engine.ws._state_deleted_keys == {"var.x"}


def test_set_to_none_is_not_a_delete(engine):
    """Set-to-None is a legitimate value, not a delete."""
    engine.state.set("var.x", "hello")
    engine.ws._state_batch.clear()
    engine.ws._state_deleted_keys.clear()
    engine.state.set("var.x", None)
    assert engine.ws._state_batch == {"var.x": None}
    assert engine.ws._state_deleted_keys == set()


def test_delete_then_set_in_window_resolves_to_set(engine):
    """If a key is deleted then re-created within a flush window, the latest
    action wins: emit it in state.update, not state.delete."""
    engine.state.set("var.x", 1)
    engine.ws._state_batch.clear()
    engine.ws._state_deleted_keys.clear()
    engine.state.delete("var.x")
    engine.state.set("var.x", 2)
    assert engine.ws._state_batch == {"var.x": 2}
    assert engine.ws._state_deleted_keys == set()


def test_set_then_delete_in_window_resolves_to_delete(engine):
    """If a key is set then deleted within the same flush window, drop the
    pending set value and emit the delete."""
    engine.state.set("var.x", 1)
    engine.ws._state_batch.clear()
    engine.ws._state_deleted_keys.clear()
    engine.state.set("var.x", 2)
    engine.state.delete("var.x")
    assert engine.ws._state_batch == {}
    assert engine.ws._state_deleted_keys == {"var.x"}


# --- Flush tests (async) ---


@pytest.mark.asyncio
async def test_flush_emits_state_update_for_sets(engine, captured_broadcasts):
    engine.state.set("var.a", 1)
    engine.state.set("var.b", "hello")
    await engine.ws.flush_state_batch()
    assert captured_broadcasts == [
        {"type": "state.update", "changes": {"var.a": 1, "var.b": "hello"}},
    ]


@pytest.mark.asyncio
async def test_flush_emits_state_delete_for_deletes(engine, captured_broadcasts):
    engine.state.set("var.a", 1)
    engine.state.set("var.b", 2)
    await engine.ws.flush_state_batch()  # drain initial sets
    captured_broadcasts.clear()

    engine.state.delete("var.a")
    engine.state.delete("var.b")
    await engine.ws.flush_state_batch()
    assert len(captured_broadcasts) == 1
    assert captured_broadcasts[0] == {
        "type": "state.delete",
        "keys": ["var.a", "var.b"],
    }


@pytest.mark.asyncio
async def test_flush_emits_both_when_window_has_sets_and_deletes(
    engine, captured_broadcasts
):
    """A window that mixes sets and deletes produces two messages."""
    engine.state.set("var.keep", 1)
    await engine.ws.flush_state_batch()
    captured_broadcasts.clear()

    engine.state.set("var.new", 2)
    engine.state.delete("var.keep")

    await engine.ws.flush_state_batch()
    types = [m["type"] for m in captured_broadcasts]
    assert "state.update" in types
    assert "state.delete" in types
    update_msg = next(m for m in captured_broadcasts if m["type"] == "state.update")
    delete_msg = next(m for m in captured_broadcasts if m["type"] == "state.delete")
    assert update_msg["changes"] == {"var.new": 2}
    assert delete_msg["keys"] == ["var.keep"]


@pytest.mark.asyncio
async def test_flush_clears_buffers(engine, captured_broadcasts):
    engine.state.set("var.a", 1)
    engine.state.set("var.b", 2)
    engine.state.delete("var.b")  # var.b is set then deleted; ends up in deletes
    await engine.ws.flush_state_batch()
    assert engine.ws._state_batch == {}
    assert engine.ws._state_deleted_keys == set()


@pytest.mark.asyncio
async def test_flush_skips_when_buffers_empty(engine, captured_broadcasts):
    await engine.ws.flush_state_batch()
    assert captured_broadcasts == []


# --- Namespace filtering for state.delete ---


@pytest.mark.asyncio
async def test_broadcast_state_delete_filters_per_client_namespace(engine):
    """A client subscribed to a namespace should only see deletes within it."""
    sent_per_client: dict[str, list[dict]] = {"all": [], "var_only": []}

    class FakeWS:
        def __init__(self, label: str):
            self.label = label

        async def send_text(self, text: str):
            import json
            sent_per_client[self.label].append(json.loads(text))

    ws_all = FakeWS("all")
    ws_var_only = FakeWS("var_only")
    engine.ws.add_client(ws_all)
    engine.ws.add_client(ws_var_only, ns_prefixes=("var.",))

    await engine.ws.broadcast({
        "type": "state.delete",
        "keys": ["var.a", "device.proj1.power"],
    })
    await engine.ws.flush_sends()

    assert sent_per_client["all"] == [
        {"type": "state.delete", "keys": ["var.a", "device.proj1.power"]},
    ]
    assert sent_per_client["var_only"] == [
        {"type": "state.delete", "keys": ["var.a"]},
    ]
    engine.ws.remove_client(ws_all)
    engine.ws.remove_client(ws_var_only)


@pytest.mark.asyncio
async def test_broadcast_state_delete_skips_client_with_no_matching_keys(engine):
    """Clients filtered to a namespace with no deletes get no message."""
    sent: list[dict] = []

    class FakeWS:
        async def send_text(self, text: str):
            import json
            sent.append(json.loads(text))

    ws = FakeWS()
    engine.ws.add_client(ws, ns_prefixes=("plugin.",))

    await engine.ws.broadcast({
        "type": "state.delete",
        "keys": ["var.a", "device.proj1.power"],
    })
    await engine.ws.flush_sends()

    assert sent == []
    engine.ws.remove_client(ws)


# --- Disconnect handling: a failing send removes the client ---


@pytest.mark.asyncio
async def test_broadcast_fast_path_drops_failing_client(engine):
    """Fast-path send failure drops the client; surviving clients still receive."""
    received_ok: list[dict] = []

    class GoodWS:
        async def send_text(self, text: str):
            import json
            received_ok.append(json.loads(text))

    class BadWS:
        async def send_text(self, text: str):
            raise RuntimeError("connection closed")

    good = GoodWS()
    bad = BadWS()
    engine.ws.add_client(good)
    engine.ws.add_client(bad)
    assert bad in engine.ws._clients

    await engine.ws.broadcast({"type": "ping"})
    await engine.ws.flush_sends()

    assert received_ok == [{"type": "ping"}]
    assert bad not in engine.ws._clients
    assert good in engine.ws._clients
    engine.ws.remove_client(good)


@pytest.mark.asyncio
async def test_broadcast_slow_path_drops_failing_client(engine):
    """Slow-path (filtered) send failure drops the client."""
    received_ok: list[dict] = []

    class GoodWS:
        async def send_text(self, text: str):
            import json
            received_ok.append(json.loads(text))

    class BadWS:
        async def send_text(self, text: str):
            raise RuntimeError("connection closed")

    good = GoodWS()
    bad = BadWS()
    # Both subscribed to var.* so the slow path is exercised on both
    engine.ws.add_client(good, ns_prefixes=("var.",))
    engine.ws.add_client(bad, ns_prefixes=("var.",))
    assert bad in engine.ws._clients

    await engine.ws.broadcast({
        "type": "state.delete",
        "keys": ["var.a"],
    })
    await engine.ws.flush_sends()

    assert received_ok == [{"type": "state.delete", "keys": ["var.a"]}]
    assert bad not in engine.ws._clients
    assert id(bad) not in engine.ws._ns_filters
    assert good in engine.ws._clients
    engine.ws.remove_client(good)


# --- Slow-client isolation: a wedged client must not stall the emit path ---


class _StuckWS:
    """A client whose send never completes (dead peer with an open TCP pipe)."""

    def __init__(self):
        self.closed = False
        self._never = asyncio.Event()

    async def send_text(self, text: str):
        await self._never.wait()

    async def close(self, code: int = 1000):
        self.closed = True


class _CollectingWS:
    def __init__(self):
        self.received: list[dict] = []

    async def send_text(self, text: str):
        import json
        self.received.append(json.loads(text))


@pytest.mark.asyncio
async def test_broadcast_returns_promptly_with_stuck_client(engine):
    """broadcast must not await the wedged client's send (V-LC-001)."""
    stuck = _StuckWS()
    good = _CollectingWS()
    engine.ws.add_client(stuck)
    engine.ws.add_client(good)

    # Pre-fix this hung until the stuck client's send completed (never).
    await asyncio.wait_for(engine.ws.broadcast({"type": "ping"}), timeout=0.5)

    # The healthy client still gets the message even while the other is stuck.
    for _ in range(50):
        if good.received:
            break
        await asyncio.sleep(0.01)
    assert good.received == [{"type": "ping"}]
    engine.ws.remove_client(stuck)
    engine.ws.remove_client(good)


@pytest.mark.asyncio
async def test_stuck_client_dropped_on_queue_overflow(engine):
    """A client whose send queue overflows is dropped and its socket closed."""
    from server.core.ws_hub import _WS_SEND_QUEUE_MAX

    stuck = _StuckWS()
    good = _CollectingWS()
    engine.ws.add_client(stuck)
    engine.ws.add_client(good)

    # One message is in-flight in the writer, _WS_SEND_QUEUE_MAX fill the
    # queue, and the next one overflows. Yield between broadcasts so the
    # healthy client's writer keeps draining (as any real emit path does) —
    # only the stuck client's queue may fill.
    for _ in range(_WS_SEND_QUEUE_MAX + 2):
        await engine.ws.broadcast({"type": "ping"})
        await asyncio.sleep(0)

    assert stuck not in engine.ws._clients
    assert id(stuck) not in engine.ws._send_queues
    assert good in engine.ws._clients

    # The overflow close is fire-and-forget; let it run.
    for _ in range(50):
        if stuck.closed:
            break
        await asyncio.sleep(0.01)
    assert stuck.closed

    await engine.ws.flush_sends()
    assert len(good.received) == _WS_SEND_QUEUE_MAX + 2
    engine.ws.remove_client(good)


@pytest.mark.asyncio
async def test_deferred_delivery_buffers_until_ready(engine):
    """defer_delivery buffers broadcasts until mark_ws_client_ready — the
    handshake registers before snapshotting so no flush window is missed
    (V-LC-005)."""
    ws = _CollectingWS()
    engine.ws.add_client(ws, defer_delivery=True)

    await engine.ws.broadcast({"type": "ping", "seq": 1})
    await asyncio.sleep(0.05)
    assert ws.received == []  # buffered, not delivered

    engine.ws.mark_client_ready(ws)
    await engine.ws.flush_sends()
    assert ws.received == [{"type": "ping", "seq": 1}]
    engine.ws.remove_client(ws)


@pytest.mark.asyncio
async def test_per_client_delivery_preserves_order(engine):
    """Messages reach a client in broadcast order through its queue."""
    ws = _CollectingWS()
    engine.ws.add_client(ws)
    for i in range(10):
        await engine.ws.broadcast({"type": "ping", "seq": i})
    await engine.ws.flush_sends()
    assert [m["seq"] for m in ws.received] == list(range(10))
    engine.ws.remove_client(ws)
