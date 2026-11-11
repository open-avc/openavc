"""The panel's scripts are ES modules, served from /panel as they are on disk.

A browser refuses to run a module answered with anything but a JavaScript
type, where a plain script ran regardless, so ``openavc.main`` pins the type
rather than trusting the machine's table. Each
module is also revalidated like panel.js, preloaded by the page so the panel
does not wait one round trip per import (the cloud tunnel makes each one
slow), and imported by a relative path: the panel is served under
/tunnel/<id>/panel/ as well, where an import of /panel/... misses.
"""

from __future__ import annotations

import re
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from openavc.main import app
from openavc.system_config import WEB_PANEL_DIR

MODULES = sorted(p.relative_to(WEB_PANEL_DIR).as_posix() for p in WEB_PANEL_DIR.rglob("*.js"))
INDEX = (WEB_PANEL_DIR / "index.html").read_text(encoding="utf-8")
SPECIFIERS = (
    re.compile(r"""^\s*(?:import|export)\b[^'"]*?\bfrom\s*['"]([^'"]+)['"]""", re.M),
    re.compile(r"""^\s*import\s*['"]([^'"]+)['"]""", re.M),
    re.compile(r"""\bimport\(\s*['"]([^'"]+)['"]\s*\)"""),
)


def test_the_page_loads_panel_js_as_a_module():
    assert '<script type="module" src="panel.js"></script>' in INDEX


def test_the_page_preloads_every_module_panel_js_imports():
    preloaded = set(re.findall(r'<link rel="modulepreload" href="([^"]+)">', INDEX))
    assert preloaded == {name for name in MODULES if name != "panel.js"}


@pytest.mark.parametrize("name", MODULES)
def test_each_module_is_served_as_javascript_and_revalidated(name):
    response = TestClient(app).get(f"/panel/{name}")
    assert response.status_code == 200
    assert response.headers["content-type"].split(";")[0] in ("text/javascript", "application/javascript")
    assert response.headers["cache-control"] == "no-cache"


def test_a_machine_that_calls_js_plain_text_still_serves_javascript():
    """A fresh process whose table says text/plain before the server loads."""
    probe = (
        "import mimetypes; mimetypes.add_type('text/plain', '.js'); "
        "from fastapi.testclient import TestClient; from openavc.main import app; "
        "print(TestClient(app).get('/panel/panel.js').headers['content-type'])"
    )
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, timeout=60)
    assert out.stdout.strip().splitlines()[-1].split(";")[0] == "text/javascript", out.stderr[-2000:]


@pytest.mark.parametrize("name", MODULES)
def test_each_import_is_relative(name):
    source = (WEB_PANEL_DIR / name).read_text(encoding="utf-8")
    for pattern in SPECIFIERS:
        for specifier in pattern.findall(source):
            assert specifier.startswith(("./", "../")), f"{name} imports {specifier!r}"
            assert (WEB_PANEL_DIR / name).parent.joinpath(specifier).resolve().is_file(), (
                f"{name} imports {specifier!r}, which is not there"
            )
