"""A plugin update has to reach panels that are already open.

A plugin's ``panel/`` folder is code a panel runs, served at a stable URL with
no version in the name, and replaced in place when the plugin is updated. With
no cache directive a browser applies heuristic freshness -- a fraction of the
file's age -- so a panel that loaded the old script keeps running it, for hours,
with no error anywhere and nothing on screen to say the update did not land.

The project's ``ui/`` tree has the identical shape and already sends the
revalidate header. These pin the plugin half to it.
"""

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openavc.main import app


@pytest.fixture
def plugin_client(monkeypatch):
    """A client whose plugin_repo is a temp dir holding one panel asset."""
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        panel = repo / "demo_plugin" / "panel"
        panel.mkdir(parents=True)
        (panel / "widget.js").write_text("console.log('v1');", encoding="utf-8")
        (repo / "demo_plugin" / "data.json").write_text("{}", encoding="utf-8")

        import openavc.config as config_mod

        class _Config:
            plugin_repo_path = repo

        monkeypatch.setattr(config_mod, "get_config", lambda: _Config())
        yield TestClient(app)


def test_a_panel_asset_is_revalidated_rather_than_reused(plugin_client):
    resp = plugin_client.get("/api/plugins/demo_plugin/panel/widget.js")
    assert resp.status_code == 200, resp.text
    assert "no-cache" in resp.headers.get("cache-control", ""), (
        "without this a plugin update leaves the previous script running in "
        "every panel that had already loaded it"
    )


def test_the_same_holds_for_a_plugins_other_bundled_files(plugin_client):
    """`files/` serves the same tree by another door and is updated the same way."""
    resp = plugin_client.get("/api/plugins/demo_plugin/files/data.json")
    assert resp.status_code == 200, resp.text
    assert "no-cache" in resp.headers.get("cache-control", "")


def test_revalidation_is_not_a_re_download(plugin_client):
    """`no-cache` means ask, not resend. The ETag is what makes it cheap, so an
    unchanged file answers 304 with no body -- the reason this is affordable on
    a wall tablet on 2.4GHz Wi-Fi."""
    first = plugin_client.get("/api/plugins/demo_plugin/panel/widget.js")
    etag = first.headers.get("etag")
    assert etag, "FileResponse should carry an ETag for the conditional request"
    second = plugin_client.get(
        "/api/plugins/demo_plugin/panel/widget.js", headers={"If-None-Match": etag}
    )
    assert second.status_code == 304
