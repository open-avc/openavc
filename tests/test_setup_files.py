"""Setup file uploads through the real HTTP and background-run doors."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from openavc.api import rest
from openavc.api.routes import command_files, devices
from openavc.core.file_staging import FileStaging, StagingError
from openavc.core.setup_actions import SetupActionRunner
from openavc.core.setup_files import setup_owner, take_setup_files
from openavc.drivers.python_info import python_driver_info_issues
from openavc.drivers.spec import platform_requirements

ACTION = {
    "id": "import", "kind": "setup", "availability": "always",
    "params": {"project": {
        "type": "file", "required": True, "accept": [".xml"], "max_bytes": 100,
    }},
}


class ImportDevice:
    DRIVER_INFO = {
        "id": "acme_import", "name": "Acme Import", "transport": "tcp",
        "min_platform_version": "0.40.1", "commands": {}, "actions": [ACTION],
    }

    def _set_setup_context(self, context):
        pass


@pytest.mark.parametrize("outcome", ["done", "error", "cancelled"])
async def test_uploaded_bytes_reach_setup_and_are_removed(tmp_path, outcome):
    staging = FileStaging(tmp_path / "stage")
    driver = ImportDevice()
    got_file = asyncio.Event()
    received = []

    async def run(action, params, progress):
        received.append(await params["project"].read())
        got_file.set()
        if outcome == "error":
            raise ValueError("Project rejected")
        if outcome == "cancelled":
            await asyncio.Event().wait()
        return {"message": "Imported"}

    driver.run_setup_action = run
    manager = SimpleNamespace(
        get_driver=lambda _: driver, begin_setup=AsyncMock(), end_setup=AsyncMock(),
    )
    engine = SimpleNamespace(devices=manager, file_staging=staging, broadcast_ws=AsyncMock())
    runner = engine.setup_actions = SetupActionRunner(engine)
    app = FastAPI()
    app.include_router(command_files.router, prefix="/api")
    app.include_router(devices.router, prefix="/api")
    rest.set_engine(engine)
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 5000))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
            opened = await client.post("/api/devices/lobby/actions/import/files", json={
                "param": "project", "name": "rooms.xml", "size": 8,
            })
            assert opened.status_code == 200, opened.text
            upload_id = opened.json()["upload"]["id"]
            sent = await client.put(f"/api/command-files/{upload_id}?offset=0", content=b"<rooms/>")
            assert sent.status_code == 200
            started = await client.post("/api/devices/lobby/actions/import", json={
                "files": {"project": upload_id},
            })
            assert started.status_code == 200, started.text
            await asyncio.wait_for(got_file.wait(), 2)
            tasks = list(runner._tasks)
            if outcome == "cancelled":
                for task in tasks:
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await asyncio.sleep(0)
        assert received == [b"<rooms/>"]
        assert staging.staged_paths() == []
        assert not runner.is_running("lobby")
        manager.end_setup.assert_awaited_once_with("lobby")
    finally:
        remaining = list(runner._tasks)
        for task in remaining:
            task.cancel()
        await asyncio.gather(*remaining, return_exceptions=True)
        rest.set_engine(None)


@pytest.mark.parametrize("wrong", ["device", "action", "param", "command", "unfinished"])
async def test_a_file_is_bound_to_one_setup_action(tmp_path, wrong):
    staging = FileStaging(tmp_path / "stage")
    upload = staging.begin(
        owner="device:lobby" if wrong == "command" else setup_owner("elsewhere" if wrong == "device" else "lobby"),
        command="other" if wrong == "action" else "import",
        param="other" if wrong == "param" else "project",
        pdef=ACTION["params"]["project"], name="x.xml", size=1,
    )
    if wrong != "unfinished":
        await staging.write(upload.id, 0, b"x")
    with pytest.raises(StagingError):
        take_setup_files(staging, "lobby", ACTION, {}, {"project": upload.id})
    assert not upload.in_use
    staging.discard(upload.id)


def test_file_required_and_path_strings_never_reach_handler(tmp_path):
    staging = FileStaging(tmp_path / "stage")
    for params, files in [({}, {}), ({"project": "/etc/passwd"}, {}), ({}, {"unknown": "id"})]:
        with pytest.raises(StagingError):
            take_setup_files(staging, "lobby", ACTION, params, files)


def test_setup_files_have_their_own_compatibility_floor():
    assert python_driver_info_issues(ImportDevice.DRIVER_INFO) == []
    floors = dict(platform_requirements(ImportDevice.DRIVER_INFO))
    assert floors["actions[0].params.project.type: file"] == "0.40.1"
    old = {**ImportDevice.DRIVER_INFO, "min_platform_version": "0.40.0"}
    assert any("0.40.1" in issue for issue in python_driver_info_issues(old))


async def test_partial_claim_is_returned_for_retry(tmp_path):
    staging = FileStaging(tmp_path / "stage")
    pdef = ACTION["params"]["project"]
    upload = staging.begin(owner=setup_owner("lobby"), command="import", param="first",
                           pdef=pdef, name="x.xml", size=1)
    await staging.write(upload.id, 0, b"x")
    action = {**ACTION, "params": {"first": pdef, "second": pdef}}
    with pytest.raises(StagingError, match="second"):
        take_setup_files(staging, "lobby", action, {}, {"first": upload.id})
    assert not upload.in_use
    assert await staging.take(upload.id, owner=setup_owner("lobby"), command="import", param="first").read() == b"x"
    staging.discard(upload.id)


async def test_cancel_before_handler_starts_releases_upload_and_active_slot(tmp_path):
    staging = FileStaging(tmp_path / "stage")
    upload = staging.begin(owner=setup_owner("lobby"), command="import", param="project",
                           pdef=ACTION["params"]["project"], name="x.xml", size=1)
    await staging.write(upload.id, 0, b"x")
    driver = ImportDevice()
    driver.run_setup_action = AsyncMock()
    engine = SimpleNamespace(devices=SimpleNamespace(get_driver=lambda _: driver), file_staging=staging)
    runner = SetupActionRunner(engine)
    await runner.start("lobby", ACTION, {}, {"project": upload.id})
    tasks = list(runner._tasks)
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    assert not runner.is_running("lobby")
    assert staging.staged_paths() == []
    driver.run_setup_action.assert_not_awaited()
