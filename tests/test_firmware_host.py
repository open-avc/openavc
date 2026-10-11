"""Private checks for firmware update ownership using disposable host files."""

import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from openavc.updater.host import get_firmware_host
from openavc.updater.manager import UpdateManager
from openavc.updater.platform import DeploymentType


@pytest.fixture(autouse=True)
def policy_cache(monkeypatch):
    monkeypatch.delenv("OPENAVC_FIRMWARE_HOST", raising=False)
    get_firmware_host.cache_clear()
    yield
    get_firmware_host.cache_clear()


def provision(tmp_path, monkeypatch, malformed=False):
    path = tmp_path / "firmware-host.json"
    path.write_text(
        "{"
        if malformed
        else json.dumps(
            {"schema": 1, "update_owner": "firmware", "firmware_version": "fixture-1.2"}
        )
    )
    monkeypatch.setenv("OPENAVC_FIRMWARE_HOST", str(path))
    return path


def files(directory):
    return {
        str(p.relative_to(directory)): p.read_bytes()
        for p in directory.rglob("*")
        if p.is_file()
    }


def stale_records(data):
    data.mkdir()
    records = {
        "staged-update.json": {
            "target_version": "9.9.9",
            "update_url": "https://fixture.invalid/update",
        },
        "apply-update.json": {"artifact": "unused"},
        "apply-update.retry": {"to_version": "9.9.9", "attempts": 1},
        "apply-rollback": {},
        "pending-update": {
            "from_version": "0.0.1",
            "to_version": "9.9.9",
            "attempts": 20,
        },
        "update-history.json": [
            {"status": "pending", "from_version": "0.0.1", "to_version": "9.9.9"}
        ],
    }
    for name, value in records.items():
        (data / name).write_text(json.dumps(value))
    return files(data)


@pytest.mark.parametrize(
    "kind",
    [
        "missing",
        "empty-path",
        "relative-path",
        "malformed",
        "unknown-owner",
        "boolean-schema",
        "extra-key",
        "directory",
        "dangling-symlink",
    ],
)
def test_invalid_explicit_host_never_falls_back_to_vanilla(tmp_path, monkeypatch, kind):
    path = provision(tmp_path, monkeypatch)
    if kind == "missing":
        path.unlink()
    elif kind == "empty-path":
        monkeypatch.setenv("OPENAVC_FIRMWARE_HOST", "")
    elif kind == "relative-path":
        monkeypatch.setenv("OPENAVC_FIRMWARE_HOST", "relative.json")
    elif kind == "malformed":
        path.write_text("{")
    elif kind == "directory":
        path.unlink()
        path.mkdir()
    elif kind == "dangling-symlink":
        path.unlink()
        path.symlink_to(tmp_path / "absent")
    else:
        value = json.loads(path.read_text())
        value.update(
            {"update_owner": "openavc"}
            if kind == "unknown-owner"
            else {"schema": True}
            if kind == "boolean-schema"
            else {"extra": True}
        )
        path.write_text(json.dumps(value))
    host = get_firmware_host()
    assert (
        host and host.error and not host.status("1", "linux_package")["can_self_update"]
    )
    assert "relative.json" not in host.guidance


def test_host_snapshot_does_not_change_during_a_process(tmp_path, monkeypatch):
    path = provision(tmp_path, monkeypatch)
    host = get_firmware_host()
    path.unlink()
    monkeypatch.delenv("OPENAVC_FIRMWARE_HOST")
    assert get_firmware_host() is host and host.firmware_version == "fixture-1.2"


def test_windows_without_explicit_host_is_an_ordinary_install(monkeypatch):
    import openavc.updater.host as host

    monkeypatch.setattr(host, "os", SimpleNamespace(name="nt", environ={}))
    assert get_firmware_host() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("malformed", [False, True])
async def test_ineffective_update_preferences_refuse_before_any_config_change(
    tmp_path, monkeypatch, malformed
):
    provision(tmp_path, monkeypatch, malformed)
    from fastapi import HTTPException
    from openavc.api.routes.system import update_system_config

    request = SimpleNamespace(
        json=AsyncMock(
            return_value={
                "updates": {"check_enabled": True},
                "logging": {"level": "DEBUG"},
            }
        )
    )
    config = Mock()
    with patch("openavc.system_config.get_system_config", return_value=config):
        with pytest.raises(HTTPException) as refused:
            await update_system_config(request)
    assert refused.value.status_code == 409
    assert refused.value.detail == get_firmware_host().guidance
    assert not config.mock_calls


@pytest.mark.asyncio
@pytest.mark.parametrize("deployment", list(DeploymentType))
@pytest.mark.parametrize("malformed", [False, True])
async def test_all_manager_entry_points_refuse_before_generic_update_work(
    tmp_path, monkeypatch, deployment, malformed
):
    provision(tmp_path, monkeypatch, malformed)
    data = tmp_path / "data"
    original = stale_records(data)
    with patch(
        "openavc.updater.manager.detect_deployment_type", return_value=deployment
    ):
        manager = UpdateManager(data_dir=data, project_path=data / "project.avc")
    manager._checker.check = AsyncMock(
        side_effect=AssertionError("Release feed must not be queried")
    )
    manager._download_update = AsyncMock(
        side_effect=AssertionError("Must not download")
    )
    manager._download_artifact = AsyncMock(
        side_effect=AssertionError("Must not download")
    )
    manager._restart_process = Mock(side_effect=AssertionError("Must not restart"))
    with (
        patch(
            "openavc.updater.backup.create_backup",
            side_effect=AssertionError("Must not back up"),
        ),
        patch(
            "openavc.updater.rollback.perform_rollback",
            side_effect=AssertionError("Must not roll back"),
        ),
        patch(
            "openavc.updater.rollback.can_rollback",
            side_effect=AssertionError("Must not infer rollback from old install"),
        ),
    ):
        status = manager.get_status()
        assert status["update_owner"] == "firmware" and not status["rollback_available"]
        assert not status["can_self_update"] and not status["update_check_available"]
        assert (
            status["staged_version"]
            == status["deferred_version"]
            == status["update_available"]
            == ""
        )
        assert status["host_configuration_valid"] is (not malformed)
        checked = await manager.check_for_updates()
        assert (
            not checked["update_available"]
            and checked["instructions"] == status["update_guidance"]
        )
        for reply in [
            await manager.apply_update(),
            await manager.rollback(),
            await manager.apply_cloud_update(
                "9.9.9", "https://fixture.invalid/update", "0" * 64
            ),
        ]:
            assert (
                not reply["success"]
                and reply["instructions"] == status["update_guidance"]
            )
        with pytest.raises(PermissionError):
            manager.stage_update("9.9.9", "https://fixture.invalid/update")
        assert (
            manager.get_staged_update() is None
            and manager.get_deferred_update() is None
        )
        manager.clear_staged_update()
        await manager.start_auto_check()
        await manager.apply_update_policy({"policy": "auto"})
        assert manager._auto_check_task is None and manager._maintenance_task is None
        assert manager.get_history() == []
    assert files(data) == original


@pytest.mark.asyncio
@pytest.mark.parametrize("malformed", [False, True])
async def test_actual_startup_leaves_generic_updater_state_untouched(
    tmp_path, monkeypatch, malformed
):
    provision(tmp_path, monkeypatch, malformed)
    data = tmp_path / "data"
    original = stale_records(data)
    import openavc.main as main

    app = SimpleNamespace(state=SimpleNamespace(engine_ready=False, engine_error=None))
    with (
        patch(
            "openavc.system_config.get_system_config",
            return_value=SimpleNamespace(data_dir=data),
        ),
        patch.object(main.engine, "start", new=AsyncMock()) as start,
        patch.object(main.discovery_engine, "load_driver_hints_from_registry"),
        patch.object(main.discovery_engine, "load_discovery_companions_from_dirs"),
        patch.object(main.engine.events, "on"),
        patch.object(main.engine, "get_status", return_value={}),
        patch(
            "openavc.updater.rollback.check_rollback_needed",
            side_effect=AssertionError("No generic boot rollback"),
        ),
        patch(
            "openavc.updater.rollback.confirm_startup",
            side_effect=AssertionError("No generic update confirmation"),
        ),
        patch(
            "openavc.updater.rollback.clear_stale_rollback_marker",
            side_effect=AssertionError("No marker mutation"),
        ),
        patch.object(main.os, "_exit", side_effect=AssertionError("No process exit")),
    ):
        await main._initialize_engine(app)
    assert app.state.engine_ready and app.state.engine_error is None
    start.assert_awaited_once()
    assert files(data) == original


@pytest.mark.asyncio
@pytest.mark.parametrize("has_manager", [False, True])
@pytest.mark.parametrize("auto_restart", [False, True])
async def test_optional_update_caller_reports_firmware_ownership(
    tmp_path, monkeypatch, has_manager, auto_restart
):
    provision(tmp_path, monkeypatch)
    from openavc.cloud.command_handler import CommandHandler

    manager = Mock() if has_manager else None
    events = SimpleNamespace(emit=AsyncMock())
    handler = CommandHandler(Mock(), Mock(), events, update_manager=manager)
    handler._send_result = AsyncMock()
    await handler._handle_software_update(
        {
            "target_version": "9.9.9",
            "auto_restart": auto_restart,
            "update_url": "https://fixture.invalid/update",
        },
        "fixture-request",
        "fixture-user",
    )
    handler._send_result.assert_awaited_once_with(
        "fixture-request",
        False,
        error="firmware_managed",
        result=get_firmware_host().guidance,
    )
    events.emit.assert_not_awaited()
    if manager:
        assert not manager.mock_calls


@pytest.mark.parametrize("malformed", [False, True])
def test_linux_prestart_helper_keeps_markers_without_server_environment(
    tmp_path, monkeypatch, malformed
):
    path = provision(tmp_path, monkeypatch, malformed)
    data = tmp_path / "data"
    original = stale_records(data)
    app = tmp_path / "app"
    app.mkdir()
    (app / "keep").write_text("current firmware")
    source = Path(__file__).resolve().parents[1]
    environment = dict(os.environ)
    environment.pop("OPENAVC_FIRMWARE_HOST", None)
    process = subprocess.run(
        [
            "bash",
            str(source / "installer/update-helper.sh"),
            str(data),
            str(app),
            str(path),
        ],
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert process.returncode == 0 and "firmware update settings" in process.stdout
    assert files(data) == original and files(app) == {"keep": b"current firmware"}


def test_macos_wrapper_bypasses_updates_and_still_launches_server(
    tmp_path, monkeypatch
):
    provision(tmp_path, monkeypatch)
    data = tmp_path / "data"
    original = stale_records(data)
    app = tmp_path / "fixture.app"
    server = app / "Contents/Resources/server/openavc-server"
    server.parent.mkdir(parents=True)
    server.write_text('#!/bin/sh\nprintf "firmware server launched\\n"\n')
    server.chmod(0o700)
    source = Path(__file__).resolve().parents[1]
    process = subprocess.run(
        ["bash", str(source / "installer/openavc-macos-run.sh")],
        env={**os.environ, "OPENAVC_DATA_DIR": str(data), "OPENAVC_APP": str(app)},
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert (
        process.returncode == 0 and process.stdout.strip() == "firmware server launched"
    )
    assert files(data) == original
