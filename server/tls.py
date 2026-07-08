"""TLS certificate generation and loading for OpenAVC HTTPS support.

Closes openavc-backlog.md §11 ("HTTPS / TLS Support").

This module handles:
- Auto-generating a self-signed CA + leaf cert pair on first start.
- Loading user-provided certs and validating them up-front.
- Reading cert info for the /api/system/tls-status endpoint.
- The cloud-issued trusted certificate: atomic install, SNI-based dual
  serving next to the self-signed pair, and hot swap on renewal.

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
import ssl
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


# ---------------------------------------------------------------------------
# Cloud-issued trusted certificate (SNI dual-serve)
# ---------------------------------------------------------------------------
#
# The cloud enrollment flow installs a publicly-trusted wildcard cert for the
# instance's `*.<label>.<zone>` hostname. It is served *in addition to* the
# self-signed pair: the listener's default SSLContext stays self-signed, and a
# per-handshake SNI callback swaps in the cloud context only when the client
# asked for one of the cloud cert's names. No-SNI clients (all bare-IP HTTPS)
# never trigger the callback, so every existing path — pinned-CA panels,
# /api/certificate downloads, provided-cert fleets — is untouched.

_CLOUD_CERT_NAME = "cloud-cert.pem"
_CLOUD_KEY_NAME = "cloud-key.pem"

# Renew when this fraction of the cert lifetime has elapsed — the same
# proportion the cloud's renewal loop uses, so the agent's connect-time
# self-check and the cloud's cert_renew_due nudges agree on "due".
CLOUD_RENEWAL_FRACTION = 2 / 3


@dataclass(frozen=True)
class CloudCertState:
    """A loaded, validated cloud certificate ready to serve."""

    context: ssl.SSLContext
    exact_names: frozenset[str]  # non-wildcard DNS SANs, lowercased
    wildcard_bases: frozenset[str]  # "<label>.<zone>" from "*.<label>.<zone>" SANs
    hostname_suffix: str  # display/redirect base, e.g. "<label>.<zone>"
    expires_at: _dt.datetime

    def matches(self, server_name: str) -> bool:
        """True if the SNI name is covered by this cert's SANs.

        Wildcards match a single label only (`x.base`, never `a.b.base`),
        mirroring how browsers validate the cert — serving it for a deeper
        name would just move the failure from our redirect to the browser.
        """
        name = server_name.lower().rstrip(".")
        if name in self.exact_names:
            return True
        head, _, rest = name.partition(".")
        return bool(head) and head != "*" and rest in self.wildcard_bases


class CloudCertHolder:
    """Mutable slot the SNI callback reads on every handshake.

    Replacing the state here is the hot-swap mechanism: the next handshake
    serves the new certificate, no listener restart. The event loop owns all
    reads and writes, so no locking is needed.
    """

    __slots__ = ("_state",)

    def __init__(self) -> None:
        self._state: CloudCertState | None = None

    def get(self) -> CloudCertState | None:
        return self._state

    def set(self, state: CloudCertState) -> None:
        self._state = state

    def clear(self) -> None:
        self._state = None


_cloud_holder = CloudCertHolder()


def cloud_cert_holder() -> CloudCertHolder:
    """The process-wide holder the running TLS listener serves from."""
    return _cloud_holder


def cloud_cert_paths(data_dir: Path) -> tuple[Path, Path]:
    """(cert, key) paths for the installed cloud certificate."""
    tls_dir = data_dir / "tls"
    return tls_dir / _CLOUD_CERT_NAME, tls_dir / _CLOUD_KEY_NAME


def _build_cloud_state(cert_path: Path, key_path: Path) -> CloudCertState:
    """Parse, validate, and load a cloud cert + key into a CloudCertState.

    Raises :class:`TLSConfigError` on any problem: unreadable/unparseable
    files, expired cert, no DNS names, or a key that doesn't match the cert.
    """
    try:
        cert = _read_cert(cert_path)  # first PEM block = the leaf
    except (ValueError, OSError) as exc:
        raise TLSConfigError(f"Cloud certificate is unreadable: {exc}") from exc

    now = _dt.datetime.now(_dt.timezone.utc)
    if cert.not_valid_after_utc < now:
        raise TLSConfigError(
            f"Cloud certificate expired {cert.not_valid_after_utc.date().isoformat()}"
        )

    try:
        san_ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
        dns_sans = [n.value for n in san_ext.value if isinstance(n, x509.DNSName)]
    except x509.ExtensionNotFound:
        dns_sans = []
    exact_names: set[str] = set()
    wildcard_bases: set[str] = set()
    for san in dns_sans:
        name = san.lower()
        if name.startswith("*."):
            wildcard_bases.add(name[2:])
        else:
            exact_names.add(name)
    if not exact_names and not wildcard_bases:
        raise TLSConfigError("Cloud certificate has no DNS names")

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    try:
        # Loads the whole chain from the PEM (leaf + intermediates) and
        # verifies the key pairs with the leaf.
        context.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
    except (ssl.SSLError, OSError) as exc:
        raise TLSConfigError(f"Cloud certificate/key rejected: {exc}") from exc

    suffix = min(wildcard_bases) if wildcard_bases else min(exact_names)
    return CloudCertState(
        context=context,
        exact_names=frozenset(exact_names),
        wildcard_bases=frozenset(wildcard_bases),
        hostname_suffix=suffix,
        expires_at=cert.not_valid_after_utc,
    )


def read_cloud_cert_facts(data_dir: Path) -> dict[str, Any] | None:
    """Dates + names of the installed cloud cert, for status display.

    Lighter than a full load (no SSLContext, no key-pair check) and never
    raises — returns None when no readable cloud cert is installed.
    """
    cert_path, _key_path = cloud_cert_paths(data_dir)
    if not cert_path.exists():
        return None
    try:
        cert = _read_cert(cert_path)
    except (ValueError, OSError):
        return None
    try:
        san_ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
        dns_sans = [n.value.lower() for n in san_ext.value if isinstance(n, x509.DNSName)]
    except x509.ExtensionNotFound:
        dns_sans = []
    wildcard_bases = sorted(n[2:] for n in dns_sans if n.startswith("*."))
    exact_names = sorted(n for n in dns_sans if not n.startswith("*."))
    not_before = cert.not_valid_before_utc
    not_after = cert.not_valid_after_utc
    return {
        "hostname_suffix": (wildcard_bases or exact_names or [""])[0],
        "expires_at": not_after,
        "renews_at": not_before + (not_after - not_before) * CLOUD_RENEWAL_FRACTION,
        "expired": not_after <= _dt.datetime.now(_dt.timezone.utc),
    }


def load_cloud_cert(data_dir: Path) -> CloudCertState | None:
    """Load the installed cloud cert (if any) into the active holder.

    Never raises: a missing, invalid, or expired cloud cert must never take
    down the listener — it just means self-signed-only serving until the
    agent installs a good one.
    """
    cert_path, key_path = cloud_cert_paths(data_dir)
    if not cert_path.exists() or not key_path.exists():
        _cloud_holder.clear()
        return None
    try:
        state = _build_cloud_state(cert_path, key_path)
    except TLSConfigError as exc:
        log.warning("Cloud certificate not serving (self-signed only): %s", exc)
        _cloud_holder.clear()
        return None
    _cloud_holder.set(state)
    log.info(
        "Cloud certificate active for *.%s (expires %s)",
        state.hostname_suffix,
        state.expires_at.date().isoformat(),
    )
    return state


def _write_atomic(path: Path, data: bytes, *, private: bool = False) -> None:
    """Write bytes to ``path`` via a same-directory temp file + os.replace."""
    tmp = path.with_name(path.name + ".tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    # 0600 from birth on POSIX (Windows inherits the directory ACL).
    fd = os.open(tmp, flags, 0o600 if private else 0o644)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    os.replace(tmp, path)


def install_cloud_cert(
    data_dir: Path, cert_pem: bytes | str, key_pem: bytes | str
) -> CloudCertState:
    """Install a cloud cert + key and start serving them immediately.

    Validates first against temp copies, then atomically replaces the real
    files and hot-swaps the holder — the next handshake serves the new cert.
    Raises :class:`TLSConfigError` on invalid input, leaving any previously
    installed files (and the currently served cert) untouched.
    """
    if isinstance(cert_pem, str):
        cert_pem = cert_pem.encode("utf-8")
    if isinstance(key_pem, str):
        key_pem = key_pem.encode("utf-8")

    cert_path, key_path = cloud_cert_paths(data_dir)
    tls_dir = cert_path.parent
    try:
        tls_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise TLSConfigError(
            f"Cannot install cloud certificate: {tls_dir} is not writable",
            log_message=f"Cannot create {tls_dir}: {exc}",
        ) from exc

    check_cert = tls_dir / (_CLOUD_CERT_NAME + ".check")
    check_key = tls_dir / (_CLOUD_KEY_NAME + ".check")
    try:
        _write_atomic(check_cert, cert_pem)
        _write_atomic(check_key, key_pem, private=True)
        state = _build_cloud_state(check_cert, check_key)
        # Validated — move into place. If we die between the two replaces,
        # the mismatched pair on disk fails load_cloud_cert() gracefully
        # (self-signed only) and the next install repairs it.
        _write_atomic(cert_path, cert_pem)
        _write_atomic(key_path, key_pem, private=True)
    finally:
        check_cert.unlink(missing_ok=True)
        check_key.unlink(missing_ok=True)

    _cloud_holder.set(state)
    log.info(
        "Cloud certificate installed for *.%s (expires %s)",
        state.hostname_suffix,
        state.expires_at.date().isoformat(),
    )
    return state


def remove_cloud_cert(data_dir: Path) -> None:
    """Stop serving the cloud cert and delete its files (disable flow)."""
    _cloud_holder.clear()
    for path in cloud_cert_paths(data_dir):
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            log.warning("Could not remove %s: %s", path, exc)


def make_sni_callback(holder: CloudCertHolder):
    """Build the per-handshake SNI callback for the TLS listener.

    When the client's SNI matches the cloud cert's names, the handshake's
    context is swapped to the cloud one; everything else — no SNI (bare-IP
    HTTPS), other names, no/expired cloud cert — proceeds on the default
    self-signed context. The callback must never raise: an exception here
    aborts the handshake outright, so every failure path falls back to
    serving the default cert instead.
    """

    def _sni_callback(
        ssl_object: ssl.SSLObject, server_name: str | None, _context: ssl.SSLContext
    ) -> None:
        try:
            if not server_name:
                return None
            state = holder.get()
            if state is None:
                return None
            if state.expires_at < _dt.datetime.now(_dt.timezone.utc):
                # Serve self-signed rather than an expired cert; the agent's
                # renewal flow re-installs and re-populates the holder.
                holder.clear()
                log.warning(
                    "Cloud certificate expired — serving self-signed only "
                    "until a renewed certificate is installed"
                )
                return None
            if state.matches(server_name):
                ssl_object.context = state.context
        except Exception:
            log.exception("SNI callback failed; serving the default certificate")
        return None

    return _sni_callback
