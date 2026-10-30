"""The one door a report leaves this system through.

``upload_report`` sends one saved report zip (a problem report or a device
audit report) to ``config.REPORT_URL`` when a person presses Send to OpenAVC.
Nothing here runs on its own and nothing retries later: one press, one
attempt, one answer.

The request is ``multipart/form-data`` with four fields: ``file`` (the zip),
``report_id`` (the UUID in its ``report.json``), ``kind`` (``problem_report``
or ``device_audit``) and ``openavc_version``. The body is built here, byte for
byte, because a paired system signs it: the signature covers the exact bytes
sent (``X-System-ID``, ``X-Timestamp``, ``X-Signature``, the headers the AI
proxy sends), so the cloud can tell staff which system sent the report. A
signed attempt the cloud refuses with 401 is sent once more, unsigned: a
system whose clock is off by more than a minute fails every signature check,
and its report should still arrive (unlinked).

The answer is final whenever the cloud gave one (stored, too large, rate
limited, refused, storage full). Only a network failure (no connection, DNS,
TLS, a proxy, a timeout) or a server error says "try another way": the
Programmer then sends the same file from the browser, whose certificate store
and proxy settings may get through where this server's do not.

A file over ``MAX_REPORT_BYTES`` is refused before any connection opens.
"""

from __future__ import annotations

import io
import json
import secrets
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from openavc import config as cfg
from openavc.utils.logger import get_logger

log = get_logger(__name__)

# The cloud's own cap on the file; the form around it may add 64 KiB.
MAX_REPORT_BYTES = 20 * 1024 * 1024
CONNECT_TIMEOUT = 10.0
TOTAL_TIMEOUT = 120.0
KINDS = ("problem_report", "device_audit")
# report.json is read from the zip only this far; the cloud's limit is 5 MiB.
_REPORT_JSON_MAX = 5 * 1024 * 1024

# Why a send did not arrive. Every kind but NETWORK is the cloud's (or this
# server's) final answer; NETWORK means the browser may try the same file.
NETWORK = "network"
TOO_LARGE = "too_large"
RATE_LIMITED = "rate_limited"
REFUSED = "refused"
STORAGE_FULL = "storage_full"
NOT_CONFIGURED = "not_configured"

_FINAL_STATUSES = {413: TOO_LARGE, 429: RATE_LIMITED, 507: STORAGE_FULL}


@dataclass(frozen=True)
class UploadResult:
    """What one press of Send came to."""

    sent: bool
    reference: str | None = None
    failure: str | None = None
    status: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "sent": self.sent,
            "reference": self.reference,
            "failure": self.failure,
            "status": self.status,
        }


class ReportUnreadable(ValueError):
    """The saved file is not a report this door can send."""


def report_url() -> str:
    return cfg.REPORT_URL


def report_host() -> str:
    """The host reports go to, as the dialog names it ("" when sending is off)."""
    return urlsplit(cfg.REPORT_URL).hostname or ""


def report_identity(path: Path) -> tuple[str, str]:
    """``(kind, report_id)`` read from the zip's ``report.json``."""
    try:
        with zipfile.ZipFile(path) as zf:
            with zf.open("report.json") as fh:
                raw = fh.read(_REPORT_JSON_MAX + 1)
    except (OSError, KeyError, zipfile.BadZipFile) as exc:
        raise ReportUnreadable(f"{path.name} has no readable report.json") from exc
    if len(raw) > _REPORT_JSON_MAX:
        raise ReportUnreadable(f"{path.name}'s report.json is too large")
    try:
        report = json.loads(raw)
    except ValueError as exc:
        raise ReportUnreadable(f"{path.name}'s report.json is not JSON") from exc
    kind = report.get("report_kind", "device_audit") if isinstance(report, dict) else None
    report_id = report.get("report_id") if isinstance(report, dict) else None
    if kind not in KINDS:
        raise ReportUnreadable(f"{path.name} is not a report kind OpenAVC receives")
    try:
        uuid.UUID(str(report_id))
    except ValueError:
        raise ReportUnreadable(f"{path.name} has no report_id") from None
    return kind, str(report_id)


def multipart_body(
    data: bytes, filename: str, fields: dict[str, str],
) -> tuple[bytes, str]:
    """``(body, content_type)``: the text fields, then the file, as the bytes
    that go on the wire (and that a signature covers)."""
    boundary = f"openavc-report-{secrets.token_hex(16)}"
    out = io.BytesIO()
    for name, value in fields.items():
        out.write(f"--{boundary}\r\n".encode("ascii"))
        out.write(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("ascii"))
        out.write(value.encode("utf-8"))
        out.write(b"\r\n")
    safe_name = filename.replace('"', "").replace("\r", "").replace("\n", "")
    out.write(f"--{boundary}\r\n".encode("ascii"))
    out.write(
        f'Content-Disposition: form-data; name="file"; filename="{safe_name}"\r\n'
        "Content-Type: application/zip\r\n\r\n".encode("utf-8")
    )
    out.write(data)
    out.write(f"\r\n--{boundary}--\r\n".encode("ascii"))
    return out.getvalue(), f"multipart/form-data; boundary={boundary}"


def _signing_headers(body: bytes) -> dict[str, str]:
    """The paired system's signature over ``body``, or nothing when this
    system is not paired."""
    if not cfg.CLOUD_ENABLED or not cfg.CLOUD_SYSTEM_ID:
        return {}
    from openavc.api.ai_proxy import _get_system_key_bytes, _sign_request

    key = _get_system_key_bytes()
    if not key:
        return {}
    return _sign_request(cfg.CLOUD_SYSTEM_ID, key, body)


def _reference(resp: httpx.Response) -> str | None:
    try:
        ref = resp.json().get("reference")
    except (ValueError, AttributeError):
        return None
    return ref if isinstance(ref, str) and ref else None


def _detail(resp: httpx.Response) -> str:
    try:
        detail = resp.json().get("detail")
    except (ValueError, AttributeError):
        detail = None
    return detail if isinstance(detail, str) else resp.text[:200]


def classify(resp: httpx.Response) -> UploadResult:
    """The cloud's answer as a result. A server error other than 507 is
    treated as a network failure: the browser may get through."""
    status = resp.status_code
    if status == 200:
        reference = _reference(resp)
        if reference:
            return UploadResult(True, reference=reference, status=status)
        return UploadResult(False, failure=NETWORK, status=status)
    if status in _FINAL_STATUSES:
        return UploadResult(False, failure=_FINAL_STATUSES[status], status=status)
    if status in (404, 405):
        return UploadResult(False, failure=NOT_CONFIGURED, status=status)
    if status >= 500:
        return UploadResult(False, failure=NETWORK, status=status)
    return UploadResult(False, failure=REFUSED, status=status)


async def upload_report(
    path: Path,
    kind: str,
    report_id: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> UploadResult:
    """Send the report at ``path`` once (see the module docstring)."""
    from openavc.version import __version__

    url = report_url()
    if not url:
        return UploadResult(False, failure=NOT_CONFIGURED)
    size = path.stat().st_size
    if size > MAX_REPORT_BYTES:
        log.info("Report %s not sent: %d bytes is over the limit", path.name, size)
        return UploadResult(False, failure=TOO_LARGE)
    body, content_type = multipart_body(
        path.read_bytes(),
        path.name,
        {"report_id": report_id, "kind": kind, "openavc_version": __version__},
    )
    signed = _signing_headers(body)
    timeout = httpx.Timeout(TOTAL_TIMEOUT, connect=CONNECT_TIMEOUT)
    try:
        async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
            resp = await client.post(
                url, content=body, headers={"Content-Type": content_type, **signed},
            )
            if resp.status_code == 401 and signed:
                log.warning(
                    "The report service refused this system's signature (is this "
                    "computer's clock right?); sending report %s unsigned", report_id,
                )
                resp = await client.post(url, content=body, headers={"Content-Type": content_type})
    except httpx.InvalidURL:
        log.warning("OPENAVC_REPORT_URL is not a URL: %r", url)
        return UploadResult(False, failure=NOT_CONFIGURED)
    except httpx.HTTPError as exc:
        # Connect, DNS, TLS (CERTIFICATE_VERIFY_FAILED), proxy and timeout
        # failures all land here: the browser may get through.
        log.info("Report %s could not reach %s: %s", report_id, url, exc)
        return UploadResult(False, failure=NETWORK)
    result = classify(resp)
    if result.sent:
        log.info("Report %s sent as %s (%s)", report_id, result.reference, kind)
    else:
        log.info(
            "Report %s not stored: %s %s (%s)",
            report_id, resp.status_code, _detail(resp), result.failure,
        )
    return result
