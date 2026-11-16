"""The size a page's background picture draws at on the panel.

The UI Builder offers Cover, Contain and Stretch for a page's picture
(``PropertiesPanel.tsx``), and the panel hands the size to the browser as CSS
(``web/panel/modules/theme.js`` ``pagePictureSize``). Stretch is no CSS size,
so it is drawn as ``100% 100%``, as a button's picture already is; a browser
given the word drops it and draws the picture at its own size. A size this
version does not know (a later version's, kept in the project as written) is
drawn as the schema's table says: Cover.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tests import gates

THEME = Path(__file__).resolve().parents[1] / "openavc" / "web" / "panel" / "modules" / "theme.js"

_SCRIPT = """
const { pagePictureSize } = await import(process.argv[1]);
const rows = JSON.parse(process.argv[2]);
console.log(JSON.stringify(rows.map((bg) => pagePictureSize(bg))));
"""

#: (the saved background, the CSS size the panel draws it at)
CASES = [
    ({"image_size": "stretch"}, "100% 100%"),
    ({"image_size": "cover"}, "cover"),
    ({"image_size": "contain"}, "contain"),
    ({}, "cover"),
    ({"image_size": "tile"}, "cover"),
]


def _drawn(rows: list[dict]) -> list[str]:
    if shutil.which("node") is None:
        gates.skip_or_fail(gates.NODE, "node not installed")
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", _SCRIPT, THEME.as_uri(), json.dumps(rows)],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@pytest.mark.parametrize("background, css", CASES, ids=lambda v: json.dumps(v))
def test_each_size_the_builder_offers_draws_as_css(background: dict, css: str) -> None:
    assert _drawn([background]) == [css]
