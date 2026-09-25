"""The open plugin file routes serve a plugin's own folder and nothing else.

``/api/plugins/{id}/panel/...`` and ``/api/plugins/{id}/files/...`` are open
(a panel's iframe loads them with no credential) and turn the id into a
directory under ``plugin_repo/``. The containment check in
``serve_static_file`` is made against that directory, so the id itself has to
be a plain plugin id before it is used as one; an id that is anything else is
answered as a plugin that does not exist.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import openavc.system_config as system_config
from openavc.core.plugin_installer import is_safe_plugin_id
from openavc.main import app


@pytest.mark.parametrize("plugin_id", ["acme_widget", "widget2", "a", "x_1_y"])
def test_plain_ids_are_safe(plugin_id):
    assert is_safe_plugin_id(plugin_id)


@pytest.mark.parametrize(
    "plugin_id", ["", ".", "..", "acme.widget", "Acme_Widget", "acme-widget", "a b", "a/b"]
)
def test_anything_else_is_not(plugin_id):
    assert not is_safe_plugin_id(plugin_id)


@pytest.fixture
def plugin_repo(tmp_path, monkeypatch) -> Path:
    """A plugin_repo/ with one plugin, beside a file that is not a plugin's."""
    repo = tmp_path / "plugin_repo"
    (repo / "acme_widget" / "panel").mkdir(parents=True)
    (repo / "acme_widget" / "panel" / "widget.html").write_text("<p>widget</p>")
    (repo / "acme_widget" / "sounds.json").write_text('{"ok": true}')
    (tmp_path / "private.json").write_text('{"private": true}')
    monkeypatch.setattr(system_config, "PLUGIN_REPO_DIR", repo)
    return repo


def test_a_plugin_serves_its_own_files(plugin_repo):
    client = TestClient(app)
    assert client.get("/api/plugins/acme_widget/panel/widget.html").status_code == 200
    assert client.get("/api/plugins/acme_widget/files/sounds.json").json() == {"ok": True}


def test_an_unsafe_id_is_a_missing_plugin(plugin_repo):
    client = TestClient(app)
    for bad in ("Acme_Widget", "acme.widget", "%2E%2E"):
        for route in ("panel/widget.html", "files/private.json"):
            response = client.get(f"/api/plugins/{bad}/{route}")
            assert response.status_code == 404, (bad, route)
            assert b"private" not in response.content
