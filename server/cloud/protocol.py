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
LOG = "log"
COMMAND_RESULT = "command_result"
TUNNEL_READY = "tunnel_ready"
DIAGNOSTIC_RESULT = "diagnostic_result"
PONG = "pong"
AI_TOOL_RESULT = "ai_tool_result"
PROJECT_DATA = "project_data"
DEVICE_COMMANDS_DATA = "device_commands_data"
GAP_REPORT = "gap_report"

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

# Sets for validation
HANDSHAKE_TYPES = {
    HELLO, CHALLENGE, AUTHENTICATE, SESSION_START,
    AUTH_FAILED, VERSION_MISMATCH, RESUME, RESUME_FROM,
}

UPSTREAM_TYPES = {
    HEARTBEAT, STATE_BATCH, ALERT, ALERT_RESOLVED, LOG,
    COMMAND_RESULT, TUNNEL_READY, DIAGNOSTIC_RESULT, PONG,
    AI_TOOL_RESULT, PROJECT_DATA, DEVICE_COMMANDS_DATA, GAP_REPORT,
}

DOWNSTREAM_TYPES = {
    ACK, SESSION_ROTATE, SESSION_INVALID, CONFIG_UPDATE,
    CAPABILITIES_UPDATE, THROTTLE, ERROR, COMMAND, CONFIG_PUSH,
    DIAGNOSTIC, SOFTWARE_UPDATE, TUNNEL_OPEN, TUNNEL_CLOSE,
    RESTART, PING, ALERT_RULES_UPDATE, AI_TOOL_CALL,
    GET_PROJECT, GET_DEVICE_COMMANDS,
}

# Message priority for buffer overflow (lower = dropped first)
MESSAGE_PRIORITY = {
    STATE_BATCH: 0,
    LOG: 1,
    HEARTBEAT: 2,
    ALERT_RESOLVED: 3,
    ALERT: 4,
    COMMAND_RESULT: 5,
    TUNNEL_READY: 5,
    DIAGNOSTIC_RESULT: 5,
    AI_TOOL_RESULT: 5,
    PROJECT_DATA: 5,
    DEVICE_COMMANDS_DATA: 5,
    GAP_REPORT: 7,
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
    hostname: str,
    project_name: str,
    capabilities: list[str],
    os_info: str,
    hardware: str,
    deployment_mode: str,
    python_version: str,
) -> dict[str, Any]:
    """Build a hello handshake message."""
    return {
        "type": HELLO,
        "ts": _now_iso(),
        "payload": {
            "protocol_version": PROTOCOL_VERSION,
            "system_id": system_id,
            "version": version,
            "hostname": hostname,
            "project_name": project_name,
            "capabilities": capabilities,
            "os": os_info,
            "hardware": hardware,
            "deployment_mode": deployment_mode,
            "python_version": python_version,
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
    """Build a resume message sent after re-handshake on reconnection."""
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


def build_heartbeat(
    seq: int,
    session_token: str,
    signing_key: bytes,
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
    """Build a signed heartbeat message."""
    payload: dict[str, Any] = {
        "uptime_seconds": uptime_seconds,
        "cpu_percent": round(cpu_percent, 1),
        "memory_percent": round(memory_percent, 1),
        "disk_percent": round(disk_percent, 1),
        "device_count": device_count,
        "devices_connected": devices_connected,
        "devices_error": devices_error,
        "active_ws_clients": active_ws_clients,
    }
    if temperature_celsius is not None:
        payload["temperature_celsius"] = round(temperature_celsius, 1)
    return build_signed_message(HEARTBEAT, payload, seq, session_token, signing_key)


def build_state_batch(
    seq: int,
    session_token: str,
    signing_key: bytes,
    changes: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build a signed state_batch message."""
    return build_signed_message(
        STATE_BATCH,
        {"changes": changes},
        seq,
        session_token,
        signing_key,
    )


def build_alert(
    seq: int,
    session_token: str,
    signing_key: bytes,
    alert_id: str,
    severity: str,
    category: str,
    device_id: str | None,
    message: str,
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a signed alert message."""
    payload: dict[str, Any] = {
        "alert_id": alert_id,
        "severity": severity,
        "category": category,
        "message": message,
    }
    if device_id is not None:
        payload["device_id"] = device_id
    if detail is not None:
        payload["detail"] = detail
    return build_signed_message(ALERT, payload, seq, session_token, signing_key)


def build_alert_resolved(
    seq: int,
    session_token: str,
    signing_key: bytes,
    alert_id: str,
) -> dict[str, Any]:
    """Build a signed alert_resolved message."""
    return build_signed_message(
        ALERT_RESOLVED,
        {"alert_id": alert_id},
        seq,
        session_token,
        signing_key,
    )


def build_log_message(
    seq: int,
    session_token: str,
    signing_key: bytes,
    level: str,
    source: str,
    message: str,
) -> dict[str, Any]:
    """Build a signed log message."""
    return build_signed_message(
        LOG,
        {"level": level, "source": source, "message": message},
        seq,
        session_token,
        signing_key,
    )


def build_command_result(
    seq: int,
    session_token: str,
    signing_key: bytes,
    request_id: str,
    success: bool,
    result: str | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    """Build a signed command_result message."""
    return build_signed_message(
        COMMAND_RESULT,
        {
            "request_id": request_id,
            "success": success,
            "result": result,
            "error": error,
        },
        seq,
        session_token,
        signing_key,
    )


def build_pong(
    seq: int,
    session_token: str,
    signing_key: bytes,
    nonce: str,
) -> dict[str, Any]:
    """Build a signed pong response to a ping."""
    return build_signed_message(
        PONG,
        {"nonce": nonce},
        seq,
        session_token,
        signing_key,
    )


def build_ai_tool_result(
    seq: int,
    session_token: str,
    signing_key: bytes,
    request_id: str,
    success: bool,
    result: Any = None,
    error: str | None = None,
) -> dict[str, Any]:
    """Build a signed ai_tool_result message (agent → cloud)."""
    return build_signed_message(
        AI_TOOL_RESULT,
        {
            "request_id": request_id,
            "success": success,
            "result": result,
            "error": error,
        },
        seq,
        session_token,
        signing_key,
    )


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
