"""PluginAPI.viewer_access — where a viewer is, and what may be sent to them.

The Video Panel plugin serves WebRTC on the LAN and HLS through the cloud
tunnel, and the second of those spends the cloud's bandwidth, so it is sold per
space. That makes one property load-bearing above all the others:

    **a viewer on the LAN is answered without the entitlement being consulted.**

Not "checked and allowed" — never reached. A panel on the customer's own
network, on the customer's own hardware, must not be capable of going dark
because an entitlement lookup is missing, stale or wrong. The first test below
is the one that pins it, and it pins it by making the lookup explode.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from starlette.requests import Request

from openavc.cloud.agent import DEFAULT_CAPABILITIES
from openavc.core.event_bus import EventBus
from openavc.core.plugin_api import (
    VIEWER_LOCAL,
    VIEWER_REMOTE,
    VIEWER_REMOTE_BLOCKED,
    PluginAPI,
    PluginPermissionError,
)
from openavc.core.plugin_registry import PluginRegistry
from openavc.core.state_store import StateStore
from openavc.utils.request_origin import TUNNEL_HEADER


def _make_api(capabilities=("http_endpoints",), cloud_agent_provider=None):
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    return PluginAPI(
        plugin_id="video_panel",
        capabilities=list(capabilities),
        config={},
        registry=PluginRegistry("video_panel"),
        state_store=state,
        event_bus=events,
        macro_engine=MagicMock(execute=AsyncMock()),
        device_manager=MagicMock(send_command=AsyncMock()),
        platform_id="test_platform",
        cloud_agent_provider=cloud_agent_provider,
    )


def _request(*, peer="192.168.1.50", tunneled=False):
    headers = [(b"host", b"openavc.local")]
    if tunneled:
        headers.append((TUNNEL_HEADER.encode(), b"1"))
    return Request({
        "type": "http",
        "method": "GET",
        "path": "/api/plugins/video_panel/ext/whep/cam1",
        "headers": headers,
        "client": (peer, 50000),
        "query_string": b"",
    })


def _agent(capabilities):
    agent = MagicMock()
    agent.has_capability = lambda c: c in capabilities
    return agent


# ---------------------------------------------------------------------------
# The local path cannot be broken by the entitlement
# ---------------------------------------------------------------------------


def test_a_lan_viewer_is_answered_without_the_entitlement_being_consulted():
    """The provider raises. A LAN viewer still gets an answer.

    This is deliberately harsher than "returns local when the capability is
    absent" — that passes for an implementation that reads both facts up front
    and combines them correctly today. This one fails the moment anything
    touches the cloud agent before deciding the caller is local, which is the
    refactor that would quietly put a LAN panel behind an entitlement.
    """
    def _explode():
        raise AssertionError("the entitlement was consulted for a local viewer")

    api = _make_api(cloud_agent_provider=_explode)
    assert api.viewer_access(_request(), "tunnel_video") == VIEWER_LOCAL


def test_a_viewer_on_the_box_itself_is_local():
    api = _make_api(cloud_agent_provider=lambda: _agent(set()))
    assert api.viewer_access(_request(peer="127.0.0.1"), "tunnel_video") == VIEWER_LOCAL


def test_a_lan_viewer_is_local_with_no_cloud_at_all():
    """An instance that was never paired still serves its own panels."""
    api = _make_api(cloud_agent_provider=None)
    assert api.viewer_access(_request(), "tunnel_video") == VIEWER_LOCAL


def test_the_tunnel_marker_is_not_believed_from_the_lan():
    """A LAN client stamping the header does not become a remote viewer.

    request_origin only believes the marker from a loopback peer. Asserting it
    here as well because this method is where a plugin would otherwise be
    tempted to read the header itself.
    """
    api = _make_api(cloud_agent_provider=lambda: _agent(set()))
    req = _request(peer="192.168.1.50", tunneled=True)
    assert api.viewer_access(req, "tunnel_video") == VIEWER_LOCAL


# ---------------------------------------------------------------------------
# The remote path
# ---------------------------------------------------------------------------


def test_a_tunnelled_viewer_with_the_capability_is_remote():
    api = _make_api(cloud_agent_provider=lambda: _agent({"tunnel", "tunnel_video"}))
    req = _request(peer="127.0.0.1", tunneled=True)
    assert api.viewer_access(req, "tunnel_video") == VIEWER_REMOTE


def test_a_tunnelled_viewer_without_the_capability_is_blocked():
    api = _make_api(cloud_agent_provider=lambda: _agent({"tunnel"}))
    req = _request(peer="127.0.0.1", tunneled=True)
    assert api.viewer_access(req, "tunnel_video") == VIEWER_REMOTE_BLOCKED


def test_a_tunnelled_viewer_with_no_agent_is_blocked():
    """Fails safe. In practice unreachable — no agent means no tunnel — but a
    request claiming to be tunnelled is not evidence of a grant."""
    api = _make_api(cloud_agent_provider=lambda: None)
    req = _request(peer="127.0.0.1", tunneled=True)
    assert api.viewer_access(req, "tunnel_video") == VIEWER_REMOTE_BLOCKED


def test_the_capability_is_read_live_not_snapshotted():
    """A plan change lands mid-session via capabilities_update, so the answer
    has to follow it without the plugin restarting."""
    granted = {"tunnel"}
    api = _make_api(cloud_agent_provider=lambda: _agent(granted))
    req = _request(peer="127.0.0.1", tunneled=True)
    assert api.viewer_access(req, "tunnel_video") == VIEWER_REMOTE_BLOCKED
    granted.add("tunnel_video")
    assert api.viewer_access(req, "tunnel_video") == VIEWER_REMOTE


# ---------------------------------------------------------------------------
# Getting it wrong is loud
# ---------------------------------------------------------------------------


def test_an_unknown_capability_raises_rather_than_blocking_everyone():
    """A typo that silently blocked every remote viewer would look exactly like
    an unsold add-on, and nobody would find it."""
    api = _make_api(cloud_agent_provider=lambda: _agent({"tunnel_video"}))
    with pytest.raises(ValueError, match="tunnel_vidoe"):
        api.viewer_access(_request(peer="127.0.0.1", tunneled=True), "tunnel_vidoe")


def test_a_plugin_that_serves_no_http_may_not_ask():
    api = _make_api(capabilities=("state_read",), cloud_agent_provider=lambda: _agent(set()))
    with pytest.raises(PluginPermissionError):
        api.viewer_access(_request(), "tunnel_video")


def test_tunnel_video_is_in_the_capability_vocabulary():
    """The agent advertises it so the cloud can grant it. Granting is the
    cloud's half; this only makes it askable."""
    assert "tunnel_video" in DEFAULT_CAPABILITIES
