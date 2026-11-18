"""
OpenAVC Cloud: THE wire contract between this agent and the cloud.

Every message the two sides exchange, its fields, the vocabularies they draw
from and the limits on them are rows in ``wire_tables.py``, which only this
module imports; every other agent file asks this one. From those rows it
derives the message sets, the buffer priorities, the capability each
downstream request needs and the result that answers it, and it reads each
downstream payload through ``read_payload`` (or ``read_tunnel_data`` on a
tunnel's data socket), which fills in the table's default for a field the
cloud left out. A request (a message ``REQUEST_ANSWERS`` names) is read
through ``read_request`` first, which refuses one whose payload is not an
object, that carries no ``request_id``, or that sends a field as a JSON type
its row does not declare, before any handler runs. It also builds every
upstream message. ``contract_gen.py``
renders the same rows into ``wire_contract.json`` for the cloud.

Messages fall into three phases:
1. Handshake (hello, challenge, authenticate, session_start, ...): no seq,
   session or sig.
2. Session (heartbeat, state_batch, command, ...): seq, session and sig for
   ordering and authentication.
3. A tunnel's data socket (http_request, ws_frame, ...): flat messages whose
   fields sit beside ``type``.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any

from openavc.cloud import wire_tables
from openavc.cloud.crypto import sign_message, verify_message_signature
from openavc.cloud.tools.input_shapes import json_kind
from openavc.cloud.wire_tables import ABSENT, MESSAGES, VOCABULARIES


def _limit(name: str) -> int:
    return wire_tables.LIMITS[name][0]


def vocabulary(name: str) -> tuple[Any, ...]:
    """The values of one of the contract's vocabularies, in order."""
    return tuple(entry.value for entry in VOCABULARIES[name])


# --- Protocol Version ---

# The wire-contract version this agent prefers to speak. It is a contract
# number, not a product version — it moves only when the shape of the wire
# changes in a way an older reader can't tolerate.
PROTOCOL_VERSION = _limit("protocol_version")

# The oldest wire version this agent still speaks. Both sides accept anything
# in MIN_PROTOCOL_VERSION..PROTOCOL_VERSION and settle on the highest version
# they share, so the two halves can be deployed in either order. That matters
# because the order is never simultaneous in the field: the cloud always
# updates first, and a fielded agent must keep working across that window.
MIN_PROTOCOL_VERSION = _limit("min_protocol_version")

# Advertised in hello so the cloud can pick the highest mutually-supported
# version instead of demanding an exact match.
SUPPORTED_PROTOCOL_VERSIONS = list(range(MIN_PROTOCOL_VERSION, PROTOCOL_VERSION + 1))


# --- Wire Size Limits ---

# Hard cap on a single wire message, enforced on BOTH sides' inbound paths
# (agent: websockets max_size closes the connection; cloud: app-level check
# drops the message with a message_too_large error).
MAX_MESSAGE_BYTES = _limit("max_message_bytes")

# Headroom subtracted when a sender pre-checks a payload against the cap:
# covers the envelope fields added after the check (seq, session, sig, ts)
# and result-wrapper keys around an embedded document.
MESSAGE_SIZE_MARGIN = _limit("message_size_margin")

# Largest embedded document (e.g. a project JSON) a sender may put in one
# message. Anything bigger must be refused with a typed error BEFORE it is
# sequenced — an oversize message in the replay buffer would be re-sent and
# re-rejected on every reconnect forever.
MAX_EMBED_BYTES = _limit("max_embed_bytes")

# The longest wait between two reconnect attempts.
RECONNECT_BACKOFF_MAX = _limit("reconnect_backoff_max_seconds")

# A tunnel's data socket carries whole uploads after base64, so its ceiling is
# well above the control connection's.
TUNNEL_DATA_MAX_BYTES = _limit("tunnel_data_max_bytes")

# How long a proxied response may sit with nothing arriving.
TUNNEL_STREAM_IDLE_SECONDS = _limit("tunnel_stream_idle_seconds")

# The longest room name pairing sends: what the cloud's rename field accepts.
ROOM_NAME_MAX_CHARS = _limit("room_name_max_chars")


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
MONITORS = "monitors"
#: Everything this instance currently has firing, sent once per connection.
#: An alert resolves when the reading recovers -- and the record of what is
#: firing lives in memory, so a restart loses it and the resolve is never sent.
#: The alert then sits in the cloud forever with nothing left that could clear
#: it. This is what closes that: the cloud resolves anything open for this
#: system that is not named here.
#:
#: An instance too old to send it sends NOTHING, and nothing must mean nothing
#: -- see the cloud's reader. That is why the list rides its own message rather
#: than becoming a field on the heartbeat: a missing field and an empty list
#: are indistinguishable in a dict, and reading one as the other resolves every
#: open alert in the fleet.
ACTIVE_ALERTS = "active_alerts"
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
# Stage a newer copy of the panel software on appliance hardware
# (updater/panel_software.py). A sibling of software_update, never a branch of
# it: it does not touch OpenAVC itself, restart anything or take a backup.
PANEL_SOFTWARE_UPDATE = "panel_software_update"
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
# Somebody in the cloud said they are on it. The room hears about it, which is
# what makes "Ask for help" a feature rather than a mail-sender.
HELP_ACKNOWLEDGED = "help_acknowledged"

# A tunnel's data socket (tunnel.py), flat messages beside `type`.
HTTP_REQUEST = "http_request"
HTTP_CANCEL = "http_cancel"
HTTP_RESPONSE = "http_response"
HTTP_RESPONSE_START = "http_response_start"
HTTP_BODY = "http_body"
HTTP_BODY_END = "http_body_end"
WS_OPEN = "ws_open"
WS_FRAME = "ws_frame"
WS_CLOSE = "ws_close"

# Every row of the contract, by type.
MESSAGE_ROWS: dict[str, wire_tables.Message] = {m.type: m for m in MESSAGES}


def _types(phase: str, *directions: str) -> set[str]:
    return {m.type for m in MESSAGES if m.phase == phase and m.direction in directions}


# Sets for validation
HANDSHAKE_TYPES = {m.type for m in MESSAGES if m.phase == "handshake"}
UPSTREAM_TYPES = _types("session", "up")
DOWNSTREAM_TYPES = _types("session", "down")
TUNNEL_DATA_TYPES = {m.type for m in MESSAGES if m.phase == "tunnel_data"}

# Message priority for buffer overflow (lower = dropped first). A message with
# none takes the sequencer's default. session_rotate is the highest: it must
# never be dropped. active_alerts is worth as much as an alert, because
# dropping it leaves an alert nobody can clear.
MESSAGE_PRIORITY = {m.type: m.priority for m in MESSAGES if m.priority is not None}

# The capability a session must have been granted before this agent acts on a
# downstream message (defense in depth: the cloud gates them too).
CAPABILITY_GATED = {m.type: m.requires for m in MESSAGES if m.requires}

# The typed result that answers each downstream request, and so answers a
# refusal of it: the cloud resolves a pending request only from its own result
# type. A request with no request_id has nothing to correlate a refusal with.
REQUEST_ANSWERS = {
    m.type: m.answer for m in MESSAGES
    if m.answer and any(f.name == "request_id" for f in m.fields)
}


# --- Vocabularies ---

# What this instance can do, announced in its hello. The cloud answers with
# the subset it grants.
CAPABILITIES = vocabulary("capabilities")

# auth_failed reasons that mean the system key itself is gone: retrying earns
# the same refusal forever, so the agent stops.
FATAL_REFUSAL_REASONS = frozenset(
    e.value for e in VOCABULARIES["refusal_reasons"] if "stops_agent" in e.flags
)

# The state keys the cloud's built-in alert rules read, written from the
# heartbeat's own metrics.
BUILTIN_ALERT_KEYS = vocabulary("builtin_alert_keys")

# The names a trusted certificate carries, filled with the label and zone the
# cloud's enrollment answer gives.
CERTIFICATE_NAMES = vocabulary("certificate_names")

# A config push's result when there was no earlier project to send back.
CONFIG_APPLIED = vocabulary("config_push_results")[0]

# Why an alert ended, on the `reason` field of `alert_resolved`. An instance
# can only ever report two things, and the difference between them is what a
# service report prints as how fast a fault was dealt with: the span from
# `fired_at` measures a repair for the first and nothing at all for the second.
#
# The reading came back. This is the only one that is a repair.
RESOLVED_RECOVERED = "recovered"
# The rule went away, so nobody is asking the question any more. Nothing
# happened in the room. This instance deliberately does NOT say whether the
# rule was disabled, deleted, re-scoped away from this system or removed from
# the project: all it sees is a rule that left the set it evaluates, and
# claiming to know which would be a guess. The cloud knows for the doors it
# owns and records those more precisely.
RESOLVED_RULE_REMOVED = "rule_removed"

# A tunnel's WebSocket close codes. A code the far end closed with is passed
# on as it is when a close frame may carry it; these are what this instance
# sends when there is no such code.
WS_CLOSE_NORMAL = 1000
WS_CLOSE_LOCAL_UNREACHABLE = 1014
# 1005 (no code given) reads as a normal close; 1006 and 1015 never appear in
# a close frame, so a socket that ended that way was dropped, not closed.
_NO_STATUS = 1005
_SENDABLE_CLOSE_CODES = frozenset(
    code
    for low, high in wire_tables.LIMITS["ws_close_sendable_codes"][0]
    for code in range(low, high + 1)
)


def ws_close_code(code: int | None) -> int:
    """The close code to pass on for a socket that ended with ``code``."""
    if code in _SENDABLE_CLOSE_CODES:
        return int(code)
    if code == _NO_STATUS:
        return WS_CLOSE_NORMAL
    return WS_CLOSE_LOCAL_UNREACHABLE


def sendable_close_code(code: Any) -> int | None:
    """A received close code this end may close its own socket with, else None."""
    if isinstance(code, bool) or not isinstance(code, int):
        return None
    return code if code in _SENDABLE_CLOSE_CODES else None


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
            "supported_versions": SUPPORTED_PROTOCOL_VERSIONS,
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
# shapes: change one only together with its cloud-side reader.


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
    notify_only: bool | None = None,
) -> dict[str, Any]:
    """Heartbeat metrics payload. temperature_celsius is omitted when the
    platform exposes no sensor.

    ``notify_only`` is the one field here that is not a measurement: it is the
    local "tell me, don't install" switch, and the cloud has no other way to
    know that a system will decline the maintenance window its fleet policy
    schedules. It rides the heartbeat rather than the hello because it is
    edited while the agent is connected, and an answer the portal shows must
    not be a session old.

    Omitted when nobody looked, the same way the temperature is. The cloud
    reads a missing key as "has not said" and shows nothing, which is the only
    honest answer from a caller that did not read the setting -- ``False``
    there would assert the opposite of what half of those systems are set to.
    """
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
    if notify_only is not None:
        payload["notify_only"] = bool(notify_only)
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
    fired_at: str | None = None,
) -> dict[str, Any]:
    """Alert-fired payload.

    ``fired_at`` is when THIS instance saw the fault, as an ISO-8601 UTC
    instant — the same thing every state change already carries in its own
    ``ts``, and here for the same reason. Without it the cloud stamped its
    receipt time and called that the fire time, so every delay between the two
    was quietly subtracted from the fault: the send loop's flush (up to a
    second), and a replay across a reconnect (as long as the reconnect took).
    A fault that fired and healed inside one flush tick arrived as a
    millisecond-long repair, and that span is what the printable service report
    renders as how fast somebody dealt with it.

    Defaults to now because every caller builds this at the moment it registers
    the alert as active. It is the moment the ALERT fired, not the moment the
    reading first went wrong: a rule with a ``duration_seconds`` is deliberately
    not complaining during its grace window, so counting that window as part of
    the fault would report a repair time nobody could have shortened.
    """
    return {
        "alert_id": alert_id,
        "rule_id": rule_id,
        "severity": severity,
        "category": category,
        "device_id": device_id,
        "message": message,
        "detail": detail,
        "fired_at": fired_at or _now_iso(),
    }


def build_alert_resolved_payload(
    alert_id: str,
    resolved_at: str | None = None,
    reason: str = RESOLVED_RECOVERED,
) -> dict[str, Any]:
    """Alert-resolved payload.

    ``resolved_at`` is when this instance saw the reading come back, and it
    rides here for the same reason ``fired_at`` rides on the alert: what the
    report prints is a SPAN, so stamping one end on the agent's clock and the
    other on the cloud's would leave the queueing delay in the answer — and
    would mix two clocks in one subtraction, which can go negative. Both ends
    now come from the one clock that watched the fault.

    ``reason`` says which of the two things happened, because the span above is
    only a repair time for one of them — see the constants.
    """
    return {
        "alert_id": alert_id,
        "resolved_at": resolved_at or _now_iso(),
        "reason": reason,
    }


def build_active_alerts_payload(alert_ids: list[str]) -> dict[str, Any]:
    """Everything firing here right now, as the cloud's reconcile list.

    Sent once per connection. An empty list is a real answer -- "nothing is
    firing in this room" -- and is the usual one after a restart, because what
    was firing before the restart was remembered in memory and is gone. The
    cloud resolves what it still has open for this system and this list does not
    name; that is the only thing that keeps *unresolved* meaning *the reading is
    still wrong*.

    It says nothing about a help request. Those are raised by a person, never
    recover on their own, and are not in the set this reads from -- the cloud
    leaves them alone rather than trusting the omission.
    """
    return {"alert_ids": list(alert_ids)}


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
    """Result payload for command / config_push / restart / software_update /
    panel_software_update."""
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
    revision: str | None = None,
) -> dict[str, Any]:
    """Result payload for get_project: project_json and the revision it was
    read at on success, error otherwise."""
    payload: dict[str, Any] = {"request_id": request_id, "success": success}
    if success:
        payload["project_json"] = project_json
        if revision is not None:
            payload["revision"] = revision
    else:
        payload["error"] = error
    return payload


def build_monitors_payload(
    monitors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Which readings this room says matter, and what normal is for each.

    Sent unprompted on connect and on every project apply — NOT read out of the
    daily project snapshot, which is up to 24 hours stale. Tagging a reading and
    seeing it appear is the whole of the feature, and a day's wait is not that.

    A peer of ``project_data`` rather than a field on the handshake because the
    list changes while the agent is running, and a handshake happens once.

    It carries the DECLARATION, not the reading: the values themselves already
    reach the cloud through the state relay every two seconds, which is fifteen
    times faster than the metrics on the health card. Nothing here is telemetry.
    """
    return {"monitors": monitors if monitors is not None else []}


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
    ACTIVE_ALERTS: build_active_alerts_payload,
    MONITORS: build_monitors_payload,
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


# --- Readers (the downstream payload contract) ---

def _fill_defaults(row: wire_tables.Message, fields: dict[str, Any]) -> dict[str, Any]:
    out = dict(fields)
    for f in row.fields:
        if f.name not in out and f.default is not ABSENT:
            out[f.name] = copy.deepcopy(f.default)
    return out


def read_payload(msg_type: str, payload: Any) -> dict[str, Any]:
    """A downstream payload as the contract reads it.

    Every field the cloud sent is kept as sent, null included, and every field
    it left out takes the table's default; a field with no default stays out.
    Raises ProtocolError for a payload that is not an object.
    """
    if not isinstance(payload, dict):
        raise ProtocolError(f"{msg_type} payload is not an object")
    row = MESSAGE_ROWS.get(msg_type)
    return _fill_defaults(row, payload) if row else dict(payload)


def read_message(msg: dict[str, Any]) -> dict[str, Any]:
    """The payload of a downstream message, read through its own type's row."""
    return read_payload(msg.get("type", ""), extract_payload(msg))


def read_as(msg_type: str, msg: dict[str, Any]) -> dict[str, Any]:
    """The payload of ``msg``, read through ``msg_type``'s row: for a handler
    that only ever receives that one type."""
    return read_payload(msg_type, extract_payload(msg))


class RequestRefused(ProtocolError):
    """A downstream request the contract's reader will not hand to a handler.

    ``request_id`` is the id to answer the refusal with, ``""`` when the
    request carries none: the cloud matches an answer to its request only by
    that id, so a request without one is logged and not run.
    """

    def __init__(self, reason: str, request_id: str = ""):
        super().__init__(reason)
        self.reason = reason
        self.request_id = request_id


# The Python value each JSON type name reads as. A boolean is read apart: in
# Python it is also an int, and on the wire it is never a number.
_PYTHON_TYPES: dict[str, type | tuple[type, ...]] = {
    "string": str, "integer": int, "number": (int, float), "boolean": bool,
    "object": dict, "array": list, "null": type(None),
}

_JSON_TYPE_WORDS = {
    "string": "a string", "integer": "an integer", "number": "a number",
    "boolean": "a boolean", "object": "an object", "array": "an array", "null": "null",
}


def _type_parts(type_expr: str) -> list[tuple[str, str | None]]:
    """``"string|null"`` as ``[("string", None), ("null", None)]``, and
    ``"array<string>"`` as ``[("array", "string")]``."""
    parts = []
    for part in type_expr.split("|"):
        if part.startswith("array<") and part.endswith(">"):
            parts.append(("array", part[len("array<"):-1]))
        else:
            parts.append((part, None))
    return parts


def _is_json_type(value: Any, kind: str) -> bool:
    if isinstance(value, bool):
        return kind == "boolean"
    return isinstance(value, _PYTHON_TYPES[kind])


def _is_part(value: Any, kind: str, item_kind: str | None) -> bool:
    """Whether ``value`` is ``kind``, and each item ``item_kind`` when it names one."""
    if not _is_json_type(value, kind):
        return False
    return item_kind is None or all(_is_json_type(item, item_kind) for item in value)


def _is_of_type(value: Any, type_expr: str) -> bool:
    """Whether ``value`` is one of the JSON types a field's ``type`` names."""
    if type_expr == "any":
        return True
    return any(_is_part(value, kind, item_kind) for kind, item_kind in _type_parts(type_expr))


def _type_words(type_expr: str) -> str:
    words = []
    for kind, item_kind in _type_parts(type_expr):
        if item_kind is None:
            words.append(_JSON_TYPE_WORDS[kind])
        else:
            words.append(f"an array of {_JSON_TYPE_WORDS[item_kind].split(' ', 1)[-1]}s")
    return " or ".join(words)


def request_id_of(msg: dict[str, Any]) -> str:
    """The id a downstream request is answered with: ``""`` when its payload
    is not an object or its ``request_id`` is not a string."""
    payload = extract_payload(msg)
    request_id = payload.get("request_id") if isinstance(payload, dict) else None
    return request_id if isinstance(request_id, str) else ""


def read_request(msg: dict[str, Any]) -> dict[str, Any]:
    """A downstream request's payload, checked against its row before any
    handler runs, then read as ``read_payload`` reads it.

    Raises ``RequestRefused`` for a payload that is not an object, a
    ``request_id`` that is missing or not a string, and a field sent as a
    JSON type its row does not declare, naming the field. A null is kept as
    sent, for the handler to read as it always has, and a field the cloud left
    out takes the table's default; a field the row does not know is read past,
    because a newer cloud may send one.
    """
    msg_type = msg.get("type", "")
    payload = extract_payload(msg)
    if not isinstance(payload, dict):
        raise RequestRefused(f"{msg_type} payload is not an object")
    request_id = request_id_of(msg)
    if not request_id:
        raise RequestRefused(f"{msg_type} carries no request_id")
    row = MESSAGE_ROWS[msg_type]
    problem = _wrong_field(row, payload)
    if problem:
        raise RequestRefused(problem, request_id)
    return _fill_defaults(row, payload)


def _wrong_field(row: wire_tables.Message, payload: dict[str, Any]) -> str | None:
    """The refusal for the first field sent as a type its row does not
    declare; None when every sent field is of its type, or null."""
    for f in row.fields:
        value = payload.get(f.name)
        if value is not None and not _is_of_type(value, f.type):
            return (f"invalid_field: {f.name} must be {_type_words(f.type)}, "
                    f"not {json_kind(value)}.")
    return None


def read_tunnel_data(msg: dict[str, Any], msg_type: str | None = None) -> dict[str, Any]:
    """A message from a tunnel's data socket, its absent fields defaulted:
    read through ``msg_type``'s row, or the message's own type's."""
    row = MESSAGE_ROWS.get(msg_type or msg.get("type", ""))
    return _fill_defaults(row, msg) if row and row.phase == "tunnel_data" else dict(msg)


# --- Builders: a tunnel's data socket (upstream) ---

def build_http_response(
    request_id: str, status: int, headers: dict[str, Any], body_b64: str
) -> dict[str, Any]:
    """A whole buffered response."""
    return {"type": HTTP_RESPONSE, "id": request_id, "status": status,
            "headers": headers, "body": body_b64}


def build_http_response_start(
    request_id: str, status: int, headers: dict[str, Any]
) -> dict[str, Any]:
    """A streamed response's status and headers."""
    return {"type": HTTP_RESPONSE_START, "id": request_id, "status": status,
            "headers": headers}


def build_http_body(request_id: str, data_b64: str) -> dict[str, Any]:
    """One piece of a streamed response's body."""
    return {"type": HTTP_BODY, "id": request_id, "data": data_b64}


def build_http_body_end(request_id: str, error: str | None = None) -> dict[str, Any]:
    """The end of a streamed response; ``error`` when it failed partway."""
    msg: dict[str, Any] = {"type": HTTP_BODY_END, "id": request_id}
    if error is not None:
        msg["error"] = error
    return msg


def build_ws_frame(ws_id: str, data_b64: str, binary: bool) -> dict[str, Any]:
    """One WebSocket message."""
    return {"type": WS_FRAME, "id": ws_id, "data": data_b64, "binary": binary}


# A close frame's reason is at most this many bytes of UTF-8.
_MAX_CLOSE_REASON_BYTES = _limit("ws_close_reason_max_bytes")


def close_reason(reason: str) -> str:
    """``reason`` cut to what a close frame carries, never mid-character."""
    return reason.encode("utf-8")[:_MAX_CLOSE_REASON_BYTES].decode("utf-8", errors="ignore")


def build_ws_close(ws_id: str, code: int, reason: str = "") -> dict[str, Any]:
    """A WebSocket closed at this end, with the code to pass on."""
    msg: dict[str, Any] = {"type": WS_CLOSE, "id": ws_id, "code": code}
    if reason:
        msg["reason"] = close_reason(reason)
    return msg
