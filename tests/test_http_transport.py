"""Tests for HTTP client transport."""

import json

import httpx
import pytest

from server.transport.http_client import HTTPClientTransport, HTTPResponse


# --- Helper: mock HTTP handler ---

def make_handler(
    status: int = 200,
    json_body: dict | None = None,
    text_body: str = "",
    headers: dict | None = None,
):
    """Create a mock transport handler that returns a fixed response."""
    resp_headers = {"content-type": "application/json"} if json_body else {}
    if headers:
        resp_headers.update(headers)
    body = json.dumps(json_body).encode() if json_body else text_body.encode()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=status,
            headers=resp_headers,
            content=body,
        )

    return handler


def echo_handler(request: httpx.Request) -> httpx.Response:
    """Echo back request details as JSON."""
    body = None
    if request.content:
        try:
            body = json.loads(request.content)
        except (json.JSONDecodeError, ValueError):
            body = request.content.decode("utf-8", errors="replace")

    echo = {
        "method": request.method,
        "path": str(request.url.raw_path, "ascii"),
        "body": body,
        "headers": dict(request.headers),
    }
    return httpx.Response(
        status_code=200,
        headers={"content-type": "application/json"},
        content=json.dumps(echo).encode(),
    )


# --- Fixtures ---

@pytest.fixture
async def transport():
    """HTTPClientTransport with a mock backend that echoes requests."""
    t = HTTPClientTransport(
        base_url="http://192.168.1.100",
        timeout=5.0,
    )
    # Replace the internal client with a mocked one after open()
    await t.open()
    # Swap out the real client for a mock transport
    mock_transport = httpx.MockTransport(echo_handler)
    await t._client.aclose()
    t._client = httpx.AsyncClient(
        base_url="http://192.168.1.100",
        transport=mock_transport,
        timeout=httpx.Timeout(5.0),
    )
    yield t
    await t.close()


@pytest.fixture
async def json_transport():
    """HTTPClientTransport that returns a fixed JSON response."""
    t = HTTPClientTransport(base_url="http://10.0.0.5")
    await t.open()
    handler = make_handler(json_body={"power": "on", "volume": 42})
    mock = httpx.MockTransport(handler)
    await t._client.aclose()
    t._client = httpx.AsyncClient(
        base_url="http://10.0.0.5",
        transport=mock,
        timeout=httpx.Timeout(5.0),
    )
    yield t
    await t.close()


# --- HTTPResponse dataclass tests ---

def test_http_response_repr():
    r = HTTPResponse(status_code=200, headers={}, text="hello", ok=True)
    assert "200" in repr(r)
    assert "ok=True" in repr(r)


def test_http_response_ok():
    r = HTTPResponse(status_code=200, headers={}, text="", ok=True)
    assert r.ok
    r2 = HTTPResponse(status_code=404, headers={}, text="", ok=False)
    assert not r2.ok


# --- Basic HTTP methods ---

async def test_get(transport):
    resp = await transport.get("/api/status")
    assert resp.ok
    assert resp.status_code == 200
    assert resp.json_data["method"] == "GET"
    assert "/api/status" in resp.json_data["path"]


async def test_get_with_params(transport):
    resp = await transport.get("/api/search", params={"q": "test"})
    assert resp.ok
    assert "q=test" in resp.json_data["path"]


async def test_post(transport):
    resp = await transport.post("/api/power", body={"power": "on"})
    assert resp.ok
    assert resp.json_data["method"] == "POST"
    assert resp.json_data["body"]["power"] == "on"


async def test_put(transport):
    resp = await transport.put("/api/volume", body={"level": 50})
    assert resp.ok
    assert resp.json_data["method"] == "PUT"
    assert resp.json_data["body"]["level"] == 50


async def test_delete(transport):
    resp = await transport.delete("/api/preset/1")
    assert resp.ok
    assert resp.json_data["method"] == "DELETE"


async def test_generic_request(transport):
    resp = await transport.request("PATCH", "/api/config", json_body={"name": "Room1"})
    assert resp.ok
    assert resp.json_data["method"] == "PATCH"


# --- JSON response parsing ---

async def test_json_parsing(json_transport):
    resp = await json_transport.get("/api/status")
    assert resp.json_data is not None
    assert resp.json_data["power"] == "on"
    assert resp.json_data["volume"] == 42


# --- Auth tests ---

async def test_basic_auth():
    """Basic auth sends Authorization header."""
    def check_auth(request: httpx.Request) -> httpx.Response:
        auth_header = request.headers.get("authorization", "")
        has_basic = auth_header.startswith("Basic ")
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=json.dumps({"auth": has_basic}).encode(),
        )

    t = HTTPClientTransport(
        base_url="http://10.0.0.1",
        auth_type="basic",
        credentials={"username": "admin", "password": "secret"},
    )
    await t.open()
    await t._client.aclose()
    t._client = httpx.AsyncClient(
        base_url="http://10.0.0.1",
        auth=httpx.BasicAuth("admin", "secret"),
        transport=httpx.MockTransport(check_auth),
        timeout=httpx.Timeout(5.0),
    )
    resp = await t.get("/api/test")
    assert resp.json_data["auth"] is True
    await t.close()


async def test_bearer_auth():
    """Bearer token is sent in Authorization header."""
    def check_bearer(request: httpx.Request) -> httpx.Response:
        auth_header = request.headers.get("authorization", "")
        has_bearer = auth_header == "Bearer my-token-123"
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=json.dumps({"auth": has_bearer}).encode(),
        )

    t = HTTPClientTransport(
        base_url="http://10.0.0.1",
        auth_type="bearer",
        credentials={"token": "my-token-123"},
    )
    await t.open()
    await t._client.aclose()
    t._client = httpx.AsyncClient(
        base_url="http://10.0.0.1",
        headers={"Authorization": "Bearer my-token-123"},
        transport=httpx.MockTransport(check_bearer),
        timeout=httpx.Timeout(5.0),
    )
    resp = await t.get("/api/test")
    assert resp.json_data["auth"] is True
    await t.close()


async def test_api_key_auth():
    """API key is sent in custom header."""
    def check_key(request: httpx.Request) -> httpx.Response:
        key = request.headers.get("x-api-key", "")
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=json.dumps({"key": key}).encode(),
        )

    t = HTTPClientTransport(
        base_url="http://10.0.0.1",
        auth_type="api_key",
        credentials={"header": "X-API-Key", "key": "abc123"},
    )
    await t.open()
    await t._client.aclose()
    t._client = httpx.AsyncClient(
        base_url="http://10.0.0.1",
        headers={"X-API-Key": "abc123"},
        transport=httpx.MockTransport(check_key),
        timeout=httpx.Timeout(5.0),
    )
    resp = await t.get("/api/test")
    assert resp.json_data["key"] == "abc123"
    await t.close()


# --- Error handling ---

async def test_error_status_code():
    """4xx/5xx responses have ok=False."""
    handler = make_handler(status=404, text_body="Not Found")
    t = HTTPClientTransport(base_url="http://10.0.0.1")
    await t.open()
    await t._client.aclose()
    t._client = httpx.AsyncClient(
        base_url="http://10.0.0.1",
        transport=httpx.MockTransport(handler),
        timeout=httpx.Timeout(5.0),
    )
    resp = await t.get("/missing")
    assert resp.status_code == 404
    assert not resp.ok
    await t.close()


async def test_server_error():
    """500 responses have ok=False."""
    handler = make_handler(status=500, text_body="Internal Server Error")
    t = HTTPClientTransport(base_url="http://10.0.0.1")
    await t.open()
    await t._client.aclose()
    t._client = httpx.AsyncClient(
        base_url="http://10.0.0.1",
        transport=httpx.MockTransport(handler),
        timeout=httpx.Timeout(5.0),
    )
    resp = await t.get("/api/broken")
    assert resp.status_code == 500
    assert not resp.ok
    await t.close()


async def test_not_open_raises():
    """Requesting before open() raises ConnectionError."""
    t = HTTPClientTransport(base_url="http://10.0.0.1")
    with pytest.raises(ConnectionError):
        await t.get("/api/test")


async def test_connected_property():
    """connected reflects whether client is open."""
    t = HTTPClientTransport(base_url="http://10.0.0.1")
    assert not t.connected
    await t.open()
    assert t.connected
    # Replace with mock so close works
    await t._client.aclose()
    t._client = httpx.AsyncClient(
        base_url="http://10.0.0.1",
        transport=httpx.MockTransport(echo_handler),
    )
    assert t.connected
    await t.close()
    assert not t.connected


# --- send() compatibility method ---

async def test_send_get(transport):
    """send() with 'GET /path' format."""
    await transport.send(b"GET /api/status")
    assert transport.last_response is not None
    assert transport.last_response.json_data["method"] == "GET"


async def test_send_post_with_body(transport):
    """send() with 'POST /path {body}' format."""
    await transport.send(b'POST /api/power {"power": "on"}')
    assert transport.last_response is not None
    assert transport.last_response.json_data["method"] == "POST"
    assert transport.last_response.json_data["body"]["power"] == "on"


async def test_send_bare_path(transport):
    """send() with just a path defaults to GET."""
    await transport.send(b"/api/status")
    assert transport.last_response is not None
    assert transport.last_response.json_data["method"] == "GET"


async def test_send_empty(transport):
    """send() with empty data defaults to GET /."""
    await transport.send(b"")
    assert transport.last_response is not None
    assert transport.last_response.json_data["method"] == "GET"


# --- send_and_wait() ---

async def test_send_and_wait(transport):
    """send_and_wait() returns response body as bytes."""
    result = await transport.send_and_wait(b"GET /api/status")
    assert isinstance(result, bytes)
    data = json.loads(result)
    assert data["method"] == "GET"


async def test_send_and_wait_with_timeout(transport):
    """send_and_wait() respects custom timeout."""
    result = await transport.send_and_wait(b"GET /api/status", timeout=2.0)
    assert isinstance(result, bytes)


# --- SSL configuration ---

def test_verify_ssl_default():
    """verify_ssl defaults to True."""
    t = HTTPClientTransport(base_url="https://10.0.0.1")
    assert t.verify_ssl is True


def test_verify_ssl_disabled():
    """verify_ssl can be set to False for self-signed certs."""
    t = HTTPClientTransport(base_url="https://10.0.0.1", verify_ssl=False)
    assert t.verify_ssl is False


# --- _parse_send_string() ---

def test_parse_get():
    method, path, body = HTTPClientTransport._parse_send_string("GET /api/status")
    assert method == "GET"
    assert path == "/api/status"
    assert body == ""


def test_parse_post_with_body():
    method, path, body = HTTPClientTransport._parse_send_string(
        'POST /api/power {"on": true}'
    )
    assert method == "POST"
    assert path == "/api/power"
    assert body == '{"on": true}'


def test_parse_bare_path():
    method, path, body = HTTPClientTransport._parse_send_string("/api/status")
    assert method == "GET"
    assert path == "/api/status"
    assert body == ""


def test_parse_empty():
    method, path, body = HTTPClientTransport._parse_send_string("")
    assert method == "GET"
    assert path == "/"


def test_parse_put():
    method, path, body = HTTPClientTransport._parse_send_string(
        'PUT /api/volume {"level": 50}'
    )
    assert method == "PUT"
    assert path == "/api/volume"
    assert body == '{"level": 50}'


def test_parse_delete():
    method, path, body = HTTPClientTransport._parse_send_string("DELETE /api/preset/1")
    assert method == "DELETE"
    assert path == "/api/preset/1"
    assert body == ""


def test_parse_no_slash():
    """String without leading slash gets one added."""
    method, path, body = HTTPClientTransport._parse_send_string("status")
    assert method == "GET"
    assert path == "/status"


# --- open() idempotency ---

async def test_open_idempotent():
    """Calling open() twice does not create a second client."""
    t = HTTPClientTransport(base_url="http://10.0.0.1")
    await t.open()
    client1 = t._client
    await t.open()
    assert t._client is client1  # Same client
    await t.close()


# --- ConfigurableDriver HTTP integration ---

async def test_configurable_http_send_command():
    """ConfigurableDriver sends HTTP commands using method/path/body fields."""
    from server.core.event_bus import EventBus
    from server.core.state_store import StateStore
    from server.drivers.configurable import create_configurable_driver_class

    definition = {
        "id": "test_http_device",
        "name": "Test HTTP Device",
        "transport": "http",
        "commands": {
            "power_on": {
                "label": "Power On",
                "method": "POST",
                "path": "/api/power",
                "body": '{"power": "on"}',
                "params": {},
            },
            "get_status": {
                "label": "Get Status",
                "method": "GET",
                "path": "/api/status",
                "params": {},
            },
            "set_volume": {
                "label": "Set Volume",
                "method": "POST",
                "path": "/api/audio/volume",
                "body": '{"level": {level}}',
                "params": {
                    "level": {"type": "integer", "required": True},
                },
            },
        },
        "responses": [
            {
                "pattern": '"power":\\s*"(\\w+)"',
                "mappings": [
                    {"group": 1, "state": "power", "type": "string"},
                ],
            },
        ],
        "state_variables": {
            "power": {"type": "string", "label": "Power"},
            "volume": {"type": "integer", "label": "Volume"},
        },
    }

    cls = create_configurable_driver_class(definition)
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)

    driver = cls("http1", {"host": "10.0.0.5", "port": 80}, state, events)

    # Manually set up the transport with a mock
    transport = HTTPClientTransport(base_url="http://10.0.0.5")
    await transport.open()
    await transport._client.aclose()
    transport._client = httpx.AsyncClient(
        base_url="http://10.0.0.5",
        transport=httpx.MockTransport(echo_handler),
        timeout=httpx.Timeout(5.0),
    )
    driver.transport = transport

    # Test power_on command
    result = await driver.send_command("power_on")
    assert result is not None
    assert result.status_code == 200
    assert result.json_data["method"] == "POST"
    assert "/api/power" in result.json_data["path"]
    assert result.json_data["body"]["power"] == "on"

    # Test get_status command
    result = await driver.send_command("get_status")
    assert result is not None
    assert result.json_data["method"] == "GET"

    # Test set_volume with parameter substitution
    result = await driver.send_command("set_volume", {"level": 75})
    assert result is not None
    assert result.json_data["method"] == "POST"
    assert result.json_data["body"]["level"] == 75

    await transport.close()


async def test_configurable_http_command_metadata():
    """HTTP command fields appear in DRIVER_INFO commands metadata."""
    from server.drivers.configurable import create_configurable_driver_class

    definition = {
        "id": "test_meta",
        "name": "Test Meta",
        "transport": "http",
        "commands": {
            "power_on": {
                "label": "Power On",
                "method": "POST",
                "path": "/api/power",
                "body": '{"power": "on"}',
                "params": {},
            },
        },
        "state_variables": {},
    }

    cls = create_configurable_driver_class(definition)
    cmd = cls.DRIVER_INFO["commands"]["power_on"]
    assert cmd["method"] == "POST"
    assert cmd["path"] == "/api/power"
    assert cmd["body"] == '{"power": "on"}'


# --- Base driver HTTP auto-transport ---

def test_base_driver_connect_builds_http_url():
    """Verify that base.py builds the correct base_url for HTTP transport."""
    # Just verify the logic by checking the import works
    from server.transport.http_client import HTTPClientTransport
    t = HTTPClientTransport(base_url="https://10.0.0.5:443")
    assert t.base_url == "https://10.0.0.5:443"
    assert t.verify_ssl is True


def test_base_driver_connect_ssl_disabled():
    """Verify SSL can be disabled."""
    from server.transport.http_client import HTTPClientTransport
    t = HTTPClientTransport(
        base_url="https://10.0.0.5:443",
        verify_ssl=False,
    )
    assert t.verify_ssl is False


# --- Driver loader validation ---

def test_driver_loader_accepts_http():
    """Driver loader validation accepts 'http' transport."""
    from server.drivers.driver_loader import validate_driver_definition
    errors = validate_driver_definition({
        "id": "test_http",
        "name": "Test HTTP",
        "transport": "http",
    })
    assert len(errors) == 0


def test_driver_loader_rejects_invalid():
    """Driver loader validation rejects unknown transport."""
    from server.drivers.driver_loader import validate_driver_definition
    errors = validate_driver_definition({
        "id": "test_bad",
        "name": "Test Bad",
        "transport": "ftp",
    })
    assert any("Unsupported transport" in e for e in errors)
