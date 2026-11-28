"""``POST /api/ui/page-issues``: what the UI the Builder holds holds that it
may not, placed, from ``openavc.ui.page_structure``.

The Builder posts its UI unsaved and shows what comes back; a reply that
fails reads there as no issues, so the route answers every JSON shape rather
than raising, and refuses only a body that is not a UI.
"""

from __future__ import annotations

import json

import httpx
import pytest

from openavc.api.routes.page_issues import UI_BODY_MAX
from openavc.main import app
from openavc.ui import page_report
from openavc.ui import page_structure as ps

pytestmark = pytest.mark.asyncio


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1")


async def _post(body: object) -> httpx.Response:
    async with _client() as http:
        return await http.post(
            "/api/ui/page-issues", content=json.dumps(body), headers={"content-type": "application/json"},
        )


def _ui() -> dict:
    return {
        "pages": [
            {"id": "main", "name": "Main", "elements": [
                {"id": "power", "type": "button", "bindings": {
                    "show": {"look": {"key": "var.on", "states": [{"label": "On"}]},
                             "visible_when": {"key": "var.mode", "operator": "constructor"}},
                }},
                {"id": "kid", "type": "button", "parent": "ghost"},
            ], "layouts": [
                {"id": "landscape", "primary": True},
                {"id": "portrait", "orientation": "portrait", "inherits": "ghost"},
            ]},
            {"id": "other", "name": "Other", "elements": [{"id": "power", "type": "label"}]},
        ],
        "master_elements": [{"id": "kid", "type": "label", "parent": "box"}],
    }


async def test_each_issue_comes_back_placed_in_the_checks_order():
    reply = await _post({"ui": _ui()})
    assert reply.status_code == 200, reply.text
    issues = reply.json()["issues"]
    assert [(i["code"], i["page"], i["element"], i["layout"]) for i in issues] == [
        (ps.ELEMENT_ID_TAKEN, "other", "power", None),
        (ps.ELEMENT_ID_TAKEN, None, "kid", None),
        (ps.INHERITS_MISSING, "main", None, "portrait"),
        (ps.PARENT_MISSING, "main", "kid", None),
        (ps.BINDING, "main", "power", None),
        (ps.BINDING, "main", "power", None),
        (ps.MASTER_PARENT, None, "kid", None),
    ]
    expected = [ps.Issue(**i) for i in issues]
    assert expected == page_report.reported_issues(_ui()["pages"], _ui()["master_elements"])


async def test_a_clean_ui_and_an_empty_one_have_none():
    assert (await _post({"ui": {"pages": [], "master_elements": []}})).json() == {"issues": []}
    assert (await _post({"ui": {}})).json() == {"issues": []}


async def test_what_the_page_review_says_of_a_control_is_left_to_it():
    ui = {"pages": [{"id": "main", "elements": [
        {"id": "go", "type": "button", "bindings": {"do": {"press": [{"action": "launch_rocket"}]}}},
    ]}]}
    assert (await _post({"ui": ui})).json() == {"issues": []}


@pytest.mark.parametrize("body", [
    {"ui": {"pages": [{"id": ["p"], "elements": [{"id": {"a": 1}, "parent": ["x"]}, "x", None],
                       "layouts": [{"id": ["l"], "inherits": ["l"]}, 3]}, None, "page"],
            "master_elements": [{"id": [1], "bindings": {"do": "x", "show": []}}, 2]}},
    {"ui": {"pages": [{"elements": [{"bindings": {"do": {"press": [{"action": {"action": "macro"}}]},
                                                  "show": {"visible_when": {"any": "x", "all": [3]}}}}]}]}},
    # The page review is asked about each of these: each runs an action nothing dispatches.
    {"ui": {"pages": [{"id": "p", "elements": [
        {"id": "m", "type": "matrix", "bindings": {"do": {"route": [{"action": "launch_rocket"}]}},
         "matrix_config": {"sources": {"from": {"count": 2}, "exclude": 5}, "destinations": "x"}},
        {"id": "t", "type": ["button"], "bindings": {"do": {"press": [{"action": "launch_rocket"}]},
                                                    "show": {"look": {"key": "k", "states": "x"}, "value": "x"}}},
    ]}]}},
])
async def test_any_json_is_answered(body):
    reply = await _post(body)
    assert reply.status_code == 200, reply.text
    assert isinstance(reply.json()["issues"], list)


@pytest.mark.parametrize("body", [
    [], "x", {}, {"ui": []}, {"ui": {"pages": {}}}, {"ui": {"master_elements": "x"}},
])
async def test_a_body_that_is_not_a_ui_is_refused(body):
    assert (await _post(body)).status_code == 400


async def test_json_nested_deeper_than_it_can_be_read_is_refused():
    async with _client() as http:
        reply = await http.post("/api/ui/page-issues", content=b"[" * 100_000 + b"]" * 100_000)
    assert reply.status_code == 400
    assert reply.json()["detail"].startswith("Invalid JSON")


async def test_not_json_and_a_number_json_does_not_have_are_refused():
    async with _client() as http:
        for content in (b"{", b'{"ui": {"pages": [{"id": NaN}]}}'):
            reply = await http.post("/api/ui/page-issues", content=content)
            assert reply.status_code == 400, content


async def test_a_ui_bigger_than_a_project_may_be_is_refused():
    async with _client() as http:
        reply = await http.post("/api/ui/page-issues", content=b" " * (UI_BODY_MAX + 1))
    assert reply.status_code == 413
