"""A device that asks for a sign-in nobody entered fails as a refused login.

A YAML driver's ``auth: telnet_login`` skips the sign-in when the credential
it types first is blank (``skip_if_empty``, the default), which is right for
a unit with no password set. A unit that has one still prompts. The driver
now listens for that prompt for a moment after connect, before it sends
anything: the prompt fails the connection as ``auth_failed``, naming the
blank setting, instead of the start-up and poll lines going into it until
the device reads as not answering. Without a prompt, what the device sent
meanwhile goes through the normal response matching.

The simulated side is the auto-simulator's ``sign_in_required`` error mode,
which prompts whatever the device's settings say.

Invented devices (``acme_*``) and synthetic payloads throughout; the
device and its simulator talk over real loopback sockets, through a real
``DeviceManager``.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from openavc.core.device_manager import DeviceManager
from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.configurable import create_configurable_driver_class
from openavc.drivers.registry import register_driver, unregister_driver
from openavc.simulator.yaml_auto import SIGN_IN_REQUIRED_MODE, YAMLAutoSimulator

HOST = "127.0.0.1"


def _switcher_def(**auth_overrides: Any) -> dict[str, Any]:
    """A switcher that signs in with a password alone, typed at its first
    prompt; a blank password skips the sign-in."""
    auth = {
        "type": "telnet_login",
        "username_prompt": "Password:",
        "username_field": "password",
        "password_prompt": "\r\n",
        "password_field": "unused_second_line",
        "success_pattern": "Login ",
        "line_ending": "\r",
        "timeout_seconds": 3,
        **auth_overrides,
    }
    return {
        "id": "acme_switcher",
        "name": "Acme Switcher",
        "manufacturer": "Acme",
        "category": "switcher",
        "version": "1.0.0",
        "transport": "tcp",
        "delimiter": "\r\n",
        "default_config": {"host": "", "port": 23, "password": ""},
        "config_schema": {
            "host": {"type": "string", "required": True, "label": "IP Address"},
            "port": {"type": "integer", "default": 23, "label": "Port"},
            "password": {"type": "string", "label": "Password", "secret": True},
        },
        "state_variables": {
            "input": {"type": "integer", "label": "Input"},
            "firmware": {"type": "string", "label": "Firmware"},
        },
        "commands": {},
        "responses": [
            {"match": r"^In(\d+)$", "set": {"input": "$1"}},
            {"match": r"^ACME SW4 (\S+)$", "set": {"firmware": "$1"}},
        ],
        "polling": {"queries": [{"send": "I\r", "query_for": "input"}]},
        "simulator": {"initial_state": {"input": 3}},
        "auth": auth,
    }


class _BannerSwitcher(YAMLAutoSimulator):
    """Greets each connection with its model and firmware, as the unit does
    before any prompt."""

    async def on_client_connected(self, client_id: str) -> bytes | None:
        return b"ACME SW4 V1.04\r\n"


class _SlowBannerSwitcher(_BannerSwitcher):
    """Takes over a second to print its banner after the connect, as a
    measured unit does."""

    async def on_client_connected(self, client_id: str) -> bytes | None:
        await asyncio.sleep(1.2)
        return await super().on_client_connected(client_id)


async def _until(predicate, timeout: float) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return predicate()


class _Rig:
    def __init__(self, sim: YAMLAutoSimulator, dm: DeviceManager, state: StateStore):
        self.sim = sim
        self.dm = dm
        self.state = state
        self.connections = 0

    def get(self, key: str):
        return self.state.get(f"device.sw1.{key}")

    def received(self) -> list[str]:
        """What reached the simulated device from the driver."""
        return [e["data_text"] for e in self.sim.get_protocol_log(1000) if e["direction"] == "in"]

    async def add(self, password: str = "") -> float:
        """Add the device; seconds until add_device returned."""
        started = time.monotonic()
        await self.dm.add_device({
            "id": "sw1", "driver": "acme_switcher", "name": "Lobby Switcher",
            "config": {
                "host": HOST, "port": self.sim.port, "password": password,
                "poll_interval": 0.2,
            },
        })
        return time.monotonic() - started


@pytest.fixture
async def rig(request):
    """The acme switcher's simulator (with a banner, or a slow one, when the
    test asks for it through ``indirect``) and a real DeviceManager; the
    test adds the device."""
    definition = _switcher_def()
    cls = {"banner": _BannerSwitcher, "slow banner": _SlowBannerSwitcher}.get(
        getattr(request, "param", ""), YAMLAutoSimulator,
    )
    sim = cls("sw1", config={"password": ""}, driver_def=definition)
    register_driver(create_configurable_driver_class(definition))
    state = StateStore()
    rig = _Rig(sim, DeviceManager(state, EventBus()), state)
    original = sim._handle_client

    async def counted(reader, writer):
        rig.connections += 1
        await original(reader, writer)

    sim._handle_client = counted
    await sim.start(0)
    try:
        yield rig
    finally:
        await rig.dm.disconnect_all()
        unregister_driver(definition["id"])
        await sim.stop()


# ── A unit with a password set, and none entered ────────────────────────────

@pytest.mark.parametrize("rig", ["banner", "slow banner"], indirect=True)
async def test_a_prompt_nobody_can_answer_fails_as_a_refused_login_naming_the_setting(rig):
    rig.sim.inject_error(SIGN_IN_REQUIRED_MODE)
    await rig.add(password="")
    assert await _until(lambda: rig.get("offline_reason") == "auth_failed", 3.0)
    assert rig.get("connected") is False
    assert rig.get("offline_detail") == (
        "The device asks for a sign-in, and the Password setting is blank."
    )
    # Nothing reached the device, and a refused login is not retried.
    await asyncio.sleep(1.0)
    assert rig.received() == []
    assert rig.connections == 1


async def test_a_prompt_without_a_banner_is_caught_too(rig):
    rig.sim.inject_error(SIGN_IN_REQUIRED_MODE)
    await rig.add(password="")
    assert await _until(lambda: rig.get("offline_reason") == "auth_failed", 3.0)
    assert rig.received() == []


async def test_with_the_password_entered_the_same_unit_signs_in(rig):
    rig.sim.inject_error(SIGN_IN_REQUIRED_MODE)
    await rig.add(password="SN123456")
    assert rig.get("connected") is True
    assert await _until(lambda: rig.get("input") == 3, 3.0)
    assert "I" in rig.received()


# ── A unit with no password set ─────────────────────────────────────────────

@pytest.mark.parametrize("rig", ["banner"], indirect=True)
async def test_a_unit_with_no_password_connects_and_its_banner_still_counts(rig):
    took = await rig.add(password="")
    assert rig.get("connected") is True
    # The banner arrived while the driver listened for a prompt, and still
    # went through the response rules; the listening ended once it went quiet.
    assert rig.get("firmware") == "V1.04"
    assert took < 1.5
    assert await _until(lambda: rig.get("input") == 3, 3.0)


async def test_a_unit_that_sends_nothing_connects_after_the_short_wait(rig):
    took = await rig.add(password="")
    assert rig.get("connected") is True
    assert took < 4.0
    assert await _until(lambda: rig.get("input") == 3, 3.0)


def test_a_serial_device_does_not_wait():
    """A serial line has no connect for a device to prompt on, so a blank
    credential there simply skips the sign-in, as before."""
    definition = {**_switcher_def(), "transports": ["tcp", "serial"]}
    cls = create_configurable_driver_class(definition)
    auth = definition["auth"]
    tcp = cls("sw1", {"host": HOST, "port": 23, "password": ""}, StateStore(), EventBus())
    serial = cls(
        "sw2", {"transport": "serial", "port": "/dev/ttyUSB0", "password": ""},
        StateStore(), EventBus(),
    )
    assert tcp._auth_plan(auth) == "blank"
    assert serial._auth_plan(auth) is None


# ── The simulator's side ────────────────────────────────────────────────────

def test_the_mode_is_offered_where_the_driver_skips_a_blank_sign_in():
    sim = YAMLAutoSimulator("sw1", config={}, driver_def=_switcher_def())
    offered = sim.to_info_dict()["available_errors"]
    assert offered[SIGN_IN_REQUIRED_MODE] == {
        "description": "Device asks for a sign-in (even with none set)", "builtin": True,
    }


@pytest.mark.parametrize("definition", [
    _switcher_def(skip_if_empty=False),
    {k: v for k, v in _switcher_def().items() if k != "auth"},
])
def test_the_mode_is_not_offered_where_the_driver_always_signs_in_or_never_does(definition):
    sim = YAMLAutoSimulator("sw1", config={}, driver_def=definition)
    assert SIGN_IN_REQUIRED_MODE not in sim.to_info_dict()["available_errors"]
