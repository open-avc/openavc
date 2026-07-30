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
``server/utils/log_redaction.py`` for the policy.
"""

from __future__ import annotations

from server.utils.log_redaction import get_secret_registry


def format_wire_data(data: bytes, device_id: str | None = None) -> str:
    """Format bytes for a TX/RX log line — decoded text, or hex for binary.

    ``device_id`` names the device whose registered credentials are masked.
    Omit it for traffic that belongs to no device (a discovery probe, a
    connection test); the bytes are then formatted and not redacted, because
    there is no credential set to redact against.
    """
    try:
        text = data.decode("ascii").strip()
        if not text.isprintable():
            text = data.hex()
    except (UnicodeDecodeError, ValueError):
        text = data.hex()

    if not device_id:
        return text
    return get_secret_registry().redact_for(device_id, text)
