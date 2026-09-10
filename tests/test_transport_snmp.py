"""SNMP transport and codec tests.

The transport half runs against a real SNMP agent on a loopback socket
(`_FakeAgent` below) rather than a mock: the point of this transport is that
the bytes on the wire are SNMP, so the tests parse real request PDUs and
answer with real response PDUs. The agent is an invented device with
synthetic OIDs under an unassigned enterprise arc.
"""

from __future__ import annotations

import asyncio

import pytest

from openavc.core.connection_fault import NO_RESPONSE, ConnectionFaultError
from openavc.transport.snmp import SNMPTransport, SnmpError
from openavc.transport.snmp_codec import (
    ASN1_SEQUENCE,
    SNMP_GET_REQUEST,
    SNMP_GET_RESPONSE,
    SNMP_GETNEXT_REQUEST,
    SNMP_NO_SUCH_OBJECT,
    SNMP_SET_REQUEST,
    ber_decode_integer,
    ber_decode_length,
    ber_decode_oid,
    ber_decode_string,
    ber_encode_integer,
    ber_encode_length,
    ber_encode_oid,
    ber_encode_sequence,
    ber_encode_string,
    ber_encode_tagged,
    ber_decode_any_value,
    build_snmp_set,
    decode_typed_value,
    encode_typed_value,
    parse_snmp_message,
)

# Invented device: a rack widget with a small outlet table. 1.3.6.1.4.1.99999 is not an
# assigned enterprise number, so nothing here names a real product.
WIDGET = "1.3.6.1.4.1.99999"
SYS_DESCR = "1.3.6.1.2.1.1.1.0"
OUTLET_NAME = f"{WIDGET}.2.1.2"     # table column: outlet names
OUTLET_STATE = f"{WIDGET}.2.1.3"    # table column: 1 = on, 2 = off
LOAD_TENTHS = f"{WIDGET}.3.1.0"     # gauge32, tenths of an amp
ABSENT = f"{WIDGET}.9.9.9"


# --- codec ------------------------------------------------------------------


@pytest.mark.parametrize(
    "type_name,value",
    [
        ("integer", 42),
        ("integer", -7),
        ("string", "outlet 1"),
        ("oid", "1.3.6.1.4.1.99999.1"),
        ("gauge32", 0),
        ("gauge32", 128),
        ("counter32", 4_000_000_000),
        ("timeticks", 987_654),
        ("counter64", 18_000_000_000),
        ("ip_address", "192.0.2.11"),
    ],
)
def test_typed_value_round_trips(type_name, value):
    """Every writable type survives encode -> decode unchanged."""
    encoded = encode_typed_value(type_name, value)
    decoded_type, decoded_value, offset = decode_typed_value(encoded, 0)

    assert decoded_value == value
    assert offset == len(encoded)
    # gauge32 and unsigned32 are the same tag; the decoder names it gauge32.
    assert decoded_type == ("gauge32" if type_name == "unsigned32" else type_name)


def test_unsigned_types_decode_above_two_billion():
    """A Counter32 past 2^31 must not come back negative.

    This is the whole reason the typed decoder exists: BER INTEGER decoding is
    signed, and byte counters live in exactly this range.
    """
    encoded = encode_typed_value("counter32", 3_000_000_000)
    _type_name, value, _offset = decode_typed_value(encoded, 0)
    assert value == 3_000_000_000


def test_encode_rejects_unknown_type_and_negative_unsigned():
    with pytest.raises(ValueError, match="Unknown SNMP value type"):
        encode_typed_value("float64", 1)
    with pytest.raises(ValueError, match="cannot be negative"):
        encode_typed_value("gauge32", -1)


def test_display_decoder_still_returns_strings():
    """`ber_decode_any_value` delegates to the typed decoder but keeps its
    display contract — the discovery sweep reads these straight into a UI."""
    assert ber_decode_any_value(ber_encode_string("hello"), 0)[0] == "hello"
    assert ber_decode_any_value(ber_encode_integer(42), 0)[0] == "42"
    assert ber_decode_any_value(ber_encode_oid("1.3.6.1"), 0)[0] == "1.3.6.1"
    assert ber_decode_any_value(b"\x05\x00", 0)[0] == ""
    # ...and now reads an application type as a number instead of raw bytes.
    assert ber_decode_any_value(encode_typed_value("gauge32", 250), 0)[0] == "250"


def test_set_packet_is_a_set_pdu_with_the_value_inline():
    """A SET carries the typed value where a GET carries NULL."""
    packet = build_snmp_set("private", [(OUTLET_STATE + ".1", "integer", 2)], 7)
    assert SNMP_SET_REQUEST in packet
    assert b"private" in packet
    # The encoded INTEGER 2 appears; a GET would have a NULL (05 00) instead.
    assert encode_typed_value("integer", 2) in packet


def test_parse_rejects_a_non_snmp_datagram():
    assert parse_snmp_message(b"not snmp at all") is None
    assert parse_snmp_message(b"") is None


def test_parse_reads_error_status_and_sentence():
    pdu = ber_encode_tagged(SNMP_GET_RESPONSE, [
        ber_encode_integer(11),
        ber_encode_integer(17),   # notWritable
        ber_encode_integer(1),
        ber_encode_sequence([]),
    ])
    packet = ber_encode_sequence([
        ber_encode_integer(1), ber_encode_string("public"), pdu,
    ])

    message = parse_snmp_message(packet)

    assert message is not None
    assert not message.ok
    assert message.error_name == "notWritable"
    assert message.error_sentence == "That OID is not writable on this device."


# --- a real agent on a real socket ------------------------------------------


def _parse_request(data: bytes) -> tuple[int, int, str, list[tuple[str, str, object]]]:
    """Parse an SNMP request PDU: (pdu_type, request_id, community, varbinds).

    The transport builds these, so the test reads them the way an agent
    would — anything wrong with the framing shows up here.
    """
    offset = 0
    assert data[offset] == ASN1_SEQUENCE
    offset += 1
    _len, offset = ber_decode_length(data, offset)
    _version, offset = ber_decode_integer(data, offset)
    community, offset = ber_decode_string(data, offset)

    pdu_type = data[offset]
    offset += 1
    _pdu_len, offset = ber_decode_length(data, offset)
    request_id, offset = ber_decode_integer(data, offset)
    _err, offset = ber_decode_integer(data, offset)
    _idx, offset = ber_decode_integer(data, offset)

    assert data[offset] == ASN1_SEQUENCE
    offset += 1
    list_len, offset = ber_decode_length(data, offset)
    end = offset + list_len

    varbinds = []
    while offset < end:
        assert data[offset] == ASN1_SEQUENCE
        offset += 1
        _vb_len, offset = ber_decode_length(data, offset)
        oid, offset = ber_decode_oid(data, offset)
        type_name, value, offset = decode_typed_value(data, offset)
        varbinds.append((oid, type_name, value))

    return pdu_type, request_id, community, varbinds


def _response(request_id: int, community: str, varbinds, error_status=0,
              error_index=0) -> bytes:
    encoded = []
    for oid, type_name, value in varbinds:
        if type_name == "noSuchObject":
            value_bytes = bytes([SNMP_NO_SUCH_OBJECT]) + ber_encode_length(0)
        else:
            value_bytes = encode_typed_value(type_name, value)
        encoded.append(ber_encode_sequence([ber_encode_oid(oid), value_bytes]))

    pdu = ber_encode_tagged(SNMP_GET_RESPONSE, [
        ber_encode_integer(request_id),
        ber_encode_integer(error_status),
        ber_encode_integer(error_index),
        ber_encode_sequence(encoded),
    ])
    return ber_encode_sequence([
        ber_encode_integer(1), ber_encode_string(community), pdu,
    ])


class _FakeAgent(asyncio.DatagramProtocol):
    """A minimal SNMP v2c agent for an invented rack widget."""

    READ_COMMUNITY = "public"
    WRITE_COMMUNITY = "private"

    def __init__(self) -> None:
        self.transport = None
        self.outlets = {1: ("rack fan", 1), 2: ("spare", 2)}
        self.load = 3_100_000_000  # deliberately past 2^31
        self.silent = False
        self.stale_id_once = False
        self.requests = 0

    # The subtree, in agent (lexicographic) order — what a walk must see.
    def _table(self) -> list[tuple[str, str, object]]:
        rows = []
        for index, (name, _state) in sorted(self.outlets.items()):
            rows.append((f"{OUTLET_NAME}.{index}", "string", name))
        for index, (_name, state) in sorted(self.outlets.items()):
            rows.append((f"{OUTLET_STATE}.{index}", "integer", state))
        rows.append((LOAD_TENTHS, "gauge32", self.load))
        return rows

    def _lookup(self, oid: str):
        if oid == SYS_DESCR:
            return (oid, "string", "Invented rack widget")
        for row_oid, type_name, value in self._table():
            if row_oid == oid:
                return (row_oid, type_name, value)
        return (oid, "noSuchObject", None)

    def _next(self, oid: str):
        for row_oid, type_name, value in self._table():
            if row_oid > oid:
                return (row_oid, type_name, value)
        return (oid, "noSuchObject", None)

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data: bytes, addr) -> None:
        self.requests += 1
        if self.silent:
            return

        pdu_type, request_id, community, varbinds = _parse_request(data)

        if self.stale_id_once:
            # Answer once with somebody else's request-id; the transport must
            # not attribute it to the exchange in flight.
            self.stale_id_once = False
            self.transport.sendto(
                _response(request_id + 1000, community,
                          [(SYS_DESCR, "string", "wrong reply")]), addr)
            return

        if pdu_type == SNMP_SET_REQUEST:
            if community != self.WRITE_COMMUNITY:
                # notWritable under the read community.
                self.transport.sendto(
                    _response(request_id, community, [], 17, 1), addr)
                return
            applied = []
            for oid, type_name, value in varbinds:
                if oid.startswith(OUTLET_NAME + "."):
                    # The name column is read-only on this widget.
                    self.transport.sendto(
                        _response(request_id, community, [], 4, 1), addr)
                    return
                index = int(oid.rsplit(".", 1)[-1])
                name, _state = self.outlets[index]
                self.outlets[index] = (name, int(value))
                applied.append((oid, type_name, value))
            self.transport.sendto(_response(request_id, community, applied), addr)
            return

        lookup = self._next if pdu_type == SNMP_GETNEXT_REQUEST else self._lookup
        assert pdu_type in (SNMP_GET_REQUEST, SNMP_GETNEXT_REQUEST)
        answered = [lookup(oid) for oid, _t, _v in varbinds]
        self.transport.sendto(_response(request_id, community, answered), addr)


@pytest.fixture
async def agent():
    loop = asyncio.get_running_loop()
    transport, protocol = await loop.create_datagram_endpoint(
        _FakeAgent, local_addr=("127.0.0.1", 0),
    )
    protocol.port = transport.get_extra_info("sockname")[1]
    try:
        yield protocol
    finally:
        transport.close()


@pytest.fixture
async def snmp(agent):
    transport = SNMPTransport(
        host="127.0.0.1", port=agent.port,
        community="public", write_community="private",
        timeout=0.5, retries=1, name="test",
    )
    await transport.open()
    try:
        yield transport
    finally:
        await transport.close()


@pytest.mark.asyncio
async def test_get_reads_typed_values(snmp):
    answered = await snmp.get([SYS_DESCR, LOAD_TENTHS])

    assert answered[SYS_DESCR].value == "Invented rack widget"
    assert answered[SYS_DESCR].type == "string"
    # Past 2^31, over a real socket, end to end.
    assert answered[LOAD_TENTHS].value == 3_100_000_000
    assert answered[LOAD_TENTHS].type == "gauge32"


@pytest.mark.asyncio
async def test_get_value_returns_none_for_a_missing_oid(snmp):
    assert await snmp.get_value(ABSENT) is None


@pytest.mark.asyncio
async def test_a_missing_oid_is_a_varbind_not_an_error(snmp):
    """Asking for four OIDs and getting three is a normal SNMP result."""
    answered = await snmp.get([SYS_DESCR, ABSENT])

    assert answered[SYS_DESCR].value == "Invented rack widget"
    assert answered[ABSENT].is_exception
    assert answered[ABSENT].type == "noSuchObject"
    assert answered[ABSENT].value is None


@pytest.mark.asyncio
async def test_walk_enumerates_a_table_and_stops_at_the_subtree_edge(snmp):
    rows = await snmp.walk(OUTLET_NAME)

    assert [row.value for row in rows] == ["rack fan", "spare"]
    assert [row.oid for row in rows] == [
        f"{OUTLET_NAME}.1", f"{OUTLET_NAME}.2",
    ]


@pytest.mark.asyncio
async def test_set_applies_and_the_agent_echoes_it(snmp, agent):
    answered = await snmp.set([(f"{OUTLET_STATE}.2", "integer", 1)])

    assert agent.outlets[2] == ("spare", 1)
    assert answered[f"{OUTLET_STATE}.2"].value == 1


@pytest.mark.asyncio
async def test_a_read_only_oid_raises_with_the_agents_reason(snmp):
    with pytest.raises(SnmpError) as excinfo:
        await snmp.set([(f"{OUTLET_NAME}.1", "string", "renamed")])

    error = excinfo.value
    assert error.error_name == "readOnly"
    assert error.oid == f"{OUTLET_NAME}.1"
    assert "read-only" in str(error)


@pytest.mark.asyncio
async def test_the_write_community_is_the_one_used_for_set(agent):
    """A SET under the read community is refused — which is what proves the
    transport sends `write_community`, not `community`, on a SET."""
    transport = SNMPTransport(
        host="127.0.0.1", port=agent.port,
        community="public", write_community="public",
        timeout=0.5, retries=0, name="test",
    )
    await transport.open()
    try:
        with pytest.raises(SnmpError) as excinfo:
            await transport.set([(f"{OUTLET_STATE}.1", "integer", 2)])
        assert excinfo.value.error_name == "notWritable"
    finally:
        await transport.close()


@pytest.mark.asyncio
async def test_a_silent_agent_is_a_typed_connection_fault(snmp, agent):
    agent.silent = True

    with pytest.raises(ConnectionFaultError) as excinfo:
        await snmp.get(SYS_DESCR)

    assert excinfo.value.fault_code == NO_RESPONSE
    # One initial attempt plus one retry, both actually sent.
    assert agent.requests == 2


@pytest.mark.asyncio
async def test_a_reply_with_the_wrong_request_id_is_not_attributed(snmp, agent):
    """A late duplicate must not be read as this exchange's answer."""
    agent.stale_id_once = True

    answered = await snmp.get(SYS_DESCR)

    assert agent.requests == 2  # the mismatch forced the retry
    assert answered[SYS_DESCR].value == "Invented rack widget"
