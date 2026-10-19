"""Finite ingress semantics with invented declarations, no device code imports.

Driver-owned tests qualify actual historical source/result tuples. These check
the platform's interpretation and refusal rules, not installed eligibility.
"""
import copy
import json

import pytest

from openavc.core.setup_legacy import generated_secret, result_from_legacy
from openavc.core.setup_results import Outcome, SetupCapacityError, SetupResultStore
from openavc.drivers.setup_compatibility import (
    SetupCompatibility, declaration_digest, parse_descriptors,
)


def descriptor(profile="boolean-check/v1", roles=None, **kwargs):
    return {"schema": "setup-compatibility/v1", "driver_id": "acme_widget", "driver_version": "1.0.0",
            "action_id": "check", "source_sha256": "1" * 64, "declaration_sha256": "2" * 64,
            "action_sha256": "3" * 64, "profile": profile,
            "roles": roles or {"success": "passed", "message": "note"}, "diagnostics": [], **kwargs}


def decode(profile, roles, raw, **kwargs):
    selected = SetupCompatibility.parse(descriptor(profile, roles, **kwargs))
    return result_from_legacy(selected, raw).payload()


def test_strict_check_preserves_failed_message_without_carrying_unknown_data():
    mapping = descriptor(diagnostics=[{"key": "count", "label": "Checks", "type": "integer",
                                      "required": True, "nullable": False}])
    result = result_from_legacy(SetupCompatibility.parse(mapping), {
        "passed": False, "note": "Device refused the check", "count": 0, "private_extra": "must-not-copy"})
    assert result.outcome is Outcome.FAILED
    assert result.payload()["findings"][0]["detail"] == "Device refused the check"
    assert result.payload()["findings"][1]["fields"] == [{"label": "Checks", "value": 0}]
    assert "must-not-copy" not in json.dumps(result.payload())


@pytest.mark.parametrize("passed", [0, 1, "true", None, [], {}])
def test_truthy_or_falsey_values_are_not_boolean_evidence(passed):
    with pytest.raises(ValueError):
        decode("boolean-check/v1", {"success": "passed", "message": "note"}, {"passed": passed, "note": ""})


@pytest.mark.parametrize("answered,silent,problems,expected", [
    ([], [], [], "unverified"), (["Input"], [], [], "succeeded"),
    (["Input"], [], ["Invalid configured row"], "degraded"),
    ([], ["Input"], [], "failed"),
])
def test_probe_evidence_overrules_nominal_success_and_preserves_bad_rows(answered, silent, problems, expected):
    result = decode("probe-check/v1", {"success": "ok", "answered": "answers", "silent": "silent",
                    "problems": "problems", "message": "message"},
                    {"ok": True, "answers": answered, "silent": silent, "problems": problems, "message": "Reported"})
    assert result["outcome"] == expected
    assert [f["detail"] for f in result["findings"] if f["code"] == "legacy.configuration"] == problems


@pytest.mark.parametrize("count,answered,failed,expected", [
    (0, [], [], "unverified"), (1, ["main"], [], "succeeded"),
    (2, ["main"], ["aux"], "degraded"), (1, [], ["main"], "failed"),
])
def test_block_check_requires_actual_nonzero_checked_blocks(count, answered, failed, expected):
    result = decode("block-check/v1", {"count": "count", "answered": "good", "failed": "bad"},
                    {"count": count, "good": answered, "bad": failed})
    assert result["outcome"] == expected
    assert result["findings"][0]["fields"][0]["value"] == count


@pytest.mark.parametrize("count", [True, -1, 1.0, 2**53, 2])
def test_invalid_or_inconsistent_counts_never_become_success(count):
    with pytest.raises(ValueError):
        decode("block-check/v1", {"count": "count", "answered": "good", "failed": "bad"},
               {"count": count, "good": ["main"], "bad": []})


def test_reachability_and_empty_inventory_do_not_claim_complete_verification():
    assert decode("inventory-check/v1", {"reachable": "reachable", "count": "count", "items": "items"},
                  {"reachable": True, "count": 0, "items": []})["outcome"] == "unverified"


@pytest.mark.parametrize("authenticated,expected", [(True, "succeeded"), (False, "unverified")])
def test_optional_session_authentication_is_not_a_rejected_password(authenticated, expected):
    result = decode("session-check/v1", {"authenticated": "auth"}, {"auth": authenticated})
    assert result["outcome"] == expected
    assert result["findings"][0]["fields"][0]["value"] is authenticated


def test_credential_check_with_authentication_disabled_does_not_claim_authentication():
    result = decode("credential-check/v1", {"success": "ok", "enabled": "enabled", "message": "message"},
                    {"ok": False, "enabled": False, "message": "Unauthenticated device"})
    assert result["outcome"] == "unverified"


@pytest.mark.parametrize("accepted", [True, False])
def test_provisioning_claim_never_proves_readback_or_no_earlier_effects(accepted):
    result = decode("provisioning-claim/v1", {"accepted": "enabled"}, {"enabled": accepted})
    assert result["outcome"] == "unverified"
    assert result["findings"][0]["fields"][0]["value"] is accepted
    assert result["config_save"] == result["reconnect"] == "not_requested"


def test_pairing_separates_declared_key_from_diagnostics_and_unrelated_passwords():
    selected = SetupCompatibility.parse(descriptor("pairing/v1", {"paired": "paired"},
        generated_secret="key", diagnostics=[{"key": "identity", "label": "Device ID", "type": "string",
                                                "required": True, "nullable": False}]))
    raw = {"paired": True, "key": "generated-marker", "password": "operator-marker", "identity": "00001"}
    assert generated_secret(selected, raw) == {"key": "generated-marker"}
    assert generated_secret(selected, {"password": "operator-marker"}) is None
    result = result_from_legacy(selected, raw).payload()
    assert result["outcome"] == "succeeded"
    assert "marker" not in json.dumps(result)
    assert result["findings"][0]["fields"] == [{"label": "Device ID", "value": "00001"}]


def test_optional_absent_and_reported_null_diagnostics_remain_distinct():
    selected = SetupCompatibility.parse(descriptor(diagnostics=[
        {"key": "firmware", "label": "Firmware", "type": "string", "required": False, "nullable": True}]))
    absent = result_from_legacy(selected, {"passed": True, "note": ""}).payload()
    present = result_from_legacy(selected, {"passed": True, "note": "", "firmware": None}).payload()
    assert len(absent["findings"]) == 1
    assert present["findings"][1]["fields"][0]["value"] is None


@pytest.mark.parametrize("value,accepted", [("a" * 16374, True), ("a" * 16375, False),
    ("é" * 2729, True), ("é" * 2730, False), ('"' * 8192, False)])
def test_extracted_credentials_fit_the_actual_reserved_json_bytes(value, accepted):
    selected = SetupCompatibility.parse(descriptor("pairing/v1", {"paired": "paired"}, generated_secret="key"))
    store = SetupResultStore()
    target = object()
    def current():
        return True
    prepared = store.prepare({}, target=target, original_grant=current, current=current)
    slot = store.claim(prepared.capability, {}, target=target, current=current)
    if not accepted:
        with pytest.raises(SetupCapacityError):
            generated_secret(selected, {"key": value})
        with pytest.raises(SetupCapacityError):
            store.publish_secret(slot, {"key": value})
        return
    secret = generated_secret(selected, {"key": value})
    assert store.publish_secret(slot, secret) == 1
    assert store.retrieve(prepared.capability, current=current)["values"] == secret


@pytest.mark.parametrize("change", [
    {"schema": "future"}, {"profile": "inferred/v1"}, {"source_sha256": "1234"},
    {"roles": {"success": "passed", "message": "result.message"}},
    {"roles": {"success": "same", "message": "same"}}, {"callback": "module.convert"},
    {"generated_secret": "passed"}, {"roles": {"success": "passed"}},
])
def test_descriptor_refuses_unknown_or_ambiguous_contracts(change):
    with pytest.raises(ValueError):
        SetupCompatibility.parse(descriptor(**change))


def test_secret_role_cannot_be_named_as_a_diagnostic_and_duplicates_are_refused():
    item = descriptor("pairing/v1", {"paired": "paired"}, generated_secret="key", diagnostics=[
        {"key": "key", "label": "Device details", "type": "string", "required": True, "nullable": False}])
    with pytest.raises(ValueError):
        SetupCompatibility.parse(item)
    item = descriptor()
    with pytest.raises(ValueError):
        parse_descriptors([item, copy.deepcopy(item)])


def test_selection_is_frozen_and_canonical_digest_keeps_false_zero_null_distinct():
    item = descriptor()
    selected = SetupCompatibility.parse(item)
    item["roles"]["success"] = "changed"
    assert dict(selected.roles)["success"] == "passed"
    assert len({declaration_digest({"value": v}) for v in (False, 0, None)}) == 3
    assert declaration_digest({"b": 1, "a": "x"}) == declaration_digest({"a": "x", "b": 1})


@pytest.mark.parametrize("value", [["id"], ["config_schema"], ["commands", "commands"], "commands", [None], [{}]])
def test_runtime_surface_declaration_is_finite_and_unique(value):
    with pytest.raises(ValueError):
        SetupCompatibility.parse(descriptor(runtime_surfaces=value))


def test_runtime_action_shaping_only_removes_non_setup_entries_in_order():
    from openavc.drivers.setup_compatibility import instance_declaration_matches
    original = {"id": "acme", "actions": [
        {"id": "first", "kind": "command"}, {"id": "check", "kind": "setup"},
        {"id": "last", "kind": "command"}]}
    for selected, expected in [([1], True), ([0, 1], True), ([1, 2], True),
                               ([2, 1], False), ([0, 2], False), ([1, 1], False)]:
        current = {**original, "actions": [original["actions"][i] for i in selected]}
        assert instance_declaration_matches(original, current, ("actions",)) is expected
