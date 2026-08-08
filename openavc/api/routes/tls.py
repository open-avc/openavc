"""HTTPS / certificate REST endpoints.

Everything the Programmer IDE's Security card drives: what certificate is
being served right now, enrolling for (and dropping) a cloud-issued trusted
certificate, uploading a third-party cert + key, and handing out the
auto-generated CA so panel devices can trust it.

Certificate reading, generation, and the SNI-served cloud-cert holder live in
``openavc/tls.py``; the cloud side of issuance is
``openavc/cloud/cert_manager.py``. This module is the HTTP surface over both.

Every route reads *live* system config rather than the import-time constants,
so the Security card's re-fetch straight after a PATCH reflects the just-saved
state instead of the stale pre-restart one.
"""

from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile

from openavc.api.errors import api_error as _api_error

router = APIRouter()
open_router = APIRouter()


@open_router.get("/certificate")
async def download_ca_certificate():
    """Serve the auto-generated CA cert so panel devices can trust it.

    No auth — this is a public certificate (it's already presented during
    every TLS handshake) and panels need to fetch it before they can speak
    HTTPS without warnings.

    404 when:
      - TLS is off (no CA exists).
      - TLS is on with a user-provided cert (caller brings their own CA).
      - TLS is on with auto_generate but the CA file is missing.
    """
    from fastapi.responses import Response
    from openavc.system_config import get_system_config

    # Read live config (not the import-time constants) so a just-saved TLS
    # change is reflected without waiting for a restart.
    sys_cfg = get_system_config()
    if not sys_cfg.get("tls", "enabled") or not sys_cfg.get("tls", "auto_generate"):
        raise HTTPException(status_code=404, detail="No CA certificate available")

    ca_path = sys_cfg.data_dir / "tls" / "ca.crt"
    if not ca_path.exists():
        raise HTTPException(status_code=404, detail="CA certificate not found")

    return Response(
        content=ca_path.read_bytes(),
        media_type="application/x-pem-file",
        headers={"Content-Disposition": 'attachment; filename="openavc-ca.crt"'},
    )


def _cloud_cert_block(sys_cfg) -> dict[str, Any]:
    """The trusted-certificate section of tls-status.

    Merges three sources: the installed cert files (dates, names), the live
    serving holder (is the SNI path actually answering), and the agent's
    certificate manager (in-flight phase, typed errors, retry state).
    """
    from datetime import datetime, timezone

    from openavc import tls as tls_module
    from openavc.api._engine import get_engine_optional

    engine = get_engine_optional()
    agent = getattr(engine, "cloud_agent", None) if engine else None
    manager = agent.cert_manager if agent else None

    block: dict[str, Any] = {
        "enabled": bool(sys_cfg.get("tls", "cloud_cert", False)),
        "paired": agent is not None,
        "available": bool(
            agent and agent.connected and agent.has_capability("trusted_certs")
        ),
        "active": False,
        "hostname_suffix": "",
        "expires_at": None,
        "renews_at": None,
        "phase": "idle",
        "last_error": "",
        "last_error_detail": "",
        "last_attempt_at": "",
        "retry_pending": False,
    }

    holder_state = tls_module.cloud_cert_holder().get()
    if holder_state is not None and holder_state.expires_at > datetime.now(timezone.utc):
        block["active"] = True
        block["hostname_suffix"] = holder_state.hostname_suffix

    facts = tls_module.read_cloud_cert_facts(sys_cfg.data_dir)
    if facts:
        block["hostname_suffix"] = block["hostname_suffix"] or facts["hostname_suffix"]
        block["expires_at"] = facts["expires_at"].isoformat()
        block["renews_at"] = facts["renews_at"].isoformat()

    if manager:
        mgr = manager.get_status()
        block["phase"] = mgr["phase"]
        block["last_error"] = mgr["last_error"]
        block["last_error_detail"] = mgr["last_error_detail"]
        block["last_attempt_at"] = mgr["last_attempt_at"]
        block["retry_pending"] = mgr["retry_pending"]
        block["hostname_suffix"] = block["hostname_suffix"] or mgr["hostname_suffix"]

    return block


@router.get("/system/tls-status")
async def get_tls_status() -> dict[str, Any]:
    """Surface current TLS state for the Programmer IDE's Security card.

    Shape:
      - TLS off: {"enabled": false, cloud_cert: {...}}
      - TLS on:  {enabled, port, redirect_http, mode, cert: {...},
                  cloud_cert: {...}, [error]}

    Cert dict contains: subject, issuer, expires_at (ISO 8601),
    days_until_expiry, fingerprint (sha256 hex), sans (list), warnings (list).

    Warnings may include: "expired", "expiring-soon", "hostname-mismatch".
    On cert-read failure, "error" is set at the top level and "cert" is null.

    cloud_cert covers the cloud-issued trusted certificate: enabled (the
    user opted in), paired/available (a cloud pairing exists / the session
    offers the feature), active (currently served via SNI), hostname_suffix,
    expires_at/renews_at, plus the issuance phase and last typed error. It
    is present even with TLS off — the Settings card's "get a trusted
    certificate" callout (which starts by turning HTTPS on) needs the
    pairing/enrollment state before HTTPS exists.

    Reads live config (not the import-time constants) so the Security card's
    immediate re-fetch after a PATCH reflects the just-saved cert/mode rather
    than the stale pre-restart state.
    """
    from openavc.system_config import get_system_config

    sys_cfg = get_system_config()
    if not sys_cfg.get("tls", "enabled"):
        return {"enabled": False, "cloud_cert": _cloud_cert_block(sys_cfg)}

    cert_file = str(sys_cfg.get("tls", "cert_file") or "")
    key_file = str(sys_cfg.get("tls", "key_file") or "")
    mode = "provided" if (cert_file and key_file) else "auto"

    if mode == "provided":
        from pathlib import Path
        cert_path = Path(cert_file)
    else:
        cert_path = sys_cfg.data_dir / "tls" / "server.crt"

    status: dict[str, Any] = {
        "enabled": True,
        "port": sys_cfg.get("tls", "port"),
        "redirect_http": sys_cfg.get("tls", "redirect_http"),
        "mode": mode,
        "cert": None,
        "cloud_cert": _cloud_cert_block(sys_cfg),
    }

    if not cert_path.exists():
        status["error"] = f"Certificate file not found: {cert_path}"
        return status

    try:
        from openavc import tls as tls_module
        info = tls_module.read_cert_info(cert_path)
    except (ValueError, OSError) as exc:
        status["error"] = f"Could not read certificate: {exc}"
        return status

    warnings = list(info.warnings)
    # Hostname-mismatch: compare cert SANs against the host's current identifiers.
    try:
        bind_address = sys_cfg.get("network", "bind_address", "0.0.0.0")
        hostnames, ips = tls_module.collect_local_identifiers(bind_address)
        all_current = set(hostnames) | set(ips)
        all_current.discard("127.0.0.1")
        all_current.discard("localhost")
        all_current.discard("::1")
        if all_current and not all_current.intersection(info.sans):
            warnings.append("hostname-mismatch")
    except Exception:  # noqa: BLE001 — diagnostic-only, never block the endpoint
        pass

    status["cert"] = {
        "subject": info.subject,
        "issuer": info.issuer,
        "expires_at": info.expires_at.isoformat(),
        "days_until_expiry": info.days_until_expiry,
        "fingerprint": info.fingerprint_sha256,
        "sans": info.sans,
        "warnings": warnings,
    }
    return status


@router.post("/system/tls/cloud-cert/enable")
async def enable_cloud_cert() -> dict[str, Any]:
    """Enroll for a cloud-issued trusted certificate (or retry after a failure).

    Requires HTTPS on and an active cloud pairing. Issuance runs
    asynchronously over the cloud connection — poll /api/system/tls-status
    for the result. Calling while already enabled is the manual early-retry
    path, bypassing the daily failure backoff.
    """
    from openavc.api._engine import get_engine_optional
    from openavc.system_config import get_system_config

    sys_cfg = get_system_config()
    if not sys_cfg.get("tls", "enabled"):
        raise HTTPException(
            status_code=400,
            detail="Enable HTTPS first — the trusted certificate is served "
                   "over the HTTPS listener.",
        )

    engine = get_engine_optional()
    agent = getattr(engine, "cloud_agent", None) if engine else None
    manager = agent.cert_manager if agent else None
    if manager is None:
        raise HTTPException(
            status_code=400,
            detail="Pair this system with OpenAVC Cloud first — the "
                   "certificate is issued through the cloud connection.",
        )

    started, reason = await manager.enable()
    result: dict[str, Any] = {"status": "enabled", "enabled": True, "started": started}
    if not started:
        result["reason"] = reason
        result["message"] = {
            "busy": "A certificate request is already in progress.",
            "not_connected": "Not connected to the cloud right now — "
                             "enrollment will run automatically once the "
                             "connection is back.",
            "not_available": "The cloud connection does not offer trusted "
                             "certificates.",
        }.get(reason, reason)
    return result


@router.post("/system/tls/cloud-cert/disable")
async def disable_cloud_cert() -> dict[str, Any]:
    """Turn the trusted certificate off: stop serving it, delete the files,
    and notify the cloud best-effort. Never blocked by cloud reachability
    (works even after unpairing)."""
    from openavc.api._engine import get_engine_optional
    from openavc.system_config import get_system_config

    engine = get_engine_optional()
    agent = getattr(engine, "cloud_agent", None) if engine else None
    manager = agent.cert_manager if agent else None
    if manager is not None:
        await manager.disable()
    else:
        # Not paired (anymore) — still clean up locally.
        from openavc import tls as tls_module

        sys_cfg = get_system_config()
        sys_cfg.set("tls", "cloud_cert", False)
        sys_cfg.save()
        tls_module.remove_cloud_cert(sys_cfg.data_dir)
    return {"enabled": False}


_UPLOAD_MAX_BYTES = 100_000  # 100 KB — real cert+key combos are 5-20 KB even with chains


@router.post("/system/tls/upload-cert")
async def upload_tls_cert(
    cert: UploadFile = File(...),
    key: UploadFile = File(...),
) -> dict[str, Any]:
    """Accept a user-provided cert + matching private key, write to data_dir/tls/.

    Lets a non-technical admin install a third-party certificate without
    touching the server's filesystem. The frontend follows this with a
    PATCH /api/system/config that points ``tls.cert_file`` / ``tls.key_file``
    at the returned paths and sets ``auto_generate=false``.

    Validation (rejects with 400 on any failure):
      * Both files non-empty.
      * Total payload <= 100 KB.
      * Cert parses as PEM-encoded X.509.
      * Key parses as a passphrase-free PEM private key.
      * Cert and key public-key DER bytes match.

    Files are written atomically (``.tmp`` + ``os.replace``) so a torn write
    can never leave half-rotated artifacts on disk. POSIX hosts get
    ``0600`` on the key file.
    """
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization

    cert_bytes = await cert.read()
    key_bytes = await key.read()

    if not cert_bytes:
        raise HTTPException(status_code=400, detail="Certificate file is empty.")
    if not key_bytes:
        raise HTTPException(status_code=400, detail="Key file is empty.")
    if len(cert_bytes) + len(key_bytes) > _UPLOAD_MAX_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"Files too large (max {_UPLOAD_MAX_BYTES // 1000} KB combined).",
        )

    try:
        parsed_cert = x509.load_pem_x509_certificate(cert_bytes)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Certificate is not valid PEM-encoded X.509: {exc}",
        ) from exc

    try:
        parsed_key = serialization.load_pem_private_key(key_bytes, password=None)
    except TypeError as exc:
        # cryptography raises TypeError when a password is required but None was supplied.
        raise HTTPException(
            status_code=400,
            detail=(
                "Private key is passphrase-protected. Re-export it without a "
                "passphrase, or put a reverse proxy in front of OpenAVC."
            ),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Key is not a valid PEM-encoded private key: {exc}",
        ) from exc

    # Match check via SubjectPublicKeyInfo DER bytes — works uniformly for RSA, EC, Ed25519, etc.
    try:
        cert_pub_der = parsed_cert.public_key().public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        key_pub_der = parsed_key.public_key().public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Could not compare cert and key public keys: {exc}",
        ) from exc

    if cert_pub_der != key_pub_der:
        raise HTTPException(status_code=400, detail="Certificate and key do not match.")

    # Atomic write to data_dir/tls/
    import os
    from openavc.system_config import get_system_config

    data_dir = get_system_config().data_dir
    tls_dir = data_dir / "tls"
    try:
        tls_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise _api_error(500, "The TLS data directory is not writable.", exc) from None

    cert_path = tls_dir / "user-cert.pem"
    key_path = tls_dir / "user-key.pem"
    cert_tmp = cert_path.with_name(cert_path.name + ".tmp")
    key_tmp = key_path.with_name(key_path.name + ".tmp")

    try:
        cert_tmp.write_bytes(cert_bytes)
        key_tmp.write_bytes(key_bytes)
        os.replace(cert_tmp, cert_path)
        os.replace(key_tmp, key_path)
    except OSError as exc:
        # Best-effort cleanup of any partial temps.
        cert_tmp.unlink(missing_ok=True)
        key_tmp.unlink(missing_ok=True)
        raise _api_error(500, "Could not write the certificate files.", exc) from None

    if os.name == "posix":
        try:
            os.chmod(key_path, 0o600)
        except OSError:
            pass  # non-fatal — fall through; tls.read_cert_info still works

    # Surface metadata so the UI can render the success card without a refetch.
    from openavc import tls as tls_module

    info = tls_module.read_cert_info(cert_path)

    # Heuristic warning when the user uploaded a CA cert instead of a server cert.
    warnings = list(info.warnings)
    try:
        bc = parsed_cert.extensions.get_extension_for_class(x509.BasicConstraints).value
        if bc.ca:
            warnings.append("is-ca-cert")
    except x509.ExtensionNotFound:
        pass

    return {
        "cert_path": str(cert_path),
        "key_path": str(key_path),
        "fingerprint": info.fingerprint_sha256,
        "subject": info.subject,
        "issuer": info.issuer,
        "expires_at": info.expires_at.isoformat(),
        "days_until_expiry": info.days_until_expiry,
        "sans": info.sans,
        "warnings": warnings,
    }
