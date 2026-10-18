"""Result-owner invariants; runner/API/browser composition is tested separately.

All credentials and devices here are invented. A reservation alone must never
start work, and result retention must not manufacture a retry after uncertainty.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from openavc.core.outbound_messages import RoomText
from openavc.core.setup_results import (
    Outcome, SetupCapacityError, SetupConflict, SetupFinding, SetupResult,
    SetupResultStore, SetupUnavailable, StageState,
)


def allowed():
    return True


def request():
    return {"device_id": "acme", "action_id": "pair", "contract": "pair/v1",
            "params": {"save": False, "nested": {"value": "before"}}}


def result():
    return SetupResult(Outcome.DEGRADED, RoomText("Setup needs attention."),
                       (SetupFinding("config.save", Outcome.UNVERIFIED, "save raised after write"),),
                       config_save=StageState.UNKNOWN)


def prepared(store, *, grant=allowed, principal=None):
    target = object()
    item = store.prepare(request(), target=target, original_grant=grant,
                         current=allowed, principal=principal)
    return item, target


def started(store, *, grant=allowed, principal=None):
    item, target = prepared(store, grant=grant, principal=principal)
    slot = store.claim(item.capability, request(), target=target, current=allowed, principal=principal)
    assert slot is not None
    store.running(slot)
    return item, slot


def test_prepare_freezes_inputs_and_requires_exact_target_before_claim():
    store = SetupResultStore()
    target = object()
    body = request()
    item = store.prepare(body, target=target, original_grant=allowed, current=allowed)
    assert store.status(item.capability, current=allowed)["status"] == "prepared"
    body["params"]["nested"]["value"] = "after"
    with pytest.raises(SetupConflict):
        store.claim(item.capability, body, target=target, current=allowed)
    with pytest.raises(SetupConflict):
        store.claim(item.capability, request(), target=object(), current=allowed)
    changed = request()
    changed["contract"] = "pair/v2"
    with pytest.raises(SetupConflict):
        store.claim(item.capability, changed, target=target, current=allowed)
    assert store.claim(item.capability, request(), target=target, current=allowed) is not None
    assert item.capability not in repr(item)


async def test_concurrent_starts_and_lost_response_recovery_do_not_dispatch_twice():
    store = SetupResultStore()
    item, target = prepared(store)
    dispatched = []

    async def client_start():
        slot = store.claim(item.capability, request(), target=target, current=allowed)
        if slot is not None:
            dispatched.append(slot.run_id)
            await asyncio.sleep(0)  # first response can disappear after dispatch
            store.complete(slot, result())
        return store.status(item.capability, current=allowed)

    await asyncio.gather(*(client_start() for _ in range(10)))
    assert dispatched == [item.run_id]
    assert store.status(item.capability, current=allowed)["status"] == "terminal"
    assert store.claim(item.capability, request(), target=target, current=allowed) is None


def test_abandoned_prepare_expires_but_active_capacity_survives_age_and_ack():
    now = [0.0]
    store = SetupResultStore(max_slots=1, prepare_ttl=5, result_ttl=10, clock=lambda: now[0])
    abandoned, _ = prepared(store)
    with pytest.raises(SetupCapacityError):
        prepared(store)
    now[0] = 5
    item, slot = started(store)
    with pytest.raises(SetupUnavailable):
        store.status(abandoned.capability, current=allowed)
    store.publish_secret(slot, {"app_key": "invented-credential"})
    generation = store.retrieve(item.capability, current=allowed)["generation"]
    store.acknowledge(item.capability, generation, current=allowed)
    now[0] = 500
    with pytest.raises(SetupCapacityError):
        prepared(store)
    store.publish_secret(slot, {"app_key": "late-credential"})
    store.complete(slot, result())
    now[0] = 509
    assert store.retrieve(item.capability, current=allowed)["values"]["app_key"] == "late-credential"
    now[0] = 510
    with pytest.raises(SetupUnavailable):
        store.claim(item.capability, request(), target=object(), current=allowed)
    prepared(store)  # terminal retention has now released the bounded slot


def test_revoked_original_grant_never_revives_and_late_secrets_do_not_free_capacity():
    valid = [True]
    store = SetupResultStore(max_slots=1)
    item, slot = started(store, grant=lambda: valid[0])
    store.publish_secret(slot, {"app_key": "before-revocation"})
    valid[0] = False
    with pytest.raises(SetupUnavailable):
        store.retrieve(item.capability, current=allowed)
    valid[0] = True  # replacement credential / interface re-enable is no revival
    store.publish_secret(slot, {"app_key": "created-after-revocation"})
    assert slot.secret is None
    with pytest.raises(SetupUnavailable):
        store.status(item.capability, current=allowed)
    with pytest.raises(SetupCapacityError):
        prepared(store)
    store.complete(slot, result())
    prepared(store)


def test_current_request_and_stable_principal_are_required_in_addition_to_capability():
    store = SetupResultStore(per_principal=1)
    item, slot = started(store, principal="operator-1")
    store.publish_secret(slot, {"app_key": "original"})
    with pytest.raises(SetupCapacityError):
        prepared(store, principal="operator-1")
    prepared(store, principal="operator-2")
    for principal, current in ((None, allowed), ("operator-2", allowed), ("operator-1", lambda: False)):
        with pytest.raises(SetupUnavailable):
            store.retrieve(item.capability, current=current, principal=principal)
    assert store.retrieve(item.capability, current=allowed, principal="operator-1")["values"] == {"app_key": "original"}


def test_retrieval_loss_and_old_ack_or_save_cannot_erase_a_later_secret():
    store = SetupResultStore()
    item, slot = started(store)
    first = store.publish_secret(slot, {"app_key": "first"})
    with pytest.raises(SetupConflict):
        store.acknowledge(item.capability, first, current=allowed)  # not retrieved
    got = store.retrieve(item.capability, current=allowed)
    got["values"]["app_key"] = "caller mutation"
    assert store.retrieve(item.capability, current=allowed)["values"]["app_key"] == "first"
    second = store.publish_secret(slot, {"app_key": "second"})
    store.acknowledge(item.capability, first, current=allowed)
    store.acknowledge(item.capability, first, current=allowed)
    store.secret_saved(slot, first)
    assert store.retrieve(item.capability, current=allowed)["values"]["app_key"] == "second"
    store.acknowledge(item.capability, second, current=allowed)
    with pytest.raises(SetupUnavailable):
        store.retrieve(item.capability, current=allowed)
    assert store.claim(item.capability, request(), target=object(), current=allowed) is None


def test_saved_credentials_report_status_without_retaining_the_value():
    store = SetupResultStore()
    item, slot = started(store)
    generation = store.publish_secret(slot, {"app_key": "saved-credential"})
    store.secret_saved(slot, generation)
    store.complete(slot, SetupResult(Outcome.SUCCEEDED, RoomText("Setup complete."),
                                    config_save=StageState.REPORTED_COMPLETE))
    status = store.status(item.capability, current=allowed)
    assert status["secret"]["state"] == "reported_saved"
    assert "saved-credential" not in json.dumps(status)
    with pytest.raises(SetupUnavailable):
        store.retrieve(item.capability, current=allowed)


def test_progress_percent_is_not_terminal_or_monotonic_and_snapshots_do_not_alias():
    store = SetupResultStore()
    item, slot = started(store)
    store.progress(slot, "setup.check", "Authenticated", 100)
    store.progress(slot, "setup.save", "Saving, reconnecting", 95)
    store.publish_secret(slot, {"app_key": "private-marker"})
    status = store.status(item.capability, current=allowed)
    assert status["status"] == "running"
    assert status["progress"]["percent"] == 95
    assert status["progress"]["sequence"] == 2
    store.complete(slot, result())
    status = store.status(item.capability, current=allowed)
    assert "private-marker" not in json.dumps(status)
    status["result"]["findings"][0]["detail"] = "mutated"
    assert store.status(item.capability, current=allowed)["result"]["findings"][0]["detail"] == "save raised after write"


def test_unknown_wrong_expired_and_process_lost_capabilities_never_create_work():
    store = SetupResultStore()
    item, target = prepared(store)
    for capability in (item.run_id, "x" * 43, "\ud800" * 43, "", None):
        with pytest.raises(SetupUnavailable):
            store.claim(capability, request(), target=target, current=allowed)
    restarted = SetupResultStore()
    with pytest.raises(SetupUnavailable):
        restarted.claim(item.capability, request(), target=target, current=allowed)
    assert not restarted._slots


def test_bad_results_and_oversize_publications_refuse_without_erasing_retained_secret():
    store = SetupResultStore()
    item, slot = started(store)
    store.publish_secret(slot, {"app_key": "retained"})
    with pytest.raises(SetupCapacityError):
        store.publish_secret(slot, {"app_key": "x" * 16384})
    with pytest.raises(ValueError):
        store.progress(slot, "setup.work", "detail", float("nan"))
    with pytest.raises(TypeError):
        store.complete(slot, {"success": True})
    with pytest.raises(TypeError):
        store.complete(slot, SetupResult(Outcome.SUCCEEDED, "raw device string"))
    assert store.retrieve(item.capability, current=allowed)["values"]["app_key"] == "retained"
    store.complete(slot, result())


def test_failed_task_construction_keeps_consumed_identity():
    store = SetupResultStore()
    item, target = prepared(store)
    slot = store.claim(item.capability, request(), target=target, current=allowed)
    # The runner reports a dispatch construction failure; it does not restore
    # prepared state or represent a lost HTTP response as permission to retry.
    store.complete(slot, SetupResult(Outcome.UNVERIFIED, RoomText("Setup could not start.")))
    assert store.claim(item.capability, request(), target=target, current=allowed) is None
