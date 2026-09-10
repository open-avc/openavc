"""
OpenAVC SNMP Transport — SNMP v2c control client over UDP.

Wraps `UDPTransport` and adds the request/response half of SNMP: request-id
correlation, retries (UDP loses datagrams and SNMP has no other recovery),
typed values on the way out and back, and an agent's refusal turned into a
sentence the integrator can act on. The wire codec is `snmp_codec.py`, shared
with the discovery scanner so there is one BER implementation.

Same shape as `osc.py` next door: a codec leaf plus a thin transport over UDP.

**v2c only.** v3 (authentication and privacy) is not implemented. The AV and
rack gear this exists for — PDUs, UPSes, managed switches, projectors with a
MIB — ships v2c, and v3 is a USM key-derivation layer that belongs in its own
change rather than half-built here.

Two failure shapes, deliberately kept apart:

  - **No answer.** The agent is unreachable, the datagram was lost, or the
    community string is wrong — a v2c agent usually answers a bad community
    with silence rather than an error, so these are indistinguishable on the
    wire. Raised as `ConnectionFaultError(code=NO_RESPONSE)` after the last
    retry, which is the platform's own "device isn't talking" path.
  - **A refusal.** The agent answered and said no: read-only OID, wrong type,
    not writable, not authorized. Raised as `SnmpError`, which carries the
    RFC 3416 error-status and its sentence. This is a command fault, not a
    connection fault — the device is up and healthy and the request was wrong.

A missing OID is neither: a v2c agent answers one with a `noSuchObject` /
`noSuchInstance` marker in the varbind and `noError` on the PDU. Those come
back as ordinary varbinds with `is_exception` set, because asking for five
OIDs and getting four is a normal result on a device whose MIB varies by
model — the caller decides whether it matters.
"""

from __future__ import annotations

import asyncio
import random

from openavc.core.connection_fault import NO_RESPONSE, ConnectionFaultError
from openavc.transport.snmp_codec import (
    SNMP_PORT,
    SnmpMessage,
    SnmpVarBind,
    build_snmp_get,
    build_snmp_getnext,
    build_snmp_set,
    parse_snmp_message,
)
from openavc.transport.udp import UDPTransport
from openavc.utils.logger import get_logger
from .types import Callback

log = get_logger(__name__)

# Request ids are 32-bit signed on the wire; keep well inside that so the
# BER encoder never needs a fifth byte.
_MAX_REQUEST_ID = 0x7FFFFFFF

# A walk that never terminates would poll forever against a broken agent.
DEFAULT_WALK_LIMIT = 512


class SnmpError(Exception):
    """An SNMP agent answered and refused the request.

    Carries the RFC 3416 error-status, its name, and the varbind index the
    agent blamed (1-based, 0 when it blamed none). `str(exc)` is the sentence
    meant for the user.
    """

    def __init__(self, message: str, *, error_status: int, error_name: str,
                 error_index: int = 0, oid: str = "") -> None:
        super().__init__(message)
        self.error_status = error_status
        self.error_name = error_name
        self.error_index = error_index
        self.oid = oid


class SNMPTransport:
    """Async SNMP v2c client for control and monitoring."""

    def __init__(
        self,
        host: str,
        port: int = SNMP_PORT,
        community: str = "public",
        write_community: str | None = None,
        timeout: float = 2.0,
        retries: int = 1,
        on_disconnect: Callback[[], None] | None = None,
        inter_command_delay: float = 0.0,
        name: str | None = None,
    ) -> None:
        """
        Args:
            host: Agent IP or hostname.
            port: Agent UDP port (161 unless the device was moved).
            community: Read community string.
            write_community: Community string for SET. Defaults to
                `community`; most devices ship a separate write community
                (often `private`) and refuse writes under the read one.
            timeout: Seconds to wait for each response.
            retries: Extra attempts after the first goes unanswered. UDP
                drops datagrams silently and SNMP has no other recovery, so
                the default is 1 rather than 0.
            on_disconnect: Called when the socket errors (BaseDriver compat).
            inter_command_delay: Seconds between sends, for agents that
                fall over when polled hard.
            name: Label for log messages.
        """
        self.host = host
        self.port = port or SNMP_PORT
        self.community = community
        self.write_community = write_community or community
        self.timeout = timeout
        self.retries = max(0, int(retries))
        self._name = name or "snmp"

        self._udp = UDPTransport(
            host=host,
            port=self.port,
            on_disconnect=on_disconnect,
            inter_command_delay=inter_command_delay,
            name=self._name,
        )
        self._request_id = random.randint(0, _MAX_REQUEST_ID)

    # --- lifecycle -----------------------------------------------------

    async def open(self, local_addr: str | None = None) -> None:
        """Open the UDP socket. SNMP is connectionless, so this binds a
        local socket and nothing reaches the device until the first send."""
        await self._udp.open(local_addr=local_addr)

    async def close(self) -> None:
        await self._udp.close()

    @property
    def connected(self) -> bool:
        return self._udp.connected

    @property
    def is_open(self) -> bool:
        return self._udp.is_open

    @property
    def last_error(self) -> str:
        return self._udp.last_error

    # --- requests ------------------------------------------------------

    async def get(self, oids: str | list[str]) -> dict[str, SnmpVarBind]:
        """GET one or more OIDs. Returns the varbinds keyed by OID.

        An OID the agent cannot supply comes back as a varbind with
        `is_exception` set, not as a raised error.
        """
        oid_list = [oids] if isinstance(oids, str) else list(oids)
        message = await self._exchange(
            lambda rid: build_snmp_get(self.community, oid_list, rid),
            oid_list,
        )
        return message.by_oid()

    async def get_value(self, oid: str) -> int | str | None:
        """GET a single OID and return just its value.

        Returns None when the agent has no such object — the caller that
        needs to tell "absent" from "the value is genuinely empty" should
        use `get()` and read the varbind's type.
        """
        varbind = (await self.get(oid)).get(oid)
        if varbind is None or varbind.is_exception:
            return None
        return varbind.value

    async def get_next(self, oids: str | list[str]) -> dict[str, SnmpVarBind]:
        """GETNEXT — one step of a walk. Returns the varbinds keyed by the
        OID the agent answered with, which is the *next* one, not the one
        that was asked for."""
        oid_list = [oids] if isinstance(oids, str) else list(oids)
        message = await self._exchange(
            lambda rid: build_snmp_getnext(self.community, oid_list, rid),
            oid_list,
        )
        return message.by_oid()

    async def walk(
        self, root_oid: str, limit: int = DEFAULT_WALK_LIMIT,
    ) -> list[SnmpVarBind]:
        """Walk a subtree with repeated GETNEXTs, in agent order.

        Stops at the first OID outside `root_oid`, at `endOfMibView`, or
        after `limit` rows — a table on a large device is the reason to
        raise the limit, an agent that never leaves the subtree is the
        reason there is one.
        """
        rows: list[SnmpVarBind] = []
        prefix = root_oid if root_oid.endswith(".") else root_oid + "."
        current = root_oid
        for _ in range(limit):
            answered = await self.get_next(current)
            if not answered:
                break
            oid, varbind = next(iter(answered.items()))
            if varbind.is_exception or not oid.startswith(prefix):
                break
            rows.append(varbind)
            current = oid
        return rows

    async def set(
        self, bindings: list[tuple[str, str, int | str | bytes | None]],
    ) -> dict[str, SnmpVarBind]:
        """SET one or more OIDs, as (oid, type_name, value) triples.

        Type names are the MIB spelling — `integer`, `string`, `gauge32`,
        `ip_address`, `oid`. The agent echoes the values it applied, which
        is what comes back; a refusal raises `SnmpError`.

        A SET carrying several varbinds is applied atomically by the agent
        (RFC 3416): either all of them take effect or none does.
        """
        if not bindings:
            return {}
        oid_list = [oid for oid, _t, _v in bindings]
        message = await self._exchange(
            lambda rid: build_snmp_set(self.write_community, bindings, rid),
            oid_list,
        )
        return message.by_oid()

    # --- the exchange ---------------------------------------------------

    def _next_request_id(self) -> int:
        self._request_id = (self._request_id + 1) % _MAX_REQUEST_ID
        return self._request_id

    async def _exchange(self, build, oids: list[str]) -> SnmpMessage:
        """Send a built PDU and return the matching response.

        Retries the whole exchange when nothing answers or when what
        answers carries the wrong request-id — `UDPTransport.send_and_wait`
        serializes exchanges and clears the queue before each send, so a
        mismatch here means a late duplicate rather than a reply we are
        about to lose.
        """
        last_timeout: Exception | None = None

        for attempt in range(self.retries + 1):
            request_id = self._next_request_id()
            packet = build(request_id)
            try:
                raw = await self._udp.send_and_wait(packet, timeout=self.timeout)
            except asyncio.TimeoutError as exc:
                last_timeout = exc
                continue

            message = parse_snmp_message(raw)
            if message is None:
                log.warning(
                    "[%s] Unparseable SNMP datagram from %s (%d bytes)",
                    self._name, self.host, len(raw),
                )
                continue
            if message.request_id != request_id:
                log.debug(
                    "[%s] Ignoring SNMP reply with request-id %s (waiting for %s)",
                    self._name, message.request_id, request_id,
                )
                continue

            if not message.ok:
                blamed = ""
                if 1 <= message.error_index <= len(oids):
                    blamed = oids[message.error_index - 1]
                sentence = message.error_sentence
                if blamed:
                    sentence = f"{sentence} ({blamed})"
                raise SnmpError(
                    sentence,
                    error_status=message.error_status,
                    error_name=message.error_name,
                    error_index=message.error_index,
                    oid=blamed,
                )

            return message

        attempts = self.retries + 1
        raise ConnectionFaultError(
            f"No SNMP reply from {self.host}:{self.port} after "
            f"{attempts} attempt{'s' if attempts != 1 else ''}. Check that "
            f"SNMP is enabled on the device and that the community string "
            f"is correct.",
            code=NO_RESPONSE,
        ) from last_timeout
