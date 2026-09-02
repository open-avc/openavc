"""Hardening regression tests for BaseDriver.

Covers the audit findings closed in this group:
  H-049 disconnect cleanup tasks keep a strong ref (no GC orphan)
  M-090 serial params coerced + validated before pyserial
  M-091 max_missed_polls <= 0 clamped so a healthy device isn't marked offline
  M-092 'float'/'number' state vars default to 0.0, not ''
  M-093 a non-numeric `min` doesn't crash driver instantiation
  M-094 a watchdog/transport disconnect closes the transport (no leaked socket)
  M-095 poll_children drops a stale write for a child re-registered mid-poll
  L-061 poll_children applies the whole poll in one atomic batch
"""

import asyncio
from typing import Any

import pytest

from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.base import BaseDriver


def _mk(cls, *, device_id="dev", config=None):
    return cls(
        device_id=device_id,
        config=config or {},
        state=StateStore(),
        events=EventBus(),
    )


async def _wait_until(predicate, *, timeout=5.0):
    """Wait for `predicate` to hold, up to `timeout` seconds.

    The polling tests below drive a real poll loop at a 10ms interval, so how
    many polls land inside a fixed sleep is a fact about the machine rather
    than about the code under test. Windows schedules on a ~15.6ms tick and a
    loaded runner starves the loop further, which is enough to fail a sleep
    sized on a developer box -- and it did, on the v0.32.0 release build.

    Waiting on the condition instead is both faster when the machine is quick
    and correct when it is not. The generous ceiling only decides how long a
    genuine failure takes to report.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() >= deadline:
            return False
        await asyncio.sleep(0.005)
    return True


class _FakeTransport:
    def __init__(self):
        self.closed = False
        self.connected = True

    async def close(self):
        self.closed = True
        self.connected = False


class _BareDriver(BaseDriver):
    DRIVER_INFO: dict[str, Any] = {
        "id": "bare", "name": "Bare", "transport": "tcp",
        "state_variables": {}, "commands": {},
    }

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        return None


class _PollDriver(BaseDriver):
    DRIVER_INFO: dict[str, Any] = {
        "id": "poll", "name": "Poll", "transport": "tcp",
        "state_variables": {}, "commands": {},
    }

    def __init__(self, *a: Any, **k: Any) -> None:
        super().__init__(*a, **k)
        self.poll_count = 0
        self.should_fail = False

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        return None

    async def poll(self) -> None:
        self.poll_count += 1
        if self.should_fail:
            raise ConnectionError("unreachable")


class _ChildDriver(BaseDriver):
    DRIVER_INFO: dict[str, Any] = {
        "id": "child_drv", "name": "Child", "transport": "tcp",
        "state_variables": {}, "commands": {},
        "child_entity_types": {
            "encoder": {
                "id_format": {"type": "integer", "min": 1, "max": 762, "pad_width": 3},
                "state_variables": {"name": {"type": "string"}},
            },
        },
    }

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        return None


# ── H-049 / M-094: disconnect cleanup keeps a strong ref + closes transport ──


class TestDisconnectCleanup:

    async def test_disconnect_keeps_strong_ref_and_runs_cleanup(self):
        drv = _mk(_BareDriver, device_id="d1")
        drv._connected = True
        fake = _FakeTransport()
        drv.transport = fake

        fired: list[bool] = []

        async def _on_disc(*_a):
            fired.append(True)

        drv.events.on("device.disconnected.d1", _on_disc)

        drv._handle_transport_disconnect()

        # H-049: the cleanup task is strongly referenced while pending.
        assert len(drv._bg_tasks) == 1
        task = next(iter(drv._bg_tasks))
        await task

        assert drv._connected is False
        assert fake.closed is True          # M-094: transport closed
        assert drv.transport is None        # M-094: ref nulled for reconnect
        assert fired == [True]              # H-049: emit actually ran
        assert len(drv._bg_tasks) == 0     # done-callback drained the set

    async def test_disconnect_without_running_loop_is_safe(self):
        # Sanity: a disconnect with no loop logs and returns, no crash.
        drv = _mk(_BareDriver)
        drv._connected = True
        # We're in a loop here, so just verify the handler path doesn't raise
        # and still flips connected synchronously.
        drv._handle_transport_disconnect()
        assert drv._connected is False
        for t in list(drv._bg_tasks):
            await t

    async def test_watchdog_disconnect_closes_transport(self):
        drv = _mk(_PollDriver, config={"max_missed_polls": 2})
        drv._connected = True
        drv.set_state("connected", True)
        fake = _FakeTransport()
        drv.transport = fake
        drv.should_fail = True

        await drv.start_polling(0.01)
        # The watchdog tears down in a background task, and it nulls the live
        # ref before awaiting the transport's own close(), so `closed` is the
        # last effect to land. Wait for the whole teardown rather than for the
        # first sign of it, then let the asserts below name what is missing.
        await _wait_until(
            lambda: fake.closed
            and drv.transport is None
            and drv.get_state("connected") is False
        )
        await drv.stop_polling()

        assert drv.get_state("connected") is False
        # M-094: the watchdog path closed + released the transport.
        assert fake.closed is True
        assert drv.transport is None


# ── M-090: serial parameter coercion / validation ──


class TestSerialParamCoercion:

    def test_defaults(self):
        # (baudrate, bytesize, parity, stopbits, rtscts, xonxoff); no flow control.
        assert BaseDriver._coerce_serial_params({}) == (9600, 8, "N", 1, False, False)

    def test_string_values_coerced(self):
        # JSON has no int/float — strings from a hand-edited .avc must work.
        assert BaseDriver._coerce_serial_params(
            {"baudrate": "19200", "bytesize": "7", "parity": "e", "stopbits": "1.5"}
        ) == (19200, 7, "E", 1.5, False, False)

    def test_stopbits_two_is_int(self):
        _, _, _, stopbits, _, _ = BaseDriver._coerce_serial_params({"stopbits": "2"})
        assert stopbits == 2 and isinstance(stopbits, int)

    @pytest.mark.parametrize("flow,rtscts,xonxoff", [
        ("none", False, False),
        ("hardware", True, False),    # the UI's "Hardware (RTS/CTS)" value
        ("software", False, True),
        ("xonxoff", False, True),
        ("rts/cts", True, False),
        ("unrecognised", False, False),  # a stray value must not raise / connect-block
    ])
    def test_flow_control_maps_to_pyserial_flags(self, flow, rtscts, xonxoff):
        _, _, _, _, got_rtscts, got_xonxoff = BaseDriver._coerce_serial_params(
            {"flow_control": flow}
        )
        assert (got_rtscts, got_xonxoff) == (rtscts, xonxoff)

    @pytest.mark.parametrize("config,match", [
        ({"bytesize": 3}, "bytesize"),
        ({"bytesize": "x"}, "bytesize"),
        ({"parity": "X"}, "parity"),
        ({"stopbits": 3}, "stopbits"),
        ({"stopbits": "nope"}, "stopbits"),
        ({"baudrate": "fast"}, "baudrate"),
    ])
    def test_invalid_values_raise_clear_error(self, config, match):
        with pytest.raises(ValueError, match=match):
            BaseDriver._coerce_serial_params(config)


# ── M-091: max_missed_polls clamp ──


class TestMaxMissedPollsClamp:

    async def test_zero_keeps_healthy_device_connected(self):
        # max_missed_polls=0 must NOT disconnect a device that polls cleanly
        # (the bug: 0 >= 0 fired the watchdog right after the first success).
        drv = _mk(_PollDriver, config={"max_missed_polls": 0})
        drv._connected = True
        drv.set_state("connected", True)

        await drv.start_polling(0.01)
        # Wait for the polls themselves rather than for a wall-clock window:
        # the claim is that two clean polls do not disconnect the device, so
        # the two polls have to have happened before it means anything.
        assert await _wait_until(lambda: drv.poll_count >= 2)
        await drv.stop_polling()

        assert drv.get_state("connected") is True

    async def test_non_numeric_falls_back_to_default(self):
        drv = _mk(_PollDriver, config={"max_missed_polls": "bogus"})
        drv._connected = True
        drv.set_state("connected", True)
        drv.should_fail = True

        await drv.start_polling(0.01)

        # Falls back to the default of 3 and the watchdog still fires (rather
        # than crashing the poll loop on `0 >= "bogus"`).
        assert await _wait_until(lambda: drv.get_state("connected") is False)
        await drv.stop_polling()


# ── M-092 / M-093: numeric state-var defaults ──


class TestNumericDefaults:

    def test_float_and_number_defaults_are_numeric(self):
        class _VarDrv(BaseDriver):
            DRIVER_INFO = {
                "id": "var", "transport": "tcp", "commands": {},
                "state_variables": {
                    "level": {"type": "float"},
                    "gain": {"type": "number", "min": 5},
                    "volume": {"type": "integer", "min": 2},
                },
            }

            async def send_command(self, c, p=None):
                return None

        drv = _mk(_VarDrv)
        # M-092: float defaults to 0.0, not '' — a numeric consumer pre-poll
        # must not see a string.
        assert drv.get_state("level") == 0.0
        assert isinstance(drv.get_state("level"), float)
        assert drv.get_state("gain") == 5.0
        assert isinstance(drv.get_state("gain"), float)
        assert drv.get_state("volume") == 2
        assert isinstance(drv.get_state("volume"), int)

    def test_non_numeric_min_does_not_crash_instantiation(self):
        class _BadMinDrv(BaseDriver):
            DRIVER_INFO = {
                "id": "badmin", "transport": "tcp", "commands": {},
                "state_variables": {
                    "ratio": {"type": "number", "min": "low"},
                    "count": {"type": "integer", "min": "nope"},
                },
            }

            async def send_command(self, c, p=None):
                return None

        # M-093: instantiation must not raise on a non-numeric min.
        drv = _mk(_BadMinDrv)
        assert drv.get_state("ratio") == 0.0
        assert drv.get_state("count") == 0

    def test_default_for_var_def_handles_float_and_bad_min(self):
        assert BaseDriver._default_for_var_def({"type": "float"}) == 0.0
        assert BaseDriver._default_for_var_def({"type": "number", "min": 3}) == 3.0
        assert BaseDriver._default_for_var_def({"type": "number", "min": "x"}) == 0.0
        assert BaseDriver._default_for_var_def({"type": "integer", "min": "x"}) == 0


# ── M-095 / L-061: poll_children atomicity + ABA guard ──


class TestPollChildren:

    async def test_drops_stale_write_for_reregistered_child(self):
        drv = _mk(_ChildDriver, device_id="ctrl")
        drv.register_child("encoder", 1, initial_state={"name": "orig"})

        async def fetch(batch_ids):
            # Simulate a concurrent refresh_children mid-poll: deregister then
            # re-register child 1 (which resets its state), then return the
            # stale detail captured before the reset.
            drv.deregister_child("encoder", 1)
            drv.register_child("encoder", 1, initial_state={"name": "reset"})
            return {1: {"name": "stale"}}

        await drv.poll_children("encoder", fetch, batch_size=10, inter_batch_delay=0)

        # M-095: the stale write is dropped; the reset value stands.
        assert drv.state.get("device.ctrl.encoder.001.name") == "reset"

    async def test_drops_results_for_unregistered_ids(self):
        drv = _mk(_ChildDriver, device_id="ctrl")
        drv.register_child("encoder", 1)

        async def fetch(batch_ids):
            return {1: {"name": "Real"}, 999: {"name": "Ghost"}}

        await drv.poll_children("encoder", fetch, batch_size=10, inter_batch_delay=0)
        assert drv.state.get("device.ctrl.encoder.001.name") == "Real"
        assert "device.ctrl.encoder.999.name" not in drv.state.snapshot()

    async def test_applies_once_per_poll(self):
        drv = _mk(_ChildDriver, device_id="ctrl")
        for i in range(1, 11):
            drv.register_child("encoder", i)

        apply_sizes: list[int] = []
        original = drv.set_children_state_batch

        def spy(updates):
            apply_sizes.append(len(updates))
            return original(updates)

        drv.set_children_state_batch = spy

        async def fetch(batch_ids):
            return {i: {"name": f"E{i}"} for i in batch_ids}

        await drv.poll_children("encoder", fetch, batch_size=3, inter_batch_delay=0)

        # L-061: one atomic apply for the whole poll, not one per batch.
        assert apply_sizes == [10]
        for i in range(1, 11):
            assert drv.state.get(f"device.ctrl.encoder.{i:03d}.name") == f"E{i}"


class _SeededDriver(BaseDriver):
    DRIVER_INFO: dict[str, Any] = {
        "id": "seeded", "name": "Seeded", "transport": "tcp",
        "state_variables": {
            "level": {"type": "integer", "min": 5},
            "label": {"type": "string"},
        },
        "commands": {},
    }

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        return None


class TestDeleteState:
    """delete_state removes a seeded key from the device namespace entirely
    (for drivers that narrow their surface at runtime and need to drop the
    defaults _init_state_variables seeded for the removed variables)."""

    def test_removes_seeded_key(self):
        drv = _mk(_SeededDriver)
        assert drv.state.get("device.dev.level") == 5
        drv.delete_state("level")
        assert "device.dev.level" not in drv.state.snapshot()
        # The other seeded key is untouched.
        assert drv.state.get("device.dev.label") == ""

    def test_missing_key_is_a_no_op(self):
        drv = _mk(_SeededDriver)
        drv.delete_state("never_existed")  # must not raise
