"""Community driver / plugin installs check what they downloaded.

Two properties are under test, and neither is about hashing:

1. **Source pinning.** A community driver is arbitrary in-process code, so
   "the URL is on github.com" was never a source check — any repo on that host
   passed it. Installs must be pinned to the one catalog repo.
2. **Nothing unverified reaches the disk.** The check has to happen on the
   bytes in memory. A driver written first and checked afterwards is a driver
   the loader can already find.

The device names here are invented (`acme_widget`); these exercise the platform,
not any real driver.
"""

from __future__ import annotations

import hashlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from openavc.api.models import CommunityDriverInstallRequest
from openavc.api.routes.drivers import install_community_driver

CATALOG = "https://raw.githubusercontent.com/open-avc/openavc-drivers/main"

YAML_BODY = """id: acme_widget
name: Acme Widget
transport: tcp
commands:
  power_on:
    label: 'On'
    send: "P\\r"
    params: {}
responses:
  - match: OK
    mappings:
      - group: 0
        state: ok
state_variables:
  ok:
    type: string
    label: OK
"""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _resp(text: str) -> MagicMock:
    """A response mock whose .content matches .text, as a real one does."""
    r = MagicMock()
    r.text = text
    r.content = text.encode("utf-8")
    r.status_code = 200
    r.raise_for_status = MagicMock()
    return r


def _client(*responses: MagicMock) -> MagicMock:
    c = AsyncMock()
    c.__aenter__ = AsyncMock(return_value=c)
    c.__aexit__ = AsyncMock(return_value=False)
    c.get = AsyncMock(side_effect=list(responses))
    return c


@pytest.fixture
def driver_repo(tmp_path, monkeypatch):
    repo = tmp_path / "driver_repo"
    repo.mkdir()
    monkeypatch.setattr(
        "openavc.api.routes.drivers._get_driver_repo_dir", lambda: repo
    )
    return repo


@pytest.fixture(autouse=True)
def silence_engine(monkeypatch):
    monkeypatch.setattr(
        "openavc.api.routes.drivers.refresh_all_device_matches",
        AsyncMock(return_value=None),
        raising=False,
    )
    monkeypatch.setattr(
        "openavc.api.discovery.refresh_all_device_matches",
        AsyncMock(return_value=None),
        raising=False,
    )
    monkeypatch.setattr(
        "openavc.api.routes.drivers.register_driver", lambda cls: None
    )


def _catalog_says(monkeypatch, files):
    """Pin what the community catalog publishes for acme_widget."""
    from openavc.utils.community_integrity import ArtifactHashes

    async def _hashes(driver_id):
        return ArtifactHashes(
            f"Driver '{driver_id}'", files, source="the community driver catalog"
        )

    monkeypatch.setattr("openavc.api.routes.drivers._catalog_hashes", _hashes)


# --- Source pinning --------------------------------------------------------


@pytest.mark.asyncio
async def test_install_refuses_a_driver_from_another_github_repo(driver_repo, monkeypatch):
    # The gap this closes: the host allowlist accepted any GitHub repo, and a
    # .py driver is imported and registered. Reachable without a person in the
    # loop, via discovery install-and-match and the cloud AI's install tool.
    _catalog_says(monkeypatch, None)
    body = CommunityDriverInstallRequest(
        driver_id="acme_widget",
        file_url="https://raw.githubusercontent.com/attacker/evil/main/audio/acme_widget.py",
    )
    with pytest.raises(HTTPException) as exc:
        await install_community_driver(body)
    assert exc.value.status_code == 422
    assert "open-avc/openavc-drivers" in exc.value.detail
    assert list(driver_repo.iterdir()) == []


@pytest.mark.asyncio
async def test_install_refuses_a_plaintext_http_url(driver_repo, monkeypatch):
    _catalog_says(monkeypatch, None)
    body = CommunityDriverInstallRequest(
        driver_id="acme_widget",
        file_url="http://raw.githubusercontent.com/open-avc/openavc-drivers/main/audio/acme_widget.avcdriver",
    )
    with pytest.raises(HTTPException) as exc:
        await install_community_driver(body)
    assert exc.value.status_code == 422
    assert "https" in exc.value.detail
    assert list(driver_repo.iterdir()) == []


# --- Hash enforcement ------------------------------------------------------


@pytest.mark.asyncio
async def test_install_accepts_bytes_matching_the_catalog(driver_repo, monkeypatch):
    _catalog_says(
        monkeypatch,
        {"audio/acme_widget.avcdriver": _sha(YAML_BODY.encode("utf-8"))},
    )
    url = f"{CATALOG}/audio/acme_widget.avcdriver"
    with patch("httpx.AsyncClient", return_value=_client(_resp(YAML_BODY))):
        result = await install_community_driver(
            CommunityDriverInstallRequest(driver_id="acme_widget", file_url=url)
        )
    assert result["status"] == "installed"
    installed = driver_repo / "acme_widget.avcdriver"
    assert installed.read_bytes() == YAML_BODY.encode("utf-8")


@pytest.mark.asyncio
async def test_install_refuses_bytes_that_do_not_match_and_writes_nothing(
    driver_repo, monkeypatch
):
    # The whole point of checking before writing: a refused driver must not be
    # sitting in driver_repo/ where the loader would pick it up on restart.
    _catalog_says(
        monkeypatch,
        {"audio/acme_widget.avcdriver": _sha(YAML_BODY.encode("utf-8"))},
    )
    tampered = YAML_BODY + "# extra\n"
    url = f"{CATALOG}/audio/acme_widget.avcdriver"
    with patch("httpx.AsyncClient", return_value=_client(_resp(tampered))):
        with pytest.raises(HTTPException) as exc:
            await install_community_driver(
                CommunityDriverInstallRequest(driver_id="acme_widget", file_url=url)
            )
    assert exc.value.status_code == 502
    assert "does not match" in exc.value.detail
    assert list(driver_repo.iterdir()) == []


@pytest.mark.asyncio
async def test_install_refuses_a_driver_the_catalog_does_not_publish(
    driver_repo, monkeypatch
):
    # The catalog has hashes, but not for this path — an artifact appearing at a
    # URL the catalog never listed.
    _catalog_says(monkeypatch, {"audio/something_else.avcdriver": _sha(b"x")})
    url = f"{CATALOG}/audio/acme_widget.avcdriver"
    with patch("httpx.AsyncClient", return_value=_client(_resp(YAML_BODY))):
        with pytest.raises(HTTPException) as exc:
            await install_community_driver(
                CommunityDriverInstallRequest(driver_id="acme_widget", file_url=url)
            )
    assert exc.value.status_code == 502
    assert "not listed" in exc.value.detail
    assert list(driver_repo.iterdir()) == []


@pytest.mark.asyncio
async def test_install_proceeds_when_the_catalog_publishes_no_hashes(
    driver_repo, monkeypatch
):
    # Deliberate: a catalog that predates the hashes must stay installable.
    # This is the honest state of a control not yet backed by a signature.
    _catalog_says(monkeypatch, None)
    url = f"{CATALOG}/audio/acme_widget.avcdriver"
    with patch("httpx.AsyncClient", return_value=_client(_resp(YAML_BODY))):
        result = await install_community_driver(
            CommunityDriverInstallRequest(driver_id="acme_widget", file_url=url)
        )
    assert result["status"] == "installed"


@pytest.mark.asyncio
async def test_companion_with_wrong_bytes_refused_and_rolled_back(
    driver_repo, monkeypatch
):
    # A YAML driver's companion is required code. A bad companion must fail the
    # install AND take the already-written YAML back out with it.
    yaml_text = YAML_BODY + (
        "discovery:\n  python:\n    file: ./acme_widget_discovery.py\n"
    )
    companion = "async def probe(ctx):\n    return None\n"
    _catalog_says(
        monkeypatch,
        {
            "audio/acme_widget.avcdriver": _sha(yaml_text.encode("utf-8")),
            "audio/acme_widget_discovery.py": _sha(companion.encode("utf-8")),
        },
    )
    url = f"{CATALOG}/audio/acme_widget.avcdriver"
    tampered_companion = companion + "import os\n"
    with patch(
        "httpx.AsyncClient",
        side_effect=[_client(_resp(yaml_text)), _client(_resp(tampered_companion))],
    ):
        with pytest.raises(HTTPException) as exc:
            await install_community_driver(
                CommunityDriverInstallRequest(driver_id="acme_widget", file_url=url)
            )
    assert exc.value.status_code == 502
    assert list(driver_repo.iterdir()) == []


@pytest.mark.asyncio
async def test_optional_python_companion_with_wrong_bytes_is_not_swallowed(
    driver_repo, monkeypatch
):
    # _try_download_python_companion treats fetch failures as "ships without
    # one" and moves on. A hash mismatch must NOT go down that path: optional
    # means it may be absent, not that a wrong copy may be installed.
    main = "class AcmeWidget:\n    DRIVER_INFO = {'id': 'acme_widget'}\n"
    sim = "SIM = True\n"
    _catalog_says(
        monkeypatch,
        {
            "audio/acme_widget.py": _sha(main.encode("utf-8")),
            "audio/acme_widget_discovery.py": _sha(sim.encode("utf-8")),
        },
    )
    monkeypatch.setattr(
        "openavc.drivers.driver_loader.load_python_driver_file",
        lambda p: type("D", (), {"DRIVER_INFO": {"id": "acme_widget"}}),
    )
    monkeypatch.setattr(
        "openavc.api.routes.drivers._enforce_driver_id_match",
        lambda *a, **k: None,
    )
    url = f"{CATALOG}/audio/acme_widget.py"
    with patch(
        "httpx.AsyncClient",
        side_effect=[_client(_resp(main)), _client(_resp("import os\n"))],
    ):
        with pytest.raises(HTTPException) as exc:
            await install_community_driver(
                CommunityDriverInstallRequest(driver_id="acme_widget", file_url=url)
            )
    assert exc.value.status_code == 502


@pytest.mark.asyncio
async def test_catalog_hashes_are_not_taken_from_the_request(driver_repo):
    # There is deliberately no way for a caller to supply a hash: it would be
    # checked against bytes the same caller chose, which verifies nothing.
    assert "sha256" not in CommunityDriverInstallRequest.model_fields
    assert "files" not in CommunityDriverInstallRequest.model_fields


# --- Plugins ---------------------------------------------------------------
#
# A plugin installs as a directory whose file list comes from the GitHub
# Contents API. That list is attacker-supplied in this threat model, which is
# why per-file hashes alone aren't enough: the manifest also has to say that
# nothing extra came along and nothing went missing.

import openavc.core.plugin_installer as pi  # noqa: E402

PLUGIN_CATALOG = "https://raw.githubusercontent.com/open-avc/openavc-plugins/main"

PLUGIN_SRC = (
    "class AcmePlugin:\n"
    "    PLUGIN_INFO = {\n"
    '        "id": "acme_thing",\n'
    '        "name": "Acme Thing",\n'
    '        "version": "1.0.0",\n'
    '        "author": "Test",\n'
    '        "description": "test",\n'
    '        "category": "utility",\n'
    '        "license": "MIT",\n'
    '        "capabilities": [],\n'
    "    }\n"
    "    async def start(self, api): pass\n"
    "    async def stop(self): pass\n"
)


@pytest.fixture
def plugin_dirs(tmp_path, monkeypatch):
    data_dir = tmp_path / "_plugin_data"
    data_dir.mkdir()
    monkeypatch.setattr(pi, "PLUGIN_REPO_DIR", tmp_path)
    monkeypatch.setattr(pi, "PLUGIN_DATA_DIR", data_dir)
    registry_before = dict(pi._PLUGIN_CLASS_REGISTRY)
    pi._plugin_op_locks.clear()
    yield tmp_path
    pi._plugin_op_locks.clear()
    pi._PLUGIN_CLASS_REGISTRY.clear()
    pi._PLUGIN_CLASS_REGISTRY.update(registry_before)


def _json(payload) -> MagicMock:
    r = MagicMock()
    r.raise_for_status = MagicMock()
    r.json.return_value = payload
    return r


def _stream(content: bytes) -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.headers = {}

    async def _aiter():
        yield content

    resp.aiter_bytes = _aiter
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=resp)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


def _plugin_client(manifest, listing, *bodies: bytes) -> MagicMock:
    c = AsyncMock()
    c.__aenter__ = AsyncMock(return_value=c)
    c.__aexit__ = AsyncMock(return_value=False)
    c.get = AsyncMock(side_effect=[_json(manifest), _json(listing)])
    c.stream = MagicMock(side_effect=[_stream(b) for b in bodies])
    return c


def _entry(name: str) -> dict:
    return {
        "name": name,
        "type": "file",
        "download_url": f"{PLUGIN_CATALOG}/utility/acme_thing/{name}",
    }


async def _install(client):
    with patch("openavc.core.plugin_installer.httpx.AsyncClient", return_value=client):
        with patch("openavc.core.plugin_installer._install_pip_deps", new_callable=AsyncMock):
            with patch("openavc.core.plugin_installer._install_native_deps", new_callable=AsyncMock):
                return await pi.install_plugin(
                    "acme_thing", f"{PLUGIN_CATALOG}/utility/acme_thing"
                )


@pytest.mark.asyncio
async def test_plugin_install_accepts_matching_files(plugin_dirs):
    src = PLUGIN_SRC.encode("utf-8")
    manifest = {
        "plugins": {
            "acme_thing": {
                "files": {"utility/acme_thing/acme_thing_plugin.py": _sha(src)}
            }
        }
    }
    result = await _install(
        _plugin_client(manifest, [_entry("acme_thing_plugin.py")], src)
    )
    assert result["status"] == "installed"
    assert (plugin_dirs / "acme_thing" / "acme_thing_plugin.py").exists()


@pytest.mark.asyncio
async def test_plugin_install_refuses_an_altered_file(plugin_dirs):
    src = PLUGIN_SRC.encode("utf-8")
    manifest = {
        "plugins": {
            "acme_thing": {
                "files": {"utility/acme_thing/acme_thing_plugin.py": _sha(src)}
            }
        }
    }
    tampered = src + b"import os\n"
    with pytest.raises(Exception) as exc:
        await _install(
            _plugin_client(manifest, [_entry("acme_thing_plugin.py")], tampered)
        )
    assert "does not match" in str(exc.value)
    # Partial install cleaned up — nothing left for the loader to find.
    assert not (plugin_dirs / "acme_thing").exists()


@pytest.mark.asyncio
async def test_plugin_install_refuses_an_injected_extra_file(plugin_dirs):
    # The case per-file hashes alone don't cover. The listing offers a file the
    # manifest never mentions, so there is no hash for it to fail — it has to be
    # refused for being unlisted.
    src = PLUGIN_SRC.encode("utf-8")
    manifest = {
        "plugins": {
            "acme_thing": {
                "files": {"utility/acme_thing/acme_thing_plugin.py": _sha(src)}
            }
        }
    }
    with pytest.raises(Exception) as exc:
        await _install(
            _plugin_client(
                manifest,
                [_entry("acme_thing_plugin.py"), _entry("backdoor.py")],
                src,
                b"import os\n",
            )
        )
    assert "not listed" in str(exc.value)
    assert not (plugin_dirs / "acme_thing").exists()


@pytest.mark.asyncio
async def test_plugin_install_refuses_a_dropped_file(plugin_dirs):
    # The other direction: every file that arrives verifies, but the listing
    # quietly left one out.
    src = PLUGIN_SRC.encode("utf-8")
    manifest = {
        "plugins": {
            "acme_thing": {
                "files": {
                    "utility/acme_thing/acme_thing_plugin.py": _sha(src),
                    "utility/acme_thing/helper.py": _sha(b"HELPER = 1\n"),
                }
            }
        }
    }
    with pytest.raises(Exception) as exc:
        await _install(
            _plugin_client(manifest, [_entry("acme_thing_plugin.py")], src)
        )
    assert "never delivered" in str(exc.value)
    assert not (plugin_dirs / "acme_thing").exists()


@pytest.mark.asyncio
async def test_plugin_install_proceeds_when_the_manifest_has_no_entry(plugin_dirs):
    result = await _install(
        _plugin_client(
            {"plugins": {}},
            [_entry("acme_thing_plugin.py")],
            PLUGIN_SRC.encode("utf-8"),
        )
    )
    assert result["status"] == "installed"
