"""Actual loaded legacy handler through prepare, retained ingress and context.

The device is invented; its independent effect list and project sink let these
checks distinguish execution, saving and recovery. No network equipment is used.
"""
import asyncio
import copy
import hashlib
import json
import sys
from types import SimpleNamespace

import pytest

from openavc.core.setup_actions import SetupContractRequired
from openavc.core.setup_results import SetupConflict, SetupUnavailable
from openavc.drivers import actions, driver_loader, registry
from openavc.drivers.setup_compatibility import SetupCompatibility, declaration_digest
from tests.test_setup_handshake import allowed, drain, env as env


SOURCE = '''from openavc.drivers.base import BaseDriver
class AcmeLegacy(BaseDriver):
    DRIVER_INFO = {
        "id": "acme_legacy", "name": "Acme Legacy", "version": "1.0.0",
        "web_ui": "https://example.invalid",
        "commands": {}, "state_variables": {},
        "actions": [{"id": "pair", "kind": "setup", "params": {
            "save": {"type": "boolean"}, "different": {"type": "boolean"},
            "malformed": {"type": "boolean"}}}],
    }
    async def connect(self):
        self.set_state("connected", True)
        self.effects.append("reconnected")
    async def disconnect(self):
        self.set_state("connected", False)
    async def send_command(self, command, params=None):
        raise AssertionError("No control commands")
    async def run_setup_action(self, action, params, progress):
        self.effects.append("paired")
        await progress("Pairing accepted", 100)
        if params.get("save"):
            await progress("Saving", 95)
            await self.request_config_update({"app_key": "invented-key-a"})
            await self.request_reconnect()
        key = "invented-key-b" if params.get("different") else "invented-key-a"
        return {"paired": "yes" if params.get("malformed") else True,
                "app_key": key, "count": 0, "password": "operator-not-a-result"}
'''


@pytest.fixture
async def legacy(env, tmp_path, monkeypatch):
    runner, engine, _ = env
    path = tmp_path / "acme_legacy.py"
    path.write_text(SOURCE, encoding="utf-8")
    cls = driver_loader.load_python_driver_file(path)
    assert cls is not None
    # Isolate registration while still using the registry's public writer.
    monkeypatch.setattr(registry, "_DRIVER_REGISTRY", dict(registry._DRIVER_REGISTRY))
    registry.register_driver(cls)
    dm = engine.devices
    driver = cls("acme", {}, dm.state, dm.events)
    driver.effects = []
    driver.set_state("connected", True)
    dm._devices["acme"] = driver
    dm._device_configs["acme"]["driver"] = "acme_legacy"
    engine.project.devices[0].driver = "acme_legacy"
    raw = {
        "schema": "setup-compatibility/v1", "driver_id": "acme_legacy",
        "driver_version": "1.0.0", "action_id": "pair",
        "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "declaration_sha256": declaration_digest(cls.DRIVER_INFO),
        "action_sha256": declaration_digest(cls.DRIVER_INFO["actions"][0]),
        "profile": "pairing/v1", "roles": {"paired": "paired"},
        "generated_secret": "app_key", "diagnostics": [
            {"key": "count", "label": "Checks", "type": "integer", "required": True, "nullable": False}],
    }
    selected = SetupCompatibility.parse(raw)
    monkeypatch.setattr(actions, "bundled_descriptors", lambda: (selected,))
    yield SimpleNamespace(runner=runner, engine=engine, driver=driver, cls=cls,
                          path=path, raw=raw, selected=selected)
    await drain(runner)
    sys.modules.pop(cls.__module__, None)


def prepare(rig, **params):
    return rig.runner.prepare("acme", "pair", params, original_grant=allowed, current=allowed)


async def start(rig, prepared, **params):
    await rig.runner.start("acme", {"id": "pair"}, params,
                           capability=prepared["capability"], current=allowed)
    await drain(rig.runner)
    return rig.runner.results.status(prepared["capability"], current=allowed)


@pytest.mark.parametrize("save,failure,different", [
    (False, None, False), (True, None, False), (True, "save", False),
    (True, "reconnect", False), (True, None, True),
])
async def test_loaded_pairing_retains_only_unsaved_generations(legacy, save, failure, different, caplog):
    rig = legacy
    saved = rig.engine.apply_project

    async def project_sink(project):
        rig.driver.effects.append("saved")
        # Observe protected capture before the first awaited save can fail.
        assert rig.runner.results.retrieve(prepared["capability"], current=allowed)["values"] == {
            "app_key": "invented-key-a"}
        await saved(project)
        if failure == "save":
            raise OSError("invented-key-a must not enter diagnostics")

    async def reconnect_failure(device_id):
        rig.driver.effects.append("reconnect-attempt")
        raise OSError("invented-key-a must not enter diagnostics")

    rig.engine.apply_project = project_sink
    if failure == "reconnect":
        rig.engine.devices.reconnect_in_place = reconnect_failure
    params = {"save": save, "different": different}
    prepared = prepare(rig, **params)
    assert rig.driver.effects == []
    frames = []
    stop = rig.runner.observe(prepared["run_id"], lambda: frames.append(
        rig.runner.results.status(prepared["capability"], current=allowed)))
    status = await start(rig, prepared, **params)
    stop()
    expected = ["paired"] + (["saved"] if save else [])
    if save and failure != "save":
        expected += ["reconnect-attempt" if failure else "reconnected"]
    assert rig.driver.effects == expected
    assert status["status"] == "terminal"
    assert status["result"]["outcome"] == ("unverified" if failure else "succeeded")
    assert status["result"]["config_save"] == ("unknown" if failure == "save" else
                                               "reported_complete" if save else "not_requested")
    assert status["result"]["reconnect"] == ("failed" if failure == "reconnect" else
                                             "reported_complete" if save and not failure else "not_requested")
    if not save or failure == "save" or different:
        assert rig.runner.results.retrieve(prepared["capability"], current=allowed) == {
            "generation": 2 if different and save else 1,
            "values": {"app_key": "invented-key-b" if different else "invented-key-a"}}
    else:
        with pytest.raises(SetupUnavailable):
            rig.runner.results.retrieve(prepared["capability"], current=allowed)
    assert "invented-key-" not in json.dumps(frames) + caplog.text
    assert "operator-not-a-result" not in json.dumps(frames)
    assert "acme" not in rig.engine.devices._intentional_disconnect
    assert await start(rig, prepared, **params) == status
    assert rig.driver.effects == expected  # response retry never pairs again


async def test_returned_key_survives_malformed_diagnostics(legacy):
    prepared = prepare(legacy, malformed=True)
    status = await start(legacy, prepared, malformed=True)
    assert status["result"]["outcome"] == "unverified"
    assert legacy.driver.effects == ["paired"]
    assert legacy.runner.results.retrieve(prepared["capability"], current=allowed)["values"] == {
        "app_key": "invented-key-a"}


@pytest.mark.parametrize("field", ["driver_id", "driver_version", "action_id", "source_sha256",
                                  "declaration_sha256", "action_sha256"])
async def test_every_exact_identity_is_required_before_effects(legacy, monkeypatch, field):
    raw = copy.deepcopy(legacy.raw)
    raw[field] = "0" * 64 if field.endswith("sha256") else "other"
    selected = SetupCompatibility.parse(raw)
    monkeypatch.setattr(actions, "bundled_descriptors", lambda: (selected,))
    with pytest.raises(SetupContractRequired):
        prepare(legacy)
    assert legacy.driver.effects == [] and not legacy.runner._tasks


async def test_metadata_alone_and_unloaded_same_name_class_do_not_select_legacy(legacy):
    info_only = actions.resolve_device_actions(legacy.cls.DRIVER_INFO)
    assert "result_contract" not in info_only[0]
    fake = type("MetadataCopy", (legacy.cls,), {"DRIVER_INFO": legacy.cls.DRIVER_INFO})
    registry.register_driver(fake)
    legacy.engine.devices._devices["acme"] = fake("acme", {}, legacy.engine.devices.state, legacy.engine.devices.events)
    with pytest.raises(SetupContractRequired):
        prepare(legacy)
    assert legacy.driver.effects == []


@pytest.mark.parametrize("error", [ValueError, TypeError, OSError])
async def test_unreadable_compatibility_data_refuses_setup_without_hiding_other_actions(legacy, monkeypatch, error):
    attempts = []
    def unavailable():
        attempts.append(True)
        raise error("invalid local resource")
    monkeypatch.setattr(actions, "bundled_descriptors", unavailable)
    resolved = actions.resolve_device_actions(legacy.cls.DRIVER_INFO, driver=legacy.driver)
    assert any(action["kind"] == "link" for action in resolved)
    assert not any(isinstance(action, actions.ResolvedAction) for action in resolved)
    with pytest.raises(SetupContractRequired):
        prepare(legacy)
    assert attempts == [True, True] and legacy.driver.effects == []


async def test_prepare_retains_adapter_and_loaded_bytes_without_later_lookup(legacy, monkeypatch):
    prepared = prepare(legacy)
    legacy.path.write_text("invalid replacement source", encoding="utf-8")

    def forbidden_lookup():
        raise AssertionError("A prepared run must not select a new adapter")

    monkeypatch.setattr(actions, "bundled_descriptors", forbidden_lookup)
    status = await start(legacy, prepared)
    assert status["result"]["outcome"] == "succeeded"
    assert legacy.driver.effects == ["paired"]


@pytest.mark.parametrize("change", ["registration", "code", "declaration", "config"])
async def test_prepared_source_changes_refuse_without_effects(legacy, change):
    prepared = prepare(legacy)
    if change == "registration":
        registry.unregister_driver("acme_legacy")
    elif change == "code":
        async def changed(self, action, params, progress):
            self.effects.append("wrong-code")
        legacy.cls.run_setup_action.__code__ = changed.__code__
    elif change == "declaration":
        legacy.cls.DRIVER_INFO["actions"][0]["label"] = "Changed action"
    else:
        legacy.driver.config["host"] = "changed"
    with pytest.raises(SetupConflict):
        await start(legacy, prepared)
    assert legacy.driver.effects == [] and not legacy.runner._tasks


async def test_registration_is_rechecked_after_reconnect_admission_wait(legacy):
    entered, release = asyncio.Event(), asyncio.Event()
    original = legacy.engine.devices.begin_setup

    async def held_begin(device_id):
        await original(device_id)
        entered.set()
        await release.wait()

    legacy.engine.devices.begin_setup = held_begin
    prepared = prepare(legacy)
    await legacy.runner.start("acme", {"id": "pair"}, {}, capability=prepared["capability"], current=allowed)
    try:
        await asyncio.wait_for(entered.wait(), 2)
        registry.unregister_driver("acme_legacy")
    finally:
        release.set()
        await drain(legacy.runner)
    assert legacy.driver.effects == []
    assert "acme" not in legacy.engine.devices._intentional_disconnect
    assert legacy.runner.results.status(prepared["capability"], current=allowed)["status"] == "terminal"
