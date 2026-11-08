"""Pieces a Discovery scan and a device audit's network check share.

``port_scan_lists`` (which TCP ports a scan checks), the evidence helpers the
scan's phases build their records with, the one raw HTTP GET, and the silent
port read. Each has one home so the two callers cannot disagree.
"""

from __future__ import annotations

import asyncio

from openavc.discovery.engine import THOROUGH_EXTRA_PORTS, DiscoveryEngine
from openavc.discovery.hints import build_signal_index, parse_driver_discovery
from openavc.discovery.http_fetch import HttpExchange, http_get, parse_head, same_origin_redirect
from openavc.discovery.oui_database import OUIDatabase
from openavc.discovery.port_scanner import BASELINE_PORTS, read_greeting
from openavc.discovery.result import DeviceState
from openavc.discovery.scan_evidence import (
    banner_texts,
    derived_evidence,
    hostname_evidence,
    mac_info_and_evidence,
)
from openavc.discovery.tier_matcher import (
    TierMatcher,
    evidence_active_probe,
    evidence_open_port,
    vendor_strings_in_text,
)
from tests.helpers import refusing_tcp_port


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


def _vendor_index():
    return build_signal_index([
        _hint("acme_widget", {"manufacturer_alias": ["acw", "acme widgets", "widgetworks"],
                              "port_open": [5001]}),
        _hint("acme_gadget", {"manufacturer_alias": ["ag"], "port_open": [5001]}),
    ])


def _named(texts):
    return {
        (ev.data["value"], ev.data["source_probe_id"], ev.data["raw"])
        for ev in vendor_strings_in_text(texts, _vendor_index())
    }


def test_a_greeting_or_server_header_names_a_manufacturer():
    # Leading a line, a three-letter alias counts; four or more count anywhere
    # as a whole word; a longer alias found in the same text wins.
    assert _named({"greeting:23": "ACW Widget (Mk II)\r\nUser-name: "}) == {
        ("acw", "greeting:23", "ACW Widget (Mk II)"),
    }
    assert _named({"http_server:80": "Acme Widgets"}) == {
        ("acme widgets", "http_server:80", "Acme Widgets"),
    }
    assert _named({"http_server:8080": "embedded WidgetWorks/2.1"}) == {
        ("widgetworks", "http_server:8080", "embedded WidgetWorks/2.1"),
    }


def test_a_short_alias_counts_only_where_it_stands_alone():
    # "ag" inside words, or three letters mid-line, name nobody.
    assert _named({"greeting:23": "Login again\r\nstage 2"}) == set()
    assert _named({"greeting:23": "Welcome to the ACW box"}) == set()
    assert _named({"greeting:22": "not widgetworksx"}) == set()
    # A whole line that is the alias still counts, however short.
    assert _named({"greeting:23": "AG\r\n> "}) == {("ag", "greeting:23", "AG")}


def test_derived_evidence_reads_greetings_and_the_ssdp_server():
    index = _vendor_index()
    ssdp = evidence_active_probe("custom_x_tcp", response={"text": "hi"}, port=23)
    ssdp.data.update({"kind": "ssdp", "server": "Linux UPnP/1.0 WidgetWorks/3"})
    records = derived_evidence(
        [ssdp], [5001], index, banner_texts({23: "ACW Widget", 22: ""}),
    )
    named = {(r.data["value"], r.data["source_probe_id"]) for r in records
             if r.data["kind"] == "vendor_string"}
    assert named == {("acw", "greeting:23"), ("widgetworks", "ssdp_server")}
    # Port 5001 alone would leave two drivers possible; the greeting names one.
    match = TierMatcher(index).match([evidence_open_port(5001), *records])
    assert match.state == DeviceState.POSSIBLE
    assert match.candidates[0] == "acme_widget"


def test_parse_head_keeps_order_and_repeats():
    head = "HTTP/1.0 302 Found\r\nSet-Cookie: a=1\r\nSet-Cookie: b=2\r\nLocation: /login"
    assert parse_head(head) == [
        ("Set-Cookie", "a=1"), ("Set-Cookie", "b=2"), ("Location", "/login"),
    ]


def _redirect(url: str, status: int, location: str | None) -> HttpExchange:
    head = f"HTTP/1.1 {status} Moved"
    if location is not None:
        head += f"\r\nLocation: {location}"
    return HttpExchange(url=url, head=head, headers=parse_head(head))


def test_a_redirect_is_followed_only_on_the_same_address_and_port():
    """The page a redirect leads to is offered only when it stays where the
    request went: the same scheme, address and port, written out or
    relative. A request never leaves the address it was sent to."""
    here = "http://10.0.0.5:80/"
    follows = {
        "http://10.0.0.5:80/www/index.html": "http://10.0.0.5:80/www/index.html",
        "http://10.0.0.5/www/index.html": "http://10.0.0.5:80/www/index.html",
        "/www/index.html": "http://10.0.0.5:80/www/index.html",
        "www/index.html?lang=en#top": "http://10.0.0.5:80/www/index.html?lang=en",
        "//10.0.0.5/login": "http://10.0.0.5:80/login",
    }
    for location, target in follows.items():
        assert same_origin_redirect(_redirect(here, 301, location)) == target, location
    assert same_origin_redirect(
        _redirect("https://10.0.0.5:443/", 302, "https://10.0.0.5:443/www/index.html"),
    ) == "https://10.0.0.5:443/www/index.html"

    stays = [
        "https://10.0.0.5:443/",           # another scheme and port
        "http://10.0.0.5:8080/",           # another port
        "http://10.0.0.6/",                # another address
        "http://widget.local/",            # a name, even for this address
        "//example.com/",                  # another host, scheme-relative
        "http://user@10.0.0.5/",           # a sign-in in the address
        "/",                               # the page asked for
        "/a page",                         # not a request line
        "http://10.0.0.5:x/",              # not a port
        "",
    ]
    for location in stays:
        assert same_origin_redirect(_redirect(here, 301, location)) is None, location
    assert same_origin_redirect(_redirect(here, 301, None)) is None
    assert same_origin_redirect(_redirect(here, 200, "/www/index.html")) is None
    assert same_origin_redirect(_redirect(here, 304, "/www/index.html")) is None


async def test_http_get_reports_why_nothing_came_back():
    with refusing_tcp_port() as port:
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


def _vendor_strings(records):
    return [
        (r.data["value"], r.data["source_probe_id"]) for r in records
        if r.data["kind"] == "vendor_string"
    ]


def _ssdp(server: str, port: int):
    ev = evidence_active_probe(f"custom_{port}_tcp", response={"text": "hi"}, port=port)
    ev.data.update({"kind": "ssdp", "server": server})
    return ev


def test_derived_evidence_reads_the_first_ssdp_server_that_says_something():
    """A device heard twice names its server twice: the first non-empty
    SERVER is the one read, and a server the caller's texts already carry
    wins over both."""
    index = _vendor_index()
    heard = [_ssdp("", 1), _ssdp("Linux UPnP/1.0 WidgetWorks/3", 2), _ssdp("Acme Widgets/2", 3)]
    assert _vendor_strings(derived_evidence(heard, [], index)) == [
        ("widgetworks", "ssdp_server"),
    ]
    given = {"ssdp_server": "Acme Widgets/2"}
    assert _vendor_strings(derived_evidence(heard, [], index, given)) == [
        ("acme widgets", "ssdp_server"),
    ]
    assert given == {"ssdp_server": "Acme Widgets/2"}
    greetings = {"greeting:23": "hello"}
    derived_evidence(heard, [], index, greetings)
    assert greetings == {"greeting:23": "hello"}  # the caller's dict untouched


def test_derived_evidence_records_each_manufacturer_string_once():
    """A string already in the evidence is not recorded again, and one two
    texts name is recorded from the first of them."""
    index = _vendor_index()
    texts = {"greeting:23": "ACW Widget", "http_server:80": "ACW httpd"}
    assert _vendor_strings(derived_evidence([], [], index, texts)) == [
        ("acw", "greeting:23"),
    ]
    recorded = derived_evidence([], [], index, {"greeting:21": "ACW ftpd"})
    assert _vendor_strings(derived_evidence(recorded, [], index, texts)) == []
