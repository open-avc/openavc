"""Actual runner/context/driver interaction with an invented in-memory device.

These tests do not stand in for HTTP final-send or rendered wizard acceptance.
The project save sink is explicit; no storage durability claim is made here.
"""
from __future__ import annotations

import asyncio
import copy
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import httpx
from fastapi import FastAPI
from starlette.responses import JSONResponse

from openavc.api import auth, session_tokens
from openavc.api.host_policy import HostPolicyMiddleware, configure_policy
from openavc.api.routes import setup_actions as setup_routes
from openavc.core.host_policy import HostPolicy, PolicySnapshot

from openavc.core.device_manager import DeviceManager
from openavc.core.event_bus import EventBus
from openavc.core.outbound_messages import RoomText
from openavc.core.project_loader import DeviceConfig, ProjectConfig, ProjectMeta
from openavc.core.setup_actions import SetupActionRunner, SetupContractRequired, SetupHandshakeRequired
from openavc.core.setup_results import (
    Outcome, SetupConflict, SetupField, SetupFinding, SetupProgress, SetupResult, SetupUnavailable,
)
from openavc.core.state_store import StateStore
from openavc.core.ws_hub import WSHub
from openavc.drivers.base import BaseDriver


def allowed():
    return True


class AcmePairingDriver(BaseDriver):
    DRIVER_INFO = {
        "id": "acme_pair", "version": "1.0.0", "name": "Acme Pairing Device",
        "state_variables": {}, "commands": {},
        "actions": [{"id": "pair", "kind": "setup", "result_contract": "setup/v1",
                     "params": {"save": {"type": "boolean"}}}],
    }

    async def connect(self):
        self._connected = True
        self.set_state("connected", True)
        self.reconnect_count += 1

    async def disconnect(self):
        self._connected = False
        self.set_state("connected", False)

    async def send_command(self, command, params=None):
        raise AssertionError("No device control command belongs to this fixture")

    async def run_setup_action(self, action_id, params, progress):
        self.pair_count += 1
        self.register_setup_secret({"app_key": "invented-generated-key"})
        await progress(SetupProgress("pair.created", "Pairing accepted", 100))
        if params.get("save"):
            await progress(SetupProgress("pair.saving", "Saving", 95))
            await self.request_config_update({"app_key": "invented-generated-key"})
            await self.request_reconnect()
        return SetupResult(Outcome.SUCCEEDED, RoomText("Pairing complete."), (
            SetupFinding("pair.details", Outcome.SUCCEEDED, "Device report", (
                SetupField("Checks", 0), SetupField("Authenticated", False), SetupField("Firmware", None),
            )),
        ))


@pytest.fixture
async def env():
    state, events = StateStore(), EventBus()
    state.set_event_bus(events)
    dm = DeviceManager(state, events)
    driver = AcmePairingDriver("acme", {}, state, events)
    driver.pair_count = 0
    driver.reconnect_count = 0
    driver.set_state("connected", True)  # no unrelated reconnect worker
    dm._devices["acme"] = driver
    dm._device_configs["acme"] = {"id": "acme", "driver": "acme_pair", "config": {}, "enabled": True}
    engine = SimpleNamespace(devices=dm, _project_revision=0, project=ProjectConfig(
        project=ProjectMeta(id="fixture", name="Fixture"),
        devices=[DeviceConfig(id="acme", driver="acme_pair", name="Acme")]))

    async def save(project):
        engine.project = project
        engine._project_revision += 1
        return engine._project_revision

    engine.apply_project = save
    runner = SetupActionRunner(engine)
    yield runner, engine, driver
    if runner._tasks:
        await asyncio.gather(*tuple(runner._tasks), return_exceptions=True)
    await dm._cancel_reconnect("acme")


async def drain(runner):
    if runner._tasks:
        await asyncio.gather(*tuple(runner._tasks), return_exceptions=True)


@pytest.mark.parametrize("save,fails", [(False, False), (True, False), (True, True)])
async def test_pairing_secret_survives_no_save_or_failed_save_and_never_enters_progress(env, save, fails, caplog):
    runner, engine, driver = env
    if fails:
        async def failed_after_write(project):
            engine.project = project  # failure cannot prove unsaved
            raise RuntimeError("response contains invented-generated-key")
        engine.apply_project = failed_after_write
    params = {"save": save}
    prepared = runner.prepare("acme", "pair", params, original_grant=allowed, current=allowed)
    assert driver.pair_count == 0 and not runner._tasks
    messages = []
    stop = runner.observe(prepared["run_id"], lambda: messages.append(
        runner.results.status(prepared["capability"], current=allowed)))
    await runner.start("acme", {"id": "pair"}, params, capability=prepared["capability"], current=allowed)
    await drain(runner)
    stop()
    status = runner.results.status(prepared["capability"], current=allowed)
    assert driver.pair_count == 1
    assert status["status"] == "terminal"
    assert status["result"]["outcome"] == ("unverified" if fails else "succeeded")
    assert status["result"]["config_save"] == ("unknown" if fails else "reported_complete" if save else "not_requested")
    assert driver.reconnect_count == (1 if save and not fails else 0)
    assert "invented-generated-key" not in str(messages) + caplog.text
    if not save or fails:
        assert runner.results.retrieve(prepared["capability"], current=allowed)["values"] == {"app_key": "invented-generated-key"}
    else:
        with pytest.raises(SetupUnavailable):
            runner.results.retrieve(prepared["capability"], current=allowed)
    assert "acme" not in engine.devices._intentional_disconnect
    # Saving changed the project/config binding. A retry still reads the same
    # terminal run and cannot pair again or select the new live configuration.
    retry = await runner.start("acme", {"id": "pair"}, params, capability=prepared["capability"], current=allowed)
    assert retry["run_id"] == prepared["run_id"] and driver.pair_count == 1


async def test_legacy_one_post_and_unqualified_contract_refuse_before_effects(env):
    runner, engine, driver = env
    with pytest.raises(SetupHandshakeRequired):
        await runner.start("acme", {"id": "pair"}, {})
    driver.DRIVER_INFO = {**driver.DRIVER_INFO, "actions": [{"id": "pair", "kind": "setup"}]}
    with pytest.raises(SetupContractRequired):
        runner.prepare("acme", "pair", {}, original_grant=allowed, current=allowed)
    assert driver.pair_count == 0 and not runner._tasks


async def test_permission_is_rechecked_after_existing_reconnect_admission_wait(env):
    runner, engine, driver = env
    valid = [True]
    entered, release = asyncio.Event(), asyncio.Event()
    original_begin = engine.devices.begin_setup

    async def held_begin(device_id):
        await original_begin(device_id)
        entered.set()
        await release.wait()

    engine.devices.begin_setup = held_begin
    prepared = runner.prepare("acme", "pair", {}, original_grant=lambda: valid[0], current=allowed)
    await runner.start("acme", {"id": "pair"}, {}, capability=prepared["capability"], current=allowed)
    await entered.wait()
    valid[0] = False
    release.set()
    await drain(runner)
    assert driver.pair_count == 0
    assert "acme" not in engine.devices._intentional_disconnect
    assert not runner.is_running("acme")


async def test_request_definition_and_live_config_changes_refuse_prepared_start(env):
    runner, engine, driver = env
    prepared = runner.prepare("acme", "pair", {"save": False}, original_grant=allowed, current=allowed)
    with pytest.raises(SetupConflict):
        await runner.start("acme", {"id": "pair"}, {"save": True}, capability=prepared["capability"], current=allowed)
    driver.config["host"] = "changed"
    with pytest.raises(SetupConflict):
        await runner.start("acme", {"id": "pair"}, {"save": False}, capability=prepared["capability"], current=allowed)
    assert driver.pair_count == 0


async def test_dispatch_failure_or_pre_start_cancellation_cannot_release_claim_for_replay(env):
    runner, engine, driver = env
    prepared = runner.prepare("acme", "pair", {}, original_grant=allowed, current=allowed)
    with patch("openavc.core.setup_actions.asyncio.create_task", side_effect=RuntimeError("fixture task failure")):
        with pytest.raises(RuntimeError, match="fixture task failure"):
            await runner.start("acme", {"id": "pair"}, {}, capability=prepared["capability"], current=allowed)
    assert (await runner.start("acme", {"id": "pair"}, {}, capability=prepared["capability"], current=allowed))["status"] == "terminal"
    another = runner.prepare("acme", "pair", {}, original_grant=allowed, current=allowed)
    await runner.start("acme", {"id": "pair"}, {}, capability=another["capability"], current=allowed)
    tasks = tuple(runner._tasks)
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    assert runner.results.status(another["capability"], current=allowed)["status"] == "terminal"
    assert driver.pair_count == 0 and not runner.is_running("acme")


class MutableHost(HostPolicy):
    current = PolicySnapshot(1, True, True)

    def snapshot(self):
        return self.current


@pytest.fixture
async def api_env(env, isolated_auth_config, monkeypatch):
    runner, engine, driver = env
    engine.setup_actions = runner
    engine.ws = WSHub(engine.devices.state)
    cfg = isolated_auth_config
    monkeypatch.setattr(cfg, "_data", copy.deepcopy(cfg._data))
    monkeypatch.setattr(cfg, "_file_data", copy.deepcopy(cfg._file_data))
    cfg.set("auth", "programmer_username", "engineer")
    cfg.set("auth", "programmer_password", "disposable-password")
    cfg.set("auth", "api_key", "")
    tokens = session_tokens.SessionTokenStore()
    monkeypatch.setattr(session_tokens, "store", tokens)
    token, _ = tokens.issue(auth.credential_fingerprint())
    monkeypatch.setattr(setup_routes, "_get_engine", lambda: engine)
    app, host = FastAPI(), MutableHost()
    configure_policy(app, host)
    app.add_middleware(HostPolicyMiddleware)
    app.include_router(setup_routes.router, prefix="/api")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://fixture",
                                headers={"Authorization": "Bearer " + token}) as client:
        yield SimpleNamespace(client=client, runner=runner, driver=driver, engine=engine,
                              host=host, tokens=tokens, token=token)
    for connection in tuple(engine.ws._clients):
        engine.ws.remove_client(connection)


async def api_pair(rig):
    prepared = await rig.client.post("/api/devices/acme/actions/pair/prepare", json={"params": {"save": False}})
    assert prepared.status_code == 200
    assert prepared.headers["Cache-Control"] == "no-store"
    assert rig.driver.pair_count == 0 and not rig.host._admissions
    data = prepared.json()
    start = await rig.client.post("/api/devices/acme/actions/pair/start",
                                  json={"capability": data["capability"], "params": {"save": False}})
    assert start.status_code == 200
    await drain(rig.runner)
    return data


@pytest.fixture
async def transfer_env(api_env, monkeypatch):
    from openavc.api.routes import devices
    rig = api_env
    monkeypatch.setattr(devices, "_get_engine", lambda: rig.engine)
    rig.client._transport.app.include_router(devices.router, prefix="/api")
    rig.client.headers.update({"Origin": "http://fixture", "Referer": "http://fixture/programmer/",
                               "X-OpenAVC-Setup-Transfer": "1"})
    return rig


async def old_setup(rig, **kwargs):
    return await rig.client.post("/api/devices/acme/actions/pair", json={"params": {"save": False}}, **kwargs)


async def test_old_browser_refresh_recovers_same_inputs_without_pairing_then_starts_once(transfer_env):
    rig = transfer_env
    first = await old_setup(rig)
    assert first.status_code == 409 and first.json()["code"] == "setup_input_retained"
    assert len(first.headers.get_list("set-cookie")) == 1
    cookie = first.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie and "Path=/api" in cookie
    assert "capability" not in first.text and "save" not in first.text
    assert (await old_setup(rig)).status_code == 409
    read = await rig.client.post("/api/setup-actions/transfer", json={})
    assert read.status_code == 200 and read.headers["cache-control"] == "no-store"
    data = read.json()["pending"]
    assert data["params"] == {"save": False} and data["device_id"] == "acme"
    assert set(data) == {"params", "device_id", "action", "run_id", "capability", "status"}
    assert (await rig.client.post("/api/setup-actions/transfer", json={})).json() == read.json()
    assert rig.driver.pair_count == 0 and len(rig.runner.results._slots) == 1
    body = {"capability": data["capability"], "params": data["params"]}
    for _ in range(2):
        assert (await rig.client.post("/api/devices/acme/actions/pair/start", json=body)).status_code == 200
    await drain(rig.runner)
    assert rig.driver.pair_count == 1
    result = await rig.client.post("/api/setup-actions/retrieve", json={"capability": data["capability"]})
    assert result.json()["values"] == {"app_key": "invented-generated-key"}
    stale = await rig.client.post("/api/setup-actions/transfer", json={})
    assert stale.json() == {"pending": None, "cleared": True}
    assert "Max-Age=0" in stale.headers["set-cookie"]


@pytest.mark.parametrize("headers", [
    {"Origin": ""}, {"Origin": "http://elsewhere"}, {"Origin": "null"},
    {"Referer": "http://fixture/panel/"}, {"Referer": "http://elsewhere/programmer/"},
    {"Referer": "http://fixture/../programmer/"},
])
async def test_old_setup_refuses_nonbrowser_or_cross_origin_before_retaining_input(transfer_env, headers):
    rig = transfer_env
    response = await old_setup(rig, headers=headers)
    assert response.status_code == 409 and "set-cookie" not in response.headers
    assert not rig.runner.results._slots and rig.driver.pair_count == 0


async def test_transfer_retries_cannot_replace_inputs_and_late_discard_cannot_clear_a_new_draft(transfer_env):
    rig = transfer_env
    await old_setup(rig)
    data = (await rig.client.post("/api/setup-actions/transfer", json={})).json()["pending"]
    refused = await rig.client.post("/api/devices/acme/actions/pair", json={"params": {"save": True}})
    assert refused.status_code == 409 and "not retained" in refused.text
    assert (await rig.client.post("/api/setup-actions/transfer", json={})).json()["pending"] == data
    discarded = await rig.client.post("/api/setup-actions/discard-transfer", json={"run_id": data["run_id"]})
    assert discarded.status_code == 200
    assert (await old_setup(rig)).status_code == 409
    replacement = (await rig.client.post("/api/setup-actions/transfer", json={})).json()["pending"]
    late = await rig.client.post("/api/setup-actions/discard-transfer", json={"run_id": data["run_id"]})
    assert late.status_code == 410 and "set-cookie" not in late.headers
    assert replacement["run_id"] != data["run_id"]
    assert (await rig.client.post("/api/setup-actions/transfer", json={})).json()["pending"] == replacement
    assert rig.driver.pair_count == 0


@pytest.mark.parametrize("change", ["token", "host", "source"])
async def test_transfer_cannot_rebind_an_original_grant_or_changed_source(transfer_env, change):
    rig = transfer_env
    await old_setup(rig)
    if change == "token":
        rig.tokens.revoke(rig.token)
        replacement, _ = rig.tokens.issue(auth.credential_fingerprint())
        rig.client.headers["Authorization"] = "Bearer " + replacement
    elif change == "host":
        rig.host.current = PolicySnapshot(2, True, True)
    else:
        rig.driver.config["host"] = "changed-target"
    response = await rig.client.post("/api/setup-actions/transfer", json={})
    assert response.status_code == 409 if change == "source" else response.json()["pending"] is None
    assert "capability" not in response.text and rig.driver.pair_count == 0


async def test_duplicate_cookie_refusal_and_stale_cleanup_are_bounded(transfer_env):
    rig = transfer_env
    await old_setup(rig)
    name, value = next(iter(rig.client.cookies.items()))
    response = await rig.client.post("/api/setup-actions/transfer", json={},
        headers={"Cookie": f"{name}={value}; {name}={value}"})
    assert response.status_code == 409 and "capability" not in response.text
    assert rig.driver.pair_count == 0 and len(rig.runner.results._slots) == 1
    data = (await rig.client.post("/api/setup-actions/transfer", json={})).json()["pending"]
    await rig.client.post("/api/setup-actions/discard-transfer", json={"run_id": data["run_id"]})
    stale = "; ".join(f"openavc_setup_{i:032x}={'a' * 43}" for i in range(8))
    full = await old_setup(rig, headers={"Cookie": stale})
    assert full.status_code == 409 and "not retained" in full.text
    assert len(full.headers.get_list("set-cookie")) == 1 and not rig.runner.results._slots


async def test_discard_while_transfer_response_waits_blocks_capability_and_inputs(transfer_env, monkeypatch):
    rig = transfer_env
    await old_setup(rig)
    data = (await rig.client.post("/api/setup-actions/transfer", json={})).json()["pending"]
    entered, release = asyncio.Event(), asyncio.Event()
    original = JSONResponse.__call__

    async def held(response, scope, receive, send):
        if scope["path"] == "/api/setup-actions/transfer" and response.status_code == 200:
            entered.set()
            await release.wait()
        await original(response, scope, receive, send)

    monkeypatch.setattr(JSONResponse, "__call__", held)
    pending = asyncio.create_task(rig.client.post("/api/setup-actions/transfer", json={}))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        assert (await rig.client.post("/api/setup-actions/discard-transfer", json={"run_id": data["run_id"]})).status_code == 200
        release.set()
        response = await asyncio.wait_for(pending, 2)
        assert response.status_code == 410 and data["capability"] not in response.text
        assert '"save"' not in response.text and rig.driver.pair_count == 0
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)


async def test_tunnel_browser_path_and_secure_cookie_do_not_follow_internal_loopback_host(transfer_env):
    rig = transfer_env
    rig.client._transport.client = ("127.0.0.1", 12345)
    tunnel = {"Origin": "https://remote.example", "Referer": "https://remote.example/tunnel/fixture/programmer/",
              "X-OpenAVC-Tunneled": "1"}
    first = await old_setup(rig, headers=tunnel)
    assert first.status_code == 409 and first.json()["code"] == "setup_input_retained"
    cookie = first.headers["set-cookie"]
    assert "Path=/tunnel/fixture/api" in cookie and "Secure" in cookie
    forwarded = {**tunnel, "Cookie": cookie.split(";", 1)[0]}
    response = await rig.client.post("/api/setup-actions/transfer", json={}, headers=forwarded)
    assert response.status_code == 200 and response.json()["pending"]["params"] == {"save": False}
    wrong_path = {**forwarded, "Referer": "https://remote.example/tunnel/other/programmer/"}
    wrong = await rig.client.post("/api/setup-actions/transfer", json={}, headers=wrong_path)
    assert wrong.json()["pending"] is None and "capability" not in wrong.text
    assert rig.driver.pair_count == 0


async def test_transfer_requires_current_auth_and_explicit_client_header(transfer_env):
    rig = transfer_env
    await old_setup(rig)
    for headers, code in [({"Authorization": ""}, 401), ({"X-OpenAVC-Setup-Transfer": ""}, 400)]:
        response = await rig.client.post("/api/setup-actions/transfer", json={}, headers=headers)
        assert response.status_code == code and "capability" not in response.text
    bad = await rig.client.post("/api/setup-actions/discard-transfer", json={"run_id": "private-invalid-marker"})
    assert bad.status_code == 422 and "private-invalid-marker" not in bad.text
    assert rig.driver.pair_count == 0


async def test_real_http_prepare_completion_keeps_original_authority_and_repeat_retrieval(api_env):
    rig = api_env
    data = await api_pair(rig)
    body = {"capability": data["capability"]}
    status = await rig.client.post("/api/setup-actions/status", json=body)
    assert status.status_code == 200 and "invented-generated-key" not in status.text
    first = await rig.client.post("/api/setup-actions/retrieve", json=body)
    second = await rig.client.post("/api/setup-actions/retrieve", json=body)
    assert first.json() == second.json() == {"generation": 1, "values": {"app_key": "invented-generated-key"}}
    assert first.headers["Cache-Control"] == "no-store"
    ack = await rig.client.post("/api/setup-actions/acknowledge", json={**body, "generation": 1})
    assert ack.status_code == 200
    assert (await rig.client.post("/api/setup-actions/retrieve", json=body)).status_code == 410
    retry = await rig.client.post("/api/devices/acme/actions/pair/start", json={**body, "params": {"save": False}})
    assert retry.status_code == 200 and retry.json()["run_id"] == data["run_id"]
    assert rig.driver.pair_count == 1


@pytest.mark.parametrize("change", ["original_token", "host_revision"])
async def test_fresh_valid_http_grant_cannot_reclaim_revoked_original_result(api_env, change):
    rig = api_env
    data = await api_pair(rig)
    if change == "original_token":
        rig.tokens.revoke(rig.token)
        replacement, _ = rig.tokens.issue(auth.credential_fingerprint())
        rig.client.headers["Authorization"] = "Bearer " + replacement
    else:
        rig.host.current = PolicySnapshot(2, False, True)
        rig.host.current = PolicySnapshot(3, True, True)
    response = await rig.client.post("/api/setup-actions/retrieve", json={"capability": data["capability"]})
    assert response.status_code == 410
    assert "invented-generated-key" not in response.text


async def test_acknowledgement_while_http_secret_response_waits_prevents_plaintext_send(api_env, monkeypatch):
    rig = api_env
    data = await api_pair(rig)
    body = {"capability": data["capability"]}
    entered, release = asyncio.Event(), asyncio.Event()
    original = JSONResponse.__call__

    async def held_response(response, scope, receive, send):
        if scope["path"] == "/api/setup-actions/retrieve" and response.status_code == 200:
            entered.set()
            await release.wait()
        await original(response, scope, receive, send)

    monkeypatch.setattr(JSONResponse, "__call__", held_response)
    pending = asyncio.create_task(rig.client.post("/api/setup-actions/retrieve", json=body))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        ack = await rig.client.post("/api/setup-actions/acknowledge", json={**body, "generation": 1})
        assert ack.status_code == 200
        release.set()
        response = await asyncio.wait_for(pending, 2)
        assert response.status_code == 410
        assert response.json()["code"] == "result_unavailable"
        assert response.headers["cache-control"] == "no-store"
        assert "invented-generated-key" not in response.text
        # A normal result-availability race must not ask the auth interceptor
        # to clear a still-valid login and eject the operator from the wizard.
        still_signed_in = await rig.client.post("/api/setup-actions/status", json=body)
        assert still_signed_in.status_code == 200
        assert still_signed_in.json()["secret"]["state"] == "acknowledged"
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)


async def test_setup_http_refuses_missing_credentials_and_does_not_echo_bad_capability(api_env):
    rig = api_env
    unauth = await rig.client.post("/api/devices/acme/actions/pair/prepare", json={}, headers={"Authorization": ""})
    assert unauth.status_code == 401 and rig.driver.pair_count == 0
    bad = await rig.client.post("/api/setup-actions/status", json={"capability": "private-invalid-marker"})
    assert bad.status_code == 422 and "private-invalid-marker" not in bad.text
    assert bad.headers["Cache-Control"] == "no-store"


class SocketSink:
    """Admitted hub recipient; actual HTTP auth runs in api_env, not this sink."""
    def __init__(self):
        self.frames = []

    async def send_text(self, value):
        self.frames.append(json.loads(value))

    async def close(self, **kwargs):
        pass


def sockets(rig, *, held=False):
    hub = rig.engine.ws
    targets = []
    for role in ("programmer", "programmer", "panel", "preview"):
        target = SocketSink()
        hub.add_client(target, client_type=role, defer_delivery=held, authority=allowed)
        targets.append(target)
    return targets


async def test_http_subscription_routes_progress_to_the_exact_programmer_without_capability_on_wire(api_env):
    rig = api_env
    requester, other, panel, preview = sockets(rig)
    prepared = (await rig.client.post("/api/devices/acme/actions/pair/prepare", json={})).json()
    for target in (panel, preview):
        denied = await rig.client.post("/api/setup-actions/subscribe", json={
            "capability": prepared["capability"], "connection_id": rig.engine.ws.connection_id(target)})
        assert denied.status_code == 410
    subscription = {"capability": prepared["capability"], "connection_id": rig.engine.ws.connection_id(requester)}
    subscribed = await rig.client.post("/api/setup-actions/subscribe", json=subscription)
    assert subscribed.status_code == 200 and subscribed.json()["status"] == "prepared"
    await rig.client.post("/api/devices/acme/actions/pair/start", json={"capability": prepared["capability"]})
    await drain(rig.runner)
    await rig.engine.ws.flush_sends()
    assert requester.frames[-1]["status"] == "terminal"
    assert requester.frames[-1]["result"]["outcome"] == "succeeded"
    assert requester.frames[-1]["result"]["findings"][0]["fields"] == [
        {"label": "Checks", "value": 0}, {"label": "Authenticated", "value": False},
        {"label": "Firmware", "value": None},
    ]
    assert all(frame["run_id"] == prepared["run_id"] for frame in requester.frames)
    assert not other.frames and not panel.frames and not preview.frames
    assert "invented-generated-key" not in str(requester.frames)
    assert prepared["capability"] not in str(requester.frames)
    # An ordinary event/audit scope cannot stand in for a setup authorization.
    generic = rig.engine.ws.open_scope(other)
    rig.engine.ws.send(other, requester.frames[-1], scope=generic)
    await rig.engine.ws.flush_sends()
    assert other.frames == []


@pytest.mark.parametrize("change", ["unsubscribe", "replace", "revocation", "disconnect"])
async def test_queued_setup_frames_do_not_survive_subscription_or_original_authority_loss(api_env, change):
    rig = api_env
    requester, *_ = sockets(rig, held=True)
    connection_id = rig.engine.ws.connection_id(requester)
    prepared = (await rig.client.post("/api/devices/acme/actions/pair/prepare", json={})).json()
    subscription = {"capability": prepared["capability"], "connection_id": connection_id}
    assert (await rig.client.post("/api/setup-actions/subscribe", json=subscription)).status_code == 200
    await rig.client.post("/api/devices/acme/actions/pair/start", json={"capability": prepared["capability"]})
    await drain(rig.runner)
    assert requester.frames == []
    if change == "unsubscribe":
        assert (await rig.client.post("/api/setup-actions/unsubscribe", json=subscription)).status_code == 200
    elif change == "replace":
        assert (await rig.client.post("/api/setup-actions/subscribe", json=subscription)).json()["status"] == "terminal"
    elif change == "revocation":
        rig.tokens.revoke(rig.token)
    else:
        rig.engine.ws.remove_client(requester)
        rejected = await rig.client.post("/api/setup-actions/subscribe", json=subscription)
        assert rejected.status_code == 410
    rig.engine.ws.mark_client_ready(requester)
    await rig.engine.ws.flush_sends()
    assert requester.frames == []
