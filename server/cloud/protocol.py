"""
OpenAVC Cloud — Protocol message types, builders, and parsers.

Defines all message types for the cloud agent protocol, provides builder
functions for constructing properly formatted messages, and a parser for
validating and extracting incoming messages.

Messages fall into two categories:
1. Handshake messages (hello, challenge, authenticate, session_start, etc.)
   — no seq, session, or sig fields.
2. Steady-state messages (heartbeat, state_batch, alert, command, etc.)
   — include seq, session, and sig fields for authentication and ordering.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from server.cloud.crypto import sign_message, verify_message_signature


# --- Protocol Version ---

PROTOCOL_VERSION = 1


# --- Message Type Constants ---

# Handshake (agent → cloud)
HELLO = "hello"
AUTHENTICATE = "authenticate"
RESUME = "resume"

# Handshake (cloud → agent)
CHALLENGE = "challenge"
SESSION_START = "session_start"
AUTH_FAILED = "auth_failed"
VERSION_MISMATCH = "version_mismatch"
RESUME_FROM = "resume_from"

# Steady-state upstream (agent → cloud)
HEARTBEAT = "heartbeat"
STATE_BATCH = "state_batch"
ALERT = "alert"
ALERT_RESOLVED = "alert_resolved"
COMMAND_RESULT = "command_result"
TUNNEL_READY = "tunnel_ready"
TUNNEL_FAILED = "tunnel_failed"
DIAGNOSTIC_RESULT = "diagnostic_result"
PONG = "pong"
AI_TOOL_RESULT = "ai_tool_result"
PROJECT_DATA = "project_data"
DEVICE_COMMANDS_DATA = "device_commands_data"
GAP_REPORT = "gap_report"
CERT_REQUEST = "cert_request"
CERT_STATUS = "cert_status"

# Steady-state downstream (cloud → agent)
ACK = "ack"
SESSION_ROTATE = "session_rotate"
SESSION_INVALID = "session_invalid"
CONFIG_UPDATE = "config_update"
CAPABILITIES_UPDATE = "capabilities_update"
THROTTLE = "throttle"
ERROR = "error"
COMMAND = "command"
CONFIG_PUSH = "config_push"
DIAGNOSTIC = "diagnostic"
SOFTWARE_UPDATE = "software_update"
TUNNEL_OPEN = "tunnel_open"
TUNNEL_CLOSE = "tunnel_close"
RESTART = "restart"
PING = "ping"
ALERT_RULES_UPDATE = "alert_rules_update"
AI_TOOL_CALL = "ai_tool_call"
GET_PROJECT = "get_project"
GET_DEVICE_COMMANDS = "get_device_commands"
CERT_RESULT = "cert_result"
CERT_RENEW_DUE = "cert_renew_due"

# Sets for validation
HANDSHAKE_TYPES = {
    HELLO, CHALLENGE, AUTHENTICATE, SESSION_START,
    AUTH_FAILED, VERSION_MISMATCH, RESUME, RESUME_FROM,
}

UPSTREAM_TYPES = {
    HEARTBEAT, STATE_BATCH, ALERT, ALERT_RESOLVED,
    COMMAND_RESULT, TUNNEL_READY, TUNNEL_FAILED, DIAGNOSTIC_RESULT, PONG,
    AI_TOOL_RESULT, PROJECT_DATA, DEVICE_COMMANDS_DATA, GAP_REPORT,
    CERT_REQUEST, CERT_STATUS,
}

DOWNSTREAM_TYPES = {
    ACK, SESSION_ROTATE, SESSION_INVALID, CONFIG_UPDATE,
    CAPABILITIES_UPDATE, THROTTLE, ERROR, COMMAND, CONFIG_PUSH,
    DIAGNOSTIC, SOFTWARE_UPDATE, TUNNEL_OPEN, TUNNEL_CLOSE,
    RESTART, PING, ALERT_RULES_UPDATE, AI_TOOL_CALL,
    GET_PROJECT, GET_DEVICE_COMMANDS, CERT_RESULT, CERT_RENEW_DUE,
}

# Message priority for buffer overflow (lower = dropped first)
MESSAGE_PRIORITY = {
    STATE_BATCH: 0,
    HEARTBEAT: 2,
    ALERT_RESOLVED: 3,
    ALERT: 4,
    COMMAND_RESULT: 5,
    TUNNEL_READY: 5,
    TUNNEL_FAILED: 5,
    DIAGNOSTIC_RESULT: 5,
    AI_TOOL_RESULT: 5,
    PROJECT_DATA: 5,
    DEVICE_COMMANDS_DATA: 5,
    GAP_REPORT: 7,
    CERT_REQUEST: 5,
    CERT_STATUS: 5,
    PONG: 6,
    # Downstream message priorities (for buffer overflow decisions)
    SESSION_ROTATE: 10,  # Highest — must never be dropped
    ACK: 9,
    SESSION_INVALID: 9,
    CONFIG_UPDATE: 7,
    CAPABILITIES_UPDATE: 7,
    COMMAND: 7,
    CONFIG_PUSH: 7,
    RESTART: 8,
    PING: 6,
    TUNNEL_OPEN: 5,
    TUNNEL_CLOSE: 5,
    DIAGNOSTIC: 4,
    SOFTWARE_UPDATE: 4,
    ALERT_RULES_UPDATE: 4,
    AI_TOOL_CALL: 5,
    GET_PROJECT: 5,
    CERT_RESULT: 5,
    CERT_RENEW_DUE: 4,
    THROTTLE: 3,
    ERROR: 3,
}


# --- Timestamp ---

def _now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


# --- Handshake Message Builders (no sig/seq/session) ---

def build_hello(
    system_id: str,
    version: str,
    capabilities: list[str],
    os_info: str,
    hardware: str,
    deployment_mode: str,
) -> dict[str, Any]:
    """Build a hello handshake message.

    Carries exactly what the cloud persists on the System record (version,
    os, hardware, deployment_mode, capabilities) plus the identity and
    protocol version the handshake needs — nothing speculative.
    """
    return {
        "type": HELLO,
        "ts": _now_iso(),
        "payload": {
            "protocol_version": PROTOCOL_VERSION,
            "system_id": system_id,
            "version": version,
            "capabilities": capabilities,
            "os": os_info,
            "hardware": hardware,
            "deployment_mode": deployment_mode,
        },
    }


def build_authenticate(system_id: str, timestamp: str, proof: str) -> dict[str, Any]:
    """Build an authenticate handshake message."""
    return {
        "type": AUTHENTICATE,
        "ts": _now_iso(),
        "payload": {
            "system_id": system_id,
            "timestamp": timestamp,
            "proof": proof,
        },
    }


def build_resume(
    last_ack_seq: int, buffered_count: int, disconnected_at: str
) -> dict[str, Any]:
    """Build a resume message sent after re-handshake on reconnection.

    The payload fields are diagnostics (they show up in cloud logs), not
    negotiation inputs: the cloud always replies resume_from with
    replay_from_seq=1 — replay the entire unacked buffer, re-numbered into
    the new session. Delivery is at-least-once; upstream consumers are
    expected to tolerate re-delivery.
    """
    return {
        "type": RESUME,
        "ts": _now_iso(),
        "payload": {
            "last_ack_seq": last_ack_seq,
            "buffered_count": buffered_count,
            "disconnected_at": disconnected_at,
        },
    }


# --- Steady-State Message Builders (signed) ---

def build_signed_message(
    msg_type: str,
    payload: dict[str, Any],
    seq: int,
    session_token: str,
    signing_key: bytes,
) -> dict[str, Any]:
    """
    Build a signed steady-state message.

    Constructs the message with type, ts, seq, session, and payload,
    then computes and appends the HMAC signature.

    Args:
        msg_type: Message type constant (e.g., HEARTBEAT).
        payload: Message-specific payload dict.
        seq: Upstream sequence number.
        session_token: Current session token string.
        signing_key: Session signing key bytes.

    Returns:
        Complete message dict including 'sig' field.
    """
    msg = {
        "type": msg_type,
        "ts": _now_iso(),
        "seq": seq,
        "session": session_token,
        "payload": payload,
    }
    msg["sig"] = sign_message(signing_key, msg)
    return msg


# --- Steady-State Payload Builders (the upstream payload contract) ---
#
# Every steady-state upstream message the agent sends is assembled from one
# of these builders; CloudAgent envelopes, sequences, and signs it at send
# time (seq comes from the sequencer, which also buffers for replay — that is
# why these build payloads, not full messages). The cloud reads exactly these
# shapes: change one only together with its cloud-side reader and spec §13.


def build_heartbeat_payload(
    uptime_seconds: int,
    cpu_percent: float,
    memory_percent: float,
    disk_percent: float,
    device_count: int,
    devices_connected: int,
    devices_error: int,
    active_ws_clients: int,
    temperature_celsius: float | None = None,
) -> dict[str, Any]:
    """Heartbeat metrics payload. temperature_celsius is omitted when the
    platform exposes no sensor."""
    payload: dict[str, Any] = {
        "uptime_seconds": uptime_seconds,
        "cpu_percent": cpu_percent,
        "memory_percent": memory_percent,
        "disk_percent": disk_percent,
        "device_count": device_count,
        "devices_connected": devices_connected,
        "devices_error": devices_error,
        "active_ws_clients": active_ws_clients,
    }
    if temperature_celsius is not None:
        payload["temperature_celsius"] = temperature_celsius
    return payload


def build_state_batch_payload(
    changes: list[dict[str, Any]], snapshot: bool = False
) -> dict[str, Any]:
    """State-change batch. The first batch of a session sets snapshot=True
    (only on its first chunk) so the cloud clears stale keys."""
    payload: dict[str, Any] = {"changes": changes}
    if snapshot:
        payload["snapshot"] = True
    return payload


def build_alert_payload(
    alert_id: str,
    rule_id: str,
    severity: str,
    category: str,
    device_id: str | None,
    message: str,
    detail: dict[str, Any],
) -> dict[str, Any]:
    """Alert-fired payload."""
    return {
        "alert_id": alert_id,
        "rule_id": rule_id,
        "severity": severity,
        "category": category,
        "device_id": device_id,
        "message": message,
        "detail": detail,
    }


def build_alert_resolved_payload(alert_id: str) -> dict[str, Any]:
    """Alert-resolved payload."""
    return {"alert_id": alert_id}


def _request_result_payload(
    request_id: str, success: bool, result: Any, error: str | None
) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "success": success,
        "result": result,
        "error": error,
    }


def build_command_result_payload(
    request_id: str, success: bool, result: Any = None, error: str | None = None
) -> dict[str, Any]:
    """Result payload for command / config_push / restart / software_update."""
    return _request_result_payload(request_id, success, result, error)


def build_diagnostic_result_payload(
    request_id: str, success: bool, result: Any = None, error: str | None = None
) -> dict[str, Any]:
    """Result payload for a diagnostic request."""
    return _request_result_payload(request_id, success, result, error)


def build_ai_tool_result_payload(
    request_id: str, success: bool, result: Any = None, error: str | None = None
) -> dict[str, Any]:
    """Result payload for an ai_tool_call."""
    return _request_result_payload(request_id, success, result, error)


def build_project_data_payload(
    request_id: str,
    success: bool,
    project_json: Any = None,
    error: str | None = None,
) -> dict[str, Any]:
    """Result payload for get_project: project_json on success, error otherwise."""
    payload: dict[str, Any] = {"request_id": request_id, "success": success}
    if success:
        payload["project_json"] = project_json
    else:
        payload["error"] = error
    return payload


def build_device_commands_data_payload(
    request_id: str,
    success: bool,
    devices: list[dict[str, Any]] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    """Result payload for get_device_commands: devices on success, error otherwise."""
    payload: dict[str, Any] = {"request_id": request_id, "success": success}
    if success:
        payload["devices"] = devices if devices is not None else []
    else:
        payload["error"] = error
    return payload


def build_pong_payload(nonce: str) -> dict[str, Any]:
    """Pong payload echoing the ping's nonce (the cloud's liveness check
    only accepts a nonce-matched pong)."""
    return {"nonce": nonce}


def build_tunnel_ready_payload(tunnel_id: str) -> dict[str, Any]:
    """Tunnel established acknowledgment."""
    return {"tunnel_id": tunnel_id}


def build_tunnel_failed_payload(tunnel_id: str, reason: str) -> dict[str, Any]:
    """Tunnel failure (or refusal) with a human-readable reason."""
    return {"tunnel_id": tunnel_id, "reason": reason}


def build_gap_report_payload(missing_from: int, missing_to: int) -> dict[str, Any]:
    """Downstream sequence-gap report (inclusive range of missing seqs)."""
    return {"missing_from": missing_from, "missing_to": missing_to}


def build_cert_request_payload(csr_pem: str | None = None) -> dict[str, Any]:
    """Trusted-certificate request: empty payload asks for enrollment
    (label/zone assignment); csr_pem asks for issuance."""
    return {"csr_pem": csr_pem} if csr_pem is not None else {}


def build_cert_status_payload(state: str) -> dict[str, Any]:
    """Trusted-certificate status report (e.g. 'installed', 'disabled')."""
    return {"state": state}


# Payload builder per upstream steady-state type. Complete by construction —
# a test asserts this covers UPSTREAM_TYPES exactly. The result-shaped
# entries (command_result, diagnostic_result, ai_tool_result, project_data,
# device_commands_data) all accept (request_id, success, error) keywords, so
# refusal NACKs can be built generically from the result type alone.
UPSTREAM_PAYLOAD_BUILDERS = {
    HEARTBEAT: build_heartbeat_payload,
    STATE_BATCH: build_state_batch_payload,
    ALERT: build_alert_payload,
    ALERT_RESOLVED: build_alert_resolved_payload,
    COMMAND_RESULT: build_command_result_payload,
    DIAGNOSTIC_RESULT: build_diagnostic_result_payload,
    AI_TOOL_RESULT: build_ai_tool_result_payload,
    PROJECT_DATA: build_project_data_payload,
    DEVICE_COMMANDS_DATA: build_device_commands_data_payload,
    PONG: build_pong_payload,
    TUNNEL_READY: build_tunnel_ready_payload,
    TUNNEL_FAILED: build_tunnel_failed_payload,
    GAP_REPORT: build_gap_report_payload,
    CERT_REQUEST: build_cert_request_payload,
    CERT_STATUS: build_cert_status_payload,
}


# --- Message Parsing and Validation ---

class ProtocolError(Exception):
    """Raised when a message fails validation."""
    pass


def parse_message(raw: str | bytes) -> dict[str, Any]:
    """
    Parse a raw JSON message string into a dict.

    Validates that the message has the required 'type' field.
    Does NOT verify signatures — use verify_steady_state_message for that.

    Args:
        raw: JSON string or bytes.

    Returns:
        Parsed message dict.

    Raises:
        ProtocolError: If the message is not valid JSON or missing 'type'.
    """
    import json as _json
    try:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        msg = _json.loads(raw)
    except (ValueError, UnicodeDecodeError) as e:
        raise ProtocolError(f"Invalid JSON: {e}")

    if not isinstance(msg, dict):
        raise ProtocolError("Message must be a JSON object")
    if "type" not in msg:
        raise ProtocolError("Message missing 'type' field")

    return msg


def is_handshake_message(msg: dict[str, Any]) -> bool:
    """Check if a message is a handshake message (no signature required)."""
    return msg.get("type") in HANDSHAKE_TYPES


def verify_steady_state_message(
    msg: dict[str, Any], signing_key: bytes
) -> bool:
    """
    Verify the signature on a steady-state message.

    Args:
        msg: Parsed message dict (must contain 'sig' field).
        signing_key: The session signing key.

    Returns:
        True if the signature is valid.

    Raises:
        ProtocolError: If the message is missing required fields.
    """
    for field in ("seq", "session", "sig"):
        if field not in msg:
            raise ProtocolError(f"Steady-state message missing '{field}' field")

    sig = msg["sig"]
    return verify_message_signature(signing_key, msg, sig)


def extract_payload(msg: dict[str, Any]) -> dict[str, Any]:
    """Extract the payload from a message, defaulting to empty dict."""
    return msg.get("payload", {})
