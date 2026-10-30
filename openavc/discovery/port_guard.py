"""THE rules every connection a scan opens follows, and the record of them.

One ``PortGuard`` per Discovery scan, and one per device audit's network
check. Three jobs:

- **Ports in use.** A project device whose driver is connected holds its
  ``(address, port)``, and nothing a scan does connects to it: not the port
  scan, not a driver's identification check, not a discovery companion. Many
  displays and projectors serve one control connection at a time, and a new
  connection takes the port from the old one, often with no FIN or RST, so the
  driver keeps a socket that never answers again. The device is identified by
  its driver already, so leaving the port alone costs the scan nothing. The
  map is built when the scan starts (``DiscoveryEngine.held_connections``) by
  ``held_ports``, which the cloud diagnostics ``port_scan`` and ``tcp_check``,
  a device page's Test Connection and the AI's ``test_driver_command`` ask too
  before they open a port.
- **One connection per port.** ``hold(host, port)`` is a lock per address and
  port, held from connect to close by every connection the identification
  stage opens: the greeting read, each check, and a companion that asks
  through ``ProbeContext.hold_port``. A device that serves one session must
  never see two of them at once. ``asyncio.Lock`` is FIFO, so the checks on
  one port run in the order they were queued.
- **The record.** What each port received: whether its greeting was a login
  prompt, and for each check whether it was judged on that greeting, opened a
  connection of its own, or was not sent and why (``HOW_*``). ``record(host)``
  is what a discovered device reports as ``port_probes``.

``is_login_prompt`` is the one test for "this port is asking for a user name
or password". A check that sends bytes is never sent to such a port unless
what it looks for is that prompt (``probe_runner.expects_greeting``): another
vendor's query typed into a login is a failed login, and a device that locks
an account or raises an alarm after a few of those would do it because
OpenAVC looked for its driver.

Engine-loop only. A companion runs on its own loop in a worker thread, and its
holds hop to this loop through ``companion.run_companion``. Pure stdlib, but
for ``held_ports``, which looks host names up through ``network_scanner``.
"""

from __future__ import annotations

import asyncio
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator, Iterable, Mapping

# How a check reached its port (``PortCheck.how``).
HOW_GREETING = "greeting"          # judged on what the port said when connected to; no connection of its own
HOW_CONNECTED = "connected"        # opened a connection of its own (and sent its bytes, if it has any)
HOW_LOGIN_PROMPT = "login_prompt"  # not sent: the port asked for a login and the check does not expect one
HOW_IN_USE = "in_use"              # not sent: a connected project device holds the port
HOW_COMPANION = "companion"        # a driver's discovery companion held the port for a connection

# What the port said when connected to (``note_greeting``).
GREETING_LOGIN = "login_prompt"
GREETING_SPOKE = "spoke"
GREETING_SILENT = "silent"


class PortInUse(ConnectionRefusedError):
    """A connected project device holds this port, so nothing here opens it."""

    def __init__(self, host: str, port: int, holder: str) -> None:
        super().__init__(f"{host}:{port} is in use by {holder}")
        self.host = host
        self.port = port
        self.holder = holder


# Telnet option negotiation (IAC ...), stripped before the prompt is read:
# subnegotiation (IAC SB ... IAC SE), then three-byte option commands
# (WILL/WONT/DO/DONT + option), then two-byte commands.
_IAC_SUB = re.compile(rb"\xff\xfa.*?\xff\xf0", re.DOTALL)
_IAC_OPTION = re.compile(rb"\xff[\xfb-\xfe].")
_IAC_COMMAND = re.compile(rb"\xff[\xf0-\xf9]")
# The last line of the greeting names a user name or password and ends with a
# colon: "login:", "Username:", "User-name: ", "Enter password:".
_LOGIN_PROMPT = re.compile(
    r"(?:\buser\b|user[ _-]?name|\blog[ _-]?in\b|password|passcode|passwd)[^:]{0,12}:$",
    re.IGNORECASE,
)


async def held_ports(
    connections: Iterable[Any],
) -> tuple[dict[tuple[str, int], Any], list[Any]]:
    """The ports connected project devices hold, by address.

    ``connections`` are ``DeviceManager.live_connections()``: objects with
    ``host``, ``port`` and ``name``. Returns ``(address, port)`` -> the
    connection, with each host name looked up first since what gets dialed is
    an address, and the connections whose host name did not resolve, which
    nothing can recognise by address.
    """
    from openavc.discovery.network_scanner import resolve_address

    wanted = [
        c for c in connections
        if getattr(c, "host", "") and isinstance(getattr(c, "port", None), int)
    ]
    addresses = await asyncio.gather(*(resolve_address(c.host) for c in wanted))
    held: dict[tuple[str, int], Any] = {}
    unresolved: list[Any] = []
    for conn, address in zip(wanted, addresses):
        if address:
            held[(address, conn.port)] = conn
        else:
            unresolved.append(conn)
    return held, unresolved


def is_login_prompt(greeting: bytes) -> bool:
    """Whether what a port said when connected to ends asking for a login."""
    if not greeting:
        return False
    data = _IAC_COMMAND.sub(b"", _IAC_OPTION.sub(b"", _IAC_SUB.sub(b"", greeting)))
    text = data.decode("latin-1").replace("\x00", "")
    lines = [line.strip() for line in re.split(r"[\r\n]+", text) if line.strip()]
    return bool(lines) and bool(_LOGIN_PROMPT.search(lines[-1]))


def greeting_kind(greeting: bytes) -> str:
    """``GREETING_LOGIN``, ``GREETING_SPOKE`` or ``GREETING_SILENT``."""
    if is_login_prompt(greeting):
        return GREETING_LOGIN
    return GREETING_SPOKE if greeting else GREETING_SILENT


@dataclass
class PortCheck:
    """One identification check's turn at a port."""

    probe_id: str
    driver_id: str
    how: str
    matched: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "probe_id": self.probe_id,
            "driver_id": self.driver_id,
            "how": self.how,
        }
        if self.matched is not None:
            out["matched"] = self.matched
        return out


class PortGuard:
    """The ports one scan leaves alone, the one-at-a-time lock, the record.

    ``held`` maps ``(address, port)`` to the name of the project device whose
    driver is connected there. Addresses are compared as given; the engine
    resolves host names to addresses before building it.
    """

    def __init__(self, held: Mapping[tuple[str, int], str] | None = None) -> None:
        self._held: dict[tuple[str, int], str] = dict(held or {})
        self._locks: dict[tuple[str, int], asyncio.Lock] = {}
        self._in_use_noted: set[tuple[str, int]] = set()
        self._greetings: dict[tuple[str, int], str] = {}
        self._checks: dict[tuple[str, int], list[PortCheck]] = {}

    # --- Ports in use -----------------------------------------------------

    def held_by(self, host: str, port: int) -> str | None:
        """The project device whose driver holds ``host:port``, or None."""
        return self._held.get((host, port))

    def note_in_use(self, host: str, port: int) -> None:
        """Record that something here left ``host:port`` alone because it is
        held. The record names only the held ports a scan would have opened."""
        if (host, port) in self._held:
            self._in_use_noted.add((host, port))

    # --- One connection per port ------------------------------------------

    async def acquire(self, host: str, port: int) -> None:
        """Wait until no other connection here is open to ``host:port``."""
        lock = self._locks.get((host, port))
        if lock is None:
            lock = self._locks[(host, port)] = asyncio.Lock()
        await lock.acquire()

    def release(self, host: str, port: int) -> None:
        lock = self._locks.get((host, port))
        if lock is not None and lock.locked():
            lock.release()

    def is_held_open(self, host: str, port: int) -> bool:
        """Whether a connection here holds ``host:port`` right now."""
        lock = self._locks.get((host, port))
        return lock is not None and lock.locked()

    @asynccontextmanager
    async def hold(self, host: str, port: int) -> AsyncIterator[None]:
        """Hold ``host:port`` for one connection, connect to close.

        Raises ``PortInUse`` for a port a connected project device holds.
        """
        holder = self.held_by(host, port)
        if holder is not None:
            self.note_in_use(host, port)
            raise PortInUse(host, port, holder)
        await self.acquire(host, port)
        try:
            yield
        finally:
            self.release(host, port)

    # --- The record ---------------------------------------------------------

    def note_greeting(self, host: str, port: int, greeting: bytes) -> None:
        self._greetings[(host, port)] = greeting_kind(greeting)

    def note(self, host: str, port: int, check: PortCheck) -> None:
        self._checks.setdefault((host, port), []).append(check)

    def record(self, host: str) -> dict[int, dict[str, Any]]:
        """What each of ``host``'s ports received, by port number.

        Each port carries ``in_use_by`` (a held port, never connected to),
        ``greeting`` (what the port said when connected to, ``GREETING_*``)
        and ``probes`` (each check's ``PortCheck``, in the order they ran),
        whichever apply.
        """
        out: dict[int, dict[str, Any]] = {}
        for (h, port) in sorted(self._in_use_noted):
            if h == host:
                out.setdefault(port, {})["in_use_by"] = self._held[(h, port)]
        for (h, port), kind in self._greetings.items():
            if h == host:
                out.setdefault(port, {})["greeting"] = kind
        for (h, port), checks in self._checks.items():
            if h == host:
                out.setdefault(port, {})["probes"] = [c.to_dict() for c in checks]
        return dict(sorted(out.items()))
