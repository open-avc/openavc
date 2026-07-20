"""AMX DDP (Dynamic Device Discovery / "AMX Beacon") listener.

Passive multicast listener on UDP 239.255.250.250:9131. Many third-party
AV devices emit a periodic AMXB beacon on this group every 30-60
seconds. The beacon advertises Make / Model / Revision in a
self-describing ASCII payload — a high-confidence passive signal that
identifies the device without sending any traffic.

This is the same passive mechanism used by AMX NetLinx Studio and the
AMX Device Discovery white paper. Reference implementation studied
(format only): https://github.com/hisasan/amxdd (MIT-licensed).

Beacon format (single UDP datagram, ASCII):

    AMXB<-UUID=001122334455><-SDKClass=AudioConferencer>
        <-Make=AcmeCo><-Model=Foo-1234>
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
from dataclasses import dataclass
from typing import Any

from server.discovery.multicast import join_group_on_interfaces, set_shared_port_reuse
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
        DDP results in the same flow as mDNS.
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
        """Emit a passive_listener Evidence record for the deterministic matcher."""
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


# Flood guard: a hostile peer on the multicast VLAN can emit beacons cycling
# spoofed source addresses, and every distinct source costs a results entry
# for the whole scan window plus a DiscoveredDevice + WebSocket fan-out
# downstream. No real site has anywhere near this many AMX beacon sources;
# past the cap, new sources are dropped (already-seen ones keep updating).
MAX_BEACON_SOURCES = 512


class AMXDDPScanner:
    """Passive listener for AMX DDP multicast beacons.

    Listens for the entire scan window, accumulating beacons keyed by
    sender IP. The most-recently-seen beacon wins on duplicate IPs;
    devices typically emit identical beacons every 30-60s. Distinct
    sources are capped at MAX_BEACON_SOURCES per scan window.
    """

    def __init__(self, control_ip: str = "") -> None:
        """``control_ip``: bind to this interface. Empty = default route."""
        self._sock: socket.socket | None = None
        self._running = False
        self._results: dict[str, DDPBeacon] = {}
        self._control_ip = control_ip
        self._cap_warned = False
        # Environment failure that kept the listener from working at all —
        # surfaced as a scan warning (see MDNSScanner.env_error).
        self.env_error: str | None = None

    @property
    def results(self) -> dict[str, DDPBeacon]:
        return dict(self._results)

    async def start(self, duration: float = 30.0) -> dict[str, DDPBeacon]:
        """Listen on the DDP multicast group for ``duration`` seconds."""
        self._results.clear()
        self._running = True
        self._cap_warned = False
        self.env_error = None

        try:
            self._sock = _create_ddp_socket(self._control_ip)
        except OSError as exc:
            log.warning("Could not create AMX DDP socket: %s", exc)
            self.env_error = f"AMX DDP listener unavailable: {exc}"
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

            self._handle_datagram(data, addr[0])

    def _handle_datagram(self, data: bytes, sender_ip: str) -> None:
        """Parse one datagram and record the beacon, subject to the cap."""
        beacon = parse_ddp_beacon(data, sender_ip)
        if not beacon:
            return
        if sender_ip not in self._results and len(self._results) >= MAX_BEACON_SOURCES:
            if not self._cap_warned:
                self._cap_warned = True
                log.warning(
                    "AMX DDP listener hit the %d distinct-source cap; "
                    "ignoring new sources for the rest of the scan window",
                    MAX_BEACON_SOURCES,
                )
            return
        self._results[sender_ip] = beacon
        log.debug(
            "DDP beacon from %s: %s %s",
            sender_ip, beacon.make, beacon.model,
        )

    def _close_socket(self) -> None:
        """Safely close the socket.

        ``shutdown`` before ``close``: the listen loop runs ``recvfrom`` in
        the default executor, and closing the socket from another thread
        does not wake a thread blocked in ``recvfrom`` on Linux/Windows —
        only ``shutdown`` does. Without it, a cancel/stop leaves that pool
        thread parked until its socket timeout, briefly starving the shared
        executor when many scanners cancel at once. ``shutdown`` raises on
        an unconnected socket (harmless), so its error is ignored.
        """
        if self._sock is not None:
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None


def _create_ddp_socket(control_ip: str = "") -> socket.socket:
    """Create a UDP socket subscribed to the AMX DDP multicast group.

    Binding to ("", port) on both platforms and relying on
    IP_ADD_MEMBERSHIP for interface selection is portable. When
    ``control_ip`` is set the group is joined via that interface only;
    otherwise it is joined once per interface IP with INADDR_ANY as the
    fallback (see ``discovery.multicast``). Raises OSError when the group
    could not be joined on any interface.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    set_shared_port_reuse(sock)

    try:
        # Bind to receive on the well-known port.
        sock.bind(("", DDP_PORT))

        joined = join_group_on_interfaces(sock, DDP_GROUP, control_ip=control_ip)
        if not joined:
            raise OSError(f"could not join {DDP_GROUP} on any interface")

        sock.setblocking(False)
    except OSError:
        sock.close()
        raise

    return sock
