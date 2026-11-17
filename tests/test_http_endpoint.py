"""Where an http device is reached: transport/endpoint.http_endpoint.

The HTTP transport's builder, Test Connection, Open Web UI and the Driver
Builder's raw probe all ask this one function, so its answer is pinned here
and each door's own test pins that the door asks it.
"""

from __future__ import annotations

import pytest

from openavc.core.device_config import connection_of
from openavc.transport.endpoint import UnusablePort, http_endpoint, url_host


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
