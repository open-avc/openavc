"""
OpenAVC BaseDriver — abstract base class for all device drivers.

Every device driver inherits from this class. A driver encapsulates all
knowledge of how to communicate with a specific piece of AV equipment.

Subclasses must:
    1. Set DRIVER_INFO with metadata, commands, state variables, config schema
    2. Implement send_command()
    3. Optionally override connect(), disconnect(), on_data_received(), poll()

Auto-transport: The default connect() reads DRIVER_INFO["transport"] and
self.config to create a TCP or serial transport automatically. Drivers with
custom connection logic (e.g., PJLink greeting handshake) can override
connect() as before.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Any

from server.core.event_bus import EventBus
from server.core.state_store import StateStore
from server.transport.frame_parsers import FrameParser
from server.utils.logger import get_logger

log = get_logger(__name__)


class BaseDriver(ABC):
    """Abstract base class for all device drivers."""

    # Subclasses MUST override this with their metadata dict
    DRIVER_INFO: dict[str, Any] = {}

    def __init__(
        self,
        device_id: str,
        config: dict[str, Any],
        state: StateStore,
        events: EventBus,
    ):
        self.device_id = device_id
        self.config = config
        self.state = state
        self.events = events
        self.transport: Any = None
        self._poll_task: asyncio.Task | None = None
        self._connected = False

        # Initialize state variables from DRIVER_INFO
        self._init_state_variables()

    @property
    def connected(self) -> bool:
        """True only if both driver and transport report connected."""
        if not self._connected:
            return False
        if self.transport is None:
            return False
        return getattr(self.transport, "connected", False)

    def _init_state_variables(self) -> None:
        """Register all state variables from DRIVER_INFO with default values."""
        state_vars = self.DRIVER_INFO.get("state_variables", {})
        for prop_name, prop_info in state_vars.items():
            var_type = prop_info.get("type", "string")
            if var_type == "boolean":
                default = False
            elif var_type == "integer":
                default = 0
            else:
                default = None
            self.set_state(prop_name, default)
        # Always set a connected state
        self.set_state("connected", False)

    # --- Connection lifecycle (concrete with auto-transport) ---

    async def connect(self) -> None:
        """
        Establish connection to the device using auto-transport.

        Reads DRIVER_INFO["transport"] and self.config to create the
        appropriate transport (TCP or serial). Override this method for
        custom connection logic (e.g., greeting handshakes).
        """
        transport_type = self.DRIVER_INFO.get("transport", "tcp")
        frame_parser = self._create_frame_parser()
        delimiter = self._resolve_delimiter()

        if transport_type == "tcp":
            from server.transport.tcp import TCPTransport

            host = self.config.get("host", "")
            port = self.config.get("port", 23)
            delay = self.config.get("inter_command_delay", 0.0)

            self.transport = await TCPTransport.create(
                host=host,
                port=port,
                on_data=self.on_data_received,
                on_disconnect=self._handle_transport_disconnect,
                delimiter=delimiter,
                frame_parser=frame_parser,
                inter_command_delay=delay,
                name=self.device_id,
            )
        elif transport_type == "serial":
            from server.transport.serial_transport import SerialTransport

            serial_port = self.config.get("port", "")
            baudrate = self.config.get("baudrate", 9600)
            delay = self.config.get("inter_command_delay", 0.0)
            bytesize = self.config.get("bytesize", 8)
            parity = self.config.get("parity", "N")
            stopbits = self.config.get("stopbits", 1)

            self.transport = await SerialTransport.create(
                port=serial_port,
                baudrate=baudrate,
                on_data=self.on_data_received,
                on_disconnect=self._handle_transport_disconnect,
                delimiter=delimiter,
                frame_parser=frame_parser,
                inter_command_delay=delay,
                bytesize=bytesize,
                parity=parity,
                stopbits=stopbits,
                name=self.device_id,
            )
        elif transport_type == "http":
            from server.transport.http_client import HTTPClientTransport

            host = self.config.get("host", "")
            port = self.config.get("port", 80)
            use_ssl = self.config.get("ssl", False)
            scheme = "https" if use_ssl else "http"
            # Default port: 443 for HTTPS, 80 for HTTP
            if use_ssl and port == 80:
                port = 443
            base_url = f"{scheme}://{host}:{port}"

            # Build credentials from config
            auth_type = self.config.get("auth_type", "none")
            credentials = {}
            if auth_type in ("basic", "digest"):
                credentials["username"] = self.config.get("username", "")
                credentials["password"] = self.config.get("password", "")
            elif auth_type == "bearer":
                credentials["token"] = self.config.get("token", "")
            elif auth_type == "api_key":
                credentials["header"] = self.config.get("api_key_header", "X-API-Key")
                credentials["key"] = self.config.get("api_key", "")

            self.transport = HTTPClientTransport(
                base_url=base_url,
                auth_type=auth_type,
                credentials=credentials,
                verify_ssl=self.config.get("verify_ssl", True),
                default_headers=self.config.get("default_headers", {}),
                timeout=self.config.get("timeout", 10.0),
                name=self.device_id,
            )
            await self.transport.open()
        else:
            raise ValueError(f"Unsupported transport type: {transport_type}")

        try:
            self._connected = True
            self.set_state("connected", True)
            await self.events.emit(f"device.connected.{self.device_id}")
            log.info(f"[{self.device_id}] Connected via {transport_type}")
        except Exception:
            # Clean up transport if post-connect setup fails
            if self.transport:
                await self.transport.close()
                self.transport = None
            self._connected = False
            raise

        # Start polling if configured
        poll_interval = self.config.get("poll_interval", 0)
        if poll_interval > 0:
            await self.start_polling(poll_interval)

    async def disconnect(self) -> None:
        """
        Gracefully close the connection.

        Stops polling, closes transport, and updates state.
        Override for custom disconnect logic.
        """
        await self.stop_polling()
        if self.transport:
            await self.transport.close()
            self.transport = None
        self._connected = False
        self.set_state("connected", False)
        await self.events.emit(f"device.disconnected.{self.device_id}")
        log.info(f"[{self.device_id}] Disconnected")

    # --- Abstract: drivers must implement ---

    @abstractmethod
    async def send_command(self, command: str, params: dict[str, Any] | None = None) -> Any:
        """
        Execute a named command.

        Translates the command name + params into protocol-specific bytes
        and sends via the transport.
        """

    # --- Device Settings ---

    async def set_device_setting(self, key: str, value: Any) -> Any:
        """
        Write a device setting value to the device.

        Override in subclasses to handle device-specific write logic.
        The default implementation raises NotImplementedError so callers
        know the driver hasn't implemented settings writes.

        Args:
            key: The setting key from DRIVER_INFO["device_settings"].
            value: The new value to write.

        Returns:
            Result of the write operation (driver-specific).
        """
        raise NotImplementedError(
            f"Driver {self.DRIVER_INFO.get('id', '?')} does not implement set_device_setting"
        )

    # --- Optional overrides ---

    async def on_data_received(self, data: bytes) -> None:
        """
        Called by the transport when data arrives from the device.

        Override in the driver to implement protocol-specific parsing.
        Default: no-op.
        """

    async def poll(self) -> None:
        """
        Called periodically to request device status.

        Override to send status query commands. Default: no-op.
        """

    def _create_frame_parser(self) -> FrameParser | None:
        """
        Hook: return a custom FrameParser for this driver.

        Override for binary protocols that need length-prefix or callable
        parsing. Return None to use delimiter-based framing (the default).
        """
        return None

    def _resolve_delimiter(self) -> bytes | None:
        """
        Hook: resolve the message delimiter for this driver.

        Checks DRIVER_INFO["delimiter"] first, then self.config["delimiter"],
        then falls back to b"\\r". Override for custom logic.
        """
        from server.transport.binary_helpers import encode_escape_sequences

        # Check DRIVER_INFO
        delim = self.DRIVER_INFO.get("delimiter")
        if delim is not None:
            if isinstance(delim, bytes):
                return delim
            return encode_escape_sequences(delim)

        # Check config
        delim = self.config.get("delimiter")
        if delim is not None:
            if isinstance(delim, bytes):
                return delim
            return encode_escape_sequences(delim)

        # Default
        return b"\r"

    def _handle_transport_disconnect(self) -> None:
        """
        Standard disconnect handler for transport callbacks.

        Sets connected state to False and emits disconnect event.
        Override for custom disconnect behavior.
        """
        self._connected = False
        self.set_state("connected", False)
        log.warning(f"[{self.device_id}] Connection lost")
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.stop_polling())
            loop.create_task(
                self.events.emit(f"device.disconnected.{self.device_id}")
            )
        except RuntimeError:
            log.warning(
                f"[{self.device_id}] No event loop during disconnect — "
                f"polling may not stop cleanly"
            )

    # --- Polling ---

    async def start_polling(self, interval: float) -> None:
        """Start a background polling loop at the given interval (seconds)."""
        if interval <= 0:
            return
        await self.stop_polling()
        self._poll_task = asyncio.create_task(self._poll_loop(interval))
        log.debug(f"[{self.device_id}] Polling started (every {interval}s)")

    async def stop_polling(self) -> None:
        """Cancel the polling background task."""
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None
            log.debug(f"[{self.device_id}] Polling stopped")

    async def _poll_loop(self, interval: float) -> None:
        """Background loop that calls self.poll() periodically."""
        try:
            while True:
                try:
                    await self.poll()
                except (ConnectionError, TimeoutError, OSError):
                    # Expected during network issues — log briefly and retry next cycle
                    log.warning(f"[{self.device_id}] Poll failed (connection issue)")
                except Exception:
                    # Unexpected errors — log full traceback so driver bugs are visible
                    log.exception(f"[{self.device_id}] Unexpected error during poll")
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            return

    # --- Convenience helpers ---

    def set_state(self, property_name: str, value: Any) -> None:
        """Set a state value under this device's namespace."""
        # Warn if value type doesn't match declaration (helps catch driver bugs)
        var_def = self.DRIVER_INFO.get("state_variables", {}).get(property_name)
        if var_def and value is not None:
            declared = var_def.get("type", "string")
            if declared == "integer" and not isinstance(value, int):
                log.debug(
                    f"[{self.device_id}] State '{property_name}' declared as "
                    f"integer but got {type(value).__name__}: {value!r}"
                )
            elif declared == "boolean" and not isinstance(value, bool):
                log.debug(
                    f"[{self.device_id}] State '{property_name}' declared as "
                    f"boolean but got {type(value).__name__}: {value!r}"
                )
            elif declared == "enum" and "values" in var_def:
                if str(value) not in [str(v) for v in var_def["values"]]:
                    log.debug(
                        f"[{self.device_id}] State '{property_name}' value "
                        f"{value!r} not in declared enum values {var_def['values']}"
                    )
        self.state.set(
            f"device.{self.device_id}.{property_name}",
            value,
            source=f"device.{self.device_id}",
        )

    def set_states(self, updates: dict[str, Any]) -> None:
        """Set multiple state values atomically (listeners see all changes at once)."""
        namespaced = {
            f"device.{self.device_id}.{k}": v for k, v in updates.items()
        }
        self.state.set_batch(namespaced, source=f"device.{self.device_id}")

    def get_state(self, property_name: str) -> Any:
        """Get a state value from this device's namespace."""
        return self.state.get(f"device.{self.device_id}.{property_name}")
