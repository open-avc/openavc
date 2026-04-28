"""Tests for Engine WS state batching: state.update vs state.delete."""

import pytest

from server.core.engine import Engine


@pytest.fixture
def engine(tmp_path):
    """Engine with no project loaded — only state subsystems wired up.

    Subscribes the engine's _on_state_change to the wildcard so calls into
    state.set() / state.delete() route into _state_batch / _state_deleted_keys
    just like they do at runtime, without needing the full start() lifecycle.
    """
    eng = Engine(str(tmp_path / "no_project.avc"))
    eng.state.subscribe("*", eng._on_state_change)
    return eng


@pytest.fixture
def captured_broadcasts(engine):
    """Replace engine.broadcast_ws with a capture; flushes go into a list."""
    sent: list[dict] = []

    async def fake_broadcast(msg):
        sent.append(msg)

    engine.broadcast_ws = fake_broadcast
    return sent


# --- Routing tests (sync; no flush required) ---


def test_set_routes_to_state_batch(engine):
    engine.state.set("var.x", 1)
    assert engine._state_batch == {"var.x": 1}
    assert engine._state_deleted_keys == set()


def test_delete_routes_to_deleted_keys(engine):
    engine.state.set("var.x", 1)
    engine._state_batch.clear()  # ignore the set we used for setup
    engine._state_deleted_keys.clear()
    engine.state.delete("var.x")
    assert engine._state_batch == {}
    assert engine._state_deleted_keys == {"var.x"}


def test_set_to_none_is_not_a_delete(engine):
    """Set-to-None is a legitimate value, not a delete."""
    engine.state.set("var.x", "hello")
    engine._state_batch.clear()
    engine._state_deleted_keys.clear()
    engine.state.set("var.x", None)
    assert engine._state_batch == {"var.x": None}
    assert engine._state_deleted_keys == set()


def test_delete_then_set_in_window_resolves_to_set(engine):
    """If a key is deleted then re-created within a flush window, the latest
    action wins: emit it in state.update, not state.delete."""
    engine.state.set("var.x", 1)
    engine._state_batch.clear()
    engine._state_deleted_keys.clear()
    engine.state.delete("var.x")
    engine.state.set("var.x", 2)
    assert engine._state_batch == {"var.x": 2}
    assert engine._state_deleted_keys == set()


def test_set_then_delete_in_window_resolves_to_delete(engine):
    """If a key is set then deleted within the same flush window, drop the
    pending set value and emit the delete."""
    engine.state.set("var.x", 1)
    engine._state_batch.clear()
    engine._state_deleted_keys.clear()
    engine.state.set("var.x", 2)
    engine.state.delete("var.x")
    assert engine._state_batch == {}
    assert engine._state_deleted_keys == {"var.x"}


# --- Flush tests (async) ---


@pytest.mark.asyncio
async def test_flush_emits_state_update_for_sets(engine, captured_broadcasts):
    engine.state.set("var.a", 1)
    engine.state.set("var.b", "hello")
    await engine._flush_state_batch()
    assert captured_broadcasts == [
        {"type": "state.update", "changes": {"var.a": 1, "var.b": "hello"}},
    ]


@pytest.mark.asyncio
async def test_flush_emits_state_delete_for_deletes(engine, captured_broadcasts):
    engine.state.set("var.a", 1)
    engine.state.set("var.b", 2)
    await engine._flush_state_batch()  # drain initial sets
    captured_broadcasts.clear()

    engine.state.delete("var.a")
    engine.state.delete("var.b")
    await engine._flush_state_batch()
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
    await engine._flush_state_batch()
    captured_broadcasts.clear()

    engine.state.set("var.new", 2)
    engine.state.delete("var.keep")

    await engine._flush_state_batch()
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
    await engine._flush_state_batch()
    assert engine._state_batch == {}
    assert engine._state_deleted_keys == set()


@pytest.mark.asyncio
async def test_flush_skips_when_buffers_empty(engine, captured_broadcasts):
    await engine._flush_state_batch()
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
    engine.add_ws_client(ws_all)
    engine.add_ws_client(ws_var_only, ns_prefixes=("var.",))

    await engine.broadcast_ws({
        "type": "state.delete",
        "keys": ["var.a", "device.proj1.power"],
    })

    assert sent_per_client["all"] == [
        {"type": "state.delete", "keys": ["var.a", "device.proj1.power"]},
    ]
    assert sent_per_client["var_only"] == [
        {"type": "state.delete", "keys": ["var.a"]},
    ]


@pytest.mark.asyncio
async def test_broadcast_state_delete_skips_client_with_no_matching_keys(engine):
    """Clients filtered to a namespace with no deletes get no message."""
    sent: list[dict] = []

    class FakeWS:
        async def send_text(self, text: str):
            import json
            sent.append(json.loads(text))

    ws = FakeWS()
    engine.add_ws_client(ws, ns_prefixes=("plugin.",))

    await engine.broadcast_ws({
        "type": "state.delete",
        "keys": ["var.a", "device.proj1.power"],
    })

    assert sent == []
