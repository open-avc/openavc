"""Tests for serial-driver simulation redirect (transport flip serial→tcp).

The device simulator has no serial server — serial drivers are simulated over a
TCP loopback stand-in. So when a device is pointed at the simulator, a serial
driver's transport must be flipped to ``tcp`` (host/port already point at the
sim), and the original transport restored when simulation stops. These tests
exercise that transport handling with a synthetic driver (no real vendor).
"""

import pytest

from openavc.core.simulation import SimulationManager


# ── Fakes ──────────────────────────────────────────────────────────────────

class _FakeDriver:
    """Minimal driver stand-in with a config dict and a DRIVER_INFO transport.

    ``transport`` seeds DRIVER_INFO["transport"] (the driver default);
    ``config_transport`` seeds an explicit device-config override (or leaves it
    absent when None), mirroring BaseDriver's resolution order.
    """

    def __init__(self, transport="serial", host="10.0.0.5", port=4001,
                 config_transport=None):
        self.DRIVER_INFO = {"transport": transport}
        self.config = {"host": host, "port": port}
        if config_transport is not None:
            self.config["transport"] = config_transport


class _FakeDeviceManager:
    def __init__(self, devices):
        self._devices = devices
        self.reconnected: list[str] = []

    async def reconnect_device(self, device_id):
        self.reconnected.append(device_id)


class _FakeEngine:
    def __init__(self, dm):
        self.devices = dm


def _manager(dm) -> SimulationManager:
    return SimulationManager(engine=_FakeEngine(dm))


# ── _driver_transport_needs_tcp_stand_in: resolution order ───────────────────────────

def test_transport_is_serial_from_driver_default():
    assert SimulationManager._driver_transport_needs_tcp_stand_in(_FakeDriver("serial"))
    assert not SimulationManager._driver_transport_needs_tcp_stand_in(_FakeDriver("tcp"))


def test_config_transport_overrides_driver_default():
    # Device config overriding a serial driver to tcp → not serial.
    d = _FakeDriver("serial", config_transport="tcp")
    assert not SimulationManager._driver_transport_needs_tcp_stand_in(d)
    # Device config overriding a tcp-default driver to serial → serial.
    d2 = _FakeDriver("tcp", config_transport="serial")
    assert SimulationManager._driver_transport_needs_tcp_stand_in(d2)


# ── _apply_sim_redirect / _restore_original_config ──────────────────────────

def test_serial_redirect_flips_transport_to_tcp_and_restores():
    mgr = _manager(_FakeDeviceManager({}))
    driver = _FakeDriver("serial", host="10.0.0.5", port=4001)  # no config transport

    mgr._apply_sim_redirect(driver, "dev1", 19003)

    assert driver.config["host"] == "127.0.0.1"
    assert driver.config["port"] == 19003
    assert driver.config["transport"] == "tcp"
    # Original transport was unset → saved as None.
    assert mgr._original_configs["dev1"]["transport"] is None

    mgr._restore_original_config(driver, mgr._original_configs["dev1"])

    assert driver.config["host"] == "10.0.0.5"
    assert driver.config["port"] == 4001
    # The override we added is removed so the DRIVER_INFO serial default returns.
    assert "transport" not in driver.config


def test_tcp_driver_redirect_leaves_transport_untouched():
    mgr = _manager(_FakeDeviceManager({}))
    driver = _FakeDriver("tcp", host="10.0.0.9", port=5000)

    mgr._apply_sim_redirect(driver, "dev2", 19007)

    assert driver.config["host"] == "127.0.0.1"
    assert driver.config["port"] == 19007
    # No transport override added for a driver the sim serves directly.
    assert "transport" not in driver.config
    assert mgr._original_configs["dev2"]["transport"] is None


def test_https_device_redirect_flips_ssl_off_when_the_sim_serves_plain_http():
    # A simulator that doesn't terminate TLS can only be reached in the clear,
    # so the device's ssl flag is flipped off for the duration and restored
    # exactly afterwards.
    mgr = _manager(_FakeDeviceManager({}))
    driver = _FakeDriver("http", host="10.0.0.7", port=4003)
    driver.config["ssl"] = True

    mgr._apply_sim_redirect(driver, "dev4", 19011)
    assert driver.config["ssl"] is False
    assert mgr._original_configs["dev4"]["ssl"] is True

    mgr._restore_original_config(driver, mgr._original_configs["dev4"])
    assert driver.config["ssl"] is True
    assert driver.config["host"] == "10.0.0.7"


def test_https_device_keeps_its_scheme_when_the_sim_serves_tls():
    # An HTTPS-only device's simulator terminates TLS, so the driver connects
    # the way it connects in the field. What gets turned off is verification —
    # the simulator's certificate is an ephemeral self-signed one.
    mgr = _manager(_FakeDeviceManager({}))
    driver = _FakeDriver("http", host="10.0.0.7", port=4003)
    driver.config["ssl"] = True
    mgr._sim_tls["dev6"] = True

    mgr._apply_sim_redirect(driver, "dev6", 19015)
    assert driver.config["ssl"] is True
    assert driver.config["verify_ssl"] is False
    # The device never set verify_ssl, so restore removes the override rather
    # than leaving verification off against the real hardware.
    assert mgr._original_configs["dev6"]["verify_ssl"] is None

    mgr._restore_original_config(driver, mgr._original_configs["dev6"])
    assert driver.config["ssl"] is True
    assert "verify_ssl" not in driver.config


def test_device_that_verifies_certs_gets_its_setting_back():
    mgr = _manager(_FakeDeviceManager({}))
    driver = _FakeDriver("http", host="10.0.0.7", port=4003)
    driver.config["ssl"] = True
    driver.config["verify_ssl"] = True
    mgr._sim_tls["dev7"] = True

    mgr._apply_sim_redirect(driver, "dev7", 19017)
    assert driver.config["verify_ssl"] is False

    mgr._restore_original_config(driver, mgr._original_configs["dev7"])
    assert driver.config["verify_ssl"] is True


def test_plain_http_device_redirect_leaves_ssl_absent():
    mgr = _manager(_FakeDeviceManager({}))
    driver = _FakeDriver("http", host="10.0.0.8", port=80)

    mgr._apply_sim_redirect(driver, "dev5", 19013)
    assert "ssl" not in driver.config

    mgr._restore_original_config(driver, mgr._original_configs["dev5"])
    assert "ssl" not in driver.config


def test_explicit_transport_override_is_preserved_on_restore():
    # A serial driver whose device config explicitly pins transport keeps that
    # exact value on restore (not deleted).
    mgr = _manager(_FakeDeviceManager({}))
    driver = _FakeDriver("serial", host="10.0.0.5", port=4001,
                         config_transport="tcp")

    mgr._apply_sim_redirect(driver, "dev3", 19009)
    # Effective transport was tcp (config wins) → no re-flip needed, stays tcp.
    assert driver.config["transport"] == "tcp"
    assert mgr._original_configs["dev3"]["transport"] == "tcp"

    mgr._restore_original_config(driver, mgr._original_configs["dev3"])
    assert driver.config["transport"] == "tcp"
    assert driver.config["host"] == "10.0.0.5"


# ── Integration through the redirect/restore loops ──────────────────────────

@pytest.mark.asyncio
async def test_redirect_and_restore_connections_round_trip():
    driver = _FakeDriver("serial", host="10.0.0.5", port=4001)
    dm = _FakeDeviceManager({"dev1": driver})
    mgr = _manager(dm)
    mgr._sim_ports = {"dev1": 19011}

    await mgr._redirect_connections()
    assert driver.config["transport"] == "tcp"
    assert driver.config["host"] == "127.0.0.1"
    assert driver.config["port"] == 19011
    assert "dev1" in dm.reconnected

    await mgr._restore_connections()
    assert "transport" not in driver.config
    assert driver.config["host"] == "10.0.0.5"
    assert driver.config["port"] == 4001
    assert dm.reconnected.count("dev1") == 2


def test_ssh_also_gets_the_tcp_stand_in():
    """An SSH driver has no simulator server either.

    Until 2026-07-31 only `serial` was flipped, so an SSH driver kept its
    declared transport under simulation and tried to open an SSH session
    against the simulator's plain TCP socket. netgear_m4250_m4350 shipped
    `simulated: true` and could not actually be simulated without the user
    first hand-editing `transport: tcp` into its device config.
    """
    assert SimulationManager._driver_transport_needs_tcp_stand_in(_FakeDriver("ssh"))


def test_a_transport_the_simulator_serves_is_left_alone():
    """Vacuity guard: these have servers of their own and must not be flipped."""
    for transport in ("tcp", "http", "udp", "osc", "mqtt", "websocket"):
        assert not SimulationManager._driver_transport_needs_tcp_stand_in(
            _FakeDriver(transport)
        ), transport
