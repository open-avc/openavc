"""Which themes there are, what a theme file may hold, and whether it is light or dark.

``openavc/core/themes.py`` owns each of these. The REST routes
(``api/themes.py``) and the AI's theme tools (``cloud/tools/system_tools.py``)
look a theme up there, the save and import doors check a theme's shape there,
and both doors record a chosen theme's light or dark treatment from the one
rule there: the luminance of the theme's page background, read as the panel
reads a colour (``web/panel/modules/lit.js``), and the theme's id only when
that colour cannot be read.
"""

from __future__ import annotations

import io
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from openavc.api import rest
from openavc.api import themes as themes_api
from openavc.cloud.ai_tool_handler import AIToolHandler
from openavc.core import themes
from openavc.core.project_loader import UISettings
from tests import gates

REPO = Path(__file__).resolve().parents[1]
LIT = REPO / "openavc" / "web" / "panel" / "modules" / "lit.js"
COLOURS = json.loads((REPO / "tests" / "fixtures" / "theme_colour_reads.json").read_text(encoding="utf-8"))["colours"]

PROJECT = {
    "project": {"id": "theme_rule", "name": "Theme Rule"},
    "devices": [], "variables": [], "macros": [],
    "ui": {"pages": [], "settings": {"theme_id": "dark-default"}},
}


def _theme(theme_id: str, **extra) -> dict:
    return {"name": "Acme", "id": theme_id, "version": "1.0.0", "variables": {"panel_bg": "#101010"}, **extra}


@pytest.fixture
def client(tmp_path):
    from openavc.core.engine import Engine
    from openavc.core.project_loader import load_project
    from openavc.main import app

    path = tmp_path / "project.avc"
    path.write_text(json.dumps(PROJECT), encoding="utf-8")
    engine = Engine(str(path))
    engine.project = load_project(str(path))
    engine._running = True
    rest.set_engine(engine)
    themes_api.set_engine(engine)
    try:
        yield TestClient(app)
    finally:
        rest.set_engine(None)
        themes_api.set_engine(None)


def _import(client, data) -> object:
    body = io.BytesIO(json.dumps(data).encode("utf-8"))
    return client.post("/api/themes/import", files={"file": ("acme.avctheme", body, "application/json")})


# --- The shape a saved or imported theme must have ---------------------------

NOT_TEXT_PICTURES = {
    "a number": {"page_defaults": {"background_image": 5}},
    "a list": {"page_defaults": {"background_image": ["assets://a.png"]}},
    "a variable holding a number": {
        "variables": {"panel_bg": "#101010", "brand_picture": 7},
        "page_defaults": {"background_image": "var(brand_picture)"},
    },
}

TAKEN_PICTURES = {
    "none": {"page_defaults": {"background_image": None}},
    "a picture": {"page_defaults": {"background_image": "assets://hall.png"}},
    "a variable holding a picture": {
        "variables": {"panel_bg": "#101010", "brand_picture": "assets://hall.png"},
        "page_defaults": {"background_image": "var(brand_picture)"},
    },
    "a variable the theme lacks": {"page_defaults": {"background_image": "var(nothing)"}},
    "no page defaults": {},
}


@pytest.mark.parametrize("extra", NOT_TEXT_PICTURES.values(), ids=list(NOT_TEXT_PICTURES))
def test_a_page_picture_that_is_not_text_is_refused_at_every_door(client, extra):
    created = client.post("/api/themes", json=_theme("acme-new", **extra))
    assert created.status_code == 400
    assert "picture" in created.json()["detail"]

    assert client.post("/api/themes", json=_theme("acme-kept")).status_code == 200
    updated = client.put("/api/themes/acme-kept", json=_theme("acme-kept", **extra))
    assert updated.status_code == 400
    assert client.get("/api/themes/acme-kept").json()["variables"] == {"panel_bg": "#101010"}

    imported = _import(client, _theme("acme-imported", **extra))
    assert imported.status_code == 400
    assert client.get("/api/themes/acme-new").status_code == 404
    assert client.get("/api/themes/acme-imported").status_code == 404


@pytest.mark.parametrize("extra", TAKEN_PICTURES.values(), ids=list(TAKEN_PICTURES))
def test_a_page_picture_that_is_text_or_none_is_taken(client, extra):
    assert client.post("/api/themes", json=_theme("acme-pic", **extra)).status_code == 200
    assert client.put("/api/themes/acme-pic", json=_theme("acme-pic", **extra)).status_code == 200
    assert _import(client, {**_theme("acme-pic-two", **extra)}).status_code == 200


def test_every_built_in_theme_has_the_shape_a_save_takes():
    for path in sorted(themes.BUILTIN_DIR.glob("*.json")):
        themes.check_theme(json.loads(path.read_text(encoding="utf-8")))


@pytest.mark.parametrize("theme_id", [5, None, ["acme"]], ids=["a number", "null", "a list"])
def test_an_id_that_is_not_text_is_refused_not_a_server_error(client, theme_id):
    resp = client.post("/api/themes", json=_theme("acme", id=theme_id))
    assert resp.status_code == 400
    assert "Theme ID" in resp.json()["detail"]


def test_an_id_with_a_line_break_after_it_is_refused(client, tmp_path):
    """The id is the file's name: one character past the rule is refused, a
    line break at its end included."""
    resp = client.post("/api/themes", json=_theme("acme\n"))
    assert resp.status_code == 400
    assert not any((tmp_path / "themes").glob("acme*"))


@pytest.mark.parametrize("variables", [5, "dark", ["#000"]], ids=["a number", "text", "a list"])
def test_variables_that_are_not_named_values_are_refused(client, variables):
    resp = client.post("/api/themes", json=_theme("acme", variables=variables))
    assert resp.status_code == 400
    assert "variables" in resp.json()["detail"]


@pytest.mark.parametrize("content", [[], "acme", 5], ids=["a list", "text", "a number"])
def test_an_imported_file_that_is_no_theme_is_refused_not_a_server_error(client, content):
    resp = _import(client, content)
    assert resp.status_code == 400


# --- The lookup ---------------------------------------------------------------

def test_an_unreadable_custom_theme_is_no_theme(client, tmp_path):
    (tmp_path / "themes").mkdir(exist_ok=True)
    (tmp_path / "themes" / "acme-broken.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "themes" / "acme-list.json").write_text("[1, 2]", encoding="utf-8")
    assert client.get("/api/themes/acme-broken").status_code == 404
    assert client.get("/api/themes/acme-list").status_code == 404
    listed = [t["id"] for t in client.get("/api/themes").json()["themes"]]
    assert "acme-broken" not in listed and "dark-default" in listed


def test_a_built_in_id_is_found_before_a_custom_file_of_the_same_name(client, tmp_path):
    (tmp_path / "themes").mkdir(exist_ok=True)
    (tmp_path / "themes" / "dark-default.json").write_text(json.dumps(_theme("dark-default", name="Acme")), encoding="utf-8")
    got = client.get("/api/themes/dark-default").json()
    assert got["_source"] == "builtin" and got["name"] != "Acme"


def test_a_built_in_theme_is_answered_without_a_project():
    """The open route the panel fetches its theme from reads a built-in
    before it asks where the project's own themes are."""
    def no_project():
        raise AssertionError("asked for the custom folder")

    assert themes.find_theme("dark-default", no_project)["_source"] == "builtin"


@pytest.mark.parametrize("theme_id", ["../cloud", "/etc/passwd"])
def test_an_id_that_leaves_the_themes_folder_is_refused(tmp_path, theme_id):
    with pytest.raises(themes.ThemeRefused):
        themes.find_theme(theme_id, tmp_path)


# --- Light or dark ------------------------------------------------------------

_LIT_SCRIPT = """
const { rgbChannels, relativeLuminance } = await import(process.argv[1]);
const colours = JSON.parse(process.argv[2]);
console.log(JSON.stringify(colours.map((c) => {
    const rgb = rgbChannels(c);
    return rgb ? { rgb, luminance: relativeLuminance(rgb) } : null;
})));
"""


def test_a_colour_is_read_as_the_panel_reads_it():
    """Run under Node: lit.js's read of every colour in the case file."""
    if shutil.which("node") is None:
        gates.skip_or_fail(gates.NODE, "node not installed")
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", _LIT_SCRIPT, LIT.as_uri(), json.dumps(COLOURS)],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    panel = json.loads(proc.stdout)
    for colour, read in zip(COLOURS, panel):
        rgb = themes.rgb_channels(colour)
        if read is None:
            assert rgb is None, colour
            continue
        assert list(rgb or ()) == read["rgb"], colour
        assert themes.relative_luminance(rgb) == pytest.approx(read["luminance"], abs=1e-12), colour


MODE_CASES = {
    "a light background whose id lacks light": ({"id": "paper", "variables": {"panel_bg": "#fafafa"}}, "light"),
    "a dark background whose id says light": ({"id": "lightning", "variables": {"panel_bg": "#101010"}}, "dark"),
    "a background the panel cannot read, an id saying light": ({"id": "light-sky", "variables": {"panel_bg": "white"}}, "light"),
    "a background the panel cannot read, another id": ({"id": "night", "variables": {"panel_bg": "white"}}, "dark"),
    "no background, the minimal id": ({"id": "minimal", "variables": {}}, "light"),
    "no variables at all": ({"id": "acme"}, "dark"),
    "variables that are not named values": ({"id": "light-ish", "variables": 5}, "light"),
    "an id that is not text": ({"id": 5, "variables": {}}, "dark"),
}


@pytest.mark.parametrize("theme,expected", MODE_CASES.values(), ids=list(MODE_CASES))
def test_light_or_dark(theme, expected):
    assert themes.theme_mode(theme) == expected


def test_the_two_treatments_are_the_schemas_words():
    """The project schema names the theme a light or a dark project with no
    theme id draws; the rule answers in exactly those words."""
    _, by_theme = UISettings.BLANK_FROM["theme_id"]
    assert set(themes.MODES) == set(by_theme)
    for mode, theme_id in by_theme.items():
        assert themes.theme_mode(themes.find_theme(theme_id, None)) == mode


def test_the_theme_list_says_whether_each_theme_is_light_or_dark(client):
    created = client.post("/api/themes", json=_theme("paper", variables={"panel_bg": "#fafafa"}))
    assert created.json()["mode"] == "light", "the answer to the save says it, before the list is read again"
    imported = _import(client, _theme("paper-two", variables={"panel_bg": "#fafafa"}))
    assert imported.json()["mode"] == "light"
    listed = {t["id"]: t["mode"] for t in client.get("/api/themes").json()["themes"]}
    assert listed["paper"] == "light"
    assert listed["dark-default"] == "dark"
    assert listed["light-modern"] == "light"


@pytest.fixture
def ai(tmp_path, monkeypatch):
    project = SimpleNamespace(ui=SimpleNamespace(settings=UISettings.model_validate({"theme_id": "dark-default"})))
    engine = MagicMock()
    engine.project = project
    engine.project_path = tmp_path / "project.avc"
    rest.set_engine(engine)
    themes_api.set_engine(engine)

    async def apply_edit(_engine, mutate):
        mutate(project)

    monkeypatch.setattr("openavc.cloud.tools.system_tools.apply_tool_edit", apply_edit)
    try:
        yield AIToolHandler(MagicMock(), MagicMock(), MagicMock()), project, tmp_path
    finally:
        rest.set_engine(None)
        themes_api.set_engine(None)


async def test_the_ai_records_light_for_a_light_theme_whose_id_lacks_light(ai):
    handler, project, tmp_path = ai
    (tmp_path / "themes").mkdir()
    (tmp_path / "themes" / "paper.json").write_text(json.dumps(_theme("paper", variables={"panel_bg": "#fafafa"})), encoding="utf-8")
    assert (await handler._apply_theme({"theme_id": "paper"}))["status"] == "applied"
    assert project.ui.settings.theme_id == "paper"
    assert project.ui.settings.theme == "light"


async def test_the_ai_finds_a_theme_where_the_panel_does(ai):
    handler, project, tmp_path = ai
    (tmp_path / "themes").mkdir()
    (tmp_path / "themes" / "acme-broken.json").write_text("{not json", encoding="utf-8")
    assert "error" in await handler._get_theme({"theme_id": "acme-broken"})
    assert "error" in await handler._apply_theme({"theme_id": "acme-broken"})
    assert project.ui.settings.theme_id == "dark-default"
    assert (await handler._get_theme({"theme_id": "light-modern"}))["_source"] == "builtin"
