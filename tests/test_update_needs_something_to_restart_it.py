"""An in-app update or rollback ends the server's process and leaves the rest
to whatever starts it again: the service applies the staged release (or the
earlier one) and starts the server. The updater asks the restart door's
answer (``restart.is_service_managed``) before offering either, so an
installed copy started by hand is told what to do instead of being left
stopped with the update unapplied."""

from __future__ import annotations

import plistlib
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openavc.updater.manager import UpdateManager
from openavc.updater.platform import STARTED_BY_HAND, DeploymentType


def _manager(tmp_path, deployment=DeploymentType.LINUX_PACKAGE):
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    mgr = UpdateManager.__new__(UpdateManager)
    mgr._data_dir = data_dir
    mgr._project_path = data_dir / "project.avc"
    mgr._state = MagicMock()
    mgr._state.get.return_value = ""
    mgr._checker = MagicMock()
    mgr._update_in_progress = False
    mgr._history = []
    mgr._maintenance_task = None
    mgr._auto_check_task = None
    mgr._older_than_last_update = ""
    mgr._deployment_type = deployment
    # Whatever a refusal fails to stop never reaches the network or ends the
    # test process.
    mgr._download_update = AsyncMock()
    mgr._download_artifact = AsyncMock()
    mgr._apply_linux = MagicMock()
    mgr._restart_process = MagicMock()
    return mgr


@pytest.fixture
def started_by_hand(monkeypatch):
    monkeypatch.setattr("openavc.restart.is_service_managed", lambda: False)


def _release(version="9.9.9"):
    return MagicMock(version=version, prerelease=False, changelog="", published_at="")


async def test_a_check_offers_no_update_and_says_what_to_do(tmp_path, started_by_hand):
    mgr = _manager(tmp_path)
    mgr._checker.check = AsyncMock(return_value=_release())

    answer = await mgr.check_for_updates(channel="stable")

    assert answer["update_available"] is True
    assert answer["can_self_update"] is False
    assert answer["instructions"] == STARTED_BY_HAND


async def test_the_status_says_it_does_not_update_itself(tmp_path, started_by_hand):
    mgr = _manager(tmp_path)
    with patch("openavc.updater.rollback.can_rollback", return_value=True), \
         patch("openavc.updater.rollback.rollback_target_version", return_value="1.0.0"):
        status = mgr.get_status()

    assert status["can_self_update"] is False
    assert status["rollback_available"] is False


async def test_an_apply_is_refused_before_anything_is_backed_up_or_downloaded(
        tmp_path, started_by_hand):
    mgr = _manager(tmp_path)
    mgr._checker.last_result = _release()
    with patch("openavc.updater.backup.create_backup") as backup:
        answer = await mgr.apply_update()

    assert answer == {
        "success": False, "error": STARTED_BY_HAND, "instructions": STARTED_BY_HAND,
    }
    backup.assert_not_called()
    mgr._download_update.assert_not_called()


async def test_a_cloud_update_is_refused_the_same_way(tmp_path, started_by_hand):
    mgr = _manager(tmp_path)
    with patch("openavc.updater.backup.create_backup") as backup:
        answer = await mgr.apply_cloud_update("9.9.9", "https://example.invalid/u.tar.gz")

    assert answer["success"] is False
    assert answer["error"] == STARTED_BY_HAND
    backup.assert_not_called()
    mgr._download_artifact.assert_not_called()


async def test_a_rollback_is_refused_before_it_exits(tmp_path, started_by_hand):
    mgr = _manager(tmp_path)
    with patch("openavc.updater.rollback.can_rollback", return_value=True), \
         patch("openavc.updater.rollback.perform_rollback") as rollback, \
         patch("openavc.updater.backup.create_backup") as backup, \
         patch.object(mgr, "_exit_for_rollback") as exit_:
        answer = await mgr.rollback()

    assert answer == {"success": False, "error": STARTED_BY_HAND}
    rollback.assert_not_called()
    backup.assert_not_called()
    exit_.assert_not_called()


async def test_an_automatic_update_policy_starts_no_maintenance_loop(tmp_path, started_by_hand):
    mgr = _manager(tmp_path)
    with patch("openavc.system_config.get_system_config") as config:
        config.return_value.get.return_value = False  # not "notify only"
        await mgr.apply_update_policy({
            "policy": "auto",
            "maintenance_window_start": "02:00",
            "maintenance_window_end": "04:00",
        })
    loop = mgr._maintenance_task
    if loop is not None:
        loop.cancel()
    assert loop is None


async def test_a_kind_of_install_that_never_updates_itself_keeps_its_own_steps(
        tmp_path, under_its_service):
    mgr = _manager(tmp_path, DeploymentType.DOCKER)
    mgr._checker.check = AsyncMock(return_value=_release())

    answer = await mgr.check_for_updates(channel="stable")

    assert answer["can_self_update"] is False
    assert "docker compose pull" in answer["instructions"]


async def test_under_its_service_the_update_is_offered(tmp_path, under_its_service):
    mgr = _manager(tmp_path)
    mgr._checker.check = AsyncMock(return_value=_release())

    answer = await mgr.check_for_updates(channel="stable")

    assert answer["can_self_update"] is True
    assert "instructions" not in answer


# -- every service an installer sets up says it is one -----------------------

INSTALLER = Path(__file__).resolve().parent.parent / "installer"


def _nssm_environment() -> list[str]:
    for line in (INSTALLER / "install-service.bat").read_text(encoding="utf-8").splitlines():
        if "AppEnvironmentExtra" in line and not line.lstrip().upper().startswith("REM"):
            return re.findall(r'"([A-Z_]+=[^"]*)"', line)
    raise AssertionError("install-service.bat sets no AppEnvironmentExtra")


def test_the_windows_service_says_it_is_one():
    assert "OPENAVC_SERVICE_MANAGED=1" in _nssm_environment()


def test_the_macos_daemon_says_it_is_one():
    with (INSTALLER / "com.openavc.server.plist").open("rb") as f:
        daemon = plistlib.load(f)
    assert daemon["EnvironmentVariables"]["OPENAVC_SERVICE_MANAGED"] == "1"


@pytest.mark.parametrize("unit", ["openavc.service", "install.sh"])
def test_the_linux_units_say_so_beside_systemds_own_signal(unit):
    text = (INSTALLER / unit).read_text(encoding="utf-8")
    assert re.search(r"^Environment=OPENAVC_SERVICE_MANAGED=1$", text, re.MULTILINE)
