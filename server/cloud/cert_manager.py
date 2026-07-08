"""
OpenAVC Cloud — Trusted-certificate manager.

Drives the cloud-issued certificate flow over the agent's WebSocket:

    cert_request {}          -> cert_result {status:"enrollment", label, zone}
    cert_request {csr_pem}   -> cert_result {status:"issued", certificate_chain, ...}
                              | cert_result {status:"error", error, detail}

The private key is generated here and never leaves the instance — only the
CSR (SANs exactly ``{*.<label>.<zone>, <label>.<zone>}``) goes up. On an
issued result the chain + key are installed via ``server.tls`` and the
running TLS listener hot-swaps to the new certificate on its next handshake.

Renewals arrive two ways: the cloud sends ``cert_renew_due`` to connected
agents whose certs entered the renewal window, and the manager self-checks
on every connect (covers instances that were offline through the window).
Each renewal uses a fresh key.

Failed issuance retries once per day — never a tight loop (Let's Encrypt
integration guidance) — with a user-initiated request as the early-retry
override. The cloud's own nudges bypass the local backoff; its renewal loop
is failure-aware and already paces them.
"""

from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime, timezone
from typing import Any

from server.cloud.protocol import CERT_REQUEST, CERT_STATUS, extract_payload
from server.utils.logger import get_logger

log = get_logger(__name__)

# The cloud aborts an ACME order at 180s; if no cert_result lands well past
# that, the request is lost (dropped connection mid-flow) and must be retried.
RESULT_TIMEOUT = 300

# Client-side backoff after a failed issuance: retry daily. The Settings
# card's manual action clears this for an early retry.
RETRY_INTERVAL = 24 * 3600

# "busy" is not an issuance failure — a prior order (e.g. from before a
# reconnect) is still running cloud-side and will finish on its own. Retry
# soon rather than burning a day.
BUSY_RETRY_INTERVAL = 300

# The enrollment label/zone cross the cloud trust boundary and become both
# CSR names and served hostnames — accept only plain lowercase DNS labels.
_DNS_LABEL_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")


def _valid_dns_name(name: str, max_labels: int = 10) -> bool:
    if not isinstance(name, str) or not 0 < len(name) <= 253:
        return False
    parts = name.split(".")
    if len(parts) > max_labels:
        return False
    return all(_DNS_LABEL_RE.match(part) for part in parts)


def generate_key_and_csr(label: str, zone: str) -> tuple[bytes, str]:
    """Fresh EC P-256 key + CSR with SANs exactly {*.label.zone, label.zone}.

    Returns ``(key_pem_bytes, csr_pem_str)``. The subject is empty — the
    names live in the SAN extension, which is all the issuance path reads.
    """
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP256R1())
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([]))
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName(f"*.{label}.{zone}"),
                    x509.DNSName(f"{label}.{zone}"),
                ]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    key_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    csr_pem = csr.public_bytes(serialization.Encoding.PEM).decode("ascii")
    return key_pem, csr_pem


class CertificateManager:
    """Agent subsystem for the trusted-certificate flow.

    Wired like the other agent subsystems (constructed by the engine, attached
    via ``agent.set_cert_manager``). The agent calls :meth:`start` once a
    session is established and :meth:`stop` on disconnect/shutdown; downstream
    ``cert_result`` / ``cert_renew_due`` messages are dispatched to the
    ``handle_*`` methods. ``enable`` / ``disable`` / ``request_certificate``
    are the surface the REST layer drives.

    Single-flight by design: ``_phase`` walks idle -> enrolling -> issuing ->
    idle, and the pending private key exists only in memory between the CSR
    going up and the chain coming down. An unsolicited "issued" result can
    never install anything — there is no key to pair it with.
    """

    def __init__(self, agent: Any, system_config: Any) -> None:
        self._agent = agent
        self._syscfg = system_config

        self._phase = "idle"  # idle | enrolling | issuing
        self._pending_key_pem: bytes | None = None
        self._hostname_suffix = ""  # "<label>.<zone>" once known

        self._watchdog_task: asyncio.Task | None = None
        self._retry_task: asyncio.Task | None = None
        self._self_check_task: asyncio.Task | None = None

        self._next_retry_at = 0.0  # time.monotonic() gate for self-initiated attempts
        self._last_error = ""
        self._last_error_detail = ""
        self._last_attempt_at = ""

    # --- Lifecycle (driven by the agent) ---

    async def start(self) -> None:
        """Session established — run the connect-time self-check."""
        self._self_check_task = asyncio.create_task(self._connect_self_check())

    async def stop(self) -> None:
        """Disconnect/shutdown — drop any in-flight request.

        The pending key (if any) is discarded; a half-finished flow simply
        restarts from enrollment on the next connect. Idempotent — the agent
        calls this from both its per-connection cleanup and full stop.
        """
        for attr in ("_self_check_task", "_watchdog_task", "_retry_task"):
            task: asyncio.Task | None = getattr(self, attr)
            setattr(self, attr, None)
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._phase = "idle"
        self._pending_key_pem = None

    # --- Public surface (REST endpoints / Settings) ---

    def enabled(self) -> bool:
        return bool(self._syscfg.get("tls", "cloud_cert", False))

    async def enable(self) -> tuple[bool, str]:
        """Turn the feature on and request a certificate (user-initiated)."""
        self._syscfg.set("tls", "cloud_cert", True)
        self._syscfg.save()
        return await self.request_certificate(manual=True)

    async def disable(self) -> None:
        """Turn the feature off: stop serving, delete files, tell the cloud.

        The cloud notification is best-effort — disable never blocks on cloud
        reachability. Certs are short-lived; the label simply stops renewing.
        """
        from server import tls

        await self.stop()
        self._syscfg.set("tls", "cloud_cert", False)
        self._syscfg.save()
        tls.remove_cloud_cert(self._syscfg.data_dir)
        self._hostname_suffix = ""
        self._last_error = ""
        self._last_error_detail = ""
        self._next_retry_at = 0.0
        self._set_state("disabled", "")
        log.info("Trusted certificate disabled — serving self-signed only")
        await self._send_status("disabled")

    async def request_certificate(self, *, manual: bool = False) -> tuple[bool, str]:
        """Start an issuance attempt. Returns (started, reason_if_not).

        ``manual=True`` (a user clicking the Settings action) clears the
        daily failure backoff; automatic callers respect it.
        """
        if self._phase != "idle":
            return False, "busy"
        if not getattr(self._agent, "connected", False):
            return False, "not_connected"
        if not self._agent.has_capability("trusted_certs"):
            self._fail(
                "not_available",
                "The cloud session does not offer trusted certificates",
                retry=False,
            )
            return False, "not_available"
        if manual:
            self._next_retry_at = 0.0
            self._cancel_retry()
        elif time.monotonic() < self._next_retry_at:
            return False, "backoff"
        await self._begin_request()
        return True, ""

    def get_status(self) -> dict[str, Any]:
        """Manager-side facts for the tls-status endpoint."""
        return {
            "enabled": self.enabled(),
            "phase": self._phase,
            "hostname_suffix": self._hostname_suffix,
            "last_error": self._last_error,
            "last_error_detail": self._last_error_detail,
            "last_attempt_at": self._last_attempt_at,
            "retry_pending": bool(self._retry_task and not self._retry_task.done()),
        }

    # --- Downstream message handlers (dispatched by the agent) ---

    async def handle_cert_result(self, msg: dict[str, Any]) -> None:
        payload = extract_payload(msg)
        status = payload.get("status", "")

        if status == "enrollment":
            await self._on_enrollment(payload)
        elif status == "issued":
            await self._on_issued(payload)
        elif status == "error":
            if self._phase == "idle":
                # e.g. the tail of a request from before a reconnect
                log.info(
                    "Trusted certificate: ignoring stray error result — %s",
                    payload.get("error", "unknown"),
                )
                return
            self._clear_pending()
            code = str(payload.get("error", "unknown"))
            detail = str(payload.get("detail", ""))
            retry_delay = BUSY_RETRY_INTERVAL if code == "busy" else RETRY_INTERVAL
            self._fail(code, detail, retry_delay=retry_delay)
        else:
            log.warning("Trusted certificate: unknown cert_result status %r", status)

    async def handle_renew_due(self, msg: dict[str, Any]) -> None:
        payload = extract_payload(msg)
        if not self.enabled():
            # Disabled locally (possibly while offline) — remind the cloud so
            # it can stop nudging and tombstone the label.
            log.info("Trusted certificate: cert_renew_due ignored — feature disabled")
            await self._send_status("disabled")
            return
        if self._phase != "idle":
            return
        log.info(
            "Trusted certificate: cloud reports renewal due (expires %s) — renewing",
            payload.get("expires_at", "unknown"),
        )
        # The cloud paces these nudges (its renewal loop is failure-aware),
        # so they intentionally bypass the local daily backoff.
        await self._begin_request()

    # --- Flow internals ---

    async def _begin_request(self) -> None:
        self._phase = "enrolling"
        self._pending_key_pem = None
        self._last_attempt_at = datetime.now(timezone.utc).isoformat()
        self._set_state("requesting", "")
        self._arm_watchdog()
        await self._agent.send_message(CERT_REQUEST, {})

    async def _on_enrollment(self, payload: dict[str, Any]) -> None:
        if self._phase != "enrolling":
            log.info("Trusted certificate: ignoring unexpected enrollment result")
            return
        label = payload.get("label", "")
        zone = payload.get("zone", "")
        if not (_valid_dns_name(label, max_labels=1) and _valid_dns_name(zone)):
            self._clear_pending()
            self._fail(
                "invalid_enrollment",
                f"Cloud sent an invalid label/zone ({label!r}, {zone!r})",
            )
            return
        key_pem, csr_pem = generate_key_and_csr(label, zone)
        self._pending_key_pem = key_pem
        self._hostname_suffix = f"{label}.{zone}"
        self._phase = "issuing"
        self._arm_watchdog()  # fresh window for the ACME round-trip
        log.info("Trusted certificate: requesting issuance for *.%s", self._hostname_suffix)
        await self._agent.send_message(CERT_REQUEST, {"csr_pem": csr_pem})

    async def _on_issued(self, payload: dict[str, Any]) -> None:
        if self._phase != "issuing" or not self._pending_key_pem:
            # Without the matching in-memory key a pushed chain is unusable,
            # so unsolicited "issued" results are inert by construction.
            log.info("Trusted certificate: ignoring cert_result with no request pending")
            return
        key_pem = self._pending_key_pem
        chain_pem = payload.get("certificate_chain", "")
        self._clear_pending()

        from server import tls

        try:
            state = tls.install_cloud_cert(self._syscfg.data_dir, chain_pem, key_pem)
        except tls.TLSConfigError as exc:
            self._fail("install_failed", str(exc))
            return

        self._hostname_suffix = state.hostname_suffix
        self._last_error = ""
        self._last_error_detail = ""
        self._next_retry_at = 0.0
        self._cancel_retry()
        self._set_state("installed", "")
        if self._agent.state:
            self._agent.state.set(
                "system.cloud.cert_hostname", state.hostname_suffix, source="cloud"
            )
        log.info(
            "Trusted certificate installed for *.%s (expires %s)",
            state.hostname_suffix,
            state.expires_at.date().isoformat(),
        )
        await self._send_status("installed")

    async def _connect_self_check(self) -> None:
        """On connect: request a cert if enrolled and missing/expired/due.

        Covers instances that slept through their renewal window (the cloud
        only nudges connected agents) and first issuances that failed before
        a restart.
        """
        try:
            if not self.enabled():
                self._set_state("disabled", "")
                return
            if not self._agent.has_capability("trusted_certs"):
                self._fail(
                    "not_available",
                    "The cloud session does not offer trusted certificates",
                    retry=False,
                )
                return
            due, reason = self._renewal_due()
            if not due:
                self._set_state("installed", "")
                return
            if time.monotonic() < self._next_retry_at:
                log.info(
                    "Trusted certificate: %s, but in failure backoff — not retrying yet",
                    reason,
                )
                return
            if self._phase != "idle":
                return
            log.info("Trusted certificate: %s — requesting", reason)
            await self._begin_request()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Trusted certificate: connect-time self-check failed")

    def _renewal_due(self) -> tuple[bool, str]:
        """Check the installed cert against the 2/3-lifetime renewal point."""
        from cryptography import x509

        from server import tls

        cert_path, key_path = tls.cloud_cert_paths(self._syscfg.data_dir)
        if not cert_path.exists() or not key_path.exists():
            return True, "no certificate installed"
        try:
            cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        except (ValueError, OSError) as exc:
            return True, f"installed certificate unreadable ({exc})"
        not_before = cert.not_valid_before_utc
        not_after = cert.not_valid_after_utc
        now = datetime.now(timezone.utc)
        if not_after <= now:
            return True, "installed certificate expired"
        renew_at = not_before + (not_after - not_before) * tls.CLOUD_RENEWAL_FRACTION
        if now >= renew_at:
            return True, "renewal window open"
        return False, ""

    # --- Failure handling / backoff ---

    def _fail(
        self,
        code: str,
        detail: str,
        *,
        retry: bool = True,
        retry_delay: float = RETRY_INTERVAL,
    ) -> None:
        self._last_error = code
        self._last_error_detail = detail
        self._set_state("error", code)
        log.warning("Trusted certificate: issuance failed — %s: %s", code, detail)
        if retry and self.enabled():
            self._schedule_retry(retry_delay)

    def _schedule_retry(self, delay: float) -> None:
        self._next_retry_at = time.monotonic() + delay
        self._cancel_retry()
        self._retry_task = asyncio.create_task(self._retry_after(delay))
        log.info("Trusted certificate: next automatic attempt in %.0f s", delay)

    def _cancel_retry(self) -> None:
        task = self._retry_task
        self._retry_task = None
        if task and not task.done():
            task.cancel()

    async def _retry_after(self, delay: float) -> None:
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        self._retry_task = None
        self._next_retry_at = 0.0
        try:
            await self.request_certificate()
        except Exception:
            log.exception("Trusted certificate: scheduled retry failed")

    # --- Watchdog ---

    def _arm_watchdog(self) -> None:
        task = self._watchdog_task
        if task and not task.done():
            task.cancel()
        self._watchdog_task = asyncio.create_task(self._watchdog())

    async def _watchdog(self) -> None:
        try:
            await asyncio.sleep(RESULT_TIMEOUT)
        except asyncio.CancelledError:
            return
        self._watchdog_task = None
        if self._phase == "idle":
            return
        phase = self._phase
        self._phase = "idle"
        self._pending_key_pem = None
        self._fail(
            "timeout",
            f"No cert_result received within {RESULT_TIMEOUT}s (phase: {phase})",
        )

    def _clear_pending(self) -> None:
        self._phase = "idle"
        self._pending_key_pem = None
        task = self._watchdog_task
        self._watchdog_task = None
        if task and not task.done():
            task.cancel()

    # --- Reporting ---

    def _set_state(self, status: str, error_code: str) -> None:
        state = getattr(self._agent, "state", None)
        if state:
            state.set("system.cloud.cert_status", status, source="cloud")
            state.set("system.cloud.cert_error", error_code, source="cloud")
            if status == "disabled":
                state.set("system.cloud.cert_hostname", "", source="cloud")

    async def _send_status(self, state_str: str) -> None:
        try:
            await self._agent.send_message(CERT_STATUS, {"state": state_str})
        except Exception:
            log.debug("Trusted certificate: cert_status send failed", exc_info=True)
