"""
SNMPSimulator — SNMP v2c agent base for device simulators.

A simulated SNMP device is a MIB, so a subclass supplies one instead of
writing a protocol handler: declare `OIDS` (or fill it at `setup()`) and the
base serves GET, GETNEXT and SET against it, including the walk a driver uses
to enumerate a table. The wire work is `transport/snmp_codec.py` — the same
codec the control transport uses, read from the other end.

    class RackWidgetSimulator(SNMPSimulator):
        READ_COMMUNITY = "public"
        WRITE_COMMUNITY = "private"

        OIDS = {
            "1.3.6.1.2.1.1.1.0": ("string", "Rack widget", False),
            "1.3.6.1.4.1.99999.2.1.3.1": ("integer", 1, True),
        }

Each entry is `(type_name, value, writable)`; a two-item entry is read-only.
A SET to a read-only OID answers `readOnly`, an unknown OID answers
`noSuchObject`, and a wrong community string is met with silence — all three
are what real agents do, and a driver that only ever meets a permissive
simulator is a driver whose error handling has never run.

Runs on the shared datagram server, so latency, jitter, drops and the
injected error modes apply here exactly as they do to the other UDP bases.
"""

from __future__ import annotations

import logging

from openavc.simulator.base import BaseSimulator
from openavc.simulator.datagram_server import DatagramServerMixin
from openavc.transport.snmp_codec import (
    ERROR_STATUS_NAMES,
    build_snmp_response,
    oid_sort_key,
    parse_snmp_request,
)

logger = logging.getLogger(__name__)

# RFC 3416 error-status values this base answers with.
_NO_ERROR = 0
_READ_ONLY = 4
_NO_ACCESS = 6
_WRONG_TYPE = 7


class SNMPSimulator(DatagramServerMixin, BaseSimulator):
    """SNMP v2c agent simulator. You supply a MIB; the base serves it."""

    #: OID -> (type_name, value) or (type_name, value, writable).
    #: Type names are the MIB spelling the codec uses: integer, string, oid,
    #: gauge32, counter32, counter64, timeticks, ip_address.
    OIDS: dict[str, tuple] = {}

    #: Community strings. A SET is accepted only under WRITE_COMMUNITY.
    READ_COMMUNITY = "public"
    WRITE_COMMUNITY = "private"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # Per-instance copy: the class attribute is a template, and a
        # simulator that a driver writes to must not mutate it for every
        # other instance in the process.
        self.oids: dict[str, tuple] = {
            oid: tuple(entry) for oid, entry in self.OIDS.items()
        }

    # ── The MIB ──

    def read_oid(self, oid: str) -> tuple[str, object] | None:
        """Return (type_name, value) for an OID, or None if it doesn't exist.

        Override to compute a value at read time (a load reading that drifts,
        an uptime counter) instead of storing it.
        """
        entry = self.oids.get(oid)
        if entry is None:
            return None
        return entry[0], entry[1]

    def write_oid(self, oid: str, type_name: str, value: object) -> int:
        """Apply a SET. Return an RFC 3416 error-status (0 = applied).

        Override for a device that validates values, or that does something
        when written (an outlet that takes a few seconds to switch).
        """
        entry = self.oids.get(oid)
        if entry is None:
            return _NO_ACCESS
        if len(entry) < 3 or not entry[2]:
            return _READ_ONLY
        if type_name != entry[0]:
            return _WRONG_TYPE
        self.oids[oid] = (entry[0], value, True)
        return _NO_ERROR

    def next_oid(self, after: str) -> str | None:
        """The next OID in agent order, for GETNEXT and walks."""
        key = oid_sort_key(after)
        candidates = [o for o in self.oids if oid_sort_key(o) > key]
        if not candidates:
            return None
        return min(candidates, key=oid_sort_key)

    # ── Request handling ──

    def handle_command(self, data: bytes) -> bytes | None:
        """Serve one SNMP request. Returns the response datagram, or None
        to stay silent (an unparseable datagram, or a wrong community)."""
        request = parse_snmp_request(data)
        if request is None:
            return None

        expected = (
            self.WRITE_COMMUNITY if request.is_set else self.READ_COMMUNITY
        )
        if request.community != expected:
            # Real agents drop a bad-community datagram rather than answering,
            # which is why a wrong community looks exactly like an unreachable
            # device to the driver.
            logger.debug(
                "SNMP request with community %r (expected %r) — ignored",
                request.community, expected,
            )
            return None

        if request.is_set:
            return self._handle_set(request)
        return self._handle_read(request)

    def _handle_read(self, request) -> bytes:
        answered: list[tuple[str, str, object]] = []
        for varbind in request.varbinds:
            oid = varbind.oid
            if request.is_get_next:
                nxt = self.next_oid(oid)
                if nxt is None:
                    answered.append((oid, "endOfMibView", None))
                    continue
                oid = nxt
            found = self.read_oid(oid)
            if found is None:
                answered.append((oid, "noSuchObject", None))
            else:
                answered.append((oid, found[0], found[1]))
        return build_snmp_response(
            request.request_id, request.community, answered,
        )

    def _handle_set(self, request) -> bytes:
        # RFC 3416: a SET is all-or-nothing, so every binding is checked
        # before any of them is applied.
        for index, varbind in enumerate(request.varbinds, start=1):
            entry = self.oids.get(varbind.oid)
            if entry is None:
                return self._refuse(request, _NO_ACCESS, index)
            if len(entry) < 3 or not entry[2]:
                return self._refuse(request, _READ_ONLY, index)
            if varbind.type != entry[0]:
                return self._refuse(request, _WRONG_TYPE, index)

        applied: list[tuple[str, str, object]] = []
        for index, varbind in enumerate(request.varbinds, start=1):
            status = self.write_oid(varbind.oid, varbind.type, varbind.value)
            if status != _NO_ERROR:
                return self._refuse(request, status, index)
            applied.append((varbind.oid, varbind.type, varbind.value))

        return build_snmp_response(
            request.request_id, request.community, applied,
        )

    def _refuse(self, request, error_status: int, error_index: int) -> bytes:
        logger.debug(
            "SNMP SET refused: %s at varbind %d",
            ERROR_STATUS_NAMES.get(error_status, error_status), error_index,
        )
        return build_snmp_response(
            request.request_id,
            request.community,
            [],
            error_status=error_status,
            error_index=error_index,
        )

    # ── Lifecycle ──

    async def start(self, port: int) -> None:
        await self.start_datagram_server(port)

    async def stop(self) -> None:
        await self.stop_datagram_server()
