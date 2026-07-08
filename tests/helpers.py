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


def make_cloud_cert_pem(
    label: str,
    zone: str,
    *,
    expired: bool = False,
    dns_sans: list[str] | None = None,
) -> tuple[bytes, bytes]:
    """Build a cloud-style wildcard cert + key PEM pair for TLS tests.

    Self-signed stand-in for a cloud-issued certificate: SANs default to
    exactly ``{*.label.zone, label.zone}``, overridable via ``dns_sans``.
    Returns ``(cert_pem, key_pem)`` bytes.
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
    not_after = now - dt.timedelta(days=1) if expired else now + dt.timedelta(days=60)
    subject = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, f"{label}.{zone}")]
    )
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=90 if expired else 0, minutes=5))
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
