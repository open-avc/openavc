"""A driver's ``auth:`` block reads the same to the driver and to its
simulated device.

The driver signs in (``drivers/sign_in.py``); the simulated device plays the
other half (``simulator/telnet_sign_in.py``). Both read the block through one
reading (``drivers/auth_block.py``): the defaults, a timeout of 0 or less read
as the 10-second default, when the sign-in is skipped and which transports
carry one. These run one table of blocks through both halves, sign in end to
end over loopback with a timeout of 0, and check the author's doors refuse
that timeout while a door that loads the driver only names it.

Invented devices (``acme_*``) and synthetic payloads throughout.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from openavc.core.device_manager import DeviceManager
from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.auth_block import (
    BLANK,
    DEFAULT_TIMEOUT_S,
    RUN,
    auth_problems,
    read_auth,
    sign_in_plan,
)
from openavc.drivers.avcdriver_semantic import check_driver_definition
from openavc.drivers.configurable import create_configurable_driver_class
from openavc.drivers.registry import register_driver, unregister_driver
from openavc.simulator.yaml_auto import SIGN_IN_REQUIRED_MODE, YAMLAutoSimulator

HOST = "127.0.0.1"

_AUTH = {
    "type": "telnet_login",
    "username_prompt": "login: ",
    "password_prompt": "Password: ",
    "success_pattern": "Welcome",
    "timeout_seconds": 3,
}


def _definition(auth: dict[str, Any] | None = None, **top: Any) -> dict[str, Any]:
    return {
        "id": "acme_gate",
        "name": "Acme Gate",
        "manufacturer": "Acme",
        "category": "switcher",
        "version": "1.0.0",
        "transport": "tcp",
        "delimiter": "\r\n",
        "default_config": {"host": "", "port": 23},
        "config_schema": {
            "host": {"type": "string", "required": True, "label": "IP Address"},
            "port": {"type": "integer", "default": 23, "label": "Port"},
            "username": {"type": "string", "label": "Username"},
            "password": {"type": "string", "label": "Password", "secret": True},
        },
        "state_variables": {"input": {"type": "integer", "label": "Input"}},
        "commands": {},
        "responses": [{"match": r"^In(\d+)$", "set": {"input": "$1"}}],
        "polling": {"queries": [{"send": "I\r\n", "query_for": "input"}]},
        "simulator": {"initial_state": {"input": 3}},
        "auth": dict(_AUTH if auth is None else auth),
        **top,
    }


async def _until(predicate, timeout: float) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return predicate()


# ── End to end: a timeout of 0 or less ──────────────────────────────────────


@pytest.mark.parametrize("timeout", [0, -5])
async def test_a_timeout_of_zero_or_less_signs_in_to_the_simulated_device(timeout):
    """The driver reads 0 or less as its 10-second default, and so does the
    simulated device, which used to wait no time at all for each credential
    and refuse every sign-in."""
    definition = _definition({**_AUTH, "timeout_seconds": timeout})
    settings = {"username": "admin", "password": "pw"}
    sim = YAMLAutoSimulator("gate1", config=settings, driver_def=definition)
    register_driver(create_configurable_driver_class(definition))
    state = StateStore()
    dm = DeviceManager(state, EventBus())
    await sim.start(0)
    try:
        await dm.add_device({
            "id": "gate1", "driver": "acme_gate", "name": "Lobby Gate",
            "config": {"host": HOST, "port": sim.port, "poll_interval": 0.2, **settings},
        })
        assert await _until(lambda: state.get("device.gate1.input") == 3, 5.0)
        assert state.get("device.gate1.connected") is True
        assert state.get("device.gate1.offline_reason") in (None, "")
    finally:
        await dm.disconnect_all()
        unregister_driver(definition["id"])
        await sim.stop()


# ── One table of blocks through both halves ─────────────────────────────────


class _Writer:
    def __init__(self) -> None:
        self.written = bytearray()

    def write(self, data: bytes) -> None:
        self.written.extend(data)

    async def drain(self) -> None:
        pass


async def _device_prompts(definition: dict, config: dict) -> bool:
    """True when the simulated device asks this client to sign in."""
    sim = YAMLAutoSimulator("gate1", config=config, driver_def=definition)
    reader = asyncio.StreamReader()
    reader.feed_data(b"admin\r\npw\r\n")
    reader.feed_eof()
    writer = _Writer()
    await sim.authenticate_client(reader, writer, "c1")
    return bool(writer.written)


def _driver(definition: dict, config: dict) -> Any:
    cls = create_configurable_driver_class(definition)
    return cls("gate1", {"host": HOST, "port": 23, **config}, StateStore(), EventBus())


_TABLE = [
    # (what the row is, the auth: block, the device's settings, the driver's plan, its timeout)
    ("signs in", _AUTH, {"username": "admin"}, RUN, 3.0),
    ("blank username skips", _AUTH, {"username": ""}, BLANK, 3.0),
    ("always signs in", {**_AUTH, "skip_if_empty": False}, {"username": ""}, RUN, 3.0),
    ("its own username field", {**_AUTH, "username_field": "login"}, {"login": "a"}, RUN, 3.0),
    ("no username prompt", {**_AUTH, "username_prompt": ""}, {"username": "admin"}, None, 3.0),
    ("a type it does not run", {**_AUTH, "type": "token"}, {"username": "admin"}, None, 3.0),
    ("timeout 0", {**_AUTH, "timeout_seconds": 0}, {"username": "admin"}, RUN, DEFAULT_TIMEOUT_S),
    ("timeout below 0", {**_AUTH, "timeout_seconds": -1}, {"username": "admin"}, RUN, DEFAULT_TIMEOUT_S),
    ("no timeout", {k: v for k, v in _AUTH.items() if k != "timeout_seconds"},
     {"username": "admin"}, RUN, DEFAULT_TIMEOUT_S),
    ("a timeout written as text", {**_AUTH, "timeout_seconds": "soon"}, {"username": "admin"},
     RUN, DEFAULT_TIMEOUT_S),
    ("a timeout left empty", {**_AUTH, "timeout_seconds": None}, {"username": "admin"},
     RUN, DEFAULT_TIMEOUT_S),
]


@pytest.mark.parametrize(
    "auth, config, plan, timeout", [row[1:] for row in _TABLE], ids=[row[0] for row in _TABLE],
)
async def test_each_block_reads_the_same_to_the_driver_and_the_simulated_device(
    auth, config, plan, timeout,
):
    definition = _definition(auth)
    driver = _driver(definition, config)
    assert driver._auth_plan(definition["auth"]) == plan
    # The simulated device asks for a sign-in exactly when the driver runs one.
    assert await _device_prompts(definition, config) is (plan == RUN)
    model = read_auth(definition["auth"])
    assert model.timeout == timeout
    assert driver._read_login(model).timeout == timeout
    assert sign_in_plan(model, "tcp", config) == plan


def test_a_device_on_serial_signs_in_over_the_simulators_tcp_stand_in():
    """Under simulation a device on serial reaches its simulated device over
    TCP, as the driver does: it is asked to sign in, and the mode that plays a
    unit with a password set is offered."""
    definition = _definition(transports=["tcp", "serial"])
    sim = YAMLAutoSimulator("gate1", config={"transport": "serial"}, driver_def=definition)
    assert SIGN_IN_REQUIRED_MODE in sim.to_info_dict()["available_errors"]


async def test_a_device_on_serial_is_asked_to_sign_in_under_simulation():
    definition = _definition(transports=["tcp", "serial"])
    assert await _device_prompts(definition, {"transport": "serial", "username": "admin"}) is True


def test_a_serial_line_skips_a_blank_sign_in_and_runs_one_with_a_username():
    model = read_auth(_AUTH)
    assert sign_in_plan(model, "serial", {"username": ""}) is None
    assert sign_in_plan(model, "serial", {"username": "admin"}) == RUN
    assert sign_in_plan(model, "udp", {"username": "admin"}) is None


# ── The doors ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("timeout", [0, -1, 0.0])
def test_the_authors_doors_refuse_a_timeout_of_zero_or_less_and_a_load_names_it(timeout):
    definition = _definition({**_AUTH, "timeout_seconds": timeout})
    words = "auth.timeout_seconds: must be a positive number"
    authored = check_driver_definition(definition, strict=True)
    assert words in authored.errors
    loaded = check_driver_definition(definition)
    assert words not in loaded.errors
    assert words in loaded.notices


@pytest.mark.parametrize("timeout", [0.5, 10, "soon", None, True])
def test_a_usable_timeout_or_one_the_shape_walk_names_is_no_problem_of_its_own(timeout):
    assert auth_problems({**_AUTH, "timeout_seconds": timeout}) == ()


@pytest.mark.parametrize("block", [None, "telnet_login", [], 5])
def test_a_block_that_is_not_a_mapping_reads_as_none(block):
    assert read_auth(block) is None
    assert auth_problems(block) == ()


def test_the_defaults():
    model = read_auth({"username_prompt": "u", "password_prompt": "p"})
    assert model.type == "telnet_login"
    assert (model.username_field, model.password_field) == ("username", "password")
    assert model.skip_if_empty is True
    assert model.timeout == DEFAULT_TIMEOUT_S == 10.0
    assert model.line_ending == "\r\n"
    assert (model.success_pattern, model.failure_pattern) == (None, None)
