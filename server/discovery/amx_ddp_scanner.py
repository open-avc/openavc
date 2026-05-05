"""AMX DDP (Dynamic Device Discovery / "AMX Beacon") listener.

Passive multicast listener on UDP 239.255.250.250:9131. Many third-party
AV devices (Polycom audio, Epson and NEC projectors, Sony displays)
emit a periodic AMXB beacon on this group every 30-60 seconds. The
beacon advertises Make / Model / Revision in a self-describing ASCII
payload, which makes it one of the most reliable Tier 1 signals
available in pro AV networks.

This is the same passive mechanism used by AMX NetLinx Studio and the
AMX Device Discovery white paper. Reference implementation studied
(format only): https://github.com/hisasan/amxdd (MIT-licensed).

Beacon format (single UDP datagram, ASCII):

    AMXB<-UUID=001122334455><-SDKClass=AudioConferencer>
        <-Make=Polycom><-Model=SoundStructureC16>
        <-Revision=1.0.0>[<Config-Name=...><Config-URL=http://...>]

Tags are ``<Key=Value>`` or ``<-Key=Value>`` (the leading dash flags
required vs optional in the spec; we treat them identically). The first
four bytes ``AMXB`` are the magic; everything after is concatenated
tag tokens.

The listener never sends — receiving the broadcast is sufficient to
identify the device. Source-IP binding (control interface) is honored
both at multicast-group join time and on the bind, so this listener
participates in the same network-safety primitives as ping_sweep and
mDNS.
"""

from __future__ import annotations

import asyncio
import logging
import re
import socket
import struct
from dataclasses import dataclass
from typing import Any

from server.discovery.result import Evidence
from server.discovery.tier_matcher import evidence_amx_ddp

log = logging.getLogger("discovery.amx_ddp")

DDP_GROUP = "239.255.250.250"
DDP_PORT = 9131

# Magic prefix that distinguishes AMX DDP from any other multicast traffic
# we might accidentally receive on the group.
_DDP_MAGIC = b"AMXB"

# Tag splitter: matches <Key=Value> or <-Key=Value>. Group 1 captures the
# key (without the optional leading dash); group 2 captures the value.
# Values may contain almost anything except the closing '>'.
_TAG_RE = re.compile(rb"<-?([A-Za-z][A-Za-z0-9_-]*)=([^>]*)>")


@dataclass
class DDPBeacon:
    """A parsed AMX DDP beacon."""

    ip: str
    raw: str
    fields: dict[str, str]

    @property
    def make(self) -> str | None:
        return self.fields.get("Make")

    @property
    def model(self) -> str | None:
        return self.fields.get("Model")

    @property
    def revision(self) -> str | None:
        return self.fields.get("Revision")

    @property
    def uuid(self) -> str | None:
        return self.fields.get("UUID")

    @property
    def sdk_class(self) -> str | None:
        return self.fields.get("SDKClass")

    @property
    def config_name(self) -> str | None:
        return self.fields.get("Config-Name")

    @property
    def config_url(self) -> str | None:
        return self.fields.get("Config-URL")

    def to_device_info(self) -> dict[str, Any]:
        """Map the beacon onto the legacy ``merge_device_info`` schema.

        Mirrors ``MDNSResult.to_device_info`` so the engine can ingest
        DDP results in the same flow as mDNS during the Phase 6 swap.
        """
        info: dict[str, Any] = {}
        if self.make:
            info["manufacturer"] = self.make
        if self.model:
            info["model"] = self.model
        if self.revision:
            info["firmware"] = self.revision
        if self.uuid:
            info["serial_number"] = self.uuid
        if self.config_name:
            info["device_name"] = self.config_name
        return info

    def to_evidence(self) -> Evidence:
        """Emit a Tier 1 Evidence record for the deterministic matcher."""
        ev = evidence_amx_ddp(
            make=self.make or "",
            model=self.model or "",
            raw=self.raw,
        )
        # Enrich evidence data with the full tag dict for the "Why?" UI.
        ev.data["fields"] = dict(self.fields)
        return ev


def parse_ddp_beacon(data: bytes, sender_ip: str) -> DDPBeacon | None:
    """Parse an AMXB datagram. Returns None if not a valid beacon."""
    if not data.startswith(_DDP_MAGIC):
        return None

    # Decode for the raw-text view; tags are extracted from the bytes
    # to avoid surprises from non-UTF-8 device names.
    try:
        raw = data.decode("utf-8", errors="replace")
    except Exception:
        raw = data.decode("ascii", errors="replace")

    fields: dict[str, str] = {}
    for match in _TAG_RE.finditer(data):
        key = match.group(1).decode("ascii", errors="replace")
        value = match.group(2).decode("utf-8", errors="replace")
        fields[key] = value

    if not fields:
        # Just the magic with no tags is not actionable.
        return None

    return DDPBeacon(ip=sender_ip, raw=raw, fields=fields)


class AMXDDPScanner:
    """Passive listener for AMX DDP multicast beacons.

    Listens for the entire scan window, accumulating beacons keyed by
    sender IP. The most-recently-seen beacon wins on duplicate IPs;
    devices typically emit identical beacons every 30-60s.
    """

    def __init__(self, control_ip: str = "") -> None:
        """``control_ip``: bind to this interface. Empty = default route."""
        self._sock: socket.socket | None = None
        self._running = False
        self._results: dict[str, DDPBeacon] = {}
        self._control_ip = control_ip

    @property
    def results(self) -> dict[str, DDPBeacon]:
        return dict(self._results)

    async def start(self, duration: float = 30.0) -> dict[str, DDPBeacon]:
        """Listen on the DDP multicast group for ``duration`` seconds."""
        self._results.clear()
        self._running = True

        try:
            self._sock = _create_ddp_socket(self._control_ip)
        except OSError as exc:
            log.warning("Could not create AMX DDP socket: %s", exc)
            return {}

        try:
            await self._listen(duration)
        except asyncio.CancelledError:
            log.debug("AMX DDP listener cancelled")
        except Exception:
            log.warning("AMX DDP listener error", exc_info=True)
        finally:
            self._running = False
            self._close_socket()

        log.info("AMX DDP scan found %d beaconing device(s)", len(self._results))
        return dict(self._results)

    async def stop(self) -> None:
        self._running = False
        self._close_socket()

    async def _listen(self, duration: float) -> None:
        if not self._sock:
            return

        loop = asyncio.get_event_loop()
        end_time = loop.time() + duration

        while self._running and loop.time() < end_time:
            remaining = end_time - loop.time()
            if remaining <= 0:
                break
            try:
                self._sock.settimeout(min(remaining, 0.5))
                data, addr = await loop.run_in_executor(
                    None, lambda: self._sock.recvfrom(4096),
                )
            except socket.timeout:
                continue
            except OSError:
                if self._running:
                    log.debug("AMX DDP socket error during listen", exc_info=True)
                break

            beacon = parse_ddp_beacon(data, addr[0])
            if beacon:
                self._results[addr[0]] = beacon
                log.debug(
                    "DDP beacon from %s: %s %s",
                    addr[0], beacon.make, beacon.model,
                )

    def _close_socket(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None


def _create_ddp_socket(control_ip: str = "") -> socket.socket:
    """Create a UDP socket subscribed to the AMX DDP multicast group.

    Cross-platform: Windows binds to ("", DDP_PORT); Linux binds to
    (DDP_GROUP, DDP_PORT) when available, falling back to ("", DDP_PORT).
    The ``control_ip`` controls which interface joins the multicast group;
    empty string means INADDR_ANY (all interfaces, OS default).
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    # Bind to receive on the well-known port. Binding to ("", port) on
    # both platforms and relying on IP_ADD_MEMBERSHIP for interface
    # selection is portable.
    sock.bind(("", DDP_PORT))

    # Join the multicast group on the chosen interface (or all interfaces).
    iface = control_ip or "0.0.0.0"
    mreq = struct.pack(
        "4s4s",
        socket.inet_aton(DDP_GROUP),
        socket.inet_aton(iface),
    )
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)

    sock.setblocking(False)
    return sock
