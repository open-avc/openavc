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


# --- delete() tests ---


def test_delete_removes_key(state):
    state.set("var.a", 1)
    assert state.get("var.a") == 1
    state.delete("var.a")
    assert state.get("var.a") is None
    # Key should be absent from snapshot, not just present-with-None
    assert "var.a" not in state.snapshot()


def test_delete_unknown_key_is_noop(state):
    """Deleting a missing key should not raise or fire listeners."""
    calls = []
    state.subscribe("*", lambda k, o, n, s: calls.append(k))
    state.delete("var.never_set")
    assert calls == []


def test_delete_fires_listener_with_none(state):
    """delete() fires listeners just like set(key, None) would."""
    state.set("var.a", "hello")
    captured = []
    state.subscribe("var.*", lambda k, o, n, s: captured.append((k, o, n, s)))
    state.delete("var.a", source="test")
    assert captured == [("var.a", "hello", None, "test")]


def test_delete_distinguishable_from_set_to_none(state):
    """At listener time, deleted keys are absent from the store, but a key
    set to None is present. This is what consumers (engine, cloud relay) use
    to distinguish the two cases.
    """
    set_to_none_state: dict[str, bool] = {}

    def handler(key, old_value, new_value, source):
        set_to_none_state[key] = key in state.snapshot()

    state.subscribe("var.*", handler)
    state.set("var.a", "hello")
    state.set("var.a", None)  # set-to-None: key still in store
    state.set("var.b", "world")
    state.delete("var.b")  # delete: key absent from store

    assert set_to_none_state["var.a"] is True   # set-to-None preserves the key
    assert set_to_none_state["var.b"] is False  # delete removes the key


def test_delete_appends_history(state):
    state.set("var.a", 42)
    state.delete("var.a", source="cleanup")
    history = state.get_history(10)
    assert history[-1]["key"] == "var.a"
    assert history[-1]["old_value"] == 42
    assert history[-1]["new_value"] is None
    assert history[-1]["source"] == "cleanup"


# --- Variable binding tests ---


def test_variable_config_source_fields():
    """VariableConfig accepts optional source_key and source_map."""
    from openavc.core.project_loader import VariableConfig

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
    from openavc.core.project_loader import VariableConfig

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


# --- Flat-primitive invariant (H-080) ---


def test_set_rejects_nested_dict(state):
    """A nested dict never enters the store and fires no notification."""
    calls = []
    state.subscribe("var.*", lambda k, o, n, s: calls.append(n))
    state.set("var.bad", {"nested": 1}, source="macro")
    assert state.get("var.bad") is None
    assert "var.bad" not in state.snapshot()
    assert calls == []


def test_set_rejects_list(state):
    state.set("var.bad", [1, 2, 3], source="script")
    assert state.get("var.bad") is None
    assert "var.bad" not in state.snapshot()


def test_set_accepts_bool_and_none(state):
    """bool (an int subclass) and None are valid primitives, not rejected."""
    state.set("var.flag", True)
    assert state.get("var.flag") is True
    state.set("var.v", "x")
    state.set("var.v", None)  # clearing to None is a valid primitive write
    assert state.get("var.v") is None
    assert "var.v" in state.snapshot()  # present-with-None, not rejected


def test_set_non_primitive_cannot_corrupt_change_detection(state):
    """The core H-080 harm: a mutable value mutated in place defeats the
    equality+identity change guard and silently drops notifications. Rejecting
    non-primitives at the store boundary removes that class entirely — and a
    later legitimate primitive write still notifies normally."""
    state.set("var.x", {"v": 1}, source="macro")  # rejected, never stored
    assert state.get("var.x") is None
    calls = []
    state.subscribe("var.x", lambda k, o, n, s: calls.append(n))
    state.set("var.x", 5)
    assert state.get("var.x") == 5
    assert calls == [5]


def test_set_batch_skips_non_primitive_keeps_valid(state):
    """set_batch drops the bad entries but applies every valid one."""
    state.set_batch({"var.ok": 1, "var.bad": {"a": 1}, "var.ok2": "y"})
    assert state.get("var.ok") == 1
    assert state.get("var.ok2") == "y"
    assert "var.bad" not in state.snapshot()


# --- get_history count clamp (M-138) ---


def test_get_history_count_zero_returns_empty(state):
    """count=0 returns nothing — not the whole 1000-entry buffer ([-0:] trap)."""
    state.set("var.a", 1)
    state.set("var.a", 2)
    assert state.get_history(0) == []


def test_get_history_negative_count_returns_empty(state):
    """A negative count returns nothing, not a wrong window."""
    state.set("var.a", 1)
    state.set("var.a", 2)
    state.set("var.a", 3)
    assert state.get_history(-5) == []


def test_get_history_positive_count_unchanged(state):
    """The normal path still returns the N most-recent entries."""
    state.set("var.a", 1)
    state.set("var.b", 2)
    recent = state.get_history(1)
    assert len(recent) == 1
    assert recent[0]["key"] == "var.b"


# --- Event emission coalescing (M-139) ---


async def test_emit_events_coalesces_into_one_task_per_batch(wired):
    """A bulk set_batch schedules ONE dispatch task, not two per key, while
    still delivering every change's generic state.changed event."""
    state, events = wired
    received = []
    events.on("state.changed", lambda e, p: received.append(p["key"]))

    updates = {f"var.k{i}": i for i in range(50)}
    state.set_batch(updates)
    # Old per-key fan-out would have scheduled 2*50 = 100 tasks here.
    assert len(state._pending_event_tasks) == 1

    await state.flush_pending_events()
    assert sorted(received) == sorted(updates.keys())
    assert state._pending_event_tasks == set()


async def test_emit_events_per_key_event_still_delivered(wired):
    """The per-key state.changed.<key> event reaches an exact subscriber."""
    state, events = wired
    hits = []
    events.on("state.changed.var.target", lambda e, p: hits.append(p["new_value"]))
    state.set("var.target", 7)
    await state.flush_pending_events()
    assert hits == [7]
