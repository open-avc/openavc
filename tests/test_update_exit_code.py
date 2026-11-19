"""The exit code an applied update or rollback ends the process with, and what
each install's service manager does on it.

One rule decides the code by install type (``updater/platform.py``
``update_exit``), asked by the updater's exit (``manager._end_process``: an
update, a cloud update, a rollback someone asked for) and by the automatic
rollback at start (``main._roll_back_a_failed_update``). Only an installed
Windows server ends with a code of its own, ``restart.LEAVE_STOPPED_EXIT_CODE``:
NSSM leaves the service stopped on it (install-service.bat), so the installer
the update scheduled starts the version it applied and NSSM does not start the
old one in its way. Every other install ends with 0, and its service manager
starts the server again on it, its helper applying the update first: systemd's
``Restart=always``, launchd's ``KeepAlive``, and the appliance's device
supervisor, which relaunches a server that is gone whatever its code.
"""

from __future__ import annotations

import plistlib
import re
from unittest.mock import patch

import pytest

from openavc import main, restart
from openavc.updater import platform
from openavc.updater.manager import UpdateManager
from openavc.updater.platform import DeploymentType, can_self_update, update_exit
from tests.test_service_stop_bound import (
    _UNITS,
    INSTALL_SERVICE_BAT,
    MAC_PLIST,
    _service_setting,
)


class _Exited(BaseException):
    def __init__(self, code: int) -> None:
        self.code = code


def _exit(code: int) -> None:
    raise _Exited(code)


# -- the rule -------------------------------------------------------------------


def test_an_installed_windows_server_leaves_the_start_to_the_installer():
    assert update_exit(DeploymentType.WINDOWS_INSTALLER).code == restart.LEAVE_STOPPED_EXIT_CODE


@pytest.mark.parametrize("deployment", [
    t for t in DeploymentType if can_self_update(t) and t is not DeploymentType.WINDOWS_INSTALLER
])
def test_every_other_install_that_updates_itself_ends_with_0(deployment):
    ending = update_exit(deployment)
    assert ending.code == 0
    assert ending.who_starts_it != update_exit(DeploymentType.UNKNOWN).who_starts_it


def _the_updater_s_code(deployment, tmp_path) -> int:
    manager = UpdateManager(state_store=None, data_dir=tmp_path)
    manager._deployment_type = deployment
    with patch.object(restart, "exit_for_update", return_value=True) as door:
        manager._end_process("rollback")
    ((_source, code, _last), _kwargs) = door.call_args
    return code


def _the_start_s_rollback_code(deployment, tmp_path, monkeypatch) -> int:
    monkeypatch.setattr(platform, "detect_deployment_type", lambda app_dir=None: deployment)
    monkeypatch.setattr("openavc.updater.rollback.check_rollback_needed", lambda data_dir: True)
    monkeypatch.setattr("openavc.updater.rollback.restore_pre_update_data", lambda *a, **k: True)
    monkeypatch.setattr("openavc.updater.rollback.perform_rollback", lambda *a, **k: True)
    monkeypatch.setattr("openavc.core.device_logins.put_back_for_rollback", lambda *a, **k: None)
    monkeypatch.setattr(main.os, "_exit", _exit)
    with pytest.raises(_Exited) as exited:
        main._roll_back_a_failed_update(tmp_path)
    return exited.value.code


@pytest.mark.parametrize("deployment", list(DeploymentType))
def test_the_updater_and_the_start_s_rollback_end_with_the_install_s_code(
    deployment, tmp_path, monkeypatch,
):
    """The install type decides, not the operating system the server runs on:
    a rollback at start ends the way the updater's own exit does."""
    expected = update_exit(deployment).code
    assert _the_updater_s_code(deployment, tmp_path) == expected
    assert _the_start_s_rollback_code(deployment, tmp_path, monkeypatch) == expected


# -- what each service manager does on it --------------------------------------


def _nssm_exit_actions() -> dict[str, str]:
    actions = {}
    for line in INSTALL_SERVICE_BAT.read_text(encoding="utf-8").splitlines():
        found = re.match(r'^"%INSTALL_DIR%\\nssm\.exe" set OpenAVC AppExit (\S+) (\S+)\s*$', line)
        if found:
            actions[found.group(1)] = found.group(2)
    return actions


def test_nssm_leaves_the_service_stopped_on_that_code_and_starts_it_again_on_any_other():
    assert _nssm_exit_actions() == {
        "Default": "Restart",
        str(restart.LEAVE_STOPPED_EXIT_CODE): "Exit",
    }
    # A failed start's retry and a plain restart are started again.
    assert restart.LEAVE_STOPPED_EXIT_CODE not in (0, restart.RETRY_EXIT_CODE)


@pytest.mark.parametrize("name", sorted(_UNITS))
def test_systemd_starts_the_server_again_after_an_exit_with_0(name):
    """``on-failure`` would leave an update's exit 0 stopped, its helper never run."""
    assert _service_setting(_UNITS[name](), "Restart") == ["always"]


def test_launchd_starts_the_server_again_after_an_exit_with_0():
    """``KeepAlive`` as a dictionary (``SuccessfulExit: false``) would leave an
    update's exit 0 stopped, its wrapper never run."""
    with MAC_PLIST.open("rb") as handle:
        assert plistlib.load(handle)["KeepAlive"] is True
