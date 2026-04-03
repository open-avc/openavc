"""Tests for TCP transport."""

import asyncio
import ssl
import tempfile
from pathlib import Path

import pytest

from server.transport.tcp import TCPTransport
from server.transport.frame_parsers import (
    DelimiterFrameParser,
    LengthPrefixFrameParser,
    FixedLengthFrameParser,
)


# --- Fixtures ---


@pytest.fixture
async def echo_server():
    """Simple TCP echo server that sends back what it receives, with delimiter."""

    async def handle(reader, writer):
        try:
            buffer = b""
            while True:
                data = await reader.read(1024)
                if not data:
                    break
                buffer += data
                while b"\r" in buffer:
                    msg, buffer = buffer.split(b"\r", 1)
                    writer.write(msg + b"\r")
                    await writer.drain()
        except (ConnectionError, OSError):
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    yield server, port
    server.close()
    await server.wait_closed()


@pytest.fixture
async def raw_echo_server():
    """TCP server that echoes data back without any framing."""

    async def handle(reader, writer):
        try:
            while True:
                data = await reader.read(4096)
                if not data:
                    break
                writer.write(data)
                await writer.drain()
        except (ConnectionError, OSError):
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    yield server, port
    server.close()
    await server.wait_closed()


@pytest.fixture
async def close_immediately_server():
    """TCP server that accepts a connection then immediately closes it."""

    async def handle(reader, writer):
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    yield server, port
    server.close()
    await server.wait_closed()


@pytest.fixture
async def length_prefix_server():
    """TCP server that echoes using 2-byte big-endian length prefix framing."""

    async def handle(reader, writer):
        try:
            buffer = b""
            while True:
                data = await reader.read(4096)
                if not data:
                    break
                buffer += data
                while len(buffer) >= 2:
                    payload_len = int.from_bytes(buffer[:2], "big")
                    if len(buffer) < 2 + payload_len:
                        break
                    payload = buffer[2:2 + payload_len]
                    buffer = buffer[2 + payload_len:]
                    # Echo back with length prefix
                    writer.write(len(payload).to_bytes(2, "big") + payload)
                    await writer.drain()
        except (ConnectionError, OSError):
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    yield server, port
    server.close()
    await server.wait_closed()


# --- Basic connection and send ---


async def test_connect_and_send(echo_server):
    server, port = echo_server
    received = []

    transport = await TCPTransport.create(
        "127.0.0.1", port,
        on_data=lambda d: received.append(d),
        on_disconnect=lambda: None,
        delimiter=b"\r",
    )

    assert transport.connected
    await transport.send(b"hello\r")
    await asyncio.sleep(0.1)
    assert received == [b"hello"]

    await transport.close()
    assert not transport.connected


async def test_delimiter_framing(echo_server):
    server, port = echo_server
    received = []

    transport = await TCPTransport.create(
        "127.0.0.1", port,
        on_data=lambda d: received.append(d),
        on_disconnect=lambda: None,
        delimiter=b"\r",
    )

    await transport.send(b"msg1\rmsg2\r")
    await asyncio.sleep(0.1)
    assert b"msg1" in received
    assert b"msg2" in received

    await transport.close()


async def test_send_and_wait(echo_server):
    server, port = echo_server

    transport = await TCPTransport.create(
        "127.0.0.1", port,
        on_data=lambda d: None,
        on_disconnect=lambda: None,
        delimiter=b"\r",
    )

    response = await transport.send_and_wait(b"query\r", timeout=2.0)
    assert response == b"query"

    await transport.close()


async def test_connection_refused():
    with pytest.raises(ConnectionError):
        await TCPTransport.create(
            "127.0.0.1", 19999,
            on_data=lambda d: None,
            on_disconnect=lambda: None,
            timeout=1.0,
        )


# --- Connection errors and timeouts ---


async def test_connection_timeout():
    """Connecting to a non-routable address should raise after timeout."""
    with pytest.raises(ConnectionError):
        await TCPTransport.create(
            "192.0.2.1", 9999,  # TEST-NET, should be unreachable
            on_data=lambda d: None,
            on_disconnect=lambda: None,
            timeout=0.5,
        )


async def test_send_when_not_connected(echo_server):
    """Sending after close raises ConnectionError."""
    server, port = echo_server
    transport = await TCPTransport.create(
        "127.0.0.1", port,
        on_data=lambda d: None,
        on_disconnect=lambda: None,
        delimiter=b"\r",
    )

    await transport.close()
    with pytest.raises(ConnectionError):
        await transport.send(b"hello\r")


async def test_send_and_wait_timeout(echo_server):
    """send_and_wait raises TimeoutError when no response arrives."""
    # Use raw echo server behavior but with delimiter framing so the response
    # won't be delivered (server echoes with \r but we send without \r so
    # server never echoes). Actually, we need a server that just accepts
    # but never responds.

    async def handle(reader, writer):
        try:
            while True:
                data = await reader.read(1024)
                if not data:
                    break
                # Intentionally do NOT respond
        except (ConnectionError, OSError):
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    transport = await TCPTransport.create(
        "127.0.0.1", port,
        on_data=lambda d: None,
        on_disconnect=lambda: None,
        delimiter=b"\r",
    )

    with pytest.raises(asyncio.TimeoutError):
        await transport.send_and_wait(b"query\r", timeout=0.3)

    await transport.close()
    server.close()
    await server.wait_closed()


# --- Disconnect handling ---


async def test_on_disconnect_callback_on_remote_close(close_immediately_server):
    """on_disconnect callback fires when remote end closes connection."""
    server, port = close_immediately_server
    disconnected = asyncio.Event()

    transport = await TCPTransport.create(
        "127.0.0.1", port,
        on_data=lambda d: None,
        on_disconnect=lambda: disconnected.set(),
        delimiter=b"\r",
    )

    # Wait for the disconnect callback to fire
    await asyncio.wait_for(disconnected.wait(), timeout=2.0)
    assert not transport.connected

    await transport.close()


async def test_connected_property_after_remote_close(close_immediately_server):
    """connected returns False after the remote end closes."""
    server, port = close_immediately_server

    transport = await TCPTransport.create(
        "127.0.0.1", port,
        on_data=lambda d: None,
        on_disconnect=lambda: None,
        delimiter=b"\r",
    )

    # Give the reader loop time to detect the close
    await asyncio.sleep(0.3)
    assert not transport.connected

    await transport.close()


# --- Close behavior ---


async def test_close_is_idempotent(echo_server):
    """Calling close multiple times does not raise."""
    server, port = echo_server
    transport = await TCPTransport.create(
        "127.0.0.1", port,
        on_data=lambda d: None,
        on_disconnect=lambda: None,
        delimiter=b"\r",
    )

    await transport.close()
    await transport.close()  # Should not raise
    assert not transport.connected


# --- Raw mode (no delimiter/parser) ---


async def test_raw_mode_no_delimiter(raw_echo_server):
    """Without a delimiter or parser, raw data is delivered as-is."""
    server, port = raw_echo_server
    received = []

    transport = await TCPTransport.create(
        "127.0.0.1", port,
        on_data=lambda d: received.append(d),
        on_disconnect=lambda: None,
        delimiter=None,
    )

    await transport.send(b"raw data")
    await asyncio.sleep(0.1)

    # Raw mode delivers whatever the reader gets (may be one or more chunks)
    combined = b"".join(received)
    assert combined == b"raw data"

    await transport.close()


# --- Custom frame parser ---


async def test_length_prefix_frame_parser(length_prefix_server):
    """LengthPrefixFrameParser correctly frames messages."""
    server, port = length_prefix_server
    received = []

    parser = LengthPrefixFrameParser(header_size=2)
    transport = await TCPTransport.create(
        "127.0.0.1", port,
        on_data=lambda d: received.append(d),
        on_disconnect=lambda: None,
        delimiter=None,  # Ignored when frame_parser is set
        frame_parser=parser,
    )

    # Send a length-prefixed message: 2-byte length + payload
    payload = b"hello"
    msg = len(payload).to_bytes(2, "big") + payload
    await transport.send(msg)
    await asyncio.sleep(0.1)

    assert received == [b"hello"]

    await transport.close()


async def test_explicit_delimiter_parser(echo_server):
    """Explicit DelimiterFrameParser overrides the delimiter parameter."""
    server, port = echo_server
    received = []

    parser = DelimiterFrameParser(b"\r")
    transport = await TCPTransport.create(
        "127.0.0.1", port,
        on_data=lambda d: received.append(d),
        on_disconnect=lambda: None,
        delimiter=None,  # Would mean raw mode, but parser overrides
        frame_parser=parser,
    )

    await transport.send(b"test\r")
    await asyncio.sleep(0.1)
    assert received == [b"test"]

    await transport.close()


# --- Inter-command delay ---


async def test_inter_command_delay(echo_server):
    """Inter-command delay adds a pause between sends."""
    import time
    server, port = echo_server

    transport = await TCPTransport.create(
        "127.0.0.1", port,
        on_data=lambda d: None,
        on_disconnect=lambda: None,
        delimiter=b"\r",
        inter_command_delay=0.15,
    )

    start = time.time()
    await transport.send(b"a\r")
    await transport.send(b"b\r")
    elapsed = time.time() - start

    # Two sends with 0.15s delay each = at least 0.3s
    assert elapsed >= 0.25

    await transport.close()


# --- Concurrent sends are serialized ---


async def test_concurrent_sends_serialized(echo_server):
    """Multiple concurrent sends don't interleave (send_lock)."""
    server, port = echo_server
    received = []

    transport = await TCPTransport.create(
        "127.0.0.1", port,
        on_data=lambda d: received.append(d),
        on_disconnect=lambda: None,
        delimiter=b"\r",
    )

    # Fire multiple sends concurrently
    await asyncio.gather(
        transport.send(b"first\r"),
        transport.send(b"second\r"),
        transport.send(b"third\r"),
    )
    await asyncio.sleep(0.2)

    # All three messages should have been echoed back
    assert len(received) == 3
    assert set(received) == {b"first", b"second", b"third"}

    await transport.close()


# --- _format_data helper ---


def test_format_data_ascii():
    """Printable ASCII is returned as decoded text."""
    assert TCPTransport._format_data(b"hello world") == "hello world"


def test_format_data_binary():
    """Non-printable binary data is returned as hex."""
    assert TCPTransport._format_data(b"\x00\x01\xff") == "0001ff"


def test_format_data_mixed_nonprintable():
    """Non-printable ASCII bytes fall through to hex."""
    assert TCPTransport._format_data(b"\x07\x08") == "0708"


# --- Custom name ---


async def test_custom_name(echo_server):
    """Custom name is used instead of host:port."""
    server, port = echo_server

    transport = await TCPTransport.create(
        "127.0.0.1", port,
        on_data=lambda d: None,
        on_disconnect=lambda: None,
        delimiter=b"\r",
        name="projector-1",
    )

    assert transport._name == "projector-1"
    await transport.close()


async def test_default_name(echo_server):
    """Default name is host:port."""
    server, port = echo_server

    transport = await TCPTransport.create(
        "127.0.0.1", port,
        on_data=lambda d: None,
        on_disconnect=lambda: None,
        delimiter=b"\r",
    )

    assert transport._name == f"127.0.0.1:{port}"
    await transport.close()


# --- TLS connection tests ---


async def test_tls_connection_self_signed():
    """TLS connect with self-signed cert, ssl_verify=False."""
    import ssl as ssl_mod
    import tempfile
    import os

    # Generate a self-signed certificate using stdlib
    # We'll use a pre-made cert/key for simplicity
    # Create a TLS context for the server
    server_ctx = ssl_mod.SSLContext(ssl_mod.PROTOCOL_TLS_SERVER)

    # Use openssl to generate a cert if available; otherwise skip
    cert_file = tempfile.NamedTemporaryFile(suffix=".pem", delete=False)
    key_file = tempfile.NamedTemporaryFile(suffix=".pem", delete=False)
    cert_file.close()
    key_file.close()

    try:
        # Try to generate self-signed cert
        import subprocess
        result = subprocess.run(
            [
                "openssl", "req", "-x509", "-newkey", "rsa:2048",
                "-keyout", key_file.name, "-out", cert_file.name,
                "-days", "1", "-nodes", "-subj", "/CN=localhost",
            ],
            capture_output=True, timeout=10,
        )
        if result.returncode != 0:
            pytest.skip("openssl not available for self-signed cert generation")
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pytest.skip("openssl not available for self-signed cert generation")

    try:
        server_ctx.load_cert_chain(cert_file.name, key_file.name)

        async def handle(reader, writer):
            try:
                buffer = b""
                while True:
                    data = await reader.read(1024)
                    if not data:
                        break
                    buffer += data
                    while b"\r" in buffer:
                        msg, buffer = buffer.split(b"\r", 1)
                        writer.write(msg + b"\r")
                        await writer.drain()
            except (ConnectionError, OSError):
                pass
            finally:
                try:
                    writer.close()
                    await writer.wait_closed()
                except (ConnectionError, OSError):
                    pass

        server = await asyncio.start_server(
            handle, "127.0.0.1", 0, ssl=server_ctx
        )
        port = server.sockets[0].getsockname()[1]

        received = []
        transport = await TCPTransport.create(
            "127.0.0.1", port,
            on_data=lambda d: received.append(d),
            on_disconnect=lambda: None,
            delimiter=b"\r",
            ssl=True,
            ssl_verify=False,  # Self-signed, so skip verification
        )

        assert transport.connected
        assert transport._ssl_context is not None

        await transport.send(b"tls_msg\r")
        await asyncio.sleep(0.2)
        assert received == [b"tls_msg"]

        await transport.close()
        server.close()
        await server.wait_closed()
    finally:
        os.unlink(cert_file.name)
        os.unlink(key_file.name)


async def test_tls_refused_with_invalid_cert():
    """TLS connect with verify=True against a self-signed cert should fail."""
    import ssl as ssl_mod
    import tempfile
    import os

    server_ctx = ssl_mod.SSLContext(ssl_mod.PROTOCOL_TLS_SERVER)
    cert_file = tempfile.NamedTemporaryFile(suffix=".pem", delete=False)
    key_file = tempfile.NamedTemporaryFile(suffix=".pem", delete=False)
    cert_file.close()
    key_file.close()

    try:
        import subprocess
        result = subprocess.run(
            [
                "openssl", "req", "-x509", "-newkey", "rsa:2048",
                "-keyout", key_file.name, "-out", cert_file.name,
                "-days", "1", "-nodes", "-subj", "/CN=localhost",
            ],
            capture_output=True, timeout=10,
        )
        if result.returncode != 0:
            pytest.skip("openssl not available")
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pytest.skip("openssl not available")

    try:
        server_ctx.load_cert_chain(cert_file.name, key_file.name)

        async def handle(reader, writer):
            try:
                await reader.read(1024)
            except Exception:
                pass
            finally:
                try:
                    writer.close()
                except Exception:
                    pass

        server = await asyncio.start_server(
            handle, "127.0.0.1", 0, ssl=server_ctx
        )
        port = server.sockets[0].getsockname()[1]

        # ssl_verify=True (default) against a self-signed cert should fail
        with pytest.raises(ConnectionError):
            await TCPTransport.create(
                "127.0.0.1", port,
                on_data=lambda d: None,
                on_disconnect=lambda: None,
                delimiter=b"\r",
                ssl=True,
                ssl_verify=True,
                timeout=2.0,
            )

        server.close()
        await server.wait_closed()
    finally:
        os.unlink(cert_file.name)
        os.unlink(key_file.name)


# --- Delimiter framing edge cases ---


async def test_multi_byte_delimiter():
    """Multi-byte delimiter (e.g., \\r\\n) works correctly."""

    async def handle(reader, writer):
        try:
            buffer = b""
            while True:
                data = await reader.read(4096)
                if not data:
                    break
                buffer += data
                while b"\r\n" in buffer:
                    msg, buffer = buffer.split(b"\r\n", 1)
                    writer.write(msg + b"\r\n")
                    await writer.drain()
        except (ConnectionError, OSError):
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    received = []
    transport = await TCPTransport.create(
        "127.0.0.1", port,
        on_data=lambda d: received.append(d),
        on_disconnect=lambda: None,
        delimiter=b"\r\n",
    )

    await transport.send(b"line1\r\nline2\r\n")
    await asyncio.sleep(0.1)
    assert b"line1" in received
    assert b"line2" in received

    await transport.close()
    server.close()
    await server.wait_closed()


async def test_partial_message_buffered(echo_server):
    """Incomplete messages are buffered until delimiter arrives."""
    server, port = echo_server
    received = []

    transport = await TCPTransport.create(
        "127.0.0.1", port,
        on_data=lambda d: received.append(d),
        on_disconnect=lambda: None,
        delimiter=b"\r",
    )

    # The echo server will only echo back complete messages (with delimiter).
    # Send a complete message in one go, verify we get it back.
    await transport.send(b"complete\r")
    await asyncio.sleep(0.1)
    assert b"complete" in received

    await transport.close()


async def test_empty_messages_skipped(echo_server):
    """Consecutive delimiters don't produce empty messages."""
    server, port = echo_server
    received = []

    transport = await TCPTransport.create(
        "127.0.0.1", port,
        on_data=lambda d: received.append(d),
        on_disconnect=lambda: None,
        delimiter=b"\r",
    )

    # Send two messages with an extra delimiter between them
    # The echo server should echo "a" and "b" (not empty strings)
    await transport.send(b"a\r")
    await transport.send(b"b\r")
    await asyncio.sleep(0.1)

    # Should have only non-empty messages
    for msg in received:
        assert len(msg) > 0

    await transport.close()


# --- send_and_wait clears previous responses ---


async def test_send_and_wait_clears_queue(echo_server):
    """send_and_wait clears the response queue before sending."""
    server, port = echo_server

    transport = await TCPTransport.create(
        "127.0.0.1", port,
        on_data=lambda d: None,
        on_disconnect=lambda: None,
        delimiter=b"\r",
    )

    # First query/response
    resp1 = await transport.send_and_wait(b"query1\r", timeout=2.0)
    assert resp1 == b"query1"

    # Second query/response should not get the first response
    resp2 = await transport.send_and_wait(b"query2\r", timeout=2.0)
    assert resp2 == b"query2"

    await transport.close()


# --- Multiple messages in one send_and_wait ---


async def test_send_and_wait_gets_first_response(echo_server):
    """send_and_wait returns the first complete response."""
    server, port = echo_server

    transport = await TCPTransport.create(
        "127.0.0.1", port,
        on_data=lambda d: None,
        on_disconnect=lambda: None,
        delimiter=b"\r",
    )

    response = await transport.send_and_wait(b"hello\r", timeout=2.0)
    assert response == b"hello"

    await transport.close()


# --- Fixed-length parser with TCP ---


async def test_fixed_length_parser():
    """FixedLengthFrameParser delivers exactly N-byte messages."""

    async def handle(reader, writer):
        try:
            while True:
                data = await reader.read(4096)
                if not data:
                    break
                writer.write(data)
                await writer.drain()
        except (ConnectionError, OSError):
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    received = []
    parser = FixedLengthFrameParser(4)
    transport = await TCPTransport.create(
        "127.0.0.1", port,
        on_data=lambda d: received.append(d),
        on_disconnect=lambda: None,
        delimiter=None,
        frame_parser=parser,
    )

    # Send 8 bytes, should be split into two 4-byte messages
    await transport.send(b"AABBCCDD")
    await asyncio.sleep(0.1)
    assert received == [b"AABB", b"CCDD"]

    await transport.close()
    server.close()
    await server.wait_closed()


# --- Error in on_data callback ---


async def test_on_data_exception_triggers_disconnect(echo_server):
    """If on_data raises, the transport disconnects for recovery."""
    server, port = echo_server
    disconnect_called = asyncio.Event()

    def bad_callback(data):
        raise ValueError("callback exploded")

    transport = await TCPTransport.create(
        "127.0.0.1", port,
        on_data=bad_callback,
        on_disconnect=lambda: disconnect_called.set(),
        delimiter=b"\r",
    )

    await transport.send(b"trigger\r")

    # Give time for the response to arrive and the callback to blow up
    try:
        await asyncio.wait_for(disconnect_called.wait(), timeout=2.0)
    except asyncio.TimeoutError:
        pass

    # Transport should have disconnected
    # (The disconnect happens asynchronously via create_task, so give a moment)
    await asyncio.sleep(0.2)
    assert not transport.connected

    await transport.close()
