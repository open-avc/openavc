"""``POST /api/ui/page-issues``: what the pages an authoring surface holds hold
that they may not.

The Builder's Validate posts the pages it is holding, unsaved, beside the
other unsaved-work doors (``/ui/resolve-matrix``, ``/ui/validate-actions`` in
``routes/project.py``), and lists the answer. The answer is
``ui/page_report.reported_issues``, which the AI's ``review_ui`` reports too.
A reply that fails reads in the Builder as no issues, so every JSON shape is
answered; only a body that is no UI is refused.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from openavc.api.host_policy import ProgrammerRoute
from openavc.ui.page_report import reported_issues

router = APIRouter(route_class=ProgrammerRoute)

#: The most the posted UI may be, in bytes: what a project save
#: (``PUT /api/project``) takes, so any UI a project can hold is answered.
UI_BODY_MAX = 10 * 1024 * 1024

_SHAPE = 'Expected {"ui": {"pages": [...], "master_elements": [...]}}'


@router.post("/ui/page-issues")
async def ui_page_issues(request: Request) -> dict[str, Any]:
    """What these pages and master elements hold that they may not.

    Post ``{"ui": {"pages": [...], "master_elements": [...]}}``; the reply is
    each issue placed, ``{"code", "message", "page", "element", "layout"}``,
    with ``page`` null for a master element. The rule is the one the AI's page
    tools ask before they write, and the AI's ``review_ui`` reports the same
    issues. Nothing is refused: a project that holds one still saves and
    loads. Any JSON is read, up to the size of a whole project (10 MB).
    """
    pages, masters = _posted_ui(await request.body())
    return {"issues": [asdict(issue) for issue in reported_issues(pages, masters)]}


def _posted_ui(body: bytes) -> tuple[list, list]:
    """The pages and master elements a body posts, or the HTTP refusal."""
    ui = _json_body(body)
    ui = ui.get("ui") if isinstance(ui, dict) else None
    pages = ui.get("pages", []) if isinstance(ui, dict) else None
    masters = ui.get("master_elements", []) if isinstance(ui, dict) else None
    if not isinstance(pages, list) or not isinstance(masters, list):
        raise HTTPException(status_code=400, detail=_SHAPE)
    return pages, masters


def _json_body(body: bytes) -> Any:
    """The JSON a body holds, refused past the size a project may be."""
    if len(body) > UI_BODY_MAX:
        raise HTTPException(status_code=413, detail="UI too large (max 10 MB)")
    try:
        return json.loads(body or b"{}", parse_constant=_refuse_constant)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}") from e


def _refuse_constant(name: str) -> Any:
    """``NaN`` and ``Infinity`` are not JSON, and a reply could not carry one."""
    raise ValueError(f"{name} is not a JSON value")
