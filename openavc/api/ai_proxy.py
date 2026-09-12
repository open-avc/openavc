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

import openavc.config as cfg
from openavc.api.auth import require_programmer_auth

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
    from openavc.cloud.crypto import derive_auth_key, compute_hmac

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

# What the IDE is told when the cloud refuses but sends no sentence of its own.
_GENERIC_REFUSAL = "The AI assistant is not available on this account."


async def _cloud_ai_refusal() -> str | None:
    """Why the cloud would refuse a chat turn from this system, or None.

    A connected agent proves the cloud is reachable, and nothing more: the
    assistant can be switched off at our end, paused on the account, or past a
    free account's allowance, and every one of those answers a chat turn with a
    refusal. This asks the question the connection cannot.

    It fails OPEN, on purpose and in every direction -- an unreachable status
    door, a non-200, an unparseable body, an older cloud that has no such route
    at all -- because the only thing a failed probe establishes is that we do
    not know. Answering "unavailable" from ignorance would take the assistant
    away from someone whose account is fine. Only an explicit ``available:
    false`` from the cloud closes the door.
    """
    try:
        api_url, system_id, system_key = _check_cloud_ready()
        headers = _sign_request(system_id, system_key, b"")
        async with httpx.AsyncClient(timeout=httpx.Timeout(8.0, connect=4.0)) as client:
            resp = await client.get(
                f"{api_url}/api/v1/ai/system/status", headers=headers
            )
        if resp.status_code != 200:
            log.debug("Cloud AI status answered %d", resp.status_code)
            return None
        data = resp.json()
    except Exception as e:
        log.debug("Cloud AI status probe failed: %s", e)
        return None

    if not isinstance(data, dict) or data.get("available") is not False:
        return None
    reason = data.get("reason")
    if isinstance(reason, str) and reason.strip():
        return reason.strip()
    return _GENERIC_REFUSAL


@router.get("/status")
async def ai_status() -> dict[str, Any]:
    """Whether the assistant can actually answer, not merely whether cloud is up.

    ``state`` is what the IDE branches its copy on, because "pair this system"
    is the wrong thing to tell somebody who paired it an hour ago and whose
    account simply does not have the assistant. ``reason`` is the sentence to
    show; it comes from the cloud whenever the cloud had one.
    """
    if not cfg.CLOUD_ENABLED or not cfg.CLOUD_SYSTEM_ID:
        return {"available": False, "state": "unpaired", "reason": "Cloud not paired"}

    if not (_engine and _engine.cloud_agent):
        return {
            "available": False,
            "state": "disconnected",
            "reason": "Cloud agent not running",
        }

    status = _engine.cloud_agent.get_status()
    if not status.get("connected"):
        return {
            "available": False,
            "state": "disconnected",
            "reason": "Cloud not connected",
        }

    refusal = await _cloud_ai_refusal()
    if refusal:
        return {"available": False, "state": "unavailable", "reason": refusal}

    return {"available": True, "state": "available"}


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


# What the browser is told for a refusal the cloud sent no sentence with —
# an intermediary's HTML 503, a body that isn't JSON, an empty detail.
_STATUS_FALLBACKS = {
    429: "AI request limit reached. Please try again later or upgrade your plan.",
    402: "AI features require an active subscription.",
    503: "AI service is not available.",
}


def _cloud_detail(body: bytes) -> str | None:
    """The cloud's own sentence out of an error body, or None if it sent none.

    Only a JSON object's string ``detail`` counts. A body that is not JSON is
    somebody else's page — an intermediary's 503, a proxy timeout — and must
    not reach the browser as if the cloud had written it.
    """
    import json
    try:
        parsed = json.loads(body)
    except Exception:
        return None
    if not isinstance(parsed, dict):
        return None
    detail = parsed.get("detail")
    if isinstance(detail, str) and detail.strip():
        return detail.strip()
    return None


def _error_message(status_code: int, body: bytes) -> str:
    """Map a cloud error response to a friendly, sanitized message.

    Never returns the raw cloud body verbatim — it extracts the JSON ``detail``
    or a truncated snippet. Shared by the streaming (_error_json) and
    non-streaming paths so both surface the same message and neither leaks a
    full internal cloud body to the browser.

    The cloud distinguishes its own refusals and they are not the same answer:
    an account past its allowance is told what lifts it, an account whose
    assistant is paused is told to get in touch, and a service that is briefly
    down is told to come back. Collapsing all three into one fixed sentence per
    status left "never going to work" and "try again in a minute" reading
    identically, so the cloud's sentence is relayed whenever it sent one and the
    fixed sentences are what answers when it did not.
    """
    detail = _cloud_detail(body)

    if status_code in _STATUS_FALLBACKS:
        return detail or _STATUS_FALLBACKS[status_code]

    return detail or str(body[:200], "utf-8", errors="replace")


def _error_json(status_code: int, body: bytes) -> str:
    """Build a JSON error string for SSE error events."""
    import json
    return json.dumps({"message": _error_message(status_code, body)})
