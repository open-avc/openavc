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

import httpx
import pytest
from fastapi import HTTPException

from server.api.routes.drivers import (
    _try_download_python_companion,
    install_community_driver,
    uninstall_driver,
    update_driver,
    upload_driver_bundle,
)
from server.api.routes.python_drivers import (
    delete_python_driver,
    export_python_driver_bundle,
)
from server.api.models import CommunityDriverInstallRequest


# --- fixtures --------------------------------------------------------------


@pytest.fixture
def driver_repo(tmp_path, monkeypatch):
    """Point both repo-path accessors at a tmp dir.

    ``upload_driver_bundle`` / install use ``routes.drivers._get_driver_repo_dir``;
    the Python-driver routes resolve through ``_safe_driver_path``, which reads
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
    # Both route modules import the registry functions at module scope, so
    # each namespace needs its own patch (same reason the engine is patched
    # twice below).
    for module in ("drivers", "python_drivers"):
        monkeypatch.setattr(
            f"server.api.routes.{module}.register_driver", lambda cls: None
        )
        monkeypatch.setattr(
            f"server.api.routes.{module}.unregister_driver", lambda driver_id: None
        )
    monkeypatch.setattr(
        "server.api.discovery.refresh_all_device_matches",
        AsyncMock(return_value=None),
        raising=False,
    )
    fake_engine = MagicMock()
    fake_engine.project = None  # uninstall skips the in-use device check
    fake_engine.devices.retry_all_orphans = AsyncMock(return_value=[])
    fake_engine.devices.get_devices_using_driver = lambda driver_id: []
    # Both route modules under test here read the engine through their own
    # module namespace — install/uninstall from routes.drivers, delete from
    # routes.python_drivers — so a single patch would leave one of them
    # reaching for the real (unstarted) engine.
    monkeypatch.setattr(
        "server.api.routes.drivers._get_engine", lambda: fake_engine
    )
    monkeypatch.setattr(
        "server.api.routes.python_drivers._get_engine", lambda: fake_engine
    )

    # Skip the install path's catalog-hash lookup. These tests drive the
    # companion fetch with a queue of mocked responses, and the real lookup
    # would fetch the catalog through that same mock, consuming one. Integrity
    # behaviour has its own tests in test_community_artifact_integrity.py.
    from server.utils.community_integrity import ArtifactHashes

    async def _no_hashes(driver_id):
        return ArtifactHashes(f"Driver '{driver_id}'", None, source="catalog")

    monkeypatch.setattr("server.api.routes.drivers._catalog_hashes", _no_hashes)
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
    resp.content = resp.text.encode("utf-8")
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
    assert out.path == tmp_path / "foo_discovery.py"
    assert out.path.read_text(encoding="utf-8") == "async def probe(ctx): pass\n"
    assert out.published is True


@pytest.mark.asyncio
async def test_companion_reports_not_published_on_404(tmp_path):
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
    assert out.path is None
    # The one case update may act on: a definite 404 means this version
    # ships without the companion, so a stale local copy should be removed.
    assert out.published is False
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
    assert out.path is None
    # Not a 404 — we never asked. `published` stays True so update leaves any
    # local file alone rather than deleting on a name it refused to fetch.
    assert out.published is True


@pytest.mark.asyncio
async def test_companion_rejects_off_allowlist_host(tmp_path):
    out = await _try_download_python_companion(
        main_url="https://attacker.example/switchers/foo.py",
        companion_filename="foo_discovery.py",
        driver_repo=tmp_path,
    )
    assert out.path is None
    assert out.published is True


# --- install pulls Python companions --------------------------------------


def _mock_three(main_src: str, disc_src: str, sim_src: str) -> MagicMock:
    main_resp = MagicMock(status_code=200, text=main_src)
    main_resp.content = main_src.encode("utf-8")
    main_resp.raise_for_status = MagicMock()
    disc_resp = MagicMock(status_code=200, text=disc_src)
    disc_resp.content = disc_src.encode("utf-8")
    disc_resp.raise_for_status = MagicMock()
    sim_resp = MagicMock(status_code=200, text=sim_src)
    sim_resp.content = sim_src.encode("utf-8")
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
    main_resp.content = main_resp.text.encode("utf-8")
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


# --- delete / uninstall remove companions ---------------------------------


@pytest.mark.asyncio
async def test_delete_removes_companions(driver_repo):
    (driver_repo / "foo.py").write_text("# m\n", encoding="utf-8")
    (driver_repo / "foo_discovery.py").write_text("# d\n", encoding="utf-8")
    (driver_repo / "foo_sim.py").write_text("# s\n", encoding="utf-8")

    result = await delete_python_driver("foo")

    assert result["status"] == "deleted"
    assert not (driver_repo / "foo.py").exists()
    assert not (driver_repo / "foo_discovery.py").exists()
    assert not (driver_repo / "foo_sim.py").exists()
    assert set(result["removed_companions"]) == {"foo_discovery.py", "foo_sim.py"}


@pytest.mark.asyncio
async def test_delete_without_companions_is_clean(driver_repo):
    (driver_repo / "solo.py").write_text("# m\n", encoding="utf-8")
    result = await delete_python_driver("solo")
    assert result["status"] == "deleted"
    assert result["removed_companions"] == []


@pytest.mark.asyncio
async def test_uninstall_python_driver_removes_companions(driver_repo):
    (driver_repo / "foo.py").write_text("# m\n", encoding="utf-8")
    (driver_repo / "foo_discovery.py").write_text("# d\n", encoding="utf-8")
    (driver_repo / "foo_sim.py").write_text("# s\n", encoding="utf-8")

    result = await uninstall_driver("foo")

    assert result["status"] == "uninstalled"
    assert not (driver_repo / "foo.py").exists()
    assert not (driver_repo / "foo_discovery.py").exists()
    assert not (driver_repo / "foo_sim.py").exists()


# --- update refreshes Python companions ------------------------------------
#
# Install fetches a Python driver's `_discovery.py` / `_sim.py` siblings;
# update did not, so new driver code landed beside the previous version's
# companions. The simulator is the one that bites: it keeps answering with the
# old protocol while the driver speaks the new one, so a project tested against
# it passes on a stale answer.


def _update_client(*responses):
    """A client whose .get answers the queued responses in order."""
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.get = AsyncMock(side_effect=list(responses))
    return client


def _ok(text: str) -> MagicMock:
    r = MagicMock(status_code=200, text=text)
    r.content = text.encode("utf-8")
    r.raise_for_status = MagicMock()
    return r


def _missing() -> MagicMock:
    """A definite 404 — the catalog says this version has no such companion."""
    return MagicMock(status_code=404)


def _seed_installed_python_driver(repo, *, sim: str = "SIM = 1\n",
                                  disc: str | None = "DISC = 1\n"):
    (repo / "foo.py").write_text("class Foo:\n    DRIVER_INFO = {'id': 'foo'}\n",
                                 encoding="utf-8")
    (repo / "foo_sim.py").write_text(sim, encoding="utf-8")
    if disc is not None:
        (repo / "foo_discovery.py").write_text(disc, encoding="utf-8")


_UPDATE_URL = (
    "https://raw.githubusercontent.com/open-avc/openavc-drivers/main/switchers/foo.py"
)


async def _run_update(monkeypatch, repo, client):
    monkeypatch.setattr(
        "server.drivers.driver_loader.load_python_driver_file",
        lambda p: type("D", (), {"DRIVER_INFO": {"id": "foo"}}),
    )
    req = MagicMock()
    req.json = AsyncMock(return_value={"file_url": _UPDATE_URL})
    with patch("httpx.AsyncClient", return_value=client):
        return await update_driver("foo", req)


@pytest.mark.asyncio
async def test_update_refreshes_python_companions(driver_repo, monkeypatch):
    repo = driver_repo
    _seed_installed_python_driver(repo)
    client = _update_client(
        _ok("class Foo:\n    DRIVER_INFO = {'id': 'foo'}\n    V = 2\n"),
        _ok("DISC = 2\n"),
        _ok("SIM = 2\n"),
    )
    result = await _run_update(monkeypatch, repo, client)

    assert result["status"] == "updated"
    assert (repo / "foo_sim.py").read_text(encoding="utf-8") == "SIM = 2\n"
    assert (repo / "foo_discovery.py").read_text(encoding="utf-8") == "DISC = 2\n"


@pytest.mark.asyncio
async def test_update_removes_a_companion_the_new_version_dropped(
    driver_repo, monkeypatch
):
    # A 404 is the one answer that means "this version ships without it". The
    # stale copy has to go, or it outlives the driver it belonged to.
    repo = driver_repo
    _seed_installed_python_driver(repo)
    client = _update_client(
        _ok("class Foo:\n    DRIVER_INFO = {'id': 'foo'}\n"),
        _missing(),   # _discovery.py dropped upstream
        _ok("SIM = 2\n"),
    )
    result = await _run_update(monkeypatch, repo, client)

    assert result["status"] == "updated"
    assert not (repo / "foo_discovery.py").exists()
    assert (repo / "foo_sim.py").read_text(encoding="utf-8") == "SIM = 2\n"


@pytest.mark.asyncio
async def test_update_keeps_a_companion_when_the_fetch_merely_fails(
    driver_repo, monkeypatch
):
    # The failure mode worth guarding: a transport error is NOT a 404. Treating
    # the two alike would delete a perfectly good simulator every time the
    # network hiccuped mid-update.
    repo = driver_repo
    _seed_installed_python_driver(repo)
    client = _update_client(
        _ok("class Foo:\n    DRIVER_INFO = {'id': 'foo'}\n"),
        httpx.RequestError("connection reset"),
        httpx.RequestError("connection reset"),
    )
    result = await _run_update(monkeypatch, repo, client)

    assert result["status"] == "updated"
    assert (repo / "foo_sim.py").read_text(encoding="utf-8") == "SIM = 1\n"
    assert (repo / "foo_discovery.py").read_text(encoding="utf-8") == "DISC = 1\n"


@pytest.mark.asyncio
async def test_update_refuses_a_companion_that_fails_the_catalog_hash(
    driver_repo, monkeypatch
):
    # Same rule as install: a companion whose bytes don't match what the
    # catalog publishes stops the update rather than landing quietly.
    import hashlib

    from server.utils.community_integrity import ArtifactHashes

    repo = driver_repo
    _seed_installed_python_driver(repo, disc=None)
    main_src = "class Foo:\n    DRIVER_INFO = {'id': 'foo'}\n"

    async def _hashes(driver_id):
        return ArtifactHashes(
            f"Driver '{driver_id}'",
            {
                "switchers/foo.py": hashlib.sha256(main_src.encode()).hexdigest(),
                "switchers/foo_sim.py": hashlib.sha256(b"SIM = 2\n").hexdigest(),
            },
            source="the community driver catalog",
        )

    monkeypatch.setattr("server.api.routes.drivers._catalog_hashes", _hashes)
    client = _update_client(
        _ok(main_src),
        _missing(),
        _ok("SIM = 999  # not what the catalog publishes\n"),
    )
    with pytest.raises(HTTPException) as exc:
        await _run_update(monkeypatch, repo, client)
    assert exc.value.status_code == 502
    assert "does not match" in exc.value.detail


@pytest.mark.asyncio
async def test_update_of_a_yaml_driver_fetches_no_python_companions(
    driver_repo, monkeypatch
):
    # Regression guard: YAML drivers keep the declared-companion path they
    # already had, and must not start convention-fetching siblings.
    repo = driver_repo
    yaml_text = "id: foo\nname: Foo\ntransport: tcp\n"
    (repo / "foo.avcdriver").write_text(yaml_text, encoding="utf-8")
    monkeypatch.setattr(
        "server.drivers.driver_loader.load_driver_file",
        lambda p: {"id": "foo", "name": "Foo", "transport": "tcp"},
    )
    monkeypatch.setattr(
        "server.api.routes.drivers.create_configurable_driver_class",
        lambda d: type("D", (), {"DRIVER_INFO": {"id": "foo"}}),
        raising=False,
    )
    client = _update_client(_ok(yaml_text))
    req = MagicMock()
    req.json = AsyncMock(return_value={
        "file_url": "https://raw.githubusercontent.com/open-avc/openavc-drivers"
                    "/main/switchers/foo.avcdriver",
    })
    with patch("httpx.AsyncClient", return_value=client):
        result = await update_driver("foo", req)

    assert result["status"] == "updated"
    # Exactly one fetch: the YAML itself. No sibling probing.
    assert client.get.call_count == 1
