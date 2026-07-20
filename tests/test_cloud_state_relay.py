"""
Tests for the OpenAVC cloud state relay's priority-tier bucketing and
inter-chunk pacing (device-children plan P9).

The relay subscribes to the StateStore wildcard and forwards changes to
the cloud. Top-level state keys (var, system, ui, plugin, and
``device.<id>.<prop>``) ride the existing 2 s ``state_batch_interval``.
Child-entity keys (``device.<id>.<type>.<padded>.<prop>``) consult the
parent driver's ``child_entity_types[<type>].state_variables[<prop>]
.cloud_priority`` to fall into one of:

    "high"          -> top tier (2 s cadence)
    "low"           -> low tier (30 s cadence)
    anything else   -> child tier (5 s cadence)

Snapshots and large flushes pace ``state_batch_pace_delay`` seconds
between chunks above the ``state_batch_pace_threshold`` key count.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from server.cloud.protocol import STATE_BATCH
from server.cloud.state_relay import StateRelay
from server.core.event_bus import EventBus
from server.core.state_store import StateStore


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _RecordingAgent:
    """Minimal CloudAgent stand-in that captures messages and the wall-clock
    time at which each was sent. ``devices`` mimics ``DeviceManager`` enough
    to expose driver schemas for tier classification.
    """

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        drivers: dict[str, Any] | None = None,
    ):
        self._config: dict[str, Any] = config or {}
        self.devices = _StubDeviceManager(drivers or {})
        self.sent: list[tuple[str, dict[str, Any], float]] = []
        self._send_lock = asyncio.Lock()

    async def send_message(self, msg_type: str, payload: dict[str, Any]) -> None:
        # Async lock keeps the send timestamps strictly monotonic per call,
        # which matters for the pacing assertions below.
        async with self._send_lock:
            self.sent.append((msg_type, payload, time.perf_counter()))


class _StubDeviceManager:
    """DeviceManager surface needed by the relay for tier lookups."""

    def __init__(self, drivers: dict[str, Any]):
        self._drivers = drivers

    def get_driver(self, device_id: str) -> Any:
        return self._drivers.get(device_id)


class _StubDriver:
    """Carries a ``DRIVER_INFO`` dict that the relay reads for
    ``child_entity_types``, plus optional per-child dynamic schemas
    keyed by ``(child_type, local_id)`` for ``get_child_schema``."""

    def __init__(
        self,
        driver_info: dict[str, Any],
        child_schemas: dict[tuple, dict[str, Any]] | None = None,
    ):
        self.DRIVER_INFO = driver_info
        self._child_schemas = child_schemas or {}

    def get_child_schema(self, child_type: str, local_id) -> dict[str, Any]:
        return self._child_schemas.get((child_type, local_id), {})


def _chazy_like_driver() -> _StubDriver:
    """A driver shaped like a Chazy-ish controller: encoders with a few
    state vars, one explicit ``low`` priority, one explicit ``high``."""
    return _StubDriver({
        "id": "chazy_like",
        "child_entity_types": {
            "encoder": {
                "label": "Encoder",
                "id_format": {"type": "integer", "min": 1, "max": 762, "pad_width": 3},
                "state_variables": {
                    "name": {"type": "string"},
                    "signal_present": {"type": "boolean"},
                    "source_video": {"type": "integer", "cloud_priority": "high"},
                    "source_ir": {"type": "integer", "cloud_priority": "low"},
                },
            },
        },
    })


# ---------------------------------------------------------------------------
# Tier classification
# ---------------------------------------------------------------------------


class TestKeyTier:
    """Covers _key_tier and _lookup_child_tier."""

    def test_top_level_device_key_is_top(self):
        relay = StateRelay(_RecordingAgent(), StateStore())
        assert relay._key_tier("device.proj.power") == "top"

    def test_var_key_is_top(self):
        relay = StateRelay(_RecordingAgent(), StateStore())
        assert relay._key_tier("var.room_active") == "top"

    def test_system_key_is_top(self):
        relay = StateRelay(_RecordingAgent(), StateStore())
        assert relay._key_tier("system.cpu_percent") == "top"

    def test_child_default_priority_is_child(self):
        agent = _RecordingAgent(drivers={"ctrl1": _chazy_like_driver()})
        relay = StateRelay(agent, StateStore())
        # `name` has no cloud_priority -> child tier
        assert relay._key_tier("device.ctrl1.encoder.005.name") == "child"
        assert relay._key_tier("device.ctrl1.encoder.005.signal_present") == "child"

    def test_child_low_priority_is_low(self):
        agent = _RecordingAgent(drivers={"ctrl1": _chazy_like_driver()})
        relay = StateRelay(agent, StateStore())
        assert relay._key_tier("device.ctrl1.encoder.005.source_ir") == "low"

    def test_dotted_child_prop_classifies_as_child_key(self):
        """A child property name may itself contain dots (Q-SYS control
        names like ``input.1.gain``) — the key must still classify against
        the child schema, not fall through to the fast top tier."""
        driver = _StubDriver({
            "id": "dsp_like",
            "child_entity_types": {
                "component": {
                    "label": "Component",
                    "dynamic": True,
                    "id_format": {"type": "string"},
                    "state_variables": {
                        "name": {"type": "string"},
                        "meter.level": {"type": "number", "cloud_priority": "low"},
                    },
                },
            },
        })
        agent = _RecordingAgent(drivers={"core": driver})
        relay = StateRelay(agent, StateStore())
        # Statically declared dotted prop honors its declared priority.
        assert relay._key_tier("device.core.component.Mixer.meter.level") == "low"
        # A dotted prop the schema doesn't claim gets the default child
        # cadence — NOT the fast top tier (the old 5-segment assumption).
        assert relay._key_tier("device.core.component.Mixer.input.1.gain") == "child"

    def test_dynamic_child_schema_priorities_are_honored(self):
        """cloud_priority in a per-child dynamic schema (register_child's
        schema=...) must be consulted — the static declaration only carries
        summary props for dynamic types."""
        driver = _StubDriver(
            {
                "id": "dsp_like",
                "child_entity_types": {
                    "component": {
                        "label": "Component",
                        "dynamic": True,
                        "id_format": {"type": "string"},
                        "state_variables": {"name": {"type": "string"}},
                    },
                },
            },
            child_schemas={
                ("component", "Mixer"): {
                    "mute": {"type": "boolean", "cloud_priority": "high"},
                    "meter.rms": {"type": "number", "cloud_priority": "low"},
                    "input.1.gain": {"type": "number"},
                },
            },
        )
        agent = _RecordingAgent(drivers={"core": driver})
        relay = StateRelay(agent, StateStore())
        assert relay._key_tier("device.core.component.Mixer.mute") == "top"
        assert relay._key_tier("device.core.component.Mixer.meter.rms") == "low"
        assert relay._key_tier("device.core.component.Mixer.input.1.gain") == "child"

    def test_flat_state_var_low_priority_is_low(self):
        """cloud_priority: low on a TOP-LEVEL state variable slows that key;
        undeclared flat keys (incl. platform-managed ones) stay fast."""
        driver = _StubDriver({
            "id": "flat_like",
            "state_variables": {
                "cpu_usage": {"type": "number", "cloud_priority": "low"},
                "power": {"type": "boolean"},
            },
        })
        agent = _RecordingAgent(drivers={"proc": driver})
        relay = StateRelay(agent, StateStore())
        assert relay._key_tier("device.proc.cpu_usage") == "low"
        assert relay._key_tier("device.proc.power") == "top"
        assert relay._key_tier("device.proc.connected") == "top"

    def test_child_high_priority_is_top(self):
        agent = _RecordingAgent(drivers={"ctrl1": _chazy_like_driver()})
        relay = StateRelay(agent, StateStore())
        # `high` rides the snappy top tier so video routing feels live.
        assert relay._key_tier("device.ctrl1.encoder.005.source_video") == "top"

    def test_unknown_device_falls_back_to_top(self):
        agent = _RecordingAgent(drivers={})  # no driver registered
        relay = StateRelay(agent, StateStore())
        assert relay._key_tier("device.ghost.encoder.001.name") == "top"

    def test_unknown_child_type_falls_back_to_top(self):
        agent = _RecordingAgent(drivers={"ctrl1": _chazy_like_driver()})
        relay = StateRelay(agent, StateStore())
        # Driver doesn't declare a `widget` child type.
        assert relay._key_tier("device.ctrl1.widget.005.name") == "top"

    def test_unknown_prop_on_known_child_type_is_child(self):
        agent = _RecordingAgent(drivers={"ctrl1": _chazy_like_driver()})
        relay = StateRelay(agent, StateStore())
        # Property not declared in the schema -> default child tier (don't
        # slow-walk it just because the driver forgot to declare it).
        assert relay._key_tier("device.ctrl1.encoder.005.bogus") == "child"

    def test_result_is_cached(self):
        agent = _RecordingAgent(drivers={"ctrl1": _chazy_like_driver()})
        relay = StateRelay(agent, StateStore())
        relay._key_tier("device.ctrl1.encoder.005.source_ir")
        assert "device.ctrl1.encoder.005.source_ir" in relay._tier_cache
        # Even if the driver changes after caching, the relay returns the
        # cached value until start() clears the cache.
        agent.devices._drivers.clear()
        assert relay._key_tier("device.ctrl1.encoder.005.source_ir") == "low"


# ---------------------------------------------------------------------------
# Bucket-aware intake
# ---------------------------------------------------------------------------


class TestBucketIntake:
    """_on_state_change routes entries into the right tier."""

    def test_high_priority_lands_in_top(self):
        agent = _RecordingAgent(drivers={"ctrl1": _chazy_like_driver()})
        relay = StateRelay(agent, StateStore())
        relay._on_state_change("device.ctrl1.encoder.005.source_video", 0, 7, "drv")
        assert len(relay._batches["top"]) == 1
        assert relay._batches["top"][0]["key"] == "device.ctrl1.encoder.005.source_video"
        assert relay._batches["child"] == []
        assert relay._batches["low"] == []

    def test_low_priority_lands_in_low(self):
        agent = _RecordingAgent(drivers={"ctrl1": _chazy_like_driver()})
        relay = StateRelay(agent, StateStore())
        relay._on_state_change("device.ctrl1.encoder.005.source_ir", 0, 3, "drv")
        assert relay._batches["top"] == []
        assert relay._batches["child"] == []
        assert len(relay._batches["low"]) == 1

    def test_default_child_priority_lands_in_child(self):
        agent = _RecordingAgent(drivers={"ctrl1": _chazy_like_driver()})
        relay = StateRelay(agent, StateStore())
        relay._on_state_change("device.ctrl1.encoder.005.signal_present", False, True, "drv")
        assert relay._batches["top"] == []
        assert len(relay._batches["child"]) == 1
        assert relay._batches["low"] == []

    def test_deletion_is_marked(self):
        """Keys deleted from the store gain a ``deleted: True`` flag so the
        cloud removes them rather than storing ``None``."""
        state = StateStore()
        agent = _RecordingAgent()
        relay = StateRelay(agent, state)
        # Simulate StateStore.delete() — the store does not contain the key
        # at callback time.
        relay._on_state_change("var.gone", "old", None, "test")
        entry = relay._batches["top"][0]
        assert entry.get("deleted") is True


# ---------------------------------------------------------------------------
# Snapshot pacing — 40k keys
# ---------------------------------------------------------------------------


class TestSnapshotPacing:
    """The initial snapshot ships in paced chunks so 40k keys do not
    burst 80+ frames in one event-loop tick."""

    @pytest.mark.asyncio
    async def test_40k_snapshot_chunks_and_paces(self):
        state = StateStore()
        events = EventBus()
        state.set_event_bus(events)

        # Seed 40,000 var keys (skipping prefix validation by using var.).
        for i in range(40_000):
            state.set(f"var.k{i}", i, source="seed")

        agent = _RecordingAgent(config={
            "state_batch_max_size": 500,        # 80 chunks total
            "state_batch_pace_threshold": 2_000,
            "state_batch_pace_delay": 0.005,    # 5 ms keeps the test fast
            # Long intervals so steady-state loops don't fire during the test.
            "state_batch_interval": 60,
            "state_batch_interval_child": 60,
            "state_batch_interval_low": 60,
        })
        relay = StateRelay(agent, state)

        start = time.perf_counter()
        await relay.start()
        elapsed = time.perf_counter() - start
        try:
            # 40,000 / 500 = exactly 80 chunks
            assert len(agent.sent) == 80, f"expected 80 chunks, got {len(agent.sent)}"

            # First chunk carries the snapshot flag; the rest do not.
            assert agent.sent[0][1].get("snapshot") is True
            for msg_type, payload, _ts in agent.sent[1:]:
                assert msg_type == STATE_BATCH
                assert "snapshot" not in payload

            # All 80 chunks together must total 40,000 changes.
            total = sum(len(p["changes"]) for _t, p, _ts in agent.sent)
            assert total == 40_000

            # Pacing should add ~ (80 - 1) * 5 ms = 395 ms of sleep.
            # Allow generous slack for slow CI: at least 200 ms.
            assert elapsed > 0.2, (
                f"snapshot completed in {elapsed:.3f}s — pacing did not engage"
            )

            # Successive sends should have non-trivial gaps. Measure the
            # median inter-send gap and require it to be at least 1 ms,
            # which is well above the noise floor on every reasonable host.
            gaps = [
                agent.sent[i + 1][2] - agent.sent[i][2]
                for i in range(len(agent.sent) - 1)
            ]
            gaps.sort()
            median = gaps[len(gaps) // 2]
            assert median >= 0.001, f"median chunk gap was only {median * 1000:.2f} ms"
        finally:
            await relay.stop()

    @pytest.mark.asyncio
    async def test_below_threshold_does_not_pace(self):
        """A snapshot under ``state_batch_pace_threshold`` keys ships in
        chunks back-to-back (no inter-chunk sleep)."""
        state = StateStore()
        events = EventBus()
        state.set_event_bus(events)

        for i in range(1_000):  # 2 chunks at max_size=500
            state.set(f"var.s{i}", i, source="seed")

        agent = _RecordingAgent(config={
            "state_batch_max_size": 500,
            "state_batch_pace_threshold": 2_000,
            "state_batch_pace_delay": 0.5,   # would dominate if it ran
            "state_batch_interval": 60,
            "state_batch_interval_child": 60,
            "state_batch_interval_low": 60,
        })
        relay = StateRelay(agent, state)

        start = time.perf_counter()
        await relay.start()
        elapsed = time.perf_counter() - start
        try:
            assert len(agent.sent) == 2
            # Without pacing, the elapsed time stays well under one pace
            # interval (0.5 s).
            assert elapsed < 0.25, (
                f"below-threshold snapshot took {elapsed:.3f}s — pacing engaged"
            )
        finally:
            await relay.stop()


# ---------------------------------------------------------------------------
# Cadence — low-priority flushes at slower interval; top stays snappy
# ---------------------------------------------------------------------------


class TestTierCadence:
    """Per-tier flush loops fire independently at their configured cadences."""

    @pytest.mark.asyncio
    async def test_low_priority_flushes_at_slow_interval(self):
        """Low-priority entries appended just after a flush do not ship
        until the slow-interval timer fires."""
        state = StateStore()
        events = EventBus()
        state.set_event_bus(events)

        agent = _RecordingAgent(
            config={
                "state_batch_interval": 60,        # park top
                "state_batch_interval_child": 60,  # park child
                "state_batch_interval_low": 0.2,   # the bucket under test
                "state_batch_max_size": 500,
                # Pace knobs irrelevant at this size, set them defensively.
                "state_batch_pace_threshold": 10_000,
                "state_batch_pace_delay": 0,
            },
            drivers={"ctrl1": _chazy_like_driver()},
        )
        relay = StateRelay(agent, state)

        await relay.start()
        try:
            # Drop the snapshot send so the test only inspects steady-state.
            agent.sent.clear()

            # Queue a low-priority change.
            relay._on_state_change(
                "device.ctrl1.encoder.005.source_ir", 0, 7, "drv",
            )
            assert len(relay._batches["low"]) == 1
            assert relay._batches["top"] == []

            # Before the low interval elapses, nothing should have shipped.
            await asyncio.sleep(0.08)
            assert agent.sent == [], (
                "low-priority entry shipped before slow interval expired"
            )

            # After the low interval, the flush task should have drained
            # the bucket exactly once.
            await asyncio.sleep(0.25)
            assert len(agent.sent) >= 1
            shipped_keys = [
                c["key"]
                for _t, payload, _ts in agent.sent
                for c in payload.get("changes", [])
            ]
            assert "device.ctrl1.encoder.005.source_ir" in shipped_keys
            assert relay._batches["low"] == []
        finally:
            await relay.stop()

    @pytest.mark.asyncio
    async def test_top_tier_flushes_regardless_of_low_backlog(self):
        """A growing low-priority backlog must not delay top-tier flushes."""
        state = StateStore()
        events = EventBus()
        state.set_event_bus(events)

        agent = _RecordingAgent(
            config={
                "state_batch_interval": 0.1,          # top flushes every 100 ms
                "state_batch_interval_child": 60,
                "state_batch_interval_low": 60,       # park low
                "state_batch_max_size": 500,
                "state_batch_pace_threshold": 10_000,
                "state_batch_pace_delay": 0,
            },
            drivers={"ctrl1": _chazy_like_driver()},
        )
        relay = StateRelay(agent, state)

        await relay.start()
        try:
            agent.sent.clear()

            # Stuff a thousand low-priority entries into the low bucket so
            # it has plenty of work that should NOT delay the top flush.
            for i in range(1, 1_001):
                relay._on_state_change(
                    f"device.ctrl1.encoder.{i:03d}.source_ir", 0, i, "drv",
                )
            assert len(relay._batches["low"]) == 1_000

            # Now drop a single top-tier change.
            relay._on_state_change("var.room_active", False, True, "drv")
            assert len(relay._batches["top"]) == 1

            # Wait for two top intervals; the top tier should have shipped
            # but the low tier must still hold the backlog.
            await asyncio.sleep(0.3)

            shipped_keys = [
                c["key"]
                for _t, payload, _ts in agent.sent
                for c in payload.get("changes", [])
            ]
            assert "var.room_active" in shipped_keys
            assert not any(k.startswith("device.ctrl1.encoder") for k in shipped_keys), (
                "low-priority backlog leaked into the top flush"
            )
            # The low backlog should still be parked.
            assert len(relay._batches["low"]) == 1_000
        finally:
            await relay.stop()

    @pytest.mark.asyncio
    async def test_child_tier_flushes_independently_of_top(self):
        """The child cadence is its own thing — top flushes don't carry
        child entries and vice versa."""
        state = StateStore()
        events = EventBus()
        state.set_event_bus(events)

        agent = _RecordingAgent(
            config={
                "state_batch_interval": 60,           # park top
                "state_batch_interval_child": 0.15,   # the bucket under test
                "state_batch_interval_low": 60,       # park low
                "state_batch_max_size": 500,
                "state_batch_pace_threshold": 10_000,
                "state_batch_pace_delay": 0,
            },
            drivers={"ctrl1": _chazy_like_driver()},
        )
        relay = StateRelay(agent, state)

        await relay.start()
        try:
            agent.sent.clear()

            # Drop a few default-priority child entries.
            relay._on_state_change(
                "device.ctrl1.encoder.001.signal_present", False, True, "drv",
            )
            relay._on_state_change(
                "device.ctrl1.encoder.002.signal_present", False, True, "drv",
            )
            assert len(relay._batches["child"]) == 2

            # Wait past one child interval.
            await asyncio.sleep(0.25)

            shipped_keys = [
                c["key"]
                for _t, payload, _ts in agent.sent
                for c in payload.get("changes", [])
            ]
            assert "device.ctrl1.encoder.001.signal_present" in shipped_keys
            assert "device.ctrl1.encoder.002.signal_present" in shipped_keys
            assert relay._batches["child"] == []
        finally:
            await relay.stop()

    @pytest.mark.asyncio
    async def test_dedup_keeps_only_last_value_per_key(self):
        """Repeated writes to the same key within one flush window collapse
        to the most recent value."""
        state = StateStore()
        events = EventBus()
        state.set_event_bus(events)

        agent = _RecordingAgent(config={
            "state_batch_interval": 0.1,
            "state_batch_interval_child": 60,
            "state_batch_interval_low": 60,
            "state_batch_max_size": 500,
            "state_batch_pace_threshold": 10_000,
            "state_batch_pace_delay": 0,
        })
        relay = StateRelay(agent, state)

        await relay.start()
        try:
            agent.sent.clear()
            for v in range(5):
                relay._on_state_change("var.churn", v - 1, v, "drv")
            await asyncio.sleep(0.25)

            # Find every change for var.churn across whatever shipped.
            churns = [
                c
                for _t, payload, _ts in agent.sent
                for c in payload.get("changes", [])
                if c["key"] == "var.churn"
            ]
            assert len(churns) == 1, f"expected one deduped entry, got {churns}"
            assert churns[0]["value"] == 4
        finally:
            await relay.stop()


# ---------------------------------------------------------------------------
# Initial-snapshot window — a change during the snapshot send must not be lost
# ---------------------------------------------------------------------------


class TestSnapshotWindowRace:
    """The relay subscribes before sending the snapshot, so a state change that
    lands while the snapshot is in flight (a driver poll-loop write) is buffered
    into a batch instead of falling between an already-captured snapshot and a
    not-yet-registered subscription."""

    @pytest.mark.asyncio
    async def test_change_during_snapshot_send_is_captured(self):
        state = StateStore()
        events = EventBus()
        state.set_event_bus(events)
        # A pre-existing key so the snapshot has content to send.
        state.set("device.d1.connected", True, source="seed")

        class _InjectingAgent(_RecordingAgent):
            """Fires a state write on the first send_message, mimicking a poll
            loop that sets a key after the snapshot was captured but before the
            flush loops run."""

            def __init__(self, relay_state):
                super().__init__(config={
                    # Long intervals: assert on the buffered batch, not a flush.
                    "state_batch_interval": 60,
                    "state_batch_interval_child": 60,
                    "state_batch_interval_low": 60,
                })
                self._relay_state = relay_state
                self._injected = False

            async def send_message(self, msg_type, payload):
                if not self._injected:
                    self._injected = True
                    self._relay_state.set("device.d1.volume", 42, source="poll")
                await super().send_message(msg_type, payload)

        agent = _InjectingAgent(state)
        relay = StateRelay(agent, state)

        await relay.start()
        try:
            pending = [
                e["key"] for bucket in relay._batches.values() for e in bucket
            ]
            assert "device.d1.volume" in pending, (
                "a change during the snapshot-send window was lost"
            )
        finally:
            await relay.stop()


# ---------------------------------------------------------------------------
# Tier cache eviction — deleted keys must not accumulate
# ---------------------------------------------------------------------------


class TestTierCacheEviction:
    """A deleted key is evicted from the tier cache so it tracks only live keys
    and can't grow without bound under high child-entity churn."""

    @staticmethod
    def _agent() -> _RecordingAgent:
        return _RecordingAgent(config={
            "state_batch_interval": 60,
            "state_batch_interval_child": 60,
            "state_batch_interval_low": 60,
        })

    @pytest.mark.asyncio
    async def test_deleted_key_evicted_but_delete_still_relayed(self):
        state = StateStore()
        events = EventBus()
        state.set_event_bus(events)
        relay = StateRelay(self._agent(), state)

        await relay.start()
        try:
            # Setting classifies + caches the key.
            state.set("device.d1.route.1.dest", "hdmi1", source="drv")
            assert "device.d1.route.1.dest" in relay._tier_cache

            state.delete("device.d1.route.1.dest", source="drv")
            # The deleted key must not linger in the cache...
            assert "device.d1.route.1.dest" not in relay._tier_cache
            # ...but its deletion is still routed into a batch for the cloud.
            pending = [e for bucket in relay._batches.values() for e in bucket]
            assert any(
                e["key"] == "device.d1.route.1.dest" and e.get("deleted")
                for e in pending
            )
        finally:
            await relay.stop()

    @pytest.mark.asyncio
    async def test_cache_bounded_under_register_deregister_churn(self):
        state = StateStore()
        events = EventBus()
        state.set_event_bus(events)
        relay = StateRelay(self._agent(), state)

        await relay.start()
        try:
            for i in range(500):
                key = f"device.d1.preset.{i}.name"
                state.set(key, f"p{i}", source="drv")
                state.delete(key, source="drv")
            leaked = [
                k for k in relay._tier_cache
                if k.startswith("device.d1.preset.")
            ]
            assert leaked == [], f"tier cache leaked {len(leaked)} deleted keys"
        finally:
            await relay.stop()


class TestFlushLoopResilience:
    """A single failed flush must not end a tier's relay loop (V-LC-009)."""

    @pytest.mark.asyncio
    async def test_flush_bucket_loop_survives_flush_exception(self):
        relay = StateRelay(_RecordingAgent(), StateStore())
        relay._running = True
        calls: list[str] = []

        async def flaky_flush(bucket):
            calls.append(bucket)
            if len(calls) == 1:
                raise RuntimeError("serialization boom")
            relay._running = False  # stop once the loop has proven it survived

        relay._flush_bucket = flaky_flush
        # Pre-fix the first exception escaped and permanently ended the loop.
        await asyncio.wait_for(
            relay._flush_bucket_loop("top", "state_batch_interval", 0.01),
            timeout=2,
        )
        assert len(calls) >= 2
