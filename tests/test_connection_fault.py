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

import pytest

from server.core.connection_fault import (
    AUTH_FAILED,
    CLIENT_MISSING,
    CONNECTION_REFUSED,
    HOST_KEY_REJECTED,
    INVALID_CONFIG,
    NO_RESPONSE,
    TLS_CERT_UNTRUSTED,
    TRANSPORT_DISCONNECTED,
    UNREACHABLE,
    ConnectionFaultError,
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


# --- tls_cert_untrusted ----------------------------------------------------

def test_tls_cert_untrusted_self_signed_httpx_string():
    # The exact string httpx surfaces for a self-signed cert (captured live
    # from httpx 0.28 + OpenSSL 3), which HTTPClientTransport stashes as
    # last_error and the device manager feeds here.
    fault = classify_connection_fault(
        last_error=(
            "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: "
            "self-signed certificate (_ssl.c:1010)"
        ),
        exc=None, host="10.0.0.9", port=443, transport="http",
    )
    assert fault.code == TLS_CERT_UNTRUSTED
    assert "Verify SSL Certificate" in fault.message
    assert "10.0.0.9:443" in fault.message


@pytest.mark.parametrize("err", [
    "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: "
    "unable to get local issuer certificate (_ssl.c:1010)",
    "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: "
    "certificate has expired (_ssl.c:1010)",
])
def test_tls_cert_untrusted_other_verification_failures(err):
    fault = classify_connection_fault(
        last_error=err, exc=None, host="h", port=443, transport="http",
    )
    assert fault.code == TLS_CERT_UNTRUSTED


def test_tls_cert_untrusted_beats_generic_timeout_noise():
    # A cert-verify failure is a specific identity problem — it must not be
    # swallowed by the weak timeout/unreachable bucket if the blob also
    # mentions a timeout.
    fault = classify_connection_fault(
        last_error="certificate verify failed: self-signed certificate; connection timeout",
        exc=None, host="h", port=443, transport="http",
    )
    assert fault.code == TLS_CERT_UNTRUSTED


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


# --- typed ConnectionFaultError (drivers declare the code explicitly) --------


def test_typed_fault_wins_over_contradicting_strings():
    """A typed code beats the string tables: the message here contains
    'operation timed out' (a strong-unreachable signature checked before
    no_response), but the driver said no_response — no_response it is."""
    exc = ConnectionFaultError(
        "Device went silent (The read operation timed out)", code=NO_RESPONSE
    )
    fault = classify_connection_fault(
        last_error="", exc=exc, host="10.0.0.9", port=80, transport="http",
    )
    assert fault.code == NO_RESPONSE
    assert "went silent" in fault.message


def test_typed_fault_honored_through_cause_chain():
    """Transports wrap driver errors — a typed fault buried in __cause__ is
    still honored, and its own message (not the wrapper's) is surfaced."""
    exc = _wrap(
        "Failed to connect to device",
        ConnectionFaultError("Login rejected by the device", code=AUTH_FAILED),
    )
    fault = classify_connection_fault(
        last_error="", exc=exc, host="pdu.local", port=23, transport="tcp",
    )
    assert fault.code == AUTH_FAILED
    assert fault.message == "Login rejected by the device"


def test_typed_fault_empty_message_uses_taxonomy_default():
    exc = ConnectionFaultError(code=AUTH_FAILED)
    fault = classify_connection_fault(
        last_error="", exc=exc, host="h", port=23, transport="tcp",
    )
    assert fault.code == AUTH_FAILED
    assert "Authentication failed" in fault.message


def test_invalid_config_typed_fault_surfaces_verbatim():
    """A bad-serial-setting ValueError, typed as invalid_config, keeps its
    actionable message instead of being classified as a transient disconnect."""
    exc = _wrap(
        "reconnect wrapper",
        ConnectionFaultError(
            "Invalid serial settings for /dev/ttyUSB0: Not a valid parity: 'X'. "
            "Check the baud rate, parity, data bits, and stop bits.",
            code=INVALID_CONFIG,
        ),
    )
    fault = classify_connection_fault(
        last_error="not a valid parity", exc=exc,
        host="", port="/dev/ttyUSB0", transport="serial",
    )
    assert fault.code == INVALID_CONFIG
    assert "parity" in fault.message.lower()


def test_invalid_config_default_message():
    exc = ConnectionFaultError(code=INVALID_CONFIG)
    fault = classify_connection_fault(
        last_error="", exc=exc, host="", port="COM7", transport="serial",
    )
    assert fault.code == INVALID_CONFIG
    assert "connection settings are invalid" in fault.message.lower()


def test_typed_fault_unknown_code_fails_at_construction():
    """A typo'd code must fail loudly at the raise site, not silently
    misclassify forever."""
    with pytest.raises(ValueError, match="Unknown connection-fault code"):
        ConnectionFaultError("boom", code="auth_failure")


def test_typed_fault_bridge_offline_not_declarable():
    """bridge_offline is assigned by the DeviceManager, never by a driver."""
    with pytest.raises(ValueError):
        ConnectionFaultError("x", code="bridge_offline")


# ── A device a running simulation could not simulate ────────────────────────
#
# Simulation redirects each device to a simulator it starts. A driver with no
# simulator gets none, so that device alone keeps pointing at its real address
# and fails there — and the classifier, reading a genuinely refused socket,
# correctly answered a question the author was not asking ("is the port
# right?"). It was; nothing was listening.

def test_no_simulator_fault_names_the_driver_and_the_fix():
    from server.core.connection_fault import NO_SIMULATOR, no_simulator_fault

    fault = no_simulator_fault("acme_widget")
    assert fault.code == NO_SIMULATOR
    assert "acme_widget" in fault.message
    assert "_sim.py" in fault.message          # says what to add
    assert "port" not in fault.message.lower()  # and does not blame the port


def test_no_simulator_fault_reads_without_a_driver_id():
    from server.core.connection_fault import no_simulator_fault

    message = no_simulator_fault("").message
    assert "''" not in message and "  " not in message


def test_the_simulation_gap_wins_over_a_refused_socket():
    """The device really was refused, so every other signal is accurate and
    misleading at once. The gap has to be checked before them."""
    from server.core.connection_fault import NO_SIMULATOR
    from server.core.device_manager import DeviceManager
    from server.core.event_bus import EventBus
    from server.core.state_store import StateStore

    state = StateStore()
    manager = DeviceManager(state, EventBus())
    exc = ConnectionRefusedError(errno.ECONNREFUSED, "Connection refused")

    # Without the hook: the honest, unhelpful answer.
    assert manager._set_offline_reason("dev1", None, exc=exc) == CONNECTION_REFUSED

    manager.unsimulated_driver = lambda device_id: "acme_widget"
    assert manager._set_offline_reason("dev1", None, exc=exc) == NO_SIMULATOR
    assert "acme_widget" in state.get("device.dev1.offline_detail")


def test_a_simulated_device_is_left_to_the_normal_classifier():
    """The hook answers None for a device that did get a simulator, so a real
    fault inside a simulated bench still classifies normally."""
    from server.core.device_manager import DeviceManager
    from server.core.event_bus import EventBus
    from server.core.state_store import StateStore

    manager = DeviceManager(StateStore(), EventBus())
    manager.unsimulated_driver = lambda device_id: None
    exc = ConnectionRefusedError(errno.ECONNREFUSED, "Connection refused")
    assert manager._set_offline_reason("dev1", None, exc=exc) == CONNECTION_REFUSED
