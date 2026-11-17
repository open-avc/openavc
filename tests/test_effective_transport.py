"""Which transport a device connects over: one rule, asked by every reader.

``drivers/transport_choice.py`` is the rule: the device's own choice (its
config's ``transport``) first, else its driver's declared ``transport``, else
tcp, with a null or blank value read as no choice at either level and the
name read lower-cased. These tests pin the rule, a driver whose file writes
``transport: null`` (it connects over tcp, as it would with the key left
out), a device whose config picks another of its driver's transports (every
reader follows the pick, not the driver's default), and that no module
outside the rule spells the formula again.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from typing import Any

import pytest

from openavc.core.device_config import connection_of
from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.base import BaseDriver
from openavc.drivers.configurable import create_configurable_driver_class
from openavc.drivers.registry import (
    _DRIVER_REGISTRY,
    get_driver_transport,
    list_registered_drivers,
)
from openavc.drivers.transport_choice import (
    configured_transport,
    declared_transport,
    effective_transport,
)

PACKAGE = Path(__file__).resolve().parents[1] / "openavc"


# --- the rule --------------------------------------------------------------


@pytest.mark.parametrize(
    "config, info, expected",
    [
        ({}, {"transport": "serial"}, "serial"),
        ({"transport": "tcp"}, {"transport": "serial"}, "tcp"),
        ({"transport": None}, {"transport": "udp"}, "udp"),
        ({"transport": ""}, {"transport": "udp"}, "udp"),
        ({"transport": "  "}, {"transport": "udp"}, "udp"),
        ({}, {"transport": None}, "tcp"),
        ({}, {"transport": ""}, "tcp"),
        ({}, {}, "tcp"),
        (None, None, "tcp"),
        ({"transport": "Serial"}, {"transport": "tcp"}, "serial"),
        ({}, {"transport": "HTTP"}, "http"),
    ],
)
def test_the_device_choice_comes_first_then_the_driver_then_tcp(config, info, expected):
    assert effective_transport(config, info) == expected


def test_the_two_halves_say_when_nothing_is_chosen():
    assert configured_transport({}) == ""
    assert configured_transport({"transport": None}) == ""
    assert configured_transport({"transport": "OSC"}) == "osc"
    assert declared_transport({"transport": None}) == "tcp"
    assert declared_transport(None) == "tcp"


# --- a driver written with transport: null ----------------------------------


def _null_transport_definition() -> dict[str, Any]:
    return {
        "id": "acme_null_transport",
        "name": "Acme Null Transport",
        "manufacturer": "Acme",
        "category": "utility",
        "version": "1.0.0",
        "transport": None,
        "commands": {"ping": {"label": "Ping", "send": "PING\\r"}},
        "state_variables": {},
    }


class _AcmePythonNull(BaseDriver):
    DRIVER_INFO = {
        "id": "acme_python_null_transport",
        "name": "Acme Python Null Transport",
        "manufacturer": "Acme",
        "category": "utility",
        "transport": None,
        "commands": {},
        "state_variables": {},
    }

    async def send_command(self, command: str, params: dict[str, Any] | None = None) -> Any:
        return None


@pytest.fixture
def registered(monkeypatch):
    """Register driver classes for one test, removed after it."""
    def add(cls):
        monkeypatch.setitem(_DRIVER_REGISTRY, cls.DRIVER_INFO["id"], cls)
        return cls
    return add


def test_a_yaml_driver_with_a_null_transport_is_listed_as_tcp(registered):
    cls = registered(create_configurable_driver_class(_null_transport_definition()))
    assert cls.DRIVER_INFO["transport"] == "tcp"
    assert get_driver_transport("acme_null_transport") == "tcp"
    listed = next(d for d in list_registered_drivers() if d["id"] == "acme_null_transport")
    assert listed["transport"] == "tcp"


def test_a_python_driver_with_a_null_transport_reads_as_tcp(registered):
    registered(_AcmePythonNull)
    assert get_driver_transport("acme_python_null_transport") == "tcp"
    listed = next(d for d in list_registered_drivers() if d["id"] == "acme_python_null_transport")
    assert listed["transport"] == "tcp"
    driver = _AcmePythonNull("acme1", {"host": "127.0.0.1"}, StateStore(), EventBus())
    assert driver._effective_transport() == "tcp"
    record = {"driver": "acme_python_null_transport", "config": {"host": "192.0.2.4", "port": 23}}
    assert connection_of(record).transport == "tcp"


async def test_a_yaml_driver_with_a_null_transport_connects_over_tcp(registered):
    cls = registered(create_configurable_driver_class(_null_transport_definition()))
    received: list[bytes] = []

    async def handle(reader, writer):
        received.append(await reader.readuntil(b"\r"))
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    driver = cls("acme1", {"host": "127.0.0.1", "port": port}, StateStore(), EventBus())
    try:
        await driver.connect()
        assert driver.get_state("connected") is True
        await driver.send_command("ping")
        for _ in range(100):
            if received:
                break
            await asyncio.sleep(0.01)
        assert received == [b"PING\r"]
    finally:
        await driver.disconnect()
        server.close()
        await server.wait_closed()


class _AcmeBuilds(BaseDriver):
    """Records the transport connect() asks the ladder to build, then stops."""

    DRIVER_INFO = {"id": "acme_builds", "transport": "tcp", "commands": {}, "state_variables": {}}

    async def send_command(self, command: str, params: dict[str, Any] | None = None) -> Any:
        return None

    async def _create_transport(self, transport_type: str) -> None:
        self.built = transport_type
        raise ConnectionError("stop here")


@pytest.mark.parametrize("written, built", [("Serial", "serial"), ("TCP", "tcp"), (None, "tcp")])
async def test_connect_builds_the_transport_the_rule_reads(written, built):
    """A hand-written project's `Serial` connects as serial, as Edit Device shows it."""
    driver = _AcmeBuilds("acme1", {"host": "192.0.2.4", "port": "/dev/ttyUSB0", "transport": written},
                         StateStore(), EventBus())
    with pytest.raises(ConnectionError):
        await driver.connect()
    assert driver.built == built


# --- a device that picks another of its driver's transports ----------------


def _picks_definition() -> dict[str, Any]:
    return {
        "id": "acme_picks",
        "name": "Acme Picks",
        "manufacturer": "Acme",
        "category": "utility",
        "version": "1.0.0",
        "transport": "tcp",
        "transports": ["tcp", "udp"],
        "auth": {
            "type": "telnet_login",
            "username_prompt": "login:",
            "password_prompt": "Password:",
        },
        "commands": {"ping": {"label": "Ping", "send": "PING\\r"}},
        "state_variables": {},
    }


def _picks(config: dict[str, Any]):
    cls = create_configurable_driver_class(_picks_definition())
    return cls("acme1", config, StateStore(), EventBus())


def test_the_sign_in_follows_the_device_pick():
    """The telnet sign-in reads a byte stream; a device that picks UDP has none."""
    auth = _picks_definition()["auth"]
    login = {"username": "admin", "password": "pw"}
    assert _picks({"host": "192.0.2.4", **login})._auth_plan(auth) == "run"
    assert _picks({"host": "192.0.2.4", "transport": "udp", **login})._auth_plan(auth) is None


def test_a_raw_poll_line_follows_the_device_pick():
    """A raw line on UDP goes unframed, whatever the driver's default."""
    definition = _picks_definition()
    definition["send_frame"] = {"type": "length_prefix", "length_size": 1}
    cls = create_configurable_driver_class(definition)
    tcp = cls("acme1", {"host": "192.0.2.4"}, StateStore(), EventBus())
    udp = cls("acme2", {"host": "192.0.2.4", "transport": "udp"}, StateStore(), EventBus())
    assert tcp._read_back_entry("ping", "STATUS?", {}, {})[1] == ("raw", b"\x07STATUS?")
    assert udp._read_back_entry("ping", "STATUS?", {}, {})[1] == ("raw", b"STATUS?")


# --- nobody spells the formula again ----------------------------------------

# Reads of a ``transport`` key that are not this rule's to answer, each by the
# expression read: what a request or a record carries, never a resolution.
_NOT_A_RESOLUTION = {
    # httpx's own `transport=` keyword, set on a request's arguments.
    ("utils/outbound.py", 'kwargs.get("transport")'),
    # The simulation keeps the device's own setting, to put back afterwards.
    ("core/simulation.py", 'driver.config.get("transport")'),
    # A driver's own Connection setting, as its config_schema declares it.
    ("drivers/registry.py", '(info.get("config_schema") or {}).get("transport")'),
    # A report repeats the connection record it was handed.
    ("audit/confidence.py", 'connection.get("transport")'),
    ("audit/report.py", "connection.get('transport')"),
    ("support/problem_report.py", 'snapshot.address.get("transport")'),
    ("support/problem_report.py", "address.get('transport', '')"),
}

# The driver checker and the simulator read a definition's own declared
# transport to judge or serve the file; they change with the driver contract.
_CONTRACT = (
    "simulator/",
    "drivers/avcdriver_semantic.py",
    "drivers/command_checks.py",
    "drivers/driver_warnings.py",
    "drivers/liveness_checks.py",
    "drivers/push_block_checks.py",
    "drivers/python_info.py",
    "drivers/sign_in_checks.py",
)


def _names_transport(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value == "transport"


def _is_get(node: ast.AST) -> bool:
    """``x.get("transport", ...)``"""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and bool(node.args)
        and _names_transport(node.args[0])
    )


def _is_index(node: ast.AST) -> bool:
    """``x["transport"]`` read, never written."""
    return (
        isinstance(node, ast.Subscript)
        and isinstance(node.ctx, ast.Load)
        and _names_transport(node.slice)
    )


def _transport_reads(source: str) -> list[tuple[int, str]]:
    """(line, expression) for each read of a ``transport`` key in ``source``."""
    return [
        (node.lineno, ast.get_source_segment(source, node))
        for node in ast.walk(ast.parse(source))
        if _is_get(node) or _is_index(node)
    ]


def _scanned():
    """Each module the scan reads, by its path inside the package."""
    for path in sorted(PACKAGE.rglob("*.py")):
        rel = path.relative_to(PACKAGE).as_posix()
        if rel != "drivers/transport_choice.py" and not rel.startswith(_CONTRACT):
            yield rel, path.read_text(encoding="utf-8")


def test_no_module_resolves_a_transport_itself():
    """Every reader asks transport_choice; none reads the key and decides."""
    offenders = [
        f"{rel}:{line}: {read}"
        for rel, source in _scanned()
        for line, read in _transport_reads(source)
        if (rel, read) not in _NOT_A_RESOLUTION
    ]
    assert offenders == [], "\n".join(offenders)


def test_every_exception_is_still_there():
    """An exception whose read has gone is removed from the list."""
    found = {
        (rel, read)
        for rel in {rel for rel, _ in _NOT_A_RESOLUTION}
        for _, read in _transport_reads((PACKAGE / rel).read_text(encoding="utf-8"))
    }
    assert _NOT_A_RESOLUTION - found == set()
