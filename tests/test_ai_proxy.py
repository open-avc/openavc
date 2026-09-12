"""Tests for the AI proxy — routes AI requests to cloud via HMAC auth."""

import json
from unittest.mock import patch, AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from openavc.api.ai_proxy import (
    router,
    set_engine,
    _get_cloud_api_url,
    _get_system_key_bytes,
    _sign_request,
    _check_cloud_ready,
    _error_json,
    _error_message,
)


# --- Fixtures ---


@pytest.fixture
def app():
    """FastAPI app with AI proxy router mounted."""
    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture
async def client(app):
    """Async test client."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture(autouse=True)
def reset_engine():
    """Reset engine between tests."""
    set_engine(None)
    yield
    set_engine(None)


# --- _get_cloud_api_url tests ---


class TestGetCloudApiUrl:
    def test_wss_to_https(self):
        with patch("openavc.api.ai_proxy.cfg") as mock_cfg:
            mock_cfg.CLOUD_ENDPOINT = "wss://cloud.openavc.com/agent/v1"
            assert _get_cloud_api_url() == "https://cloud.openavc.com"

    def test_ws_to_http(self):
        with patch("openavc.api.ai_proxy.cfg") as mock_cfg:
            mock_cfg.CLOUD_ENDPOINT = "ws://localhost:8000/agent/v1"
            assert _get_cloud_api_url() == "http://localhost:8000"

    def test_empty_endpoint(self):
        with patch("openavc.api.ai_proxy.cfg") as mock_cfg:
            mock_cfg.CLOUD_ENDPOINT = ""
            assert _get_cloud_api_url() == ""

    def test_no_agent_path(self):
        with patch("openavc.api.ai_proxy.cfg") as mock_cfg:
            mock_cfg.CLOUD_ENDPOINT = "wss://cloud.openavc.com"
            assert _get_cloud_api_url() == "https://cloud.openavc.com"


# --- _get_system_key_bytes tests ---


class TestGetSystemKeyBytes:
    def test_empty_key(self):
        with patch("openavc.api.ai_proxy.cfg") as mock_cfg:
            mock_cfg.CLOUD_SYSTEM_KEY = ""
            assert _get_system_key_bytes() == b""

    def test_hex_key(self):
        with patch("openavc.api.ai_proxy.cfg") as mock_cfg:
            mock_cfg.CLOUD_SYSTEM_KEY = "abcdef0123456789"
            result = _get_system_key_bytes()
            assert result == bytes.fromhex("abcdef0123456789")

    def test_bytes_key(self):
        with patch("openavc.api.ai_proxy.cfg") as mock_cfg:
            mock_cfg.CLOUD_SYSTEM_KEY = b"\x01\x02\x03"
            assert _get_system_key_bytes() == b"\x01\x02\x03"

    def test_non_hex_string_key(self):
        with patch("openavc.api.ai_proxy.cfg") as mock_cfg:
            mock_cfg.CLOUD_SYSTEM_KEY = "not-hex-key"
            result = _get_system_key_bytes()
            assert result == b"not-hex-key"


# --- _sign_request tests ---


class TestSignRequest:
    def test_returns_required_headers(self):
        system_id = "sys-123"
        system_key = b"\x00" * 64
        body = b'{"message": "hello"}'
        headers = _sign_request(system_id, system_key, body)
        assert "X-System-ID" in headers
        assert "X-Timestamp" in headers
        assert "X-Signature" in headers
        assert headers["X-System-ID"] == "sys-123"

    def test_signature_is_hex_string(self):
        headers = _sign_request("sys-1", b"\x00" * 64, b"test")
        sig = headers["X-Signature"]
        # Should be a valid hex string
        int(sig, 16)

    def test_different_bodies_produce_different_signatures(self):
        key = b"\x00" * 64
        h1 = _sign_request("sys-1", key, b"body1")
        h2 = _sign_request("sys-1", key, b"body2")
        assert h1["X-Signature"] != h2["X-Signature"]

    def test_different_keys_produce_different_signatures(self):
        h1 = _sign_request("sys-1", b"\x00" * 64, b"body")
        h2 = _sign_request("sys-1", b"\x01" * 64, b"body")
        assert h1["X-Signature"] != h2["X-Signature"]


# --- _check_cloud_ready tests ---


class TestCheckCloudReady:
    def test_cloud_not_enabled_raises_503(self):
        with patch("openavc.api.ai_proxy.cfg") as mock_cfg:
            mock_cfg.CLOUD_ENABLED = False
            from fastapi import HTTPException
            with pytest.raises(HTTPException) as exc_info:
                _check_cloud_ready()
            assert exc_info.value.status_code == 503
            assert "not enabled" in exc_info.value.detail

    def test_missing_system_id_raises_503(self):
        with patch("openavc.api.ai_proxy.cfg") as mock_cfg:
            mock_cfg.CLOUD_ENABLED = True
            mock_cfg.CLOUD_SYSTEM_ID = ""
            mock_cfg.CLOUD_SYSTEM_KEY = "abcd"
            mock_cfg.CLOUD_ENDPOINT = "wss://cloud.openavc.com/agent/v1"
            from fastapi import HTTPException
            with pytest.raises(HTTPException) as exc_info:
                _check_cloud_ready()
            assert exc_info.value.status_code == 503

    def test_success_returns_tuple(self):
        with patch("openavc.api.ai_proxy.cfg") as mock_cfg:
            mock_cfg.CLOUD_ENABLED = True
            mock_cfg.CLOUD_SYSTEM_ID = "sys-123"
            mock_cfg.CLOUD_SYSTEM_KEY = "aabb"
            mock_cfg.CLOUD_ENDPOINT = "wss://cloud.openavc.com/agent/v1"
            api_url, system_id, system_key = _check_cloud_ready()
            assert api_url == "https://cloud.openavc.com"
            assert system_id == "sys-123"
            assert isinstance(system_key, bytes)


# --- _error_json tests ---


class TestErrorJson:
    def test_429_message(self):
        result = json.loads(_error_json(429, b"rate limited"))
        assert "limit reached" in result["message"]

    def test_402_message(self):
        result = json.loads(_error_json(402, b"payment required"))
        assert "subscription" in result["message"]

    def test_503_message(self):
        result = json.loads(_error_json(503, b"unavailable"))
        assert "not available" in result["message"]

    def test_generic_error_extracts_detail(self):
        body = json.dumps({"detail": "Something went wrong"}).encode()
        result = json.loads(_error_json(500, body))
        assert result["message"] == "Something went wrong"

    def test_non_json_body(self):
        result = json.loads(_error_json(500, b"plain text error"))
        assert "plain text error" in result["message"]


class TestErrorMessage:
    """_error_message is shared by the streaming and non-streaming paths so
    neither relays a raw cloud body verbatim (L-168)."""

    def test_maps_known_statuses(self):
        assert "limit reached" in _error_message(429, b"whatever")
        assert "subscription" in _error_message(402, b"whatever")
        assert "not available" in _error_message(503, b"whatever")

    def test_extracts_json_detail(self):
        body = json.dumps({"detail": "Model not found"}).encode()
        assert _error_message(400, body) == "Model not found"

    def test_truncates_long_raw_body(self):
        """A long non-JSON cloud body is truncated, not forwarded whole."""
        body = b"x" * 5000
        msg = _error_message(500, body)
        assert len(msg) <= 200

    def test_relays_the_clouds_own_503_sentence(self):
        """Q-176: every 503 read as "AI service is not available" — including a
        plan refusal the customer could have acted on."""
        body = json.dumps({"detail": "The AI assistant is paused on this account."}).encode()
        assert _error_message(503, body) == "The AI assistant is paused on this account."

    def test_relays_the_clouds_own_402_sentence(self):
        body = json.dumps(
            {"detail": "This account has used the allowance that comes with a free account."}
        ).encode()
        assert (
            _error_message(402, body)
            == "This account has used the allowance that comes with a free account."
        )

    def test_relays_the_clouds_own_429_sentence(self):
        body = json.dumps({"detail": "Rate limit exceeded (60 requests/minute). Please wait."}).encode()
        assert _error_message(429, body) == "Rate limit exceeded (60 requests/minute). Please wait."

    def test_a_non_json_503_body_never_reaches_the_browser(self):
        """An intermediary's HTML 503 is not the cloud's sentence."""
        msg = _error_message(503, b"<html><body>502 Bad Gateway</body></html>")
        assert msg == "AI service is not available."

    def test_an_empty_detail_is_not_a_sentence(self):
        assert _error_message(503, json.dumps({"detail": "   "}).encode()) == (
            "AI service is not available."
        )

    def test_a_non_string_detail_is_not_a_sentence(self):
        body = json.dumps({"detail": [{"loc": ["body"], "msg": "field required"}]}).encode()
        assert _error_message(503, body) == "AI service is not available."


# --- API endpoint tests ---


def _connected_engine():
    engine = MagicMock()
    engine.cloud_agent.get_status.return_value = {"connected": True}
    return engine


def _paired_cfg(mock_cfg):
    mock_cfg.CLOUD_ENABLED = True
    mock_cfg.CLOUD_SYSTEM_ID = "sys-123"
    mock_cfg.CLOUD_SYSTEM_KEY = "aa" * 32
    mock_cfg.CLOUD_ENDPOINT = "wss://cloud.openavc.com/agent/v1"


def _cloud_get(status_code, json_body=None, raises=None):
    """Patch context for one GET to the cloud."""
    mock_response = MagicMock()
    mock_response.status_code = status_code
    if json_body is None:
        mock_response.json.side_effect = ValueError("not json")
    else:
        mock_response.json.return_value = json_body

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    if raises is not None:
        mock_client.get = AsyncMock(side_effect=raises)
    else:
        mock_client.get = AsyncMock(return_value=mock_response)

    ctx = patch("httpx.AsyncClient")
    return ctx, mock_client


class TestAiStatusEndpoint:
    async def test_cloud_not_enabled(self, client):
        with patch("openavc.api.ai_proxy.cfg") as mock_cfg:
            mock_cfg.CLOUD_ENABLED = False
            mock_cfg.CLOUD_SYSTEM_ID = ""
            resp = await client.get("/api/ai/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["available"] is False
        assert data["state"] == "unpaired"

    async def test_cloud_enabled_but_no_agent(self, client):
        with patch("openavc.api.ai_proxy.cfg") as mock_cfg:
            mock_cfg.CLOUD_ENABLED = True
            mock_cfg.CLOUD_SYSTEM_ID = "sys-123"
            set_engine(None)
            resp = await client.get("/api/ai/status")
        data = resp.json()
        assert data["available"] is False
        assert data["state"] == "disconnected"

    async def test_cloud_agent_connected(self, client):
        set_engine(_connected_engine())
        ctx, mock_client = _cloud_get(200, {"available": True, "reason": None})
        with patch("openavc.api.ai_proxy.cfg") as mock_cfg:
            _paired_cfg(mock_cfg)
            with ctx as mock_client_cls:
                mock_client_cls.return_value = mock_client
                resp = await client.get("/api/ai/status")
        data = resp.json()
        assert data["available"] is True
        assert data["state"] == "available"

    async def test_cloud_agent_disconnected(self, client):
        engine = MagicMock()
        engine.cloud_agent.get_status.return_value = {"connected": False}
        set_engine(engine)
        with patch("openavc.api.ai_proxy.cfg") as mock_cfg:
            mock_cfg.CLOUD_ENABLED = True
            mock_cfg.CLOUD_SYSTEM_ID = "sys-123"
            resp = await client.get("/api/ai/status")
        data = resp.json()
        assert data["available"] is False
        assert data["state"] == "disconnected"

    async def test_connected_but_the_cloud_refuses_is_not_available(self, client):
        """The finding: connected said available, and every prompt then 503'd."""
        set_engine(_connected_engine())
        ctx, mock_client = _cloud_get(
            200,
            {
                "available": False,
                "reason": "This account has used the AI assistant allowance.",
            },
        )
        with patch("openavc.api.ai_proxy.cfg") as mock_cfg:
            _paired_cfg(mock_cfg)
            with ctx as mock_client_cls:
                mock_client_cls.return_value = mock_client
                resp = await client.get("/api/ai/status")
        data = resp.json()
        assert data["available"] is False
        assert data["state"] == "unavailable"
        assert data["reason"] == "This account has used the AI assistant allowance."

    async def test_refusal_without_a_sentence_still_gets_one(self, client):
        set_engine(_connected_engine())
        ctx, mock_client = _cloud_get(200, {"available": False})
        with patch("openavc.api.ai_proxy.cfg") as mock_cfg:
            _paired_cfg(mock_cfg)
            with ctx as mock_client_cls:
                mock_client_cls.return_value = mock_client
                resp = await client.get("/api/ai/status")
        data = resp.json()
        assert data["available"] is False
        assert data["reason"]

    async def test_older_cloud_without_the_route_stays_available(self, client):
        """A 404 means we could not ask, not that the answer is no.

        Every cloud in the field predates this route, so a probe that read a
        404 as a refusal would switch the assistant off for everybody until the
        cloud deploys.
        """
        set_engine(_connected_engine())
        ctx, mock_client = _cloud_get(404, {"detail": "Not Found"})
        with patch("openavc.api.ai_proxy.cfg") as mock_cfg:
            _paired_cfg(mock_cfg)
            with ctx as mock_client_cls:
                mock_client_cls.return_value = mock_client
                resp = await client.get("/api/ai/status")
        data = resp.json()
        assert data["available"] is True
        assert data["state"] == "available"

    async def test_unreachable_status_door_stays_available(self, client):
        set_engine(_connected_engine())
        ctx, mock_client = _cloud_get(200, raises=httpx.TimeoutException("timed out"))
        with patch("openavc.api.ai_proxy.cfg") as mock_cfg:
            _paired_cfg(mock_cfg)
            with ctx as mock_client_cls:
                mock_client_cls.return_value = mock_client
                resp = await client.get("/api/ai/status")
        assert resp.json()["available"] is True

    async def test_unparseable_answer_stays_available(self, client):
        set_engine(_connected_engine())
        ctx, mock_client = _cloud_get(200, None)
        with patch("openavc.api.ai_proxy.cfg") as mock_cfg:
            _paired_cfg(mock_cfg)
            with ctx as mock_client_cls:
                mock_client_cls.return_value = mock_client
                resp = await client.get("/api/ai/status")
        assert resp.json()["available"] is True

    async def test_the_probe_never_500s_the_ide(self, client):
        """Whatever the probe trips over, status still answers."""
        set_engine(_connected_engine())
        with patch("openavc.api.ai_proxy.cfg") as mock_cfg:
            mock_cfg.CLOUD_ENABLED = True
            mock_cfg.CLOUD_SYSTEM_ID = "sys-123"
            # left as MagicMocks: signing blows up on them
            resp = await client.get("/api/ai/status")
        assert resp.status_code == 200
        assert resp.json()["available"] is True


class TestAiChatEndpoint:
    async def test_cloud_not_ready_returns_503(self, client):
        with patch("openavc.api.ai_proxy.cfg") as mock_cfg:
            mock_cfg.CLOUD_ENABLED = False
            resp = await client.post("/api/ai/chat", content=b'{"message": "hi"}')
        assert resp.status_code == 503

    async def test_non_streaming_success(self, client):
        """Non-streaming chat proxies to cloud and returns JSON."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"response": "hello"}

        with patch("openavc.api.ai_proxy.cfg") as mock_cfg:
            mock_cfg.CLOUD_ENABLED = True
            mock_cfg.CLOUD_SYSTEM_ID = "sys-123"
            mock_cfg.CLOUD_SYSTEM_KEY = "aa" * 32
            mock_cfg.CLOUD_ENDPOINT = "wss://cloud.openavc.com/agent/v1"

            with patch("httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=False)
                mock_client.post = AsyncMock(return_value=mock_response)
                mock_client_cls.return_value = mock_client

                resp = await client.post("/api/ai/chat", content=b'{"message": "hi"}')

        assert resp.status_code == 200
        assert resp.json() == {"response": "hello"}

    async def test_non_streaming_cloud_error(self, client):
        """Non-streaming chat relays the cloud error STATUS but a sanitized
        detail — not the raw cloud body verbatim (L-168)."""
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.content = b"Rate limited: account 4c9f... over quota"

        with patch("openavc.api.ai_proxy.cfg") as mock_cfg:
            mock_cfg.CLOUD_ENABLED = True
            mock_cfg.CLOUD_SYSTEM_ID = "sys-123"
            mock_cfg.CLOUD_SYSTEM_KEY = "aa" * 32
            mock_cfg.CLOUD_ENDPOINT = "wss://cloud.openavc.com/agent/v1"

            with patch("httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=False)
                mock_client.post = AsyncMock(return_value=mock_response)
                mock_client_cls.return_value = mock_client

                resp = await client.post("/api/ai/chat", content=b'{"message": "hi"}')

        assert resp.status_code == 429
        # The friendly 429 message, not the raw cloud body.
        detail = resp.json()["detail"]
        assert "limit reached" in detail
        assert "over quota" not in detail

    async def test_non_streaming_cloud_timeout_is_graceful(self, client):
        """A cloud timeout on the non-streaming path yields a graceful 504, not
        a raw 500 (L-167)."""
        import httpx as _httpx

        with patch("openavc.api.ai_proxy.cfg") as mock_cfg:
            mock_cfg.CLOUD_ENABLED = True
            mock_cfg.CLOUD_SYSTEM_ID = "sys-123"
            mock_cfg.CLOUD_SYSTEM_KEY = "aa" * 32
            mock_cfg.CLOUD_ENDPOINT = "wss://cloud.openavc.com/agent/v1"

            with patch("httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=False)
                mock_client.post = AsyncMock(side_effect=_httpx.ConnectTimeout("timed out"))
                mock_client_cls.return_value = mock_client

                resp = await client.post("/api/ai/chat", content=b'{"message": "hi"}')

        assert resp.status_code == 504
        assert "timed out" in resp.json()["detail"].lower()

    async def test_non_streaming_cloud_connection_error_is_graceful(self, client):
        """A cloud connection failure yields a graceful 502, not a raw 500 (L-167)."""
        import httpx as _httpx

        with patch("openavc.api.ai_proxy.cfg") as mock_cfg:
            mock_cfg.CLOUD_ENABLED = True
            mock_cfg.CLOUD_SYSTEM_ID = "sys-123"
            mock_cfg.CLOUD_SYSTEM_KEY = "aa" * 32
            mock_cfg.CLOUD_ENDPOINT = "wss://cloud.openavc.com/agent/v1"

            with patch("httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=False)
                mock_client.post = AsyncMock(side_effect=_httpx.ConnectError("refused"))
                mock_client_cls.return_value = mock_client

                resp = await client.post("/api/ai/chat", content=b'{"message": "hi"}')

        assert resp.status_code == 502
        assert "cloud" in resp.json()["detail"].lower()


class TestConversationsEndpoints:
    async def test_list_conversations_cloud_not_ready(self, client):
        with patch("openavc.api.ai_proxy.cfg") as mock_cfg:
            mock_cfg.CLOUD_ENABLED = False
            resp = await client.get("/api/ai/conversations")
        assert resp.status_code == 503

    async def test_get_conversation_cloud_not_ready(self, client):
        with patch("openavc.api.ai_proxy.cfg") as mock_cfg:
            mock_cfg.CLOUD_ENABLED = False
            resp = await client.get("/api/ai/conversations/conv-123")
        assert resp.status_code == 503

    async def test_delete_conversation_cloud_not_ready(self, client):
        with patch("openavc.api.ai_proxy.cfg") as mock_cfg:
            mock_cfg.CLOUD_ENABLED = False
            resp = await client.delete("/api/ai/conversations/conv-123")
        assert resp.status_code == 503

    async def test_list_conversations_success(self, client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [{"id": "conv-1"}]

        with patch("openavc.api.ai_proxy.cfg") as mock_cfg:
            mock_cfg.CLOUD_ENABLED = True
            mock_cfg.CLOUD_SYSTEM_ID = "sys-123"
            mock_cfg.CLOUD_SYSTEM_KEY = "aa" * 32
            mock_cfg.CLOUD_ENDPOINT = "wss://cloud.openavc.com/agent/v1"

            with patch("httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=False)
                mock_client.get = AsyncMock(return_value=mock_response)
                mock_client_cls.return_value = mock_client

                resp = await client.get("/api/ai/conversations")

        assert resp.status_code == 200
        assert resp.json() == [{"id": "conv-1"}]


class TestUsageEndpoint:
    async def test_usage_cloud_not_ready(self, client):
        with patch("openavc.api.ai_proxy.cfg") as mock_cfg:
            mock_cfg.CLOUD_ENABLED = False
            resp = await client.get("/api/ai/usage")
        assert resp.status_code == 503

    async def test_usage_success(self, client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"tokens_used": 1000, "limit": 50000}

        with patch("openavc.api.ai_proxy.cfg") as mock_cfg:
            mock_cfg.CLOUD_ENABLED = True
            mock_cfg.CLOUD_SYSTEM_ID = "sys-123"
            mock_cfg.CLOUD_SYSTEM_KEY = "aa" * 32
            mock_cfg.CLOUD_ENDPOINT = "wss://cloud.openavc.com/agent/v1"

            with patch("httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=False)
                mock_client.get = AsyncMock(return_value=mock_response)
                mock_client_cls.return_value = mock_client

                resp = await client.get("/api/ai/usage")

        assert resp.status_code == 200
        data = resp.json()
        assert data["tokens_used"] == 1000
