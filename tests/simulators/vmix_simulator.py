"""
vMix TCP API Simulator.

A TCP server that simulates a vMix instance for development and testing.
Supports FUNCTION commands, XML state queries, and TALLY subscriptions.

Usage:
    # In tests (fast timing):
    sim = VMixSimulator(port=18099)
    await sim.start()
    # ... run tests ...
    await sim.stop()

    # Standalone:
    python -m tests.simulators.vmix_simulator
"""

from __future__ import annotations

import asyncio
import sys


class VMixSimulator:
    """Simulates a vMix TCP API server."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8099,
    ):
        self.host = host
        self.port = port

        # Simulated state
        self.active_input = 1
        self.preview_input = 2
        self.recording = False
        self.streaming = False
        self.external = False
        self.fade_to_black = False
        self.version = "29.0.0.1"

        # 4 simulated inputs
        self.inputs = [
            {"number": "1", "title": "Camera 1", "type": "Capture", "state": "Running", "muted": False, "loop": False, "position": 0, "duration": 0, "volume": 100},
            {"number": "2", "title": "Camera 2", "type": "Capture", "state": "Running", "muted": False, "loop": False, "position": 0, "duration": 0, "volume": 100},
            {"number": "3", "title": "Slides", "type": "Image", "state": "Paused", "muted": True, "loop": False, "position": 0, "duration": 30000, "volume": 100},
            {"number": "4", "title": "Video Clip", "type": "Video", "state": "Paused", "muted": False, "loop": True, "position": 5000, "duration": 60000, "volume": 80},
        ]

        self.overlays = {"1": 0, "2": 0, "3": 0, "4": 0}
        self.transitions = [
            {"number": "1", "effect": "Fade", "duration": 1000},
            {"number": "2", "effect": "Merge", "duration": 2000},
            {"number": "3", "effect": "Wipe", "duration": 1500},
            {"number": "4", "effect": "Cut", "duration": 0},
        ]

        self._server: asyncio.Server | None = None
        self._clients: list[asyncio.StreamWriter] = []
        self._tally_subscribers: list[asyncio.StreamWriter] = []
        self._acts_subscribers: list[asyncio.StreamWriter] = []

    async def start(self) -> None:
        """Start the simulator TCP server."""
        self._server = await asyncio.start_server(
            self._handle_client, self.host, self.port
        )
        print(f"vMix Simulator running on {self.host}:{self.port}")

    async def stop(self) -> None:
        """Stop the simulator and disconnect all clients."""
        for writer in self._clients:
            try:
                writer.close()
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass
        self._clients.clear()
        self._tally_subscribers.clear()
        self._acts_subscribers.clear()
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        print("vMix Simulator stopped")

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Handle a single vMix client connection."""
        addr = writer.get_extra_info("peername")
        print(f"vMix client connected from {addr}")
        self._clients.append(writer)

        try:
            buffer = b""
            while True:
                data = await reader.read(4096)
                if not data:
                    break

                buffer += data
                while b"\r\n" in buffer:
                    cmd_bytes, buffer = buffer.split(b"\r\n", 1)
                    cmd = cmd_bytes.decode("utf-8", errors="ignore").strip()
                    if cmd:
                        await self._process_command(cmd, writer)
        except (ConnectionError, OSError):
            pass
        finally:
            print(f"vMix client disconnected from {addr}")
            if writer in self._clients:
                self._clients.remove(writer)
            if writer in self._tally_subscribers:
                self._tally_subscribers.remove(writer)
            if writer in self._acts_subscribers:
                self._acts_subscribers.remove(writer)
            try:
                writer.close()
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass

    async def _process_command(self, cmd: str, writer: asyncio.StreamWriter) -> None:
        """Process a vMix TCP API command."""
        # XML state request
        if cmd == "XML":
            await self._send_xml(writer)
            return

        # VERSION request
        if cmd == "VERSION":
            await self._send_line(writer, f"VERSION OK {self.version}")
            return

        # SUBSCRIBE commands
        if cmd.startswith("SUBSCRIBE"):
            parts = cmd.split()
            if len(parts) >= 2:
                sub_type = parts[1].upper()
                if sub_type == "TALLY":
                    if writer not in self._tally_subscribers:
                        self._tally_subscribers.append(writer)
                    await self._send_line(writer, "SUBSCRIBE OK TALLY")
                    # Send initial tally state
                    await self._send_tally(writer)
                elif sub_type == "ACTS":
                    if writer not in self._acts_subscribers:
                        self._acts_subscribers.append(writer)
                    await self._send_line(writer, "SUBSCRIBE OK ACTS")
            return

        # UNSUBSCRIBE
        if cmd.startswith("UNSUBSCRIBE"):
            parts = cmd.split()
            if len(parts) >= 2:
                sub_type = parts[1].upper()
                if sub_type == "TALLY" and writer in self._tally_subscribers:
                    self._tally_subscribers.remove(writer)
                elif sub_type == "ACTS" and writer in self._acts_subscribers:
                    self._acts_subscribers.remove(writer)
            await self._send_line(writer, "UNSUBSCRIBE OK")
            return

        # TALLY request (non-subscription query)
        if cmd == "TALLY":
            await self._send_tally(writer)
            return

        # FUNCTION commands
        if cmd.startswith("FUNCTION"):
            await self._process_function(cmd, writer)
            return

        # Unknown
        print(f"[vMix Sim] Unknown command: {cmd}")

    async def _process_function(self, cmd: str, writer: asyncio.StreamWriter) -> None:
        """Process a FUNCTION command."""
        # Parse: FUNCTION <Name> [query_string]
        parts = cmd.split(" ", 2)
        if len(parts) < 2:
            await self._send_line(writer, "FUNCTION 0 ER Invalid command")
            return

        func_name = parts[1]
        query = parts[2] if len(parts) > 2 else ""

        # Parse query params
        params: dict[str, str] = {}
        if query:
            for pair in query.split("&"):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    params[k] = v

        input_val = params.get("Input", "")
        value = params.get("Value", "")

        # Resolve input number
        input_num = self._resolve_input(input_val) if input_val else None

        # Process known functions
        tally_changed = False

        if func_name == "Cut":
            if input_num:
                self.active_input = input_num
            else:
                # Cut preview to program
                old_active = self.active_input
                self.active_input = self.preview_input
                self.preview_input = old_active
            tally_changed = True
        elif func_name == "CutDirect":
            if input_num:
                self.active_input = input_num
                tally_changed = True
        elif func_name == "Fade":
            if input_num:
                self.active_input = input_num
            else:
                old_active = self.active_input
                self.active_input = self.preview_input
                self.preview_input = old_active
            tally_changed = True
        elif func_name == "FadeToBlack":
            self.fade_to_black = not self.fade_to_black
        elif func_name == "PreviewInput":
            if input_num:
                self.preview_input = input_num
                tally_changed = True
        elif func_name == "ActiveInput":
            if input_num:
                self.active_input = input_num
                tally_changed = True
        elif func_name == "StartRecording":
            self.recording = True
        elif func_name == "StopRecording":
            self.recording = False
        elif func_name == "StartStreaming":
            self.streaming = True
        elif func_name == "StopStreaming":
            self.streaming = False
        elif func_name == "StartExternal":
            self.external = True
        elif func_name == "StopExternal":
            self.external = False
        elif func_name.startswith("SetVolume") and input_num:
            inp = self._get_input(input_num)
            if inp and value:
                try:
                    inp["volume"] = int(value)
                except ValueError:
                    pass
        elif func_name == "AudioOn" and input_num:
            inp = self._get_input(input_num)
            if inp:
                inp["muted"] = False
        elif func_name == "AudioOff" and input_num:
            inp = self._get_input(input_num)
            if inp:
                inp["muted"] = True
        elif func_name.startswith("OverlayInput"):
            overlay_num = value if value else "1"
            if func_name == "OverlayInputIn" and input_num:
                self.overlays[overlay_num] = input_num
            elif func_name == "OverlayInputOut":
                self.overlays[overlay_num] = 0
            elif func_name == "OverlayInputOff":
                self.overlays[overlay_num] = 0
            elif func_name == "OverlayInputAllOff":
                for k in self.overlays:
                    self.overlays[k] = 0
            elif input_num:
                # Toggle
                if self.overlays.get(overlay_num) == input_num:
                    self.overlays[overlay_num] = 0
                else:
                    self.overlays[overlay_num] = input_num
        elif func_name == "SetText" and input_num:
            # In a real sim we'd update title text fields
            pass
        elif func_name == "SetInputName" and input_num and value:
            inp = self._get_input(input_num)
            if inp:
                inp["title"] = value
        elif func_name == "Play" and input_num:
            inp = self._get_input(input_num)
            if inp:
                inp["state"] = "Running"
        elif func_name == "Pause" and input_num:
            inp = self._get_input(input_num)
            if inp:
                inp["state"] = "Paused"
        elif func_name == "Restart" and input_num:
            inp = self._get_input(input_num)
            if inp:
                inp["state"] = "Running"
                inp["position"] = 0
        elif func_name == "LoopOn" and input_num:
            inp = self._get_input(input_num)
            if inp:
                inp["loop"] = True
        elif func_name == "LoopOff" and input_num:
            inp = self._get_input(input_num)
            if inp:
                inp["loop"] = False

        # Send OK response
        await self._send_line(writer, "FUNCTION OK")

        # Push tally updates to subscribers
        if tally_changed:
            await self._push_tally()

    def _resolve_input(self, val: str) -> int | None:
        """Resolve input number from number string or input name."""
        try:
            return int(val)
        except ValueError:
            # Search by name
            for inp in self.inputs:
                if inp["title"].lower() == val.lower():
                    return int(inp["number"])
        return None

    def _get_input(self, num: int) -> dict | None:
        """Get input dict by number."""
        for inp in self.inputs:
            if int(inp["number"]) == num:
                return inp
        return None

    def _build_tally_string(self) -> str:
        """Build tally state string: one digit per input (0=safe, 1=program, 2=preview)."""
        chars = []
        for inp in self.inputs:
            num = int(inp["number"])
            if num == self.active_input:
                chars.append("1")
            elif num == self.preview_input:
                chars.append("2")
            else:
                chars.append("0")
        return "".join(chars)

    async def _send_tally(self, writer: asyncio.StreamWriter) -> None:
        """Send tally state to a specific client."""
        tally = self._build_tally_string()
        await self._send_line(writer, f"TALLY OK {tally}")

    async def _push_tally(self) -> None:
        """Push tally update to all tally subscribers."""
        tally = self._build_tally_string()
        for writer in list(self._tally_subscribers):
            try:
                await self._send_line(writer, f"TALLY OK {tally}")
            except (ConnectionError, OSError):
                if writer in self._tally_subscribers:
                    self._tally_subscribers.remove(writer)

    async def _send_xml(self, writer: asyncio.StreamWriter) -> None:
        """Send the full XML state response."""
        xml = self._build_xml()
        xml_bytes = xml.encode("utf-8")
        # Send as: XML <length>\r\n<body>
        header = f"XML {len(xml_bytes)}\r\n".encode("utf-8")
        try:
            writer.write(header + xml_bytes)
            await writer.drain()
        except (ConnectionError, OSError):
            pass

    def _build_xml(self) -> str:
        """Build the vMix XML state document."""
        lines = [
            f'<vmix version="{self.version}" active="{self.active_input}" preview="{self.preview_input}">',
            f"  <recording>{'True' if self.recording else 'False'}</recording>",
            f"  <streaming>{'True' if self.streaming else 'False'}</streaming>",
            f"  <external>{'True' if self.external else 'False'}</external>",
            f"  <fadeToBlack>{'True' if self.fade_to_black else 'False'}</fadeToBlack>",
            "  <inputs>",
        ]
        for inp in self.inputs:
            lines.append(
                f'    <input number="{inp["number"]}" title="{inp["title"]}" '
                f'type="{inp["type"]}" state="{inp["state"]}" '
                f'muted="{"True" if inp["muted"] else "False"}" '
                f'loop="{"True" if inp["loop"] else "False"}" '
                f'position="{inp["position"]}" duration="{inp["duration"]}" />'
            )
        lines.append("  </inputs>")

        lines.append("  <overlays>")
        for num, inp_num in sorted(self.overlays.items()):
            lines.append(f'    <overlay number="{num}">{inp_num if inp_num else ""}</overlay>')
        lines.append("  </overlays>")

        lines.append("  <transitions>")
        for trans in self.transitions:
            lines.append(
                f'    <transition number="{trans["number"]}" '
                f'effect="{trans["effect"]}" duration="{trans["duration"]}" />'
            )
        lines.append("  </transitions>")

        lines.append("</vmix>")
        return "\n".join(lines)

    async def _send_line(self, writer: asyncio.StreamWriter, text: str) -> None:
        """Send a CRLF-terminated line to a client."""
        try:
            writer.write(f"{text}\r\n".encode("utf-8"))
            await writer.drain()
        except (ConnectionError, OSError):
            pass


async def main():
    """Run the simulator standalone for manual testing."""
    sim = VMixSimulator()
    await sim.start()
    print("Press Ctrl+C to stop")
    try:
        await asyncio.Event().wait()
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
