"""Regression guard for the plugin enable/config clobber bug.

Server-side project saves that bypass the reload path (plugin enable/disable and
plugin-config saves such as the Video Streams editor) must bump the project
revision. Otherwise an open editor's cached ETag still matches the server, so its
next full-project ``PUT /api/project`` silently overwrites the change — which is
how enabling the Video Panel plugin (and picking a stream) kept getting wiped by
the UI Builder's autosave.
"""

import pytest

from server.core.engine import Engine
from server.core.project_loader import PluginConfig, ProjectConfig, ProjectMeta


def _engine(tmp_path, monkeypatch):
    # Don't touch disk; we only care about the in-memory revision counter.
    monkeypatch.setattr("server.core.engine.save_project", lambda *a, **k: None)
    engine = Engine(str(tmp_path / "t.avc"))
    engine.project = ProjectConfig(
        project=ProjectMeta(id="t", name="Test"),
        devices=[],
        connections={},
        plugins={"video_panel": PluginConfig(enabled=True, config={})},
    )
    return engine


def test_bump_project_revision_increments(tmp_path, monkeypatch):
    engine = _engine(tmp_path, monkeypatch)
    engine._project_revision = 3
    engine.bump_project_revision()
    assert engine._project_revision == 4


@pytest.mark.asyncio
async def test_save_plugin_config_persists_and_bumps_revision(tmp_path, monkeypatch):
    engine = _engine(tmp_path, monkeypatch)
    engine._project_revision = 5

    await engine._save_plugin_config(
        "video_panel", {"streams": [{"stream_id": "cam1"}]}
    )

    # Config persisted to the project...
    assert engine.project.plugins["video_panel"].config == {
        "streams": [{"stream_id": "cam1"}]
    }
    # ...and the revision advanced, so a stale editor PUT will 409 rather than
    # clobber this change.
    assert engine._project_revision == 6


@pytest.mark.asyncio
async def test_save_plugin_config_unknown_plugin_does_not_bump(tmp_path, monkeypatch):
    engine = _engine(tmp_path, monkeypatch)
    engine._project_revision = 9

    await engine._save_plugin_config("not_installed", {"x": 1})

    # No matching plugin entry -> nothing saved -> revision unchanged.
    assert engine._project_revision == 9


class _StubLoader:
    """Minimal plugin_loader stand-in for the uninstall endpoint."""

    def is_running(self, plugin_id):
        return False

    async def stop_plugin(self, plugin_id):
        pass

    def clear_missing(self, plugin_id):
        pass

    def remove_plugin_tracking(self, plugin_id):
        pass


@pytest.mark.asyncio
async def test_uninstall_endpoint_removes_plugin_and_bumps_revision(tmp_path, monkeypatch):
    """Uninstalling must bump the revision after scrubbing the plugin from the
    project. Otherwise a stale editor PUT /api/project (still holding the entry)
    is accepted and silently restores the plugin and its streams -- which is what
    left the Video Panel stream configured after an uninstall.
    """
    from server.api import plugins as plugins_api
    from server.core import plugin_installer

    # The endpoint persists via server.api.plugins.save_project; keep it off disk.
    monkeypatch.setattr(plugins_api, "save_project", lambda *a, **k: None)

    # Stand in for the installer's file removal (skip real files / enabled-guard).
    async def _fake_uninstall(plugin_id, project_plugins, *, remove_data=False):
        return {"status": "uninstalled", "plugin_id": plugin_id, "data_removed": remove_data}

    monkeypatch.setattr(plugin_installer, "uninstall_plugin", _fake_uninstall)

    engine = _engine(tmp_path, monkeypatch)
    engine.plugin_loader = _StubLoader()
    engine.project.plugins["video_panel"] = PluginConfig(
        enabled=False, config={"streams": [{"stream_id": "cam1"}]}
    )
    engine._project_revision = 7
    plugins_api.set_engine(engine)

    result = await plugins_api.uninstall_plugin_endpoint("video_panel")

    assert result["status"] == "uninstalled"
    # Entry scrubbed from the project...
    assert "video_panel" not in engine.project.plugins
    # ...and the revision advanced, so a stale editor PUT 409s rather than
    # restoring video_panel and its streams.
    assert engine._project_revision == 8
