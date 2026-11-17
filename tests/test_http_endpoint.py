"""Where an http device is reached: transport/endpoint.http_endpoint.

The HTTP transport's builder, Test Connection, Open Web UI and the Driver
Builder's raw probe all ask this one function, so its answer is pinned here
and each door's own test pins that the door asks it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from openavc.core.device_config import connection_of
from openavc.transport.endpoint import UnusablePort, http_endpoint, implied_port, url_host

LIVE_TEST_HELPERS = (
    Path(__file__).resolve().parents[1]
    / "openavc" / "web" / "programmer" / "src" / "components" / "driver-builder"
    / "liveTestHelpers.ts"
)


@pytest.mark.parametrize(
    ("host", "written"),
    [
        ("192.0.2.40", "192.0.2.40"),
        ("acme.example", "acme.example"),
        (" 192.0.2.40 ", "192.0.2.40"),
        ("2001:db8::40", "[2001:db8::40]"),
        ("[2001:db8::40]", "[2001:db8::40]"),
        ("fe80::1%eth0", "[fe80::1%25eth0]"),
        ("", ""),
        (None, ""),
    ],
)
def test_a_host_as_it_goes_in_a_url(host, written):
    assert url_host(host) == written


@pytest.mark.parametrize(
    ("config", "url"),
    [
        ({}, "http://192.0.2.40:80"),
        ({"ssl": True}, "https://192.0.2.40:443"),
        ({"ssl": True, "port": 80}, "https://192.0.2.40:80"),
        ({"port": 8080}, "http://192.0.2.40:8080"),
        ({"port": "8443", "ssl": True}, "https://192.0.2.40:8443"),
        ({"port": ""}, "http://192.0.2.40:80"),
        ({"port": None, "ssl": True}, "https://192.0.2.40:443"),
        ({"ssl": None}, "http://192.0.2.40:80"),
    ],
)
def test_the_base_url(config, url):
    assert http_endpoint("192.0.2.40", config).base_url == url


def test_the_parts_a_browser_link_is_written_from():
    endpoint = http_endpoint("2001:db8::40", {"ssl": True, "port": "8443"})
    assert (endpoint.scheme, endpoint.host, endpoint.port) == ("https", "[2001:db8::40]", 8443)
    assert endpoint.base_url == "https://[2001:db8::40]:8443"


@pytest.mark.parametrize(
    ("config", "verify"),
    [
        ({}, True),
        ({"verify_ssl": True}, True),
        ({"verify_ssl": False}, False),
        # Null or blank is read as not set: the check stays on.
        ({"verify_ssl": None}, True),
        ({"verify_ssl": ""}, True),
        # Anything else by its truth, as the TCP and MQTT transports read it.
        ({"verify_ssl": 0}, False),
        ({"verify_ssl": "false"}, True),
    ],
)
def test_the_certificate_check(config, verify):
    assert http_endpoint("192.0.2.40", config).verify is verify


@pytest.mark.parametrize("port", ["web", "80.5", 0, 70000, -1, [80]])
def test_a_port_no_connection_can_be_made_to_is_refused_as_written(port):
    with pytest.raises(UnusablePort) as caught:
        http_endpoint("192.0.2.40", {"port": port})
    assert caught.value.port == port


@pytest.mark.parametrize("port", [None, "", 8080, "8080", "web"])
def test_the_port_is_the_one_a_device_connection_reports(port):
    """Test Connection reports connection_of's port next to its result; the
    port it dials is the one reported."""
    config = {"transport": "http", "host": "192.0.2.40", "port": port}
    reported = connection_of({"id": "acme1", "driver": "acme_unregistered", "config": config}).port
    try:
        dialled = http_endpoint("192.0.2.40", config).port
    except UnusablePort:
        dialled = None
    assert dialled == reported


def test_the_driver_builders_live_test_starts_at_the_same_ports():
    """Live Test's port box starts an http draft that names no port where the
    connection dials (liveTestHelpers.ts keeps the two numbers); a copy that
    drifts sends the test to a port the device never uses."""
    source = LIVE_TEST_HELPERS.read_text(encoding="utf-8")
    written = dict(re.findall(r"export const (HTTPS?_PORT) = (\d+);", source))
    assert written == {
        "HTTP_PORT": str(implied_port("http", {})),
        "HTTPS_PORT": str(implied_port("http", {"ssl": True})),
    }


# --- a host written as a URL -------------------------------------------------
# A driver may ask for a service's address as a URL (https://acme.example):
# the URL names the scheme, the name and the port, and the Port and Use HTTPS
# settings are not read, as such a driver's own client reads it.


@pytest.mark.parametrize(
    ("host", "config", "url"),
    [
        ("https://acme.example", {}, "https://acme.example:443"),
        ("http://acme.example", {"ssl": True, "port": 443}, "http://acme.example:80"),
        ("https://acme.example:8443", {"port": 9999}, "https://acme.example:8443"),
        ("https://acme.example:8443/api/", {}, "https://acme.example:8443/api"),
        (" HTTPS://Acme.Example ", {}, "https://acme.example:443"),
        ("https://[2001:db8::40]:8443", {}, "https://[2001:db8::40]:8443"),
    ],
    ids=["https", "http-over-the-settings", "own-port", "path", "as-typed", "ipv6"],
)
def test_a_url_host_is_reached_where_it_names(host, config, url):
    assert http_endpoint(host, config).base_url == url


def test_a_url_host_with_a_port_that_is_not_one_is_refused_as_written():
    with pytest.raises(UnusablePort) as caught:
        http_endpoint("https://acme.example:web", {"port": 443})
    assert caught.value.port == "web"


@pytest.mark.parametrize(
    ("host", "named"),
    [
        ("https://acme.example", ("acme.example", 443)),
        ("http://acme.example", ("acme.example", 80)),
        ("https://acme.example:8443/api", ("acme.example", 8443)),
        ("https://[2001:db8::40]:8443", ("2001:db8::40", 8443)),
        ("https://acme.example:web", ("acme.example", None)),
    ],
)
def test_a_device_connection_names_the_address_a_url_host_names(host, named):
    config = {"transport": "http", "host": host, "port": 9999, "ssl": False}
    conn = connection_of({"id": "acme1", "driver": "acme_unregistered", "config": config})
    assert (conn.host, conn.port) == named


@pytest.mark.parametrize(
    "host", ["https://acme.example", "http://acme.example:8080/api", "https://acme.example:web"],
)
def test_a_url_hosts_port_is_the_one_a_device_connection_reports(host):
    config = {"transport": "http", "host": host, "port": 9999, "ssl": False}
    reported = connection_of({"id": "acme1", "driver": "acme_unregistered", "config": config}).port
    try:
        dialled = http_endpoint(host, config).port
    except UnusablePort:
        dialled = None
    assert dialled == reported
