"""``POST /api/ui/page-issues``: what the pages an authoring surface holds hold
that they may not, and each page its idle page, masters and macros name that
it does not have.

The Builder's Validate posts the UI and macros it is holding, unsaved, beside
the other unsaved-work doors (``/ui/resolve-matrix``, ``/ui/validate-actions``
in ``routes/project.py``), and lists the answer. The answer is
``ui/page_report.reported_issues`` and ``ui/missing_pages.missing_pages``,
which the AI's ``review_ui`` reports too. A reply that fails reads in the
Builder as no issues, so every JSON shape is answered; only a body that is no
UI is refused.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from openavc.api.host_policy import ProgrammerRoute
from openavc.ui.missing_pages import missing_pages
from openavc.ui.page_report import reported_issues

router = APIRouter(route_class=ProgrammerRoute)

#: The most the posted UI may be, in bytes: what a project save
#: (``PUT /api/project``) takes, so any UI a project can hold is answered.
UI_BODY_MAX = 10 * 1024 * 1024

_SHAPE = (
    'Expected {"ui": {"pages": [...], "master_elements": [...], "settings": {...}}, '
    '"macros": [...]}'
)


@router.post("/ui/page-issues")
async def ui_page_issues(request: Request) -> dict[str, Any]:
    """What these pages and master elements hold that they may not, and each
    page the idle page, the masters and the macros name that is not there.

    Post ``{"ui": {"pages": [...], "master_elements": [...], "settings":
    {"idle_page": ...}}, "macros": [...]}``; each part but ``ui`` may be left
    out. The reply's ``issues`` are each issue placed, ``{"code", "message",
    "page", "element", "layout"}``, with ``page`` null for a master element:
    the rule the AI's page tools ask before they write. Its ``missing_pages``
    are each place outside a page's controls naming a page that is not there,
    ``{"code", "message", "element", "macro", "step"}``: the idle page (all
    three null), a master's ``pages`` (``element``), a macro step's
    ``ui.navigate`` (``macro`` and ``step``, its path). The AI's ``review_ui``
    reports both. Nothing is refused: a project that holds one still saves and
    loads. Any JSON is read, up to the size of a whole project (10 MB).
    """
    posted = _posted(await request.body())
    return {
        "issues": [asdict(issue) for issue in reported_issues(posted.pages, posted.masters)],
        "missing_pages": [asdict(said) for said in missing_pages(posted.ui, posted.macros)],
    }


@dataclass(frozen=True)
class _Posted:
    """What a body posts: its UI, that UI's pages and master elements, and its macros."""

    ui: dict
    pages: list
    masters: list
    macros: list


def _posted(body: bytes) -> _Posted:
    """What a body posts, or the HTTP refusal."""
    posted = _json_body(body)
    ui = posted.get("ui") if isinstance(posted, dict) else None
    if not isinstance(ui, dict):
        raise HTTPException(status_code=400, detail=_SHAPE)
    pages, masters = ui.get("pages", []), ui.get("master_elements", [])
    macros = posted.get("macros", [])
    lists = isinstance(pages, list) and isinstance(masters, list) and isinstance(macros, list)
    if not lists or not isinstance(ui.get("settings", {}), dict):
        raise HTTPException(status_code=400, detail=_SHAPE)
    return _Posted(ui, pages, masters, macros)


def _json_body(body: bytes) -> Any:
    """The JSON a body holds, refused past the size a project may be."""
    if len(body) > UI_BODY_MAX:
        raise HTTPException(status_code=413, detail="UI too large (max 10 MB)")
    try:
        return json.loads(body or b"{}", parse_constant=_refuse_constant)
    except (ValueError, RecursionError) as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}") from e


def _refuse_constant(name: str) -> Any:
    """``NaN`` and ``Infinity`` are not JSON, and a reply could not carry one."""
    raise ValueError(f"{name} is not a JSON value")
