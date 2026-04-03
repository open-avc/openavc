"""Tests for the AI proxy — routes AI requests to cloud via HMAC auth."""

import json
from unittest.mock import patch, AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from server.api.ai_proxy import (
    router,
    set_engine,
    _get_cloud_api_url,
    _get_system_key_bytes,
    _sign_request,
    _check_cloud_ready,
    _error_json,
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
        with patch("server.api.ai_proxy.cfg") as mock_cfg:
            mock_cfg.CLOUD_ENDPOINT = "wss://cloud.openavc.com/agent/v1"
            assert _get_cloud_api_url() == "https://cloud.openavc.com"

    def test_ws_to_http(self):
        with patch("server.api.ai_proxy.cfg") as mock_cfg:
            mock_cfg.CLOUD_ENDPOINT = "ws://localhost:8000/agent/v1"
            assert _get_cloud_api_url() == "http://localhost:8000"

    def test_empty_endpoint(self):
        with patch("server.api.ai_proxy.cfg") as mock_cfg:
            mock_cfg.CLOUD_ENDPOINT = ""
            assert _get_cloud_api_url() == ""

    def test_no_agent_path(self):
        with patch("server.api.ai_proxy.cfg") as mock_cfg:
            mock_cfg.CLOUD_ENDPOINT = "wss://cloud.openavc.com"
            assert _get_cloud_api_url() == "https://cloud.openavc.com"


# --- _get_system_key_bytes tests ---


class TestGetSystemKeyBytes:
    def test_empty_key(self):
        with patch("server.api.ai_proxy.cfg") as mock_cfg:
            mock_cfg.CLOUD_SYSTEM_KEY = ""
            assert _get_system_key_bytes() == b""

    def test_hex_key(self):
        with patch("server.api.ai_proxy.cfg") as mock_cfg:
            mock_cfg.CLOUD_SYSTEM_KEY = "abcdef0123456789"
            result = _get_system_key_bytes()
            assert result == bytes.fromhex("abcdef0123456789")

    def test_bytes_key(self):
        with patch("server.api.ai_proxy.cfg") as mock_cfg:
            mock_cfg.CLOUD_SYSTEM_KEY = b"\x01\x02\x03"
            assert _get_system_key_bytes() == b"\x01\x02\x03"

    def test_non_hex_string_key(self):
        with patch("server.api.ai_proxy.cfg") as mock_cfg:
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
        with patch("server.api.ai_proxy.cfg") as mock_cfg:
            mock_cfg.CLOUD_ENABLED = False
            from fastapi import HTTPException
            with pytest.raises(HTTPException) as exc_info:
                _check_cloud_ready()
            assert exc_info.value.status_code == 503
            assert "not enabled" in exc_info.value.detail

    def test_missing_system_id_raises_503(self):
        with patch("server.api.ai_proxy.cfg") as mock_cfg:
            mock_cfg.CLOUD_ENABLED = True
            mock_cfg.CLOUD_SYSTEM_ID = ""
            mock_cfg.CLOUD_SYSTEM_KEY = "abcd"
            mock_cfg.CLOUD_ENDPOINT = "wss://cloud.openavc.com/agent/v1"
            from fastapi import HTTPException
            with pytest.raises(HTTPException) as exc_info:
                _check_cloud_ready()
            assert exc_info.value.status_code == 503

    def test_success_returns_tuple(self):
        with patch("server.api.ai_proxy.cfg") as mock_cfg:
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


# --- API endpoint tests ---


class TestAiStatusEndpoint:
    async def test_cloud_not_enabled(self, client):
        with patch("server.api.ai_proxy.cfg") as mock_cfg:
            mock_cfg.CLOUD_ENABLED = False
            mock_cfg.CLOUD_SYSTEM_ID = ""
            resp = await client.get("/api/ai/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["available"] is False

    async def test_cloud_enabled_but_no_agent(self, client):
        with patch("server.api.ai_proxy.cfg") as mock_cfg:
            mock_cfg.CLOUD_ENABLED = True
            mock_cfg.CLOUD_SYSTEM_ID = "sys-123"
            set_engine(None)
            resp = await client.get("/api/ai/status")
        data = resp.json()
        assert data["available"] is False

    async def test_cloud_agent_connected(self, client):
        engine = MagicMock()
        engine.cloud_agent.get_status.return_value = {"connected": True}
        set_engine(engine)
        with patch("server.api.ai_proxy.cfg") as mock_cfg:
            mock_cfg.CLOUD_ENABLED = True
            mock_cfg.CLOUD_SYSTEM_ID = "sys-123"
            resp = await client.get("/api/ai/status")
        data = resp.json()
        assert data["available"] is True

    async def test_cloud_agent_disconnected(self, client):
        engine = MagicMock()
        engine.cloud_agent.get_status.return_value = {"connected": False}
        set_engine(engine)
        with patch("server.api.ai_proxy.cfg") as mock_cfg:
            mock_cfg.CLOUD_ENABLED = True
            mock_cfg.CLOUD_SYSTEM_ID = "sys-123"
            resp = await client.get("/api/ai/status")
        data = resp.json()
        assert data["available"] is False


class TestAiChatEndpoint:
    async def test_cloud_not_ready_returns_503(self, client):
        with patch("server.api.ai_proxy.cfg") as mock_cfg:
            mock_cfg.CLOUD_ENABLED = False
            resp = await client.post("/api/ai/chat", content=b'{"message": "hi"}')
        assert resp.status_code == 503

    async def test_non_streaming_success(self, client):
        """Non-streaming chat proxies to cloud and returns JSON."""
        import httpx
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"response": "hello"}

        with patch("server.api.ai_proxy.cfg") as mock_cfg:
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
        """Non-streaming chat returns cloud error status."""
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.text = "Rate limited"

        with patch("server.api.ai_proxy.cfg") as mock_cfg:
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


class TestConversationsEndpoints:
    async def test_list_conversations_cloud_not_ready(self, client):
        with patch("server.api.ai_proxy.cfg") as mock_cfg:
            mock_cfg.CLOUD_ENABLED = False
            resp = await client.get("/api/ai/conversations")
        assert resp.status_code == 503

    async def test_get_conversation_cloud_not_ready(self, client):
        with patch("server.api.ai_proxy.cfg") as mock_cfg:
            mock_cfg.CLOUD_ENABLED = False
            resp = await client.get("/api/ai/conversations/conv-123")
        assert resp.status_code == 503

    async def test_delete_conversation_cloud_not_ready(self, client):
        with patch("server.api.ai_proxy.cfg") as mock_cfg:
            mock_cfg.CLOUD_ENABLED = False
            resp = await client.delete("/api/ai/conversations/conv-123")
        assert resp.status_code == 503

    async def test_list_conversations_success(self, client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [{"id": "conv-1"}]

        with patch("server.api.ai_proxy.cfg") as mock_cfg:
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
        with patch("server.api.ai_proxy.cfg") as mock_cfg:
            mock_cfg.CLOUD_ENABLED = False
            resp = await client.get("/api/ai/usage")
        assert resp.status_code == 503

    async def test_usage_success(self, client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"tokens_used": 1000, "limit": 50000}

        with patch("server.api.ai_proxy.cfg") as mock_cfg:
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
