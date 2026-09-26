"""The per-device traffic recorder and the transport taps that feed it.

Every platform transport reports what it moved under the device id it carries
as its name; readers get scrollback from a bounded ring and a live stream only
while subscribed. Secrets are masked when an entry is written out.
"""

from __future__ import annotations

import asyncio
import socket

import httpx
import pytest

from openavc.core.device_traffic import (
    BODY_LIMIT,
    RX,
    TX,
    DeviceTrafficRecorder,
    TrafficRedactor,
    get_traffic_recorder,
    header_pairs,
    serialize_entry,
)
from openavc.utils.log_redaction import get_secret_registry, redact_bytes


@pytest.fixture(autouse=True)
def clean_recorder():
    recorder = get_traffic_recorder()
    recorder.clear()
    yield recorder
    recorder.clear()
    get_secret_registry().clear()


def _entries(device_id: str) -> list:
    return get_traffic_recorder().get_recent(device_id)


# ---------------------------------------------------------------------------
# The recorder
# ---------------------------------------------------------------------------


def test_ring_keeps_the_newest_entries():
    rec = DeviceTrafficRecorder(ring_entries=3, ring_bytes=10_000)
    for i in range(5):
        rec.record("acme_widget", TX, f"CMD{i}".encode(), channel="tcp")
    kept = rec.get_recent("acme_widget")
    assert [e.data for e in kept] == [b"CMD2", b"CMD3", b"CMD4"]
    assert [e.data for e in rec.get_recent("acme_widget", 2)] == [b"CMD3", b"CMD4"]
    assert kept[0].seq < kept[1].seq < kept[2].seq


def test_ring_is_bounded_by_bytes_too():
    rec = DeviceTrafficRecorder(ring_entries=100, ring_bytes=100)
    for _ in range(5):
        rec.record("acme_widget", RX, b"x" * 40, channel="http")
    kept = rec.get_recent("acme_widget")
    assert len(kept) == 2
    assert sum(len(e.data) for e in kept) <= 100


def test_least_recently_used_ring_without_a_reader_goes_first():
    rec = DeviceTrafficRecorder(max_devices=2)
    rec.record("a", TX, b"1", channel="tcp")
    sub = rec.subscribe("a", callback=lambda e: None)
    rec.record("b", TX, b"2", channel="tcp")
    rec.record("c", TX, b"3", channel="tcp")
    # "a" is the oldest but has a reader; "b" goes instead.
    assert rec.get_recent("a") and rec.get_recent("c")
    assert rec.get_recent("b") == []
    rec.unsubscribe(sub)


def test_forget_drops_the_ring_but_keeps_readers():
    rec = DeviceTrafficRecorder()
    seen = []
    rec.subscribe("acme_widget", callback=seen.append)
    rec.record("acme_widget", TX, b"one", channel="tcp")
    rec.forget("acme_widget")
    assert rec.get_recent("acme_widget") == []
    rec.record("acme_widget", TX, b"two", channel="tcp")
    assert [e.data for e in seen] == [b"one", b"two"]


def test_nothing_is_recorded_without_a_name():
    rec = DeviceTrafficRecorder()
    assert rec.record("", TX, b"x", channel="tcp") is None


async def test_queue_subscriber_counts_what_it_dropped():
    rec = DeviceTrafficRecorder()
    sub = rec.subscribe("acme_widget", queue_size=2)
    for i in range(4):
        rec.record("acme_widget", RX, bytes([i]), channel="udp")
    assert sub.queue.qsize() == 2
    assert sub.dropped == 2
    assert (await sub.queue.get()).data == b"\x00"
    rec.unsubscribe(sub)
    rec.record("acme_widget", RX, b"late", channel="udp")
    assert sub.queue.qsize() == 1


def test_only_other_devices_traffic_does_not_reach_a_reader():
    rec = DeviceTrafficRecorder()
    seen = []
    rec.subscribe("acme_widget", callback=seen.append)
    rec.record("other_device", TX, b"x", channel="tcp")
    assert seen == []


def test_raw_chunks_reach_only_readers_that_asked():
    rec = DeviceTrafficRecorder()
    frames, chunks = [], []
    rec.subscribe("acme_widget", callback=frames.append)
    assert not rec.wants_chunks("acme_widget")
    rec.record_chunk("acme_widget", b"ignored", channel="tcp")
    sub = rec.subscribe("acme_widget", callback=chunks.append, chunks=True)
    assert rec.wants_chunks("acme_widget")
    rec.record_chunk("acme_widget", b"A\rB\r", channel="tcp")
    assert [e.data for e in chunks] == [b"A\rB\r"]
    assert chunks[0].chunk is True
    assert frames == []
    # Chunks are never kept in the ring.
    assert rec.get_recent("acme_widget") == []
    rec.unsubscribe(sub)
    assert not rec.wants_chunks("acme_widget")


def test_a_reader_that_unsubscribes_itself_does_not_break_delivery():
    rec = DeviceTrafficRecorder()
    got: list = []
    sub = None

    def once(entry):
        got.append(entry)
        rec.unsubscribe(sub)

    sub = rec.subscribe("acme_widget", callback=once)
    other: list = []
    rec.subscribe("acme_widget", callback=other.append)
    rec.record("acme_widget", TX, b"1", channel="tcp")
    rec.record("acme_widget", TX, b"2", channel="tcp")
    assert len(got) == 1 and len(other) == 2


# ---------------------------------------------------------------------------
# Writing entries out
# ---------------------------------------------------------------------------


def test_serialize_masks_the_devices_secrets_in_the_bytes():
    get_secret_registry().set_config_secrets("acme_widget", {"hunter22"})
    entry = get_traffic_recorder().record(
        "acme_widget", TX, b"LOGIN admin hunter22\r", channel="tcp",
    )
    out = serialize_entry(entry)
    assert "hunter22" not in out["text"]
    assert out["text"] == "LOGIN admin ***\r"
    # The hex is of the masked bytes, so it still decodes.
    assert bytes.fromhex(out["hex"]) == b"LOGIN admin ***\r"
    assert "hunter22".encode().hex() not in out["hex"]


def test_serialize_with_a_callers_own_secrets():
    entry = get_traffic_recorder().record(
        "audit-1", RX, b"\x02pw=s3cret!\x03", channel="serial",
        meta={"note": "s3cret! seen"},
    )
    out = serialize_entry(entry, TrafficRedactor({"s3cret!"}))
    assert out["text"] == "\x02pw=***\x03"
    assert out["meta"] == {"note": "*** seen"}
    assert out["direction"] == "rx" and out["channel"] == "serial"


def test_byte_redaction_keeps_word_boundaries():
    assert redact_bytes(b"republic public", {"public"}) == b"republic ***"
    assert redact_bytes(b"\x00public\xff", {"public"}) == b"\x00***\xff"


def test_authorization_headers_are_masked_at_capture():
    pairs = header_pairs({"Authorization": "Basic YWRtaW46aHVudGVyMjI=", "Accept": "*/*"})
    assert pairs == [["Authorization", "Basic ***"], ["Accept", "*/*"]]
    assert header_pairs({"Proxy-Authorization": "opaque"}) == [["Proxy-Authorization", "***"]]


# ---------------------------------------------------------------------------
# The taps
# ---------------------------------------------------------------------------


async def test_tcp_records_both_ways_and_chunks_when_asked():
    from openavc.transport.tcp import TCPTransport

    async def handle(reader, writer):
        await reader.readuntil(b"\r")
        writer.write(b"PWR=1\rVOL=5\r")
        await writer.drain()
        await asyncio.sleep(0.5)
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    chunks: list = []
    sub = get_traffic_recorder().subscribe("acme_widget", callback=chunks.append, chunks=True)
    got: list[bytes] = []
    transport = await TCPTransport.create(
        "127.0.0.1", port, on_data=got.append, on_disconnect=lambda: None,
        delimiter=b"\r", name="acme_widget",
    )
    try:
        await transport.send(b"PWR?\r")
        for _ in range(100):
            if len(got) == 2:
                break
            await asyncio.sleep(0.01)
    finally:
        await transport.close()
        server.close()
        get_traffic_recorder().unsubscribe(sub)

    kept = _entries("acme_widget")
    assert [(e.direction, e.data, e.channel) for e in kept] == [
        ("tx", b"PWR?\r", "tcp"), ("rx", b"PWR=1", "tcp"), ("rx", b"VOL=5", "tcp"),
    ]
    raw = [e for e in chunks if e.chunk]
    assert b"".join(e.data for e in raw) == b"PWR=1\rVOL=5\r"


async def test_tcp_without_a_name_records_nothing():
    from openavc.transport.tcp import TCPTransport

    async def handle(reader, writer):
        await reader.read(10)
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    transport = await TCPTransport.create(
        "127.0.0.1", port, on_data=lambda d: None, on_disconnect=lambda: None,
    )
    try:
        await transport.send(b"X\r")
    finally:
        await transport.close()
        server.close()
    assert _entries(f"127.0.0.1:{port}") == []


async def test_udp_records_datagrams_with_their_peer():
    from openavc.transport.udp import UDPTransport

    class Echo(asyncio.DatagramProtocol):
        def connection_made(self, transport):
            self.transport = transport

        def datagram_received(self, data, addr):
            self.transport.sendto(b"ACK " + data, addr)

    loop = asyncio.get_running_loop()
    server, _ = await loop.create_datagram_endpoint(Echo, local_addr=("127.0.0.1", 0))
    port = server.get_extra_info("sockname")[1]
    transport = UDPTransport(host="127.0.0.1", port=port, name="acme_udp")
    await transport.open(local_addr="127.0.0.1")
    try:
        reply = await transport.send_and_wait(b"PING", timeout=2.0)
    finally:
        await transport.close()
        server.close()
    assert reply == b"ACK PING"
    kept = _entries("acme_udp")
    assert [(e.direction, e.data) for e in kept] == [("tx", b"PING"), ("rx", b"ACK PING")]
    assert kept[0].meta == {"peer": f"127.0.0.1:{port}"}
    assert kept[1].meta["peer"].startswith("127.0.0.1:")


async def test_osc_and_snmp_record_on_their_own_channels():
    from openavc.transport.osc import OSCTransport
    from openavc.transport.snmp import SNMPTransport

    sink = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sink.bind(("127.0.0.1", 0))
    port = sink.getsockname()[1]
    osc = OSCTransport(host="127.0.0.1", port=port, name="acme_osc")
    await osc.open(local_addr="127.0.0.1")
    try:
        await osc.send(b"/ping\x00\x00\x00,\x00\x00\x00")
    finally:
        await osc.close()
    assert [(e.channel, e.direction) for e in _entries("acme_osc")] == [("osc", "tx")]

    snmp = SNMPTransport(host="127.0.0.1", port=port, community="acme-ro", timeout=0.2,
                         retries=0, name="acme_snmp")
    await snmp.open(local_addr="127.0.0.1")
    try:
        with pytest.raises(Exception):
            await snmp.get("1.3.6.1.2.1.1.1.0")
    finally:
        await snmp.close()
        sink.close()
    sent = _entries("acme_snmp")
    assert sent and sent[0].channel == "snmp" and b"acme-ro" in sent[0].data


async def test_serial_records_what_it_sends_and_delivers():
    from openavc.transport.serial_transport import SerialTransport

    got: list[bytes] = []
    transport = await SerialTransport.create(
        port="SIM:acme", baudrate=9600, on_data=got.append,
        on_disconnect=lambda: None, delimiter=b"\r", name="acme_serial",
    )
    try:
        await transport.send(b"POWR1\r")
        transport.sim_receive(b"OK\r")
        await asyncio.sleep(0.05)
    finally:
        await transport.close()
    kept = _entries("acme_serial")
    assert [(e.direction, e.data, e.channel) for e in kept] == [
        ("tx", b"POWR1\r", "serial"), ("rx", b"OK", "serial"),
    ]


async def test_http_records_request_response_and_failure():
    from openavc.transport.http_client import HTTPClientTransport

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/down":
            raise httpx.ConnectError("refused", request=request)
        return httpx.Response(200, headers={"content-type": "text/plain"},
                              content=b"power=on" + b"x" * (BODY_LIMIT + 10))

    t = HTTPClientTransport(base_url="http://device", name="acme_http")
    t._client = httpx.AsyncClient(
        base_url="http://device", transport=httpx.MockTransport(handler),
        headers={"Authorization": "Bearer tok-abcdef"},
    )
    try:
        await t.request("POST", "/api/power?x=1", json_body={"on": True})
        with pytest.raises(ConnectionError):
            await t.get("/down")
    finally:
        await t.close()

    tx, rx, failed_tx, failed = _entries("acme_http")
    assert tx.direction == "tx" and tx.channel == "http"
    assert tx.meta["method"] == "POST" and tx.meta["target"] == "/api/power?x=1"
    assert tx.data == b'{"on":true}'
    # httpx sends header names in lower case.
    assert ["authorization", "Bearer ***"] in tx.meta["headers"]
    assert rx.direction == "rx" and rx.meta["status"] == 200
    assert rx.data.startswith(b"power=on") and len(rx.data) == BODY_LIMIT
    assert rx.meta["truncated"] is True
    assert failed_tx.meta["target"] == "/down"
    assert failed.data == b"" and "refused" in failed.meta["error"]


async def test_ssh_and_mqtt_record_their_payloads():
    from openavc.transport.mqtt import MQTTTransport
    from openavc.transport.ssh import SSHTransport

    ssh = SSHTransport("127.0.0.1", 22, "admin", on_data=lambda d: None,
                       on_disconnect=lambda: None, name="acme_ssh")
    ssh._deliver(b"switch# ")

    class Stdin:
        def __init__(self):
            self.written = b""

        def write(self, data):
            self.written += data

        async def drain(self):
            return None

    class Proc:
        stdin = Stdin()

    ssh._proc = Proc()
    ssh._connected = True
    await ssh.send(b"show version\n")
    assert [(e.direction, e.data, e.channel) for e in _entries("acme_ssh")] == [
        ("rx", b"switch# ", "ssh"), ("tx", b"show version\n", "ssh"),
    ]

    class Client:
        is_connected = True

        def publish(self, topic, payload, qos=0, retain=False):
            pass

    mqtt = MQTTTransport("127.0.0.1", name="acme_mqtt")
    mqtt._client = Client()
    await mqtt.publish("acme/cmd", "on", qos=1)
    await mqtt._on_message(None, "acme/state", b"on", 0, None)
    kept = _entries("acme_mqtt")
    assert [(e.direction, e.data, e.meta["topic"]) for e in kept] == [
        ("tx", b"on", "acme/cmd"), ("rx", b"on", "acme/state"),
    ]


async def test_push_channels_record_what_arrives():
    from openavc.transport import http_listener, tcp_listener
    from openavc.transport.multicast_listener import MulticastSubscription, _PortListener

    listener = _PortListener(0)
    sub = MulticastSubscription(listener, "239.1.2.3", {"127.0.0.1"}, lambda d, a: None,
                                "acme_mc")
    listener.subscriptions.append(sub)
    listener.deliver(b"EVENT 1", ("127.0.0.1", 5000))
    listener.deliver(b"NOT MINE", ("10.9.9.9", 5000))
    assert [(e.channel, e.data) for e in _entries("acme_mc")] == [("multicast", b"EVENT 1")]

    got: list[bytes] = []
    # A fixed free port: an ephemeral bind on "" gets a different port per
    # address family on some systems.
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    tsub = await tcp_listener.subscribe(port, "127.0.0.1", lambda f, a: got.append(f), "acme_tl")
    try:
        _r, w = await asyncio.open_connection("127.0.0.1", port)
        w.write(b"NOTIFY")
        await w.drain()
        for _ in range(100):
            if got:
                break
            await asyncio.sleep(0.01)
        w.close()
    finally:
        await tsub.close()
    assert [(e.channel, e.data) for e in _entries("acme_tl")] == [("tcp_listener", b"NOTIFY")]

    hsub = await http_listener.subscribe("acme_hl", "127.0.0.1", lambda r: None, "acme_hl")
    try:
        status = await http_listener.dispatch(
            "acme_hl", "",
            http_listener.HTTPPushRequest(body=b"<e/>", method="NOTIFY",
                                          headers={"sid": "uuid:1"}, source_ip="127.0.0.1"),
        )
    finally:
        await hsub.close()
    assert status == 200
    (entry,) = _entries("acme_hl")
    assert entry.channel == "http_listener" and entry.data == b"<e/>"
    assert entry.meta["method"] == "NOTIFY" and ["sid", "uuid:1"] in entry.meta["headers"]

    # A subscription named for its log lines still records under its device.
    labelled = await http_listener.subscribe(
        "acme_hl2", "127.0.0.1", lambda r: None, "acme_hl2:events", label="events",
    )
    try:
        await http_listener.dispatch(
            "acme_hl2", "events",
            http_listener.HTTPPushRequest(body=b"<e/>", method="NOTIFY", headers={},
                                          source_ip="127.0.0.1"),
        )
    finally:
        await labelled.close()
    assert [e.meta["label"] for e in _entries("acme_hl2")] == ["events"]
    assert _entries("acme_hl2:events") == []


async def test_a_driver_that_owns_its_connection_reports_through_record_traffic():
    from openavc.core.event_bus import EventBus
    from openavc.core.state_store import StateStore
    from openavc.drivers.base import BaseDriver

    class AcmeSocketDriver(BaseDriver):
        DRIVER_INFO = {"id": "acme_socket", "name": "Acme", "transport": "tcp"}

        async def send_command(self, command, params=None):
            return None

    driver = AcmeSocketDriver("acme_ws", {}, StateStore(), EventBus())
    driver.record_traffic("tx", '{"op":"hello"}', meta={"kind": "text"})
    driver.record_traffic("rx", b"\x81\x05hello")
    kept = _entries("acme_ws")
    assert [(e.direction, e.channel) for e in kept] == [("tx", "driver"), ("rx", "driver")]
    assert kept[0].data == b'{"op":"hello"}' and kept[0].meta == {"kind": "text"}


async def test_removing_a_device_forgets_its_traffic():
    from openavc.core.device_manager import DeviceManager
    from openavc.core.event_bus import EventBus
    from openavc.core.state_store import StateStore

    get_traffic_recorder().record("acme_widget", TX, b"x", channel="tcp")
    manager = DeviceManager(StateStore(), EventBus())
    await manager.remove_device("acme_widget")
    assert _entries("acme_widget") == []
