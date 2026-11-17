"""
OpenAVC Cloud: the wire contract's tables.

Every message the agent and the cloud exchange, every field it carries, and
every vocabulary those fields draw from, written once. ``protocol.py`` is the
only module that imports this one: it derives its message sets, priorities and
gates from these rows and reads each downstream payload through them, and
every other agent file asks ``protocol.py``. ``contract_gen.py`` renders the
same rows into ``wire_contract.json``, the file the cloud copies and tests its
own builders and readers against.

A row's ``since`` is the first final OpenAVC release that carried it. An entry
already present in ``BASELINE``, the oldest release the cloud still serves,
keeps the default and reads "at or before". Whoever adds a message, field or
vocabulary entry writes the release it will ship in, when it lands;
``python -m openavc.cloud.contract_gen --cut <version>`` refuses a cut while a
``since`` names a release that never shipped.

A field's ``default`` is what this agent's reader takes when the field is
absent, which is not the same as optional: a field the sender must always
send can still have a fallback here. A field with no default stays absent.

A field with no ``since`` of its own arrived with the message (or object)
that holds it.

Downstream rows newer than ``BASELINE`` say how an older agent copes with them:

* ``announced_by``: the cloud sends the message only to a system whose hello
  announced that capability;
* ``follows``: the cloud sends it only in answer to, or after, that system's
  own upstream message, which no older release sends;
* ``ignorable``: an older agent that drops the field, or the whole message,
  still does the right thing, for the reason given in ``why``.

Pure data and stdlib, so it imports anywhere ``protocol.py`` does.
"""

from __future__ import annotations

from dataclasses import dataclass, field as _field, replace
from typing import Any

# Where the contract's history starts: the cloud's first floor, the oldest
# release still paired when the contract was written. An entry this release
# already carried keeps it as its since and reads "at or before". It stays put
# when the cloud raises its own floor later, so a raise never drops a mark an
# older agent still needs.
BASELINE = "0.5.14"

# The release that first carries this contract. A downstream entry newer than
# it must be announced by a capability; ignorable and follows cover only what
# shipped before the contract existed.
FIRST_CONTRACT_RELEASE = "0.41.0"


class _Absent:
    """No default: the reader leaves the field out when the sender did."""

    def __repr__(self) -> str:
        return "ABSENT"


ABSENT: Any = _Absent()


@dataclass(frozen=True)
class Field:
    """One field of a message, or one property of a described object.

    ``type`` is a JSON type name ("string", "integer", "number", "boolean",
    "object", "array", "null"), several joined with ``|``, ``array<T>`` for an
    array whose items are all T, or ``any``. An object with ``fields`` holds
    exactly those properties unless ``open`` says it may hold others.
    """

    name: str
    type: str
    example: Any = ABSENT
    doc: str = ""
    required: bool = True
    default: Any = ABSENT
    since: str | None = None
    vocabulary: str | None = None
    ignorable: bool = False
    why: str = ""
    fields: tuple["Field", ...] = ()
    open: bool = False
    items: tuple["Field", ...] = ()


@dataclass(frozen=True)
class Message:
    """One message type.

    ``direction`` is ``up`` (agent to cloud), ``down`` (cloud to agent) or
    ``both`` (the tunnel's frames and closes, which each side sends). An
    upstream payload's schema is open: an older agent may still send a field
    since removed, which the cloud ignores.
    ``phase`` is ``handshake`` (no seq, session or sig), ``session`` (signed
    and sequenced) or ``tunnel_data`` (a tunnel's own data socket, whose fields
    sit beside ``type`` with no payload around them).

    ``requires`` is the capability this agent must have been granted before it
    acts on a downstream message; refused otherwise. ``answer`` is the upstream
    result that answers a downstream request, which is also how a refusal
    answers it. ``priority`` orders an upstream buffer's evictions (lower goes
    first); none means the sequencer's default.
    """

    type: str
    direction: str
    phase: str
    doc: str
    fields: tuple[Field, ...] = ()
    since: str = BASELINE
    priority: int | None = None
    requires: str | None = None
    announced_by: str | None = None
    follows: str | None = None
    answer: str | None = None
    open: bool = False
    ignorable: bool = False
    why: str = ""


@dataclass(frozen=True)
class Entry:
    """One value of a vocabulary."""

    value: Any
    doc: str
    since: str = BASELINE
    flags: frozenset[str] = _field(default_factory=frozenset)


# --- Shared field shapes ---

def _request_id(example: str = "8f7c2a90-3d1e-4b6a-9c55-1f0e2d3c4b5a") -> Field:
    return Field("request_id", "string", example, "Correlates the request with its answer.",
                 default="")


_USER_ID = Field("user_id", "string", "5b1d0c7e-2a44-4f1e-8d0a-6c9e1b2a3f40",
                 "The cloud user who asked, for this instance's own log.", default="")
_USER_NAME = Field("user_name", "string", "Pat Example",
                   "That user's display name, for this instance's own log.", default="")

_RESULT_FIELDS = (
    _request_id(),
    Field("success", "boolean", True, "Whether the request did what it asked."),
    Field("result", "any", "OK", "What it produced; its shape is the request's."),
    Field("error", "string|null", None, "Why it failed; null on success."),
)

_ALERT_RULE = (
    Field("id", "string", "3c0e8d6a-77b2-4f0a-b0e1-0d9a5c2f1e44", "The rule's id.", default=None),
    Field("name", "string", "High CPU usage", "The rule's name, shown with what it raises.",
          default=None),
    Field("rule_type", "string", "threshold", "Which kind of rule it is.",
          vocabulary="alert_rule_types", default=""),
    Field("condition", "object", {"key": "system.cpu_percent", "operator": ">", "value": 95,
                                  "resolve_value": 90},
          "What the rule watches and when it fires. Its operators and values are the monitor "
          "rule's.", default={}, open=True, fields=(
              Field("key", "string", "system.cpu_percent",
                    "The state key, or a pattern of them, the rule reads.", required=False),
              Field("operator", "string", ">", "How a reading is compared with value.",
                    required=False),
              Field("value", "any", 95, "What a reading is compared with.", required=False),
              Field("resolve_value", "any", 90,
                    "A threshold rule's reading that clears it; the firing value when absent.",
                    required=False),
              Field("duration_seconds", "number", 0,
                    "How long a reading must stay wrong before the rule fires.", required=False),
              Field("threshold_seconds", "number", 120,
                    "An absence rule: how long without a change before it fires.",
                    required=False),
              Field("key_prefix", "string", "device.",
                    "An absence rule: which keys it watches.", required=False),
          )),
    Field("severity", "string", "warning", "How serious what it raises is.",
          vocabulary="alert_severities", default="warning"),
    Field("category", "string", "system", "What kind of fault it raises.",
          vocabulary="alert_categories", default="device"),
    Field("enabled", "boolean", True, "Whether the rule is asked at all.", default=True),
    Field("is_builtin", "boolean", True, "Whether the cloud wrote the rule itself.",
          required=False),
)


# --- Messages ---

MESSAGES: tuple[Message, ...] = (
    # Handshake, agent to cloud
    Message("hello", "up", "handshake", "Opens a connection: who this is and what it can do.", (
        Field("protocol_version", "integer", 1, "The wire version this agent prefers."),
        Field("supported_versions", "array<integer>", [1], "Every wire version it speaks.", since="0.24.0"),
        Field("system_id", "string", "6f1c9e2a-4b7d-4e0a-9a33-2c8d5e7f1b90",
              "This system's id, from pairing."),
        Field("version", "string", "0.40.0", "This instance's OpenAVC release."),
        Field("capabilities", "array<string>", ["monitoring", "remote_access", "tunnel"],
              "What this instance can do.", vocabulary="capabilities"),
        Field("os", "string", "Linux 6.8.0", "The host's operating system."),
        Field("hardware", "string", "x86_64", "The host's processor architecture."),
        Field("deployment_mode", "string", "linux_package", "How OpenAVC was installed.",
              vocabulary="deployment_modes"),
    )),
    Message("authenticate", "up", "handshake", "Answers the challenge with proof of the system key.", (
        Field("system_id", "string", "6f1c9e2a-4b7d-4e0a-9a33-2c8d5e7f1b90", "This system's id."),
        Field("timestamp", "string", "2026-11-17T15:46:00.000Z", "When the proof was made."),
        Field("proof", "string", "9d3b1f0e7c2a4b6d8e0f1a3c5e7b9d2f4a6c8e0b1d3f5a7c9e2b4d6f8a0c1e3b",
              "The proof, in hex."),
    )),
    Message("resume", "up", "handshake",
            "Sent after a reconnect's handshake: what this agent still holds unacknowledged. "
            "Diagnostic only; the cloud always asks for the whole buffer again.", (
                Field("last_ack_seq", "integer", 41, "The last sequence the cloud acknowledged."),
                Field("buffered_count", "integer", 3, "How many messages wait for replay."),
                Field("disconnected_at", "string", "2026-11-17T15:40:00.000Z",
                      "When the connection dropped."),
            )),
    # Handshake, cloud to agent
    Message("challenge", "down", "handshake", "The nonce the agent signs to prove its key.", (
        Field("nonce", "string", "4e2a9c7b1d0f3e5a", "The nonce, in hex.", default=None),
    )),
    Message("session_start", "down", "handshake", "Opens a session: its keys, grants and config.", (
        Field("protocol_version", "integer", 1, "The wire version both sides settled on.",
              default=None, since="0.23.0",
              ignorable=True, why="An older agent speaks protocol 1, the only version there is."),
        Field("session_id", "string", "b2e4c6a8-0d1f-4e3a-8c5b-7d9f1a3e5c70", "The session's id.",
              default=None),
        Field("session_token", "string", "st_7c1e9a3b5d", "Rides on every signed message.",
              default=None),
        Field("signing_key_salt", "string", "a1b2c3d4e5f60718293a4b5c6d7e8f90",
              "Salt for this session's signing key, in hex.", default=None),
        Field("session_expires", "string", "2026-11-18T15:46:00.000Z",
              "When the session must rotate.", default=""),
        Field("enabled_capabilities", "array<string>", ["monitoring", "remote_access", "tunnel"],
              "The capabilities granted to this session.", vocabulary="capabilities", default=[]),
        Field("config", "object", {"heartbeat_interval": 30, "state_batch_interval": 2},
              "Server-driven config, as config_update carries it.", default={}, open=True),
        Field("upgrade_required", "object|null",
              {"min_version": "0.30.0", "message": "Cloud features need OpenAVC v0.30.0 or later."},
              "Present when this release is below what the cloud supports.", required=False,
              default=None, fields=(
                  Field("min_version", "string", "0.30.0", "The release the cloud needs."),
                  Field("message", "string", "Cloud features need OpenAVC v0.30.0 or later.",
                        "What to show."),
                  Field("features_disabled", "array<string>", [],
                        "Capabilities switched off until the update.", required=False,
                        vocabulary="capabilities", since="0.11.0",
              ignorable=True,
              why="The cloud gates those features itself too; an older agent keeps its own side of them on."),
              )),
    )),
    Message("auth_failed", "down", "handshake", "Refuses the handshake, naming why.", (
        Field("reason", "string", "unknown_system", "Why.", vocabulary="refusal_reasons",
              default="unknown"),
        Field("message", "string", "System not found", "A sentence for the log.",
              default="Authentication failed"),
    )),
    Message("version_mismatch", "down", "handshake",
            "Refuses the handshake: the two sides share no wire version.", (
                Field("supported_versions", "array<integer>", [1], "What the cloud speaks.",
                      default=[]),
                Field("message", "string", "Protocol version not supported",
                      "A sentence for the log.", default="Protocol version not supported"),
            )),
    Message("resume_from", "down", "handshake", "Where replay starts after a resume.", (
        Field("replay_from_seq", "integer", 1, "The first sequence to send again.", default=0),
    )),

    # Session, agent to cloud
    Message("heartbeat", "up", "session", "The host's health, at the configured interval.", (
        Field("uptime_seconds", "integer", 86400, "Seconds since OpenAVC started."),
        Field("cpu_percent", "number", 12.5, "Processor use."),
        Field("memory_percent", "number", 41.0, "Memory use."),
        Field("disk_percent", "number", 63.2, "Disk use."),
        Field("device_count", "integer", 8, "Devices in the project."),
        Field("devices_connected", "integer", 7, "Devices connected now."),
        Field("devices_error", "integer", 1, "Devices in a fault now."),
        Field("active_ws_clients", "integer", 2, "Panels and IDEs connected now."),
        Field("notify_only", "boolean", False,
              "The local tell-me-don't-install switch; absent when nobody read it.",
              required=False, default=None, since="0.33.0"),
        Field("temperature_celsius", "number", 48.5,
              "The host's temperature; absent when it has no sensor.", required=False,
              default=None),
    ), priority=2),
    Message("state_batch", "up", "session", "State changes, batched.", (
        Field("changes", "array", [{"key": "device.projector.power", "value": "on",
                                    "ts": "2026-11-17T15:46:00.000Z"}],
              "Each change: its key, its flat value and when it changed.", items=(
                  Field("key", "string", "device.projector.power", "The state key."),
                  Field("value", "string|number|boolean|null", "on", "Its value."),
                  Field("ts", "string", "2026-11-17T15:46:00.000Z", "When it changed."),
                  Field("deleted", "boolean", doc="The key is gone; the cloud removes it.",
                        required=False, default=False),
              )),
        Field("snapshot", "boolean", True,
              "Set on a session's first batch: keys it does not name are gone.",
              required=False, default=False),
    ), priority=0),
    Message("alert", "up", "session", "An alert fired.", (
        Field("alert_id", "string", "rule:3c0e8d6a:system.cpu_percent", "This firing's id."),
        Field("rule_id", "string", "3c0e8d6a-77b2-4f0a-b0e1-0d9a5c2f1e44", "The rule that fired.", since="0.11.0"),
        Field("severity", "string", "warning", "How serious.", vocabulary="alert_severities"),
        Field("category", "string", "system", "What kind of fault.",
              vocabulary="alert_categories"),
        Field("device_id", "string|null", None, "The device it is about, if one."),
        Field("message", "string", "CPU usage is 97%", "What to show."),
        Field("detail", "object", {"key": "system.cpu_percent", "value": 97, "threshold": 95},
              "What the rule saw."),
        Field("fired_at", "string", "2026-11-17T15:46:00.000Z",
              "When this instance saw it fire.", required=False, default=None, since="0.33.0"),
    ), priority=4),
    Message("alert_resolved", "up", "session", "An alert ended.", (
        Field("alert_id", "string", "rule:3c0e8d6a:system.cpu_percent", "The firing that ended."),
        Field("resolved_at", "string", "2026-11-17T15:52:00.000Z",
              "When this instance saw it end.", required=False, default=None, since="0.33.0"),
        Field("reason", "string", "recovered", "Why it ended.", required=False,
              vocabulary="alert_resolved_reasons", default="recovered", since="0.33.0"),
    ), priority=3),
    Message("active_alerts", "up", "session",
            "Everything firing here, once per connection; the cloud resolves what it does "
            "not name. A system that never sends it has said nothing.", (
                Field("alert_ids", "array<string>", ["rule:3c0e8d6a:system.cpu_percent"],
                      "Every firing open now; an empty list means none."),
            ), priority=4, since="0.28.0"),
    Message("monitors", "up", "session",
            "Which readings this room says matter and what normal is for each, on connect and "
            "on every project apply.", (
                Field("monitors", "array<object>",
                      [{"key": "device.projector.lamp_hours", "label": "Lamp hours"}],
                      "The declarations; their shape is the monitor rule's."),
            ), since="0.28.0"),
    Message("command_result", "up", "session",
            "Answers command, config_push, restart, software_update and "
            "panel_software_update.", _RESULT_FIELDS, priority=5),
    Message("diagnostic_result", "up", "session", "Answers a diagnostic.", _RESULT_FIELDS,
            priority=5),
    Message("ai_tool_result", "up", "session", "Answers an ai_tool_call.", _RESULT_FIELDS,
            priority=5),
    Message("project_data", "up", "session", "Answers get_project.", (
        _request_id(),
        Field("success", "boolean", True, "Whether the project was read."),
        Field("project_json", "object", {"openavc_version": "0.17.0"},
              "The project; present on success only.", required=False),
        Field("error", "string", doc="Why not; present on failure only.", required=False),
    ), priority=5),
    Message("device_commands_data", "up", "session", "Answers get_device_commands.", (
        _request_id(),
        Field("success", "boolean", True, "Whether the devices were read."),
        Field("devices", "array", [{"id": "projector", "name": "Projector", "driver": "acme_widget",
                                    "connected": True, "commands": {"power_on": {"params": {}}}}],
              "Every device and the commands it takes; present on success only.",
              required=False, items=(
                  Field("id", "string", "projector", "The device's id."),
                  Field("name", "string", "Projector", "Its name."),
                  Field("driver", "string", "acme_widget", "Its driver's id."),
                  Field("connected", "boolean", True, "Whether it is connected now."),
                  Field("commands", "object", {"power_on": {"params": {}}},
                        "Each command by name, with its params: the driver's object, keyed by "
                        "parameter name with its type and label. A command that takes or "
                        "returns a file is left out.", open=True),
              )),
        Field("error", "string", doc="Why not; present on failure only.", required=False),
    ), priority=5),
    Message("pong", "up", "session", "Answers a ping with its nonce.", (
        Field("nonce", "string", "4e2a9c7b", "The ping's nonce."),
    ), priority=6),
    Message("tunnel_ready", "up", "session", "The tunnel's data socket is up.", (
        Field("tunnel_id", "string", "tun_5d2c8e", "The tunnel."),
    ), priority=5),
    Message("tunnel_failed", "up", "session", "The tunnel could not open, or was refused.", (
        Field("tunnel_id", "string", "tun_5d2c8e", "The tunnel."),
        Field("reason", "string", "capability_disabled: the 'tunnel' capability is not enabled",
              "Why."),
    ), priority=5),
    Message("gap_report", "up", "session", "Downstream sequences that never arrived.", (
        Field("missing_from", "integer", 12, "The first missing sequence."),
        Field("missing_to", "integer", 14, "The last missing sequence."),
    ), priority=7),
    Message("cert_request", "up", "session",
            "Asks for a trusted certificate: empty to enroll, with a CSR to issue.", (
                Field("csr_pem", "string",
                      "-----BEGIN CERTIFICATE REQUEST-----\n...\n-----END CERTIFICATE REQUEST-----\n",
                      "The CSR; its names are the certificate_names.", required=False),
            ), priority=5, since="0.23.0"),
    Message("cert_status", "up", "session", "Where the trusted certificate stands here.", (
        Field("state", "string", "installed", "Its state.", vocabulary="cert_states"),
    ), priority=5, since="0.23.0"),

    # Session, cloud to agent
    Message("ack", "down", "session", "The last upstream sequence the cloud holds.", (
        Field("last_seq", "integer", 41, "That sequence.", default=0),
    ), priority=9),
    Message("session_rotate", "down", "session",
            "Rotates the session's token and key at a named sequence.", (
                Field("new_session_token", "string", "st_9a3c5e7b1d", "The next token.",
                      default=None),
                Field("new_signing_key_salt", "string", "0f1e2d3c4b5a69788796a5b4c3d2e1f0",
                      "Salt for the next signing key, in hex.", default=None),
                Field("new_session_expires", "string", "2026-11-19T15:46:00.000Z",
                      "When the next session must rotate.", default=""),
                Field("switch_at_seq", "integer", 120,
                      "The first sequence signed with the next key.", default=None),
            ), priority=10),
    Message("session_invalid", "down", "session", "Ends the session; the agent reconnects.", (
        Field("reason", "string", "expired", "Why.", default="unknown"),
    ), priority=9),
    Message("config_update", "down", "session",
            "Server-driven config, merged into what the session started with: only the keys "
            "it carries change.", (
                Field("heartbeat_interval", "integer", 30, "Seconds between heartbeats.",
                      required=False),
                Field("state_batch_interval", "number", 2, "Seconds between state batches.",
                      required=False),
                Field("state_batch_max_size", "integer", 500, "Changes per state batch.",
                      required=False),
                Field("ack_interval", "number", 5, "Seconds between the cloud's acks.",
                      required=False),
                Field("max_messages_per_minute", "integer", 300, "The cloud's ceiling.",
                      required=False),
                Field("max_heartbeats_per_minute", "integer", 10, "The cloud's ceiling.",
                      required=False),
                Field("max_state_batches_per_minute", "integer", 60, "The cloud's ceiling.",
                      required=False),
                Field("features", "object", {"state_forwarding": True, "alerts": True},
                      "Switches the state relay and the alert monitor.", required=False, fields=(
                          Field("state_forwarding", "boolean", True, "The state relay.",
                                required=False),
                          Field("alerts", "boolean", True, "The alert monitor.", required=False, since="0.24.0",
              ignorable=True,
              why="An older agent switches its alert monitor by its own alerts_enabled, which is on unless set."),
                      )),
                Field("update_policy", "object",
                      {"policy": "manual", "maintenance_window_start": None,
                       "maintenance_window_end": None, "maintenance_window_tz": None},
                      "How this organization's systems take an update.", required=False,
                      fields=(
                          Field("policy", "string", "manual", "The policy."),
                          Field("maintenance_window_start", "string|null", None,
                                "When the window opens."),
                          Field("maintenance_window_end", "string|null", None,
                                "When it closes."),
                          Field("maintenance_window_tz", "string|null", None, "Its time zone."),
                      ), since="0.15.0",
              ignorable=True,
              why="An older agent applies the policy from session_start's config at its next connection."),
            ), priority=7, open=True),
    Message("capabilities_update", "down", "session", "Changes the session's grants.", (
        Field("enabled_capabilities", "array<string>", ["monitoring", "remote_access"],
              "Every capability granted now.", vocabulary="capabilities", default=None),
    ), priority=7),
    Message("throttle", "down", "session", "Backs a message type off.", (
        Field("limit", "string", "state_batch", "The type, or global.", default=""),
        Field("retry_after_seconds", "number", 30, "How long to wait.", default=30),
    ), priority=3),
    Message("error", "down", "session", "The cloud could not take an upstream message.", (
        Field("code", "string", "message_too_large", "What went wrong.", default="unknown"),
        Field("message", "string", "Message exceeds 1048576 bytes", "A sentence for the log.",
              default=""),
        Field("ref_type", "string", "state_batch", "The type it was about.", required=False,
              default=""),
    ), priority=3),
    Message("command", "down", "session", "Sends a command to a device.", (
        Field("device_id", "string", "projector", "The device.", default=""),
        Field("command", "string", "power_on", "The command.", default=""),
        Field("params", "object", {}, "Its parameters, by name.", default={}),
        _request_id(), _USER_ID, _USER_NAME,
    ), priority=7, requires="remote_access", answer="command_result"),
    Message("config_push", "down", "session", "Replaces the project.", (
        _request_id(),
        Field("mode", "string", "full_replace", "How.", vocabulary="config_push_modes",
              default="full_replace"),
        Field("project_json", "object", {"openavc_version": "0.17.0"},
              "The project; a push without one reloads the project on disk.", default=None),
        _USER_ID, _USER_NAME,
    ), priority=7, requires="remote_access", answer="command_result"),
    Message("restart", "down", "session", "Restarts OpenAVC.", (
        _request_id(),
        Field("mode", "string", "graceful", "How.", default="graceful"),
        _USER_ID, _USER_NAME,
    ), priority=8, requires="remote_access", answer="command_result"),
    Message("diagnostic", "down", "session", "Runs a network check from this system.", (
        _request_id(),
        Field("action", "string", "ping", "Which check.", vocabulary="diagnostic_actions",
              default=""),
        Field("target", "string", "192.168.1.50", "What it checks.", default=""),
        Field("params", "object", {"count": 4}, "The check's own settings.", default=None, since="0.11.0",
              ignorable=True,
              why="An older agent runs the check with its own settings."),
        _USER_ID, _USER_NAME,
    ), priority=4, requires="diagnostics", answer="diagnostic_result"),
    Message("software_update", "down", "session", "Updates OpenAVC to a release.", (
        _request_id(),
        Field("target_version", "string", "0.40.0", "The release.", default=""),
        Field("update_url", "string", "https://github.com/open-avc/openavc/releases/download/"
              "v0.40.0/openavc-0.40.0.tar.gz", "Where its file is.", default=""),
        Field("checksum_sha256", "string", "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
              "The file's SHA-256.", required=False, default=None),
        Field("auto_restart", "boolean", True, "Whether to restart onto it at once.",
              default=True),
        _USER_ID, _USER_NAME,
    ), priority=4, requires="fleet_update", answer="command_result"),
    Message("panel_software_update", "down", "session",
            "Stages a newer copy of the panel software on appliance hardware. It does not "
            "touch OpenAVC itself, restart anything or take a backup.", (
                _request_id(),
                Field("target_version", "string", "0.3.28", "The panel software's version.",
                      default=""),
                Field("target_version_code", "integer", 328, "Its version code.", default=None),
                Field("update_url", "string", "https://example.invalid/panel-0.3.28.apk",
                      "Where its file is.", default=None),
                Field("checksum_sha256", "string",
                      "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
                      "The file's SHA-256.", default=None),
                Field("signer_sha256", "string",
                      "2c26b46b68ffc68ff99b453c1d30413413422d706483bfa0f98a5e886266e7ae",
                      "The SHA-256 of the certificate it must be signed with.", required=False,
                      default=None),
                Field("install", "string", "at_panel", "When it installs.",
                      vocabulary="panel_install_modes", default=None),
                _USER_ID, _USER_NAME,
            ), priority=4, requires="panel_software_update",
            announced_by="panel_software_update", answer="command_result", since=FIRST_CONTRACT_RELEASE),
    Message("tunnel_open", "down", "session", "Opens a Remote Access tunnel.", (
        Field("tunnel_id", "string", "tun_5d2c8e", "The tunnel.", default=""),
        Field("tunnel_token", "string", "tt_3e7a1c", "Authorizes its data socket.", default=""),
        Field("tunnel_data_url", "string", "wss://cloud.example.invalid/agent/v1/tunnel/tun_5d2c8e",
              "Where its data socket connects.", default=""),
        Field("authorized_session", "string|null", None,
              "Who authorized it to act as a signed-in Programmer: owner or support. Null for "
              "an ordinary tunnel, which asks for this instance's password.",
              vocabulary="authorized_sessions", default=None, since="0.27.0",
              ignorable=True, why="An older agent treats the tunnel as an ordinary one and asks for this instance's password."),
        Field("stream_responses", "boolean", True,
              "The cloud can take a response in pieces.", required=False, default=None, since="0.28.0",
              ignorable=True, why="An older agent sends every response whole, which the cloud reads as it always has."),
        Field("target_port", "integer", doc=
              "No longer sent: this instance proxies to its own port, and honours a named port "
              "only when it serves it.", required=False, default=None),
    ), priority=5, requires="tunnel", answer="tunnel_ready"),
    Message("tunnel_close", "down", "session", "Ends a tunnel.", (
        Field("tunnel_id", "string", "tun_5d2c8e", "The tunnel.", default=""),
    ), priority=5, requires="tunnel"),
    Message("ping", "down", "session", "Liveness; answered with a pong.", (
        Field("nonce", "string", "4e2a9c7b", "Echoed in the pong.", default=""),
    ), priority=6),
    Message("alert_rules_update", "down", "session",
            "The alert rules this instance evaluates, whole.", (
                Field("rules", "array", [{"id": "3c0e8d6a-77b2-4f0a-b0e1-0d9a5c2f1e44",
                                          "name": "High CPU usage", "rule_type": "threshold",
                                          "condition": {"key": "system.cpu_percent",
                                                        "operator": ">", "value": 95,
                                                        "resolve_value": 90},
                                          "severity": "warning", "category": "system",
                                          "enabled": True, "is_builtin": True}],
                      "Every rule.", default=[], items=_ALERT_RULE),
            ), priority=4),
    Message("ai_tool_call", "down", "session", "Runs one AI assistant tool here.", (
        _request_id(),
        Field("tool_name", "string", "get_project_summary", "The tool.", default=""),
        Field("tool_input", "object", {}, "Its input.", default={}),
    ), priority=5, answer="ai_tool_result"),
    Message("get_project", "down", "session", "Asks for the project.", (
        _request_id(),
    ), priority=5, answer="project_data"),
    Message("get_device_commands", "down", "session",
            "Asks for every device and the commands it takes.", (
                _request_id(),
            ), answer="device_commands_data"),
    Message("cert_result", "down", "session", "Answers a cert_request.", (
        Field("status", "string", "issued", "What happened.", vocabulary="cert_result_statuses",
              default=""),
        Field("label", "string", doc="Enrollment: this system's label.", required=False,
              default=""),
        Field("zone", "string", doc="Enrollment: the zone.", required=False, default=""),
        Field("certificate_chain", "string",
              "-----BEGIN CERTIFICATE-----\n...\n-----END CERTIFICATE-----\n",
              "Issued: the chain.", required=False, default=""),
        Field("error", "string", doc="Error: what failed.", required=False, default="unknown"),
        Field("detail", "string", doc="Error: a sentence.", required=False, default=""),
        Field("expires_at", "string", doc="When it expires.", required=False,
              default="unknown"),
    ), priority=5, follows="cert_request", since="0.23.0"),
    Message("cert_renew_due", "down", "session",
            "The certificate is due for renewal; the agent requests a new one.", (
                Field("expires_at", "string", "2026-12-01T00:00:00Z",
                      "When the current one expires.", default="unknown"),
            ), priority=4, requires="trusted_certs", follows="cert_request", since="0.23.0"),
    Message("help_acknowledged", "down", "session",
            "Somebody in the cloud picked the room's help request up.", (
                Field("help_id", "string", "help_7a2c", "The request.", default=None),
                Field("acknowledged_by", "string", "Pat Example",
                      "Their display name, for the room's panel.", default=None),
                Field("acknowledged_at", "string", "2026-11-17T15:48:00.000Z", "When.",
                      default=None),
            ), priority=6, follows="alert", since="0.26.0"),

    # A tunnel's data socket. Fields sit beside type; there is no envelope.
    Message("http_request", "down", "tunnel_data", "A browser's HTTP request, to proxy locally.", (
        Field("id", "string", "req_1", "Correlates the response.", default=""),
        Field("method", "string", "GET", "The HTTP method.", default="GET"),
        Field("path", "string", "/programmer/", "The path and query.", default="/"),
        Field("headers", "object", {"accept": "text/html"}, "The request's headers.",
              default={}),
        Field("body", "string", "", "The body, base64.", default=""),
    )),
    Message("http_cancel", "down", "tunnel_data",
            "Nobody reads that response any more; stop sending it.", (
                Field("id", "string", "req_1", "The request.", default=""),
            ), since="0.28.0", ignorable=True,
            why="An older agent logs a type it does not know and finishes sending the response, which the cloud discards."),
    Message("http_response", "up", "tunnel_data", "A whole buffered response.", (
        Field("id", "string", "req_1", "The request it answers."),
        Field("status", "integer", 200, "The HTTP status."),
        Field("headers", "object", {"content-type": "text/html"}, "The response's headers."),
        Field("body", "string", "PGh0bWw+PC9odG1sPg==", "The body, base64."),
    )),
    Message("http_response_start", "up", "tunnel_data",
            "A streamed response's status and headers; its body follows in pieces. Sent only "
            "on a tunnel whose tunnel_open said stream_responses.", (
                Field("id", "string", "req_1", "The request it answers."),
                Field("status", "integer", 200, "The HTTP status."),
                Field("headers", "object", {"content-type": "text/event-stream"},
                      "The response's headers, without its length and encoding."),
            ), since="0.28.0"),
    Message("http_body", "up", "tunnel_data", "One piece of a streamed response's body.", (
        Field("id", "string", "req_1", "The request it answers."),
        Field("data", "string", "ZGF0YTogb2sKCg==", "The piece, base64."),
    ), since="0.28.0"),
    Message("http_body_end", "up", "tunnel_data", "A streamed response is finished.", (
        Field("id", "string", "req_1", "The request it answers."),
        Field("error", "string", doc="Present when the local server failed partway.",
              required=False, default=None),
    ), since="0.28.0"),
    Message("ws_open", "down", "tunnel_data", "A browser's WebSocket, to open locally.", (
        Field("id", "string", "ws_1", "Names the socket in its frames and close.", default=""),
        Field("path", "string", "/ws?client=programmer", "The path and query.",
              default="/"),
        Field("subprotocols", "array<string>|null", None, "Subprotocols the browser offered.",
              default=None, since="0.11.0",
              ignorable=True, why="An older agent opens its local socket without them, as that release always did."),
        Field("headers", "object|null", None, "The browser's headers.", default=None, since="0.11.0",
              ignorable=True, why="An older agent opens its local socket without them, as that release always did."),
    )),
    Message("ws_frame", "both", "tunnel_data", "One WebSocket message.", (
        Field("id", "string", "ws_1", "The socket.", default=""),
        Field("data", "string", "eyJ0eXBlIjoicGluZyJ9", "The message, base64.", default=""),
        Field("binary", "boolean", False, "A binary message; text otherwise.", default=None),
    )),
    Message("ws_close", "both", "tunnel_data",
            "A WebSocket closed at one end, so the other end closes too, with the same code.", (
                Field("id", "string", "ws_1", "The socket.", default=""),
                Field("code", "integer", 4001,
                      "The close code to pass on: the far end's own, or one of the "
                      "ws_close_codes. Absent from an older sender, which means a normal close.",
                      required=False, default=None, since=FIRST_CONTRACT_RELEASE,
                      ignorable=True,
                      why="An older agent closes its local socket normally, as it always has."),
                Field("reason", "string", "Authentication required",
                      "The far end's close reason, at most 123 bytes.", required=False,
                      default=None, since=FIRST_CONTRACT_RELEASE, ignorable=True,
                      why="A reason is for a log; nothing branches on it."),
            )),
)


def _inherit_since(fields: tuple[Field, ...], since: str) -> tuple[Field, ...]:
    """Each field with no since of its own takes its holder's, all the way down."""
    out = []
    for f in fields:
        own = f.since or since
        out.append(replace(
            f, since=own,
            fields=_inherit_since(f.fields, own), items=_inherit_since(f.items, own),
        ))
    return tuple(out)


MESSAGES = tuple(replace(m, fields=_inherit_since(m.fields, m.since)) for m in MESSAGES)


# --- Vocabularies ---

VOCABULARIES: dict[str, tuple[Entry, ...]] = {
    "capabilities": (
        Entry("monitoring", "Health, state and alerts reach the cloud."),
        Entry("remote_access", "Commands, project pushes and restarts from the cloud."),
        Entry("fleet_update", "OpenAVC updates sent from the cloud."),
        Entry("diagnostics", "Network checks run from this system."),
        Entry("tunnel", "Remote Access to the panel and the Programmer."),
        Entry("trusted_certs", "A trusted certificate issued through the cloud.", since="0.23.0"),
        Entry("tunnel_video", "Video across a Remote Access tunnel.", since="0.31.0"),
        Entry("saved_logins", "A pushed project's saved-login marker keeps the login saved here.", since="0.40.0"),
        Entry("saved_command_params",
              "A pushed project's saved-parameter marker keeps the parameter saved here.", since=FIRST_CONTRACT_RELEASE),
        Entry("panel_software_update",
              "This release can stage a panel software update (panel_software_update).", since=FIRST_CONTRACT_RELEASE),
    ),
    "deployment_modes": (
        Entry("windows_installer", "The Windows installer."),
        Entry("linux_package", "A Linux package or image."),
        Entry("macos_app", "The macOS app.", since="0.19.0"),
        Entry("android_appliance", "Appliance hardware.", since="0.16.0"),
        Entry("docker", "A Docker container."),
        Entry("git_dev", "A development checkout."),
        Entry("unknown", "None of the above could be told."),
    ),
    "refusal_reasons": (
        Entry("unexpected_message", "A handshake step arrived out of order."),
        Entry("bad_system_id", "The system id is not a valid id.",
              flags=frozenset({"stops_agent"})),
        Entry("unknown_system", "No such system: it was removed.",
              flags=frozenset({"stops_agent"})),
        Entry("no_key", "The system has no active key: it was unpaired.",
              flags=frozenset({"stops_agent"})),
        Entry("system_id_mismatch", "authenticate named a different system from hello."),
        Entry("bad_proof", "The proof did not verify."),
        Entry("bad_timestamp", "The proof's timestamp could not be read."),
        Entry("timestamp_expired", "The proof's timestamp is too far from the cloud's clock."),
    ),
    "ws_close_codes": (
        Entry(1000, "A normal close: the far end closed with no code of its own.", since=FIRST_CONTRACT_RELEASE),
        Entry(1014, "This instance could not open the socket on its own server, or that "
                    "socket dropped with no close frame.", since=FIRST_CONTRACT_RELEASE),
    ),
    "certificate_names": (
        Entry("*.{label}.{zone}", "Every host name under this system's label.", since="0.23.0"),
        Entry("{label}.{zone}", "This system's own name.", since="0.23.0"),
    ),
    "alert_rule_types": (
        Entry("threshold", "A reading crosses a value."),
        Entry("pattern", "A reading matches a value."),
        Entry("absence", "Keys stop changing for too long."),
    ),
    "alert_severities": (
        Entry("info", "Information."),
        Entry("warning", "Needs attention."),
        Entry("error", "Something failed."),
        Entry("critical", "Needs attention now."),
    ),
    "alert_categories": (
        Entry("device", "A device."),
        Entry("system", "This host."),
        Entry("service", "OpenAVC itself."),
        Entry("help", "Somebody in the room asked for help.", since="0.26.0"),
    ),
    "builtin_alert_keys": (
        Entry("system.cpu_percent", "Processor use, from the heartbeat."),
        Entry("system.memory_percent", "Memory use, from the heartbeat."),
        Entry("system.disk_percent", "Disk use, from the heartbeat."),
    ),
    "alert_resolved_reasons": (
        Entry("recovered", "The reading came back: a repair.", since="0.33.0"),
        Entry("rule_removed", "The rule left the set this instance evaluates: nothing happened "
                              "in the room.", since="0.33.0"),
    ),
    "config_push_modes": (
        Entry("full_replace", "The pushed project replaces this one."),
        Entry("selective_merge", "The cloud merged a template into this system's project "
                                 "before sending it; it replaces this one the same way."),
    ),
    "config_push_results": (
        Entry("Config applied", "A push's result when there was no earlier project to send "
                                "back for a rollback."),
    ),
    "diagnostic_actions": (
        Entry("ping", "Ping a host.", since="0.11.0"),
        Entry("tcp_check", "Open a TCP port.", since="0.11.0"),
        Entry("dns_lookup", "Resolve a name.", since="0.11.0"),
        Entry("traceroute", "Trace the route to a host.", since="0.11.0"),
        Entry("port_scan", "Check a range of TCP ports.", since="0.11.0"),
    ),
    "panel_install_modes": (
        Entry("at_panel", "Somebody starts the install at the panel.", since=FIRST_CONTRACT_RELEASE),
        Entry("when_idle", "The panel installs it once nobody has touched it for its idle "
                           "window.", since=FIRST_CONTRACT_RELEASE),
    ),
    "authorized_sessions": (
        Entry("owner", "The system's own account, with password-free remote programming on.", since="0.27.0"),
        Entry("support", "A live support-access grant from the customer.", since="0.27.0"),
    ),
    "cert_result_statuses": (
        Entry("enrollment", "The label and zone to put in the CSR.", since="0.23.0"),
        Entry("issued", "The certificate chain.", since="0.23.0"),
        Entry("error", "Why it could not.", since="0.23.0"),
    ),
    "cert_states": (
        Entry("requesting", "A request is under way.", since="0.23.0"),
        Entry("installed", "A certificate is installed.", since="0.23.0"),
        Entry("disabled", "Trusted certificates are switched off here.", since="0.23.0"),
        Entry("error", "The last request failed.", since="0.23.0"),
    ),
    "tunnel_clients": (
        Entry("panel", "A touch panel; the default."),
        Entry("programmer", "The Programmer IDE; needs a signed-in session."),
        Entry("preview", "The Programmer's panel preview; needs a signed-in session.", since="0.37.0"),
    ),
}


# --- Limits ---

LIMITS: dict[str, tuple[int, str]] = {
    "protocol_version": (1, "The wire version this agent prefers."),
    "min_protocol_version": (1, "The oldest wire version it still speaks."),
    "max_message_bytes": (2**20, "The largest message either side takes."),
    "message_size_margin": (8192, "Headroom a sender keeps for the envelope."),
    "max_embed_bytes": (2**20 - 8192, "The largest document one message may embed."),
    "reconnect_backoff_max_seconds": (300, "The longest wait between reconnects."),
    "tunnel_data_max_bytes": (2**24, "The largest message on a tunnel's data socket."),
    "tunnel_stream_idle_seconds": (300, "How long a streamed response may sit idle."),
    "room_name_max_chars": (200, "The longest room name pairing sends."),
}
