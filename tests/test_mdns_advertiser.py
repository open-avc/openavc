"""Tests for the mDNS advertiser TXT pair construction.

Focused on the scheme/port behaviour added for HTTPS support — readers
without the new `scheme` key must still default to plain HTTP, and the
SRV port must point at the TLS listener when TLS is on.
"""

from __future__ import annotations

from server.discovery.mdns_advertiser import MDNSAdvertiser


def _make_advertiser(**overrides) -> MDNSAdvertiser:
    kwargs = {
        "instance_name": "Test Room",
        "instance_id": "test-instance-id",
        "http_port": 8080,
        "version": "0.1.0",
    }
    kwargs.update(overrides)
    return MDNSAdvertiser(**kwargs)


def test_txt_pairs_omit_scheme_when_tls_off():
    adv = _make_advertiser()
    pairs = adv._build_txt_pairs()
    assert pairs == {
        "name": "Test-Room",
        "id": "test-instance-id",
        "version": "0.1.0",
        "path": "/panel",
    }
    assert "scheme" not in pairs


def test_txt_pairs_include_scheme_https_when_tls_on():
    adv = _make_advertiser(tls_enabled=True, tls_port=8443)
    pairs = adv._build_txt_pairs()
    assert pairs["scheme"] == "https"
    # baseline keys still present
    assert pairs["name"] == "Test-Room"
    assert pairs["id"] == "test-instance-id"
    assert pairs["version"] == "0.1.0"
    assert pairs["path"] == "/panel"


def test_service_port_is_http_port_when_tls_off():
    adv = _make_advertiser(http_port=9090)
    assert adv._service_port == 9090


def test_service_port_is_tls_port_when_tls_on():
    """SRV record must point at the TLS listener, not the redirect listener."""
    adv = _make_advertiser(http_port=8080, tls_enabled=True, tls_port=8443)
    assert adv._service_port == 8443


def test_tls_port_zero_default_when_tls_off():
    """Default tls_port stays at 0 when TLS isn't in play — never advertised."""
    adv = _make_advertiser()
    assert adv._tls_port == 0
    assert adv._service_port == 8080


# --- Direct A queries for "<hostname>.local" ---
#
# Hosts with a native mDNS responder (Windows, avahi) answer their own
# hostname; appliance deployments have no other responder, so the advertiser
# must answer or the hostname URL shown on the setup screen / Panel Access
# card never resolves from a phone.

import asyncio  # noqa: E402
import struct  # noqa: E402

from server.discovery.mdns_scanner import DNS_TYPE_A, encode_dns_name  # noqa: E402


def _query_packet(name: str, qtype: int) -> bytes:
    header = struct.pack("!HHHHHH", 0, 0, 1, 0, 0, 0)
    return header + encode_dns_name(name) + struct.pack("!HH", qtype, 1)


async def _announcements_for(adv, packet) -> list:
    sent = []

    async def fake_announce():
        sent.append(True)

    adv._send_announcement = fake_announce
    adv._handle_query(packet, ("192.168.1.9", 5353))
    await asyncio.sleep(0)
    return sent


async def test_answers_a_query_for_own_hostname():
    adv = _make_advertiser()
    adv._hostname = "openavc-box"
    packet = _query_packet("openavc-box.local.", DNS_TYPE_A)
    assert await _announcements_for(adv, packet)


async def test_a_query_matches_case_insensitively():
    adv = _make_advertiser()
    adv._hostname = "OpenAVC-Box"
    packet = _query_packet("openavc-box.LOCAL.", DNS_TYPE_A)
    assert await _announcements_for(adv, packet)


async def test_ignores_a_query_for_other_hosts():
    adv = _make_advertiser()
    adv._hostname = "openavc-box"
    packet = _query_packet("some-other-host.local.", DNS_TYPE_A)
    assert not await _announcements_for(adv, packet)
