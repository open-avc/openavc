"""The simulator's two ports go through the layered config.

Both, not just the UI port: two OpenAVC instances that shared the per-device
range would collide on the device listeners even with distinct UI ports, so
moving one without the other does not achieve the thing the setting is for.
"""

from __future__ import annotations

import pytest

from server.core.simulation import (
    SIMULATOR_DEVICE_PORT_BASE,
    SIMULATOR_UI_PORT,
    _port_in_use_message,
    simulator_device_port_base,
    simulator_ui_port,
)
from simulator.engine import PORT_RANGE_START, PORT_RANGE_WIDTH, SimulatorManager


def test_defaults_match_the_shipped_numbers():
    assert simulator_ui_port() == SIMULATOR_UI_PORT
    assert simulator_device_port_base() == SIMULATOR_DEVICE_PORT_BASE


def test_env_override_moves_the_ui_port(monkeypatch):
    monkeypatch.setenv("OPENAVC_SIMULATOR_UI_PORT", "29500")
    from server import system_config

    system_config.reset_system_config()
    try:
        assert simulator_ui_port() == 29500
    finally:
        monkeypatch.delenv("OPENAVC_SIMULATOR_UI_PORT", raising=False)
        system_config.reset_system_config()


def test_env_override_moves_the_device_port_base(monkeypatch):
    monkeypatch.setenv("OPENAVC_SIMULATOR_DEVICE_PORT_BASE", "29000")
    from server import system_config

    system_config.reset_system_config()
    try:
        assert simulator_device_port_base() == 29000
    finally:
        monkeypatch.delenv("OPENAVC_SIMULATOR_DEVICE_PORT_BASE", raising=False)
        system_config.reset_system_config()


def test_a_junk_value_falls_back_rather_than_crashing_start(monkeypatch):
    """A bad system.json value must not make Start Simulation raise.

    Falling back keeps a typo'd port from taking simulation away entirely,
    which is a worse outcome than ignoring the setting.
    """
    monkeypatch.setenv("OPENAVC_SIMULATOR_UI_PORT", "not-a-port")
    from server import system_config

    system_config.reset_system_config()
    try:
        assert simulator_ui_port() == SIMULATOR_UI_PORT
    finally:
        monkeypatch.delenv("OPENAVC_SIMULATOR_UI_PORT", raising=False)
        system_config.reset_system_config()


def test_the_section_is_in_defaults_so_it_round_trips_through_the_api():
    """system.json PATCH only writes keys already present in the section."""
    from server.system_config import DEFAULTS

    assert DEFAULTS["simulation"]["ui_port"] == 19500
    assert DEFAULTS["simulation"]["device_port_base"] == 19000


def test_port_in_use_message_names_the_configured_port():
    message = _port_in_use_message(29500)
    assert "29500" in message
    assert "19500" not in message


def test_port_in_use_message_points_at_the_setting():
    """The message has to say the collision is fixable, not just report it."""
    message = _port_in_use_message(19500)
    assert "system.json" in message
    assert "simulation" in message


@pytest.mark.parametrize("base", [19000, 29000])
def test_device_ports_are_allocated_from_the_configured_base(base):
    manager = SimulatorManager(port_range_start=base)
    first = manager._allocate_port()
    manager._allocated_ports.add(first)
    second = manager._allocate_port()
    assert first == base
    assert second == base + 1


def test_two_managers_on_different_bases_never_hand_out_the_same_port():
    """The whole point: two instances can simulate at once."""
    a = SimulatorManager(port_range_start=19000)
    b = SimulatorManager(port_range_start=29000)
    a_ports = set()
    b_ports = set()
    for _ in range(20):
        p = a._allocate_port()
        a._allocated_ports.add(p)
        a_ports.add(p)
        q = b._allocate_port()
        b._allocated_ports.add(q)
        b_ports.add(q)
    assert not (a_ports & b_ports)


def test_moving_the_base_moves_the_whole_window():
    """The range is a width, not a second absolute number."""
    manager = SimulatorManager(port_range_start=29000)
    assert manager._port_start == 29000
    assert manager._port_end == 29000 + PORT_RANGE_WIDTH


def test_default_manager_is_unchanged():
    manager = SimulatorManager()
    assert manager._port_start == PORT_RANGE_START
