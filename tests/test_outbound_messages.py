"""Disclosure and preservation contracts for the outbound projection owner.

These exercise the owner before socket integration. Hub/direct-send composition
and rendered clients are separate acceptance boundaries.
"""
from dataclasses import replace
import json

import pytest

from openavc.core.outbound_messages import MessageScope, OutboundPolicy, Recipient, RoomText


def recipient(kind="panel", **kwargs):
    return Recipient(object(), kind, kwargs.pop("current", lambda: True), **kwargs)


def delivered(policy, message, client, **kwargs):
    envelope = policy.project(message, client, **kwargs)
    assert envelope is not None
    wire = envelope.for_send(client)
    assert wire is not None
    return json.loads(wire)


@pytest.mark.parametrize("kind", ["panel", "preview", "programmer"])
def test_state_controls_audio_and_explicit_deletion_survive(kind):
    p, c = OutboundPolicy(), recipient(kind)
    values = {"device.acme.level": -12.0, "var.mode": "presentation",
              "plugin.audio_player.play_request": '{"id":"one","url":"/assets/chime.wav"}',
              "ui.status.text": None}
    for name, field in (("snapshot", "state"), ("update", "changes")):
        result = delivered(p, {"type": f"state.{name}", field: values,
                               "config": {"password": "private-marker"}}, c)
        assert result == {"type": f"state.{name}", field: values}
    assert delivered(p, {"type": "state.delete", "keys": list(values)}, c)["keys"] == list(values)


def test_effective_ui_keeps_custom_controls_but_stored_lock_pin_is_refused():
    p, c = OutboundPolicy(), recipient()
    ui = {"settings": {"lock_enabled": True}, "pages": [
        {"id": "home", "elements": [{"type": "custom", "source": "ui/custom.html"}]}]}
    assert delivered(p, {"type": "ui.definition", "ui": ui}, c)["ui"] == ui
    ui["settings"]["lock_code"] = "private-marker"
    assert p.project({"type": "ui.definition", "ui": ui}, c) is None
    assert delivered(p, {"type": "ui.files", "version": "123", "path": "ui/custom.html"}, c) == {
        "type": "ui.files", "version": "123"}
    assert delivered(p, {"type": "ui.navigate", "page_id": "home"}, c)["page_id"] == "home"


@pytest.mark.parametrize("kind", ["panel", "preview"])
def test_nested_macro_diagnostics_never_enter_room_projection(kind):
    p, c = OutboundPolicy(), recipient(kind)
    message = {"type": "macro.progress", "macro_id": "present", "step_index": 2,
               "total_steps": 4, "description": "Select the display input",
               "action": "group.command", "status": "group_complete",
               "step_path": [1, "then", 2],
               "device_results": [{"device_id": "acme", "error": {"key": "nested-private"}}],
               "actual_value": "condition-private", "error": "exception-private",
               "unexpected": {"secret": "extra-private"}}
    result = delivered(p, message, c)
    assert result["macro_id"] == "present" and result["total_steps"] == 4
    assert result["description"] == "Select the display input"
    assert "private" not in json.dumps(result)
    admin_result = delivered(p, message, recipient("programmer"))
    assert admin_result["device_results"] == message["device_results"]
    assert admin_result["actual_value"] == "condition-private"
    assert "unexpected" not in admin_result


def test_macro_lifecycle_balances_old_panel_and_preserves_safe_failure_guidance():
    p, c = OutboundPolicy(), recipient()
    for name in ("started", "completed", "error", "cancelled"):
        result = delivered(p, {"type": f"macro.{name}", "macro_id": "present",
                               "total_steps": 2, "error": "secret-traceback"}, c)
        assert result["macro_id"] == "present"
        assert "secret-traceback" not in json.dumps(result)
    result = delivered(p, {"type": "macro.step_error", "macro_id": "child",
                           "call_chain": ["present", "child"], "error": "secret-traceback",
                           "room_guidance": RoomText("The display is offline.")}, c)
    assert result["call_chain"] == ["present", "child"]
    assert result["message"] == "The display is offline."


def test_project_revision_and_edit_origin_remain_programmer_only():
    p = OutboundPolicy()
    message = {"type": "project.reloaded", "revision": "r2", "origin_client": "editor-tab"}
    assert delivered(p, message, recipient()) == {"type": "project.reloaded"}
    assert delivered(p, message, recipient("programmer")) == message


@pytest.mark.parametrize("message", [
    {"type": "script.error", "error": "private-trace"},
    {"type": "plugin.error", "plugin_id": "acme", "error": "private-trace"},
    {"type": "trigger.completed", "trigger_id": "acme", "error": "private-trace"},
    {"type": "discovery.complete", "status": "partial", "warnings": ["private-trace"]},
    {"type": "log.entry", "message": "private-trace"},
])
def test_admin_diagnostics_require_programmer_even_for_direct_sends(message):
    p = OutboundPolicy()
    for kind in ("panel", "preview"):
        assert p.project(message, recipient(kind), targeted=True) is None
    assert "private-trace" in json.dumps(delivered(p, message, recipient("programmer"), targeted=True))


@pytest.mark.parametrize("message", [
    {"type": "error", "source_type": "ui.change", "element_id": "level", "message": "raw-private"},
    {"type": "command.ack", "device_id": "acme", "command": "select", "success": False,
     "error": "raw-private"},
    {"type": "state.set.ack", "key": "var.mode", "success": False, "error": "raw-private"},
])
def test_failure_replies_keep_correlation_without_broadcast_or_raw_error(message):
    p, c = OutboundPolicy(), recipient()
    assert p.project(message, c) is None
    result = delivered(p, message, c, targeted=True)
    assert "raw-private" not in json.dumps(result)
    for key in ("source_type", "element_id", "device_id", "command", "key", "success"):
        if key in message:
            assert result[key] == message[key]
    assert result.get("message", result.get("error"))


def test_queued_bytes_are_frozen_and_revocation_checked_at_send():
    p = OutboundPolicy()
    current = True
    c = recipient("programmer", current=lambda: current)
    message = {"type": "macro.progress", "macro_id": "acme", "device_results": [{"error": "original"}]}
    envelope = p.project(message, c)
    assert envelope is not None
    message["device_results"][0]["error"] = "changed-after-admission"
    assert "original" in envelope.for_send(c)
    assert envelope.for_send(replace(c, client_type="panel")) is None
    assert envelope.for_send(recipient("programmer")) is None
    current = False
    assert envelope.for_send(c) is None


def test_audit_scope_is_not_public_session_id_and_is_rechecked():
    p = OutboundPolicy()
    scope, another = MessageScope(), MessageScope()
    active = {scope}
    c = recipient("programmer", has_scope=lambda s: s in active)
    message = {"type": "audit.state", "session_id": "public-id", "state": {"detail": "private"}}
    for wrong in (None, "public-id", another):
        assert p.project(message, c, targeted=True, scope=wrong) is None
    envelope = p.project(message, c, targeted=True, scope=scope)
    assert envelope is not None and envelope.for_send(c) is not None
    active.clear()
    assert envelope.for_send(c) is None


def test_unknown_and_malformed_messages_fail_closed_without_payload_logging(caplog):
    p, c = OutboundPolicy(), recipient("programmer")
    for number in range(100):
        assert p.project({"type": f"private-name-{number}", "secret": "private-data"}, c) is None
    assert p.refused == {"unknown": 100, "shape": 0}
    assert "private-name" not in caplog.text and "private-data" not in caplog.text
    assert caplog.text.count("Outbound message refused") == 1
    assert p.project({"type": "state.update", "changes": {"var.x": {"secret": "value"}}}, c) is None
    assert p.project({"type": "macro.progress", "macro_id": "x", "description": {"secret": "value"}}, c) is None
    assert p.project({"type": "state.update", "changes": {"var.x": float("nan")}}, c) is None
    assert p.project({"type": "state.update"}, c) is None


def test_authority_verifier_failure_is_closed_and_does_not_log_secret(caplog):
    def broken():
        raise RuntimeError("private-verifier-data")
    assert OutboundPolicy().project({"type": "ping"}, recipient(current=broken)) is None
    assert "private-verifier-data" not in caplog.text
