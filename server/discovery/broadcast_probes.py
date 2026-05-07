"""Tier 2 broadcast probes — vendor-specific UDP probes that elicit
deterministic identifications.

Each probe sends one well-formed packet to a broadcast or multicast
address and listens for vendor-specific replies. A response is a 100%
identification: only PJLink Class 2 projectors answer ``%2SRCH``, only
Crestron devices answer the CIP 1-byte probe on UDP 41794. There is
no scoring — if a probe responds, the device is what the probe says
it is.

This module currently ships:
  - PJLink Class 2 SRCH (UDP 4352)
  - Crestron CIP discovery (UDP 41794)

The plan calls for additional Tier 2 probes (ONVIF WS-Discovery,
HiQnet, Symetrix) which will land in follow-up commits as their wire
formats are verified against Wireshark captures.

Network safety
--------------
Every probe binds its socket to the configured control interface IP
(``socket.bind((control_ip, 0))``) so on multi-homed hosts the SYN-
equivalent broadcast leaves through the right adapter and replies
return on the same path. Without this, the kernel may pick a Docker
or VPN source IP that gets dropped by ``rp_filter`` or iptables.

Probes never retry. Receiving zero replies on a subnet is a normal
outcome (no devices of that vendor on the LAN). Some old AV firmware
locks up on aggressive probing; one well-formed packet per scan is
sufficient and safe.

References
----------
PJLink Class 2 spec: https://pjlink.jbmia.or.jp/english/data_cl2/PJLink_5-1.pdf
Crestron CIP probe (Tenable PoC, BSD-style permissive):
    https://github.com/tenable/poc/blob/master/crestron/dge-100/discover_and_hostname_change.py
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import socket
from dataclasses import dataclass, field
from typing import Any

from server.discovery.result import Evidence
from server.discovery.tier_matcher import evidence_broadcast

log = logging.getLogger("discovery.broadcast")


# ===========================================================================
# PJLink Class 2 SRCH (UDP 4352)
# ===========================================================================

PJLINK_PORT = 4352
PJLINK_SRCH_REQUEST = b"%2SRCH\r"
# Spec response format: %2ACKN=<12 hex chars>\r — case insensitive,
# may include trailing whitespace from some firmware.
_PJLINK_ACKN_RE = re.compile(rb"%2ACKN=([0-9a-fA-F]{12})\b")


@dataclass
class PJLinkClass2Reply:
    """A response from a PJLink Class 2 projector."""

    ip: str
    mac: str  # 12 hex characters, lowercase, no separators

    def to_evidence(self) -> Evidence:
        return evidence_broadcast(
            "pjlink_class2",
            response={"mac": self.mac, "ip": self.ip},
        )

    def to_device_info(self) -> dict[str, Any]:
        info: dict[str, Any] = {"protocols": ["pjlink"]}
        if self.mac:
            # Format MAC as colon-separated for compatibility with the
            # legacy DiscoveredDevice.mac field.
            info["mac"] = ":".join(self.mac[i:i + 2] for i in range(0, 12, 2))
        return info


async def probe_pjlink_class2(
    subnets: list[str],
    duration: float = 30.0,
    control_ip: str = "",
) -> dict[str, PJLinkClass2Reply]:
    """Broadcast a PJLink Class 2 ``%2SRCH`` and collect replies.

    Args:
        subnets: CIDR ranges. The probe broadcasts to each subnet's
            directed broadcast address.
        duration: Listen window in seconds. Spec mandates randomized
            response delay so 30s is the recommended minimum.
        control_ip: Source IP for the socket. Empty = OS default.

    Returns:
        dict keyed by responder IP -> PJLinkClass2Reply.
    """
    targets = _broadcast_addresses_for(subnets)
    if not targets:
        return {}

    sock = _make_broadcast_socket(PJLINK_PORT, control_ip)
    if sock is None:
        return {}

    results: dict[str, PJLinkClass2Reply] = {}

    try:
        # Send SRCH to each subnet's broadcast address. Spec says single
        # SRCH per scan; never burst.
        loop = asyncio.get_event_loop()
        for bcast in targets:
            try:
                await loop.run_in_executor(
                    None,
                    lambda b=bcast: sock.sendto(PJLINK_SRCH_REQUEST, (b, PJLINK_PORT)),
                )
                log.debug("PJLink SRCH sent to %s:%d", bcast, PJLINK_PORT)
            except OSError as exc:
                log.debug("PJLink SRCH send to %s failed: %s", bcast, exc)

        # Listen for ACKN replies for the full duration.
        end = loop.time() + duration
        while loop.time() < end:
            remaining = end - loop.time()
            if remaining <= 0:
                break
            try:
                sock.settimeout(min(remaining, 0.5))
                data, addr = await loop.run_in_executor(
                    None, lambda: sock.recvfrom(2048),
                )
            except socket.timeout:
                continue
            except OSError as exc:
                log.debug("PJLink SRCH socket error: %s", exc)
                break

            reply = _parse_pjlink_ackn(data, addr[0])
            if reply:
                results[reply.ip] = reply
                log.debug("PJLink ACKN from %s mac=%s", reply.ip, reply.mac)
    finally:
        try:
            sock.close()
        except OSError:
            pass

    log.info("PJLink Class 2 SRCH found %d projector(s)", len(results))
    return results


def _parse_pjlink_ackn(data: bytes, sender_ip: str) -> PJLinkClass2Reply | None:
    """Parse a ``%2ACKN=<MAC>\\r`` reply. Returns None if invalid."""
    match = _PJLINK_ACKN_RE.search(data)
    if not match:
        return None
    mac_hex = match.group(1).decode("ascii", errors="replace").lower()
    if len(mac_hex) != 12:
        return None
    return PJLinkClass2Reply(ip=sender_ip, mac=mac_hex)


# ===========================================================================
# Crestron CIP discovery probe (UDP 41794)
# ===========================================================================

CRESTRON_CIP_PORT = 41794
# The minimal probe: a single 0x14 byte. Crestron devices reply with a
# fixed-format payload starting with 0x15 followed by metadata.
CRESTRON_CIP_PROBE = bytes([0x14])

# Response constants. Verified against Tenable PoC + Phenomite AMP-Research.
_CRESTRON_RESP_MAGIC = 0x15
# Hostname field starts ~offset 10, fixed 16 bytes, NUL-padded.
_CRESTRON_HOSTNAME_OFFSET = 10
_CRESTRON_HOSTNAME_LEN = 16
# Model / firmware appear later in the payload as ASCII; we extract
# everything printable past the hostname and split on NULs.


@dataclass
class CrestronCIPReply:
    """A response to the Crestron CIP discovery probe."""

    ip: str
    hostname: str = ""
    model: str = ""
    firmware: str = ""
    raw_payload: bytes = b""
    fields: list[str] = field(default_factory=list)

    def to_evidence(self) -> Evidence:
        return evidence_broadcast(
            "crestron_cip",
            response={
                "ip": self.ip,
                "hostname": self.hostname,
                "model": self.model,
                "firmware": self.firmware,
            },
        )

    def to_device_info(self) -> dict[str, Any]:
        info: dict[str, Any] = {
            "manufacturer": "Crestron",
            "category": "control",
            "protocols": ["crestron_cip"],
        }
        if self.hostname:
            info["device_name"] = self.hostname
            info["hostname"] = self.hostname
        if self.model:
            info["model"] = self.model
        if self.firmware:
            info["firmware"] = self.firmware
        return info


async def probe_crestron_cip(
    targets: list[str],
    duration: float = 5.0,
    control_ip: str = "",
) -> dict[str, CrestronCIPReply]:
    """Send the 1-byte CIP discovery probe and collect replies.

    Args:
        targets: IP addresses or directed-broadcast addresses to probe.
            Per-host unicast probes are reliable; subnet-broadcast probes
            also work for the same-LAN case but most modern Crestron
            firmware ignores broadcast.
        duration: Listen window after the last send.
        control_ip: Source IP for the socket. Empty = OS default.
    """
    if not targets:
        return {}

    sock = _make_broadcast_socket(CRESTRON_CIP_PORT, control_ip)
    if sock is None:
        return {}

    results: dict[str, CrestronCIPReply] = {}
    try:
        loop = asyncio.get_event_loop()
        for ip in targets:
            try:
                await loop.run_in_executor(
                    None,
                    lambda i=ip: sock.sendto(
                        CRESTRON_CIP_PROBE, (i, CRESTRON_CIP_PORT),
                    ),
                )
            except OSError as exc:
                log.debug("Crestron CIP send to %s failed: %s", ip, exc)
            # Spec / safety: 1 probe per host, with a small spacing so
            # Crestron-made devices that share a SAN aren't hit at once.
            await asyncio.sleep(0.02)

        end = loop.time() + duration
        while loop.time() < end:
            remaining = end - loop.time()
            if remaining <= 0:
                break
            try:
                sock.settimeout(min(remaining, 0.5))
                data, addr = await loop.run_in_executor(
                    None, lambda: sock.recvfrom(4096),
                )
            except socket.timeout:
                continue
            except OSError as exc:
                log.debug("Crestron CIP socket error: %s", exc)
                break

            reply = _parse_crestron_cip(data, addr[0])
            if reply:
                results[reply.ip] = reply
                log.debug(
                    "Crestron CIP reply from %s: hostname=%s model=%s",
                    reply.ip, reply.hostname, reply.model,
                )
    finally:
        try:
            sock.close()
        except OSError:
            pass

    log.info("Crestron CIP probe found %d device(s)", len(results))
    return results


def _parse_crestron_cip(data: bytes, sender_ip: str) -> CrestronCIPReply | None:
    """Parse a Crestron CIP discovery reply. Returns None on bad data."""
    if not data or data[0] != _CRESTRON_RESP_MAGIC:
        return None

    reply = CrestronCIPReply(ip=sender_ip, raw_payload=data)

    # Hostname: 16 bytes at fixed offset, NUL-padded.
    if len(data) >= _CRESTRON_HOSTNAME_OFFSET + _CRESTRON_HOSTNAME_LEN:
        hostname_bytes = data[
            _CRESTRON_HOSTNAME_OFFSET:
            _CRESTRON_HOSTNAME_OFFSET + _CRESTRON_HOSTNAME_LEN
        ]
        hostname = hostname_bytes.split(b"\x00", 1)[0].decode(
            "utf-8", errors="replace",
        ).strip()
        if hostname:
            reply.hostname = hostname

    # Everything past the hostname field is a sequence of NUL-terminated
    # ASCII fields: model, firmware version, build date, serial. Extract
    # any printable ASCII run of >= 3 chars.
    tail = data[_CRESTRON_HOSTNAME_OFFSET + _CRESTRON_HOSTNAME_LEN:]
    fields = [
        f.decode("ascii", errors="replace").strip()
        for f in tail.split(b"\x00")
        if 3 <= len(f) <= 64 and all(0x20 <= b < 0x7f for b in f)
    ]
    reply.fields = fields

    # First plausible model token: ASCII alphanumeric with optional dashes.
    for f in fields:
        if re.match(r"^[A-Z][A-Z0-9-]{2,}$", f):
            reply.model = f
            break

    # First plausible firmware version: starts with 'v' or digit, has dots.
    for f in fields:
        if re.match(r"^[vV]?\d+\.\d+", f):
            reply.firmware = f.lstrip("v").lstrip("V")
            break

    return reply


# ===========================================================================
# Shared socket setup
# ===========================================================================


def _make_broadcast_socket(port: int, control_ip: str = "") -> socket.socket | None:
    """Create a UDP socket bound to ``control_ip`` (or any) with broadcast on.

    Returns None if the socket cannot be created. The error is logged but
    not raised — a missing socket is a recoverable condition (the scan
    continues without that probe).
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        # Bind to the control interface when set so replies route back
        # through the right NIC on multi-homed hosts. Source port 0 lets
        # the OS assign an ephemeral port (we listen on the same socket).
        sock.bind((control_ip or "", 0))
        sock.setblocking(False)
        return sock
    except OSError as exc:
        log.warning("Could not create broadcast socket on port %d: %s", port, exc)
        return None


def _broadcast_addresses_for(subnets: list[str]) -> list[str]:
    """Return the directed broadcast address for each CIDR. Skips invalid CIDRs."""
    out: list[str] = []
    for cidr in subnets:
        try:
            net = ipaddress.IPv4Network(cidr, strict=False)
        except ValueError:
            log.debug("Skipping invalid CIDR for broadcast: %s", cidr)
            continue
        if net.prefixlen >= 31:
            # /31 and /32 don't have a meaningful broadcast address.
            continue
        out.append(str(net.broadcast_address))
    return out
