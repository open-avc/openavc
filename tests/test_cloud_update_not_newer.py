"""A cloud update installs only a newer version, unless the cloud marks it a rollback.

A fleet update planned while a system was behind can reach it after somebody
has updated it by hand, and a request can name the version a system already
runs. Either is answered "Already on vX; a fleet update installs only a newer
version" rather than backed up, applied and restarted. A cloud-staged update
that is no longer newer is dropped when it is read, so the Updates page stops
offering it.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openavc.cloud.command_handler import CommandHandler
from openavc.updater.manager import UpdateManager
from openavc.updater.platform import DeploymentType

pytestmark = pytest.mark.usefixtures("under_its_service")

RUNNING = "0.40.0"
URL = "https://github.com/open-avc/openavc/releases/download/v0.39.0/openavc-0.39.0.tar.gz"
ALREADY = "Already on v0.40.0; a fleet update installs only a newer version"


class _Agent:
    def __init__(self):
        self.sent = []

    async def send_message(self, msg_type, payload):
        self.sent.append((msg_type, payload))


class _Events:
    async def emit(self, event, data):
        pass


def _handler(update_manager):
    agent = _Agent()
    devices = MagicMock()
    return agent, CommandHandler(agent, devices, _Events(), update_manager=update_manager)


def _update(target, *, auto_restart=True, rollback=None, url=URL):
    payload = {
        "request_id": "req-1",
        "target_version": target,
        "update_url": url,
        "checksum_sha256": "ab" * 32,
        "auto_restart": auto_restart,
    }
    if rollback is not None:
        payload["rollback"] = rollback
    return {"type": "software_update", "payload": payload}


async def _send(handler, msg):
    with patch("openavc.updater.platform.can_self_update", return_value=True), \
         patch("openavc.updater.platform.detect_deployment_type"), \
         patch("openavc.cloud.command_handler.__version__", RUNNING):
        await handler.handle(msg)


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["0.40.0", "0.39.0", "0.40.0-rc2", "0.40.0rc2"])
async def test_a_target_that_is_not_newer_is_refused_and_nothing_is_installed(target):
    manager = AsyncMock()
    agent, handler = _handler(manager)

    await _send(handler, _update(target))

    manager.apply_cloud_update.assert_not_called()
    manager.stage_update.assert_not_called()
    [(msg_type, payload)] = agent.sent
    assert msg_type == "command_result"
    assert payload["success"] is False
    assert payload["error"] == "not_newer"
    assert payload["result"] == ALREADY


@pytest.mark.asyncio
async def test_a_target_that_is_not_newer_is_not_staged_either():
    manager = MagicMock()
    agent, handler = _handler(manager)

    await _send(handler, _update("0.39.0", auto_restart=False))

    manager.stage_update.assert_not_called()
    [(_, payload)] = agent.sent
    assert payload["success"] is False
    assert payload["result"] == ALREADY


@pytest.mark.asyncio
async def test_a_target_that_names_no_version_is_refused_in_its_own_words():
    manager = AsyncMock()
    agent, handler = _handler(manager)

    await _send(handler, _update("latest"))

    manager.apply_cloud_update.assert_not_called()
    [(_, payload)] = agent.sent
    assert payload["error"] == "not_newer"
    assert payload["result"] == (
        "The update names no release version; a fleet update installs only a newer version"
    )


@pytest.mark.asyncio
async def test_a_newer_target_is_installed():
    manager = AsyncMock()
    manager.apply_cloud_update = AsyncMock(return_value={"success": True, "message": "started"})
    _, handler = _handler(manager)

    await _send(handler, _update("0.41.0"))

    manager.apply_cloud_update.assert_called_once_with(
        "0.41.0", URL, "ab" * 32, rollback=False,
    )


@pytest.mark.asyncio
async def test_a_rollback_the_cloud_marks_installs_an_older_release():
    manager = AsyncMock()
    manager.apply_cloud_update = AsyncMock(return_value={"success": True, "message": "started"})
    _, handler = _handler(manager)

    await _send(handler, _update("0.39.0", rollback=True))

    manager.apply_cloud_update.assert_called_once_with(
        "0.39.0", URL, "ab" * 32, rollback=True,
    )


@pytest.mark.asyncio
async def test_a_staged_rollback_keeps_its_mark():
    manager = MagicMock()
    _, handler = _handler(manager)

    await _send(handler, _update("0.39.0", auto_restart=False, rollback=True))

    manager.stage_update.assert_called_once_with("0.39.0", URL, "ab" * 32, rollback=True)


# --- The updater itself ---


def _manager(tmp_path, state=None):
    mgr = UpdateManager(state_store=state, data_dir=tmp_path)
    mgr._deployment_type = DeploymentType.LINUX_PACKAGE
    return mgr


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["0.40.0", "0.39.0"])
async def test_the_updater_refuses_a_cloud_target_that_is_not_newer_before_any_backup(tmp_path, target):
    mgr = _manager(tmp_path)
    with patch("openavc.updater.manager.__version__", RUNNING), \
         patch("openavc.updater.backup.create_backup") as backup:
        result = await mgr.apply_cloud_update(target, URL, "ab" * 32)

    backup.assert_not_called()
    assert result == {"success": False, "error": "not_newer", "message": ALREADY}


@pytest.mark.asyncio
async def test_the_updater_takes_an_older_target_marked_a_rollback(tmp_path):
    mgr = _manager(tmp_path)
    with patch("openavc.updater.manager.__version__", RUNNING), \
         patch("openavc.updater.backup.create_backup", side_effect=RuntimeError("backed up")) as backup:
        result = await mgr.apply_cloud_update("0.39.0", URL, "ab" * 32, rollback=True)

    backup.assert_called_once()
    assert result["success"] is False and "backed up" in result["error"]


def _write_staged(tmp_path, target, **extra):
    (tmp_path / "staged-update.json").write_text(json.dumps({
        "target_version": target, "update_url": URL, "checksum_sha256": "ab" * 32, **extra,
    }))


@pytest.mark.parametrize("target", ["0.40.0", "0.30.0"])
def test_a_staged_update_that_is_not_newer_is_dropped_when_read(tmp_path, target):
    state = MagicMock()
    mgr = _manager(tmp_path, state)
    _write_staged(tmp_path, target)

    with patch("openavc.updater.manager.__version__", RUNNING):
        assert mgr.get_staged_update() is None

    assert not (tmp_path / "staged-update.json").exists()
    state.set.assert_any_call("system.update_staged_version", "", source="system")


def test_a_staged_rollback_is_kept_until_the_system_runs_it(tmp_path):
    mgr = _manager(tmp_path)
    _write_staged(tmp_path, "0.39.0", rollback=True)
    with patch("openavc.updater.manager.__version__", RUNNING):
        assert mgr.get_staged_update()["target_version"] == "0.39.0"
    with patch("openavc.updater.manager.__version__", "0.39.0"):
        assert mgr.get_staged_update() is None
    assert not (tmp_path / "staged-update.json").exists()


def test_a_stale_staged_update_is_not_surfaced_at_start(tmp_path):
    _write_staged(tmp_path, "0.30.0")
    state = MagicMock()
    with patch("openavc.updater.manager.__version__", RUNNING):
        mgr = UpdateManager(state_store=state, data_dir=tmp_path)
        assert mgr.get_status()["staged_version"] == ""
    assert ("system.update_staged_version", "0.30.0") not in [
        c.args for c in state.set.call_args_list
    ]


@pytest.mark.asyncio
async def test_update_does_not_install_a_stale_staged_update(tmp_path):
    state = MagicMock()
    state.get = MagicMock(return_value="")
    mgr = _manager(tmp_path, state)
    _write_staged(tmp_path, "0.30.0")
    mgr._checker = MagicMock(last_result=None)

    with patch("openavc.updater.manager.__version__", RUNNING), \
         patch.object(mgr, "apply_cloud_update", new=AsyncMock()) as apply_cloud:
        result = await mgr.apply_update()

    apply_cloud.assert_not_called()
    assert result == {"success": False, "error": "No update available. Run check first."}


def test_staging_keeps_the_rollback_mark(tmp_path):
    mgr = _manager(tmp_path, MagicMock())
    with patch("openavc.updater.manager.__version__", RUNNING):
        mgr.stage_update("0.39.0", URL, "ab" * 32, rollback=True)
        staged = mgr.get_staged_update()
    assert staged["rollback"] is True


@pytest.mark.asyncio
async def test_update_hands_a_staged_rollback_on_with_its_mark(tmp_path):
    mgr = _manager(tmp_path, MagicMock())
    _write_staged(tmp_path, "0.39.0", rollback=True)
    with patch("openavc.updater.manager.__version__", RUNNING), \
         patch.object(mgr, "apply_cloud_update",
                      new=AsyncMock(return_value={"success": False})) as apply_cloud:
        await mgr.apply_update()
    apply_cloud.assert_called_once_with("0.39.0", URL, "ab" * 32, rollback=True)
