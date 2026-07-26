"""
Ephemeral self-signed TLS for simulator servers.

Some devices only ever speak TLS: a Crestron NVX's REST API, a Dante Director,
a Hisense TV's MQTT broker. Their drivers are written to match — an `https://`
base URL, `use_tls=True` — and each ships with a self-signed certificate, so
the drivers verify nothing. Teaching such a driver a plain-HTTP mode just to
reach its simulator would exercise a path it never takes against real hardware,
which is the wrong direction: the harness should adapt to the device, not the
other way round.

So the *simulator* terminates TLS instead. It mints a throwaway self-signed
certificate at startup and serves the same scheme the real device serves, and
the driver connects exactly as it would in the field.

Opt in per simulator with `"tls": True` in SIMULATOR_INFO (or in the
simulator's config). Shared by every simulator base that runs a socket server
(`HTTPSimulator`, `MQTTSimulator`).
"""

from __future__ import annotations

import logging
import os
import ssl
import tempfile

logger = logging.getLogger(__name__)

# The cert + key temp-file paths a started server must delete when it stops.
CertFiles = tuple[str, str]


def wants_tls(simulator_info: dict | None, config: dict | None) -> bool:
    """True if this simulator asked to serve TLS (SIMULATOR_INFO or config)."""
    return bool((simulator_info or {}).get("tls") or (config or {}).get("tls"))


def build_optional_tls(
    simulator_info: dict | None, config: dict | None, label: str
) -> tuple[ssl.SSLContext | None, CertFiles | None]:
    """Resolve the TLS opt-in into a server context.

    Returns `(None, None)` when TLS wasn't requested — or when the context
    couldn't be built, in which case the caller serves plain and the reason is
    logged rather than failing the simulator outright. On success returns the
    context plus the temp files the caller passes to `remove_cert_files()` at
    shutdown.

    The context accepts but does not require a client certificate, so a driver
    that presents one connects cleanly.
    """
    if not wants_tls(simulator_info, config):
        return None, None
    try:
        cert_path, key_path = _generate_self_signed()
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(certfile=cert_path, keyfile=key_path)
        ctx.verify_mode = ssl.CERT_OPTIONAL
        return ctx, (cert_path, key_path)
    except Exception:
        logger.exception("%s: failed to build TLS context, serving plain", label)
        return None, None


def remove_cert_files(files: CertFiles | None) -> None:
    """Delete the ephemeral cert + key. Safe to call with None or twice."""
    for path in files or ():
        try:
            os.unlink(path)
        except OSError:
            pass


def _generate_self_signed() -> CertFiles:
    """Write an ephemeral self-signed cert + key to temp files."""
    from datetime import datetime, timedelta, timezone

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "openavc-sim")])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=365))
        .sign(key, hashes.SHA256())
    )
    cert_fd, cert_path = tempfile.mkstemp(suffix=".crt")
    key_fd, key_path = tempfile.mkstemp(suffix=".key")
    with os.fdopen(cert_fd, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    with os.fdopen(key_fd, "wb") as f:
        f.write(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.TraditionalOpenSSL,
                serialization.NoEncryption(),
            )
        )
    return cert_path, key_path
