"""
Samsung MDC Simulator — mock Samsung display for testing.

Listens on a TCP port, accepts MDC binary frames, and responds
with appropriate ACK frames.
"""

from __future__ import annotations

import asyncio

from server.transport.binary_helpers import checksum_sum
from server.utils.logger import get_logger

log = get_logger(__name__)


class SamsungMDCSimulator:
    """Simulated Samsung MDC display."""

    def __init__(self, port: int = 1515):
        self.port = port
        self._server: asyncio.Server | None = None
        self._power = False
        self._volume = 20
        self._mute = False
        self._input = 0x21  # HDMI1

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._handle_client, "127.0.0.1", self.port,
        )
        log.info(f"Samsung MDC simulator listening on port {self.port}")

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            log.info("Samsung MDC simulator stopped")

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
    ) -> None:
        buffer = b""
        try:
            while True:
                data = await reader.read(1024)
                if not data:
                    break
                buffer += data

                while len(buffer) >= 4:
                    # Find 0xAA header
                    start = buffer.find(0xAA)
                    if start == -1:
                        buffer = b""
                        break
                    if start > 0:
                        buffer = buffer[start:]

                    if len(buffer) < 4:
                        break

                    data_len = buffer[3]
                    total_len = 4 + data_len + 1

                    if len(buffer) < total_len:
                        break

                    # Extract frame (skip header and checksum)
                    cmd = buffer[1]
                    display_id = buffer[2]
                    payload = buffer[4 : 4 + data_len]
                    buffer = buffer[total_len:]

                    # Process and respond
                    response = self._process_command(cmd, display_id, payload)
                    if response:
                        writer.write(response)
                        await writer.drain()
        except (ConnectionError, OSError):
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass

    def _process_command(
        self, cmd: int, display_id: int, payload: bytes,
    ) -> bytes | None:
        """Process a command and return a response frame."""
        if cmd == 0x11:  # POWER
            if payload:
                self._power = bool(payload[0])
            return self._build_ack(cmd, display_id, bytes([int(self._power)]))

        elif cmd == 0x12:  # VOLUME
            if payload:
                self._volume = payload[0]
            return self._build_ack(cmd, display_id, bytes([self._volume]))

        elif cmd == 0x13:  # MUTE
            if payload:
                self._mute = bool(payload[0])
            return self._build_ack(cmd, display_id, bytes([int(self._mute)]))

        elif cmd == 0x14:  # INPUT
            if payload:
                self._input = payload[0]
            return self._build_ack(cmd, display_id, bytes([self._input]))

        elif cmd == 0x00:  # STATUS
            return self._build_ack(
                cmd, display_id,
                bytes([int(self._power), self._volume, int(self._mute)]),
            )

        return None

    @staticmethod
    def _build_ack(cmd: int, display_id: int, data: bytes) -> bytes:
        """Build an ACK response frame."""
        frame = bytes([cmd, display_id, len(data)]) + data
        cs = checksum_sum(frame)
        return bytes([0xAA]) + frame + bytes([cs])


if __name__ == "__main__":
    async def main():
        sim = SamsungMDCSimulator(port=1515)
        await sim.start()
        print(f"Samsung MDC simulator running on port {sim.port}")
        try:
            await asyncio.Event().wait()
        except KeyboardInterrupt:
            pass
        finally:
            await sim.stop()

    asyncio.run(main())
