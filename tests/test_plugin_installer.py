"""
Tests for the plugin installer.

Covers: CommunityPluginCache, list_installed_plugins, install_plugin,
uninstall_plugin, _register_installed_plugin, _install_pip_deps.
"""

import zipfile
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from server.core.plugin_installer import (
    CommunityPluginCache,
    _install_deps_from_pypi,
    _install_pip_deps,
    _normalize_pkg_name,
    _parse_requirement,
    _read_wheel_deps,
    _register_installed_plugin,
    _resolve_version,
    _version_tuple,
    install_plugin,
    list_installed_plugins,
    uninstall_plugin,
)
from server.core.plugin_loader import _PLUGIN_CLASS_REGISTRY, register_plugin_class


# ──── Helpers ────


def _make_plugin_zip(plugin_id: str, plugin_source: str) -> bytes:
    """Create an in-memory zip archive containing a single plugin .py file."""
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        # Zip with a top-level directory (like GitHub downloads)
        zf.writestr(f"{plugin_id}/{plugin_id}_plugin.py", plugin_source)
    return buf.getvalue()


SAMPLE_PLUGIN_SOURCE = '''\
class SamplePlugin:
    PLUGIN_INFO = {
        "id": "sample_community",
        "name": "Sample Community Plugin",
        "version": "1.0.0",
        "author": "Test",
        "description": "A test community plugin.",
        "category": "utility",
        "license": "MIT",
        "capabilities": [],
    }

    async def start(self, api):
        pass

    async def stop(self):
        pass
'''

PLUGIN_WITH_DEPS_SOURCE = '''\
PLUGIN_INFO = {
    "id": "deps_plugin",
    "name": "Deps Plugin",
    "version": "1.0.0",
    "author": "Test",
    "description": "Plugin with dependencies.",
    "category": "utility",
    "license": "MIT",
    "dependencies": ["some-library>=1.0", "another-lib"],
}
'''


# ──── Fixtures ────


@pytest.fixture(autouse=True)
def _patch_plugin_repo(tmp_path, monkeypatch):
    """Redirect PLUGIN_REPO_DIR to a temp directory for every test."""
    monkeypatch.setattr(
        "server.core.plugin_installer.PLUGIN_REPO_DIR", tmp_path
    )


@pytest.fixture
def plugin_repo(tmp_path):
    """Return the tmp_path used as PLUGIN_REPO_DIR."""
    return tmp_path


# ═══════════════════════════════════════════════════════════
#  CommunityPluginCache Tests
# ═══════════════════════════════════════════════════════════


class TestCommunityPluginCache:

    async def test_cache_miss_fetches(self):
        """First call fetches from remote."""
        cache = CommunityPluginCache(ttl=600.0)
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "plugins": [{"id": "demo", "name": "Demo Plugin"}]
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("server.core.plugin_installer.httpx.AsyncClient", return_value=mock_client):
            plugins, error = await cache.get()

        assert error is None
        assert len(plugins) == 1
        assert plugins[0]["id"] == "demo"

    async def test_cache_hit_within_ttl(self):
        """Second call within TTL returns cached data without fetching."""
        cache = CommunityPluginCache(ttl=600.0)
        mock_response = MagicMock()
        mock_response.json.return_value = {"plugins": [{"id": "cached"}]}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("server.core.plugin_installer.httpx.AsyncClient", return_value=mock_client):
            # First call: fetches
            plugins1, _ = await cache.get()
            # Second call: should use cache
            plugins2, _ = await cache.get()

        # Only one HTTP call should have been made
        assert mock_client.get.call_count == 1
        assert plugins1 == plugins2

    async def test_force_refresh_bypasses_cache(self):
        """force=True fetches even when cache is valid."""
        cache = CommunityPluginCache(ttl=600.0)
        mock_response = MagicMock()
        mock_response.json.return_value = {"plugins": [{"id": "fresh"}]}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("server.core.plugin_installer.httpx.AsyncClient", return_value=mock_client):
            await cache.get()
            await cache.get(force=True)

        assert mock_client.get.call_count == 2

    async def test_cache_expired_refetches(self):
        """Cache with TTL=0 always refetches."""
        cache = CommunityPluginCache(ttl=0)
        mock_response = MagicMock()
        mock_response.json.return_value = {"plugins": []}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("server.core.plugin_installer.httpx.AsyncClient", return_value=mock_client):
            await cache.get()
            await cache.get()

        assert mock_client.get.call_count == 2

    async def test_fetch_failure_returns_error(self):
        """Network error returns stale data + error string."""
        cache = CommunityPluginCache(ttl=600.0)

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("no network"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("server.core.plugin_installer.httpx.AsyncClient", return_value=mock_client):
            plugins, error = await cache.get()

        assert error is not None
        assert "no network" in error
        assert plugins == []  # No stale data on first failure


# ═══════════════════════════════════════════════════════════
#  list_installed_plugins Tests
# ═══════════════════════════════════════════════════════════


class TestListInstalledPlugins:

    def test_empty_dir(self, plugin_repo):
        """Empty plugin_repo returns empty list."""
        result = list_installed_plugins()
        assert result == []

    def test_ignores_hidden_dirs(self, plugin_repo):
        """Directories starting with . or _ are skipped."""
        (plugin_repo / ".deps").mkdir()
        (plugin_repo / "_internal").mkdir()
        result = list_installed_plugins()
        assert result == []

    def test_unregistered_plugin_listed(self, plugin_repo):
        """Plugin dir without a registered class is still listed."""
        (plugin_repo / "my_plugin").mkdir()
        result = list_installed_plugins()
        assert len(result) == 1
        assert result[0]["id"] == "my_plugin"
        assert result[0]["name"] == "my_plugin"
        assert result[0]["version"] == ""

    def test_registered_plugin_has_info(self, plugin_repo):
        """Plugin with registered class shows PLUGIN_INFO metadata."""

        class TestPlugin:
            PLUGIN_INFO = {
                "id": "fancy",
                "name": "Fancy Plugin",
                "version": "2.0.0",
            }

        (plugin_repo / "fancy").mkdir()
        register_plugin_class(TestPlugin)

        result = list_installed_plugins()
        assert len(result) == 1
        assert result[0]["id"] == "fancy"
        assert result[0]["name"] == "Fancy Plugin"
        assert result[0]["version"] == "2.0.0"

    def test_nonexistent_dir(self, plugin_repo, monkeypatch):
        """If plugin_repo doesn't exist, returns empty list."""
        monkeypatch.setattr(
            "server.core.plugin_installer.PLUGIN_REPO_DIR",
            plugin_repo / "nonexistent",
        )
        result = list_installed_plugins()
        assert result == []


# ═══════════════════════════════════════════════════════════
#  install_plugin Tests
# ═══════════════════════════════════════════════════════════


class TestInstallPlugin:

    async def test_install_zip_plugin(self, plugin_repo):
        """Installing a .zip creates the plugin directory with extracted files."""
        zip_bytes = _make_plugin_zip("sample_community", SAMPLE_PLUGIN_SOURCE)

        mock_response = MagicMock()
        mock_response.content = zip_bytes
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("server.core.plugin_installer.httpx.AsyncClient", return_value=mock_client):
            result = await install_plugin(
                "sample_community",
                "https://example.com/sample_community.zip",
            )

        assert result["status"] == "installed"
        assert result["plugin_id"] == "sample_community"
        assert (plugin_repo / "sample_community").is_dir()
        assert (plugin_repo / "sample_community" / "sample_community_plugin.py").exists()

    async def test_install_single_py_file(self, plugin_repo):
        """Installing a .py URL creates a directory with the file inside."""
        mock_response = MagicMock()
        mock_response.content = SAMPLE_PLUGIN_SOURCE.encode()
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("server.core.plugin_installer.httpx.AsyncClient", return_value=mock_client):
            result = await install_plugin(
                "single_file",
                "https://example.com/single_file_plugin.py",
            )

        assert result["status"] == "installed"
        assert (plugin_repo / "single_file" / "single_file_plugin.py").exists()

    async def test_install_already_installed_raises(self, plugin_repo):
        """Installing a plugin that already exists raises ValueError."""
        (plugin_repo / "existing").mkdir()

        with pytest.raises(ValueError, match="already installed"):
            await install_plugin("existing", "https://example.com/existing.zip")

    async def test_install_http_failure_cleans_up(self, plugin_repo):
        """If download fails, partial directory is cleaned up."""
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "404", request=MagicMock(), response=MagicMock()
            )
        )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("server.core.plugin_installer.httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(httpx.HTTPStatusError):
                await install_plugin("bad_download", "https://example.com/bad.zip")

        # Directory should not remain after failure
        assert not (plugin_repo / "bad_download").exists()

    async def test_install_directory_url_downloads_all_files(self, plugin_repo):
        """URL that doesn't end in .py or .zip downloads directory via GitHub API."""
        api_response = MagicMock()
        api_response.raise_for_status = MagicMock()
        api_response.json.return_value = [
            {"name": "my_plugin.py", "type": "file",
             "download_url": "https://raw.githubusercontent.com/open-avc/openavc-plugins/main/plugins/my_plugin.py"},
            {"name": "config.json", "type": "file",
             "download_url": "https://raw.githubusercontent.com/open-avc/openavc-plugins/main/plugins/config.json"},
        ]

        file_response = MagicMock()
        file_response.content = b"# plugin code"
        file_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[api_response, file_response, file_response])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("server.core.plugin_installer.httpx.AsyncClient", return_value=mock_client):
            with patch("server.core.plugin_installer._install_pip_deps", new_callable=AsyncMock):
                with patch("server.core.plugin_installer._install_native_deps", new_callable=AsyncMock):
                    result = await install_plugin(
                        "generic_plugin",
                        "https://raw.githubusercontent.com/open-avc/openavc-plugins/main/plugins/generic_plugin",
                    )

        assert result["status"] == "installed"
        assert (plugin_repo / "generic_plugin" / "my_plugin.py").exists()
        assert (plugin_repo / "generic_plugin" / "config.json").exists()


# ═══════════════════════════════════════════════════════════
#  uninstall_plugin Tests
# ═══════════════════════════════════════════════════════════


class TestUninstallPlugin:

    async def test_uninstall_removes_dir_and_unregisters(self, plugin_repo):
        """Uninstalling removes the directory and unregisters the class."""

        class UninstallMe:
            PLUGIN_INFO = {"id": "remove_me", "name": "Remove Me", "version": "1.0.0"}

        register_plugin_class(UninstallMe)
        plugin_dir = plugin_repo / "remove_me"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.py").write_text("# code", encoding="utf-8")

        result = await uninstall_plugin("remove_me")

        assert result["status"] == "uninstalled"
        assert not plugin_dir.exists()
        assert "remove_me" not in _PLUGIN_CLASS_REGISTRY

    async def test_uninstall_not_installed_raises(self, plugin_repo):
        """Uninstalling a non-existent plugin raises ValueError."""
        with pytest.raises(ValueError, match="not installed"):
            await uninstall_plugin("ghost_plugin")

    async def test_uninstall_enabled_plugin_raises(self, plugin_repo):
        """Cannot uninstall a plugin that is enabled in the current project."""
        plugin_dir = plugin_repo / "active_plugin"
        plugin_dir.mkdir()

        project_plugins = {
            "active_plugin": {"enabled": True, "config": {}},
        }

        with pytest.raises(ValueError, match="currently enabled"):
            await uninstall_plugin("active_plugin", project_plugins=project_plugins)

    async def test_uninstall_disabled_plugin_ok(self, plugin_repo):
        """Uninstalling a disabled plugin succeeds."""
        plugin_dir = plugin_repo / "disabled_plugin"
        plugin_dir.mkdir()

        project_plugins = {
            "disabled_plugin": {"enabled": False, "config": {}},
        }

        result = await uninstall_plugin("disabled_plugin", project_plugins=project_plugins)
        assert result["status"] == "uninstalled"
        assert not plugin_dir.exists()

    async def test_uninstall_with_pydantic_model(self, plugin_repo):
        """project_plugins entries can be objects with .enabled attribute."""
        plugin_dir = plugin_repo / "model_plugin"
        plugin_dir.mkdir()

        entry = MagicMock()
        entry.enabled = True
        project_plugins = {"model_plugin": entry}

        with pytest.raises(ValueError, match="currently enabled"):
            await uninstall_plugin("model_plugin", project_plugins=project_plugins)


# ═══════════════════════════════════════════════════════════
#  _register_installed_plugin Tests
# ═══════════════════════════════════════════════════════════


class TestRegisterInstalledPlugin:

    def test_register_from_named_file(self, plugin_repo):
        """Registers a plugin class found in <plugin_id>_plugin.py."""
        plugin_dir = plugin_repo / "sample_community"
        plugin_dir.mkdir()
        (plugin_dir / "sample_community_plugin.py").write_text(
            SAMPLE_PLUGIN_SOURCE, encoding="utf-8"
        )

        result = _register_installed_plugin("sample_community", plugin_dir)

        assert result is True
        assert "sample_community" in _PLUGIN_CLASS_REGISTRY

    def test_register_no_matching_class(self, plugin_repo):
        """Returns False if no class with matching PLUGIN_INFO.id is found."""
        plugin_dir = plugin_repo / "no_match"
        plugin_dir.mkdir()
        (plugin_dir / "no_match_plugin.py").write_text(
            "class Unrelated:\n    pass\n", encoding="utf-8"
        )

        result = _register_installed_plugin("no_match", plugin_dir)

        assert result is False
        assert "no_match" not in _PLUGIN_CLASS_REGISTRY

    def test_register_empty_dir(self, plugin_repo):
        """Returns False for an empty plugin directory."""
        plugin_dir = plugin_repo / "empty"
        plugin_dir.mkdir()

        result = _register_installed_plugin("empty", plugin_dir)

        assert result is False

    def test_register_syntax_error_handled(self, plugin_repo):
        """Syntax errors in plugin files are caught gracefully."""
        plugin_dir = plugin_repo / "broken"
        plugin_dir.mkdir()
        (plugin_dir / "broken_plugin.py").write_text(
            "def broken(\n  # missing close paren", encoding="utf-8"
        )

        result = _register_installed_plugin("broken", plugin_dir)

        assert result is False


# ═══════════════════════════════════════════════════════════
#  _install_pip_deps Tests
# ═══════════════════════════════════════════════════════════


class TestInstallPipDeps:

    async def test_parses_and_installs_deps(self, plugin_repo):
        """Finds dependencies list via AST and calls pip install."""
        plugin_dir = plugin_repo / "deps_plugin"
        plugin_dir.mkdir()
        (plugin_dir / "deps_plugin.py").write_text(
            PLUGIN_WITH_DEPS_SOURCE, encoding="utf-8"
        )

        with patch("server.core.plugin_installer.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            await _install_pip_deps("deps_plugin", plugin_dir)

        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert "some-library>=1.0" in call_args
        assert "another-lib" in call_args
        assert "--target" in call_args

    async def test_no_deps_skips_pip(self, plugin_repo):
        """Plugin without dependencies does not call pip."""
        plugin_dir = plugin_repo / "no_deps"
        plugin_dir.mkdir()
        (plugin_dir / "no_deps.py").write_text(
            "PLUGIN_INFO = {'id': 'no_deps', 'name': 'No Deps'}\n",
            encoding="utf-8",
        )

        with patch("server.core.plugin_installer.subprocess.run") as mock_run:
            await _install_pip_deps("no_deps", plugin_dir)

        mock_run.assert_not_called()

    async def test_pip_failure_logged_not_raised(self, plugin_repo):
        """pip install failure is logged but does not raise."""
        plugin_dir = plugin_repo / "deps_plugin"
        plugin_dir.mkdir()
        (plugin_dir / "deps_plugin.py").write_text(
            PLUGIN_WITH_DEPS_SOURCE, encoding="utf-8"
        )

        import subprocess as sp

        with patch(
            "server.core.plugin_installer.subprocess.run",
            side_effect=sp.CalledProcessError(1, "pip", stderr="error"),
        ):
            # Should not raise
            await _install_pip_deps("deps_plugin", plugin_dir)

    async def test_deps_dir_created(self, plugin_repo):
        """The .deps directory is created when dependencies are found."""
        plugin_dir = plugin_repo / "deps_plugin"
        plugin_dir.mkdir()
        (plugin_dir / "deps_plugin.py").write_text(
            PLUGIN_WITH_DEPS_SOURCE, encoding="utf-8"
        )

        with patch("server.core.plugin_installer.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            await _install_pip_deps("deps_plugin", plugin_dir)

        assert (plugin_repo / ".deps").is_dir()

    async def test_syntax_error_in_file_skipped(self, plugin_repo):
        """Files with syntax errors are skipped gracefully."""
        plugin_dir = plugin_repo / "bad_syntax"
        plugin_dir.mkdir()
        (plugin_dir / "bad.py").write_text(
            "def broken(\n", encoding="utf-8"
        )

        with patch("server.core.plugin_installer.subprocess.run") as mock_run:
            await _install_pip_deps("bad_syntax", plugin_dir)

        mock_run.assert_not_called()


# ═══════════════════════════════════════════════════════════
#  PyPI Wheel Download Tests (frozen environment path)
# ═══════════════════════════════════════════════════════════


class TestParseRequirement:

    def test_name_only(self):
        assert _parse_requirement("requests") == ("requests", "")

    def test_version_gte(self):
        assert _parse_requirement("pillow>=10.0") == ("pillow", ">=10.0")

    def test_version_eq(self):
        assert _parse_requirement("some-lib==2.1.0") == ("some-lib", "==2.1.0")

    def test_whitespace(self):
        assert _parse_requirement("  mylib >= 1.0 ") == ("mylib", ">= 1.0")


class TestNormalizePkgName:

    def test_underscores(self):
        assert _normalize_pkg_name("My_Package") == "my-package"

    def test_dots(self):
        assert _normalize_pkg_name("zope.interface") == "zope-interface"

    def test_already_normalized(self):
        assert _normalize_pkg_name("requests") == "requests"


class TestVersionTuple:

    def test_simple(self):
        assert _version_tuple("1.2.3") == (1, 2, 3)

    def test_two_part(self):
        assert _version_tuple("10.0") == (10, 0)

    def test_invalid(self):
        # Non-numeric parts are filtered out, resulting in an empty tuple
        assert _version_tuple("abc") == ()


class TestResolveVersion:

    def test_gte(self):
        releases = {
            "1.0.0": [{"url": "x"}],
            "2.0.0": [{"url": "x"}],
            "3.0.0": [{"url": "x"}],
        }
        assert _resolve_version(releases, ">=2.0.0") == "3.0.0"

    def test_eq(self):
        releases = {
            "1.0.0": [{"url": "x"}],
            "2.0.0": [{"url": "x"}],
        }
        assert _resolve_version(releases, "==1.0.0") == "1.0.0"

    def test_skips_prerelease(self):
        releases = {
            "1.0.0": [{"url": "x"}],
            "2.0.0a1": [{"url": "x"}],
        }
        assert _resolve_version(releases, ">=1.0.0") == "1.0.0"

    def test_no_match(self):
        releases = {"1.0.0": [{"url": "x"}]}
        assert _resolve_version(releases, ">=5.0.0") is None


class TestReadWheelDeps:

    def test_reads_requires_dist(self):
        buf = BytesIO()
        with zipfile.ZipFile(buf, "w") as whl:
            whl.writestr("pkg-1.0.dist-info/METADATA", (
                "Metadata-Version: 2.1\n"
                "Name: pkg\n"
                "Requires-Dist: dep-a\n"
                "Requires-Dist: dep-b (>=2.0)\n"
                'Requires-Dist: optional-dep ; extra == "test"\n'
            ))
        with zipfile.ZipFile(BytesIO(buf.getvalue())) as whl:
            deps = _read_wheel_deps(whl)
        assert "dep-a" in deps
        assert "dep-b (>=2.0)" in deps
        assert not any("optional" in d for d in deps)

    def test_no_metadata(self):
        buf = BytesIO()
        with zipfile.ZipFile(buf, "w") as whl:
            whl.writestr("pkg/__init__.py", "")
        with zipfile.ZipFile(BytesIO(buf.getvalue())) as whl:
            assert _read_wheel_deps(whl) == []


class TestInstallDepsFromPyPI:

    async def test_downloads_and_extracts_wheel(self, plugin_repo):
        """When frozen, downloads a wheel and extracts into .deps/."""
        deps_dir = plugin_repo / ".deps"
        deps_dir.mkdir()

        # Build a fake wheel (zip with a Python module + METADATA)
        buf = BytesIO()
        with zipfile.ZipFile(buf, "w") as whl:
            whl.writestr("fake_pkg/__init__.py", "VERSION = '1.0'")
            whl.writestr("fake_pkg-1.0.dist-info/METADATA", (
                "Metadata-Version: 2.1\n"
                "Name: fake-pkg\n"
            ))
        wheel_bytes = buf.getvalue()

        # Mock PyPI JSON response
        pypi_json = {
            "info": {"version": "1.0.0"},
            "releases": {
                "1.0.0": [{
                    "packagetype": "bdist_wheel",
                    "filename": "fake_pkg-1.0.0-py3-none-any.whl",
                    "url": "https://files.example.com/fake_pkg-1.0.0-py3-none-any.whl",
                }]
            },
        }

        async def mock_get(url, **kwargs):
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.status_code = 200
            if "pypi.org" in url:
                resp.json.return_value = pypi_json
            else:
                resp.content = wheel_bytes
            return resp

        mock_client = AsyncMock()
        mock_client.get = mock_get
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("server.core.plugin_installer.httpx.AsyncClient", return_value=mock_client):
            await _install_deps_from_pypi(["fake-pkg"], deps_dir, "test_plugin")

        assert (deps_dir / "fake_pkg" / "__init__.py").exists()

    async def test_frozen_path_used_when_frozen(self, plugin_repo):
        """_install_pip_deps uses PyPI download when sys.frozen is True."""
        plugin_dir = plugin_repo / "deps_plugin"
        plugin_dir.mkdir()
        (plugin_dir / "deps_plugin.py").write_text(
            PLUGIN_WITH_DEPS_SOURCE, encoding="utf-8"
        )

        with (
            patch("server.core.plugin_installer.sys") as mock_sys,
            patch("server.core.plugin_installer._install_deps_from_pypi") as mock_pypi,
        ):
            mock_sys.frozen = True
            mock_sys.version_info = MagicMock(major=3, minor=12)
            mock_sys.platform = "win32"
            await _install_pip_deps("deps_plugin", plugin_dir)

        mock_pypi.assert_called_once()
        # Verify the parsed deps were passed through
        call_deps = mock_pypi.call_args[0][0]
        assert "some-library>=1.0" in call_deps
        assert "another-lib" in call_deps


# ═══════════════════════════════════════════════════════════
#  Sanitize Filename Test
# ═══════════════════════════════════════════════════════════


class TestSanitizeFilename:

    def test_removes_unsafe_chars(self):
        from server.core.plugin_installer import _sanitize_filename

        assert _sanitize_filename("my plugin!@#$.py") == "myplugin.py"

    def test_allows_safe_chars(self):
        from server.core.plugin_installer import _sanitize_filename

        assert _sanitize_filename("my_plugin-v2.py") == "my_plugin-v2.py"
