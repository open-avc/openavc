"""Tests for the SSRF-guard helpers in server.utils.net_safety."""

import pytest

from server.utils.net_safety import assert_safe_outbound_url, ip_is_internal


def test_ip_is_internal_classification():
    # Internal / reserved ranges (v4 + v6).
    assert ip_is_internal("127.0.0.1")
    assert ip_is_internal("10.0.0.1")
    assert ip_is_internal("192.168.1.50")
    assert ip_is_internal("172.16.5.5")
    assert ip_is_internal("169.254.169.254")  # cloud metadata (link-local)
    assert ip_is_internal("::1")
    assert ip_is_internal("0.0.0.0")
    assert ip_is_internal("not-an-ip")  # unparseable -> fail closed
    # Public addresses pass.
    assert not ip_is_internal("8.8.8.8")
    assert not ip_is_internal("1.1.1.1")


@pytest.mark.asyncio
async def test_blocks_loopback_and_metadata():
    # IP literals resolve locally (no network), so these are deterministic.
    for url in (
        "http://127.0.0.1:9000/x",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.5/x",
        "http://[::1]/x",
    ):
        with pytest.raises(ValueError):
            await assert_safe_outbound_url(url)


@pytest.mark.asyncio
async def test_rejects_non_http_scheme():
    for url in ("ftp://8.8.8.8/x", "file:///etc/passwd", "gopher://8.8.8.8/"):
        with pytest.raises(ValueError):
            await assert_safe_outbound_url(url)


@pytest.mark.asyncio
async def test_allows_public_host():
    # Public IP literal -> no resolution needed, must not raise.
    await assert_safe_outbound_url("http://8.8.8.8/x")


@pytest.mark.asyncio
async def test_allow_internal_opt_in_skips_check():
    # Explicit opt-in (localhost sidecar pattern) bypasses the host check but
    # still enforces the scheme.
    await assert_safe_outbound_url("http://127.0.0.1:9000/x", allow_internal=True)
    with pytest.raises(ValueError):
        await assert_safe_outbound_url("ftp://127.0.0.1/x", allow_internal=True)
