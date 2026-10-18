"""An SSH device's card and log say why ssh failed, whichever client runs.

The transport runs the operating system's ssh client. Some of its failures
are logged only at LogLevel=INFO: a refused port on Windows' built-in client,
a key exchange with no common algorithm and a session the device stopped
answering on every client. At INFO, though, ssh also prints the device's
pre-login banner, and a banner is device text that can read like a rejected
login. So ssh writes its log to a file of its own (-E), and that file, never
stderr, is what the transport reports.

The client here is a stand-in that behaves the way those clients were
measured to: it writes its log lines only at INFO or above, to the -E file
when it is given one and to stderr otherwise, and prints the banner to stderr
at INFO or above. Each port is one this test bound, and the devices are
invented.
"""

from __future__ import annotations

import asyncio
import os
import socket
import sys
import textwrap

import pytest

from openavc.core.connection_fault import (
    CONNECTION_REFUSED,
    HOST_KEY_REJECTED,
    TRANSPORT_DISCONNECTED,
    classify_connection_fault,
)
from openavc.core.device_manager import DeviceManager
from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.base import BaseDriver, _verify_failure_error
from openavc.drivers.registry import _DRIVER_REGISTRY
from openavc.transport.ssh import SSHTransport

STAND_IN = "acme-ssh"

BANNER = "If you are not authorized to access this system, disconnect now."

_CLIENT = textwrap.dedent(
    '''
    import socket
    import sys

    RANKS = {"QUIET": 0, "FATAL": 1, "ERROR": 2, "INFO": 3, "VERBOSE": 4}
    args = sys.argv[1:]
    log_path, level, port = None, None, 22
    i = 0
    while i < len(args):
        if args[i] == "-E":
            log_path = args[i + 1]
        elif args[i] == "-p":
            port = int(args[i + 1])
        elif args[i] == "-o" and args[i + 1].startswith("LogLevel=") and level is None:
            level = args[i + 1].split("=", 1)[1]
        if args[i] in ("-E", "-p", "-o"):
            i += 2
        else:
            i += 1
    level = RANKS[level or "INFO"]
    host = args[-1].rpartition("@")[2]


    def log(text):
        # Every line here is one ssh logs at INFO.
        if level < RANKS["INFO"]:
            return
        if log_path:
            with open(log_path, "a") as f:
                f.write(text + "\\n")
        else:
            sys.stderr.write(text + "\\n")


    try:
        link = socket.create_connection((host, port), timeout=5)
    except ConnectionRefusedError:
        log("banner exchange: Connection to UNKNOWN port -1: Connection refused")
        sys.exit(255)
    greeting = link.makefile("rb").readline().decode().strip()
    if greeting == "SSH-2.0-AcmeLegacy":
        log(f"Unable to negotiate with {host} port {port}: no matching key "
            "exchange method found. Their offer: diffie-hellman-group1-sha1")
        sys.exit(255)
    if greeting == "SSH-2.0-AcmeChangedKey":
        log("@    WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!     @")
        log(f"Host key for {host} has changed and you have requested strict checking.")
        log("Host key verification failed.")
        sys.exit(255)
    # A client before OpenSSH 8.5 checks the host's address as well as its
    # name (CheckHostIP) and says so at INFO when the address is new to it or
    # holds another device's key. The session itself goes on.
    if greeting == "SSH-2.0-AcmeNewAddress":
        log(f"Warning: Permanently added the ECDSA host key for IP address '{host}' "
            "to the list of known hosts.")
    if greeting == "SSH-2.0-AcmeReusedAddress":
        log(f"Warning: the ECDSA host key for 'acme-widget.local' differs from the key "
            f"for the IP address '{host}'")
        log("Offending key for IP in /home/acme/.openavc/known_hosts:2")
        log("Matching host key in /home/acme/.openavc/known_hosts:1")
    if level >= RANKS["INFO"]:
        sys.stderr.write("BANNER\\n")
        sys.stderr.flush()
    sys.stdout.write("acme> ")
    sys.stdout.flush()
    log(f"Timeout, server {host} not responding.")
    sys.exit(255)
    '''
).replace("BANNER", BANNER)


@pytest.fixture
def stand_in(tmp_path, monkeypatch):
    """Every ssh the transport starts is the stand-in client."""
    script = tmp_path / "acme_ssh.py"
    script.write_text(_CLIENT, encoding="utf-8")
    real = asyncio.create_subprocess_exec

    async def spawn(program, *args, **kwargs):
        assert program == STAND_IN
        return await real(sys.executable, str(script), *args, **kwargs)

    monkeypatch.setattr("openavc.transport.ssh.shutil.which", lambda name: STAND_IN)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)


def _closed_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.fixture
async def greeter():
    """A listener that sends one greeting line and then waits."""
    servers = []

    async def start(greeting: bytes) -> int:
        async def greet(reader, writer):
            writer.write(greeting + b"\r\n")
            await writer.drain()
            await reader.read()
            writer.close()

        server = await asyncio.start_server(greet, "127.0.0.1", 0)
        servers.append(server)
        return server.sockets[0].getsockname()[1]

    yield start
    for server in servers:
        server.close()


async def _drop(port: int, tmp_path) -> SSHTransport:
    """Start a session against the port and wait for it to drop."""
    dropped = asyncio.Event()
    transport = await SSHTransport.create(
        "127.0.0.1", port, "admin", lambda data: None, dropped.set,
        known_hosts_path=str(tmp_path / "known_hosts"),
    )
    await asyncio.wait_for(dropped.wait(), 10)
    return transport


def _classify(transport: SSHTransport, port: int):
    return classify_connection_fault(
        last_error=transport.last_error, exc=None,
        host="127.0.0.1", port=port, transport="ssh",
    )


async def test_a_refused_port_is_named_when_the_client_logs_it_only_at_info(
    stand_in, tmp_path,
):
    port = _closed_port()
    transport = await _drop(port, tmp_path)
    try:
        assert transport.last_error.endswith("Connection refused")
        assert _classify(transport, port).code == CONNECTION_REFUSED
    finally:
        await transport.close()


async def test_a_key_exchange_with_no_common_algorithm_is_named(
    stand_in, tmp_path, greeter,
):
    port = await greeter(b"SSH-2.0-AcmeLegacy")
    transport = await _drop(port, tmp_path)
    try:
        assert "no matching key exchange method found" in transport.last_error
    finally:
        await transport.close()


async def test_the_devices_banner_is_never_read_as_the_reason(
    stand_in, tmp_path, greeter,
):
    port = await greeter(b"SSH-2.0-AcmeBanner")
    transport = await _drop(port, tmp_path)
    try:
        assert transport.last_error == "Timeout, server 127.0.0.1 not responding."
        assert "authorized" not in transport.last_error
        assert _classify(transport, port).code == TRANSPORT_DISCONNECTED
    finally:
        await transport.close()


@pytest.mark.parametrize("greeting", [b"SSH-2.0-AcmeNewAddress", b"SSH-2.0-AcmeReusedAddress"])
async def test_an_older_clients_address_warning_is_not_a_rejected_host_key(
    stand_in, tmp_path, greeter, greeting,
):
    # A device reached by name that DHCP moved: the session opened and later
    # timed out. A rejected host key stops the retries for good, so reading
    # the address warning that way would leave the device offline.
    port = await greeter(greeting)
    transport = await _drop(port, tmp_path)
    try:
        assert "host key for" in transport.last_error
        assert _classify(transport, port).code == TRANSPORT_DISCONNECTED
    finally:
        await transport.close()


async def test_a_changed_host_key_is_still_rejected(stand_in, tmp_path, greeter):
    port = await greeter(b"SSH-2.0-AcmeChangedKey")
    transport = await _drop(port, tmp_path)
    try:
        assert _classify(transport, port).code == HOST_KEY_REJECTED
    finally:
        await transport.close()


def test_the_strict_checking_line_alone_still_reads_as_a_changed_key():
    fault = classify_connection_fault(
        last_error="Host key for acme-widget.local has changed and you have requested strict checking.",
        exc=None, host="acme-widget.local", port=22, transport="ssh",
    )
    assert fault.code == HOST_KEY_REJECTED


async def test_the_log_file_is_removed_when_the_session_closes(
    stand_in, tmp_path,
):
    transport = await _drop(_closed_port(), tmp_path)
    log_path = transport._log_path
    assert log_path is not None
    await transport.close()
    assert not os.path.exists(log_path)
    assert transport.last_error.endswith("Connection refused")


# --- On a device card ----------------------------------------------------------


class AcmeSshShellDriver(BaseDriver):
    """An invented device on BaseDriver's own connect, which runs the
    transport's verify step."""

    DRIVER_INFO = {
        "id": "acme_ssh_shell_reason_test",
        "name": "Acme Shell",
        "manufacturer": "Acme",
        "category": "utility",
        "transport": "ssh",
        "default_config": {"host": "", "port": 22, "username": "admin"},
        "commands": {},
        "state_variables": {},
        "config_schema": {},
    }

    async def send_command(self, command, params=None):
        return None


class AcmeSshConsoleDriver(AcmeSshShellDriver):
    """An invented device with no verify step whose handshake waits for its
    prompt."""

    DRIVER_INFO = {
        **AcmeSshShellDriver.DRIVER_INFO,
        "id": "acme_ssh_console_reason_test",
        "default_config": {
            "host": "", "port": 22, "username": "admin", "verify_timeout": 0,
        },
    }

    async def _post_connect(self) -> None:
        await asyncio.sleep(3.0)
        raise ConnectionError(f"[{self.device_id}] No prompt")


@pytest.fixture
def core():
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    return state, events


@pytest.fixture
async def manager(core, monkeypatch, tmp_path):
    state, events = core
    for driver in (AcmeSshShellDriver, AcmeSshConsoleDriver):
        monkeypatch.setitem(_DRIVER_REGISTRY, driver.DRIVER_INFO["id"], driver)
    dm = DeviceManager(state, events)
    # A retry started here waits instead of dialing again during the test.
    monkeypatch.setattr(dm, "_reconnect_delay", lambda device_id, attempt: 60.0)
    yield dm
    await dm.disconnect_all()


@pytest.mark.parametrize("driver", [AcmeSshShellDriver, AcmeSshConsoleDriver])
async def test_a_closed_ssh_port_reads_refused_from_the_first_attempt(
    stand_in, manager, core, tmp_path, driver,
):
    state, _ = core
    port = _closed_port()
    await manager.add_device({
        "id": "shell",
        "driver": driver.DRIVER_INFO["id"],
        "name": "Shell",
        "config": {
            **driver.DRIVER_INFO["default_config"],
            "host": "127.0.0.1", "port": port,
            "known_hosts_path": str(tmp_path / "known_hosts"),
        },
    })
    assert state.get("device.shell.offline_reason") == CONNECTION_REFUSED
    assert f"127.0.0.1:{port}" in state.get("device.shell.offline_detail")


# --- In the log ---------------------------------------------------------------


async def test_the_connect_error_names_the_refusal(stand_in, core, tmp_path):
    # The "Failed to connect" log line is this error's text: it used to say
    # "is not responding" while the card said the port refused.
    state, events = core
    port = _closed_port()
    driver = AcmeSshShellDriver("shell", {
        **AcmeSshShellDriver.DRIVER_INFO["default_config"],
        "host": "127.0.0.1", "port": port,
        "known_hosts_path": str(tmp_path / "known_hosts"),
    }, state, events)
    with pytest.raises(ConnectionError) as caught:
        await driver.connect()
    assert str(caught.value).endswith("Connection refused")


REFUSED = "ssh: connect to host 192.0.2.1 port 22: Connection refused"


@pytest.mark.parametrize("said, expected", [
    (REFUSED, REFUSED),
    # The reason is in an earlier line than the last.
    (REFUSED + "\nsomething else ssh said", REFUSED),
    # Nothing said, or nothing in particular: the card reads no_response
    # from "is not responding", so it stays.
    ("", "Device at 192.0.2.1:22 is not responding"),
    ("ssh said something unrecognised", "Device at 192.0.2.1:22 is not responding"),
    # A drop is not a reason to name either.
    ("Timeout, server 192.0.2.1 not responding.", "Device at 192.0.2.1:22 is not responding"),
])
def test_a_failed_verify_says_the_transports_reason_only_when_it_is_one(said, expected):
    assert str(_verify_failure_error(said, None, "192.0.2.1", 22, "ssh")) == expected


def test_a_reason_only_in_the_exceptions_chain_keeps_not_responding():
    # httpx says "All connection attempts failed" for a refused port; the
    # refusal is in the cause, which the card reads, not in the words.
    cause = ConnectionRefusedError(111, "Connection refused")
    error = _verify_failure_error(
        "All connection attempts failed", cause, "192.0.2.1", 80, "http",
    )
    assert str(error) == "Device at 192.0.2.1:80 is not responding"
    assert error.__cause__ is cause
    assert classify_connection_fault(
        last_error="All connection attempts failed", exc=error,
        host="192.0.2.1", port=80, transport="http",
    ).code == CONNECTION_REFUSED
