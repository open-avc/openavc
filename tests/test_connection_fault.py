"""Unit tests for the shared connection-fault classifier.

Each test feeds a realistic ``last_error`` / exception (the strings OpenSSH,
the OS socket layer, and BaseDriver actually emit) and asserts the stable code
plus a sanity check on the human message. The classifier is transport-agnostic;
these cover every row of the taxonomy and the ordering rules that keep refused /
unreachable / no_response from shadowing each other.
"""

from __future__ import annotations

import asyncio
import errno

from server.core.connection_fault import (
    AUTH_FAILED,
    CLIENT_MISSING,
    CONNECTION_REFUSED,
    HOST_KEY_REJECTED,
    NO_RESPONSE,
    TRANSPORT_DISCONNECTED,
    UNREACHABLE,
    classify_connection_fault,
)


def _wrap(outer_msg: str, cause: BaseException) -> BaseException:
    """Build a ConnectionError with a real ``__cause__`` chain, the way the
    transports wrap an OSError before it reaches the device manager."""
    try:
        try:
            raise cause
        except BaseException as c:
            raise ConnectionError(outer_msg) from c
    except ConnectionError as e:
        return e


# --- auth_failed -----------------------------------------------------------

def test_auth_failed_ssh_permission_denied():
    fault = classify_connection_fault(
        last_error="admin@169.254.100.100: Permission denied (publickey,password).",
        exc=ConnectionError(
            "[sw] No CLI prompt from 169.254.100.100 "
            "(admin@169.254.100.100: Permission denied (publickey,password).)"
        ),
        host="169.254.100.100", port=22, transport="ssh",
    )
    assert fault.code == AUTH_FAILED
    assert "Authentication failed" in fault.message


def test_auth_failed_bare_permission_denied_tcp():
    # The §53 device_manager scenario: a transport last_error of
    # "Permission denied" yields auth_failed.
    fault = classify_connection_fault(
        last_error="Permission denied", exc=None,
        host="10.0.0.5", port=23, transport="tcp",
    )
    assert fault.code == AUTH_FAILED


def test_auth_failed_password_authentication_failed():
    fault = classify_connection_fault(
        last_error="password authentication failed", exc=None,
        host="h", port=22, transport="ssh",
    )
    assert fault.code == AUTH_FAILED


def test_auth_failed_mqtt_connack_not_authorized():
    # MQTT CONNACK rc 5. The string also contains "connection refused", so this
    # guards that the auth check wins over the refused bucket (order matters).
    fault = classify_connection_fault(
        last_error="connection refused: not authorized", exc=None,
        host="10.0.0.5", port=36669, transport="mqtt",
    )
    assert fault.code == AUTH_FAILED


def test_auth_failed_mqtt_connack_bad_credentials():
    # MQTT CONNACK rc 4.
    fault = classify_connection_fault(
        last_error="connection refused: bad username or password", exc=None,
        host="10.0.0.5", port=36669, transport="mqtt",
    )
    assert fault.code == AUTH_FAILED


# --- connection_refused ----------------------------------------------------

def test_connection_refused_ssh_stderr():
    # Has both "connection refused" and the "connect to host" connect-phase
    # prefix; refused must win over unreachable.
    fault = classify_connection_fault(
        last_error="ssh: connect to host 169.254.100.100 port 2222: Connection refused",
        exc=ConnectionError("[sw] No CLI prompt from 169.254.100.100 (... Connection refused)"),
        host="169.254.100.100", port=2222, transport="ssh",
    )
    assert fault.code == CONNECTION_REFUSED
    assert "169.254.100.100:2222" in fault.message


def test_connection_refused_tcp_errno():
    exc = _wrap(
        "Failed to connect to 10.0.0.5:80: [Errno 111] Connection refused",
        ConnectionRefusedError(errno.ECONNREFUSED, "Connection refused"),
    )
    fault = classify_connection_fault(
        last_error="", exc=exc, host="10.0.0.5", port=80, transport="tcp",
    )
    assert fault.code == CONNECTION_REFUSED


def test_connection_refused_windows_phrasing():
    fault = classify_connection_fault(
        last_error="No connection could be made because the target machine "
                   "actively refused it",
        exc=None, host="10.0.0.5", port=80, transport="tcp",
    )
    assert fault.code == CONNECTION_REFUSED


# --- unreachable -----------------------------------------------------------

def test_unreachable_no_route():
    fault = classify_connection_fault(
        last_error="ssh: connect to host 10.0.0.9 port 22: No route to host",
        exc=None, host="10.0.0.9", port=22, transport="ssh",
    )
    assert fault.code == UNREACHABLE
    assert "10.0.0.9:22" in fault.message


def test_unreachable_ssh_connect_timeout():
    fault = classify_connection_fault(
        last_error="ssh: connect to host 10.0.0.9 port 22: Connection timed out",
        exc=ConnectionError("[sw] No CLI prompt from 10.0.0.9 (... Connection timed out)"),
        host="10.0.0.9", port=22, transport="ssh",
    )
    assert fault.code == UNREACHABLE


def test_unreachable_tcp_connect_timeout_empty_message():
    # A TCP connect timeout wraps an empty-str asyncio.TimeoutError — only the
    # connect wrapper + the timeout-in-chain distinguish it.
    exc = _wrap("Failed to connect to 10.0.0.9:80: ", asyncio.TimeoutError())
    fault = classify_connection_fault(
        last_error="", exc=exc, host="10.0.0.9", port=80, transport="tcp",
    )
    assert fault.code == UNREACHABLE


def test_unreachable_dns_failure():
    fault = classify_connection_fault(
        last_error="ssh: Could not resolve hostname switch.local: Name or service not known",
        exc=None, host="switch.local", port=22, transport="ssh",
    )
    assert fault.code == UNREACHABLE


def test_unreachable_network_unreachable_errno():
    exc = _wrap(
        "Failed to connect to 192.168.9.9:80: [Errno 101] Network is unreachable",
        OSError(errno.ENETUNREACH, "Network is unreachable"),
    )
    fault = classify_connection_fault(
        last_error="", exc=exc, host="192.168.9.9", port=80, transport="tcp",
    )
    assert fault.code == UNREACHABLE


# --- host_key_rejected -----------------------------------------------------

def test_host_key_rejected():
    fault = classify_connection_fault(
        last_error=(
            "@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@\n"
            "WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!\n"
            "Host key verification failed."
        ),
        exc=ConnectionError("[sw] No CLI prompt from h (... Host key verification failed.)"),
        host="169.254.100.100", port=22, transport="ssh",
    )
    assert fault.code == HOST_KEY_REJECTED
    assert "host key" in fault.message.lower()


def test_host_key_rejected_beats_auth():
    # Even if a "permission denied" sneaks into the same blob, the host-key
    # signal (a possible MITM) wins.
    fault = classify_connection_fault(
        last_error="Host key verification failed.\nPermission denied (publickey).",
        exc=None, host="h", port=22, transport="ssh",
    )
    assert fault.code == HOST_KEY_REJECTED


# --- no_response -----------------------------------------------------------

def test_no_response_tcp_at_non_cli_port():
    # TCP socket opens fine; the CLI banner never arrives. The driver's
    # post-connect timeout must classify as no_response, not unreachable.
    exc = _wrap("[sw] No CLI prompt from 169.254.100.100", asyncio.TimeoutError())
    fault = classify_connection_fault(
        last_error="", exc=exc, host="169.254.100.100", port=80, transport="tcp",
    )
    assert fault.code == NO_RESPONSE
    assert "didn't respond as expected" in fault.message


def test_no_response_verify_failure():
    fault = classify_connection_fault(
        last_error="", exc=ConnectionError("Device at 10.0.0.5:80 is not responding"),
        host="10.0.0.5", port=80, transport="http",
    )
    assert fault.code == NO_RESPONSE


def test_no_response_does_not_shadow_refused_from_last_error():
    # HTTP verify failure: BaseDriver raises "is not responding", but the
    # transport stashed the real cause. Refused wins over no_response.
    fault = classify_connection_fault(
        last_error="Failed to connect to http://10.0.0.5:80/: [Errno 111] Connection refused",
        exc=ConnectionError("Device at 10.0.0.5:80 is not responding"),
        host="10.0.0.5", port=80, transport="http",
    )
    assert fault.code == CONNECTION_REFUSED


# --- client_missing --------------------------------------------------------

def test_client_missing_ssh_not_on_path():
    fault = classify_connection_fault(
        last_error="",
        exc=ConnectionError(
            "OpenSSH client ('ssh') not found on PATH. Install OpenSSH "
            "(bundled with Windows 10+/Linux/macOS) to use the SSH transport."
        ),
        host="h", port=22, transport="ssh",
    )
    assert fault.code == CLIENT_MISSING
    assert "Required client not found" in fault.message


# --- serial ----------------------------------------------------------------

def test_serial_open_failure_is_not_auth():
    # A serial "Permission denied" is an OS port-permission problem, never a
    # login failure — it must not classify as auth_failed.
    fault = classify_connection_fault(
        last_error="Failed to open serial port /dev/ttyUSB0: [Errno 13] Permission denied",
        exc=None, host="", port="/dev/ttyUSB0", transport="serial",
    )
    assert fault.code == UNREACHABLE
    assert "serial port" in fault.message
    assert "/dev/ttyUSB0" in fault.message


def test_serial_missing_port():
    fault = classify_connection_fault(
        last_error="could not open port 'COM7'", exc=None,
        host="", port="COM7", transport="serial",
    )
    assert fault.code == UNREACHABLE
    assert "COM7" in fault.message


def test_serial_unknown_drop_is_generic():
    fault = classify_connection_fault(
        last_error="", exc=None, host="", port="COM3", transport="serial",
    )
    assert fault.code == TRANSPORT_DISCONNECTED


# --- fallback --------------------------------------------------------------

def test_fallback_unexplained_drop():
    fault = classify_connection_fault(
        last_error="", exc=None, host="10.0.0.5", port=23, transport="tcp",
    )
    assert fault.code == TRANSPORT_DISCONNECTED
    assert fault.message


def test_fallback_unrecognized_error():
    fault = classify_connection_fault(
        last_error="something weird happened", exc=None,
        host="10.0.0.5", port=23, transport="tcp",
    )
    assert fault.code == TRANSPORT_DISCONNECTED


# --- endpoint rendering ----------------------------------------------------

def test_endpoint_degrades_without_host():
    fault = classify_connection_fault(
        last_error="Connection refused", exc=None, host="", port=None, transport="tcp",
    )
    assert fault.code == CONNECTION_REFUSED
    assert "the device" in fault.message
