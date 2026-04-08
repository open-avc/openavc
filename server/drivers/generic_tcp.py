"""
OpenAVC Generic TCP Driver.

A utility driver for controlling any TCP device by sending raw commands.
Commands are defined in the device config and can include parameter substitution.

Useful for devices without a dedicated driver — the integrator defines the
command strings directly in the project configuration.
"""

from __future__ import annotations

import re
from typing import Any

from server.drivers.base import BaseDriver
from server.transport.tcp import TCPTransport
from server.utils.logger import get_logger

log = get_logger(__name__)


class GenericTCPDriver(BaseDriver):
    """Generic TCP device driver with configurable commands."""

    DRIVER_INFO = {
        "id": "generic_tcp",
        "name": "Generic TCP Device",
        "manufacturer": "Generic",
        "category": "utility",
        "version": "1.0.0",
        "author": "OpenAVC",
        "description": (
            "Send raw TCP commands to any device. "
            "Define commands in the device config."
        ),
        "transport": "tcp",
        "default_config": {
            "host": "",
            "port": 23,
            "delimiter": "\r\n",
            "inter_command_delay": 0.0,
            "commands": {},
        },
        "config_schema": {
            "host": {"type": "string", "required": True, "label": "IP Address"},
            "port": {"type": "integer", "default": 23, "label": "Port"},
            "delimiter": {
                "type": "string",
                "default": "\\r\\n",
                "label": "Delimiter",
            },
            "inter_command_delay": {
                "type": "number",
                "default": 0.0,
                "label": "Inter-Command Delay (sec)",
            },
            "commands": {
                "type": "object",
                "default": {},
                "label": "Command Map (name -> raw string)",
            },
        },
        "state_variables": {},
        "commands": {},
    }

    async def connect(self) -> None:
        """Connect to the device via TCP."""
        host = self.config.get("host", "")
        port = self.config.get("port", 23)
        from server.transport.binary_helpers import encode_escape_sequences
        delimiter_str = self.config.get("delimiter", "\r\n")
        delimiter = encode_escape_sequences(delimiter_str)
        delay = self.config.get("inter_command_delay", 0.0)

        self.transport = await TCPTransport.create(
            host=host,
            port=port,
            on_data=self.on_data_received,
            on_disconnect=self._handle_disconnect,
            delimiter=delimiter,
            inter_command_delay=delay,
        )

        self._connected = True
        self.set_state("connected", True)
        await self.events.emit(f"device.connected.{self.device_id}")
        log.info(f"[{self.device_id}] Connected to {host}:{port}")

    async def disconnect(self) -> None:
        """Disconnect from the device."""
        await self.stop_polling()
        if self.transport:
            await self.transport.close()
            self.transport = None
        self._connected = False
        self.set_state("connected", False)
        await self.events.emit(f"device.disconnected.{self.device_id}")
        log.info(f"[{self.device_id}] Disconnected")

    async def send_command(
        self, command: str, params: dict[str, Any] | None = None
    ) -> Any:
        """
        Send a named command. Looks up the raw string from the config's
        commands map and substitutes any parameters.
        """
        params = params or {}

        if not self.transport or not self.transport.connected:
            raise ConnectionError(f"[{self.device_id}] Not connected")

        commands = self.config.get("commands", {})
        raw_cmd = commands.get(command)

        if raw_cmd is None:
            log.warning(f"[{self.device_id}] Unknown command: {command}")
            return

        # Substitute parameters using safe substitution (unknown placeholders preserved)
        def _replace(m: re.Match) -> str:
            key = m.group(1)
            if key in params:
                return str(params[key])
            return m.group(0)

        formatted = re.sub(r"\{(\w+)\}", _replace, raw_cmd)

        # Encode and send (handle escape sequences like \r\n)
        data = formatted.encode().decode("unicode_escape").encode()
        await self.transport.send(data)
        log.debug(f"[{self.device_id}] Sent command '{command}': {data!r}")

    async def on_data_received(self, data: bytes) -> None:
        """Log received data and emit a response event."""
        text = data.decode("ascii", errors="replace")
        log.info(f"[{self.device_id}] Received: {text}")
        await self.events.emit(
            f"device.response.{self.device_id}",
            {"data": text, "raw": data.hex()},
        )

    def _handle_disconnect(self) -> None:
        """Called by the transport on connection loss."""
        self._connected = False
        self.set_state("connected", False)
        try:
            import asyncio

            loop = asyncio.get_running_loop()
            loop.create_task(
                self.events.emit(f"device.disconnected.{self.device_id}")
            )
        except RuntimeError:
            pass
