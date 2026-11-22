"""What a panel gets from a server that is still starting.

A panel left open across a restart draws its controls again while its socket
is down, and each icon it draws asks for the panel's icon sheet again. The
sheet is a file on disk that needs no engine, so it is served while the start
runs; given the starting page in its place, the panel kept no icons once the
server was ready. The panel's page itself still gets the starting page, which
reloads into the panel when the start finishes.
"""

from __future__ import annotations

import pytest
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.staticfiles import StaticFiles
from starlette.testclient import TestClient

from openavc.core.start_stages import StartRun
from openavc.middleware.startup_splash import RunState, StartupSplashMiddleware

ICONS = '<svg xmlns="http://www.w3.org/2000/svg"><symbol id="house"/></svg>'


@pytest.fixture
def panel_dir(tmp_path):
    root = tmp_path / "panel"
    (root / "modules").mkdir(parents=True)
    (root / "index.html").write_text("<!doctype html><title>Panel</title>", encoding="utf-8")
    (root / "icons.svg").write_text(ICONS, encoding="utf-8")
    (root / "panel.css").write_text("body{}", encoding="utf-8")
    (root / "modules" / "words.js").write_text("export {};", encoding="utf-8")
    return root


def _client(panel_dir, record: RunState) -> TestClient:
    app = Starlette(routes=[Mount("/panel", StaticFiles(directory=str(panel_dir), html=True))])
    app.add_middleware(StartupSplashMiddleware)
    app.state.run_state = record
    return TestClient(app)


def _failed() -> RunState:
    record = RunState()
    record.stopped(StartRun(), RuntimeError("boom"))
    return record


@pytest.mark.parametrize("record", [RunState, _failed], ids=["starting", "failed"])
def test_the_panels_own_files_are_served_before_the_start_finishes(panel_dir, record):
    client = _client(panel_dir, record())

    icons = client.get("/panel/icons.svg")
    assert icons.status_code == 200
    assert icons.text == ICONS
    assert client.get("/panel/panel.css").status_code == 200
    assert client.get("/panel/modules/words.js").status_code == 200


@pytest.mark.parametrize("path", ["/panel", "/panel/", "/panel/index.html"])
def test_the_panel_page_still_gets_the_starting_page(panel_dir, path):
    client = _client(panel_dir, RunState())

    page = client.get(path, follow_redirects=True)
    assert page.status_code == 503
    assert "Starting up..." in page.text


def test_a_missing_panel_file_is_not_found_rather_than_the_starting_page(panel_dir):
    client = _client(panel_dir, RunState())

    assert client.get("/panel/nothing-here.js").status_code == 404


def test_other_paths_and_the_api_still_wait_for_the_start(panel_dir):
    client = _client(panel_dir, RunState())

    assert client.get("/programmer/").status_code == 503
    assert client.get("/api/devices").status_code == 503
    assert client.get("/panelists/icons.svg").status_code == 503
