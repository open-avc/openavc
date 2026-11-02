"""The computer's own ssh client, against a device that offers only
algorithms it does not use by default.

test_ssh_failure_reasons.py uses a stand-in client that says what the real
ones were measured to say. These run the real one, so the words the device
card is built from are the client's, on whatever system runs the suite. The
device is a listener on this machine that sends an SSH greeting and the
algorithm lists of an invented old device, then waits: ssh compares the
lists and stops, or, given the option the card suggests, carries on past
them. Skipped where no ssh client is installed.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import struct

import pytest

from openavc.core.connection_fault import INVALID_CONFIG, classify_connection_fault
from openavc.transport.ssh import SSHTransport

pytestmark = pytest.mark.skipif(shutil.which("ssh") is None, reason="no ssh client")

# Current key exchange, host key and MAC; only an old cipher.
OFFER = {
    "kex": "curve25519-sha256",
    "hostkey": "ssh-ed25519",
    "cipher": "aes128-cbc",
    "mac": "hmac-sha2-256",
}


def _name_list(text: str) -> bytes:
    raw = text.encode("ascii")
    return struct.pack(">I", len(raw)) + raw


def _kexinit() -> bytes:
    """One SSH_MSG_KEXINIT in an unencrypted binary packet (RFC 4253 7.1)."""
    payload = bytes([20]) + os.urandom(16)
    for item in (OFFER["kex"], OFFER["hostkey"], OFFER["cipher"], OFFER["cipher"],
                 OFFER["mac"], OFFER["mac"], "none", "none", "", ""):
        payload += _name_list(item)
    payload += b"\x00" + struct.pack(">I", 0)
    padding = 8 - ((5 + len(payload)) % 8)
    if padding < 4:
        padding += 8
    return struct.pack(">IB", 1 + len(payload) + padding, padding) + payload + bytes(padding)


@pytest.fixture
async def old_device():
    async def serve(reader, writer):
        writer.write(b"SSH-2.0-AcmeLegacy_1.0\r\n" + _kexinit())
        await writer.drain()
        try:
            await asyncio.wait_for(reader.read(-1), 1.0)
        except asyncio.TimeoutError:
            pass
        writer.close()

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    yield server.sockets[0].getsockname()[1]
    server.close()


async def _session(port: int, tmp_path, options: list[str]) -> str:
    dropped = asyncio.Event()
    transport = await SSHTransport.create(
        "127.0.0.1", port, "admin", lambda data: None, dropped.set,
        known_hosts_path=str(tmp_path / "known_hosts"), extra_ssh_options=options,
    )
    try:
        await asyncio.wait_for(dropped.wait(), 15)
        return transport.last_error
    finally:
        await transport.close()


def _fault(said: str, port: int):
    return classify_connection_fault(
        last_error=said, exc=None, host="127.0.0.1", port=port, transport="ssh",
    )


async def test_the_card_names_the_option_the_device_needs(old_device, tmp_path):
    said = await _session(old_device, tmp_path, [])
    assert "no matching cipher found" in said
    fault = _fault(said, old_device)
    assert fault.code == INVALID_CONFIG
    assert "add Ciphers=+aes128-cbc to SSH options" in fault.message


async def test_the_option_it_names_gets_ssh_past_the_algorithms(old_device, tmp_path):
    said = await _session(old_device, tmp_path, ["Ciphers=+aes128-cbc"])
    assert "Unable to negotiate" not in said
    assert _fault(said, old_device).code != INVALID_CONFIG


async def test_an_option_the_client_will_not_take_is_named(old_device, tmp_path):
    said = await _session(old_device, tmp_path, ["HostKeyAlgorithms=+acme-unknown-type"])
    assert "command-line" in said
    assert "acme-unknown-type" in said
    assert _fault(said, old_device).code == INVALID_CONFIG
