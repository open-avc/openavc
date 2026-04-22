export interface DriverTemplate {
  id: string;
  name: string;
  description: string;
  transport: string;
  generateCode: (info: {
    id: string;
    name: string;
    manufacturer: string;
    category: string;
    transport: string;
  }) => string;
}

export const DRIVER_TEMPLATES: DriverTemplate[] = [
  {
    id: "tcp",
    name: "TCP Device",
    description: "Delimiter-based text protocol (most AV gear)",
    transport: "tcp",
    generateCode: (info) => `"""${info.name} driver for OpenAVC."""
import asyncio
from typing import Any

from server.drivers.base import BaseDriver
from server.utils.logger import get_logger

log = get_logger(__name__)


# --- Input mapping (customize for your device) ---
# Map friendly names to protocol codes and back
# INPUT_MAP = {
#     "hdmi1": "31", "hdmi2": "32", "hdmi3": "33",
#     "vga1": "11", "vga2": "12",
#     "dvi": "41",
# }
# INPUT_REVERSE = {v: k for k, v in INPUT_MAP.items()}


class ${classNameFrom(info.id)}(BaseDriver):
    """Driver for ${info.name}."""

    DRIVER_INFO = {
        "id": "${info.id}",
        "name": "${info.name}",
        "manufacturer": "${info.manufacturer}",
        "category": "${info.category}",
        "version": "1.0.0",
        "description": "Control ${info.name} via TCP.",
        "transport": "tcp",
        "delimiter": "\\r\\n",
        "default_config": {
            "host": "",
            "port": 23,
            "poll_interval": 15,
        },
        "config_schema": {
            "host": {"type": "string", "label": "IP Address", "required": True},
            "port": {"type": "integer", "label": "Port", "default": 23},
            # "password": {
            #     "type": "string",
            #     "label": "Password",
            #     "default": "",
            #     "secret": True,  # Masked in UI
            # },
            "poll_interval": {
                "type": "integer",
                "label": "Poll Interval (s)",
                "default": 15,
                "min": 0,  # 0 = disabled
            },
        },
        "state_variables": {
            "power": {
                "type": "enum",
                "values": ["off", "on", "warming", "cooling"],
                "label": "Power State",
                "help": "Current power state of the device.",
            },
            "input": {
                "type": "string",
                "label": "Current Input",
            },
            "volume": {
                "type": "integer",
                "label": "Volume",
            },
            "mute": {
                "type": "boolean",
                "label": "Audio Mute",
            },
            # "error_status": {
            #     "type": "string",
            #     "label": "Error Status",
            # },
            # "model": {
            #     "type": "string",
            #     "label": "Model Name",
            # },
        },
        "commands": {
            "power_on": {
                "label": "Power On",
                "help": "Turn on the device.",
            },
            "power_off": {
                "label": "Power Off",
                "help": "Turn off the device.",
            },
            "set_input": {
                "label": "Set Input",
                "params": {
                    "input": {
                        "type": "string",
                        "required": True,
                        "help": "Input source name (e.g. hdmi1, vga1).",
                    },
                },
                "help": "Switch the active input source.",
            },
            "set_volume": {
                "label": "Set Volume",
                "params": {
                    "level": {
                        "type": "integer",
                        "required": True,
                        "min": 0,
                        "max": 100,
                        "help": "Volume level 0-100.",
                    },
                },
                "help": "Set the volume level.",
            },
            "mute_on": {"label": "Mute On"},
            "mute_off": {"label": "Mute Off"},
        },
        "help": {
            "overview": (
                "Controls ${info.name} via TCP. "
                "Replace the command strings and response parsing below "
                "with values from the device's protocol documentation."
            ),
            "setup": (
                "1. Enable network control on the device\\n"
                "2. Assign a static IP address\\n"
                "3. Note the control port (often 23, 4352, 1515, or 9090)\\n"
                "4. If the device requires a password, enter it in the config"
            ),
        },
        # "discovery": {
        #     "ports": [23],  # Ports to scan during device discovery
        #     # "mac_prefixes": ["00:01:2e"],  # OUI for manufacturer
        # },
        # "device_settings": {
        #     "device_name": {
        #         "type": "string",
        #         "label": "Device Name",
        #         "help": "The name stored on the device hardware.",
        #         "state_key": "device_name",
        #         "default": "",
        #     },
        # },
    }

    # --- Uncomment to add instance variables ---
    # def __init__(self, device_id: str, config: dict, state: Any, events: Any):
    #     self._auth_prefix = ""
    #     super().__init__(device_id, config, state, events)

    # --- Uncomment for custom connection logic ---
    # async def connect(self) -> None:
    #     """Custom connect with post-connection setup."""
    #     await super().connect()  # Creates transport, starts polling
    #     # Query device info after connecting
    #     # await self._query_device_info()

    # --- Uncomment for custom disconnect cleanup ---
    # async def disconnect(self) -> None:
    #     """Clean up resources on disconnect."""
    #     await self.stop_polling()
    #     if self.transport:
    #         await self.transport.close()
    #         self.transport = None
    #     self._connected = False
    #     self.set_state("connected", False)
    #     await self.events.emit(f"device.disconnected.{self.device_id}")

    async def send_command(self, command: str, params: dict | None = None) -> None:
        """Send a command to the device.

        Replace the command strings below with actual protocol commands
        from the device's documentation.
        """
        params = params or {}

        match command:
            case "power_on":
                await self._send("POWER ON")
            case "power_off":
                await self._send("POWER OFF")
            case "set_input":
                inp = params.get("input", "")
                # code = INPUT_MAP.get(inp, inp)  # Map friendly name to code
                await self._send(f"INPUT {inp}")
            case "set_volume":
                level = params.get("level", 0)
                await self._send(f"VOLUME {level}")
            case "mute_on":
                await self._send("MUTE ON")
            case "mute_off":
                await self._send("MUTE OFF")
            case _:
                log.warning(f"[{self.device_id}] Unknown command: {command}")

    async def on_data_received(self, data: bytes) -> None:
        """Parse incoming data from the device.

        Called automatically when the device sends data. Each call
        contains one delimited message (split by the delimiter in DRIVER_INFO).
        """
        text = data.decode("ascii", errors="replace").strip()
        if not text:
            return

        log.debug(f"[{self.device_id}] Received: {text}")

        # Acknowledgement responses
        if text.endswith("OK"):
            return

        # Error responses
        if "ERR" in text:
            log.warning(f"[{self.device_id}] Device error: {text}")
            return

        # --- Parse responses and update state ---
        # Replace these patterns with your device's actual response format.
        # Common formats:
        #   KEY=VALUE (e.g., "PWR=ON")
        #   KEY VALUE (e.g., "VOLUME 65")
        #   PREFIX.KEY=VALUE (e.g., "%1POWR=01")

        if "=" in text:
            key, _, value = text.partition("=")
            key = key.strip().upper()
            value = value.strip()

            if key == "PWR" or key == "POWER":
                # Map device values to state enum values
                power_map = {"ON": "on", "OFF": "off", "1": "on", "0": "off"}
                power = power_map.get(value.upper(), value.lower())
                old = self.get_state("power")
                self.set_state("power", power)
                if power != old:
                    log.info(f"[{self.device_id}] Power: {power}")

            elif key == "INP" or key == "INPUT":
                # input_name = INPUT_REVERSE.get(value, value)
                self.set_state("input", value)

            elif key == "VOL" or key == "VOLUME":
                try:
                    self.set_state("volume", int(value))
                except ValueError:
                    pass

            elif key == "MUTE":
                self.set_state("mute", value.upper() in ("ON", "1", "TRUE"))

    async def poll(self) -> None:
        """Query device for current status.

        Called automatically at the poll_interval. Responses arrive
        in on_data_received().
        """
        if not self.transport or not self.transport.connected:
            return

        try:
            # Always query power
            await self._send("GET POWER")
            await asyncio.sleep(0.2)  # Inter-command delay

            # Only query other states when powered on
            if self.get_state("power") == "on":
                await self._send("GET INPUT")
                await asyncio.sleep(0.2)
                await self._send("GET VOLUME")
                await asyncio.sleep(0.2)
                await self._send("GET MUTE")
        except ConnectionError:
            log.warning(f"[{self.device_id}] Poll failed - not connected")

    # --- Uncomment for writable hardware settings ---
    # async def set_device_setting(self, key: str, value: Any) -> Any:
    #     """Write a setting to the device hardware."""
    #     match key:
    #         case "device_name":
    #             await self._send(f"SET NAME {value}")
    #             self.set_state("device_name", str(value))
    #         case _:
    #             raise ValueError(f"Unknown setting: {key}")

    async def _send(self, cmd: str) -> None:
        """Send a command string with the protocol delimiter."""
        if not self.transport or not self.transport.connected:
            raise ConnectionError(f"[{self.device_id}] Not connected")
        await self.transport.send(f"{cmd}\\r\\n".encode("ascii"))
`,
  },
  {
    id: "http",
    name: "HTTP/REST Device",
    description: "HTTP API polling driver",
    transport: "http",
    generateCode: (info) => `"""${info.name} driver for OpenAVC."""
import asyncio
from typing import Any

import httpx

from server.drivers.base import BaseDriver
from server.utils.logger import get_logger

log = get_logger(__name__)


class ${classNameFrom(info.id)}(BaseDriver):
    """Driver for ${info.name} via HTTP/REST API."""

    DRIVER_INFO = {
        "id": "${info.id}",
        "name": "${info.name}",
        "manufacturer": "${info.manufacturer}",
        "category": "${info.category}",
        "version": "1.0.0",
        "description": "Control ${info.name} via HTTP/REST API.",
        "transport": "http",
        "default_config": {
            "host": "",
            "port": 80,
            "poll_interval": 10,
        },
        "config_schema": {
            "host": {"type": "string", "label": "IP Address", "required": True},
            "port": {"type": "integer", "label": "Port", "default": 80},
            # "api_key": {
            #     "type": "string",
            #     "label": "API Key",
            #     "default": "",
            #     "secret": True,
            # },
            "poll_interval": {
                "type": "integer",
                "label": "Poll Interval (s)",
                "default": 10,
                "min": 0,
            },
        },
        "state_variables": {
            "power": {
                "type": "enum",
                "values": ["off", "on"],
                "label": "Power State",
            },
            "volume": {
                "type": "integer",
                "label": "Volume",
            },
            "mute": {
                "type": "boolean",
                "label": "Audio Mute",
            },
            "input": {
                "type": "string",
                "label": "Current Input",
            },
            # "model": {
            #     "type": "string",
            #     "label": "Model Name",
            # },
        },
        "commands": {
            "power_on": {
                "label": "Power On",
                "help": "Turn on the device.",
            },
            "power_off": {
                "label": "Power Off",
                "help": "Turn off the device.",
            },
            "set_volume": {
                "label": "Set Volume",
                "params": {
                    "level": {
                        "type": "integer",
                        "required": True,
                        "min": 0,
                        "max": 100,
                    },
                },
            },
            "set_input": {
                "label": "Set Input",
                "params": {
                    "input": {"type": "string", "required": True},
                },
            },
            "mute_on": {"label": "Mute On"},
            "mute_off": {"label": "Mute Off"},
        },
        "help": {
            "overview": (
                "Controls ${info.name} via its HTTP REST API. "
                "Replace the API paths and JSON payloads below with values "
                "from the device's API documentation."
            ),
            "setup": (
                "1. Connect the device to the network\\n"
                "2. Enable the REST/HTTP API in device settings\\n"
                "3. Note the API port and any authentication keys\\n"
                "4. Enter the IP address and port in the device config"
            ),
        },
        # "discovery": {
        #     "ports": [80, 8080],
        # },
    }

    def __init__(self, device_id: str, config: dict, state: Any, events: Any):
        self._client: httpx.AsyncClient | None = None
        self._base_url = ""
        super().__init__(device_id, config, state, events)

    async def connect(self) -> None:
        """Set up the HTTP client and verify the connection."""
        host = self.config.get("host", "")
        port = self.config.get("port", 80)
        scheme = "https" if port == 443 else "http"
        self._base_url = f"{scheme}://{host}:{port}"

        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=10.0,
            # headers={"Authorization": f"Bearer {self.config.get('api_key', '')}"},
        )

        # Verify connection with a simple request
        try:
            resp = await self._client.get("/api/status")
            if resp.status_code >= 400:
                raise ConnectionError(f"HTTP {resp.status_code}")
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            if self._client:
                await self._client.aclose()
                self._client = None
            raise ConnectionError(f"Cannot reach {self._base_url}: {e}")

        self._connected = True
        self.set_state("connected", True)
        await self.events.emit(f"device.connected.{self.device_id}")
        log.info(f"[{self.device_id}] Connected to {self._base_url}")

        # Fetch initial status
        await self.poll()

        # Start polling
        poll_interval = self.config.get("poll_interval", 10)
        if poll_interval > 0:
            await self.start_polling(poll_interval)

    async def disconnect(self) -> None:
        """Close the HTTP client."""
        await self.stop_polling()
        if self._client:
            await self._client.aclose()
            self._client = None
        self._connected = False
        self.set_state("connected", False)
        await self.events.emit(f"device.disconnected.{self.device_id}")
        log.info(f"[{self.device_id}] Disconnected")

    async def send_command(self, command: str, params: dict | None = None) -> None:
        """Send a command to the device via HTTP.

        Replace the API paths and payloads below with values from
        the device's API documentation.
        """
        params = params or {}

        match command:
            case "power_on":
                await self._api_post("/api/power", {"power": "on"})
            case "power_off":
                await self._api_post("/api/power", {"power": "off"})
            case "set_volume":
                level = params.get("level", 0)
                await self._api_post("/api/audio/volume", {"level": level})
            case "set_input":
                inp = params.get("input", "")
                await self._api_post("/api/input", {"input": inp})
            case "mute_on":
                await self._api_post("/api/audio/mute", {"mute": True})
            case "mute_off":
                await self._api_post("/api/audio/mute", {"mute": False})
            case _:
                log.warning(f"[{self.device_id}] Unknown command: {command}")

    async def poll(self) -> None:
        """Query device status via HTTP."""
        data = await self._api_get("/api/status")
        if not data:
            return

        # Parse the status response and update state
        # Replace these with the actual JSON structure from your device
        if "power" in data:
            self.set_state("power", "on" if data["power"] else "off")
        if "volume" in data:
            self.set_state("volume", int(data["volume"]))
        if "mute" in data:
            self.set_state("mute", bool(data["mute"]))
        if "input" in data:
            self.set_state("input", str(data["input"]))

    # --- HTTP helpers ---

    async def _api_get(self, path: str) -> dict | None:
        """Send a GET request and return the JSON response."""
        if not self._client:
            return None
        try:
            resp = await self._client.get(path)
            if resp.status_code == 200:
                return resp.json()
            log.warning(f"[{self.device_id}] GET {path}: HTTP {resp.status_code}")
            return None
        except (httpx.TimeoutException, httpx.ConnectError):
            return None
        except Exception as e:
            log.warning(f"[{self.device_id}] GET {path} error: {e}")
            return None

    async def _api_post(self, path: str, data: dict | None = None) -> dict | None:
        """Send a POST request with JSON body."""
        if not self._client:
            raise ConnectionError(f"[{self.device_id}] Not connected")
        try:
            resp = await self._client.post(path, json=data)
            if resp.status_code < 400:
                try:
                    return resp.json()
                except Exception:
                    return None
            log.warning(f"[{self.device_id}] POST {path}: HTTP {resp.status_code}")
            return None
        except (httpx.TimeoutException, httpx.ConnectError) as e:
            raise ConnectionError(f"[{self.device_id}] POST {path} failed: {e}")
`,
  },
  {
    id: "serial",
    name: "Serial Device",
    description: "RS-232/RS-485 with baud rate config",
    transport: "serial",
    generateCode: (info) => `"""${info.name} driver for OpenAVC."""
import asyncio
from typing import Any

from server.drivers.base import BaseDriver
from server.utils.logger import get_logger

log = get_logger(__name__)


class ${classNameFrom(info.id)}(BaseDriver):
    """Driver for ${info.name} via serial port."""

    DRIVER_INFO = {
        "id": "${info.id}",
        "name": "${info.name}",
        "manufacturer": "${info.manufacturer}",
        "category": "${info.category}",
        "version": "1.0.0",
        "description": "Control ${info.name} via RS-232/RS-485.",
        "transport": "serial",
        "delimiter": "\\r",
        "default_config": {
            "com_port": "",
            "baud_rate": 9600,
            "data_bits": 8,
            "parity": "N",
            "stop_bits": 1,
            "poll_interval": 15,
        },
        "config_schema": {
            "com_port": {"type": "string", "label": "COM Port", "required": True},
            "baud_rate": {
                "type": "integer",
                "label": "Baud Rate",
                "default": 9600,
            },
            "poll_interval": {
                "type": "integer",
                "label": "Poll Interval (s)",
                "default": 15,
                "min": 0,
            },
        },
        "state_variables": {
            "power": {
                "type": "enum",
                "values": ["off", "on"],
                "label": "Power State",
            },
            "input": {
                "type": "string",
                "label": "Current Input",
            },
            "volume": {
                "type": "integer",
                "label": "Volume",
            },
            "mute": {
                "type": "boolean",
                "label": "Audio Mute",
            },
        },
        "commands": {
            "power_on": {
                "label": "Power On",
                "help": "Turn on the device.",
            },
            "power_off": {
                "label": "Power Off",
                "help": "Turn off the device.",
            },
            "set_input": {
                "label": "Set Input",
                "params": {
                    "input": {"type": "string", "required": True},
                },
            },
            "set_volume": {
                "label": "Set Volume",
                "params": {
                    "level": {
                        "type": "integer",
                        "required": True,
                        "min": 0,
                        "max": 100,
                    },
                },
            },
            "mute_on": {"label": "Mute On"},
            "mute_off": {"label": "Mute Off"},
        },
        "help": {
            "overview": (
                "Controls ${info.name} via RS-232 serial. "
                "Replace the command strings and response parsing below "
                "with values from the device's RS-232 protocol manual."
            ),
            "setup": (
                "1. Connect a serial cable (straight or null-modem per device docs)\\n"
                "2. Note the baud rate, data bits, parity, and stop bits\\n"
                "3. On Windows, find the COM port in Device Manager\\n"
                "4. On Linux, the port is typically /dev/ttyUSB0 or /dev/ttyS0"
            ),
        },
    }

    async def send_command(self, command: str, params: dict | None = None) -> None:
        """Send a command to the device via serial.

        Replace the command strings below with actual protocol commands
        from the device's RS-232 manual.
        """
        params = params or {}

        match command:
            case "power_on":
                await self._send("POWER ON")
            case "power_off":
                await self._send("POWER OFF")
            case "set_input":
                inp = params.get("input", "")
                await self._send(f"INPUT {inp}")
            case "set_volume":
                level = params.get("level", 0)
                await self._send(f"VOLUME {level}")
            case "mute_on":
                await self._send("MUTE ON")
            case "mute_off":
                await self._send("MUTE OFF")
            case _:
                log.warning(f"[{self.device_id}] Unknown command: {command}")

    async def on_data_received(self, data: bytes) -> None:
        """Parse incoming serial data.

        Called for each delimited message from the device.
        """
        text = data.decode("ascii", errors="replace").strip()
        if not text:
            return

        log.debug(f"[{self.device_id}] Received: {text}")

        # Acknowledgement
        if text.endswith("OK"):
            return

        # Error
        if "ERR" in text:
            log.warning(f"[{self.device_id}] Device error: {text}")
            return

        # Parse responses (replace with your device's format)
        if "=" in text:
            key, _, value = text.partition("=")
            key = key.strip().upper()
            value = value.strip()

            if key == "POWER":
                self.set_state("power", "on" if value.upper() in ("ON", "1") else "off")
            elif key == "INPUT":
                self.set_state("input", value)
            elif key == "VOLUME":
                try:
                    self.set_state("volume", int(value))
                except ValueError:
                    pass
            elif key == "MUTE":
                self.set_state("mute", value.upper() in ("ON", "1", "TRUE"))

    async def poll(self) -> None:
        """Query device status periodically."""
        if not self.transport or not self.transport.connected:
            return
        try:
            await self._send("GET POWER")
            await asyncio.sleep(0.2)
            if self.get_state("power") == "on":
                await self._send("GET INPUT")
                await asyncio.sleep(0.2)
                await self._send("GET VOLUME")
                await asyncio.sleep(0.2)
                await self._send("GET MUTE")
        except ConnectionError:
            log.warning(f"[{self.device_id}] Poll failed - not connected")

    async def _send(self, cmd: str) -> None:
        """Send a command string with the serial delimiter."""
        if not self.transport or not self.transport.connected:
            raise ConnectionError(f"[{self.device_id}] Not connected")
        await self.transport.send(f"{cmd}\\r".encode("ascii"))
`,
  },
  {
    id: "polling",
    name: "Polling Device",
    description: "TCP device with periodic status queries",
    transport: "tcp",
    generateCode: (info) => `"""${info.name} driver for OpenAVC."""
import asyncio
from typing import Any

from server.drivers.base import BaseDriver
from server.utils.logger import get_logger

log = get_logger(__name__)


class ${classNameFrom(info.id)}(BaseDriver):
    """Driver for ${info.name} with periodic polling.

    This template is for devices where you send a query command and
    then parse the multi-line or structured response. Good for devices
    that don't send unsolicited status updates.
    """

    DRIVER_INFO = {
        "id": "${info.id}",
        "name": "${info.name}",
        "manufacturer": "${info.manufacturer}",
        "category": "${info.category}",
        "version": "1.0.0",
        "description": "Control ${info.name} via TCP with status polling.",
        "transport": "tcp",
        "delimiter": "\\r\\n",
        "default_config": {
            "host": "",
            "port": 23,
            "poll_interval": 10,
        },
        "config_schema": {
            "host": {"type": "string", "label": "IP Address", "required": True},
            "port": {"type": "integer", "label": "Port", "default": 23},
            "poll_interval": {
                "type": "integer",
                "label": "Poll Interval (s)",
                "default": 10,
                "min": 1,
            },
        },
        "state_variables": {
            "power": {
                "type": "enum",
                "values": ["off", "on", "warming", "cooling"],
                "label": "Power State",
            },
            "input": {
                "type": "string",
                "label": "Current Input",
            },
            "volume": {
                "type": "integer",
                "label": "Volume",
            },
            "mute": {
                "type": "boolean",
                "label": "Audio Mute",
            },
            # "hours": {
            #     "type": "integer",
            #     "label": "Usage Hours",
            # },
            # "error_status": {
            #     "type": "string",
            #     "label": "Error Status",
            # },
        },
        "commands": {
            "power_on": {
                "label": "Power On",
                "help": "Turn on the device. May enter a warming state.",
            },
            "power_off": {
                "label": "Power Off",
                "help": "Turn off the device. May enter a cooling state.",
            },
            "set_input": {
                "label": "Set Input",
                "params": {
                    "input": {
                        "type": "string",
                        "required": True,
                        "help": "Input name (e.g. hdmi1, vga1, dvi).",
                    },
                },
            },
            "set_volume": {
                "label": "Set Volume",
                "params": {
                    "level": {
                        "type": "integer",
                        "required": True,
                        "min": 0,
                        "max": 100,
                    },
                },
            },
            "mute_on": {"label": "Mute On"},
            "mute_off": {"label": "Mute Off"},
        },
        "help": {
            "overview": (
                "Controls ${info.name} via TCP with periodic status polling. "
                "The driver queries the device at regular intervals and parses "
                "the responses to keep state current."
            ),
            "setup": (
                "1. Enable network control on the device\\n"
                "2. Assign a static IP address\\n"
                "3. Note the control port\\n"
                "4. Set the poll interval (how often to query status)"
            ),
        },
    }

    async def send_command(self, command: str, params: dict | None = None) -> None:
        """Send a command to the device."""
        params = params or {}

        match command:
            case "power_on":
                await self._send("POWER ON")
            case "power_off":
                await self._send("POWER OFF")
            case "set_input":
                inp = params.get("input", "")
                await self._send(f"INPUT {inp}")
            case "set_volume":
                level = params.get("level", 0)
                await self._send(f"VOLUME {level}")
            case "mute_on":
                await self._send("MUTE ON")
            case "mute_off":
                await self._send("MUTE OFF")
            case _:
                log.warning(f"[{self.device_id}] Unknown command: {command}")

    async def on_data_received(self, data: bytes) -> None:
        """Parse incoming data from the device."""
        text = data.decode("ascii", errors="replace").strip()
        if not text:
            return

        log.debug(f"[{self.device_id}] Received: {text}")

        if text.endswith("OK"):
            return

        if "ERR" in text:
            log.warning(f"[{self.device_id}] Device error: {text}")
            return

        # Parse KEY=VALUE responses
        if "=" in text:
            key, _, value = text.partition("=")
            key = key.strip().upper()
            value = value.strip()

            if key == "POWER":
                power_map = {"ON": "on", "OFF": "off", "1": "on", "0": "off"}
                power = power_map.get(value.upper(), value.lower())
                old = self.get_state("power")
                self.set_state("power", power)
                if power != old:
                    log.info(f"[{self.device_id}] Power: {power}")
            elif key == "INPUT":
                self.set_state("input", value)
            elif key == "VOLUME":
                try:
                    self.set_state("volume", int(value))
                except ValueError:
                    pass
            elif key == "MUTE":
                self.set_state("mute", value.upper() in ("ON", "1", "TRUE"))

    async def poll(self) -> None:
        """Query device for current status.

        Responses arrive in on_data_received(). Add inter-command delays
        to avoid overwhelming devices that can't handle rapid queries.
        """
        if not self.transport or not self.transport.connected:
            return

        try:
            # Always query power
            await self._send("GET POWER")
            await asyncio.sleep(0.2)

            # Only query other states when powered on
            power = self.get_state("power")
            if power == "on":
                await self._send("GET INPUT")
                await asyncio.sleep(0.2)
                await self._send("GET VOLUME")
                await asyncio.sleep(0.2)
                await self._send("GET MUTE")

            # These can be queried regardless of power state
            # await self._send("GET HOURS")
            # await asyncio.sleep(0.2)
            # await self._send("GET ERRORS")
        except ConnectionError:
            log.warning(f"[{self.device_id}] Poll failed - not connected")

    async def _send(self, cmd: str) -> None:
        """Send a command string with the protocol delimiter."""
        if not self.transport or not self.transport.connected:
            raise ConnectionError(f"[{self.device_id}] Not connected")
        await self.transport.send(f"{cmd}\\r\\n".encode("ascii"))
`,
  },
  {
    id: "minimal",
    name: "Minimal",
    description: "Bare BaseDriver with all extension points documented",
    transport: "tcp",
    generateCode: (info) => `"""${info.name} driver for OpenAVC.

Minimal template with all available extension points shown as comments.
Uncomment what you need.
"""
import asyncio
from typing import Any

from server.drivers.base import BaseDriver
# from server.transport.frame_parsers import CallableFrameParser, FrameParser
from server.utils.logger import get_logger

log = get_logger(__name__)


class ${classNameFrom(info.id)}(BaseDriver):
    """Driver for ${info.name}."""

    DRIVER_INFO = {
        "id": "${info.id}",
        "name": "${info.name}",
        "manufacturer": "${info.manufacturer}",
        "category": "${info.category}",
        "version": "1.0.0",
        "transport": "${info.transport}",
        "default_config": {
            "host": "",
            "port": 23,
        },
        "config_schema": {
            "host": {"type": "string", "label": "IP Address", "required": True},
            "port": {"type": "integer", "label": "Port", "default": 23},
        },
        "state_variables": {},
        "commands": {},
        # "help": {
        #     "overview": "Description of what this driver controls.",
        #     "setup": "Step-by-step setup instructions.",
        # },
        # "discovery": {
        #     "ports": [23],
        #     # "mac_prefixes": ["00:01:2e"],
        # },
        # "device_settings": {
        #     "setting_name": {
        #         "type": "string",
        #         "label": "Setting Label",
        #         "state_key": "setting_name",
        #         "default": "",
        #     },
        # },
    }

    # --- Extension Points (uncomment as needed) ---

    # def __init__(self, device_id: str, config: dict, state: Any, events: Any):
    #     self._my_var = None  # Custom instance variables
    #     super().__init__(device_id, config, state, events)

    # async def connect(self) -> None:
    #     """Custom connection logic."""
    #     await super().connect()
    #     # Post-connection setup (handshakes, device queries, etc.)

    # async def disconnect(self) -> None:
    #     """Custom disconnect with resource cleanup."""
    #     await self.stop_polling()
    #     if self.transport:
    #         await self.transport.close()
    #         self.transport = None
    #     self._connected = False
    #     self.set_state("connected", False)
    #     await self.events.emit(f"device.disconnected.{self.device_id}")

    async def send_command(self, command: str, params: dict | None = None) -> None:
        """Send a command to the device."""
        params = params or {}
        log.info(f"[{self.device_id}] Command: {command}, params: {params}")

    # async def on_data_received(self, data: bytes) -> None:
    #     """Parse incoming data from the device."""
    #     text = data.decode("ascii", errors="replace").strip()
    #     log.debug(f"[{self.device_id}] Received: {text}")

    # async def poll(self) -> None:
    #     """Query device status periodically."""
    #     if not self.transport or not self.transport.connected:
    #         return

    # async def set_device_setting(self, key: str, value: Any) -> Any:
    #     """Write a setting to the device hardware."""
    #     raise ValueError(f"Unknown setting: {key}")

    # def _create_frame_parser(self) -> FrameParser | None:
    #     """Override for binary protocols with custom framing."""
    #     return CallableFrameParser(self._parse_frame)

    # def _resolve_delimiter(self) -> bytes | None:
    #     """Override to change or disable delimiter-based message splitting."""
    #     return None  # Return None for binary protocols using frame parsers

    # @staticmethod
    # def _parse_frame(buffer: bytes) -> tuple[bytes | None, bytes]:
    #     """Extract one frame from the buffer for binary protocols.
    #     Returns (frame, remaining_buffer) or (None, buffer) if incomplete."""
    #     if len(buffer) < 3:
    #         return None, buffer
    #     length = buffer[2]
    #     total = 3 + length + 1  # header + payload + checksum
    #     if len(buffer) < total:
    #         return None, buffer
    #     return buffer[:total], buffer[total:]
`,
  },
  {
    id: "osc",
    name: "OSC Device",
    description: "Open Sound Control over UDP (mixing consoles, show control, lighting)",
    transport: "osc",
    generateCode: (info) => {
      const cls = classNameFrom(info.id);
      return `"""${info.name} driver for OpenAVC — OSC over UDP."""

from __future__ import annotations

from typing import Any

from server.drivers.base import BaseDriver
from server.transport.osc_codec import osc_decode_message, osc_encode_message
from server.utils.logger import get_logger

log = get_logger(__name__)


class ${cls}Driver(BaseDriver):
    """${info.name} driver using Open Sound Control."""

    DRIVER_INFO = {
        "id": "${info.id}",
        "name": "${info.name}",
        "manufacturer": "${info.manufacturer}",
        "category": "${info.category}",
        "version": "1.0.0",
        "author": "OpenAVC",
        "description": "Controls ${info.name} via OSC over UDP.",
        "transport": "osc",
        "default_config": {
            "host": "",
            "port": 8000,
            "listen_port": 0,
            "poll_interval": 10,
        },
        "config_schema": {
            "host": {"type": "string", "required": True, "label": "IP Address"},
            "port": {"type": "integer", "default": 8000, "label": "Send Port"},
            "listen_port": {
                "type": "integer",
                "default": 0,
                "label": "Listen Port",
                "description": "Set to 0 to receive on the same socket",
            },
        },
        "state_variables": {
            # Define your state variables here:
            # "fader": {"type": "number", "label": "Fader Level"},
            # "mute": {"type": "boolean", "label": "Mute"},
        },
        "commands": {
            # Define your commands here:
            # "set_fader": {
            #     "label": "Set Fader",
            #     "params": {"level": {"type": "number"}},
            # },
        },
    }

    async def send_command(
        self, command: str, params: dict[str, Any] | None = None
    ) -> Any:
        params = params or {}

        if not self.transport or not self.transport.connected:
            raise ConnectionError(f"[{self.device_id}] Not connected")

        # Route commands to OSC messages:
        # if command == "set_fader":
        #     level = float(params.get("level", 0))
        #     msg = osc_encode_message("/ch/01/mix/fader", [("f", level)])
        #     await self.transport.send(msg)

        log.warning(f"[{self.device_id}] Unknown command: {command}")

    async def on_data_received(self, data: bytes) -> None:
        try:
            address, args = osc_decode_message(data)
        except (ValueError, Exception) as e:
            log.warning(f"[{self.device_id}] OSC decode error: {e}")
            return

        log.debug(f"[{self.device_id}] OSC: {address} {args}")

        # Match incoming OSC messages to state updates:
        # if address == "/ch/01/mix/fader" and args:
        #     self.set_state("fader", args[0][1])

    async def poll(self) -> None:
        if not self.transport or not self.transport.connected:
            return
        # Send periodic queries:
        # msg = osc_encode_message("/xremote")
        # await self.transport.send(msg)
`;
    },
  },
];

function classNameFrom(id: string): string {
  return id
    .split(/[_-]/)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join("")
    + "Driver";
}
