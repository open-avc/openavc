"""A failed remote command says which of three different things went wrong.

The receiving instance computes a real refusal — its allowlist denied the
command, it is at its concurrent-command cap, its device reported a fault —
and sends the sentence back. That sentence used to be replaced at the
caller's own route by "Failed to send command to ISC peer '<id>'" with a 500,
so a policy allowlist you have to edit on the OTHER box, a dead device and a
network fault all answered identically. Three problems, three different
fixes, one message.

Platform test: the peer is a stub, no sockets.
"""

from unittest.mock import patch

import pytest
from fastapi import HTTPException

from openavc.api.models import ISCCommandRequest
from openavc.api.routes.isc import isc_command
from openavc.core.isc import ISCRemoteError


class _Isc:
    def __init__(self, raises=None, result="ok"):
        self._raises = raises
        self._result = result

    async def send_command(self, instance_id, device_id, command, params):
        if self._raises is not None:
            raise self._raises
        return self._result


class _Engine:
    def __init__(self, isc):
        self.isc = isc


REQUEST = ISCCommandRequest(
    instance_id="bbbb-2222", device_id="display", command="power_on", params={},
)


async def _call(isc):
    with patch("openavc.api.routes.isc._get_engine", return_value=_Engine(isc)):
        return await isc_command(REQUEST)


async def _status_of(isc) -> tuple[int, str]:
    with pytest.raises(HTTPException) as exc:
        await _call(isc)
    return exc.value.status_code, str(exc.value.detail)


async def test_a_successful_command_returns_its_result():
    assert await _call(_Isc(result={"power": True})) == {
        "success": True, "result": {"power": True},
    }


@pytest.mark.parametrize("sentence", [
    "command not authorized by remote instance's ISC policy",
    "remote instance is at its concurrent command limit",
    "Device 'display' not found",
])
async def test_a_peer_refusal_comes_back_verbatim(sentence):
    """The peer answered. Its own sentence is the whole value of the reply —
    ours would only say that something went wrong somewhere."""
    assert await _call(_Isc(raises=ISCRemoteError(sentence))) == {
        "success": False, "error": sentence,
    }


async def test_an_unreachable_peer_is_a_503():
    status, detail = await _status_of(_Isc(raises=ConnectionError("no peer")))
    assert status == 503
    assert "not connected" in detail


async def test_a_peer_that_drops_mid_command_is_a_503_too():
    """Nothing over there formed an opinion, so it is the same class of
    failure as never having been connected — not a refusal."""
    status, _ = await _status_of(
        _Isc(raises=ConnectionError("Peer bbbb-222 disconnected before answering"))
    )
    assert status == 503


async def test_a_silent_peer_is_a_504():
    status, detail = await _status_of(_Isc(raises=TimeoutError("too slow")))
    assert status == 504
    assert "timed out" in detail


async def test_a_local_fault_is_still_a_500_without_its_exception_text():
    """A 500 never carries raw exception text (api/errors.py), and a local
    bug is not something the peer said."""
    status, detail = await _status_of(_Isc(raises=ValueError("/some/path.py line 3")))
    assert status == 500
    assert "/some/path.py" not in detail


async def test_isc_disabled_is_a_503():
    status, detail = await _status_of(None)
    assert status == 503
    assert detail == "ISC not enabled"
