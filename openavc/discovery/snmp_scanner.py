"""Lightweight SNMP v2c scanner for device discovery.

Custom implementation using raw UDP sockets + ASN.1/BER encoding.
No dependency on pysnmp — uses only stdlib (asyncio, socket, struct).

Queries standard MIB-II OIDs to identify devices:
  - sysDescr    (1.3.6.1.2.1.1.1.0) — Device description
  - sysName     (1.3.6.1.2.1.1.5.0) — Admin-assigned name
  - sysObjectID (1.3.6.1.2.1.1.2.0) — Vendor OID
  - sysContact  (1.3.6.1.2.1.1.4.0) — Contact info
  - sysLocation (1.3.6.1.2.1.1.6.0) — Physical location

References:
  - RFC 1157: SNMP v1
  - RFC 3416: SNMP v2c
  - ITU-T X.690: BER encoding rules
"""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass
from typing import Any

# The wire codec lives in the transport layer (`transport/snmp_codec.py`) so
# the discovery sweep and the control transport share one BER implementation.
# Re-exported here because this module was the codec's original home and is
# still the import path the discovery tests and callers use.
from openavc.transport.snmp_codec import (  # noqa: F401
    ASN1_INTEGER,
    ASN1_NULL,
    ASN1_OCTET_STRING,
    ASN1_OID,
    ASN1_SEQUENCE,
    SNMP_PORT,
    SNMP_VERSION_2C,
    SNMP_GET_REQUEST,
    SNMP_GET_RESPONSE,
    SNMP_GETNEXT_REQUEST,
    ber_decode_any_value,
    ber_decode_integer,
    ber_decode_length,
    ber_decode_oid,
    ber_decode_string,
    ber_encode_integer,
    ber_encode_length,
    ber_encode_null,
    ber_encode_oid,
    ber_encode_sequence,
    ber_encode_string,
    ber_encode_tagged,
    ber_skip_tlv,
    build_snmp_get,
    build_snmp_getnext,
    parse_snmp_request_id,
    parse_snmp_response,
)


log = logging.getLogger("discovery.snmp")

# Standard MIB-II OIDs
OIDS = {
    "sysDescr": "1.3.6.1.2.1.1.1.0",
    "sysName": "1.3.6.1.2.1.1.5.0",
    "sysObjectID": "1.3.6.1.2.1.1.2.0",
    "sysContact": "1.3.6.1.2.1.1.4.0",
    "sysLocation": "1.3.6.1.2.1.1.6.0",
}

# Entity MIB column prefixes (for Standard/Thorough depth — detailed hardware
# info). The instance OID is ``<column>.<entPhysicalIndex>``; the index of the
# top-level entity (chassis) is agent-assigned and NOT necessarily 1, so it's
# located at query time by walking entPhysicalContainedIn (RFC 6933: the
# top-most entity is the row whose entPhysicalContainedIn is 0).
ENTITY_COLUMNS = {
    "entPhysicalMfgName": "1.3.6.1.2.1.47.1.1.1.1.12",
    "entPhysicalModelName": "1.3.6.1.2.1.47.1.1.1.1.13",
    "entPhysicalSerialNum": "1.3.6.1.2.1.47.1.1.1.1.11",
    "entPhysicalHardwareRev": "1.3.6.1.2.1.47.1.1.1.1.8",
    "entPhysicalFirmwareRev": "1.3.6.1.2.1.47.1.1.1.1.9",
}
ENT_PHYSICAL_CONTAINED_IN = "1.3.6.1.2.1.47.1.1.1.1.4"

# Upper bound on entPhysicalContainedIn walk steps. The top-level entity is
# almost always among the first table rows (indexes ascend from the chassis),
# so this only guards against pathological agents with huge entity tables.
ENTITY_WALK_LIMIT = 64

# --- IANA Private Enterprise Number extraction ---
# sysObjectID format: 1.3.6.1.4.1.{PEN}.<rest>. Core does not ship a
# curated PEN→manufacturer table — drivers register PENs they care
# about via the ``snmp_pen:`` hint, which feeds the matcher's
# enrichment lookup. The plain integer extraction is a generic
# capability; the vendor binding is per-driver.


def extract_pen(sys_object_id: str) -> int | None:
    """Return the IANA Private Enterprise Number from a sysObjectID,
    or None if the OID isn't in the standard PEN form."""
    prefix = "1.3.6.1.4.1."
    if not sys_object_id.startswith(prefix):
        return None
    rest = sys_object_id[len(prefix):]
    pen_str = rest.split(".")[0] if rest else ""
    try:
        return int(pen_str)
    except ValueError:
        return None


# --- SNMP Result ---


@dataclass
class SNMPInfo:
    """SNMP information collected from a device."""
    sys_descr: str = ""
    sys_name: str = ""
    sys_object_id: str = ""
    sys_contact: str = ""
    sys_location: str = ""
    # Entity MIB fields (populated when entity_mib=True)
    entity_manufacturer: str = ""
    entity_model: str = ""
    entity_serial: str = ""
    entity_hardware_rev: str = ""
    entity_firmware_rev: str = ""

    def to_dict(self) -> dict[str, str]:
        d: dict[str, str] = {}
        if self.sys_descr:
            d["sysDescr"] = self.sys_descr
        if self.sys_name:
            d["sysName"] = self.sys_name
        if self.sys_object_id:
            d["sysObjectID"] = self.sys_object_id
        if self.sys_contact:
            d["sysContact"] = self.sys_contact
        if self.sys_location:
            d["sysLocation"] = self.sys_location
        if self.entity_manufacturer:
            d["entPhysicalMfgName"] = self.entity_manufacturer
        if self.entity_model:
            d["entPhysicalModelName"] = self.entity_model
        if self.entity_serial:
            d["entPhysicalSerialNum"] = self.entity_serial
        return d

    @property
    def pen(self) -> int | None:
        """Return the IANA Private Enterprise Number from sysObjectID, if any.

        sysObjectID format: ``1.3.6.1.4.1.<PEN>.<rest>``. Returns the
        PEN as an int when present; None otherwise. Used as an
        enrichment soft signal for the matcher; multiple drivers may
        register the same PEN, producing a ``possible`` state with a
        candidate list rather than a deterministic match.
        """
        return extract_pen(self.sys_object_id)

    def to_evidence(self):
        """Emit an enrichment Evidence record, or None if no PEN."""
        if self.pen is None:
            return None
        from openavc.discovery.tier_matcher import evidence_snmp_pen

        return evidence_snmp_pen(self.pen, sysdescr=self.sys_descr or None)

    def to_device_info(self) -> dict[str, Any]:
        """Convert to a dict suitable for merge_device_info().

        Core does not parse vendor strings out of sysDescr — that's
        fuzzy and inherently vendor-specific. Drivers contribute
        manufacturer recognition via ``manufacturer_alias:`` hints,
        and the engine's ``extract_vendor_strings`` finalize step
        lifts strings out of probe responses for the matcher. This
        method just surfaces the device's self-reported fields.
        """
        info: dict[str, Any] = {}

        if self.sys_name:
            info["device_name"] = self.sys_name
        if self.to_dict():
            info["snmp_info"] = self.to_dict()

        # Entity MIB fields are device self-report (entPhysical*) — not
        # vendor knowledge in core. They're authoritative when the
        # device populates them.
        if self.entity_manufacturer:
            info["manufacturer"] = self.entity_manufacturer
        if self.entity_model:
            info["model"] = self.entity_model
        if self.entity_serial:
            info["serial_number"] = self.entity_serial
        if self.entity_firmware_rev:
            info["firmware"] = self.entity_firmware_rev

        return info


# --- SNMP Scanner ---


class _SNMPQueryProtocol(asyncio.DatagramProtocol):
    """One-shot SNMP request/response exchange.

    The datagram endpoint is created with ``remote_addr`` so the socket is
    connected — the OS only delivers datagrams from the queried device's
    IP and port. On top of that, the response future only resolves for a
    datagram whose request-id matches the request; anything else (stale
    duplicates, spoofed datagrams that beat the source check) is dropped
    and the wait continues until the caller's timeout.
    """

    def __init__(
        self,
        packet: bytes,
        expected_request_id: int,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self._packet = packet
        self._expected_request_id = expected_request_id
        self.response: asyncio.Future[bytes] = loop.create_future()

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        transport.sendto(self._packet)  # type: ignore[attr-defined]

    def datagram_received(self, data: bytes, addr: tuple) -> None:
        if self.response.done():
            return
        if parse_snmp_request_id(data) != self._expected_request_id:
            log.debug("Dropping SNMP datagram with unexpected request-id from %s", addr)
            return
        self.response.set_result(data)

    def error_received(self, exc: Exception) -> None:
        # ICMP errors (port unreachable etc.) — fail fast instead of
        # waiting out the timeout.
        if not self.response.done():
            self.response.set_exception(exc)

    def connection_lost(self, exc: Exception | None) -> None:
        if exc is not None and not self.response.done():
            self.response.set_exception(exc)


class SNMPScanner:
    """SNMP v2c device scanner.

    Queries standard MIB-II OIDs to identify devices. Uses raw UDP sockets
    with custom BER encoding — no external dependencies.
    """

    DEFAULT_COMMUNITY = "public"

    def __init__(self) -> None:
        self._results: dict[str, SNMPInfo] = {}

    @property
    def results(self) -> dict[str, SNMPInfo]:
        return dict(self._results)

    async def query_device(
        self,
        ip: str,
        community: str = DEFAULT_COMMUNITY,
        timeout: float = 2.0,
        entity_mib: bool = False,
    ) -> SNMPInfo | None:
        """Query a single device for SNMP information.

        Args:
            entity_mib: If True, also query ENTITY-MIB OIDs for detailed
                hardware info (model, serial, manufacturer).

        Returns SNMPInfo if the device responded, None otherwise.
        """
        request_id = random.randint(1, 2**31 - 1)
        oid_list = list(OIDS.values())

        packet = build_snmp_get(community, oid_list, request_id)
        response = await self._udp_query(ip, packet, timeout, request_id)
        if not response:
            return None

        values = parse_snmp_response(response)
        if not values:
            return None

        # Map OID strings back to field names
        info = SNMPInfo()
        for name, oid_str in OIDS.items():
            val = values.get(oid_str, "")
            if val:
                if name == "sysDescr":
                    info.sys_descr = val
                elif name == "sysName":
                    info.sys_name = val
                elif name == "sysObjectID":
                    info.sys_object_id = val
                elif name == "sysContact":
                    info.sys_contact = val
                elif name == "sysLocation":
                    info.sys_location = val

        # Only return if we got at least one non-empty field
        if not (info.sys_descr or info.sys_name):
            return None

        # Query Entity MIB for richer hardware info
        if entity_mib:
            await self._query_entity_mib(ip, community, timeout, info)

        return info

    async def _query_entity_mib(
        self, ip: str, community: str, timeout: float, info: SNMPInfo,
    ) -> None:
        """Query Entity MIB OIDs and populate entity fields on info.

        The entPhysicalIndex of the top-level entity is agent-assigned
        (RFC 6933), so it's located by walking entPhysicalContainedIn for
        the row whose value is 0. Falls back to index 1 (the most common
        assignment) when the walk finds nothing.
        """
        index = await self._find_chassis_index(ip, community, timeout)
        if index is None:
            index = 1

        oid_map = {name: f"{prefix}.{index}" for name, prefix in ENTITY_COLUMNS.items()}
        request_id = random.randint(1, 2**31 - 1)
        packet = build_snmp_get(community, list(oid_map.values()), request_id)

        response = await self._udp_query(ip, packet, timeout, request_id)
        if not response:
            return

        values = parse_snmp_response(response)
        if not values:
            return

        for name, oid_str in oid_map.items():
            val = values.get(oid_str, "")
            if val:
                if name == "entPhysicalMfgName":
                    info.entity_manufacturer = val
                elif name == "entPhysicalModelName":
                    info.entity_model = val
                elif name == "entPhysicalSerialNum":
                    info.entity_serial = val
                elif name == "entPhysicalHardwareRev":
                    info.entity_hardware_rev = val
                elif name == "entPhysicalFirmwareRev":
                    info.entity_firmware_rev = val

    async def _find_chassis_index(
        self, ip: str, community: str, timeout: float,
    ) -> int | None:
        """Walk entPhysicalContainedIn to find the top-level entity's index.

        The top-most physical entity (the chassis) is the row whose
        entPhysicalContainedIn is 0. Returns its entPhysicalIndex, or None
        when the agent doesn't expose the column (no Entity MIB, walk left
        the column, or the bounded walk found no top-level row).
        """
        prefix = ENT_PHYSICAL_CONTAINED_IN + "."
        current_oid = ENT_PHYSICAL_CONTAINED_IN

        for _ in range(ENTITY_WALK_LIMIT):
            request_id = random.randint(1, 2**31 - 1)
            packet = build_snmp_getnext(community, [current_oid], request_id)
            response = await self._udp_query(ip, packet, timeout, request_id)
            if not response:
                return None

            values = parse_snmp_response(response)
            if not values:
                return None

            oid_str, value = next(iter(values.items()))
            if not oid_str.startswith(prefix):
                # Walked past the entPhysicalContainedIn column
                return None
            if value == "0":
                try:
                    return int(oid_str[len(prefix):])
                except ValueError:
                    return None
            current_oid = oid_str

        return None

    async def scan_devices(
        self,
        ips: list[str],
        community: str = DEFAULT_COMMUNITY,
        timeout: float = 2.0,
        concurrency: int = 20,
        entity_mib: bool = False,
    ) -> dict[str, SNMPInfo]:
        """Query multiple devices in parallel.

        Args:
            ips: List of IP addresses to query.
            community: SNMP community string.
            timeout: Per-request timeout in seconds (each SNMP exchange
                with a device gets this long to respond).
            concurrency: Max concurrent queries.
            entity_mib: If True, also query ENTITY-MIB for detailed hardware info.

        Returns:
            Dict of {ip: SNMPInfo} for devices that responded.
        """
        self._results.clear()
        sem = asyncio.Semaphore(concurrency)

        async def query_one(ip: str) -> None:
            async with sem:
                result = await self.query_device(ip, community, timeout, entity_mib=entity_mib)
                if result:
                    self._results[ip] = result

        await asyncio.gather(
            *[query_one(ip) for ip in ips],
            return_exceptions=True,
        )

        log.info("SNMP scan: %d/%d devices responded", len(self._results), len(ips))
        return dict(self._results)

    async def _udp_query(
        self,
        ip: str,
        packet: bytes,
        timeout: float,
        expected_request_id: int,
    ) -> bytes | None:
        """Send one SNMP request and wait for the matching response.

        Uses a connected UDP socket (RFC-compliant agents reply from
        port 161) so the OS rejects datagrams from other sources, and
        only accepts a response whose request-id matches. Runs entirely
        on the event loop — no worker threads, so cancellation and
        timeout cleanly close the socket. Returns the raw response
        bytes, or None on timeout/error.
        """
        loop = asyncio.get_running_loop()
        try:
            transport, protocol = await loop.create_datagram_endpoint(
                lambda: _SNMPQueryProtocol(packet, expected_request_id, loop),
                remote_addr=(ip, SNMP_PORT),
            )
        except OSError:
            return None

        try:
            return await asyncio.wait_for(protocol.response, timeout=timeout)
        except (asyncio.TimeoutError, OSError):
            return None
        finally:
            transport.close()
