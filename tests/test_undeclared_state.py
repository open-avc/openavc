"""A driver that writes state it never declared is told so.

The finding this closes: a driver declaring one state variable and writing
eight created all eight state keys with no error, no warning and no log line,
while the *child* half of the same API had been strict about exactly this
since it was added. The platform was strict about the surface it grew recently
and silent about the original one.

The posture here is deliberately not the child posture, and the asymmetry is
the design:

    undeclared child prop      raise      -- the write has nowhere to go
    unregistered child         skip+warn  -- the key would be an orphan
    undeclared flat state var  warn       -- the write lands and is correct;
                                            only the declaration is missing

Raising on the flat one at runtime would take a working device offline over an
author's omission, and the person who pays for that is the end user in the
room, not the author. So it warns where a device is running and raises where
an author is iterating -- their test suite and ours, both under
OPENAVC_STRICT_DRIVER_STATE.

The suite runs with that env var on (tests/conftest.py), so tests that want
the warn path turn it off explicitly.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from server.core.event_bus import EventBus
from server.core.state_store import StateStore
from server.drivers.base import (
    STRICT_DRIVER_STATE_ENV,
    BaseDriver,
    UndeclaredStateError,
    strict_driver_state,
)


class _Driver(BaseDriver):
    DRIVER_INFO: dict[str, Any] = {
        "id": "acme_zone_amp",
        "name": "Acme Zone Amp",
        "transport": "tcp",
        "state_variables": {
            "output_1_mute": {"type": "boolean"},
            "level": {"type": "integer"},
        },
        "commands": {},
    }

    async def send_command(self, command, params=None):
        return True


def _mk(device_id="amp_1"):
    return _Driver(device_id, {}, StateStore(), EventBus())


def _reports(caplog) -> list[str]:
    """Just this check's messages. caplog's handler sits on the root logger,
    so everything the state store logs at DEBUG lands in the same list."""
    return [
        r.getMessage()
        for r in caplog.records
        if r.name == "server.drivers.base" and r.levelno >= logging.WARNING
    ]


@pytest.fixture
def lenient(monkeypatch):
    """Runtime posture: warn, never raise."""
    monkeypatch.setenv(STRICT_DRIVER_STATE_ENV, "0")
    assert not strict_driver_state()


@pytest.fixture
def strict(monkeypatch):
    """Author posture: raise."""
    monkeypatch.setenv(STRICT_DRIVER_STATE_ENV, "1")
    assert strict_driver_state()


# ── Runtime: the write lands, and it is reported ────────────────────────────

def test_undeclared_write_still_lands(lenient, caplog):
    """The device keeps working. That is the whole reason this is a warning."""
    drv = _mk()
    with caplog.at_level(logging.WARNING, logger="server.drivers.base"):
        drv.set_state("output_5_mute", True)
    assert drv.state.get("device.amp_1.output_5_mute") is True


def test_undeclared_write_warns_and_names_what_is_wrong(lenient, caplog):
    drv = _mk()
    with caplog.at_level(logging.WARNING, logger="server.drivers.base"):
        drv.set_state("output_5_mute", True)
    (msg,) = _reports(caplog)
    assert "amp_1" in msg                      # which device
    assert "acme_zone_amp" in msg              # which driver
    assert "output_5_mute" in msg              # which key
    assert 'DRIVER_INFO["state_variables"]' in msg   # where to fix it


def test_warns_once_per_key_not_once_per_poll(lenient, caplog):
    """A driver that writes an undeclared key writes it every poll cycle. A
    per-write warning is a log flood, and a flooded channel is an ignored one.
    """
    drv = _mk()
    with caplog.at_level(logging.WARNING, logger="server.drivers.base"):
        for _ in range(50):
            drv.set_state("output_5_mute", True)
            drv.set_state("output_6_mute", False)
    keys = sorted(
        m.split("wrote state '")[1].split("'")[0] for m in _reports(caplog)
    )
    assert keys == ["output_5_mute", "output_6_mute"]


def test_declared_write_is_silent(lenient, caplog):
    drv = _mk()
    with caplog.at_level(logging.WARNING, logger="server.drivers.base"):
        drv.set_state("output_1_mute", True)
        drv.set_state("level", 7)
    assert _reports(caplog) == []


def test_platform_managed_connected_is_never_an_authoring_error(lenient, caplog):
    """`connected` is seeded by the platform on every driver and thereafter
    owned by the DeviceManager. Asking authors to declare it would make the
    warning fire on every driver ever written, which is how a check gets
    turned off.
    """
    with caplog.at_level(logging.WARNING, logger="server.drivers.base"):
        drv = _mk()                       # __init__ seeds it
        drv.set_state("connected", True)
    assert _reports(caplog) == []
    assert drv.state.get("device.amp_1.connected") is True


def test_seeding_declared_defaults_is_silent(lenient, caplog):
    """_init_state_variables writes every declared variable through set_state;
    if that tripped the check, every driver would warn at construction."""
    with caplog.at_level(logging.WARNING, logger="server.drivers.base"):
        _mk()
    assert _reports(caplog) == []


# ── Strict mode: where the author is iterating ──────────────────────────────

def test_strict_mode_raises(strict):
    drv = _mk()
    with pytest.raises(UndeclaredStateError) as exc:
        drv.set_state("output_5_mute", True)
    assert "output_5_mute" in str(exc.value)
    assert "acme_zone_amp" in str(exc.value)
    assert STRICT_DRIVER_STATE_ENV in str(exc.value)


def test_strict_mode_raises_on_every_write_not_just_the_first(strict):
    """The warn-once bookkeeping must not swallow the second raise -- an
    author fixing one call site would otherwise see the next one pass.
    """
    drv = _mk()
    for _ in range(3):
        with pytest.raises(UndeclaredStateError):
            drv.set_state("output_5_mute", True)


def test_strict_mode_leaves_declared_writes_alone(strict):
    drv = _mk()
    drv.set_state("level", 3)
    assert drv.state.get("device.amp_1.level") == 3


def test_undeclared_state_error_is_a_value_error(strict):
    """Existing `except ValueError` handlers around driver calls keep working."""
    assert issubclass(UndeclaredStateError, ValueError)


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_strict_env_accepts_the_usual_truthy_spellings(monkeypatch, value):
    monkeypatch.setenv(STRICT_DRIVER_STATE_ENV, value)
    assert strict_driver_state()


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "  "])
def test_strict_env_off_by_default_shapes(monkeypatch, value):
    monkeypatch.setenv(STRICT_DRIVER_STATE_ENV, value)
    assert not strict_driver_state()


def test_strict_is_read_per_call_not_cached_at_import(monkeypatch):
    """The flag exists to be turned on around one driver. An import-time
    constant could not be, and the Builder's Test tab needs exactly that.
    """
    drv = _mk()
    monkeypatch.setenv(STRICT_DRIVER_STATE_ENV, "0")
    drv.set_state("output_9_mute", True)
    monkeypatch.setenv(STRICT_DRIVER_STATE_ENV, "1")
    with pytest.raises(UndeclaredStateError):
        drv.set_state("output_9_mute", True)


# ── The batch peer ──────────────────────────────────────────────────────────

def test_set_states_reports_each_undeclared_key(lenient, caplog):
    drv = _mk()
    with caplog.at_level(logging.WARNING, logger="server.drivers.base"):
        drv.set_states({"level": 1, "output_5_mute": True, "output_6_mute": False})
    assert len(_reports(caplog)) == 2
    assert drv.state.get("device.amp_1.output_5_mute") is True


def test_set_states_is_whole_or_nothing_under_strict(strict):
    """set_child_state_batch validates every prop before writing any, so one
    bad key aborts the batch rather than half-applying it. The flat batch
    follows the same rule -- a half-applied batch is worse than a failed one.
    """
    drv = _mk()
    with pytest.raises(UndeclaredStateError):
        drv.set_states({"level": 42, "output_5_mute": True})
    assert drv.state.get("device.amp_1.level") == 0     # seeded default, untouched


# ── The asymmetry itself, pinned ────────────────────────────────────────────

class _ChildDriver(BaseDriver):
    DRIVER_INFO: dict[str, Any] = {
        "id": "acme_zone_amp_children",
        "name": "Acme Zone Amp",
        "transport": "tcp",
        "state_variables": {"zone_count": {"type": "integer"}},
        "child_entity_types": {
            "zone": {
                "label": "Zone",
                "id_format": {"type": "integer", "min": 1, "max": 32},
                "state_variables": {"level": {"type": "number"}},
            },
        },
        "commands": {},
    }

    async def send_command(self, command, params=None):
        return True


def test_the_three_postures_are_deliberate(lenient, caplog):
    """One test holding the whole decision, so a later session that decides
    the flat case "should just raise like the child case" has to read why not.
    """
    drv = _ChildDriver("amp_2", {}, StateStore(), EventBus())
    drv.register_child("zone", 1)

    # A child prop the type never declared: the key would be an orphan.
    with pytest.raises(ValueError, match="not declared"):
        drv.set_child_state("zone", 1, "not_a_prop", 1)

    # An unregistered child: nothing lists it, so the write is dropped.
    with caplog.at_level(logging.WARNING, logger="server.drivers.base"):
        drv.set_child_state("zone", 99, "level", 1.0)
    assert drv.state.get("device.amp_2.zone.99.level") is None

    # A flat key: it lands, correct and live, and is reported once.
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="server.drivers.base"):
        drv.set_state("firmware", "1.2.3")
    assert drv.state.get("device.amp_2.firmware") == "1.2.3"
    assert len(_reports(caplog)) == 1
