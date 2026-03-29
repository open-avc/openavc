"""Tests for variable-to-state binding (source_key + source_map)."""

from server.core.state_store import StateStore
from server.core.project_loader import VariableConfig


def test_variable_config_source_fields():
    """VariableConfig accepts optional source_key and source_map."""
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
    var = VariableConfig.model_validate({
        "id": "room_active",
        "type": "boolean",
        "default": False,
        "label": "Room Active",
        "dashboard": True,
    })
    assert var.source_key is None
    assert var.source_map is None


def test_state_subscribe_and_binding():
    """State change on source_key triggers variable update via subscription."""
    state = StateStore()

    # Simulate what the engine does for variable binding
    source_key = "device.projector.power"
    var_key = "var.projector_status"
    source_map = {"on": "Ready", "off": "Off", "warming": "Warming Up"}

    # Set initial values
    state.set(var_key, "Unknown", source="system")
    state.set(source_key, "off", source="device")

    # Subscribe
    captured = []

    def handler(key, old_value, new_value, source):
        if source == "variable_binding":
            return
        mapped = source_map.get(str(new_value), new_value)
        state.set(var_key, mapped, source="variable_binding")
        captured.append(mapped)

    state.subscribe(source_key, handler)

    # Trigger a change
    state.set(source_key, "on", source="device")
    assert state.get(var_key) == "Ready"
    assert captured == ["Ready"]

    # Another change
    state.set(source_key, "warming", source="device")
    assert state.get(var_key) == "Warming Up"

    # Unmapped value falls through
    state.set(source_key, "cooling", source="device")
    assert state.get(var_key) == "cooling"


def test_state_binding_no_map():
    """Without source_map, raw value is passed through."""
    state = StateStore()

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


def test_state_binding_no_loop():
    """Variable binding source='variable_binding' should not cause infinite loop."""
    state = StateStore()

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

    # Handler should only fire once (not loop)
    assert call_count == 1
    assert state.get(var_key) == "on"
