"""SNMPSimulator tests — the real transport against the real simulator.

The transport tests next door answer with a hand-built agent, so they prove
the controller half on its own. These drive the shipped simulator base with
the shipped transport over a loopback socket: if the codec were wrong in the
same way in both directions, the pair here would agree and the hand-built
agent would not.

The device is invented: a twelve-outlet rack widget under an unassigned
enterprise arc.
"""

from __future__ import annotations

import pytest

from openavc.core.connection_fault import NO_RESPONSE, ConnectionFaultError
from openavc.simulator.snmp_simulator import SNMPSimulator
from openavc.transport.snmp import SNMPTransport, SnmpError

WIDGET = "1.3.6.1.4.1.99999"
SYS_DESCR = "1.3.6.1.2.1.1.1.0"
OUTLET_NAME = f"{WIDGET}.2.1.2"
OUTLET_STATE = f"{WIDGET}.2.1.3"
LOAD_TENTHS = f"{WIDGET}.3.1.0"


class RackWidgetSimulator(SNMPSimulator):
    """Twelve outlets, so the table crosses the 9-to-10 index boundary that
    string-sorted OIDs get wrong."""

    SIMULATOR_INFO = {
        "driver_id": "acme_widget",
        "name": "Acme Rack Widget",
        "category": "power",
        "transport": "snmp",
    }

    READ_COMMUNITY = "public"
    WRITE_COMMUNITY = "private"

    OIDS = {
        SYS_DESCR: ("string", "Acme rack widget"),
        LOAD_TENTHS: ("gauge32", 3_100_000_000),
        **{
            f"{OUTLET_NAME}.{i}": ("string", f"outlet {i}")
            for i in range(1, 13)
        },
        **{
            f"{OUTLET_STATE}.{i}": ("integer", 1 if i % 2 else 2, True)
            for i in range(1, 13)
        },
    }


@pytest.fixture
async def widget():
    sim = RackWidgetSimulator("widget-1")
    await sim.start(0)
    # BaseSimulator.port is read-only and start(0) records the 0, so the
    # bound port comes off the socket.
    sim.bound_port = sim._udp_transport.get_extra_info("sockname")[1]
    try:
        yield sim
    finally:
        await sim.stop()


@pytest.fixture
async def snmp(widget):
    transport = SNMPTransport(
        host="127.0.0.1", port=widget.bound_port,
        community="public", write_community="private",
        timeout=0.5, retries=1, name="test",
    )
    await transport.open()
    try:
        yield transport
    finally:
        await transport.close()


@pytest.mark.asyncio
async def test_get_reads_the_declared_mib(snmp):
    answered = await snmp.get([SYS_DESCR, LOAD_TENTHS])

    assert answered[SYS_DESCR].value == "Acme rack widget"
    assert answered[LOAD_TENTHS].value == 3_100_000_000
    assert answered[LOAD_TENTHS].type == "gauge32"


@pytest.mark.asyncio
async def test_walk_returns_the_table_in_numeric_order(snmp):
    """Index 9 must come before index 10 — sorted as strings it doesn't,
    and a walk that gets this wrong silently truncates the table."""
    rows = await snmp.walk(OUTLET_NAME)

    assert [row.value for row in rows] == [f"outlet {i}" for i in range(1, 13)]


@pytest.mark.asyncio
async def test_set_applies_and_reads_back(snmp):
    await snmp.set([(f"{OUTLET_STATE}.4", "integer", 1)])

    assert await snmp.get_value(f"{OUTLET_STATE}.4") == 1


@pytest.mark.asyncio
async def test_a_read_only_oid_is_refused(snmp):
    with pytest.raises(SnmpError) as excinfo:
        await snmp.set([(f"{OUTLET_NAME}.1", "string", "renamed")])

    assert excinfo.value.error_name == "readOnly"
    assert excinfo.value.oid == f"{OUTLET_NAME}.1"


@pytest.mark.asyncio
async def test_an_unknown_oid_is_refused_on_write_and_absent_on_read(snmp):
    with pytest.raises(SnmpError) as excinfo:
        await snmp.set([(f"{WIDGET}.9.9.9", "integer", 1)])
    assert excinfo.value.error_name == "noAccess"

    assert await snmp.get_value(f"{WIDGET}.9.9.9") is None


@pytest.mark.asyncio
async def test_the_wrong_value_type_is_refused(snmp):
    with pytest.raises(SnmpError) as excinfo:
        await snmp.set([(f"{OUTLET_STATE}.1", "string", "on")])

    assert excinfo.value.error_name == "wrongType"


@pytest.mark.asyncio
async def test_a_multi_binding_set_applies_all_or_none(snmp, widget):
    """RFC 3416: one bad binding means none of them takes effect."""
    before = widget.oids[f"{OUTLET_STATE}.1"][1]

    with pytest.raises(SnmpError):
        await snmp.set([
            (f"{OUTLET_STATE}.1", "integer", 2),
            (f"{OUTLET_NAME}.1", "string", "renamed"),  # read-only
        ])

    assert widget.oids[f"{OUTLET_STATE}.1"][1] == before


@pytest.mark.asyncio
async def test_a_wrong_community_looks_like_an_unreachable_device(widget):
    """Real agents drop a bad-community datagram instead of answering, so
    the driver must see a connection fault rather than a refusal."""
    transport = SNMPTransport(
        host="127.0.0.1", port=widget.bound_port,
        community="wrong", timeout=0.3, retries=0, name="test",
    )
    await transport.open()
    try:
        with pytest.raises(ConnectionFaultError) as excinfo:
            await transport.get(SYS_DESCR)
        assert excinfo.value.fault_code == NO_RESPONSE
    finally:
        await transport.close()


@pytest.mark.asyncio
async def test_a_set_under_the_read_community_is_ignored(widget):
    transport = SNMPTransport(
        host="127.0.0.1", port=widget.bound_port,
        community="public", write_community="public",
        timeout=0.3, retries=0, name="test",
    )
    await transport.open()
    try:
        with pytest.raises(ConnectionFaultError):
            await transport.set([(f"{OUTLET_STATE}.1", "integer", 2)])
    finally:
        await transport.close()


def test_each_simulator_instance_owns_its_mib():
    """A write to one simulated device must not reach every other one — the
    OIDS class attribute is a template, not shared state."""
    first = RackWidgetSimulator("widget-1")
    second = RackWidgetSimulator("widget-2")

    first.write_oid(f"{OUTLET_STATE}.1", "integer", 2)

    assert first.oids[f"{OUTLET_STATE}.1"][1] == 2
    assert second.oids[f"{OUTLET_STATE}.1"][1] == 1
    assert RackWidgetSimulator.OIDS[f"{OUTLET_STATE}.1"][1] == 1
