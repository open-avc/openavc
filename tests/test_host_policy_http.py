"""Exercise the real FastAPI body parser and ASGI response lifetime."""
import asyncio
import base64

from fastapi import APIRouter, Body, FastAPI, HTTPException, Request
import pytest
from starlette.responses import StreamingResponse

from openavc.api import auth
from openavc.api.host_policy import (
    EndpointPolicy,
    PolicyRoute,
    configure_policy,
    endpoint_policy,
    policy_route,
    request_admission,
)
from openavc.core.host_policy import HostCategory, HostPolicy, PolicySnapshot


class MutableHost(HostPolicy):
    def __init__(self):
        super().__init__()
        self.current = PolicySnapshot(1, True, True)

    def snapshot(self):
        return self.current


def scope(path="/sample", method="POST"):
    return {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
            "method": method, "path": path, "raw_path": path.encode(),
            "query_string": b"", "root_path": "", "scheme": "http",
            "headers": [(b"content-type", b"application/json")],
            "server": ("test", 80), "client": ("127.0.0.1", 12345),
            "http_version": "1.1"}


def application(guard=None):
    app, host = FastAPI(), MutableHost()
    configure_policy(app, host)
    router = APIRouter(route_class=policy_route(EndpointPolicy(HostCategory.PROGRAMMER, guard)))
    return app, host, router


@pytest.mark.asyncio
async def test_disabled_route_never_requests_body_chunks():
    app, host, router = application()

    @router.post("/sample")
    async def sample(value: dict = Body()):
        raise AssertionError("Disabled endpoint was called")

    app.include_router(router)
    host.current = PolicySnapshot(2, False, True)
    sent = []

    async def receive():
        raise AssertionError("Disabled upload body was consumed")

    async def send(message):
        sent.append(message)

    await app(scope(), receive, send)
    assert sent[0]["status"] == 403
    assert (b"cache-control", b"no-store") in sent[0]["headers"]
    assert (await host.apply_current(timeout=0)).observed


@pytest.mark.asyncio
async def test_authentication_happens_before_body_parsing_on_enabled_route():
    async def authenticate(request):
        raise HTTPException(401, "Authentication required")
    app, host, router = application(authenticate)

    @router.post("/sample")
    async def sample(value: dict = Body()):
        raise AssertionError("Unauthenticated endpoint was called")

    app.include_router(router)
    sent = []

    async def receive():
        raise AssertionError("Unauthenticated upload body was consumed")

    async def send(message):
        sent.append(message)

    await app(scope(), receive, send)
    assert sent[0]["status"] == 401


@pytest.mark.asyncio
async def test_normal_parser_schema_and_explicit_runtime_exception_survive_inclusion():
    app, host, router = application()

    @router.post("/sample")
    async def sample(value: dict = Body()):
        return value

    @router.get("/health")
    @endpoint_policy(EndpointPolicy(HostCategory.RUNTIME))
    async def health():
        return {"ready": True}

    app.include_router(router)
    assert "requestBody" in app.openapi()["paths"]["/sample"]["post"]
    sent = []

    async def receive():
        return {"type": "http.request", "body": b'{"value":7}', "more_body": False}

    async def send(message):
        sent.append(message)

    await app(scope(), receive, send)
    assert sent[0]["status"] == 200 and sent[-1]["body"] == b'{"value":7}'
    host.current = PolicySnapshot(2, False, False)
    sent.clear()
    await app(scope("/health", "GET"), receive, send)
    assert sent[0]["status"] == 200 and sent[-1]["body"] == b'{"ready":true}'


@pytest.mark.asyncio
async def test_stream_revocation_waits_for_response_cleanup_and_owned_commit():
    app, host, router = application()
    started, stream_closed = asyncio.Event(), asyncio.Event()
    ending, release_end = asyncio.Event(), asyncio.Event()
    owned_commit = []

    @router.get("/sample")
    async def sample(request: Request):
        owned_commit.append(request_admission(request).begin_commit())

        async def chunks():
            try:
                yield b"first"
                started.set()
                await asyncio.Event().wait()
            finally:
                stream_closed.set()
        return StreamingResponse(chunks())

    app.include_router(router)
    sent = []

    async def receive():
        await asyncio.Event().wait()

    async def send(message):
        sent.append(message)
        if message["type"] == "http.response.body" and not message.get("more_body", False):
            ending.set()
            await release_end.wait()

    task = asyncio.create_task(app(scope(method="GET"), receive, send))
    await asyncio.wait_for(started.wait(), 1)
    host.current = PolicySnapshot(2, False, True)
    pending = await host.apply_current(timeout=0)
    assert pending.pending_connections == pending.pending_commits == 1
    await asyncio.wait_for(ending.wait(), 1)
    pending = await host.apply_current(timeout=0)
    assert pending.pending_connections == 1  # Transport has not finished closing.
    release_end.set()
    await asyncio.wait_for(task, 1)
    assert stream_closed.is_set()
    pending = await host.apply_current(timeout=0)
    assert pending.pending_connections == 0 and pending.pending_commits == 1
    owned_commit[0].finish("committed")
    assert (await host.apply_current(timeout=0)).observed
    assert sent[-1] == {"type": "http.response.body", "body": b"", "more_body": False}


def test_missing_metadata_and_late_provider_replacement_are_refused():
    async def sample():
        return {}
    with pytest.raises(ValueError):
        PolicyRoute("/unclassified", sample)
    app, _, _ = application()
    app.state.host_policy_started = True
    with pytest.raises(RuntimeError):
        configure_policy(app, HostPolicy())


@pytest.mark.asyncio
async def test_canonical_credentials_work_and_bad_credentials_do_not_consume_upload(isolated_auth_config):
    cfg = isolated_auth_config
    cfg.set("auth", "programmer_username", "engineer")
    cfg.set("auth", "programmer_password", "pässwörd-for-disposable-test")
    cfg.set("auth", "api_key", "")
    app, _, router = application(auth.authenticate_programmer_request)

    @router.post("/sample")
    async def sample(value: dict = Body()):
        return value

    app.include_router(router)
    consumed, sent = [], []

    async def receive():
        consumed.append(True)
        return {"type": "http.request", "body": b'{"ok":true}', "more_body": False}

    async def send(message):
        sent.append(message)

    await app(scope(), receive, send)
    assert sent[0]["status"] == 401 and not consumed
    sent.clear()
    request_scope = scope()
    credential = base64.b64encode("engineer:pässwörd-for-disposable-test".encode())
    request_scope["headers"].append((b"authorization", b"Basic " + credential))
    await app(request_scope, receive, send)
    assert consumed and sent[0]["status"] == 200
    assert sent[-1]["body"] == b'{"ok":true}'
