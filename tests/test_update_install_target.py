"""What an update installs, whichever door starts it.

The maintenance window and an update the cloud sends with no download link
check for the newest release and install the one they found, whatever the
cloud has staged. An Install installs what the updater publishes as
``system.update_install_version`` (and the status's ``install_version``): the
staged target while one waits, else the release the last check found. Once
any update applies, a staged target is dropped: installed now, or replaced by
the update that applied. One that fails keeps it.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import time as dt_time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from openavc.cloud.command_handler import CommandHandler
from openavc.updater import update_run
from openavc.updater.checker import ReleaseInfo
from openavc.updater.manager import UpdateManager
from openavc.updater.platform import DeploymentType

# An installed system, whose service starts the version an update installs.
pytestmark = pytest.mark.usefixtures("under_its_service")

RUNNING = "0.41.0"
STAGED = "0.43.0"
STAGED_URL = f"https://github.com/open-avc/openavc/releases/download/v{STAGED}/openavc-{STAGED}-linux-x86_64.tar.gz"
CHECKED = ReleaseInfo(version="0.42.0", tag="v0.42.0", prerelease=False, changelog="", published_at="")


class _State:
    """The state keys the updater sets, as the IDE reads them."""

    def __init__(self):
        self.values: dict = {}

    def set(self, key, value, source=None):
        self.values[key] = value

    def get(self, key, default=None):
        return self.values.get(key, default)


def _start(tmp_path: Path, *, staged: bool = True) -> UpdateManager:
    """A system on ``RUNNING``, with the cloud's ``STAGED`` waiting when asked."""
    with patch("openavc.updater.manager.__version__", RUNNING):
        mgr = UpdateManager(state_store=_State(), data_dir=tmp_path)
        mgr._deployment_type = DeploymentType.LINUX_PACKAGE
        if staged:
            mgr.stage_update(STAGED, STAGED_URL, "ab" * 32)
    return mgr


def _finds(mgr: UpdateManager, release: ReleaseInfo | None) -> None:
    """The update check finds ``release``."""
    async def check(channel="stable"):
        mgr._checker._last_check_result = release
        return release
    mgr._checker.check = check


@contextmanager
def _installing(mgr: UpdateManager, tmp_path: Path, *, download_fails: bool = False):
    """An install with nothing fetched: the checked release's download answers
    a file (or fails), and the staged target's is never reached."""
    checked = AsyncMock(
        side_effect=RuntimeError("connection reset") if download_fails else None,
        return_value=tmp_path / "update-cache" / "a.tar.gz",
    )
    staged = AsyncMock(side_effect=AssertionError("the staged target was downloaded"))
    with patch("openavc.updater.manager.__version__", RUNNING), \
         patch("openavc.updater.backup.create_backup", return_value=tmp_path / "b.zip"), \
         patch("openavc.updater.backup.cleanup_old_backups"), \
         patch("openavc.updater.rollback.write_pending_marker"), \
         patch.object(mgr, "_download_update", checked), \
         patch.object(mgr, "_download_artifact", staged), \
         patch.object(mgr, "_end_process"):
        yield


def _run(tmp_path: Path) -> dict:
    return json.loads((tmp_path / update_run.RUN_FILE).read_text(encoding="utf-8"))


def _staged_shown(mgr: UpdateManager) -> str:
    """The staged target as the Updates page sees it, live and on load."""
    with patch("openavc.updater.manager.__version__", RUNNING):
        on_load = mgr.get_status()["staged_version"]
    live = mgr._state.get("system.update_staged_version")
    assert live == on_load
    return live


def _assert_the_checked_release_went_ahead(mgr: UpdateManager, tmp_path: Path) -> None:
    run = _run(tmp_path)
    assert (run["target"], run["source"], run["step"]) == (CHECKED.version, "checked", "restarting")
    assert not (tmp_path / "staged-update.json").exists()
    assert _staged_shown(mgr) == ""


# ── The doors that check first install what they found ──


@pytest.mark.asyncio
async def test_the_maintenance_window_installs_the_release_it_checked(tmp_path, monkeypatch):
    mgr = _start(tmp_path)
    _finds(mgr, CHECKED)
    monkeypatch.setattr(mgr, "_in_window", lambda *_args: True)

    with _installing(mgr, tmp_path):
        await mgr._maintenance_window_loop(dt_time(2), dt_time(4), None)

    _assert_the_checked_release_went_ahead(mgr, tmp_path)


class _Agent:
    def __init__(self):
        self.sent: list[tuple[str, dict]] = []

    async def send_message(self, msg_type, payload):
        self.sent.append((msg_type, payload))


class _Events:
    async def emit(self, event, data):
        pass


@pytest.mark.asyncio
async def test_an_update_the_cloud_sends_with_no_link_installs_the_release_it_checked(tmp_path):
    mgr = _start(tmp_path)
    _finds(mgr, CHECKED)
    agent = _Agent()
    handler = CommandHandler(agent, object(), _Events(), update_manager=mgr)
    msg = {"type": "software_update", "payload": {
        "request_id": "req-1", "target_version": CHECKED.version, "update_url": "", "auto_restart": True,
    }}

    with _installing(mgr, tmp_path), \
         patch("openavc.cloud.command_handler.__version__", RUNNING), \
         patch("openavc.updater.platform.detect_deployment_type", return_value=DeploymentType.LINUX_PACKAGE):
        await handler.handle(msg)

    _assert_the_checked_release_went_ahead(mgr, tmp_path)
    (_, answer), = agent.sent
    assert (answer["success"], answer["result"]) == (True, f"Update to v{CHECKED.version} started")


@pytest.mark.asyncio
async def test_a_checked_install_that_fails_keeps_the_staged_target(tmp_path, monkeypatch):
    mgr = _start(tmp_path)
    _finds(mgr, CHECKED)
    monkeypatch.setattr(mgr, "_in_window", lambda *_args: True)

    with _installing(mgr, tmp_path, download_fails=True):
        await mgr._maintenance_window_loop(dt_time(2), dt_time(4), None)

    run = _run(tmp_path)
    assert (run["target"], run["end"]) == (CHECKED.version, "failed")
    assert (tmp_path / "staged-update.json").exists()
    assert _staged_shown(mgr) == STAGED


@pytest.mark.asyncio
async def test_a_checked_release_no_longer_on_offer_is_refused(tmp_path):
    """The window and the cloud's door name the version their check found; a
    check that has found another since is not installed in its place."""
    mgr = _start(tmp_path, staged=False)
    _finds(mgr, CHECKED)
    with patch("openavc.updater.manager.__version__", RUNNING):
        await mgr.check_for_updates("stable")

    with _installing(mgr, tmp_path):
        answer = await mgr.apply_checked_update("0.44.0")

    assert answer == {
        "success": False,
        "error": "v0.44.0 is no longer the newest release this system finds. Try the update again.",
    }
    assert not (tmp_path / update_run.RUN_FILE).exists()


# ── An Install installs the version the Updates page names ──


@pytest.mark.asyncio
@pytest.mark.parametrize(("staged", "checked", "installs"), [
    (True, None, STAGED),
    (False, CHECKED, CHECKED.version),
    (True, CHECKED, STAGED),
])
async def test_an_install_installs_the_version_it_names(tmp_path, staged, checked, installs):
    mgr = _start(tmp_path, staged=staged)
    _finds(mgr, checked)
    with patch("openavc.updater.manager.__version__", RUNNING):
        await mgr.check_for_updates("stable")
        named = mgr.get_status()["install_version"]
    assert mgr._state.get("system.update_install_version") == named == installs

    downloaded = AsyncMock(return_value=tmp_path / "a.tar.gz")
    with patch("openavc.updater.manager.__version__", RUNNING), \
         patch("openavc.updater.backup.create_backup", return_value=tmp_path / "b.zip"), \
         patch("openavc.updater.backup.cleanup_old_backups"), \
         patch("openavc.updater.rollback.write_pending_marker"), \
         patch.object(mgr, "_download_update", downloaded), \
         patch.object(mgr, "_download_artifact", downloaded), \
         patch.object(mgr, "_verify_hash", AsyncMock()), \
         patch.object(mgr, "_download_sidecar_sig", AsyncMock()), \
         patch.object(mgr, "_end_process"):
        await mgr.apply_update()

    assert _run(tmp_path)["target"] == installs


def test_with_nothing_staged_or_found_an_install_names_nothing(tmp_path):
    mgr = _start(tmp_path, staged=False)
    with patch("openavc.updater.manager.__version__", RUNNING):
        assert mgr.get_status()["install_version"] == ""
    assert mgr._state.get("system.update_install_version") == ""


def test_staging_and_clearing_move_the_version_an_install_names(tmp_path):
    mgr = _start(tmp_path, staged=False)
    mgr._checker._last_check_result = CHECKED
    with patch("openavc.updater.manager.__version__", RUNNING):
        mgr.stage_update(STAGED, STAGED_URL, "ab" * 32)
        assert mgr._state.get("system.update_install_version") == STAGED
        mgr.clear_staged_update()
    assert mgr._state.get("system.update_install_version") == CHECKED.version
