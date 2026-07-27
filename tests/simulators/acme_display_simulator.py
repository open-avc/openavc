"""
Acme Display simulator — an invented TCP device for core platform tests.

There is no such product. The protocol below was made up to exercise the
platform, not to match anything real: an ASCII line protocol terminated by
CR, an optional challenge/response login, queries that answer with a single
value and queries that answer with several fields in one line, a power state
that takes time to settle, and a command the device refuses while it is off.
Those are the shapes a driver has to cope with, so they are what the
simulator provides.

Wire format (all frames terminated by ``\\r``):

    greeting   device -> ``ACME 0``               (no login required)
                         ``ACME 1 <nonce>``       (login required)
    login      client -> ``AUTH <token>``         token = md5(nonce + password)
               device -> ``ACME OK`` | ``ACME ERR AUTH``
    query      client -> ``?FIELD``
               device -> ``=FIELD <value>`` | ``!FIELD ERR_FIELD``
    set        client -> ``+FIELD <value>``
               device -> ``=FIELD <value>`` | ``!FIELD ERR_STATE``

Fields:

    PWR      off | warm | on | cool   (settable: ``on`` / ``off``)
    SRC      input code               (settable only while PWR is ``on``)
    SRCLIST  space-separated codes    (read-only)
    MUTEV    0 | 1                    video mute
    MUTEA    0 | 1                    audio mute
    MUTEALL  0 | 1                    set-only; writes both mute fields
    HRS      "<hours> <cycles>"       two integers in one response
    FAULT    six digits               fan, lamp, temp, cover, filter, other
                                      0 = ok, 1 = warning, 2 = error
    ID       "<name>|<make>|<model>|<rev>"

Usage in tests::

    sim = AcmeDisplaySimulator(port=0, warmup_time=0.3, cooldown_time=0.2)
    await sim.start()      # sim.port holds the bound port
    ...
    await sim.stop()
"""

from __future__ import annotations

import asyncio
import hashlib

# Power codes as they appear on the wire.
POWER_OFF = "off"
POWER_WARMING = "warm"
POWER_ON = "on"
POWER_COOLING = "cool"


class AcmeDisplaySimulator:
    """Simulates an Acme Display over TCP."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 0,
        warmup_time: float = 3.0,
        cooldown_time: float = 2.0,
        password: str = "",
    ):
        self.host = host
        self.port = port
        self.warmup_time = warmup_time
        self.cooldown_time = cooldown_time
        self.password = password

        # Simulated device state.
        self.power = POWER_OFF
        self.source = "d1"
        self.mute_video = False
        self.mute_audio = False
        self.hours = 3200
        self.cycles = 1
        self.fault = "000000"
        self.name = "Acme Display Simulator"
        self.manufacturer = "Acme"
        self.model = "AX-100"
        self.protocol_rev = "2"
        self.sources = ["a1", "a2", "d1", "d2", "n1"]

        # Every query the simulator has answered, in order. Tests assert on
        # this to prove a poll skipped a field rather than merely ignoring
        # the reply.
        self.queries: list[str] = []

        self.nonce = "5f3a91c4"

        self._server: asyncio.Server | None = None
        self._transition: asyncio.Task | None = None
        self._clients: list[asyncio.StreamWriter] = []

    # --- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        """Bind and start serving. With ``port=0``, ``self.port`` is updated
        to the ephemeral port actually bound."""
        self._server = await asyncio.start_server(
            self._handle_client, self.host, self.port
        )
        if self._server.sockets:
            self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        """Stop serving and drop every client."""
        if self._transition and not self._transition.done():
            self._transition.cancel()
            try:
                await self._transition
            except asyncio.CancelledError:
                pass
        self._transition = None
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

    # --- connection handling -----------------------------------------------

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self._clients.append(writer)
        authed = not self.password
        try:
            if self.password:
                writer.write(f"ACME 1 {self.nonce}\r".encode("ascii"))
            else:
                writer.write(b"ACME 0\r")
            await writer.drain()

            buffer = b""
            while True:
                data = await reader.read(1024)
                if not data:
                    break
                buffer += data
                while b"\r" in buffer:
                    line, buffer = buffer.split(b"\r", 1)
                    text = line.decode("ascii", errors="ignore").strip()
                    if not text:
                        continue
                    reply, authed = self._process(text, authed)
                    if reply is not None:
                        writer.write(f"{reply}\r".encode("ascii"))
                        await writer.drain()
        except (ConnectionError, OSError, asyncio.CancelledError):
            pass
        finally:
            if writer in self._clients:
                self._clients.remove(writer)
            try:
                writer.close()
            except (ConnectionError, OSError):
                pass

    def _process(self, text: str, authed: bool) -> tuple[str | None, bool]:
        """Handle one frame. Returns (reply or None, new auth state)."""
        if text.startswith("AUTH "):
            token = text[5:].strip()
            expected = hashlib.md5(
                (self.nonce + self.password).encode("ascii")
            ).hexdigest()
            if self.password and token == expected:
                return "ACME OK", True
            return "ACME ERR AUTH", False

        if not authed:
            # A device that wants a login answers nothing else until it gets
            # one. Silence (rather than an error) is deliberate: it is the
            # harder case for a driver, because there is no frame to key on.
            return None, authed

        if text.startswith("?"):
            return self._query(text[1:].strip()), authed
        if text.startswith("+"):
            field, _, value = text[1:].partition(" ")
            return self._set(field.strip(), value.strip()), authed
        return None, authed

    # --- fields ------------------------------------------------------------

    def _query(self, field: str) -> str:
        self.queries.append(field)
        if field == "PWR":
            return f"=PWR {self.power}"
        if field == "SRC":
            return f"=SRC {self.source}"
        if field == "SRCLIST":
            return f"=SRCLIST {' '.join(self.sources)}"
        if field == "MUTEV":
            return f"=MUTEV {int(self.mute_video)}"
        if field == "MUTEA":
            return f"=MUTEA {int(self.mute_audio)}"
        if field == "HRS":
            return f"=HRS {self.hours} {self.cycles}"
        if field == "FAULT":
            return f"=FAULT {self.fault}"
        if field == "ID":
            return (
                f"=ID {self.name}|{self.manufacturer}|"
                f"{self.model}|{self.protocol_rev}"
            )
        return f"!{field} ERR_FIELD"

    def _set(self, field: str, value: str) -> str:
        if field == "PWR":
            if value == "on":
                if self.power in (POWER_ON, POWER_WARMING):
                    return f"=PWR {self.power}"
                self.power = POWER_WARMING
                self._schedule(POWER_ON, self.warmup_time)
                return f"=PWR {self.power}"
            if value == "off":
                if self.power in (POWER_OFF, POWER_COOLING):
                    return f"=PWR {self.power}"
                self.power = POWER_COOLING
                self._schedule(POWER_OFF, self.cooldown_time)
                return f"=PWR {self.power}"
            return "!PWR ERR_FIELD"

        # Everything below needs the device awake — the classic "the device
        # is reachable but refuses this right now" case.
        if self.power != POWER_ON:
            return f"!{field} ERR_STATE"

        if field == "SRC":
            if value not in self.sources:
                return "!SRC ERR_FIELD"
            self.source = value
            return f"=SRC {self.source}"
        if field == "MUTEV":
            self.mute_video = value == "1"
            return f"=MUTEV {int(self.mute_video)}"
        if field == "MUTEA":
            self.mute_audio = value == "1"
            return f"=MUTEA {int(self.mute_audio)}"
        if field == "MUTEALL":
            self.mute_video = self.mute_audio = value == "1"
            return f"=MUTEALL {int(self.mute_video)}"
        return f"!{field} ERR_FIELD"

    def _schedule(self, target: str, delay: float) -> None:
        """Settle into ``target`` after ``delay`` seconds."""
        if self._transition and not self._transition.done():
            self._transition.cancel()

        async def _settle() -> None:
            await asyncio.sleep(delay)
            self.power = target

        self._transition = asyncio.create_task(_settle())
