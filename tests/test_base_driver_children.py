"""Tests for BaseDriver child-entity APIs (P3 of the device-with-children plan).

A "child entity" is a sub-unit owned by a parent device — encoders on a
matrix controller, decoders on a presentation switcher, zones on a DSP.
Drivers declare them in DRIVER_INFO["child_entity_types"] and manage live
instances via register_child / deregister_child / set_child_state /
set_child_state_batch / set_children_state_batch / poll_children.

The platform owns the state-key format:

    device.<parent_id>.<child_type>.<local_id_padded>.<property>

These tests cover the seven acceptance items called out in the plan plus
the edge cases that drove the design (idempotency, padded IDs, atomicity,
synthetic `online`, range validation, schema rejection).
"""

from __future__ import annotations

from typing import Any

import pytest

from server.core.event_bus import EventBus
from server.core.state_store import StateStore
from server.drivers.base import BaseDriver


# ---------------------------------------------------------------------------
# Fixture driver — declares two child types with realistic shapes.
# ---------------------------------------------------------------------------


class _FakeControllerDriver(BaseDriver):
    """Test fixture: a controller-style driver with two child types.

    `encoder` uses 3-digit padded IDs (mirroring real AV-over-IP gear).
    `decoder` uses no padding so we can prove the platform respects the
    declared pad_width per type, not globally.
    """

    DRIVER_INFO: dict[str, Any] = {
        "id": "fake_controller",
        "name": "Fake Controller",
        "transport": "tcp",
        "state_variables": {},
        "commands": {},
        "child_entity_types": {
            "encoder": {
                "label": "Encoder",
                "label_plural": "Encoders",
                "id_format": {
                    "type": "integer", "min": 1, "max": 762, "pad_width": 3,
                },
                "state_variables": {
                    "name": {"type": "string"},
                    "ip": {"type": "string"},
                    "signal_present": {"type": "boolean"},
                    "audio_source": {
                        "type": "enum", "values": ["HDMI", "ANALOG"],
                    },
                    "lan_mode": {"type": "integer", "min": 1, "max": 2},
                },
                "summary_fields": ["name", "ip", "signal_present"],
                "label_field": "name",
            },
            "decoder": {
                "label": "Decoder",
                "id_format": {
                    "type": "integer", "min": 1, "max": 32,
                    # no pad_width — IDs render bare ("5", not "005").
                },
                "state_variables": {
                    "name": {"type": "string"},
                    "source": {"type": "integer", "min": 0, "max": 762},
                },
            },
        },
    }

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        return None


def _make_driver(
    device_id: str = "ctrl1",
    child_entities: dict[str, dict[str, dict[str, Any]]] | None = None,
) -> _FakeControllerDriver:
    """Fresh driver + state/event store. Each test gets an isolated set."""
    drv = _FakeControllerDriver(
        device_id=device_id,
        config={},
        state=StateStore(),
        events=EventBus(),
    )
    if child_entities is not None:
        drv.set_project_child_entities(child_entities)
    return drv


# ---------------------------------------------------------------------------
# register_child
# ---------------------------------------------------------------------------


def test_child_entity_register_creates_state_keys():
    drv = _make_driver()
    drv.register_child("encoder", 5, initial_state={"name": "Lobby TX"})

    # Every declared prop got a key, with caller overrides applied and
    # type-correct defaults filled in for the rest.
    assert drv.state.get("device.ctrl1.encoder.005.name") == "Lobby TX"
    assert drv.state.get("device.ctrl1.encoder.005.ip") == ""
    assert drv.state.get("device.ctrl1.encoder.005.signal_present") is False
    assert drv.state.get("device.ctrl1.encoder.005.audio_source") == "HDMI"
    assert drv.state.get("device.ctrl1.encoder.005.lan_mode") == 1

    # Platform-managed `online` defaults to True (registered → assumed online).
    assert drv.state.get("device.ctrl1.encoder.005.online") is True


def test_child_entity_register_idempotent():
    """Calling register_child twice with the same (type, id) is a no-op the
    second time — it does not stomp existing state with fresh defaults."""
    drv = _make_driver()
    drv.register_child("encoder", 5, initial_state={"name": "First"})
    drv.set_child_state("encoder", 5, "signal_present", True)
    # Drift the synthetic `online` flag too — second register must not reset it.
    drv.set_child_state("encoder", 5, "online", False)

    drv.register_child("encoder", 5, initial_state={"name": "Second"})

    assert drv.state.get("device.ctrl1.encoder.005.name") == "First"
    assert drv.state.get("device.ctrl1.encoder.005.signal_present") is True
    assert drv.state.get("device.ctrl1.encoder.005.online") is False
    # Still only registered once.
    assert drv.list_children("encoder") == [5]


def test_register_online_default_true_when_initial_state_omitted():
    drv = _make_driver()
    drv.register_child("encoder", 1)
    assert drv.state.get("device.ctrl1.encoder.001.online") is True


def test_register_online_can_be_set_false_via_initial_state():
    drv = _make_driver()
    drv.register_child("encoder", 1, initial_state={"online": False})
    assert drv.state.get("device.ctrl1.encoder.001.online") is False


def test_register_unknown_initial_state_prop_raises_and_rolls_back():
    drv = _make_driver()
    with pytest.raises(ValueError, match="not declared"):
        drv.register_child("encoder", 7, initial_state={"bogus": "x"})
    # No partial state should remain.
    assert drv.list_children("encoder") == []
    assert drv.state.get("device.ctrl1.encoder.007.name") is None


def test_register_unknown_child_type_raises():
    drv = _make_driver()
    with pytest.raises(ValueError, match="did not declare"):
        drv.register_child("video_wall", 1)


def test_register_id_out_of_range_raises():
    drv = _make_driver()
    with pytest.raises(ValueError, match="max"):
        drv.register_child("encoder", 1000)  # max is 762
    with pytest.raises(ValueError, match="min"):
        drv.register_child("encoder", 0)


def test_register_rejects_non_integer_id():
    drv = _make_driver()
    with pytest.raises(TypeError):
        drv.register_child("encoder", "5")  # str, not int
    with pytest.raises(TypeError):
        # bool is a subclass of int but should still be rejected — otherwise
        # register_child("encoder", True) silently lands at ID 1.
        drv.register_child("encoder", True)


# ---------------------------------------------------------------------------
# Padded local IDs
# ---------------------------------------------------------------------------


def test_state_keys_use_padded_local_id():
    """Encoder pad_width=3 → IDs render as 001/005/762, not 1/5/762."""
    drv = _make_driver()
    drv.register_child("encoder", 5)
    drv.register_child("encoder", 762)
    snapshot = drv.state.snapshot()
    assert "device.ctrl1.encoder.005.name" in snapshot
    assert "device.ctrl1.encoder.762.name" in snapshot
    assert "device.ctrl1.encoder.5.name" not in snapshot


def test_state_keys_respect_per_type_padding():
    """Decoder declares no pad_width → IDs render bare."""
    drv = _make_driver()
    drv.register_child("decoder", 5)
    snapshot = drv.state.snapshot()
    assert "device.ctrl1.decoder.5.name" in snapshot
    assert "device.ctrl1.decoder.005.name" not in snapshot


# ---------------------------------------------------------------------------
# deregister_child
# ---------------------------------------------------------------------------


def test_child_entity_deregister_removes_state_keys():
    drv = _make_driver()
    drv.register_child("encoder", 5)
    drv.register_child("encoder", 6)
    drv.register_child("decoder", 1)

    drv.deregister_child("encoder", 5)

    snap = drv.state.snapshot()
    assert not any(k.startswith("device.ctrl1.encoder.005.") for k in snap)
    # Sibling and other-type children are untouched.
    assert "device.ctrl1.encoder.006.name" in snap
    assert "device.ctrl1.decoder.1.name" in snap
    assert drv.list_children("encoder") == [6]


def test_deregister_fires_delete_notifications():
    """A subscriber on the child prefix sees one delete per state key."""
    drv = _make_driver()
    drv.register_child("encoder", 5)

    captured: list[tuple[str, Any, Any]] = []
    drv.state.subscribe_children(
        "ctrl1", "encoder",
        lambda k, o, n, s: captured.append((k, o, n)),
    )

    drv.deregister_child("encoder", 5)

    deleted_keys = {k for k, _o, n in captured if n is None}
    assert "device.ctrl1.encoder.005.name" in deleted_keys
    assert "device.ctrl1.encoder.005.online" in deleted_keys


def test_deregister_unknown_child_is_noop():
    drv = _make_driver()
    # No prior register; should not raise and should not fire anything.
    captured: list = []
    drv.state.subscribe("*", lambda k, o, n, s: captured.append(k))
    drv.deregister_child("encoder", 5)
    assert captured == []


# ---------------------------------------------------------------------------
# list_children
# ---------------------------------------------------------------------------


def test_list_children_empty_when_none_registered():
    drv = _make_driver()
    assert drv.list_children("encoder") == []
    # Unknown types also return [] (not a raise) — they're just queries.
    assert drv.list_children("anything") == []


def test_list_children_returns_insertion_order():
    drv = _make_driver()
    drv.register_child("encoder", 7)
    drv.register_child("encoder", 2)
    drv.register_child("encoder", 10)
    assert drv.list_children("encoder") == [7, 2, 10]


# ---------------------------------------------------------------------------
# set_child_state / set_child_state_batch
# ---------------------------------------------------------------------------


def test_set_child_state_unknown_prop_raises():
    drv = _make_driver()
    drv.register_child("encoder", 5)
    with pytest.raises(ValueError, match="not declared"):
        drv.set_child_state("encoder", 5, "bogus", 1)


def test_set_child_state_writes_to_correct_key():
    drv = _make_driver()
    drv.register_child("encoder", 5)
    drv.set_child_state("encoder", 5, "name", "Stage TX")
    assert drv.state.get("device.ctrl1.encoder.005.name") == "Stage TX"


def test_set_child_state_allows_synthetic_online_prop():
    drv = _make_driver()
    drv.register_child("encoder", 5)
    drv.set_child_state("encoder", 5, "online", False)
    assert drv.state.get("device.ctrl1.encoder.005.online") is False


def test_set_child_state_batch_unknown_prop_aborts_entire_batch():
    drv = _make_driver()
    drv.register_child("encoder", 5)
    with pytest.raises(ValueError):
        drv.set_child_state_batch("encoder", 5, {"name": "OK", "bogus": 1})
    # The valid "name" update must NOT have landed — validation is up-front.
    assert drv.state.get("device.ctrl1.encoder.005.name") == ""


def test_set_child_state_batch_writes_all_props():
    drv = _make_driver()
    drv.register_child("encoder", 5)
    drv.set_child_state_batch("encoder", 5, {
        "name": "Stage", "ip": "10.0.0.5", "signal_present": True,
    })
    assert drv.state.get("device.ctrl1.encoder.005.name") == "Stage"
    assert drv.state.get("device.ctrl1.encoder.005.ip") == "10.0.0.5"
    assert drv.state.get("device.ctrl1.encoder.005.signal_present") is True


# ---------------------------------------------------------------------------
# set_children_state_batch — atomicity
# ---------------------------------------------------------------------------


def test_set_children_state_batch_atomic():
    """All children's state is in place before any per-key listener fires.

    A bulk subscriber is the cleanest way to assert atomicity: it receives
    one delta list containing every change in the transaction, after the
    store has fully been updated. If the writes were per-key, the bulk
    callback would receive multiple smaller deltas instead.
    """
    drv = _make_driver()
    drv.register_child("encoder", 1)
    drv.register_child("encoder", 2)
    drv.register_child("encoder", 3)

    # Snapshot state for atomicity check: when the bulk callback fires, the
    # store must already reflect *every* write in the batch.
    callbacks: list[list[tuple]] = []
    snapshots: list[dict] = []

    def _bulk(changes: list[tuple]) -> None:
        callbacks.append(list(changes))
        snapshots.append(drv.state.snapshot())

    drv.state.subscribe_bulk("device.ctrl1.encoder.*", _bulk)

    drv.set_children_state_batch([
        ("encoder", 1, {"name": "A", "signal_present": True}),
        ("encoder", 2, {"name": "B", "signal_present": True}),
        ("encoder", 3, {"name": "C", "ip": "10.0.0.3"}),
    ])

    # Exactly one bulk callback, with all 6 changes (3 children × 2 props).
    # Every value we wrote differs from the seeded default, so the store's
    # no-op short-circuit doesn't drop any of them.
    assert len(callbacks) == 1
    assert len(callbacks[0]) == 6

    # And at callback time, all per-child state is already visible.
    snap = snapshots[0]
    assert snap["device.ctrl1.encoder.001.name"] == "A"
    assert snap["device.ctrl1.encoder.001.signal_present"] is True
    assert snap["device.ctrl1.encoder.002.name"] == "B"
    assert snap["device.ctrl1.encoder.003.name"] == "C"
    assert snap["device.ctrl1.encoder.003.ip"] == "10.0.0.3"


def test_set_children_state_batch_rejects_unknown_prop_before_any_write():
    drv = _make_driver()
    drv.register_child("encoder", 1)
    drv.register_child("encoder", 2)
    with pytest.raises(ValueError):
        drv.set_children_state_batch([
            ("encoder", 1, {"name": "Updated"}),
            ("encoder", 2, {"bogus": "x"}),
        ])
    # Neither update lands — validation is up-front.
    assert drv.state.get("device.ctrl1.encoder.001.name") == ""


def test_set_children_state_batch_empty_is_noop():
    drv = _make_driver()
    drv.register_child("encoder", 1)
    captured: list = []
    drv.state.subscribe("*", lambda k, o, n, s: captured.append(k))
    drv.set_children_state_batch([])
    drv.set_children_state_batch([("encoder", 1, {})])
    assert captured == []


# ---------------------------------------------------------------------------
# StateStore.subscribe_children helper
# ---------------------------------------------------------------------------


def test_subscribe_children_helper():
    """state.subscribe_children resolves to the right glob pattern, fires
    on matching keys, and does NOT fire on unrelated devices/types."""
    drv = _make_driver()
    drv.register_child("encoder", 1)
    drv.register_child("decoder", 1)

    seen: list[str] = []
    drv.state.subscribe_children(
        "ctrl1", "encoder", lambda k, o, n, s: seen.append(k),
    )

    drv.set_child_state("encoder", 1, "name", "X")
    drv.set_child_state("decoder", 1, "name", "Y")  # different type
    drv.set_child_state("encoder", 1, "signal_present", True)

    assert "device.ctrl1.encoder.001.name" in seen
    assert "device.ctrl1.encoder.001.signal_present" in seen
    assert "device.ctrl1.decoder.1.name" not in seen


# ---------------------------------------------------------------------------
# poll_children
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_poll_children_batches_and_applies():
    drv = _make_driver()
    for eid in range(1, 11):
        drv.register_child("encoder", eid)

    seen_batches: list[list[int]] = []

    async def fetch(batch_ids: list[int]) -> dict[int, dict[str, Any]]:
        seen_batches.append(list(batch_ids))
        # Echo back a state update for every id we were asked about.
        return {eid: {"name": f"E{eid:03d}"} for eid in batch_ids}

    await drv.poll_children(
        "encoder", fetch, batch_size=3, inter_batch_delay=0,
    )

    # 10 IDs / batch_size 3 → batches of 3, 3, 3, 1.
    assert [len(b) for b in seen_batches] == [3, 3, 3, 1]
    assert seen_batches[0] == [1, 2, 3]
    assert seen_batches[-1] == [10]

    # State was applied for every encoder.
    for eid in range(1, 11):
        assert drv.state.get(f"device.ctrl1.encoder.{eid:03d}.name") == f"E{eid:03d}"


@pytest.mark.asyncio
async def test_poll_children_skips_results_for_unregistered_ids():
    """The fetch hook is free to return extra IDs that aren't registered
    (e.g. a child was removed concurrently); the helper drops them rather
    than blowing up validation when computing a state key for an unknown
    child."""
    drv = _make_driver()
    drv.register_child("encoder", 1)
    drv.register_child("encoder", 2)

    async def fetch(batch_ids: list[int]) -> dict[int, dict[str, Any]]:
        return {
            1: {"name": "Real"},
            999: {"name": "Ghost"},  # not registered — must be ignored
        }

    await drv.poll_children("encoder", fetch, batch_size=10, inter_batch_delay=0)
    assert drv.state.get("device.ctrl1.encoder.001.name") == "Real"
    # No 999-flavored state should exist.
    assert "device.ctrl1.encoder.999.name" not in drv.state.snapshot()


@pytest.mark.asyncio
async def test_poll_children_empty_when_nothing_registered():
    drv = _make_driver()
    fetched: list[list[int]] = []

    async def fetch(batch_ids: list[int]) -> dict[int, dict[str, Any]]:
        fetched.append(batch_ids)
        return {}

    await drv.poll_children("encoder", fetch)
    assert fetched == []


# ---------------------------------------------------------------------------
# Project-side child_entities -> register_child label injection (P4)
# ---------------------------------------------------------------------------


def test_register_child_injects_project_label():
    """When DeviceManager passes child_entities (from the project file),
    register_child seeds the synthetic `label` state key from the project's
    ChildEntityConfig.label so listeners see the user's name without a
    second update round-trip."""
    drv = _make_driver(child_entities={
        "encoder": {
            "005": {"label": "Lobby TX", "config": {}},
        },
    })
    drv.register_child("encoder", 5)
    assert drv.state.get("device.ctrl1.encoder.005.label") == "Lobby TX"


def test_register_child_label_defaults_empty_without_project_entry():
    """A child with no project-side label gets `label = ""`, not None."""
    drv = _make_driver(child_entities={})
    drv.register_child("encoder", 5)
    assert drv.state.get("device.ctrl1.encoder.005.label") == ""


def test_register_child_initial_state_label_wins_over_project_label():
    """An explicit initial_state[label] takes precedence over the project's
    stored label. This matters for drivers that compute a label from
    discovered metadata (e.g. the device's hostname) when no user label
    has been authored yet."""
    drv = _make_driver(child_entities={
        "encoder": {"005": {"label": "Project Label"}},
    })
    drv.register_child("encoder", 5, initial_state={"label": "Driver Label"})
    assert drv.state.get("device.ctrl1.encoder.005.label") == "Driver Label"


def test_register_child_label_writable_via_set_child_state():
    """set_child_state accepts the synthetic `label` key without raising
    — the IDE writes user-edited labels back through this path."""
    drv = _make_driver()
    drv.register_child("encoder", 5)
    drv.set_child_state("encoder", 5, "label", "Updated")
    assert drv.state.get("device.ctrl1.encoder.005.label") == "Updated"


def test_register_child_padded_id_lookup():
    """Project entries are keyed by the padded id string, matching the
    state-store convention. Encoder 5 with pad_width=3 must look up "005",
    not "5"."""
    drv = _make_driver(child_entities={
        "encoder": {
            "5": {"label": "WRONG — unpadded key"},
            "005": {"label": "Correct"},
        },
    })
    drv.register_child("encoder", 5)
    assert drv.state.get("device.ctrl1.encoder.005.label") == "Correct"


def test_register_child_project_entries_for_other_types_ignored():
    """Project entries for child_type X don't bleed into child_type Y."""
    drv = _make_driver(child_entities={
        "decoder": {"5": {"label": "Decoder label"}},
    })
    drv.register_child("encoder", 5)
    assert drv.state.get("device.ctrl1.encoder.005.label") == ""


# ---------------------------------------------------------------------------
# String local IDs + dynamic per-child schemas
#
# These exercise the two generic child-entity capabilities used by any
# runtime-discovered device whose sub-units are named (not numbered) and whose
# per-unit control set is only known at connect time. The fixture below is an
# invented DSP — no real product — with a `component` type (string id,
# dynamic per-child schema) and a `named_control` type (string id, static
# schema), mirroring how such a device's topology is auto-imported.
# ---------------------------------------------------------------------------


class _AcmeDspDriver(BaseDriver):
    """Invented DSP fixture: string-keyed, dynamically-schema'd children."""

    DRIVER_INFO: dict[str, Any] = {
        "id": "acme_dsp",
        "name": "Acme DSP",
        "transport": "tcp",
        "state_variables": {},
        "commands": {},
        "child_entity_types": {
            "component": {
                "label": "Component",
                "label_plural": "Components",
                "dynamic": True,
                "id_format": {"type": "string"},
                # No fixed state_variables — each component publishes its own
                # control set at register_child(schema=...).
                "summary_fields": ["label", "kind"],
            },
            "named_control": {
                "label": "Named Control",
                "id_format": {"type": "string", "max_length": 64},
                "state_variables": {
                    "value": {"type": "number"},
                    "string": {"type": "string"},
                },
            },
        },
    }

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        return None


def _make_dsp(device_id: str = "dsp1") -> _AcmeDspDriver:
    return _AcmeDspDriver(
        device_id=device_id, config={}, state=StateStore(), events=EventBus(),
    )


def test_string_local_id_static_type_creates_keys():
    drv = _make_dsp()
    drv.register_child("named_control", "Volume", initial_state={"value": -12.0})
    assert drv.state.get("device.dsp1.named_control.Volume.value") == -12.0
    assert drv.state.get("device.dsp1.named_control.Volume.string") == ""
    assert drv.state.get("device.dsp1.named_control.Volume.online") is True
    assert drv.list_children("named_control") == ["Volume"]
    assert drv.is_child_registered("named_control", "Volume") is True
    assert drv.get_child_state("named_control", "Volume")["value"] == -12.0


def test_string_local_id_rejects_unsafe_and_wrong_type():
    drv = _make_dsp()
    # Dot would collide with the state-key separator.
    with pytest.raises(ValueError):
        drv.register_child("named_control", "Room.Volume")
    # Whitespace / glob metacharacters are rejected.
    with pytest.raises(ValueError):
        drv.register_child("named_control", "Main Gain")
    with pytest.raises(ValueError):
        drv.register_child("named_control", "gain*")
    # Empty is rejected.
    with pytest.raises(ValueError):
        drv.register_child("named_control", "")
    # Over max_length (64) is rejected.
    with pytest.raises(ValueError):
        drv.register_child("named_control", "x" * 65)
    # An integer where a string id is declared is a TypeError.
    with pytest.raises(TypeError):
        drv.register_child("named_control", 5)


def test_integer_type_rejects_string_id():
    """A static integer-id driver still rejects a string id (no regression
    from adding string support)."""
    drv = _make_driver()
    with pytest.raises(TypeError):
        drv.register_child("encoder", "five")


def test_dynamic_child_schema_per_instance():
    """Two components of the same dynamic type carry different control sets;
    each validates against its own schema."""
    drv = _make_dsp()
    drv.register_child(
        "component", "PgmGain",
        schema={
            "gain": {"type": "number", "label": "Gain (dB)"},
            "mute": {"type": "boolean"},
        },
        initial_state={"gain": -6.0, "label": "Program Gain"},
    )
    drv.register_child(
        "component", "PgmRouter",
        schema={"select_1": {"type": "integer"}},
    )

    # PgmGain's discovered controls exist; the platform `online`/`label` too.
    assert drv.state.get("device.dsp1.component.PgmGain.gain") == -6.0
    assert drv.state.get("device.dsp1.component.PgmGain.mute") is False
    assert drv.state.get("device.dsp1.component.PgmGain.label") == "Program Gain"
    assert drv.state.get("device.dsp1.component.PgmGain.online") is True
    assert drv.state.get("device.dsp1.component.PgmRouter.select_1") == 0

    # Each child validates against ITS OWN schema.
    drv.set_child_state("component", "PgmGain", "gain", -3.0)
    assert drv.state.get("device.dsp1.component.PgmGain.gain") == -3.0
    # PgmGain has no `select_1`; PgmRouter has no `gain`.
    with pytest.raises(ValueError):
        drv.set_child_state("component", "PgmGain", "select_1", 2)
    with pytest.raises(ValueError):
        drv.set_child_state("component", "PgmRouter", "gain", -3.0)


def test_dynamic_child_schema_exposed_via_get_child_schema():
    drv = _make_dsp()
    drv.register_child(
        "component", "PgmGain",
        schema={"gain": {"type": "number"}, "mute": {"type": "boolean"}},
    )
    schema = drv.get_child_schema("component", "PgmGain")
    assert "gain" in schema and "mute" in schema
    # Platform-managed keys injected into the effective schema.
    assert "online" in schema and "label" in schema
    # The type is reported dynamic; the type-level schema carries no controls.
    types = drv.get_child_entity_types()
    assert types["component"]["dynamic"] is True
    assert set(types["component"]["state_variables"]) == {"online", "label"}
    assert drv.is_child_type_dynamic("component") is True
    assert drv.is_child_type_dynamic("named_control") is False


def test_schema_arg_rejected_on_static_type():
    drv = _make_dsp()
    with pytest.raises(ValueError):
        drv.register_child(
            "named_control", "Volume",
            schema={"value": {"type": "number"}},
        )


def test_dynamic_initial_state_unknown_prop_rolls_back_schema():
    """A bad initial_state prop rolls back the registration AND the stored
    per-child schema, so a corrected retry succeeds."""
    drv = _make_dsp()
    with pytest.raises(ValueError):
        drv.register_child(
            "component", "PgmGain",
            schema={"gain": {"type": "number"}},
            initial_state={"nonexistent": 1},
        )
    assert drv.is_child_registered("component", "PgmGain") is False
    # Stored schema was cleaned up — a retry with a different schema works.
    drv.register_child(
        "component", "PgmGain",
        schema={"mute": {"type": "boolean"}},
    )
    assert "mute" in drv.get_child_schema("component", "PgmGain")
    assert "gain" not in drv.get_child_schema("component", "PgmGain")


def test_deregister_dynamic_child_drops_schema():
    """Deregistering a dynamic child clears its stored schema so a later
    re-register can publish a fresh one (topology changed on the device)."""
    drv = _make_dsp()
    drv.register_child(
        "component", "Blk",
        schema={"gain": {"type": "number"}},
    )
    drv.deregister_child("component", "Blk")
    assert drv.state.get("device.dsp1.component.Blk.gain") is None
    # Re-register with a different control set.
    drv.register_child(
        "component", "Blk",
        schema={"position": {"type": "number"}, "label2": {"type": "string"}},
    )
    schema = drv.get_child_schema("component", "Blk")
    assert "position" in schema
    assert "gain" not in schema


def test_dynamic_child_set_batch_validates_per_instance():
    drv = _make_dsp()
    drv.register_child(
        "component", "Mix",
        schema={
            "in_1_gain": {"type": "number"},
            "out_1_mute": {"type": "boolean"},
        },
    )
    drv.set_child_state_batch(
        "component", "Mix",
        {"in_1_gain": -10.0, "out_1_mute": True},
    )
    assert drv.state.get("device.dsp1.component.Mix.in_1_gain") == -10.0
    assert drv.state.get("device.dsp1.component.Mix.out_1_mute") is True
    # A batch with any unknown prop aborts entirely.
    with pytest.raises(ValueError):
        drv.set_child_state_batch(
            "component", "Mix",
            {"in_1_gain": 0.0, "bogus": 1},
        )
