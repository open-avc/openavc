"""THE formatter for device traffic in the log.

Every transport that logs a payload byte routes through :func:`format_wire_data`
— TCP, serial and UDP directly; OSC through whichever of TCP/UDP it is running
over, since ``OSCTransport.send`` delegates rather than logging its own TX line.
There were three copies of this function before (``tcp.py``,
``serial_transport.py``, ``udp.py``), byte-identical apart from one being
module-level and two being staticmethods.

One copy matters beyond tidiness: this is where credentials are removed. Device
protocols put logins on the wire in the clear, and this traffic is pinned to
DEBUG into the in-memory ring buffer whatever the configured log level, so it
reaches ``GET /api/logs/recent`` and the Log view's Download. Redacting here
means the credential never enters the buffer at all, rather than being scrubbed
by each reader in turn.

The redaction set is per device: the transport already carries its device id as
``name``, so no transport signature changed. See
``openavc/utils/log_redaction.py`` for the policy.

A file is never logged either. While a command that takes or hands back a file
runs on a device, the traffic recorder holds that direction
(``core/device_traffic.py``). The transports write no TX/RX line in a held
direction (:func:`wire_held`): a firmware image is thousands of messages, and a
line each says nothing the recorder's one note about the file does not. A line
that is formatted anyway gives the size instead of the bytes.
"""

from __future__ import annotations

from openavc.core.device_traffic import get_traffic_recorder
from openavc.utils.log_redaction import get_secret_registry


def wire_held(device_id: str | None, direction: str) -> bool:
    """Whether this device's traffic in ``direction`` is held for a file."""
    return bool(device_id) and get_traffic_recorder().is_held(device_id, direction)


def format_wire_data(
    data: bytes, device_id: str | None = None, direction: str | None = None,
) -> str:
    """Format bytes for a TX/RX log line — decoded text, or hex for binary.

    ``device_id`` names the device whose registered credentials are masked.
    Omit it for traffic that belongs to no device (a discovery probe, a
    connection test); the bytes are then formatted and not redacted, because
    there is no credential set to redact against. ``direction`` (``"tx"`` /
    ``"rx"``) lets a line in a direction held for a file give only its size.
    """
    if device_id and direction and get_traffic_recorder().is_held(device_id, direction):
        return f"({len(data)} bytes, not recorded while a file command ran)"
    try:
        text = data.decode("ascii").strip()
        if not text.isprintable():
            text = data.hex()
    except (UnicodeDecodeError, ValueError):
        text = data.hex()

    if not device_id:
        return text
    return get_secret_registry().redact_for(device_id, text)
