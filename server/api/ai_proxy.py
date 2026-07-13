"""AI proxy — routes AI requests through the local server to the cloud.

Instead of the browser calling cloud.openavc.com directly (which causes
CORS issues and requires a separate login), the browser calls these
local endpoints and the server proxies to the cloud using the system
key established during pairing. No separate cloud login needed.
"""

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

import server.config as cfg
from server.api.auth import require_programmer_auth

log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/ai",
    tags=["ai"],
    dependencies=[Depends(require_programmer_auth)],
)

_engine = None


def set_engine(engine) -> None:
    """Set the engine reference (called by main.py at startup)."""
    global _engine
    _engine = engine


def _get_cloud_api_url() -> str:
    """Derive the cloud HTTP API URL from the WebSocket endpoint.

    wss://cloud.openavc.com/agent/v1 -> https://cloud.openavc.com
    ws://localhost:8000/agent/v1     -> http://localhost:8000
    """
    endpoint = cfg.CLOUD_ENDPOINT
    if not endpoint:
        return ""
    url = endpoint.replace("wss://", "https://").replace("ws://", "http://")
    # Strip the /agent/... path
    idx = url.find("/agent")
    if idx > 0:
        url = url[:idx]
    return url


def _get_system_key_bytes() -> bytes:
    """Load system key bytes from config."""
    key = cfg.CLOUD_SYSTEM_KEY
    if not key:
        return b""
    if isinstance(key, bytes):
        return key
    try:
        return bytes.fromhex(key)
    except ValueError:
        return key.encode("utf-8")


def _sign_request(system_id: str, system_key: bytes, body: bytes) -> dict[str, str]:
    """Create HMAC auth headers for a cloud system-authenticated request."""
    from server.cloud.crypto import derive_auth_key, compute_hmac

    timestamp = datetime.now(timezone.utc).isoformat()
    auth_key = derive_auth_key(system_key, system_id)
    body_hash = hashlib.sha256(body).hexdigest()
    message = (system_id + timestamp + body_hash).encode("utf-8")
    signature = compute_hmac(auth_key, message)

    return {
        "X-System-ID": system_id,
        "X-Timestamp": timestamp,
        "X-Signature": signature,
    }


def _check_cloud_ready() -> tuple[str, str, bytes]:
    """Check cloud is configured and return (api_url, system_id, system_key).
    Raises HTTPException if not ready."""
    if not cfg.CLOUD_ENABLED:
        raise HTTPException(status_code=503, detail="Cloud not enabled. Pair this system first.")

    system_id = cfg.CLOUD_SYSTEM_ID
    system_key = _get_system_key_bytes()
    api_url = _get_cloud_api_url()

    if not system_id or not system_key or not api_url:
        raise HTTPException(status_code=503, detail="Cloud not configured. Pair this system first.")

    return api_url, system_id, system_key


async def _cloud_request(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    content: bytes | None = None,
    timeout: Any = 30.0,
    ok_statuses: tuple[int, ...] = (200,),
) -> httpx.Response:
    """Proxy a non-streaming request to the cloud, translating transport
    failures and non-OK responses into graceful HTTPExceptions.

    Mirrors the streaming path's error handling so a cloud outage yields a clear
    message instead of a raw 500, and a non-OK cloud response yields a sanitized
    detail (via _error_message) instead of the raw cloud body relayed verbatim.
    """
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            if method == "POST":
                resp = await client.post(url, content=content, headers=headers)
            elif method == "GET":
                resp = await client.get(url, headers=headers)
            elif method == "DELETE":
                resp = await client.delete(url, headers=headers)
            else:  # pragma: no cover - internal misuse
                raise HTTPException(status_code=500, detail="Unsupported cloud request method")
    except HTTPException:
        raise
    except httpx.TimeoutException:
        log.warning("Cloud AI request timed out: %s %s", method, url)
        raise HTTPException(status_code=504, detail="Request timed out. Please try again.")
    except Exception as e:
        log.warning("Cloud AI request failed: %s %s — %s", method, url, e)
        raise HTTPException(status_code=502, detail="Connection to cloud lost. Please try again.")

    if resp.status_code not in ok_statuses:
        raise HTTPException(
            status_code=resp.status_code,
            detail=_error_message(resp.status_code, resp.content),
        )
    return resp


# --- Status ---


@router.get("/status")
async def ai_status() -> dict[str, Any]:
    """Check if AI is available (cloud paired and connected)."""
    if not cfg.CLOUD_ENABLED or not cfg.CLOUD_SYSTEM_ID:
        return {"available": False, "reason": "Cloud not paired"}

    # Check if cloud agent is connected
    if _engine and _engine.cloud_agent:
        status = _engine.cloud_agent.get_status()
        if status.get("connected"):
            return {"available": True}
        return {"available": False, "reason": "Cloud not connected"}

    return {"available": False, "reason": "Cloud agent not running"}


# --- Chat ---


@router.post("/chat")
async def ai_chat(request: Request, stream: bool = Query(False)):
    """Proxy AI chat to cloud. Supports SSE streaming with stream=true."""
    api_url, system_id, system_key = _check_cloud_ready()

    body = await request.body()
    auth_headers = _sign_request(system_id, system_key, body)

    cloud_url = f"{api_url}/api/v1/ai/system/chat"
    if stream:
        cloud_url += "?stream=true"

    if stream:
        async def relay_stream():
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0)) as client:
                    async with client.stream(
                        "POST",
                        cloud_url,
                        content=body,
                        headers={
                            "Content-Type": "application/json",
                            "Accept": "text/event-stream",
                            **auth_headers,
                        },
                    ) as resp:
                        if resp.status_code != 200:
                            error_body = await resp.aread()
                            log.warning("Cloud AI chat error: %d %s", resp.status_code, error_body[:200])
                            yield f"event: error\ndata: {_error_json(resp.status_code, error_body)}\n\n"
                            return
                        async for chunk in resp.aiter_bytes():
                            yield chunk
            except httpx.TimeoutException:
                log.warning("Cloud AI chat stream timed out")
                yield 'event: error\ndata: {"message": "Request timed out. Please try again."}\n\n'
            except Exception as e:
                log.warning("Cloud AI chat stream error: %s", e)
                yield 'event: error\ndata: {"message": "Connection to cloud lost. Please try again."}\n\n'

        return StreamingResponse(
            relay_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    # Non-streaming
    resp = await _cloud_request(
        "POST",
        cloud_url,
        content=body,
        headers={"Content-Type": "application/json", **auth_headers},
        timeout=httpx.Timeout(300.0, connect=10.0),
    )
    return resp.json()


# --- Conversations ---


@router.get("/conversations")
async def ai_list_conversations():
    """List AI conversations for this system."""
    api_url, system_id, system_key = _check_cloud_ready()

    body = b""
    auth_headers = _sign_request(system_id, system_key, body)

    resp = await _cloud_request(
        "GET", f"{api_url}/api/v1/ai/system/conversations", headers=auth_headers,
    )
    return resp.json()


@router.get("/conversations/{conversation_id}")
async def ai_get_conversation(conversation_id: str):
    """Get a specific conversation with messages."""
    api_url, system_id, system_key = _check_cloud_ready()

    body = b""
    auth_headers = _sign_request(system_id, system_key, body)

    resp = await _cloud_request(
        "GET",
        f"{api_url}/api/v1/ai/system/conversations/{conversation_id}",
        headers=auth_headers,
    )
    return resp.json()


@router.delete("/conversations/{conversation_id}", status_code=204)
async def ai_delete_conversation(conversation_id: str):
    """Delete a conversation."""
    api_url, system_id, system_key = _check_cloud_ready()

    body = b""
    auth_headers = _sign_request(system_id, system_key, body)

    await _cloud_request(
        "DELETE",
        f"{api_url}/api/v1/ai/system/conversations/{conversation_id}",
        headers=auth_headers,
        ok_statuses=(200, 204),
    )


# --- Usage ---


@router.get("/usage")
async def ai_get_usage():
    """Get AI usage for this system's account."""
    api_url, system_id, system_key = _check_cloud_ready()

    body = b""
    auth_headers = _sign_request(system_id, system_key, body)

    resp = await _cloud_request(
        "GET", f"{api_url}/api/v1/ai/system/usage", headers=auth_headers,
    )
    return resp.json()


def _error_message(status_code: int, body: bytes) -> str:
    """Map a cloud error response to a friendly, sanitized message.

    Never returns the raw cloud body verbatim — it extracts the JSON ``detail``
    or a truncated snippet, and maps the common billing/limit statuses. Shared by
    the streaming (_error_json) and non-streaming paths so both surface the same
    message and neither leaks a full internal cloud body to the browser.
    """
    import json
    try:
        detail = json.loads(body)
        msg = detail.get("detail", str(body[:200], "utf-8", errors="replace"))
    except Exception:
        msg = str(body[:200], "utf-8", errors="replace")

    if status_code == 429:
        msg = "AI request limit reached. Please try again later or upgrade your plan."
    elif status_code == 402:
        msg = "AI features require an active subscription."
    elif status_code == 503:
        msg = "AI service is not available."

    return msg


def _error_json(status_code: int, body: bytes) -> str:
    """Build a JSON error string for SSE error events."""
    import json
    return json.dumps({"message": _error_message(status_code, body)})
