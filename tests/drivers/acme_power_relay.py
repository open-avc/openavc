"""Acme Power Relay — an invented transport-less driver for core tests.

There is no such product. It models the shape of a device that holds no
connection at all: every command opens a throwaway socket, fires one
datagram, and closes. That shape is worth a core test because it is the one
that exercises the platform's transport-less path — ``connected`` carried by
the driver rather than a transport, no liveness watchdog, and a command
declared ``available_offline`` so the send is allowed to happen while the
device is, by definition, unreachable.

The payload format is made up: ``ACME/1 WAKE <unit_id>``.
"""

from __future__ import annotations

import logging
from typing import Any

from openavc.drivers.base import BaseDriver
from openavc.transport.udp import UDPTransport

log = logging.getLogger(__name__)


def build_wake_payload(unit_id: str) -> bytes:
    """Build the invented wake datagram.

    Raises ``ValueError`` when the unit id is missing or malformed, which is
    what lets the driver report a config problem instead of sending nonsense.
    """
    unit = (unit_id or "").strip()
    if not unit:
        raise ValueError("unit_id is required")
    if not unit.isalnum():
        raise ValueError(f"unit_id must be alphanumeric, got {unit_id!r}")
    return b"ACME/1 WAKE " + unit.upper().encode("ascii")


class AcmePowerRelayDriver(BaseDriver):
    """An invented power relay that is contacted, never connected to."""

    DRIVER_INFO: dict[str, Any] = {
        "id": "acme_power_relay",
        "name": "Acme Power Relay",
        "manufacturer": "Acme",
        "category": "power",
        "transport": "udp",
        "default_config": {"port": 9414, "host": "127.0.0.1"},
        "config_schema": {
            "host": {"type": "string", "label": "Host", "default": "127.0.0.1"},
            "port": {"type": "integer", "label": "Port", "default": 9414},
            "unit_id": {"type": "string", "label": "Unit ID"},
        },
        "state_variables": {
            "last_wake": {"type": "string", "label": "Last Wake Sent", "default": ""},
            "wakes_sent": {"type": "integer", "label": "Wakes Sent", "default": 0},
        },
        "commands": {
            # Runs with no live connection — the whole point of the device.
            "wake": {"label": "Send Wake", "available_offline": True},
            # A normal command, for contrast: the platform blocks this one
            # while the device is offline.
            "identify": {"label": "Identify"},
        },
    }

    async def _create_transport(self, transport_type: str) -> None:
        # Fire-and-forget: each command opens its own throwaway socket, so
        # there is no persistent transport to build.
        self.transport = None

    def _link_alive(self) -> bool:
        # Nothing to test — the device never answers, so the driver is ready
        # whenever it is active.
        return True

    async def send_command(
        self, command: str, params: dict[str, Any] | None = None
    ) -> Any:
        if command not in ("wake", "identify"):
            log.warning(f"[{self.device_id}] Unknown command: {command}")
            return None

        try:
            payload = build_wake_payload(self.config.get("unit_id", ""))
        except ValueError as e:
            log.error(f"[{self.device_id}] {e}")
            return None

        if command == "identify":
            payload = payload.replace(b"WAKE", b"IDENT")

        host = self.config.get("host", "127.0.0.1")
        port = int(self.config.get("port", 9414))

        udp = UDPTransport(name=self.device_id)
        try:
            await udp.open()
            await udp.send_to(payload, host, port)
        except Exception as e:
            log.error(f"[{self.device_id}] Failed to send: {e}")
            return None
        finally:
            await udp.close()

        if command == "wake":
            self.set_states({
                "last_wake": payload.decode("ascii"),
                "wakes_sent": int(self.get_state("wakes_sent") or 0) + 1,
            })
        return True
