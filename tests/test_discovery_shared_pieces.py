"""Pieces a Discovery scan and a device audit's network check share.

``port_scan_lists`` (which TCP ports a scan checks), the evidence helpers the
scan's phases build their records with, the one raw HTTP GET, and the silent
port read. Each has one home so the two callers cannot disagree.
"""

from __future__ import annotations

import asyncio

from openavc.discovery.engine import (
    THOROUGH_EXTRA_PORTS,
    DiscoveryEngine,
    derived_evidence,
    hostname_evidence,
    mac_info_and_evidence,
)
from openavc.discovery.hints import build_signal_index, parse_driver_discovery
from openavc.discovery.http_fetch import http_get, parse_head
from openavc.discovery.oui_database import OUIDatabase
from openavc.discovery.port_scanner import BASELINE_PORTS, read_greeting
from openavc.discovery.tier_matcher import evidence_active_probe


def _hint(driver_id, discovery):
    return parse_driver_discovery({
        "id": driver_id, "name": driver_id, "manufacturer": "Acme",
        "category": "utility", "transport": "tcp", "discovery": discovery,
    })


def test_port_lists_take_drivers_catalog_and_extras():
    engine = DiscoveryEngine()
    engine.discovery_hints = [
        _hint("acme_widget", {"tcp_probe": {"port": 4999, "expect_regex": "ACME"},
                              "port_open": [5001]}),
    ]
    catalog = [{"id": "acme_gadget", "ports": [6100, "not-a-port"]}, "junk"]
    core, full = engine.port_scan_lists(catalog, extended=False)
    assert core == sorted(set(BASELINE_PORTS) | {4999, 5001})
    assert full == sorted(set(core) | {6100})
    _, thorough = engine.port_scan_lists(catalog, extended=True)
    assert set(THOROUGH_EXTRA_PORTS) <= set(thorough)


def test_hostname_evidence_names_each_matching_pattern():
    index = build_signal_index([_hint("acme_widget", {"hostname": ["^widget-"]})])
    records = hostname_evidence("widget-3000", index)
    assert [r.data.get("matched_pattern") for r in records] == ["^widget-"]
    bare = hostname_evidence("printer", index)
    assert len(bare) == 1 and not bare[0].data.get("matched_pattern")


def test_mac_info_names_the_card_maker_only_when_known():
    db = OUIDatabase()
    db.add_prefix("aa:bb:cc", "Acme", "utility")
    info, ev = mac_info_and_evidence("aa:bb:cc:00:11:22", db)
    assert info == {"mac": "aa:bb:cc:00:11:22", "manufacturer": "Acme", "category": "utility"}
    assert ev.data["vendor"] == "Acme"
    info, ev = mac_info_and_evidence("de:ad:be:ef:00:01", db)
    assert info == {"mac": "de:ad:be:ef:00:01"}


def test_derived_evidence_adds_claimed_ports_and_vendor_strings():
    index = build_signal_index([_hint("acme_widget", {"port_open": [5001],
                                                      "manufacturer_alias": ["Acme"]})])
    probe = evidence_active_probe(
        "custom_x_tcp", response={"text": "hi", "manufacturer": "Acme"}, port=23,
    )
    records = derived_evidence([probe], [23, 5001], index)
    kinds = sorted(r.data["kind"] for r in records)
    assert kinds == ["open_port", "vendor_string"]


def test_parse_head_keeps_order_and_repeats():
    head = "HTTP/1.0 302 Found\r\nSet-Cookie: a=1\r\nSet-Cookie: b=2\r\nLocation: /login"
    assert parse_head(head) == [
        ("Set-Cookie", "a=1"), ("Set-Cookie", "b=2"), ("Location", "/login"),
    ]


async def test_http_get_reports_why_nothing_came_back():
    import socket

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    exchange = await http_get(f"http://127.0.0.1:{port}/", timeout=5.0)
    assert exchange is not None and exchange.error == "refused"
    assert await http_get("ftp://127.0.0.1/") is None


async def test_read_greeting_sends_nothing_and_keeps_every_byte():
    received: list[bytes] = []

    async def handler(reader, writer):
        writer.write(b"\x00\x01WELCOME")
        await writer.drain()
        try:
            received.append(await asyncio.wait_for(reader.read(100), timeout=1.0))
        except asyncio.TimeoutError:
            received.append(b"<nothing>")
        writer.close()

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        greeting = await read_greeting("127.0.0.1", port, wait=0.5)
    finally:
        server.close()
        await server.wait_closed()
    assert greeting.data == b"\x00\x01WELCOME"
    assert greeting.first_byte_ms is not None
    assert received == [b""]  # the reader saw us close, having sent nothing
