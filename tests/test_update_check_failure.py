"""A failed update check leaves no update behind it.

The check's answer is what the Updates page reads (``system.update_available``)
and what an Install installs (``system.update_install_version``, REST's apply).
When a check fails (GitHub unreachable, an error status, a body that is no
release list), the page hides Install, so the release an earlier check found
must be gone from the checker too: an Install through REST, the status and the
state key then agree with the page that there is nothing to install. A staged
target does not come from the check and stays.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from openavc.updater import update_run
from openavc.updater.checker import UpdateChecker
from openavc.updater.manager import UpdateManager
from openavc.updater.platform import DeploymentType

# An installed system, whose service starts the version an update installs.
pytestmark = pytest.mark.usefixtures("under_its_service")

RUNNING = "0.41.0"
FOUND = "0.42.0"
STAGED = "0.43.0"
STAGED_URL = f"https://github.com/open-avc/openavc/releases/download/v{STAGED}/openavc-{STAGED}-linux-x86_64.tar.gz"

_RELEASES = [{
    "tag_name": f"v{FOUND}",
    "prerelease": False,
    "draft": False,
    "body": "",
    "published_at": "2026-11-01T00:00:00Z",
    "assets": [],
}]


def _fails_with_status(_request):
    return httpx.Response(503)


def _fails_to_connect(request):
    raise httpx.ConnectError("Connection refused", request=request)


def _answers_no_json(_request):
    return httpx.Response(200, text="<html>maintenance</html>")


def _answers_no_list(_request):
    return httpx.Response(200, json={"message": "Moved"})


def _answers_no_releases(_request):
    return httpx.Response(200, json=[])


FAILURES = pytest.mark.parametrize("fails", [
    _fails_with_status, _fails_to_connect, _answers_no_json, _answers_no_list, _answers_no_releases,
], ids=["error status", "unreachable", "not json", "not a list", "no releases"])


class _GitHub:
    """GitHub's releases endpoint as the checker reaches it: every request
    goes to ``answer``, which finds ``FOUND`` until it is changed."""

    def __init__(self):
        self.answer = lambda _request: httpx.Response(200, json=_RELEASES)
        real = httpx.AsyncClient
        self._patch = patch(
            "openavc.updater.checker.httpx.AsyncClient",
            lambda **_kwargs: real(transport=httpx.MockTransport(lambda r: self.answer(r))),
        )

    def __enter__(self):
        self._patch.start()
        return self

    def __exit__(self, *_exc):
        self._patch.stop()


class _State:
    """The state keys the updater sets, as the IDE reads them."""

    def __init__(self):
        self.values: dict = {}

    def set(self, key, value, source=None):
        self.values[key] = value

    def get(self, key, default=None):
        return self.values.get(key, default)


def _start(tmp_path, *, staged: bool = False) -> UpdateManager:
    with patch("openavc.updater.manager.__version__", RUNNING):
        mgr = UpdateManager(state_store=_State(), data_dir=tmp_path)
        mgr._deployment_type = DeploymentType.LINUX_PACKAGE
        mgr._checker.current_version = RUNNING
        if staged:
            mgr.stage_update(STAGED, STAGED_URL, "ab" * 32)
    return mgr


async def _check(mgr: UpdateManager) -> dict:
    with patch("openavc.updater.manager.__version__", RUNNING):
        return await mgr.check_for_updates("stable")


def _install_named(mgr: UpdateManager) -> str:
    """What the Updates page's Install names, live and on load; the two agree."""
    with patch("openavc.updater.manager.__version__", RUNNING):
        on_load = mgr.get_status()["install_version"]
    assert mgr._state.get("system.update_install_version") == on_load
    return on_load


async def _install(mgr: UpdateManager, tmp_path, call) -> dict:
    """Press Install (``call``) with nothing fetched for real."""
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
        return await call()


def _run_target(tmp_path) -> str:
    run_file = tmp_path / update_run.RUN_FILE
    if not run_file.exists():
        return ""
    return json.loads(run_file.read_text(encoding="utf-8"))["target"]


# ── The checker ──


@FAILURES
async def test_a_failed_check_forgets_the_release_an_earlier_one_found(fails):
    checker = UpdateChecker(current_version=RUNNING)
    with _GitHub() as github:
        assert (await checker.check("stable")).version == FOUND
        github.answer = fails
        assert await checker.check("stable") is None

    assert checker.last_error
    assert checker.last_result is None


async def test_a_check_that_finds_nothing_forgets_it_too():
    checker = UpdateChecker(current_version=RUNNING)
    with _GitHub() as github:
        await checker.check("stable")
        checker.current_version = FOUND
        github.answer = lambda _request: httpx.Response(200, json=_RELEASES)
        assert await checker.check("stable") is None

    assert checker.last_error == ""
    assert checker.last_result is None


# ── The doors that read it ──


@FAILURES
async def test_after_a_failed_check_an_install_installs_nothing(tmp_path, fails):
    mgr = _start(tmp_path)
    with _GitHub() as github:
        await _check(mgr)
        assert _install_named(mgr) == FOUND
        github.answer = fails
        result = await _check(mgr)

    assert result["update_available"] is False and result["error"]
    assert mgr._state.get("system.update_available") == ""
    assert _install_named(mgr) == ""

    applied = await _install(mgr, tmp_path, mgr.apply_update)
    assert applied["success"] is False
    assert "No update available" in applied["error"]
    checked = await _install(mgr, tmp_path, lambda: mgr.apply_checked_update(FOUND))
    assert checked["success"] is False
    assert _run_target(tmp_path) == ""


async def test_after_a_failed_check_a_staged_target_still_installs(tmp_path):
    mgr = _start(tmp_path, staged=True)
    with _GitHub() as github:
        await _check(mgr)
        github.answer = _fails_to_connect
        await _check(mgr)

    assert _install_named(mgr) == STAGED
    applied = await _install(mgr, tmp_path, mgr.apply_update)
    assert applied["success"] is True, applied
    assert _run_target(tmp_path) == STAGED


async def test_the_next_check_that_finds_the_release_offers_it_again(tmp_path):
    mgr = _start(tmp_path)
    with _GitHub() as github:
        github.answer = _fails_to_connect
        await _check(mgr)
        assert _install_named(mgr) == ""
        github.answer = lambda _request: httpx.Response(200, json=_RELEASES)
        result = await _check(mgr)

    assert result["available_version"] == FOUND
    assert _install_named(mgr) == FOUND
