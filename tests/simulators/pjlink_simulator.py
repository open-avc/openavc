"""
PJLink Class 1 Projector Simulator.

A TCP server that simulates a PJLink-compatible projector for development
and testing. Supports power on/off (with warming/cooling delays), input
selection, mute, lamp hours, error status, device info, input discovery,
and optional MD5 authentication.

Usage:
    # In tests (fast timing):
    sim = PJLinkSimulator(port=4352, warmup_time=0.5, cooldown_time=0.3)
    await sim.start()
    # ... run tests ...
    await sim.stop()

    # With authentication:
    sim = PJLinkSimulator(port=4352, password="secret")
    await sim.start()

    # Standalone (realistic timing):
    python -m tests.simulators.pjlink_simulator
"""

from __future__ import annotations

import asyncio
import hashlib
import sys


class PJLinkSimulator:
    """Simulates a PJLink Class 1 projector over TCP."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 4352,
        warmup_time: float = 3.0,
        cooldown_time: float = 2.0,
        password: str = "",
    ):
        self.host = host
        self.port = port
        self.warmup_time = warmup_time
        self.cooldown_time = cooldown_time
        self.password = password

        # Simulated projector state
        self.power = 0  # 0=off, 1=on, 2=cooling, 3=warming
        self.input_source = "31"  # HDMI1
        self.mute_status = "30"  # 30=off, 11=video mute, 21=audio mute, 31=both
        self.lamp_hours = 3200
        self.lamp_count = 1
        self.error_status = "000000"  # 6 chars: fan, lamp, temp, cover, filter, other
        self.name = "PJLink Simulator"
        self.manufacturer = "OpenAVC"
        self.product_name = "PJLink Sim v1.0"
        self.available_inputs = ["11", "12", "31", "32", "51"]

        self._server: asyncio.Server | None = None
        self._transition_task: asyncio.Task | None = None
        self._clients: list[asyncio.StreamWriter] = []
        self._auth_random = "abcd1234"

        # Friendly names for logging
        self._power_names = {0: "OFF", 1: "ON", 2: "COOLING", 3: "WARMING"}
        self._input_names = {
            "11": "VGA 1", "12": "VGA 2",
            "31": "HDMI 1", "32": "HDMI 2",
            "51": "Network",
        }
        self._mute_names = {
            "10": "video unmuted", "11": "video MUTED",
            "20": "audio unmuted", "21": "audio MUTED",
            "30": "all unmuted", "31": "all MUTED",
        }

    async def start(self) -> None:
        """Start the simulator TCP server."""
        self._server = await asyncio.start_server(
            self._handle_client, self.host, self.port
        )
        # Update port to the actual bound port (supports port=0 for ephemeral)
        if self._server.sockets:
            self.port = self._server.sockets[0].getsockname()[1]
        auth_str = " (auth enabled)" if self.password else ""
        print(f"PJLink Simulator running on {self.host}:{self.port}{auth_str}")

    async def stop(self) -> None:
        """Stop the simulator and disconnect all clients."""
        if self._transition_task and not self._transition_task.done():
            self._transition_task.cancel()
            try:
                await self._transition_task
            except asyncio.CancelledError:
                pass
        for writer in self._clients:
            try:
                writer.close()
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass
        self._clients.clear()
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        print("PJLink Simulator stopped")

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Handle a single PJLink client connection."""
        addr = writer.get_extra_info("peername")
        print(f"PJLink client connected from {addr}")
        self._clients.append(writer)

        try:
            # PJLink greeting
            if self.password:
                writer.write(
                    f"PJLINK 1 {self._auth_random}\r".encode("ascii")
                )
            else:
                writer.write(b"PJLINK 0\r")
            await writer.drain()

            buffer = b""
            while True:
                data = await reader.read(1024)
                if not data:
                    break

                buffer += data
                # Process complete commands (delimited by \r)
                while b"\r" in buffer:
                    cmd_bytes, buffer = buffer.split(b"\r", 1)
                    cmd = cmd_bytes.decode("ascii", errors="ignore").strip()
                    if cmd:
                        response = self._process_command(cmd)
                        if response:
                            writer.write(response.encode("ascii") + b"\r")
                            await writer.drain()
        except (ConnectionError, OSError):
            pass
        finally:
            print(f"PJLink client disconnected from {addr}")
            if writer in self._clients:
                self._clients.remove(writer)
            try:
                writer.close()
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass

    def _process_command(self, cmd: str) -> str | None:
        """
        Parse a PJLink command and return the response string.

        PJLink command format: %1<CMD> <param>
        PJLink response format: %1<CMD>=<result>

        If authentication is enabled, commands are prefixed with an MD5 hash.
        """
        # Handle authentication
        if self.password:
            expected_hash = hashlib.md5(
                (self._auth_random + self.password).encode("ascii")
            ).hexdigest()
            if cmd.startswith(expected_hash):
                cmd = cmd[len(expected_hash):]
            else:
                return "PJLINK ERRA"

        if not cmd.startswith("%1"):
            return None

        # Extract the 4-char command code and parameter
        body = cmd[2:]  # Remove "%1"
        if " " in body:
            code, param = body.split(" ", 1)
        else:
            code = body
            param = ""

        code = code.upper()

        # --- Power ---
        if code == "POWR":
            if param == "?":
                return f"%1POWR={self.power}"
            elif param == "1":
                if self.power in (0, 2):  # off or cooling
                    self.power = 3  # warming
                    print(
                        f"[PJLink] Power ON requested — warming up "
                        f"({self.warmup_time}s)..."
                    )
                    self._schedule_transition(1, self.warmup_time)
                else:
                    print(
                        f"[PJLink] Power ON requested — already "
                        f"{self._power_names[self.power]}"
                    )
                return "%1POWR=OK"
            elif param == "0":
                if self.power in (1, 3):  # on or warming
                    self.power = 2  # cooling
                    print(
                        f"[PJLink] Power OFF requested — cooling down "
                        f"({self.cooldown_time}s)..."
                    )
                    self._schedule_transition(0, self.cooldown_time)
                else:
                    print(
                        f"[PJLink] Power OFF requested — already "
                        f"{self._power_names[self.power]}"
                    )
                return "%1POWR=OK"

        # --- Input (ERR2 when not on) ---
        elif code == "INPT":
            if self.power != 1:
                return "%1INPT=ERR2"
            if param == "?":
                return f"%1INPT={self.input_source}"
            elif param:
                if param in self.available_inputs:
                    old_name = self._input_names.get(
                        self.input_source, self.input_source
                    )
                    new_name = self._input_names.get(param, param)
                    self.input_source = param
                    print(f"[PJLink] Input changed: {old_name} -> {new_name}")
                    return "%1INPT=OK"
                else:
                    return "%1INPT=ERR2"

        # --- AV Mute (ERR2 when not on) ---
        elif code == "AVMT":
            if self.power != 1:
                return "%1AVMT=ERR2"
            if param == "?":
                return f"%1AVMT={self.mute_status}"
            elif param in ("10", "11", "20", "21", "30", "31"):
                self.mute_status = param
                print(
                    f"[PJLink] Mute: {self._mute_names.get(param, param)}"
                )
                return "%1AVMT=OK"

        # --- Lamp ---
        elif code == "LAMP":
            if param == "?":
                lamp_on = 1 if self.power == 1 else 0
                # Multi-lamp support
                parts = []
                for i in range(self.lamp_count):
                    parts.append(f"{self.lamp_hours} {lamp_on}")
                return f"%1LAMP={' '.join(parts)}"

        # --- Error Status ---
        elif code == "ERST":
            if param == "?":
                return f"%1ERST={self.error_status}"

        # --- Name ---
        elif code == "NAME":
            if param == "?":
                return f"%1NAME={self.name}"

        # --- Manufacturer ---
        elif code == "INF1":
            if param == "?":
                return f"%1INF1={self.manufacturer}"

        # --- Product Name ---
        elif code == "INF2":
            if param == "?":
                return f"%1INF2={self.product_name}"

        # --- Class ---
        elif code == "CLSS":
            if param == "?":
                return "%1CLSS=1"

        # --- Input Terminal List ---
        elif code == "INST":
            if param == "?":
                return f"%1INST={' '.join(self.available_inputs)}"

        # Unknown command
        return f"%1{code}=ERR1"

    def _schedule_transition(self, target_power: int, delay: float) -> None:
        """Schedule a power state transition after a delay."""
        if self._transition_task and not self._transition_task.done():
            self._transition_task.cancel()

        async def _do_transition():
            await asyncio.sleep(delay)
            self.power = target_power
            print(f"[PJLink] Power is now {self._power_names[target_power]}")

        self._transition_task = asyncio.create_task(_do_transition())


async def main():
    """Run the simulator standalone for manual testing."""
    sim = PJLinkSimulator(warmup_time=5.0, cooldown_time=3.0)
    await sim.start()
    print("Press Ctrl+C to stop")
    try:
        await asyncio.Event().wait()  # Run forever
    except asyncio.CancelledError:
        pass
    finally:
        await sim.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nShutting down...")
        sys.exit(0)
