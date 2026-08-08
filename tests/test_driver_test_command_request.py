"""TestCommandRequest schema — accepts both numeric and string ports.

Serial drivers identify the device by a path string ("COM3", "/dev/ttyUSB0"),
while IP transports carry an integer port. The Driver Builder Live Test panel
talks to a single endpoint that has to handle both shapes.
"""

import pytest
from pydantic import ValidationError

from openavc.api.models import TestCommandRequest


def test_accepts_int_port_for_tcp():
    req = TestCommandRequest(host="192.168.1.10", port=23, transport="tcp")
    assert req.port == 23


def test_accepts_numeric_string_port_for_tcp():
    """Pydantic coerces numeric strings to int when the field is int|str."""
    req = TestCommandRequest(host="192.168.1.10", port="23", transport="tcp")
    # Either int(23) or str("23") is acceptable; the route layer coerces
    # before handing off to TCPTransport.
    assert req.port in (23, "23")


def test_accepts_serial_port_path():
    """A serial-port path must survive validation as a string."""
    req = TestCommandRequest(host="", port="COM3", transport="serial")
    assert req.port == "COM3"
    assert req.host == ""


def test_accepts_unix_serial_port_path():
    req = TestCommandRequest(host="", port="/dev/ttyUSB0", transport="serial")
    assert req.port == "/dev/ttyUSB0"


def test_host_defaults_to_empty():
    """Serial test requests don't need a host."""
    req = TestCommandRequest(port="COM3", transport="serial")
    assert req.host == ""


def test_rejects_non_port_types():
    with pytest.raises(ValidationError):
        TestCommandRequest(host="h", port=[1, 2], transport="tcp")  # type: ignore[arg-type]
