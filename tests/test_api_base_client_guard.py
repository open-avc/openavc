"""Regression test for the Programmer shared API client.

The shared ``request()`` in web/programmer/src/api/base.ts used to end with an
unconditional ``return res.json();``. On a 204 No Content or empty body,
``res.json()`` rejects with "Unexpected end of JSON input", so any caller
hitting such a response (e.g. a DELETE route that returns 204) surfaces a
confusing JSON-parse toast instead of a resolved promise. The sibling client
(cloudClient.ts) already guards this.

The fix mirrors the sibling: guard 204 and non-JSON/empty responses before
parsing. There is no vitest/jest harness in web/programmer, so this pins
base.ts to the fixed shape the same way the other frontend regression tests
pin their modules: the old parse-everything path can't quietly come back.
"""

from __future__ import annotations

import re
from pathlib import Path

# Repo root = openavc/ (this file is openavc/tests/test_api_base_client_guard.py).
OPENAVC_ROOT = Path(__file__).resolve().parents[1]
BASE_TS = OPENAVC_ROOT / "web" / "programmer" / "src" / "api" / "base.ts"


def _request_body() -> str:
    """Return the source of the shared request() function in base.ts."""
    src = BASE_TS.read_text(encoding="utf-8")
    match = re.search(r"export async function request<T>.*", src, re.DOTALL)
    assert match, "base.ts no longer exports a request<T>() function"
    return match.group(0)


def test_request_guards_204_before_parsing_json() -> None:
    body = _request_body()
    assert "res.status === 204" in body, (
        "request() must short-circuit a 204 No Content response instead of "
        "calling res.json() on an empty body"
    )
    # The 204 guard has to run before the res.json() parse to have any effect.
    guard = body.index("res.status === 204")
    parse = body.rindex("return res.json()")
    assert guard < parse, "the 204 guard must precede the res.json() call"


def test_request_guards_non_json_responses() -> None:
    body = _request_body()
    assert "content-type" in body, (
        "request() must check the content-type so a non-JSON / empty 200 body "
        "does not reach res.json()"
    )
    assert 'includes("application/json")' in body
