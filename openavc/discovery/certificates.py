"""The one reader for a device's TLS certificate during discovery.

Discovery connects with a permissive context (``CERT_NONE``: devices ship
self-signed certificates and are not trusted yet), so ``getpeercert()`` gives
an empty dict; the DER form is still there, and ``cryptography`` parses it.
Many AV devices put their model in the certificate (a subject like
``CN=<model>-<mac>``), which makes it a strong pre-sign-in signal.

``certificate_details`` is what a report records. ``certificate_match_text``
is the string a driver's ``cert_subject`` rule is matched against: the
subject in RFC 4514 form, then ``SAN:`` and the DNS names. Its format is part
of the driver contract, so it does not change.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import ssl
from typing import Any

log = logging.getLogger("discovery.certificates")


def _make_discovery_tls_context() -> ssl.SSLContext:
    """A permissive TLS context for talking to a device that is not trusted yet.

    Discovery happens before a device is configured, and AV gear ships
    self-signed certificates out of the box, so neither the chain nor the host
    name can be verified; discovery only needs the encrypted channel to read
    what the device says and the certificate it presents. Verification is the
    driver's job once the device is added. Built once; it holds no per-host
    state.
    """
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


_DISCOVERY_TLS_CONTEXT = _make_discovery_tls_context()


def discovery_tls_context() -> ssl.SSLContext:
    """The one TLS context discovery connects with (see above)."""
    return _DISCOVERY_TLS_CONTEXT


def peer_certificate_der(writer: asyncio.StreamWriter) -> bytes | None:
    """The DER bytes of the certificate the peer presented, or None."""
    ssl_obj = writer.get_extra_info("ssl_object")
    if ssl_obj is None:
        return None
    try:
        der = ssl_obj.getpeercert(binary_form=True)
    except (ValueError, OSError):
        return None
    return der or None


def certificate_details(der: bytes) -> dict[str, Any] | None:
    """Subject, issuer, SANs, validity, serial and fingerprint of a cert.

    Returns None when the bytes do not parse. Names are RFC 4514 strings,
    times are ISO 8601 UTC, the serial is hex, and the fingerprint is the
    SHA-256 of the DER, hex.
    """
    try:
        from cryptography import x509
        from cryptography.x509.oid import ExtensionOID

        cert = x509.load_der_x509_certificate(der)
        san_dns: list[str] = []
        san_ip: list[str] = []
        try:
            san = cert.extensions.get_extension_for_oid(
                ExtensionOID.SUBJECT_ALTERNATIVE_NAME
            ).value
            san_dns = [str(n) for n in san.get_values_for_type(x509.DNSName)]
            san_ip = [str(n) for n in san.get_values_for_type(x509.IPAddress)]
        except x509.ExtensionNotFound:
            pass
        subject = cert.subject.rfc4514_string()
        issuer = cert.issuer.rfc4514_string()
        return {
            "subject": subject,
            "issuer": issuer,
            "self_signed": subject == issuer,
            "san_dns": san_dns,
            "san_ip": san_ip,
            "not_before": cert.not_valid_before_utc.isoformat(),
            "not_after": cert.not_valid_after_utc.isoformat(),
            "serial": format(cert.serial_number, "x"),
            "sha256": hashlib.sha256(der).hexdigest(),
        }
    except Exception as exc:  # malformed cert / parse failure — treat as no signal
        log.debug("certificate parse failed: %s", exc)
        return None


def certificate_match_text(details: dict[str, Any] | None) -> str:
    """The string a driver's ``cert_subject`` rule matches, or "" if none."""
    if not details:
        return ""
    parts = [details["subject"]]
    if details.get("san_dns"):
        parts.append("SAN:" + ",".join(details["san_dns"]))
    return " ".join(parts)


def read_peer_certificate(writer: asyncio.StreamWriter) -> dict[str, Any] | None:
    """``certificate_details`` of the certificate the peer presented."""
    der = peer_certificate_der(writer)
    return certificate_details(der) if der else None
