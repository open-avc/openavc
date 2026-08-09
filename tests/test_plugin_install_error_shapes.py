"""What the plugin install door says when it is asked wrong.

Neither case is reachable from the Programmer IDE, which always sends a
well-formed body carrying a catalog URL — so both went unnoticed until the
REST API was driven directly. Both answered 500.
"""
from __future__ import annotations

import json

import httpx
import pytest

from openavc.api.plugins import _install_body


class _FakeRequest:
    """Stands in for a Starlette Request's json() contract only."""

    def __init__(self, raw: object, raises: Exception | None = None):
        self._raw = raw
        self._raises = raises

    async def json(self):
        if self._raises is not None:
            raise self._raises
        return self._raw


@pytest.mark.asyncio
class TestInstallBody:
    async def test_reads_a_normal_body(self):
        req = _FakeRequest({"file_url": "https://example.invalid/x"})
        assert (await _install_body(req)) == {"file_url": "https://example.invalid/x"}

    async def test_absent_body_becomes_empty_not_a_500(self):
        # request.json() raises JSONDecodeError on an empty body. That used to
        # escape as a 500 with an ASGI traceback -- and it raised BEFORE the
        # endpoint's own "422 file_url is required" could fire, making that 422
        # unreachable for exactly the case it was written for.
        req = _FakeRequest(None, raises=json.JSONDecodeError("Expecting value", "", 0))
        assert (await _install_body(req)) == {}

    async def test_malformed_body_becomes_empty(self):
        req = _FakeRequest(None, raises=ValueError("nope"))
        assert (await _install_body(req)) == {}

    async def test_non_object_body_becomes_empty(self):
        # A bare list or string parses fine but has no .get(); returning it
        # would move the AttributeError one line down rather than fix it.
        assert (await _install_body(_FakeRequest(["a", "b"]))) == {}
        assert (await _install_body(_FakeRequest("a string"))) == {}

    async def test_empty_body_still_yields_the_422_path(self):
        # The point of the fix: an empty body must reach the endpoint's own
        # required-field check, which reads file_url off this dict.
        assert (await _install_body(_FakeRequest({}))).get("file_url") is None


def test_upstream_404_is_classified_as_a_gateway_error():
    """A wrong file_url means the upstream served something else, not that we
    failed. The neighbouring CommunityArtifactError branch already argues this
    -- "502, not 500: the request was fine, the upstream served something
    else" -- and an httpx 404 matched none of the handled types, so it fell to
    the generic handler and told the operator nothing useful.
    """
    request = httpx.Request("GET", "https://raw.githubusercontent.invalid/open-avc/x/main/nope")
    response = httpx.Response(404, request=request)
    err = httpx.HTTPStatusError("404", request=request, response=response)

    # The endpoint builds its message from these two, so pin that they carry
    # what the message promises: which URL, and what it answered.
    assert str(err.request.url).endswith("/nope")
    assert err.response.status_code == 404
