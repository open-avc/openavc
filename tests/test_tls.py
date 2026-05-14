"""Tests for server.tls — cert generation, loading, inspection."""

from __future__ import annotations

import datetime as _dt
import logging
import os
import socket
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from server import tls


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(**overrides):
    """Build a config stub for load_or_generate."""
    base = {
        "TLS_AUTO_GENERATE": True,
        "TLS_CERT_FILE": "",
        "TLS_KEY_FILE": "",
        "BIND_ADDRESS": "127.0.0.1",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _write_expired_cert(cert_path: Path, key_path: Path) -> None:
    """Write a quick expired self-signed cert + key for provided-cert tests."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = _dt.datetime.now(_dt.timezone.utc)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "expired-test")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(now - _dt.timedelta(days=365))
        .not_valid_after(now - _dt.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("localhost")]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )


# ---------------------------------------------------------------------------
# generate_self_signed
# ---------------------------------------------------------------------------


def test_generate_self_signed_creates_valid_cert(tmp_path):
    paths = tls.generate_self_signed(
        tmp_path,
        hostnames=["localhost", "openavc"],
        ips=["127.0.0.1", "192.168.1.50"],
    )

    assert paths.cert_path.exists()
    assert paths.key_path.exists()
    assert paths.ca_cert_path.exists()
    assert paths.cert_path == tmp_path / "tls" / "server.crt"

    cert = x509.load_pem_x509_certificate(paths.cert_path.read_bytes())
    san_ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
    sans = san_ext.value
    dns_names = {n.value for n in sans if isinstance(n, x509.DNSName)}
    ip_addrs = {str(n.value) for n in sans if isinstance(n, x509.IPAddress)}

    assert "localhost" in dns_names
    assert "openavc" in dns_names
    assert "127.0.0.1" in ip_addrs
    assert "192.168.1.50" in ip_addrs

    # Validity is ~10 years out, ±1 day.
    now = _dt.datetime.now(_dt.timezone.utc)
    expected = now + _dt.timedelta(days=365 * 10)
    delta = abs((cert.not_valid_after_utc - expected).total_seconds())
    assert delta < 86400, (
        f"Validity {cert.not_valid_after_utc} differs from {expected} by {delta}s"
    )


def test_generate_self_signed_ca_signs_server_cert(tmp_path):
    """The server cert's issuer matches the CA subject."""
    paths = tls.generate_self_signed(
        tmp_path, hostnames=["localhost"], ips=["127.0.0.1"]
    )
    server_cert = x509.load_pem_x509_certificate(paths.cert_path.read_bytes())
    ca_cert = x509.load_pem_x509_certificate(paths.ca_cert_path.read_bytes())

    assert server_cert.issuer == ca_cert.subject
    assert ca_cert.issuer == ca_cert.subject  # CA self-signed


@pytest.mark.skipif(os.name != "posix", reason="POSIX only — Windows uses NTFS ACLs")
def test_generate_self_signed_key_mode_0600(tmp_path):
    paths = tls.generate_self_signed(
        tmp_path, hostnames=["localhost"], ips=["127.0.0.1"]
    )
    mode = paths.key_path.stat().st_mode & 0o777
    assert mode == 0o600


def test_generate_self_signed_unwritable_dir_raises(tmp_path, monkeypatch):
    """data_dir that cannot be created surfaces as TLSConfigError."""
    def _boom(*_a, **_kw):
        raise OSError("permission denied")
    monkeypatch.setattr(Path, "mkdir", _boom)

    with pytest.raises(tls.TLSConfigError) as excinfo:
        tls.generate_self_signed(
            tmp_path, hostnames=["localhost"], ips=["127.0.0.1"]
        )
    assert "not writable" in excinfo.value.reason


# ---------------------------------------------------------------------------
# load_or_generate (auto mode)
# ---------------------------------------------------------------------------


def test_load_or_generate_reuses_existing(tmp_path):
    cfg = _make_config()
    cert1, key1 = tls.load_or_generate(cfg, tmp_path)
    first_bytes = cert1.read_bytes()

    cert2, key2 = tls.load_or_generate(cfg, tmp_path)
    assert cert2 == cert1
    assert key2 == key1
    # Contents unchanged confirms we returned the existing cert, not regenerated.
    assert cert2.read_bytes() == first_bytes


def test_load_or_generate_regenerates_on_ip_change(tmp_path, monkeypatch):
    cfg = _make_config(BIND_ADDRESS="0.0.0.0")

    monkeypatch.setattr(
        tls,
        "collect_local_identifiers",
        lambda _bind: (["localhost"], ["127.0.0.1", "192.168.1.50"]),
    )
    cert1, _ = tls.load_or_generate(cfg, tmp_path)
    first_bytes = cert1.read_bytes()

    monkeypatch.setattr(
        tls,
        "collect_local_identifiers",
        lambda _bind: (["localhost"], ["127.0.0.1", "10.0.0.5"]),
    )
    cert2, _ = tls.load_or_generate(cfg, tmp_path)
    assert cert2 == cert1  # path is stable
    assert cert2.read_bytes() != first_bytes  # file was rewritten

    new_sans = tls.read_cert_info(cert2).sans
    assert "10.0.0.5" in new_sans
    assert "192.168.1.50" not in new_sans


def test_load_or_generate_regenerates_on_corrupt_cert(tmp_path):
    cfg = _make_config()
    cert1, _ = tls.load_or_generate(cfg, tmp_path)
    cert1.write_bytes(b"not a real cert at all")

    cert2, _ = tls.load_or_generate(cfg, tmp_path)
    assert cert2 == cert1
    # File was rewritten and parses cleanly.
    info = tls.read_cert_info(cert2)
    assert info.subject


def test_load_or_generate_no_config(tmp_path):
    cfg = _make_config(TLS_AUTO_GENERATE=False)
    with pytest.raises(tls.TLSConfigError) as excinfo:
        tls.load_or_generate(cfg, tmp_path)
    reason = excinfo.value.reason
    assert "no cert" in reason.lower() or "auto_generate" in reason


# ---------------------------------------------------------------------------
# load_or_generate (user-provided mode)
# ---------------------------------------------------------------------------


def test_load_or_generate_provided_missing_file(tmp_path):
    missing = tmp_path / "no-such-cert.crt"
    key = tmp_path / "no-such-key.key"
    cfg = _make_config(TLS_CERT_FILE=str(missing), TLS_KEY_FILE=str(key))

    with pytest.raises(tls.TLSConfigError) as excinfo:
        tls.load_or_generate(cfg, tmp_path)
    assert str(missing) in excinfo.value.reason


def test_load_or_generate_provided_only_one_path(tmp_path):
    cfg = _make_config(TLS_CERT_FILE="/some/path.crt", TLS_KEY_FILE="")
    with pytest.raises(tls.TLSConfigError) as excinfo:
        tls.load_or_generate(cfg, tmp_path)
    assert "both" in excinfo.value.reason.lower()


def test_load_or_generate_provided_expired_warns_but_loads(tmp_path, caplog):
    cert_path = tmp_path / "expired.crt"
    key_path = tmp_path / "expired.key"
    _write_expired_cert(cert_path, key_path)

    cfg = _make_config(
        TLS_CERT_FILE=str(cert_path), TLS_KEY_FILE=str(key_path)
    )

    with caplog.at_level(logging.WARNING, logger="server.tls"):
        c, k = tls.load_or_generate(cfg, tmp_path)
    assert c == cert_path
    assert k == key_path
    assert any("expired" in rec.message.lower() for rec in caplog.records), (
        f"expected an 'expired' warning, got: {[r.message for r in caplog.records]}"
    )


def test_load_or_generate_provided_unreadable_cert(tmp_path):
    cert_path = tmp_path / "bad.crt"
    key_path = tmp_path / "good.key"
    cert_path.write_bytes(b"this is not a PEM cert")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    cfg = _make_config(TLS_CERT_FILE=str(cert_path), TLS_KEY_FILE=str(key_path))
    with pytest.raises(tls.TLSConfigError) as excinfo:
        tls.load_or_generate(cfg, tmp_path)
    assert str(cert_path) in excinfo.value.reason


# ---------------------------------------------------------------------------
# read_cert_info
# ---------------------------------------------------------------------------


def test_read_cert_info_fields(tmp_path):
    paths = tls.generate_self_signed(
        tmp_path,
        hostnames=["localhost", "mybox"],
        ips=["127.0.0.1", "10.1.2.3"],
    )
    info = tls.read_cert_info(paths.cert_path)

    assert "OpenAVC" in info.issuer
    assert info.days_until_expiry > 365 * 9  # ~10 years out
    assert len(info.fingerprint_sha256) == 64
    assert "localhost" in info.sans
    assert "mybox" in info.sans
    assert "127.0.0.1" in info.sans
    assert "10.1.2.3" in info.sans
    assert info.warnings == []


def test_read_cert_info_expired_warning(tmp_path):
    cert_path = tmp_path / "expired.crt"
    key_path = tmp_path / "expired.key"
    _write_expired_cert(cert_path, key_path)

    info = tls.read_cert_info(cert_path)
    assert "expired" in info.warnings


# ---------------------------------------------------------------------------
# collect_local_identifiers
# ---------------------------------------------------------------------------


def test_collect_local_identifiers_loopback_bind():
    """Loopback-only bind keeps LAN IPs out of the cert."""
    _, ips = tls.collect_local_identifiers("127.0.0.1")
    assert "127.0.0.1" in ips
    for ip in ips:
        assert ip in ("127.0.0.1", "::1"), f"unexpected ip {ip} for loopback bind"


def test_collect_local_identifiers_wide_open(monkeypatch):
    """Wide-open bind adds every LAN IPv4 from ifaddr."""

    class _IPInfo:
        def __init__(self, ip):
            self.ip = ip
            self.network_prefix = 24

    class _Adapter:
        nice_name = "Test"

        def __init__(self, ips):
            self.ips = ips

    fake_module = SimpleNamespace(
        get_adapters=lambda: [_Adapter([_IPInfo("192.168.1.50"), _IPInfo("127.0.0.1")])],
    )
    monkeypatch.setitem(sys.modules, "ifaddr", fake_module)

    _, ips = tls.collect_local_identifiers("0.0.0.0")
    assert "192.168.1.50" in ips
    assert "127.0.0.1" in ips
    assert "169.254.99.1" not in ips


def test_collect_local_identifiers_hostname_sanitized(monkeypatch):
    monkeypatch.setattr(socket, "gethostname", lambda: "Aaron's Pi 4")
    hostnames, _ = tls.collect_local_identifiers("127.0.0.1")

    sanitized = [h for h in hostnames if h != "localhost"]
    assert sanitized, "expected a sanitized hostname alongside 'localhost'"
    for h in sanitized:
        assert all(c.isalnum() or c == "-" for c in h), (
            f"hostname {h!r} contains unexpected chars"
        )
