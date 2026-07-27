"""Acme Display — an invented Python driver for core platform tests.

There is no such product. It speaks the made-up line protocol served by
``tests/simulators/acme_display_simulator.py``, and it exists so core tests
can drive a *real* driver over a *real* socket: the platform's TCP transport,
its delimiter framing, ``BaseDriver.connect()``'s full lifecycle including a
challenge/response login in ``_post_connect``, an identity read in
``_initial_sync``, ``poll()``, and command dispatch with parameters.

Deliberately written the way the driver guide tells an author to write one —
hooks rather than a re-implemented ``connect()`` — so that if the lifecycle
regresses, these tests notice.
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import Any

from server.drivers.base import BaseDriver

# Wire power code -> state value.
_POWER = {"off": "off", "warm": "warming", "on": "on", "cool": "cooling"}

# Wire source code -> friendly name. Authors pick either when sending; the
# driver reports the friendly name.
_SOURCES = {"a1": "vga1", "a2": "vga2", "d1": "hdmi1", "d2": "hdmi2", "n1": "net"}
_SOURCE_CODES = {name: code for code, name in _SOURCES.items()}

# Fault digit -> severity, and the field order the device packs them in.
_SEVERITY = {"0": "ok", "1": "warning", "2": "error"}
_FAULT_FIELDS = ("fan", "lamp", "temp", "cover", "filter", "other")


class AcmeDisplayDriver(BaseDriver):
    """An invented display controller reached over TCP."""

    DRIVER_INFO: dict[str, Any] = {
        "id": "acme_display",
        "name": "Acme Display",
        "manufacturer": "Acme",
        "category": "display",
        "transport": "tcp",
        "default_config": {"port": 9412},
        "config_schema": {
            "host": {"type": "string", "label": "Host"},
            "port": {"type": "integer", "label": "Port", "default": 9412},
            "password": {"type": "string", "label": "Password", "secret": True},
        },
        "state_variables": {
            "power": {"type": "string", "label": "Power"},
            "input": {"type": "string", "label": "Input"},
            "available_inputs": {"type": "string", "label": "Available Inputs"},
            "lamp_hours": {"type": "integer", "label": "Lamp Hours"},
            "power_cycles": {"type": "integer", "label": "Power Cycles"},
            "mute_video": {"type": "boolean", "label": "Video Muted"},
            "mute_audio": {"type": "boolean", "label": "Audio Muted"},
            "fault_status": {"type": "string", "label": "Fault Status"},
            "fault_fan": {"type": "string", "label": "Fan"},
            "fault_lamp": {"type": "string", "label": "Lamp"},
            "fault_temp": {"type": "string", "label": "Temperature"},
            "fault_cover": {"type": "string", "label": "Cover"},
            "fault_filter": {"type": "string", "label": "Filter"},
            "fault_other": {"type": "string", "label": "Other"},
            "device_name": {"type": "string", "label": "Device Name"},
            "manufacturer": {"type": "string", "label": "Manufacturer"},
            "model": {"type": "string", "label": "Model"},
            "protocol_rev": {"type": "string", "label": "Protocol Revision"},
        },
        "commands": {
            "power_on": {"label": "Power On"},
            "power_off": {"label": "Power Off"},
            "set_input": {
                "label": "Set Input",
                "params": {
                    "input": {
                        "type": "string",
                        "label": "Input",
                        "help": "Friendly name (hdmi1) or raw code (d1).",
                    },
                },
            },
            "mute_video": {"label": "Mute Video"},
            "unmute_video": {"label": "Unmute Video"},
            "mute_audio": {"label": "Mute Audio"},
            "unmute_audio": {"label": "Unmute Audio"},
            "mute_all": {"label": "Mute All"},
            "unmute_all": {"label": "Unmute All"},
        },
    }

    def __init__(self, device_id, config, state, events):
        super().__init__(device_id, config, state, events)
        self._greeting: asyncio.Future[str] | None = None
        self._login: asyncio.Future[bool] | None = None

    # --- lifecycle hooks ---------------------------------------------------

    async def _pre_connect(self) -> None:
        """Arm the greeting catcher before the socket opens.

        The device greets the instant it accepts, so the future has to exist
        before ``_create_transport`` runs or the frame lands with nobody
        waiting for it.
        """
        loop = asyncio.get_running_loop()
        self._greeting = loop.create_future()
        self._login = loop.create_future()

    async def _post_connect(self) -> None:
        """Read the greeting and log in if the device asked for it.

        Raising here aborts the connection, which is what should happen when
        the password is wrong.
        """
        try:
            greeting = await asyncio.wait_for(self._greeting, timeout=3.0)
        except asyncio.TimeoutError as e:
            raise ConnectionError("Device sent no greeting") from e

        parts = greeting.split()
        if len(parts) < 2 or parts[1] == "0":
            return  # open device, nothing to do

        nonce = parts[2] if len(parts) > 2 else ""
        password = self.config.get("password", "") or ""
        token = hashlib.md5((nonce + password).encode("ascii")).hexdigest()
        await self.send_raw(f"AUTH {token}")
        try:
            ok = await asyncio.wait_for(self._login, timeout=3.0)
        except asyncio.TimeoutError as e:
            raise ConnectionError("Device did not answer the login") from e
        if not ok:
            raise ConnectionError("Device rejected the password")

    async def _initial_sync(self) -> None:
        """One-time identity read once the session is up."""
        await self._query("ID")
        await self._query("SRCLIST")

    # --- protocol ----------------------------------------------------------

    async def _query(self, field: str) -> None:
        await self.send_raw(f"?{field}")

    async def _set(self, field: str, value: str) -> None:
        await self.send_raw(f"+{field} {value}")

    async def poll(self) -> None:
        """Read the fields that are always available, plus the ones that only
        answer while the device is awake.

        Skipping the awake-only fields when the device is off is the point:
        querying them anyway would earn an ``ERR_STATE`` for every field on
        every poll cycle.
        """
        await self._query("PWR")
        await self._query("HRS")
        await self._query("FAULT")
        if self.get_state("power") == "on":
            await self._query("SRC")
            await self._query("MUTEV")
            await self._query("MUTEA")

    async def send_command(self, command: str, params: dict[str, Any] | None = None) -> Any:
        params = params or {}
        if command == "power_on":
            await self._set("PWR", "on")
        elif command == "power_off":
            await self._set("PWR", "off")
        elif command == "set_input":
            requested = str(params.get("input", ""))
            code = _SOURCE_CODES.get(requested, requested)
            await self._set("SRC", code)
        elif command == "mute_video":
            await self._set("MUTEV", "1")
        elif command == "unmute_video":
            await self._set("MUTEV", "0")
        elif command == "mute_audio":
            await self._set("MUTEA", "1")
        elif command == "unmute_audio":
            await self._set("MUTEA", "0")
        elif command == "mute_all":
            await self._set("MUTEALL", "1")
        elif command == "unmute_all":
            await self._set("MUTEALL", "0")
        else:
            raise ValueError(f"Unknown command: {command}")
        return True

    async def on_data_received(self, data: bytes) -> None:
        text = data.decode("ascii", errors="ignore").strip()
        if not text:
            return

        if text.startswith("ACME "):
            self._handle_session_frame(text)
            return
        if text.startswith("!"):
            return  # the device refused a field; poll() will ask again
        if not text.startswith("="):
            return

        field, _, value = text[1:].partition(" ")
        self._apply(field, value.strip())

    def _handle_session_frame(self, text: str) -> None:
        """Greeting and login replies, both of which a hook is awaiting."""
        if text.startswith("ACME OK") or text.startswith("ACME ERR"):
            if self._login is not None and not self._login.done():
                self._login.set_result(text.startswith("ACME OK"))
            return
        if self._greeting is not None and not self._greeting.done():
            self._greeting.set_result(text)

    def _apply(self, field: str, value: str) -> None:
        if field == "PWR":
            self.set_state("power", _POWER.get(value, value))
        elif field == "SRC":
            self.set_state("input", _SOURCES.get(value, value))
        elif field == "SRCLIST":
            names = [_SOURCES.get(code, code) for code in value.split()]
            self.set_state("available_inputs", ",".join(names))
        elif field == "MUTEV":
            self.set_state("mute_video", value == "1")
        elif field == "MUTEA":
            self.set_state("mute_audio", value == "1")
        elif field == "MUTEALL":
            self.set_states({"mute_video": value == "1", "mute_audio": value == "1"})
        elif field == "HRS":
            parts = value.split()
            if len(parts) == 2 and all(p.isdigit() for p in parts):
                self.set_states(
                    {"lamp_hours": int(parts[0]), "power_cycles": int(parts[1])}
                )
        elif field == "FAULT":
            self._apply_fault(value)
        elif field == "ID":
            parts = value.split("|")
            if len(parts) == 4:
                self.set_states({
                    "device_name": parts[0],
                    "manufacturer": parts[1],
                    "model": parts[2],
                    "protocol_rev": parts[3],
                })

    def _apply_fault(self, digits: str) -> None:
        if len(digits) != len(_FAULT_FIELDS):
            return
        updates: dict[str, Any] = {}
        problems: list[str] = []
        for name, digit in zip(_FAULT_FIELDS, digits):
            severity = _SEVERITY.get(digit, "unknown")
            updates[f"fault_{name}"] = severity
            if severity != "ok":
                problems.append(f"{name}:{severity}")
        updates["fault_status"] = ", ".join(problems) if problems else "ok"
        self.set_states(updates)
