"""
Test helper utilities for OpenAVC tests.

Provides event-driven assertion helpers to replace raw asyncio.sleep() calls
with condition-based waiting, improving test reliability on slow systems.
"""

import asyncio
from typing import Any, Callable


async def wait_for_state(
    state,
    key: str,
    expected: Any = None,
    timeout: float = 5.0,
    interval: float = 0.05,
) -> Any:
    """Wait until a state key has the expected value, or any value if expected is None.

    Returns the value when matched. Raises TimeoutError if not matched within timeout.
    """
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        value = state.get(key)
        if expected is None and value is not None:
            return value
        if value == expected:
            return value
        await asyncio.sleep(interval)
    raise TimeoutError(f"State key '{key}' did not reach {expected!r} within {timeout}s (current: {state.get(key)!r})")


async def wait_for_condition(
    condition: Callable[[], bool],
    timeout: float = 5.0,
    interval: float = 0.05,
    message: str = "Condition not met",
) -> None:
    """Wait until a callable returns True. Raises TimeoutError otherwise."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if condition():
            return
        await asyncio.sleep(interval)
    raise TimeoutError(f"{message} within {timeout}s")


def tunnel_stream_client(response) -> Any:
    """A stand-in httpx client whose ``.stream()`` hands back ``response``.

    The tunnel proxies every request with ``client.stream(...)``, which returns
    an async context manager rather than a coroutine -- so a bare AsyncMock
    does not stand in for it. Any attribute the caller doesn't set on
    ``response`` (``aread``, ``aiter_bytes``) gets a default here, so a test
    that only cares about request headers can pass a two-field MagicMock.
    """
    from unittest.mock import AsyncMock, MagicMock

    if not isinstance(getattr(response, "aread", None), AsyncMock):
        response.aread = AsyncMock()

    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=response)
    context.__aexit__ = AsyncMock(return_value=False)

    client = MagicMock()
    client.stream = MagicMock(return_value=context)
    # stop() closes whatever client it was handed.
    client.aclose = AsyncMock()
    return client


def make_cloud_cert_pem(
    label: str,
    zone: str,
    *,
    expired: bool = False,
    dns_sans: list[str] | None = None,
    age_days: int = 0,
    lifetime_days: int = 60,
) -> tuple[bytes, bytes]:
    """Build a cloud-style wildcard cert + key PEM pair for TLS tests.

    Self-signed stand-in for a cloud-issued certificate: SANs default to
    exactly ``{*.label.zone, label.zone}``, overridable via ``dns_sans``.
    ``age_days``/``lifetime_days`` position the validity window (e.g. an old
    cert deep in its renewal window). Returns ``(cert_pem, key_pem)`` bytes.
    """
    import datetime as dt

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    if dns_sans is None:
        dns_sans = [f"*.{label}.{zone}", f"{label}.{zone}"]

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = dt.datetime.now(dt.timezone.utc)
    if expired:
        not_before = now - dt.timedelta(days=90, minutes=5)
        not_after = now - dt.timedelta(days=1)
    else:
        not_before = now - dt.timedelta(days=age_days, minutes=5)
        not_after = not_before + dt.timedelta(days=lifetime_days)
    subject = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, f"{label}.{zone}")]
    )
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(n) for n in dns_sans]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    key_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return cert_pem, key_pem


def sign_csr_like_cloud(csr_pem: str, *, lifetime_days: int = 90) -> bytes:
    """Issue a cert for a CSR the way the cloud would (test stand-in).

    Self-signs with a throwaway CA key over the CSR's public key and SANs.
    Returns the certificate PEM bytes — pair it with the CSR's private key.
    """
    import datetime as dt

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    csr = x509.load_pem_x509_csr(csr_pem.encode())
    san = csr.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test Issuing CA")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(csr.subject)
        .issuer_name(issuer)
        .public_key(csr.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=lifetime_days))
        .add_extension(san, critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM)
