"""Tests for StateStore."""



def test_get_set_basic(state):
    state.set("var.test", "hello")
    assert state.get("var.test") == "hello"


def test_get_default(state):
    assert state.get("nonexistent") is None
    assert state.get("nonexistent", 42) == 42


def test_set_all_types(state):
    state.set("a", "string")
    state.set("b", 42)
    state.set("c", 3.14)
    state.set("d", True)
    state.set("e", None)
    assert state.get("a") == "string"
    assert state.get("b") == 42
    assert state.get("c") == 3.14
    assert state.get("d") is True
    assert state.get("e") is None


def test_no_change_no_callback(state):
    state.set("var.x", 10)
    calls = []
    state.subscribe("var.*", lambda k, o, n, s: calls.append(n))
    state.set("var.x", 10)  # Same value
    assert len(calls) == 0


def test_subscribe_exact(state):
    calls = []
    state.subscribe("var.test", lambda k, o, n, s: calls.append((k, n)))
    state.set("var.test", "a")
    state.set("var.other", "b")  # Should not trigger
    assert len(calls) == 1
    assert calls[0] == ("var.test", "a")


def test_subscribe_glob(state):
    calls = []
    state.subscribe("device.proj1.*", lambda k, o, n, s: calls.append(k))
    state.set("device.proj1.power", "on")
    state.set("device.proj1.input", "hdmi1")
    state.set("device.proj2.power", "off")  # Different device
    assert len(calls) == 2


def test_subscribe_wildcard_all(state):
    calls = []
    state.subscribe("*", lambda k, o, n, s: calls.append(k))
    state.set("var.a", 1)
    state.set("device.b.c", 2)
    assert len(calls) == 2


def test_unsubscribe(state):
    calls = []
    sub_id = state.subscribe("var.*", lambda k, o, n, s: calls.append(k))
    state.set("var.a", 1)
    assert len(calls) == 1
    state.unsubscribe(sub_id)
    state.set("var.b", 2)
    assert len(calls) == 1  # No new call


def test_bulk_set(state):
    calls = []
    state.subscribe("var.*", lambda k, o, n, s: calls.append(k))
    state.bulk_set({"var.a": 1, "var.b": 2, "var.c": 3})
    assert len(calls) == 3
    assert state.get("var.a") == 1
    assert state.get("var.b") == 2
    assert state.get("var.c") == 3


def test_get_namespace(state):
    state.set("device.proj1.power", "on")
    state.set("device.proj1.input", "hdmi1")
    state.set("device.proj1.lamp_hours", 3200)
    state.set("device.proj2.power", "off")

    ns = state.get_namespace("device.proj1")
    assert ns == {"power": "on", "input": "hdmi1", "lamp_hours": 3200}


def test_get_matching(state):
    state.set("device.proj1.power", "on")
    state.set("device.proj2.power", "off")
    state.set("var.test", "x")

    result = state.get_matching("device.*.power")
    assert result == {"device.proj1.power": "on", "device.proj2.power": "off"}


def test_snapshot_is_copy(state):
    state.set("var.a", 1)
    snap = state.snapshot()
    snap["var.a"] = 999
    assert state.get("var.a") == 1  # Original unchanged


def test_history(state):
    state.set("var.a", 1)
    state.set("var.a", 2)
    state.set("var.b", "x")
    history = state.get_history(10)
    assert len(history) == 3
    assert history[0]["key"] == "var.a"
    assert history[0]["new_value"] == 1
    assert history[2]["key"] == "var.b"


def test_callback_exception_doesnt_break(state):
    """A bad callback shouldn't prevent state from being set."""
    def bad_callback(k, o, n, s):
        raise RuntimeError("boom")

    state.subscribe("var.*", bad_callback)
    state.set("var.a", 1)  # Should not raise
    assert state.get("var.a") == 1
