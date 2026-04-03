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


# --- Variable binding tests ---


def test_variable_config_source_fields():
    """VariableConfig accepts optional source_key and source_map."""
    from server.core.project_loader import VariableConfig

    var = VariableConfig(id="test", type="string", default="")
    assert var.source_key is None
    assert var.source_map is None

    var2 = VariableConfig(
        id="status",
        type="string",
        default="Unknown",
        source_key="device.projector.power",
        source_map={"on": "Ready", "off": "Off", "warming": "Warming Up"},
    )
    assert var2.source_key == "device.projector.power"
    assert var2.source_map == {"on": "Ready", "off": "Off", "warming": "Warming Up"}


def test_variable_config_backward_compat():
    """Existing variables without source fields load fine."""
    from server.core.project_loader import VariableConfig

    var = VariableConfig.model_validate({
        "id": "room_active",
        "type": "boolean",
        "default": False,
        "label": "Room Active",
        "dashboard": True,
    })
    assert var.source_key is None
    assert var.source_map is None


def test_state_subscribe_and_binding(state):
    """State change on source_key triggers variable update via subscription."""
    source_key = "device.projector.power"
    var_key = "var.projector_status"
    source_map = {"on": "Ready", "off": "Off", "warming": "Warming Up"}

    state.set(var_key, "Unknown", source="system")
    state.set(source_key, "off", source="device")

    captured = []

    def handler(key, old_value, new_value, source):
        if source == "variable_binding":
            return
        mapped = source_map.get(str(new_value), new_value)
        state.set(var_key, mapped, source="variable_binding")
        captured.append(mapped)

    state.subscribe(source_key, handler)

    state.set(source_key, "on", source="device")
    assert state.get(var_key) == "Ready"
    assert captured == ["Ready"]

    state.set(source_key, "warming", source="device")
    assert state.get(var_key) == "Warming Up"

    # Unmapped value falls through
    state.set(source_key, "cooling", source="device")
    assert state.get(var_key) == "cooling"


def test_state_binding_no_map(state):
    """Without source_map, raw value is passed through."""
    source_key = "device.dsp.level"
    var_key = "var.volume"

    state.set(var_key, 0, source="system")
    state.set(source_key, 0, source="device")

    def handler(key, old_value, new_value, source):
        if source == "variable_binding":
            return
        state.set(var_key, new_value, source="variable_binding")

    state.subscribe(source_key, handler)

    state.set(source_key, 75, source="device")
    assert state.get(var_key) == 75

    state.set(source_key, 100, source="device")
    assert state.get(var_key) == 100


def test_state_binding_no_loop(state):
    """Variable binding source='variable_binding' should not cause infinite loop."""
    source_key = "device.projector.power"
    var_key = "var.status"
    call_count = 0

    def handler(key, old_value, new_value, source):
        nonlocal call_count
        if source == "variable_binding":
            return
        call_count += 1
        state.set(var_key, new_value, source="variable_binding")

    state.subscribe(source_key, handler)
    state.set(source_key, "on", source="device")

    assert call_count == 1
    assert state.get(var_key) == "on"
