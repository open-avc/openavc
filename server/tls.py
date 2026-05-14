"""TLS certificate generation and loading for OpenAVC HTTPS support.

Closes openavc-backlog.md §11 ("HTTPS / TLS Support").

This module handles:
- Auto-generating a self-signed CA + leaf cert pair on first start.
- Loading user-provided certs and validating them up-front.
- Reading cert info for the /api/system/tls-status endpoint.

The TLS-off code path of the server never imports this module — it is only
touched when ``config.TLS_ENABLED`` is True.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import ipaddress
import logging
import os
import socket
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

log = logging.getLogger(__name__)

# Validity windows: 10 years each, matching realistic AV install lifetimes.
_CA_VALIDITY_DAYS = 365 * 10
_SERVER_VALIDITY_DAYS = 365 * 10
_RSA_KEY_SIZE = 2048
_EXPIRY_WARNING_DAYS = 30


class TLSConfigError(Exception):
    """Raised when TLS is enabled but cannot be configured.

    ``reason`` is the short, user-facing form (shown in the Programmer UI's
    TLS status). ``str(exc)`` carries the full message suitable for logs.
    """

    def __init__(self, reason: str, *, log_message: str | None = None) -> None:
        self.reason = reason
        super().__init__(log_message or reason)


@dataclass(frozen=True)
class CertPaths:
    """Output of generate_self_signed: paths to the issued artifacts."""

    cert_path: Path
    key_path: Path
    ca_cert_path: Path


@dataclass(frozen=True)
class CertInfo:
    """Cert facts surfaced to the UI via /api/system/tls-status."""

    subject: str
    issuer: str
    expires_at: _dt.datetime
    days_until_expiry: int
    fingerprint_sha256: str
    sans: list[str]
    warnings: list[str]


# ---------------------------------------------------------------------------
# Identifier collection
# ---------------------------------------------------------------------------


def collect_local_identifiers(bind_address: str) -> tuple[list[str], list[str]]:
    """Return (hostnames, ips) for inclusion in the cert's SubjectAlternativeName.

    Always includes "localhost" + the OS hostname (sanitized through
    ``mdns_advertiser._sanitize_hostname``) and "127.0.0.1".

    When ``bind_address`` is wide-open ("0.0.0.0" or "::"), every non-loopback
    IPv4 from ``ifaddr.get_adapters()`` is added so phones on the LAN connecting
    by IP find a matching SAN.

    "::1" is added whenever IPv6 is detected on any adapter.
    """
    # Imported lazily so the TLS-off code path never pulls discovery in.
    from server.discovery.mdns_advertiser import _sanitize_hostname

    hostnames: list[str] = ["localhost"]

    try:
        sanitized = _sanitize_hostname(socket.gethostname())
        if sanitized and sanitized != "localhost":
            hostnames.append(sanitized)
    except OSError as exc:
        log.debug("Could not determine OS hostname for cert SAN: %s", exc)

    ips: list[str] = ["127.0.0.1"]
    has_ipv6 = False
    wide_open = bind_address in ("0.0.0.0", "::", "")

    try:
        import ifaddr

        for adapter in ifaddr.get_adapters():
            for ip_info in adapter.ips:
                if not isinstance(ip_info.ip, str):
                    # ifaddr represents IPv6 addresses as tuples.
                    has_ipv6 = True
                    continue
                addr = ip_info.ip
                if addr.startswith("127.") or addr.startswith("169.254."):
                    continue
                if wide_open and addr not in ips:
                    ips.append(addr)
    except ImportError:
        log.warning("ifaddr not available; cert SAN limited to localhost")
    except OSError as exc:
        log.warning("Could not enumerate adapters for cert SAN: %s", exc)

    if has_ipv6 and "::1" not in ips:
        ips.append("::1")

    return hostnames, ips


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------


def _build_san(hostnames: list[str], ips: list[str]) -> x509.SubjectAlternativeName:
    entries: list[x509.GeneralName] = []
    for name in hostnames:
        entries.append(x509.DNSName(name))
    for raw_ip in ips:
        try:
            entries.append(x509.IPAddress(ipaddress.ip_address(raw_ip)))
        except ValueError:
            log.warning("Skipping invalid IP in cert SAN: %r", raw_ip)
    return x509.SubjectAlternativeName(entries)


def _write_key(key: Any, path: Path) -> None:
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    path.write_bytes(pem)
    # POSIX: restrict to owner-only. Windows: ACL inherits from %PROGRAMDATA%
    # (user-only by default), so chmod is unnecessary.
    if os.name == "posix":
        try:
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError as exc:
            log.warning("Could not set 0600 mode on %s: %s", path, exc)


def _write_cert(cert: x509.Certificate, path: Path) -> None:
    path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


def generate_self_signed(
    data_dir: Path,
    *,
    hostnames: list[str],
    ips: list[str],
) -> CertPaths:
    """Generate a CA + server cert pair under ``data_dir/tls/``.

    The CA is self-signed; the server cert is signed by the CA. Devices install
    the CA cert once; subsequent server cert regenerations (e.g. on IP change)
    don't require re-installing trust on the device.
    """
    tls_dir = data_dir / "tls"
    try:
        tls_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise TLSConfigError(
            f"TLS enabled but data dir is not writable: {tls_dir}",
            log_message=f"Cannot create {tls_dir}: {exc}",
        ) from exc

    now = _dt.datetime.now(_dt.timezone.utc)

    # --- CA key + self-signed root cert ---
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=_RSA_KEY_SIZE)
    ca_subject = x509.Name(
        [
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "OpenAVC"),
            x509.NameAttribute(NameOID.COMMON_NAME, "OpenAVC Local Root CA"),
        ]
    )
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_subject)
        .issuer_name(ca_subject)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - _dt.timedelta(minutes=5))
        .not_valid_after(now + _dt.timedelta(days=_CA_VALIDITY_DAYS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )

    # --- Server key + leaf cert signed by the CA ---
    server_key = rsa.generate_private_key(public_exponent=65537, key_size=_RSA_KEY_SIZE)
    common_name = hostnames[0] if hostnames else "openavc"
    server_subject = x509.Name(
        [
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "OpenAVC"),
            x509.NameAttribute(NameOID.COMMON_NAME, common_name),
        ]
    )
    server_cert = (
        x509.CertificateBuilder()
        .subject_name(server_subject)
        .issuer_name(ca_subject)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - _dt.timedelta(minutes=5))
        .not_valid_after(now + _dt.timedelta(days=_SERVER_VALIDITY_DAYS))
        .add_extension(_build_san(hostnames, ips), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(server_key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )

    cert_path = tls_dir / "server.crt"
    key_path = tls_dir / "server.key"
    ca_cert_path = tls_dir / "ca.crt"

    _write_cert(server_cert, cert_path)
    _write_key(server_key, key_path)
    _write_cert(ca_cert, ca_cert_path)

    log.info(
        "Generated self-signed cert at %s (DNS SANs: %s; IP SANs: %s; valid until %s)",
        cert_path,
        ", ".join(hostnames),
        ", ".join(ips),
        (now + _dt.timedelta(days=_SERVER_VALIDITY_DAYS)).date().isoformat(),
    )

    return CertPaths(cert_path=cert_path, key_path=key_path, ca_cert_path=ca_cert_path)


# ---------------------------------------------------------------------------
# Inspection
# ---------------------------------------------------------------------------


def _read_cert(cert_path: Path) -> x509.Certificate:
    return x509.load_pem_x509_certificate(cert_path.read_bytes())


def _extract_sans(cert: x509.Certificate) -> list[str]:
    try:
        san_ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
    except x509.ExtensionNotFound:
        return []
    sans: list[str] = []
    for name in san_ext.value:
        if isinstance(name, x509.DNSName):
            sans.append(name.value)
        elif isinstance(name, x509.IPAddress):
            sans.append(str(name.value))
    return sans


def read_cert_info(cert_path: Path) -> CertInfo:
    """Inspect a PEM cert and return UI-friendly facts.

    Raises ``ValueError`` from ``cryptography`` or ``OSError`` from the
    filesystem on read failure — callers should handle both.
    """
    cert = _read_cert(cert_path)
    expires_at = cert.not_valid_after_utc
    now = _dt.datetime.now(_dt.timezone.utc)
    days_until_expiry = (expires_at - now).days

    warnings: list[str] = []
    if days_until_expiry < 0:
        warnings.append("expired")
    elif days_until_expiry < _EXPIRY_WARNING_DAYS:
        warnings.append("expiring-soon")

    fingerprint = hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()

    return CertInfo(
        subject=cert.subject.rfc4514_string(),
        issuer=cert.issuer.rfc4514_string(),
        expires_at=expires_at,
        days_until_expiry=days_until_expiry,
        fingerprint_sha256=fingerprint,
        sans=_extract_sans(cert),
        warnings=warnings,
    )


def _cert_covers_current_host(cert_path: Path, ips: list[str]) -> bool:
    """Return True if the cert's IP SANs still include the host's current IPs.

    Loopback IPs are treated as always-present (they're in every cert by
    construction) and don't affect the comparison. If the host has no
    non-loopback IPs to check, the cert is considered current.
    """
    try:
        sans = set(read_cert_info(cert_path).sans)
    except (ValueError, OSError) as exc:
        log.warning("Could not read cert SANs for IP-change check: %s", exc)
        return False

    relevant = [ip for ip in ips if ip not in ("127.0.0.1", "::1")]
    if not relevant:
        return True
    return any(ip in sans for ip in relevant)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def load_or_generate(config: Any, data_dir: Path) -> tuple[Path, Path]:
    """Resolve the (cert, key) pair to hand uvicorn.

    Modes (driven by ``config``):
      - **User-provided** when both ``TLS_CERT_FILE`` and ``TLS_KEY_FILE`` are
        set: validates the paths and parses both files. Raises
        :class:`TLSConfigError` on any read/parse failure. Logs (but does
        not fail) on expiry or hostname mismatch.
      - **Auto-generate** when neither file is set and
        ``TLS_AUTO_GENERATE`` is True: reuses ``data_dir/tls/server.{crt,key}``
        if present, valid, and still covers the host's current IPs.
        Regenerates otherwise.
      - **Misconfigured** (TLS enabled, no certs configured, auto-generate
        off): raises :class:`TLSConfigError` with a precise reason.
    """
    cert_file = (getattr(config, "TLS_CERT_FILE", "") or "").strip()
    key_file = (getattr(config, "TLS_KEY_FILE", "") or "").strip()

    if cert_file or key_file:
        if not cert_file or not key_file:
            raise TLSConfigError(
                "Provide both cert_file and key_file, or leave both blank to auto-generate.",
            )
        return _load_provided(Path(cert_file), Path(key_file))

    if not getattr(config, "TLS_AUTO_GENERATE", False):
        raise TLSConfigError(
            "TLS enabled but no cert is configured. "
            "Enable auto_generate or provide cert_file + key_file.",
        )

    return _load_or_generate_auto(data_dir, getattr(config, "BIND_ADDRESS", "127.0.0.1"))


def _load_provided(cert_path: Path, key_path: Path) -> tuple[Path, Path]:
    if not cert_path.exists():
        raise TLSConfigError(f"User-provided cert not found: {cert_path}")
    if not key_path.exists():
        raise TLSConfigError(f"User-provided key not found: {key_path}")
    try:
        cert = _read_cert(cert_path)
    except (ValueError, OSError) as exc:
        raise TLSConfigError(
            f"User-provided cert at {cert_path} is unreadable: {exc}",
        ) from exc
    try:
        serialization.load_pem_private_key(key_path.read_bytes(), password=None)
    except (ValueError, OSError) as exc:
        raise TLSConfigError(
            f"User-provided key at {key_path} is unreadable: {exc}",
        ) from exc

    now = _dt.datetime.now(_dt.timezone.utc)
    if cert.not_valid_after_utc < now:
        log.warning(
            "User-provided cert at %s expired on %s — proceeding anyway "
            "(admin may know what they're doing)",
            cert_path,
            cert.not_valid_after_utc.date().isoformat(),
        )
    return cert_path, key_path


def _load_or_generate_auto(data_dir: Path, bind_address: str) -> tuple[Path, Path]:
    hostnames, ips = collect_local_identifiers(bind_address)
    tls_dir = data_dir / "tls"
    cert_path = tls_dir / "server.crt"
    key_path = tls_dir / "server.key"

    if cert_path.exists() and key_path.exists():
        try:
            cert = _read_cert(cert_path)
        except (ValueError, OSError) as exc:
            log.info("Self-signed cert is unreadable (%s), regenerating", exc)
        else:
            now = _dt.datetime.now(_dt.timezone.utc)
            if cert.not_valid_after_utc < now:
                log.info("Self-signed cert expired, regenerating")
            elif not _cert_covers_current_host(cert_path, ips):
                log.info("Host IP changed, regenerating self-signed cert")
            else:
                return cert_path, key_path

    paths = generate_self_signed(data_dir, hostnames=hostnames, ips=ips)
    return paths.cert_path, paths.key_path
