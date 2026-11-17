"""The wire contract's tables decide what the agent sends, reads and refuses.

``openavc/cloud/wire_tables.py`` holds every message, field and vocabulary the
agent and the cloud exchange; ``protocol.py`` derives the agent's own tables
from it. These tests hold the derived tables to what the agent did before they
were derived, hold each builder and reader to its row, and hold the rules that
let an older release keep working with a newer cloud.
"""

from __future__ import annotations

import base64
import json

import pytest

from openavc.cloud import protocol, wire_tables
from openavc.cloud.contract_gen import render
from openavc.cloud.protocol import (
    CAPABILITY_GATED, MESSAGE_PRIORITY, REQUEST_ANSWERS, ProtocolError,
    read_message, read_payload, read_tunnel_data,
)
from openavc.cloud.wire_tables import (
    ABSENT, BASELINE, FIRST_CONTRACT_RELEASE, MESSAGES, VOCABULARIES,
)


def _v(version: str) -> tuple[int, ...]:
    return tuple(int(p) for p in version.split("."))


# --- The derived tables are the ones the agent used before ---


def test_message_sets_are_the_agents_own():
    assert protocol.HANDSHAKE_TYPES == {
        "hello", "challenge", "authenticate", "session_start", "auth_failed",
        "version_mismatch", "resume", "resume_from",
    }
    assert protocol.UPSTREAM_TYPES == {
        "heartbeat", "state_batch", "alert", "alert_resolved", "active_alerts",
        "command_result", "tunnel_ready", "tunnel_failed", "diagnostic_result", "pong",
        "ai_tool_result", "project_data", "monitors", "device_commands_data", "gap_report",
        "cert_request", "cert_status",
    }
    assert protocol.DOWNSTREAM_TYPES == {
        "ack", "session_rotate", "session_invalid", "config_update",
        "capabilities_update", "throttle", "error", "command", "config_push",
        "diagnostic", "software_update", "panel_software_update", "tunnel_open",
        "tunnel_close", "restart", "ping", "alert_rules_update", "ai_tool_call",
        "get_project", "get_device_commands", "cert_result", "cert_renew_due",
        "help_acknowledged",
    }
    assert protocol.TUNNEL_DATA_TYPES == {
        "http_request", "http_cancel", "http_response", "http_response_start",
        "http_body", "http_body_end", "ws_open", "ws_frame", "ws_close",
    }


def test_every_type_constant_names_a_row_and_every_row_has_one():
    constants = {
        value for name, value in vars(protocol).items()
        if name.isupper() and isinstance(value, str) and value in protocol.MESSAGE_ROWS
    }
    assert constants == set(protocol.MESSAGE_ROWS)


def test_buffer_priorities_are_unchanged():
    assert MESSAGE_PRIORITY == {
        "state_batch": 0, "heartbeat": 2, "alert_resolved": 3, "alert": 4,
        "active_alerts": 4, "command_result": 5, "tunnel_ready": 5, "tunnel_failed": 5,
        "diagnostic_result": 5, "ai_tool_result": 5, "project_data": 5,
        "device_commands_data": 5, "gap_report": 7, "cert_request": 5, "cert_status": 5,
        "pong": 6, "session_rotate": 10, "ack": 9, "session_invalid": 9,
        "config_update": 7, "capabilities_update": 7, "command": 7, "config_push": 7,
        "restart": 8, "ping": 6, "tunnel_open": 5, "tunnel_close": 5, "diagnostic": 4,
        "software_update": 4, "panel_software_update": 4, "alert_rules_update": 4,
        "ai_tool_call": 5, "get_project": 5, "cert_result": 5, "cert_renew_due": 4,
        "help_acknowledged": 6, "throttle": 3, "error": 3,
    }


def test_every_capability_gate_holds():
    # Dropping one of these lets a plan without the capability drive hardware,
    # replace the project, restart the service or open a tunnel.
    assert CAPABILITY_GATED == {
        "tunnel_open": "tunnel", "tunnel_close": "tunnel", "diagnostic": "diagnostics",
        "software_update": "fleet_update", "panel_software_update": "panel_software_update",
        "command": "remote_access", "config_push": "remote_access",
        "restart": "remote_access", "cert_renew_due": "trusted_certs",
    }
    from openavc.cloud.agent import _CAPABILITY_GATED
    assert _CAPABILITY_GATED is CAPABILITY_GATED


def test_each_refused_request_is_answered_by_its_own_result():
    assert REQUEST_ANSWERS == {
        "command": "command_result", "config_push": "command_result",
        "restart": "command_result", "software_update": "command_result",
        "panel_software_update": "command_result", "get_project": "project_data",
        "get_device_commands": "device_commands_data", "diagnostic": "diagnostic_result",
        "ai_tool_call": "ai_tool_result",
    }
    for answer in REQUEST_ANSWERS.values():
        assert answer in protocol.UPSTREAM_PAYLOAD_BUILDERS


def test_the_hello_announces_every_capability_in_the_vocabulary():
    from openavc.cloud.agent import DEFAULT_CAPABILITIES
    assert DEFAULT_CAPABILITIES == [
        "monitoring", "remote_access", "fleet_update", "diagnostics", "tunnel",
        "trusted_certs", "tunnel_video", "saved_logins", "saved_command_params",
        "panel_software_update",
    ]
    for capability in CAPABILITY_GATED.values():
        assert capability in DEFAULT_CAPABILITIES


def test_the_agent_stops_only_on_a_refusal_that_means_its_key_is_gone():
    assert protocol.FATAL_REFUSAL_REASONS == {"unknown_system", "no_key", "bad_system_id"}


def test_limits_are_unchanged():
    assert protocol.PROTOCOL_VERSION == 1
    assert protocol.MIN_PROTOCOL_VERSION == 1
    assert protocol.MAX_MESSAGE_BYTES == 2**20
    assert protocol.MESSAGE_SIZE_MARGIN == 8192
    assert protocol.MAX_EMBED_BYTES == protocol.MAX_MESSAGE_BYTES - protocol.MESSAGE_SIZE_MARGIN
    assert protocol.RECONNECT_BACKOFF_MAX == 300
    from openavc.cloud.agent import BACKOFF_MAX
    assert BACKOFF_MAX == protocol.RECONNECT_BACKOFF_MAX


# --- The vocabularies are the ones the code uses ---


def test_deployment_modes_are_the_updaters():
    from openavc.updater.platform import DeploymentType
    assert {d.value for d in DeploymentType} == set(protocol.vocabulary("deployment_modes"))


def test_the_tunnel_client_roles_are_the_ones_the_local_socket_admits():
    import inspect

    from openavc.api import ws
    source = inspect.getsource(ws)
    assert 'not in ("panel", "programmer", "preview")' in source
    assert set(protocol.vocabulary("tunnel_clients")) == {"panel", "programmer", "preview"}


def test_alert_resolved_reasons_are_the_vocabulary():
    assert set(protocol.vocabulary("alert_resolved_reasons")) == {
        protocol.RESOLVED_RECOVERED, protocol.RESOLVED_RULE_REMOVED,
    }


def test_panel_install_modes_are_the_updaters():
    from openavc.updater import panel_software
    assert set(protocol.vocabulary("panel_install_modes")) == set(panel_software.INSTALL_MODES)


def test_the_certificate_names_are_the_csrs():
    from cryptography import x509

    from openavc.cloud.cert_manager import generate_key_and_csr
    _key, csr_pem = generate_key_and_csr("a1b2c3", "systems.example.invalid")
    csr = x509.load_pem_x509_csr(csr_pem.encode())
    sans = csr.extensions.get_extension_for_class(x509.SubjectAlternativeName)
    assert set(sans.value.get_values_for_type(x509.DNSName)) == {
        name.format(label="a1b2c3", zone="systems.example.invalid")
        for name in protocol.CERTIFICATE_NAMES
    }


def test_a_push_with_no_earlier_project_answers_the_vocabularys_word():
    assert protocol.CONFIG_APPLIED == "Config applied"


# --- Rows ---


def test_no_type_or_field_is_named_twice():
    types = [m.type for m in MESSAGES]
    assert len(types) == len(set(types))
    for m in MESSAGES:
        names = [f.name for f in m.fields]
        assert len(names) == len(set(names)), m.type


def test_every_vocabulary_a_field_names_exists():
    def walk(fields):
        for f in fields:
            yield f
            yield from walk(f.fields)
            yield from walk(f.items)

    for m in MESSAGES:
        for f in walk(m.fields):
            if f.vocabulary:
                assert f.vocabulary in VOCABULARIES, (m.type, f.name)


def test_every_answer_and_follows_names_an_upstream_message():
    rows = protocol.MESSAGE_ROWS
    for m in MESSAGES:
        for name in (m.answer, m.follows):
            if name:
                assert rows[name].direction == "up", (m.type, name)
        for capability in (m.requires, m.announced_by):
            if capability:
                assert capability in protocol.CAPABILITIES, (m.type, capability)


def _nested(fields, holders):
    for f in fields:
        yield f, holders
        yield from _nested(f.fields, holders + (f.since,))
        yield from _nested(f.items, holders + (f.since,))


def _down_entries():
    """Every downstream message and field, nested ones included, with the
    sinces of what holds each field."""
    for m in MESSAGES:
        if m.direction in ("down", "both"):
            yield m, None, ()
            for f, holders in _nested(m.fields, (m.since,)):
                yield m, f, holders


def test_a_downstream_entry_newer_than_the_baseline_says_how_an_older_agent_copes():
    for m, f, holders in _down_entries():
        since = (f or m).since
        if _v(since) <= _v(BASELINE):
            continue
        if f is None:
            assert m.announced_by or m.follows or m.ignorable, (
                f"{m.type} is newer than {BASELINE}: an older agent ignores it, so the cloud "
                f"may send it only where a capability announced it (announced_by) or in "
                f"answer to the system's own message (follows), or an older agent must do "
                f"the right thing without it (ignorable, with why)"
            )
            if m.ignorable:
                assert m.why, f"{m.type} is ignorable: say why"
        else:
            # A field that arrived with what holds it is that holder's question.
            covered = m.announced_by or m.follows or any(_v(h) >= _v(since) for h in holders)
            assert f.ignorable or covered, (
                f"{m.type}.{f.name} is newer than {BASELINE}: mark it ignorable with why, or "
                f"put it behind a capability"
            )
            if f.ignorable:
                assert f.why, f"{m.type}.{f.name} is ignorable: say why"


def test_a_downstream_entry_newer_than_the_contract_is_announced_by_a_capability():
    for m, f, _holders in _down_entries():
        since = (f or m).since
        if _v(since) <= _v(FIRST_CONTRACT_RELEASE):
            continue
        assert m.announced_by, (
            f"{m.type}{'.' + f.name if f else ''} arrives after the contract did: the cloud "
            f"must know the system reads it, so it needs a capability (announced_by)"
        )


def test_a_field_is_never_older_than_what_holds_it():
    def walk(fields, holder):
        for f in fields:
            assert _v(f.since) >= _v(holder), f.name
            yield from walk(f.fields, f.since)
            yield from walk(f.items, f.since)

    for m in MESSAGES:
        list(walk(m.fields, m.since))


def test_the_contracts_history_starts_at_the_first_floor():
    # Fixed where the contract began; the cloud raises its own floor apart
    # from this, so a raise never drops a mark an older agent still needs.
    assert BASELINE == "0.5.14"


def test_a_since_is_a_release_number():
    def sinces():
        for m in MESSAGES:
            yield m.since
            for f in m.fields:
                yield f.since
        for entries in VOCABULARIES.values():
            for e in entries:
                yield e.since

    for since in sinces():
        assert len(_v(since)) == 3
        assert _v(since) >= _v(BASELINE)


# --- Readers ---


@pytest.mark.parametrize("row", [m for m in MESSAGES if m.direction != "up"],
                         ids=lambda m: m.type)
def test_an_empty_payload_reads_as_the_tables_defaults(row):
    if row.phase == "tunnel_data":
        read = read_tunnel_data({"type": row.type})
        read.pop("type")
    else:
        read = read_payload(row.type, {})
    expected = {f.name: f.default for f in row.fields if f.default is not ABSENT}
    assert read == expected


def test_a_sent_null_is_kept_and_an_unknown_field_passes_through():
    read = read_payload("command", {"params": None, "extra": 1})
    assert read["params"] is None
    assert read["extra"] == 1
    assert read["device_id"] == ""


def test_a_default_is_never_shared_between_reads():
    first = read_payload("command", {})
    first["params"]["x"] = 1
    assert read_payload("command", {})["params"] == {}


def test_a_payload_that_is_not_an_object_is_a_protocol_error():
    with pytest.raises(ProtocolError):
        read_payload("command", ["not", "an", "object"])


def test_read_message_reads_the_messages_own_payload():
    msg = {"type": "throttle", "payload": {"limit": "state_batch"}}
    assert read_message(msg) == {"limit": "state_batch", "retry_after_seconds": 30}


def test_config_update_fills_nothing_so_only_what_it_carries_changes():
    assert read_payload("config_update", {"heartbeat_interval": 10}) == {
        "heartbeat_interval": 10,
    }


# --- The tunnel's close codes ---


@pytest.mark.parametrize("code,sent", [
    (1000, 1000), (1001, 1001), (1011, 1011), (4001, 4001), (4010, 4010), (4013, 4013),
    (1005, 1000), (1006, 1014), (1015, 1014), (None, 1014), (999, 1014), (5000, 1014),
    # The edges of each range the contract gives a close frame.
    (1003, 1003), (1004, 1014), (1007, 1007), (1014, 1014), (2999, 1014), (3000, 3000),
    (4999, 4999),
])
def test_a_close_code_is_passed_on_when_a_close_frame_may_carry_it(code, sent):
    assert protocol.ws_close_code(code) == sent


def test_the_codes_this_instance_sends_are_the_vocabulary():
    assert set(protocol.vocabulary("ws_close_codes")) == {
        protocol.WS_CLOSE_NORMAL, protocol.WS_CLOSE_LOCAL_UNREACHABLE,
    }


@pytest.mark.parametrize("code,usable", [
    (4001, 4001), (1000, 1000), (1006, None), ("4001", None), (True, None), (None, None),
])
def test_only_a_sendable_received_code_closes_the_local_socket(code, usable):
    assert protocol.sendable_close_code(code) == usable


def _contract() -> dict:
    """The contract as the cloud reads it: the generated file, parsed."""
    return json.loads(render())


def test_the_contract_carries_the_close_frame_rule_for_the_cloud():
    """The cloud closes a browser's socket with the code the agent passes on,
    so it reads which codes a close frame may carry, and how long a reason may
    be, from the contract rather than writing its own."""
    limits = _contract()["limits"]
    assert limits["ws_close_sendable_codes"]["value"] == [[1000, 1003], [1007, 1014], [3000, 4999]]
    assert limits["ws_close_reason_max_bytes"]["value"] == 123


def test_a_field_drawn_from_a_vocabulary_names_it():
    """So a reader projecting the contract to an older release can drop the
    values that release did not have yet."""
    hello = _contract()["messages"]["hello"]["payload"]["properties"]
    assert hello["capabilities"]["x-vocabulary"] == "capabilities"
    assert hello["deployment_mode"]["x-vocabulary"] == "deployment_modes"


def test_a_close_reason_is_cut_to_what_a_close_frame_carries():
    msg = protocol.build_ws_close("ws_1", 4001, "é" * 100)
    assert len(msg["reason"].encode("utf-8")) <= 123
    assert protocol.build_ws_close("ws_1", 1000) == {"type": "ws_close", "id": "ws_1",
                                                     "code": 1000}


def test_data_builders_make_the_shapes_the_cloud_reads():
    body = base64.b64encode(b"<html></html>").decode()
    assert protocol.build_http_response("r", 200, {}, body) == {
        "type": "http_response", "id": "r", "status": 200, "headers": {}, "body": body,
    }
    assert protocol.build_http_body_end("r") == {"type": "http_body_end", "id": "r"}
    assert protocol.build_http_body_end("r", "boom")["error"] == "boom"
    assert protocol.build_ws_frame("w", "AA==", True) == {
        "type": "ws_frame", "id": "w", "data": "AA==", "binary": True,
    }


def test_the_wire_tables_are_imported_only_by_protocol():
    from pathlib import Path

    root = Path(protocol.__file__).resolve().parents[1]
    importers = [
        p.relative_to(root).as_posix() for p in root.rglob("*.py")
        if "wire_tables" in p.read_text(encoding="utf-8")
        and p.name not in ("protocol.py", "wire_tables.py", "contract_gen.py")
    ]
    assert importers == []
    assert wire_tables.MESSAGES is MESSAGES
