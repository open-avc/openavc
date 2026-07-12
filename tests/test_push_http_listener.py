"""Tests for the driver push primitive (``push: {type: http_listener}``).

Platform-feature tests with an INVENTED device (acme_codec) and synthetic
bodies per the test policy: a device that dials OUT to a registered callback
URL and POSTs notification documents (webhook style). Covers the registry,
the API route (POST and GENA-style NOTIFY), the callback-URL builder, the
driver lifecycle, ``{push_callback_url}`` substitution, the redirect
listener's pass-through, and the simulator's POST-to-callback emission.
"""

import asyncio

import httpx
import pytest
from fastapi import FastAPI

from server.core.event_bus import EventBus
from server.core.state_store import StateStore
from server.drivers.configurable import create_configurable_driver_class
from server.drivers.driver_loader import validate_driver_definition
from server.transport import http_listener as hl


def _make_driver(definition: dict, config: dict | None = None, device_id: str = "dev1"):
    cls = create_configurable_driver_class(definition)
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    return cls(device_id, config or {}, state, events)


def _codec_def(**overrides) -> dict:
    d = {
        "id": "acme_codec",
        "name": "Acme Codec",
        "manufacturer": "Acme",
        "category": "video",
        "version": "1.0.0",
        "author": "Test",
        "description": "Invented webhook-push codec",
        "transport": "http",
        "source_url": "https://example.com",
        "config_schema": {
            "host": {"type": "string", "required": True, "label": "IP"},
        },
        "default_config": {"host": "", "port": 80},
        "push": {"type": "http_listener"},
        "commands": {
            "register_feedback": {
                "method": "POST",
                "path": "/register",
                "body": "<Register><Url>{push_callback_url}</Url></Register>",
            },
        },
        "state_variables": {
            "mute": {"type": "boolean", "label": "Mute"},
            "level": {"type": "integer", "label": "Level"},
        },
        "responses": [
            {"match": r"<Mute>(\d)</Mute>", "set": {"mute": "$1"}},
            {"match": r"<Level>(\d+)</Level>", "set": {"level": "$1"}},
        ],
    }
    d.update(overrides)
    return d


@pytest.fixture(autouse=True)
def _clean_registry():
    yield
    hl.close_all()


# ===========================================================================
# Loader validation
# ===========================================================================


def test_loader_accepts_http_listener_push():
    assert validate_driver_definition(_codec_def()) == []


def test_loader_accepts_http_listener_on_tcp_transport():
    # The callback channel is independent of the control transport (a TCP
    # device can still register a webhook URL via a raw command).
    d = _codec_def(transport="tcp")
    d["commands"] = {}
    assert validate_driver_definition(d) == []


@pytest.mark.parametrize(
    "push, expect",
    [
        # tcp_listener is a real type too, but it needs its own keys — a bare
        # one is a missing-port error, not an unsupported-type error.
        ({"type": "tcp_listener"}, "missing 'port'"),
        ({"type": "http_listener", "path": "/x"}, "unknown key"),
        ({"type": "http_listener", "group": "239.0.0.1"}, "unknown key"),
        ({"type": "bogus"}, "missing or unknown 'type'"),
    ],
)
def test_loader_rejects_bad_http_listener_blocks(push, expect):
    d = _codec_def()
    d["push"] = push
    errors = validate_driver_definition(d)
    assert any(expect in e for e in errors), errors


def test_factory_copies_push_into_driver_info():
    drv = _make_driver(_codec_def(), {"host": "10.0.0.5"})
    assert drv.DRIVER_INFO["push"]["type"] == "http_listener"


# ===========================================================================
# Registry — paths, demux, source gate
# ===========================================================================


@pytest.mark.asyncio
async def test_subscription_paths():
    sub = await hl.subscribe("dev1", "10.0.0.1", lambda r: None, "dev1")
    labeled = await hl.subscribe("dev1", "10.0.0.1", lambda r: None, "dev1", label="avt")
    assert sub.path == "/api/push/dev1"
    assert labeled.path == "/api/push/dev1/avt"
    await sub.close()
    await labeled.close()


@pytest.mark.asyncio
async def test_dispatch_delivers_to_matching_subscription():
    got: list[hl.HTTPPushRequest] = []
    sub = await hl.subscribe("dev1", "10.0.0.1", got.append, "dev1")
    status = await hl.dispatch(
        "dev1", "", hl.HTTPPushRequest(body=b"<Mute>1</Mute>", source_ip="10.0.0.1")
    )
    assert status == 200
    assert got and got[0].body == b"<Mute>1</Mute>"
    await sub.close()


@pytest.mark.asyncio
async def test_dispatch_unknown_path_is_404():
    status = await hl.dispatch("nobody", "", hl.HTTPPushRequest(body=b"x", source_ip="10.0.0.1"))
    assert status == 404


@pytest.mark.asyncio
async def test_dispatch_wrong_source_is_403():
    got: list[hl.HTTPPushRequest] = []
    sub = await hl.subscribe("dev1", "10.0.0.1", got.append, "dev1")
    status = await hl.dispatch(
        "dev1", "", hl.HTTPPushRequest(body=b"x", source_ip="10.9.9.9")
    )
    assert status == 403
    assert not got
    await sub.close()


@pytest.mark.asyncio
async def test_loopback_subscription_accepts_local_sources():
    """A simulated device (host rewritten to 127.0.0.1) accepts callbacks
    from any local address — the simulator POSTs from a real interface."""
    got: list[hl.HTTPPushRequest] = []
    sub = await hl.subscribe("dev1", "127.0.0.1", got.append, "dev1")
    assert await hl.dispatch("dev1", "", hl.HTTPPushRequest(body=b"a", source_ip="127.0.0.9")) == 200
    assert len(got) == 1
    await sub.close()


@pytest.mark.asyncio
async def test_resubscribe_replaces_previous_registration():
    """A reconnect's fresh subscription wins even when the old handle's
    close runs afterwards (async teardown racing the new connection)."""
    first_got: list[hl.HTTPPushRequest] = []
    second_got: list[hl.HTTPPushRequest] = []
    first = await hl.subscribe("dev1", "10.0.0.1", first_got.append, "dev1")
    second = await hl.subscribe("dev1", "10.0.0.1", second_got.append, "dev1")
    await first.close()  # stale teardown must not remove the fresh sub
    status = await hl.dispatch(
        "dev1", "", hl.HTTPPushRequest(body=b"x", source_ip="10.0.0.1")
    )
    assert status == 200
    assert second_got and not first_got
    await second.close()


@pytest.mark.asyncio
async def test_labels_are_independent_subscriptions():
    got_a: list[bytes] = []
    got_b: list[bytes] = []
    sub_a = await hl.subscribe("dev1", "10.0.0.1", lambda r: got_a.append(r.body), "dev1", label="a")
    sub_b = await hl.subscribe("dev1", "10.0.0.1", lambda r: got_b.append(r.body), "dev1", label="b")
    await hl.dispatch("dev1", "a", hl.HTTPPushRequest(body=b"for-a", source_ip="10.0.0.1", label="a"))
    await hl.dispatch("dev1", "b", hl.HTTPPushRequest(body=b"for-b", source_ip="10.0.0.1", label="b"))
    assert got_a == [b"for-a"] and got_b == [b"for-b"]
    await sub_a.close()
    await sub_b.close()


@pytest.mark.asyncio
async def test_async_callback_is_awaited():
    got: list[bytes] = []

    async def cb(request: hl.HTTPPushRequest) -> None:
        await asyncio.sleep(0)
        got.append(request.body)

    sub = await hl.subscribe("dev1", "10.0.0.1", cb, "dev1")
    await hl.dispatch("dev1", "", hl.HTTPPushRequest(body=b"x", source_ip="10.0.0.1"))
    assert got == [b"x"]
    await sub.close()


@pytest.mark.asyncio
async def test_callback_exception_still_returns_200():
    def cb(request: hl.HTTPPushRequest) -> None:
        raise RuntimeError("boom")

    sub = await hl.subscribe("dev1", "10.0.0.1", cb, "dev1")
    status = await hl.dispatch("dev1", "", hl.HTTPPushRequest(body=b"x", source_ip="10.0.0.1"))
    assert status == 200  # the device must not retry/deactivate over our bug
    await sub.close()


# ===========================================================================
# Callback URL builder
# ===========================================================================


class _StubSystemConfig:
    def __init__(self, control_interface: str = "") -> None:
        self._ci = control_interface

    def get(self, section: str, key: str):
        assert (section, key) == ("network", "control_interface")
        return self._ci


@pytest.fixture
def _no_pin(monkeypatch):
    import server.system_config as system_config

    monkeypatch.setattr(
        system_config, "get_system_config", lambda: _StubSystemConfig("")
    )


def test_callback_url_plain_http(monkeypatch, _no_pin):
    from server import config

    monkeypatch.setattr(config, "TLS_ENABLED", False)
    monkeypatch.setattr(config, "HTTP_PORT", 8080)
    url = hl.callback_url("127.0.0.1", "/api/push/dev1")
    assert url == "http://127.0.0.1:8080/api/push/dev1"


def test_callback_url_tls_with_redirect_stays_http(monkeypatch, _no_pin):
    """With the redirect listener up, devices deliver plain HTTP to the HTTP
    port — the push pass-through serves them there."""
    from server import config

    monkeypatch.setattr(config, "TLS_ENABLED", True)
    monkeypatch.setattr(config, "TLS_REDIRECT_HTTP", True)
    monkeypatch.setattr(config, "HTTP_PORT", 8080)
    url = hl.callback_url("127.0.0.1", "/api/push/dev1")
    assert url.startswith("http://127.0.0.1:8080/")


def test_callback_url_https_only(monkeypatch, _no_pin):
    from server import config

    monkeypatch.setattr(config, "TLS_ENABLED", True)
    monkeypatch.setattr(config, "TLS_REDIRECT_HTTP", False)
    monkeypatch.setattr(config, "TLS_PORT", 8443)
    url = hl.callback_url("127.0.0.1", "/api/push/dev1")
    assert url == "https://127.0.0.1:8443/api/push/dev1"


def test_callback_url_honors_control_interface_pin(monkeypatch):
    import server.system_config as system_config
    from server import config

    monkeypatch.setattr(
        system_config,
        "get_system_config",
        lambda: _StubSystemConfig("192.0.2.77"),
    )
    monkeypatch.setattr(config, "TLS_ENABLED", False)
    monkeypatch.setattr(config, "HTTP_PORT", 8080)
    url = hl.callback_url("203.0.113.5", "/api/push/dev1")
    assert url == "http://192.0.2.77:8080/api/push/dev1"


def test_callback_url_loopback_device_gets_loopback(monkeypatch, _no_pin):
    from server import config

    monkeypatch.setattr(config, "TLS_ENABLED", False)
    monkeypatch.setattr(config, "HTTP_PORT", 8080)
    assert hl._local_ip_for("localhost") == "127.0.0.1"
    assert hl._local_ip_for("127.0.0.5") == "127.0.0.1"


# ===========================================================================
# API route (POST + NOTIFY) end to end
# ===========================================================================


def _push_app() -> FastAPI:
    from server.api.routes import push as push_routes

    app = FastAPI()
    app.include_router(push_routes.open_router, prefix="/api")
    return app


@pytest.mark.asyncio
async def test_route_post_reaches_driver_state():
    drv = _make_driver(_codec_def(), {"host": "127.0.0.1"})
    await drv._start_push()
    try:
        transport = httpx.ASGITransport(app=_push_app(), client=("127.0.0.1", 1234))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/push/dev1", content=b"<Mute>1</Mute>")
            assert resp.status_code == 200
        await asyncio.sleep(0)
        assert drv.state.get("device.dev1.mute") is True
    finally:
        await drv._stop_push()


@pytest.mark.asyncio
async def test_route_notify_method_reaches_driver_state():
    """UPnP GENA delivers with the NOTIFY method — same dispatch path."""
    drv = _make_driver(_codec_def(), {"host": "127.0.0.1"})
    await drv._start_push()
    try:
        transport = httpx.ASGITransport(app=_push_app(), client=("127.0.0.1", 1234))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.request(
                "NOTIFY", "/api/push/dev1", content=b"<Level>42</Level>"
            )
            assert resp.status_code == 200
        await asyncio.sleep(0)
        assert drv.state.get("device.dev1.level") == 42
    finally:
        await drv._stop_push()


@pytest.mark.asyncio
async def test_route_404_when_device_not_subscribed():
    transport = httpx.ASGITransport(app=_push_app(), client=("127.0.0.1", 1234))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/push/ghost", content=b"x")
        assert resp.status_code == 404


@pytest.mark.asyncio
async def test_redirect_listener_passes_push_through(monkeypatch):
    """With HTTPS + redirect enabled, the HTTP listener redirects everything
    EXCEPT /api/push/, which it serves in-process — devices don't follow
    redirects."""
    from server.main import _build_redirect_app

    drv = _make_driver(_codec_def(), {"host": "127.0.0.1"})
    await drv._start_push()
    try:
        app = _build_redirect_app(8443)
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 1234))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/push/dev1", content=b"<Mute>1</Mute>")
            assert resp.status_code == 200
            other = await client.post("/api/status", content=b"{}")
            assert other.status_code == 307  # everything else still redirects
        await asyncio.sleep(0)
        assert drv.state.get("device.dev1.mute") is True
    finally:
        await drv._stop_push()


# ===========================================================================
# Driver lifecycle + {push_callback_url} substitution
# ===========================================================================


@pytest.mark.asyncio
async def test_start_push_sets_callback_url(monkeypatch, _no_pin):
    from server import config

    monkeypatch.setattr(config, "TLS_ENABLED", False)
    monkeypatch.setattr(config, "HTTP_PORT", 8080)
    drv = _make_driver(_codec_def(), {"host": "127.0.0.1"})
    await drv._start_push()
    try:
        assert drv._push_subscription is not None
        assert drv.push_callback_url == "http://127.0.0.1:8080/api/push/dev1"
    finally:
        await drv._stop_push()
    assert drv._push_subscription is None
    assert drv.push_callback_url == ""


@pytest.mark.asyncio
async def test_push_callback_url_substitutes_in_commands(monkeypatch, _no_pin):
    from server import config

    monkeypatch.setattr(config, "TLS_ENABLED", False)
    monkeypatch.setattr(config, "HTTP_PORT", 8080)
    drv = _make_driver(_codec_def(), {"host": "127.0.0.1"})

    body = "<Register><Url>{push_callback_url}</Url></Register>"
    # Before the subscription starts, the token stays verbatim (loud in the
    # device log, instead of silently registering an empty URL).
    before = drv._safe_substitute(body, {**drv.config, **drv._push_params()})
    assert "{push_callback_url}" in before

    await drv._start_push()
    try:
        after = drv._safe_substitute(body, {**drv.config, **drv._push_params()})
        assert "<Url>http://127.0.0.1:8080/api/push/dev1</Url>" in after
    finally:
        await drv._stop_push()


@pytest.mark.asyncio
async def test_reconnect_resubscribes():
    drv = _make_driver(_codec_def(), {"host": "127.0.0.1"})
    await drv._start_push()
    first = drv._push_subscription
    await drv._stop_push()
    await drv._start_push()
    second = drv._push_subscription
    try:
        assert first is not second and second is not None
        status = await hl.dispatch(
            "dev1", "", hl.HTTPPushRequest(body=b"<Mute>1</Mute>", source_ip="127.0.0.1")
        )
        assert status == 200
    finally:
        await drv._stop_push()


@pytest.mark.asyncio
async def test_stop_push_is_idempotent():
    drv = _make_driver(_codec_def(), {"host": "127.0.0.1"})
    await drv._start_push()
    await drv._stop_push()
    await drv._stop_push()
    assert drv._push_subscription is None


# ===========================================================================
# Simulator emission (register_callback + POST-to-callback)
# ===========================================================================


def _sim_def() -> dict:
    d = _codec_def()
    d["simulator"] = {
        "initial_state": {"mute": False, "level": 100},
        "notifications": {
            "mute": {"*": "<Mute>{value:d}</Mute>"},
            "level": {"*": "<Level>{value}</Level>"},
        },
        "command_handlers": [
            {
                "match": r"POST /register\|<Register><Url>(.+)</Url></Register>",
                "handler": (
                    "register_callback(match.group(1))\n"
                    "respond('<OK/>')\n"
                ),
            },
        ],
    }
    return d


def test_sim_detects_http_listener_push():
    from simulator.yaml_auto import YAMLAutoSimulator

    sim = YAMLAutoSimulator(device_id="acme1", config={}, driver_def=_sim_def())
    assert sim._push_http is True
    assert sim._http_push_callbacks == []


def test_sim_register_callback_via_script_handler():
    from simulator.yaml_auto import YAMLAutoSimulator

    sim = YAMLAutoSimulator(device_id="acme1", config={}, driver_def=_sim_def())
    resp = sim.handle_command(
        b"POST /register|<Register><Url>http://10.0.0.2:8080/api/push/dev1</Url></Register>"
    )
    assert resp == b"<OK/>"
    assert sim._http_push_callbacks == ["http://10.0.0.2:8080/api/push/dev1"]
    # Re-registration (reconnect) is a no-op, not a duplicate.
    sim.handle_command(
        b"POST /register|<Register><Url>http://10.0.0.2:8080/api/push/dev1</Url></Register>"
    )
    assert len(sim._http_push_callbacks) == 1


@pytest.mark.asyncio
async def test_sim_posts_notification_to_registered_callback(monkeypatch):
    from simulator.yaml_auto import YAMLAutoSimulator

    sim = YAMLAutoSimulator(device_id="acme1", config={}, driver_def=_sim_def())
    posted: list[tuple[str, str]] = []

    async def fake_post(url: str, msg: str) -> None:
        posted.append((url, msg))

    monkeypatch.setattr(sim, "_post_http_callback", fake_post)
    sim.register_callback("http://10.0.0.2:8080/api/push/dev1")

    sim.set_state("mute", True)
    await asyncio.sleep(0)
    assert posted == [("http://10.0.0.2:8080/api/push/dev1", "<Mute>1</Mute>")]


@pytest.mark.asyncio
async def test_sim_without_registration_posts_nothing(monkeypatch):
    from simulator.yaml_auto import YAMLAutoSimulator

    sim = YAMLAutoSimulator(device_id="acme1", config={}, driver_def=_sim_def())
    posted: list[tuple[str, str]] = []

    async def fake_post(url: str, msg: str) -> None:
        posted.append((url, msg))

    monkeypatch.setattr(sim, "_post_http_callback", fake_post)
    sim.set_state("mute", True)
    await asyncio.sleep(0)
    assert posted == []


def test_sim_unregister_callback():
    from simulator.yaml_auto import YAMLAutoSimulator

    sim = YAMLAutoSimulator(device_id="acme1", config={}, driver_def=_sim_def())
    sim.register_callback("http://10.0.0.2:8080/api/push/dev1")
    sim.unregister_callback("http://10.0.0.2:8080/api/push/dev1")
    assert sim._http_push_callbacks == []
