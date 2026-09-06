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


def _panel_asset(client) -> Path:
    """The file the fixture wrote, found the way the routes find it."""
    from openavc.config import get_config

    return get_config().plugin_repo_path / "demo_plugin" / "panel" / "widget.js"


def test_a_file_rewritten_within_the_same_second_is_not_cached_forever(plugin_client):
    """The freshness this module exists for, at the resolution people work at.

    ``Last-Modified`` is whole seconds; the ETag is derived from a float mtime.
    So two saves inside one second change the tag and not the date -- and a
    revalidating browser sends both headers. Answering the date anyway meant
    304 to every later request from that viewer: the panel kept running the
    previous file, for good, with nothing on screen to say so.
    """
    import os

    first = plugin_client.get("/api/plugins/demo_plugin/panel/widget.js")
    stale_etag = first.headers["etag"]
    stale_date = first.headers["last-modified"]

    # Save it again inside the same second: the mtime moves by a fraction, so
    # the whole-second date the caller holds is still current.
    path = _panel_asset(plugin_client)
    path.write_text("console.log('v2');", encoding="utf-8")
    stat = path.stat()
    os.utime(path, (stat.st_atime, int(stat.st_mtime) + 0.5))
    assert plugin_client.get(
        "/api/plugins/demo_plugin/panel/widget.js"
    ).headers["last-modified"] == stale_date, "the date has to be unchanged for this to be the case it is about"

    resp = plugin_client.get(
        "/api/plugins/demo_plugin/panel/widget.js",
        headers={"If-None-Match": stale_etag, "If-Modified-Since": stale_date},
    )
    assert resp.status_code == 200, (
        "the caller's own ETag says the file changed; a date a second wide "
        "must not overrule it"
    )
    assert "v2" in resp.text


def test_an_unchanged_file_still_answers_304_to_both_headers(plugin_client):
    """The must-not-move half: a real revalidation of an unchanged file stays
    cheap, whichever headers the browser sends."""
    first = plugin_client.get("/api/plugins/demo_plugin/panel/widget.js")
    etag, date = first.headers["etag"], first.headers["last-modified"]

    both = plugin_client.get(
        "/api/plugins/demo_plugin/panel/widget.js",
        headers={"If-None-Match": etag, "If-Modified-Since": date},
    )
    assert both.status_code == 304
    date_only = plugin_client.get(
        "/api/plugins/demo_plugin/panel/widget.js",
        headers={"If-Modified-Since": date},
    )
    assert date_only.status_code == 304, (
        "a caller that sent no ETag is still entitled to the date comparison"
    )
