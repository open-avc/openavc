"""The panel's scripts are ES modules, served from /panel as they are on disk.

A browser refuses to run a module answered with anything but a JavaScript
type, where a plain script ran regardless, and the type comes from the
platform's own table, so this runs on every CI system. Each module is also
revalidated like panel.js, and every import is relative: the panel is served
under /tunnel/<id>/panel/ as well, where an import of /panel/... misses.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from openavc.main import app
from openavc.system_config import WEB_PANEL_DIR

MODULES = sorted(p.relative_to(WEB_PANEL_DIR).as_posix() for p in WEB_PANEL_DIR.rglob("*.js"))
IMPORT_FROM = re.compile(r"""^\s*(?:import|export)\b[^'"]*?\bfrom\s*['"]([^'"]+)['"]""", re.M)


def test_the_page_loads_panel_js_as_a_module():
    assert '<script type="module" src="panel.js"></script>' in (
        WEB_PANEL_DIR / "index.html"
    ).read_text(encoding="utf-8")


@pytest.mark.parametrize("name", MODULES)
def test_each_module_is_served_as_javascript_and_revalidated(name):
    response = TestClient(app).get(f"/panel/{name}")
    assert response.status_code == 200
    assert response.headers["content-type"].split(";")[0] in ("text/javascript", "application/javascript")
    assert response.headers["cache-control"] == "no-cache"


@pytest.mark.parametrize("name", MODULES)
def test_each_import_is_relative(name):
    source = (WEB_PANEL_DIR / name).read_text(encoding="utf-8")
    for specifier in IMPORT_FROM.findall(source):
        assert specifier.startswith(("./", "../")), f"{name} imports {specifier!r}"
        assert (WEB_PANEL_DIR / name).parent.joinpath(specifier).resolve().is_file(), (
            f"{name} imports {specifier!r}, which is not there"
        )
