"""The real panel's scripts, for a test page that renders the panel itself.

``panel.js`` is an ES module that imports its parts by relative path, so a
test page cannot paste it inline the way it pastes the stylesheets: it loads
it with a module script from a routed address, and every script under
``openavc/web/panel/`` is answered there from the checkout. The address is an
origin of its own, so a page's own catch-all route (``PANEL_URL + "*"``)
never answers a script with the page's HTML. A module from another origin is
fetched with CORS, hence the allow header.

Call ``serve_panel_scripts(page)`` before the page loads, and put
``panel_script_tag()`` where ``panel.js`` goes.
"""
from __future__ import annotations

from pathlib import Path

PANEL_DIR = Path(__file__).resolve().parents[2] / "openavc" / "web" / "panel"
SCRIPT_ORIGIN = "http://panel-scripts.test"


def panel_script_tag() -> str:
    """The tag that loads the panel, as ``web/panel/index.html`` does."""
    return f'<script type="module" src="{SCRIPT_ORIGIN}/panel/panel.js"></script>'


def serve_panel_scripts(page) -> None:
    """Answer the panel's scripts from the checkout; anything else is a 404."""

    def fulfill(route) -> None:
        name = route.request.url.split("/panel/", 1)[1].split("?", 1)[0]
        path = (PANEL_DIR / name).resolve()
        if PANEL_DIR not in path.parents or path.suffix != ".js" or not path.is_file():
            route.fulfill(status=404, body="")
            return
        route.fulfill(
            status=200,
            content_type="text/javascript",
            headers={"Access-Control-Allow-Origin": "*"},
            body=path.read_text(encoding="utf-8"),
        )

    page.route(f"{SCRIPT_ORIGIN}/panel/**", fulfill)
