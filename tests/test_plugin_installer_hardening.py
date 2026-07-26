"""Security/reliability hardening tests for the plugin installer.

Covers the audit findings closed in this group:
  H-045 install accepts only the official catalog repo (not "any GitHub URL")
  H-046 native-dependency download URL is SSRF-guarded
  H-047 pip dependency strings can't inject pip args / VCS / URL installs
  H-048 update_plugin rolls back to the working version on failure
  M-086 per-plugin lock serializes concurrent install/update/uninstall
  M-087 directory-install per-file download_url is re-validated against catalog
  M-088 directory-install entry names can't traverse out of the plugin dir
  M-089 every download path enforces size / file-count / decompression caps
"""

import asyncio
import zipfile
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import server.core.plugin_installer as pi
from server.core.plugin_installer import (
    _check_zip_bomb,
    _DownloadBudget,
    _download_capped,
    _is_safe_entry_name,
    _is_safe_requirement,
    _validate_catalog_url,
    _validate_download_url,
    install_plugin,
    update_plugin,
)

CATALOG = "https://raw.githubusercontent.com/open-avc/openavc-plugins/main"


# ──── Fixtures / helpers ────


@pytest.fixture(autouse=True)
def _patch_dirs(tmp_path, monkeypatch):
    """Redirect the plugin dirs to temp space and isolate the global registry +
    per-plugin lock table so tests don't bleed into each other."""
    data_dir = tmp_path / "_plugin_data"
    data_dir.mkdir()
    monkeypatch.setattr(pi, "PLUGIN_REPO_DIR", tmp_path)
    monkeypatch.setattr(pi, "PLUGIN_DATA_DIR", data_dir)
    registry_before = dict(pi._PLUGIN_CLASS_REGISTRY)
    pi._plugin_op_locks.clear()
    yield
    pi._plugin_op_locks.clear()
    pi._PLUGIN_CLASS_REGISTRY.clear()
    pi._PLUGIN_CLASS_REGISTRY.update(registry_before)


def _plugin_src(pid: str, version: str = "1.0.0") -> str:
    return (
        "class ThePlugin:\n"
        "    PLUGIN_INFO = {\n"
        f'        "id": "{pid}",\n'
        f'        "name": "{pid}",\n'
        f'        "version": "{version}",\n'
        '        "author": "Test",\n'
        '        "description": "test",\n'
        '        "category": "utility",\n'
        '        "license": "MIT",\n'
        '        "capabilities": [],\n'
        "    }\n"
        "    async def start(self, api): pass\n"
        "    async def stop(self): pass\n"
    )


def _plugin_zip(pid: str, source: str) -> bytes:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"{pid}/{pid}_plugin.py", source)
    return buf.getvalue()


def _make_stream_response(content: bytes = b"", *, headers=None, error=None):
    resp = MagicMock()
    resp.raise_for_status = MagicMock(side_effect=error) if error else MagicMock()
    resp.headers = headers or {}

    async def _aiter_bytes():
        yield content

    resp.aiter_bytes = _aiter_bytes
    return resp


def _stream_cm(resp):
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=resp)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


def _empty_manifest_response():
    """The manifest fetch every install now makes, answering "no hashes".

    Prepended to a test's client.get queue so the install still exercises the
    real fetch rather than a stub. Integrity behaviour itself is covered in
    test_community_artifact_integrity.py.
    """
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"plugins": {}}
    return resp


def _download_client(*, stream=None, get=None):
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    if stream is not None:
        items = stream if isinstance(stream, list) else [stream]
        cms = [
            _stream_cm(it if not isinstance(it, (bytes, bytearray))
                       else _make_stream_response(bytes(it)))
            for it in items
        ]
        client.stream = MagicMock(side_effect=cms)
    if get is not None:
        client.get = AsyncMock(side_effect=get if isinstance(get, list) else [get])
    return client


def _json_response(payload):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = payload
    return resp


# ═══════════════════════════════════════════════════════════
#  H-045 — catalog-only plugin URL
# ═══════════════════════════════════════════════════════════


class TestCatalogUrl:

    @pytest.mark.parametrize("url", [
        f"{CATALOG}/some_plugin.zip",
        f"{CATALOG}/some_plugin_plugin.py",
        f"{CATALOG}/plugins/some_plugin",
        "https://api.github.com/repos/open-avc/openavc-plugins/contents/x?ref=main",
        "https://github.com/open-avc/openavc-plugins/raw/main/x.py",
    ])
    def test_accepts_official_catalog(self, url):
        _validate_catalog_url(url)  # must not raise

    def test_rejects_other_github_repo(self):
        # Same GitHub host, attacker-controlled repo — the whole point of H-045.
        with pytest.raises(ValueError, match="open-avc/openavc-plugins|catalog"):
            _validate_catalog_url(
                "https://raw.githubusercontent.com/attacker/evil/main/x.py"
            )

    def test_rejects_non_github_host(self):
        with pytest.raises(ValueError, match="catalog|host"):
            _validate_catalog_url("https://evil.example/open-avc/openavc-plugins/x.py")

    def test_rejects_non_https(self):
        with pytest.raises(ValueError, match="https"):
            _validate_catalog_url(
                "http://raw.githubusercontent.com/open-avc/openavc-plugins/main/x.py"
            )

    def test_rejects_path_traversal(self):
        with pytest.raises(ValueError, match="path under|catalog"):
            _validate_catalog_url(
                "https://raw.githubusercontent.com/open-avc/openavc-plugins/main/../../attacker/x.py"
            )

    async def test_install_rejects_non_catalog_url(self, tmp_path):
        with pytest.raises(ValueError, match="open-avc/openavc-plugins|catalog"):
            await install_plugin(
                "evil", "https://raw.githubusercontent.com/attacker/evil/main/x.py"
            )
        assert not (tmp_path / "evil").exists()


# ═══════════════════════════════════════════════════════════
#  H-046 — native-dependency SSRF guard
# ═══════════════════════════════════════════════════════════


class TestDownloadUrlSSRF:

    def _patch_resolution(self, ip):
        loop = asyncio.get_event_loop()
        return patch.object(
            loop, "getaddrinfo",
            AsyncMock(return_value=[(2, 1, 6, "", (ip, 443))]),
        )

    async def test_rejects_non_https(self):
        with pytest.raises(ValueError, match="https"):
            await _validate_download_url("http://github.com/x/y.zip")

    @pytest.mark.parametrize("ip", ["169.254.169.254", "10.0.0.5", "192.168.1.10"])
    async def test_rejects_internal_address(self, ip):
        with self._patch_resolution(ip):
            with pytest.raises(ValueError, match="disallowed"):
                await _validate_download_url("https://attacker.test/x.zip")

    async def test_allows_public_address(self):
        with self._patch_resolution("140.82.112.3"):  # github.com
            await _validate_download_url(
                "https://github.com/open-avc/x/releases/download/v1/a.zip"
            )

    async def test_native_dep_archive_blocks_metadata(self, tmp_path):
        deps_dir = tmp_path / ".deps"
        deps_dir.mkdir()
        info = {"type": "zip", "url": "https://metadata.test/x.zip", "extract": "f"}
        with self._patch_resolution("169.254.169.254"):
            with pytest.raises(ValueError, match="disallowed"):
                await pi._install_native_dep_archive("dep", info, deps_dir)


# ═══════════════════════════════════════════════════════════
#  H-047 — pip dependency injection
# ═══════════════════════════════════════════════════════════


class TestSafeRequirement:

    @pytest.mark.parametrize("req", [
        "requests",
        "pillow>=10.0",
        "numpy==1.26.4",
        "uvicorn[standard]",
        "requests>=2,<3",
        "pkg~=1.2",
        "pywin32; sys_platform=='win32'",  # PEP 508 marker — safe, allowed
    ])
    def test_accepts_plain_requirements(self, req):
        assert _is_safe_requirement(req)

    @pytest.mark.parametrize("req", [
        "--index-url=http://evil/simple/",
        "-r requirements.txt",
        "-e .",
        "git+https://evil/repo",
        "pkg @ https://evil/x.whl",
        "http://evil/x.tar.gz",
        "file:///etc/passwd",
        "./local",
    ])
    def test_rejects_dangerous_specifiers(self, req):
        assert not _is_safe_requirement(req)

    async def test_install_pip_deps_rejects_injection(self, tmp_path):
        plugin_dir = tmp_path / "evilpip"
        plugin_dir.mkdir()
        (plugin_dir / "evilpip.py").write_text(
            'PLUGIN_INFO = {"id": "evilpip", '
            '"dependencies": ["--index-url=http://evil/simple/", "requests"]}\n',
            encoding="utf-8",
        )
        with patch("server.core.plugin_installer.subprocess.run") as mock_run:
            with pytest.raises(ValueError, match="Unsafe dependency"):
                await pi._install_pip_deps("evilpip", plugin_dir)
        mock_run.assert_not_called()  # never reached pip


# ═══════════════════════════════════════════════════════════
#  H-048 — update rollback
# ═══════════════════════════════════════════════════════════


class TestUpdateRollback:

    async def _install_v1(self, pid, version="1.0.0"):
        src = _plugin_src(pid, version)
        client = _download_client(stream=_plugin_zip(pid, src))
        with patch("server.core.plugin_installer.httpx.AsyncClient", return_value=client):
            result = await install_plugin(pid, f"{CATALOG}/{pid}.zip")
        assert result["status"] == "installed"
        return src

    async def test_rolls_back_on_download_failure(self, tmp_path):
        v1_src = await self._install_v1("updnet")
        assert "updnet" in pi._PLUGIN_CLASS_REGISTRY

        import httpx
        err = httpx.ConnectError("network down")
        client = _download_client(stream=_make_stream_response(error=err))
        with patch("server.core.plugin_installer.httpx.AsyncClient", return_value=client):
            result = await update_plugin("updnet", f"{CATALOG}/updnet.zip")

        assert result["status"] == "update_failed"
        assert result["rolled_back"] is True
        # Working version restored byte-for-byte + re-registered.
        restored = (tmp_path / "updnet" / "updnet_plugin.py").read_text(encoding="utf-8")
        assert restored == v1_src
        assert "updnet" in pi._PLUGIN_CLASS_REGISTRY
        assert not (tmp_path / ".updnet.update-bak").exists()

    async def test_rolls_back_on_broken_new_version(self, tmp_path):
        v1_src = await self._install_v1("updbad")
        # New version is syntactically broken -> _do_install returns load_failed.
        broken = _plugin_zip("updbad", "class Broken(\n  # missing paren\n")
        client = _download_client(stream=broken)
        with patch("server.core.plugin_installer.httpx.AsyncClient", return_value=client):
            result = await update_plugin("updbad", f"{CATALOG}/updbad.zip")

        assert result["status"] == "update_failed"
        assert result["rolled_back"] is True
        restored = (tmp_path / "updbad" / "updbad_plugin.py").read_text(encoding="utf-8")
        assert restored == v1_src
        assert "updbad" in pi._PLUGIN_CLASS_REGISTRY
        assert not (tmp_path / ".updbad.update-bak").exists()

    async def test_update_success_swaps_version(self, tmp_path):
        await self._install_v1("updok", "1.0.0")
        v2 = _download_client(stream=_plugin_zip("updok", _plugin_src("updok", "2.0.0")))
        with patch("server.core.plugin_installer.httpx.AsyncClient", return_value=v2):
            result = await update_plugin("updok", f"{CATALOG}/updok.zip")

        assert result["status"] == "installed"
        assert pi._PLUGIN_CLASS_REGISTRY["updok"].PLUGIN_INFO["version"] == "2.0.0"
        assert not (tmp_path / ".updok.update-bak").exists()


# ═══════════════════════════════════════════════════════════
#  M-086 — per-plugin operation lock
# ═══════════════════════════════════════════════════════════


class TestPluginLock:

    def test_lock_identity(self):
        a1 = pi._get_plugin_lock("plug_a")
        a2 = pi._get_plugin_lock("plug_a")
        b = pi._get_plugin_lock("plug_b")
        assert a1 is a2
        assert a1 is not b

    async def test_concurrent_same_id_install_serialized(self, tmp_path):
        """Two concurrent installs of the same id: the lock serializes them so
        exactly one wins and the other sees 'already installed' — neither
        corrupts the dir nor wipes the other's files."""
        src = _plugin_src("racey")
        clients = [
            _download_client(stream=_plugin_zip("racey", src)),
            _download_client(stream=_plugin_zip("racey", src)),
        ]
        with patch("server.core.plugin_installer.httpx.AsyncClient", side_effect=clients):
            results = await asyncio.gather(
                install_plugin("racey", f"{CATALOG}/racey.zip"),
                install_plugin("racey", f"{CATALOG}/racey.zip"),
                return_exceptions=True,
            )

        installed = [r for r in results if isinstance(r, dict)]
        errors = [r for r in results if isinstance(r, Exception)]
        assert len(installed) == 1 and installed[0]["status"] == "installed"
        assert len(errors) == 1 and "already installed" in str(errors[0])
        assert (tmp_path / "racey" / "racey_plugin.py").exists()


# ═══════════════════════════════════════════════════════════
#  M-087 / M-088 — directory install: URL re-validation + traversal
# ═══════════════════════════════════════════════════════════


class TestDirectoryInstallSafety:

    async def test_rejects_off_catalog_per_file_url(self, tmp_path):
        """A per-file download_url harvested from the listing must itself be
        under the catalog, not just the entry-point file_url."""
        listing = _json_response([
            {"name": "x.py", "type": "file",
             "download_url": "https://raw.githubusercontent.com/attacker/evil/main/x.py"},
        ])
        client = _download_client(
            get=[_empty_manifest_response(), listing], stream=b"payload"
        )
        with patch("server.core.plugin_installer.httpx.AsyncClient", return_value=client):
            with patch("server.core.plugin_installer._install_pip_deps", new_callable=AsyncMock):
                with patch("server.core.plugin_installer._install_native_deps", new_callable=AsyncMock):
                    with pytest.raises(ValueError, match="open-avc/openavc-plugins|catalog|path under"):
                        await install_plugin("direvil", f"{CATALOG}/plugins/direvil")
        # Partial install cleaned up.
        assert not (tmp_path / "direvil").exists()

    @pytest.mark.parametrize("name,ok", [
        ("plugin.py", True),
        ("sub", True),
        ("..", False),
        (".", False),
        ("../escape", False),
        ("a/b", False),
        ("a\\b", False),
        ("", False),
    ])
    def test_is_safe_entry_name(self, name, ok):
        assert _is_safe_entry_name(name) is ok

    async def test_directory_skips_traversal_entry(self, tmp_path):
        """A '..' dir entry is skipped; the legitimate file still installs and
        nothing is written outside the plugin dir."""
        listing = _json_response([
            {"name": "..", "type": "dir"},  # traversal attempt -> skipped
            {"name": "dirsafe_plugin.py", "type": "file",
             "download_url": f"{CATALOG}/plugins/dirsafe/dirsafe_plugin.py"},
        ])
        client = _download_client(
            get=[_empty_manifest_response(), listing],
            stream=_plugin_src("dirsafe").encode(),
        )
        with patch("server.core.plugin_installer.httpx.AsyncClient", return_value=client):
            with patch("server.core.plugin_installer._install_pip_deps", new_callable=AsyncMock):
                with patch("server.core.plugin_installer._install_native_deps", new_callable=AsyncMock):
                    result = await install_plugin("dirsafe", f"{CATALOG}/plugins/dirsafe")

        assert result["status"] == "installed"
        assert (tmp_path / "dirsafe" / "dirsafe_plugin.py").exists()
        # The '..' entry created no sibling/parent artifacts in the repo root.
        assert sorted(p.name for p in tmp_path.iterdir()) == ["_plugin_data", "dirsafe"]


# ═══════════════════════════════════════════════════════════
#  M-089 — download / decompression / file-count caps
# ═══════════════════════════════════════════════════════════


class TestDownloadCaps:

    async def test_capped_rejects_oversize_stream(self):
        client = _download_client(stream=b"x" * 100)
        with pytest.raises(ValueError, match="download limit"):
            await _download_capped(client, "https://x/y", max_bytes=10, label="t")

    async def test_capped_rejects_oversize_content_length(self):
        resp = _make_stream_response(b"", headers={"content-length": "999"})
        client = _download_client(stream=resp)
        with pytest.raises(ValueError, match="too large"):
            await _download_capped(client, "https://x/y", max_bytes=10, label="t")

    async def test_capped_returns_within_limit(self):
        client = _download_client(stream=b"hello")
        out = await _download_capped(client, "https://x/y", max_bytes=100, label="t")
        assert out == b"hello"

    def test_zip_bomb_member_count(self, monkeypatch):
        monkeypatch.setattr(pi, "_MAX_ARCHIVE_MEMBERS", 2)
        buf = BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for i in range(3):
                zf.writestr(f"f{i}.txt", b"x")
        with zipfile.ZipFile(BytesIO(buf.getvalue())) as zf:
            with pytest.raises(ValueError, match="too many entries"):
                _check_zip_bomb(zf)

    def test_zip_bomb_uncompressed_size(self, monkeypatch):
        monkeypatch.setattr(pi, "_MAX_UNCOMPRESSED_BYTES", 10)
        buf = BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("big.txt", b"x" * 100)
        with zipfile.ZipFile(BytesIO(buf.getvalue())) as zf:
            with pytest.raises(ValueError, match="too large uncompressed"):
                _check_zip_bomb(zf)

    def test_budget_file_count_cap(self):
        budget = _DownloadBudget(max_files=2, max_bytes=10**9)
        budget.add_file(1)
        budget.add_file(1)
        with pytest.raises(ValueError, match="too many files"):
            budget.add_file(1)

    def test_budget_byte_cap(self):
        budget = _DownloadBudget(max_files=100, max_bytes=10)
        with pytest.raises(ValueError, match="total size"):
            budget.add_file(20)

    async def test_install_zip_bomb_rejected(self, tmp_path):
        """End-to-end: a zip whose declared sizes exceed the cap is rejected and
        the partial install is cleaned up."""
        buf = BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("bomb/bomb_plugin.py", b"x" * 100)
        client = _download_client(stream=buf.getvalue())
        with patch.object(pi, "_MAX_UNCOMPRESSED_BYTES", 10):
            with patch("server.core.plugin_installer.httpx.AsyncClient", return_value=client):
                with pytest.raises(ValueError, match="too large uncompressed"):
                    await install_plugin("bomb", f"{CATALOG}/bomb.zip")
        assert not (tmp_path / "bomb").exists()
