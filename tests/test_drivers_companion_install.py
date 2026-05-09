"""Tests for sibling-companion fetch on the community-driver install /
update / uninstall endpoints.

Discovery-rewrite Step 4.5: a YAML driver that declares
``discovery.python: ./<id>_discovery.py`` cannot function without its
sibling Python file landing in ``driver_repo/`` next to the YAML. The
install endpoint now fetches the pair atomically; uninstall drops both
files; update replaces the companion alongside the YAML.

These tests exercise:
  * The two helper functions in isolation (parsing + URL/filename
    validation + the actual fetch).
  * The install endpoint end-to-end with httpx mocked so we can drive
    YAML / companion responses without GitHub.
  * The uninstall endpoint dropping the companion alongside the YAML.
  * The update endpoint swapping companion when the new YAML's
    declaration changes filename.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import yaml as _yaml
from fastapi import HTTPException

from server.api.routes.drivers import (
    _companion_relpath_from_yaml,
    _download_companion,
    install_community_driver,
    uninstall_driver,
    update_driver,
)
from server.api.models import CommunityDriverInstallRequest


# --- _companion_relpath_from_yaml -----------------------------------------


def test_relpath_from_string_form():
    yaml_text = _yaml.safe_dump({
        "id": "x",
        "name": "X",
        "transport": "tcp",
        "discovery": {"python": "./x_discovery.py"},
    })
    assert _companion_relpath_from_yaml(yaml_text) == "./x_discovery.py"


def test_relpath_from_dict_form():
    yaml_text = _yaml.safe_dump({
        "id": "x",
        "name": "X",
        "transport": "tcp",
        "discovery": {
            "python": {"file": "./x_discovery.py", "cross_vendor": True},
        },
    })
    assert _companion_relpath_from_yaml(yaml_text) == "./x_discovery.py"


def test_relpath_missing_when_no_discovery():
    yaml_text = _yaml.safe_dump({"id": "x", "name": "X", "transport": "tcp"})
    assert _companion_relpath_from_yaml(yaml_text) is None


def test_relpath_missing_when_no_python_block():
    yaml_text = _yaml.safe_dump({
        "id": "x",
        "name": "X",
        "transport": "tcp",
        "discovery": {"oui": ["aa:bb:cc"]},
    })
    assert _companion_relpath_from_yaml(yaml_text) is None


def test_relpath_handles_malformed_yaml():
    # Invalid YAML — helper returns None instead of raising; the actual
    # YAML validator on register flags malformed content downstream.
    assert _companion_relpath_from_yaml("not: valid: yaml: :") is None


def test_relpath_handles_non_dict_yaml():
    # YAML parses, but the result isn't a mapping at the top level.
    assert _companion_relpath_from_yaml("- just\n- a\n- list\n") is None


# --- _download_companion --------------------------------------------------


@pytest.mark.asyncio
async def test_download_companion_writes_to_repo(tmp_path):
    yaml_url = "https://raw.githubusercontent.com/open-avc/openavc-drivers/main/utility/foo.avcdriver"

    mock_resp = MagicMock()
    mock_resp.text = "async def probe(ctx): pass\n"
    mock_resp.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        local = await _download_companion(
            yaml_url=yaml_url,
            companion_relpath="./foo_discovery.py",
            driver_repo=tmp_path,
            driver_id="foo",
        )

    assert local == tmp_path / "foo_discovery.py"
    assert local.read_text(encoding="utf-8") == "async def probe(ctx): pass\n"


@pytest.mark.asyncio
async def test_download_companion_rejects_off_allowlist_url(tmp_path):
    # If the YAML companion path resolves to a non-GitHub host (e.g. an
    # absolute URL pointing at attacker.example), fail before any fetch.
    yaml_url = "https://raw.githubusercontent.com/open-avc/openavc-drivers/main/utility/foo.avcdriver"
    with pytest.raises(HTTPException) as exc:
        await _download_companion(
            yaml_url=yaml_url,
            companion_relpath="https://attacker.example/evil.py",
            driver_repo=tmp_path,
            driver_id="foo",
        )
    assert exc.value.status_code == 422
    assert "allowed host" in exc.value.detail


@pytest.mark.asyncio
async def test_download_companion_rejects_filename_without_discovery_suffix(tmp_path):
    # Even a YAML pointing at a same-host file is rejected unless the
    # basename matches the documented ``_discovery.py`` suffix —
    # prevents a YAML from using its companion path to land an
    # arbitrary .py file in driver_repo via path traversal
    # (``../../etc/passwd.py``) or stem-only references (``foo.py``).
    yaml_url = "https://raw.githubusercontent.com/open-avc/openavc-drivers/main/utility/foo.avcdriver"
    for bad in ["../../etc/passwd.py", "./foo.py", "./foo bar_discovery.py"]:
        with pytest.raises(HTTPException) as exc:
            await _download_companion(
                yaml_url=yaml_url,
                companion_relpath=bad,
                driver_repo=tmp_path,
                driver_id="foo",
            )
        assert exc.value.status_code == 422
        assert "invalid filename" in exc.value.detail


@pytest.mark.asyncio
async def test_download_companion_propagates_404_as_502(tmp_path):
    yaml_url = "https://raw.githubusercontent.com/open-avc/openavc-drivers/main/utility/foo.avcdriver"
    err_resp = MagicMock(status_code=404)
    err = httpx.HTTPStatusError("not found", request=MagicMock(), response=err_resp)
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock(side_effect=err)

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        with pytest.raises(HTTPException) as exc:
            await _download_companion(
                yaml_url=yaml_url,
                companion_relpath="./foo_discovery.py",
                driver_repo=tmp_path,
                driver_id="foo",
            )
    assert exc.value.status_code == 502
    assert "404" in exc.value.detail


# --- install / uninstall / update integration -----------------------------


def _yaml_with_companion(driver_id: str, *, companion: bool = True) -> str:
    """Build a minimal valid YAML driver, optionally with a companion."""
    body: dict = {
        "id": driver_id,
        "name": driver_id.replace("_", " ").title(),
        "transport": "tcp",
        "commands": {"power_on": {"label": "On", "string": "P\r", "params": {}}},
        "responses": [{"pattern": "OK", "mappings": [{"group": 0, "state": "ok"}]}],
        "state_variables": {"ok": {"type": "string", "label": "OK"}},
    }
    if companion:
        body["discovery"] = {
            "python": {
                "file": f"./{driver_id}_discovery.py",
                "cross_vendor": True,
            },
        }
    return _yaml.safe_dump(body, sort_keys=False)


def _mock_two_responses(yaml_text: str, companion_text: str) -> MagicMock:
    """Configure a patched httpx.AsyncClient that returns YAML then companion."""
    yaml_resp = MagicMock()
    yaml_resp.text = yaml_text
    yaml_resp.raise_for_status = MagicMock()
    companion_resp = MagicMock()
    companion_resp.text = companion_text
    companion_resp.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(side_effect=[yaml_resp, companion_resp])
    return mock_client


@pytest.fixture
def driver_repo(tmp_path, monkeypatch):
    """Point the install endpoint's driver_repo at a tmp dir."""
    repo = tmp_path / "driver_repo"
    repo.mkdir()
    monkeypatch.setattr(
        "server.api.routes.drivers._get_driver_repo_dir",
        lambda: repo,
    )
    return repo


@pytest.fixture(autouse=True)
def silence_register_and_refresh(monkeypatch):
    """Stub out the engine wiring so tests stay focused on filesystem state.

    The real install path registers the driver with the device manager
    and triggers a discovery refresh; both depend on a live engine that
    these tests don't set up. We replace them with no-ops so the
    companion-fetch logic is what's under test.
    """
    monkeypatch.setattr(
        "server.api.routes.drivers.refresh_all_device_matches",
        AsyncMock(return_value=None),
        raising=False,
    )
    # Patch where the names are looked up inside the route bodies.
    monkeypatch.setattr(
        "server.core.device_manager.register_driver",
        lambda cls: None,
    )
    monkeypatch.setattr(
        "server.core.device_manager.unregister_driver",
        lambda driver_id: None,
    )
    yield


@pytest.mark.asyncio
async def test_install_yaml_with_companion_lands_both_files(driver_repo):
    yaml_text = _yaml_with_companion("crestron_cip")
    companion_text = "async def probe(ctx):\n    pass\n"

    body = CommunityDriverInstallRequest(
        driver_id="crestron_cip",
        file_url="https://raw.githubusercontent.com/open-avc/openavc-drivers/main/utility/crestron_cip.avcdriver",
    )

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client_cls.return_value = _mock_two_responses(yaml_text, companion_text)
        # Patch the discovery refresh inside the route module.
        with patch(
            "server.api.discovery.refresh_all_device_matches",
            AsyncMock(return_value=None),
        ):
            result = await install_community_driver(body)

    assert result["status"] == "installed"
    yaml_file = driver_repo / "crestron_cip.avcdriver"
    companion_file = driver_repo / "crestron_cip_discovery.py"
    assert yaml_file.exists()
    assert companion_file.exists()
    assert companion_file.read_text(encoding="utf-8") == companion_text


@pytest.mark.asyncio
async def test_install_yaml_without_companion_lands_only_yaml(driver_repo):
    yaml_text = _yaml_with_companion("plain_widget", companion=False)
    body = CommunityDriverInstallRequest(
        driver_id="plain_widget",
        file_url="https://raw.githubusercontent.com/open-avc/openavc-drivers/main/utility/plain_widget.avcdriver",
    )

    yaml_resp = MagicMock()
    yaml_resp.text = yaml_text
    yaml_resp.raise_for_status = MagicMock()
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=yaml_resp)

    with patch("httpx.AsyncClient", return_value=mock_client):
        with patch(
            "server.api.discovery.refresh_all_device_matches",
            AsyncMock(return_value=None),
        ):
            result = await install_community_driver(body)

    assert result["status"] == "installed"
    assert (driver_repo / "plain_widget.avcdriver").exists()
    # No .py files at all in the repo.
    assert list(driver_repo.glob("*.py")) == []
    # AsyncClient.get was called exactly once (no second fetch attempted).
    assert mock_client.get.call_count == 1


@pytest.mark.asyncio
async def test_install_rolls_back_yaml_when_companion_fetch_fails(driver_repo):
    yaml_text = _yaml_with_companion("crestron_cip")
    body = CommunityDriverInstallRequest(
        driver_id="crestron_cip",
        file_url="https://raw.githubusercontent.com/open-avc/openavc-drivers/main/utility/crestron_cip.avcdriver",
    )

    yaml_resp = MagicMock()
    yaml_resp.text = yaml_text
    yaml_resp.raise_for_status = MagicMock()

    err_resp = MagicMock(status_code=404)
    companion_err = MagicMock()
    companion_err.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError(
            "not found", request=MagicMock(), response=err_resp,
        ),
    )

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(side_effect=[yaml_resp, companion_err])

    with patch("httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(HTTPException) as exc:
            await install_community_driver(body)

    assert exc.value.status_code == 502
    # Both files should be absent — install was atomic.
    assert not (driver_repo / "crestron_cip.avcdriver").exists()
    assert not (driver_repo / "crestron_cip_discovery.py").exists()


@pytest.mark.asyncio
async def test_uninstall_drops_companion_alongside_yaml(driver_repo, monkeypatch):
    # Pre-populate driver_repo as if a previous install had landed the pair.
    yaml_text = _yaml_with_companion("crestron_cip")
    yaml_file = driver_repo / "crestron_cip.avcdriver"
    yaml_file.write_text(yaml_text, encoding="utf-8")
    companion_file = driver_repo / "crestron_cip_discovery.py"
    companion_file.write_text("async def probe(ctx): pass\n", encoding="utf-8")

    # Stub the engine — uninstall checks for devices using the driver.
    fake_engine = MagicMock()
    fake_engine.project = None
    monkeypatch.setattr(
        "server.api.routes.drivers._get_engine",
        lambda: fake_engine,
    )

    with patch(
        "server.api.discovery.refresh_all_device_matches",
        AsyncMock(return_value=None),
    ):
        result = await uninstall_driver("crestron_cip")

    assert result["status"] == "uninstalled"
    assert not yaml_file.exists()
    assert not companion_file.exists()


@pytest.mark.asyncio
async def test_uninstall_leaves_unrelated_py_files_alone(driver_repo, monkeypatch):
    """An installed YAML with no `python:` block should not delete a
    similarly-named .py file. The user might have created that .py
    independently as a Python driver."""
    yaml_text = _yaml_with_companion("plain_widget", companion=False)
    yaml_file = driver_repo / "plain_widget.avcdriver"
    yaml_file.write_text(yaml_text, encoding="utf-8")
    user_py = driver_repo / "plain_widget_discovery.py"
    user_py.write_text("# user-authored, unrelated\n", encoding="utf-8")

    fake_engine = MagicMock()
    fake_engine.project = None
    monkeypatch.setattr(
        "server.api.routes.drivers._get_engine",
        lambda: fake_engine,
    )

    with patch(
        "server.api.discovery.refresh_all_device_matches",
        AsyncMock(return_value=None),
    ):
        await uninstall_driver("plain_widget")

    assert not yaml_file.exists()
    # User's standalone .py file is untouched because the YAML never
    # claimed it via discovery.python.
    assert user_py.exists()


@pytest.mark.asyncio
async def test_update_swaps_companion_when_new_yaml_changes_filename(
    driver_repo, monkeypatch,
):
    """If the new YAML version declares a different companion filename
    than the old one, the orphaned old companion gets cleaned up."""
    # Pre-existing install: YAML + old_discovery.py
    old_yaml = _yaml.safe_dump({
        "id": "crestron_cip",
        "name": "CIP",
        "transport": "tcp",
        "commands": {"x": {"string": "x\r", "params": {}}},
        "discovery": {"python": "./crestron_cip_discovery.py"},
    })
    yaml_file = driver_repo / "crestron_cip.avcdriver"
    yaml_file.write_text(old_yaml, encoding="utf-8")
    old_companion = driver_repo / "crestron_cip_discovery.py"
    old_companion.write_text("# old\n", encoding="utf-8")

    # New YAML renames the companion to a different file.
    new_yaml = _yaml.safe_dump({
        "id": "crestron_cip",
        "name": "CIP",
        "transport": "tcp",
        "commands": {"x": {"string": "x\r", "params": {}}},
        "discovery": {"python": "./crestron_cip_v2_discovery.py"},
    })
    new_companion_text = "# new probe\n"

    fake_request = MagicMock()
    fake_request.json = AsyncMock(return_value={
        "file_url": "https://raw.githubusercontent.com/open-avc/openavc-drivers/main/utility/crestron_cip.avcdriver",
    })

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client_cls.return_value = _mock_two_responses(new_yaml, new_companion_text)
        with patch(
            "server.api.discovery.refresh_all_device_matches",
            AsyncMock(return_value=None),
        ):
            result = await update_driver("crestron_cip", fake_request)

    assert result["status"] == "updated"
    assert yaml_file.exists()
    # Old companion was orphaned by the rename and removed.
    assert not old_companion.exists()
    # New companion is in place.
    assert (driver_repo / "crestron_cip_v2_discovery.py").exists()
    assert (driver_repo / "crestron_cip_v2_discovery.py").read_text(
        encoding="utf-8",
    ) == new_companion_text
