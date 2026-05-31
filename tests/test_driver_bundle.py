"""Tests for multi-file Python driver handling: companion fetch on install,
and the .zip bundle import / export endpoints.

A Python driver is really a bundle — the main ``.py`` plus an optional
``*_discovery.py`` companion and ``*_sim.py`` simulator. Before this, the
community install pulled only the main ``.py`` (companions are fetched for
YAML drivers only), so installed Python drivers silently lost simulation and
the discovery backup path. These tests cover:

  * ``_try_download_python_companion`` in isolation (writes / 404 / allowlist /
    filename validation).
  * ``install_community_driver`` on a ``.py`` driver pulling the conventional
    ``_discovery.py`` + ``_sim.py`` siblings (and tolerating their absence).
  * ``upload_driver_bundle`` — zip validation + round-trip.
  * ``export_python_driver_bundle`` — zips the driver + its companions.
"""

import io
import zipfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from server.api.routes.drivers import (
    _try_download_python_companion,
    export_python_driver_bundle,
    install_community_driver,
    upload_driver_bundle,
)
from server.api.models import CommunityDriverInstallRequest


# --- fixtures --------------------------------------------------------------


@pytest.fixture
def driver_repo(tmp_path, monkeypatch):
    """Point both repo-path accessors at a tmp dir.

    ``upload_driver_bundle`` / install use ``_get_driver_repo_dir``; export
    resolves through ``_safe_driver_path``, which reads
    ``server.system_config.DRIVER_REPO_DIR`` directly — so patch both.
    """
    repo = tmp_path / "driver_repo"
    repo.mkdir()
    monkeypatch.setattr(
        "server.api.routes.drivers._get_driver_repo_dir", lambda: repo
    )
    monkeypatch.setattr("server.system_config.DRIVER_REPO_DIR", repo)
    return repo


@pytest.fixture(autouse=True)
def stub_engine_wiring(monkeypatch):
    """No-op the engine registration / discovery refresh / orphan retry."""
    monkeypatch.setattr(
        "server.core.device_manager.register_driver", lambda cls: None
    )
    monkeypatch.setattr(
        "server.api.discovery.refresh_all_device_matches",
        AsyncMock(return_value=None),
        raising=False,
    )
    fake_engine = MagicMock()
    fake_engine.devices.retry_all_orphans = AsyncMock(return_value=[])
    monkeypatch.setattr(
        "server.api.routes.drivers._get_engine", lambda: fake_engine
    )
    yield


def _fake_driver_class(driver_id: str):
    cls = MagicMock()
    cls.DRIVER_INFO = {"id": driver_id}
    return cls


def _make_zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


class _FakeUpload:
    def __init__(self, filename: str, data: bytes):
        self.filename = filename
        self._data = data

    async def read(self) -> bytes:
        return self._data


def _request_with_file(filename: str | None, data: bytes = b"") -> MagicMock:
    req = MagicMock()
    form = {} if filename is None else {"file": _FakeUpload(filename, data)}
    req.form = AsyncMock(return_value=form)
    return req


# --- _try_download_python_companion ---------------------------------------


@pytest.mark.asyncio
async def test_companion_writes_on_200(tmp_path):
    main_url = "https://raw.githubusercontent.com/open-avc/openavc-drivers/main/switchers/foo.py"
    resp = MagicMock(status_code=200, text="async def probe(ctx): pass\n")
    resp.raise_for_status = MagicMock()
    with patch("httpx.AsyncClient") as cls:
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.get = AsyncMock(return_value=resp)
        cls.return_value = client
        out = await _try_download_python_companion(
            main_url=main_url,
            companion_filename="foo_discovery.py",
            driver_repo=tmp_path,
        )
    assert out == tmp_path / "foo_discovery.py"
    assert out.read_text(encoding="utf-8") == "async def probe(ctx): pass\n"


@pytest.mark.asyncio
async def test_companion_returns_none_on_404(tmp_path):
    main_url = "https://raw.githubusercontent.com/open-avc/openavc-drivers/main/switchers/foo.py"
    resp = MagicMock(status_code=404)
    with patch("httpx.AsyncClient") as cls:
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.get = AsyncMock(return_value=resp)
        cls.return_value = client
        out = await _try_download_python_companion(
            main_url=main_url,
            companion_filename="foo_sim.py",
            driver_repo=tmp_path,
        )
    assert out is None
    assert list(tmp_path.glob("*.py")) == []


@pytest.mark.asyncio
async def test_companion_rejects_bad_filename(tmp_path):
    # Not a documented companion suffix -> refused before any fetch.
    main_url = "https://raw.githubusercontent.com/open-avc/openavc-drivers/main/switchers/foo.py"
    out = await _try_download_python_companion(
        main_url=main_url,
        companion_filename="foo.py",
        driver_repo=tmp_path,
    )
    assert out is None


@pytest.mark.asyncio
async def test_companion_rejects_off_allowlist_host(tmp_path):
    out = await _try_download_python_companion(
        main_url="https://attacker.example/switchers/foo.py",
        companion_filename="foo_discovery.py",
        driver_repo=tmp_path,
    )
    assert out is None


# --- install pulls Python companions --------------------------------------


def _mock_three(main_src: str, disc_src: str, sim_src: str) -> MagicMock:
    main_resp = MagicMock(status_code=200, text=main_src)
    main_resp.raise_for_status = MagicMock()
    disc_resp = MagicMock(status_code=200, text=disc_src)
    disc_resp.raise_for_status = MagicMock()
    sim_resp = MagicMock(status_code=200, text=sim_src)
    sim_resp.raise_for_status = MagicMock()
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.get = AsyncMock(side_effect=[main_resp, disc_resp, sim_resp])
    return client


@pytest.mark.asyncio
async def test_install_python_driver_pulls_both_companions(driver_repo, monkeypatch):
    monkeypatch.setattr(
        "server.drivers.driver_loader.load_python_driver_file",
        lambda p: _fake_driver_class("chazy_control_pro"),
    )
    body = CommunityDriverInstallRequest(
        driver_id="chazy_control_pro",
        file_url="https://raw.githubusercontent.com/open-avc/openavc-drivers/main/switchers/chazy_control_pro.py",
    )
    client = _mock_three("# main\n", "# discovery\n", "# sim\n")
    with patch("httpx.AsyncClient", return_value=client):
        result = await install_community_driver(body)

    assert result["status"] == "installed"
    assert (driver_repo / "chazy_control_pro.py").exists()
    assert (driver_repo / "chazy_control_pro_discovery.py").read_text(encoding="utf-8") == "# discovery\n"
    assert (driver_repo / "chazy_control_pro_sim.py").read_text(encoding="utf-8") == "# sim\n"


@pytest.mark.asyncio
async def test_install_python_driver_tolerates_missing_companions(driver_repo, monkeypatch):
    monkeypatch.setattr(
        "server.drivers.driver_loader.load_python_driver_file",
        lambda p: _fake_driver_class("solo_driver"),
    )
    body = CommunityDriverInstallRequest(
        driver_id="solo_driver",
        file_url="https://raw.githubusercontent.com/open-avc/openavc-drivers/main/utility/solo_driver.py",
    )
    main_resp = MagicMock(status_code=200, text="# main\n")
    main_resp.raise_for_status = MagicMock()
    disc_404 = MagicMock(status_code=404)
    sim_404 = MagicMock(status_code=404)
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.get = AsyncMock(side_effect=[main_resp, disc_404, sim_404])
    with patch("httpx.AsyncClient", return_value=client):
        result = await install_community_driver(body)

    assert result["status"] == "installed"
    assert (driver_repo / "solo_driver.py").exists()
    assert not (driver_repo / "solo_driver_discovery.py").exists()
    assert not (driver_repo / "solo_driver_sim.py").exists()


# --- upload_driver_bundle --------------------------------------------------


@pytest.mark.asyncio
async def test_bundle_round_trip_lands_all_files(driver_repo, monkeypatch):
    monkeypatch.setattr(
        "server.drivers.driver_loader.load_python_driver_file",
        lambda p: _fake_driver_class("chazy_control_pro"),
    )
    zip_bytes = _make_zip({
        "chazy_control_pro.py": b"# main\n",
        "chazy_control_pro_discovery.py": b"# discovery\n",
        "chazy_control_pro_sim.py": b"# sim\n",
    })
    req = _request_with_file("chazy_control_pro.zip", zip_bytes)
    result = await upload_driver_bundle(req)

    assert result["status"] == "uploaded"
    assert result["driver_id"] == "chazy_control_pro"
    assert set(result["files"]) == {
        "chazy_control_pro.py",
        "chazy_control_pro_discovery.py",
        "chazy_control_pro_sim.py",
    }
    for name in result["files"]:
        assert (driver_repo / name).exists()


@pytest.mark.asyncio
async def test_bundle_rejects_non_zip(driver_repo):
    req = _request_with_file("driver.py", b"# not a zip")
    with pytest.raises(Exception) as exc:
        await upload_driver_bundle(req)
    assert getattr(exc.value, "status_code", None) == 422


@pytest.mark.asyncio
async def test_bundle_rejects_bad_zip_bytes(driver_repo):
    req = _request_with_file("bundle.zip", b"these are not zip bytes")
    with pytest.raises(Exception) as exc:
        await upload_driver_bundle(req)
    assert getattr(exc.value, "status_code", None) == 422


@pytest.mark.asyncio
async def test_bundle_rejects_companion_only(driver_repo):
    zip_bytes = _make_zip({"foo_discovery.py": b"# c\n", "foo_sim.py": b"# s\n"})
    req = _request_with_file("foo.zip", zip_bytes)
    with pytest.raises(Exception) as exc:
        await upload_driver_bundle(req)
    assert getattr(exc.value, "status_code", None) == 422
    assert "no main driver" in exc.value.detail.lower()
    assert list(driver_repo.glob("*")) == []  # nothing written


@pytest.mark.asyncio
async def test_bundle_rejects_disallowed_file_type(driver_repo):
    zip_bytes = _make_zip({"foo.py": b"# m\n", "evil.sh": b"rm -rf /\n"})
    req = _request_with_file("foo.zip", zip_bytes)
    with pytest.raises(Exception) as exc:
        await upload_driver_bundle(req)
    assert getattr(exc.value, "status_code", None) == 422
    assert list(driver_repo.glob("*")) == []  # rejected before any write


@pytest.mark.asyncio
async def test_bundle_rejects_multiple_mains(driver_repo):
    zip_bytes = _make_zip({"foo.py": b"# a\n", "bar.py": b"# b\n"})
    req = _request_with_file("foo.zip", zip_bytes)
    with pytest.raises(Exception) as exc:
        await upload_driver_bundle(req)
    assert getattr(exc.value, "status_code", None) == 422
    assert "more than one driver" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_bundle_strips_directory_paths(driver_repo, monkeypatch):
    # Entries with directory components are reduced to their basename, so a
    # traversal attempt lands flat in driver_repo (and only if it's a valid
    # driver-file name).
    monkeypatch.setattr(
        "server.drivers.driver_loader.load_python_driver_file",
        lambda p: _fake_driver_class("foo"),
    )
    zip_bytes = _make_zip({"nested/dir/foo.py": b"# m\n"})
    req = _request_with_file("foo.zip", zip_bytes)
    result = await upload_driver_bundle(req)
    assert (driver_repo / "foo.py").exists()
    assert not (driver_repo / "nested").exists()
    assert result["files"] == ["foo.py"]


# --- export_python_driver_bundle ------------------------------------------


@pytest.mark.asyncio
async def test_export_zips_driver_and_companions(driver_repo):
    (driver_repo / "chazy_control_pro.py").write_text("# main\n", encoding="utf-8")
    (driver_repo / "chazy_control_pro_discovery.py").write_text("# disc\n", encoding="utf-8")
    (driver_repo / "chazy_control_pro_sim.py").write_text("# sim\n", encoding="utf-8")

    resp = await export_python_driver_bundle("chazy_control_pro")
    assert resp.media_type == "application/zip"
    assert "chazy_control_pro.zip" in resp.headers["Content-Disposition"]

    names = zipfile.ZipFile(io.BytesIO(resp.body)).namelist()
    assert set(names) == {
        "chazy_control_pro.py",
        "chazy_control_pro_discovery.py",
        "chazy_control_pro_sim.py",
    }


@pytest.mark.asyncio
async def test_export_only_main_when_no_companions(driver_repo):
    (driver_repo / "solo.py").write_text("# main\n", encoding="utf-8")
    resp = await export_python_driver_bundle("solo")
    names = zipfile.ZipFile(io.BytesIO(resp.body)).namelist()
    assert names == ["solo.py"]


@pytest.mark.asyncio
async def test_export_missing_driver_404(driver_repo):
    with pytest.raises(Exception) as exc:
        await export_python_driver_bundle("nonexistent")
    assert getattr(exc.value, "status_code", None) == 404
