"""Pins for how the auto-simulator sends what the device says unprompted.

Where a driver's ``push:`` block points the notifications (a multicast group,
an event stream, dial-back subscribers, webhook callbacks, a UDP listener
port, else the control link), the one channel a notification goes out on,
whether the device counts as pushing, the standard push of a changed value
and the warnings a push block that resolves to nothing logs. Recorded from
the code as it is, on an invented device.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from openavc.simulator.yaml_auto import YAMLAutoSimulator

LOGGER = "openavc.simulator.yaml_auto"


def _def(**overrides) -> dict:
    d = {
        "id": "acme_widget",
        "name": "Acme Widget",
        "transport": "tcp",
        "delimiter": "\r",
        "default_config": {"port": 9000, "grp": "239.5.5.5", "gport": "7001",
                           "lport": "7002", "spath": "/feed"},
        "state_variables": {"level": {"type": "integer"}, "power": {"type": "boolean"}},
        "commands": {
            "subscribe": {"send": "SUB {listener_port} {token}"},
            "unsubscribe": {"method": "delete", "path": "/sub/{listener_port}"},
            "plain": {"send": "PLAIN"},
            "blank": {"send": ""},
        },
        "responses": [{"match": r"LVL(\d+)", "set": {"level": "$1"}}],
    }
    d.update(overrides)
    return d


def _sim(config=None, **overrides) -> YAMLAutoSimulator:
    return YAMLAutoSimulator("w1", config or {}, driver_def=_def(**overrides))


def _warnings(caplog):
    return [(r.name, r.levelname, r.getMessage()) for r in caplog.records
            if r.levelno >= logging.WARNING]


# ── where a push block points ──


def test_multicast_resolves_literal_and_config_fields(caplog):
    sim = _sim(push={"type": "multicast", "group": "239.1.2.3", "port": 5000})
    assert sim._push_multicast == ("239.1.2.3", 5000)
    sim = _sim({"gport": "7100"}, push={"type": "multicast", "group": "{grp}", "port": "{gport}"})
    assert sim._push_multicast == ("239.5.5.5", 7100)
    assert _warnings(caplog) == []


@pytest.mark.parametrize("block, shown", [
    ({"type": "multicast", "group": "10.0.0.1", "port": 5000}, "group='10.0.0.1' port=5000"),
    ({"type": "multicast", "group": "239.1.2.3", "port": "x"}, "group='239.1.2.3' port='x'"),
    ({"type": "multicast", "group": "239.1.2.3", "port": 70000}, "group='239.1.2.3' port=70000"),
    ({"type": "multicast", "group": "nonsense", "port": 5000}, "group='nonsense' port=5000"),
    ({"type": "multicast"}, "group='' port=None"),
])
def test_multicast_that_resolves_to_nothing_warns(caplog, block, shown):
    caplog.set_level(logging.WARNING, logger=LOGGER)
    sim = _sim(push=block)
    assert sim._push_multicast is None
    assert _warnings(caplog) == [(LOGGER, "WARNING",
        f"push block did not resolve to a multicast group/port ({shown}) "
        "— simulator will not emit notifications")]


def test_sse_paths_resolve_and_bad_ones_drop(caplog):
    caplog.set_level(logging.WARNING, logger=LOGGER)
    assert _sim(push={"type": "sse", "path": "/events"}).sse_paths == ["/events"]
    assert _sim(push={"type": "sse", "path": ["/a", "{spath}", "nope", " /b "]}).sse_paths == ["/a", "/feed", "/b"]
    assert _warnings(caplog) == []
    sim = _sim(push={"type": "sse", "path": "bad"})
    assert sim.sse_paths == []
    assert _warnings(caplog) == [(LOGGER, "WARNING",
        "push block did not resolve to event-stream path(s) (path='bad') "
        "— simulator will not emit notifications")]


def test_sse_session_is_copied_only_for_an_sse_block():
    sess = {"header": "X-Session"}
    sim = _sim(push={"type": "sse", "path": "/e", "session": sess})
    assert sim.sse_session == {"header": "X-Session"} and sim.sse_session is not sess
    assert _sim(push={"type": "sse", "path": "/e", "session": "x"}).sse_session is None
    assert _sim(push={"type": "multicast", "session": sess}).sse_session is None
    assert _sim().sse_session is None


def test_udp_listener_port_resolves(caplog):
    caplog.set_level(logging.WARNING, logger=LOGGER)
    assert _sim(push={"type": "udp_listener", "port": 6000})._push_udp_listener == 6000
    assert _sim(push={"type": "udp_listener", "port": "{lport}"})._push_udp_listener == 7002
    assert _sim(push={"type": "udp_listener", "port": " 6001 "})._push_udp_listener == 6001
    assert _warnings(caplog) == []
    assert _sim(push={"type": "udp_listener", "port": 0})._push_udp_listener is None
    assert _warnings(caplog) == [(LOGGER, "WARNING",
        "push block did not resolve to a UDP listener port (port=0) "
        "— simulator will not send notifications")]


def test_tcp_listener_matchers_come_from_the_named_commands():
    sim = _sim(push={"type": "tcp_listener", "register": "subscribe", "unregister": "unsubscribe",
                     "frame_parser": {"type": "struct_frame", "length_size": 2}})
    pt = sim._push_tcp
    assert pt["frame"] == {"type": "struct_frame", "length_size": 2}
    assert pt["register"].pattern == r"^SUB\ (?P<lport>\d+)\ [^&\s|]+\s*(?:\|.*)?$"
    assert pt["unregister"].pattern == r"^DELETE\ /sub/(?P<lport>\d+)\s*(?:\|.*)?$"
    sim = _sim(push={"type": "tcp_listener", "register": "blank", "unregister": "missing",
                     "frame_parser": "x"})
    assert sim._push_tcp == {"frame": None, "register": None, "unregister": None}
    sim = _sim(push={"type": "tcp_listener", "register": 5})
    assert sim._push_tcp == {"frame": None, "register": None, "unregister": None}


def test_http_listener_and_no_push():
    assert _sim(push={"type": "http_listener"})._push_http is True
    sim = _sim()
    assert (sim._push_multicast, sim.sse_paths, sim._push_tcp, sim._push_http,
            sim._push_udp_listener) == (None, [], None, False, None)
    sim = _sim(push="notadict")
    assert (sim._push_multicast, sim.sse_paths, sim._push_tcp, sim._push_http,
            sim._push_udp_listener) == (None, [], None, False, None)


# ── dial-back registrations ──


def test_a_registration_adds_and_an_unregistration_removes_the_subscriber():
    sim = _sim(push={"type": "tcp_listener", "register": "subscribe", "unregister": "unsubscribe"})
    sim._last_peer_ip = "10.1.1.9"
    assert sim._watch_push_registration("SUB 5555 tok") is True
    assert sim._push_tcp_subscribers == {("10.1.1.9", 5555): 0}
    sim._push_tcp_subscribers[("10.1.1.9", 5555)] = 2
    assert sim._watch_push_registration("SUB 5555 tok|body") is True
    assert sim._push_tcp_subscribers == {("10.1.1.9", 5555): 0}
    assert sim._watch_push_registration("DELETE /sub/5555") is True
    assert sim._push_tcp_subscribers == {}
    assert sim._watch_push_registration("PLAIN") is False
    log = [(e["direction"], e["data_text"]) for e in sim.get_protocol_log()]
    assert log == [("in", "(push subscriber registered: 10.1.1.9:5555)"),
                   ("in", "(push subscriber registered: 10.1.1.9:5555)"),
                   ("in", "(push subscriber removed: 10.1.1.9:5555)")]


def test_a_registration_with_no_port_counts_but_tracks_nothing():
    sim = _sim(push={"type": "tcp_listener", "register": "plain"})
    assert sim._watch_push_registration("PLAIN") is True
    assert sim._push_tcp_subscribers == {}
    assert sim._watch_push_registration("OTHER") is False
    assert _sim()._watch_push_registration("SUB 1 x") is False


def test_a_registration_command_with_no_handler_is_answered_empty():
    sim = _sim(push={"type": "tcp_listener", "register": "subscribe"})
    assert sim.handle_command(b"SUB 4000 abc") == b""
    assert ("127.0.0.1", 4000) in sim._push_tcp_subscribers
    assert sim.handle_command(b"NOPE") is None


def test_the_struct_frame_wraps_a_notification():
    sim = _sim(push={"type": "tcp_listener", "register": "subscribe", "frame_parser": {
        "type": "struct_frame", "length_size": 2, "length_endian": "little",
        "header_reserve": 2, "mid_reserve": 1, "trailer_reserve": 1, "length_adjust": 1}})
    assert sim._wrap_push_tcp_frame(b"abc") == b"\x00\x00\x02\x00\x00abc\x00"
    sim = _sim(push={"type": "tcp_listener", "frame_parser": {"type": "struct_frame", "length_adjust": 9}})
    assert sim._wrap_push_tcp_frame(b"abc") == b"\x00\x00abc"
    sim = _sim(push={"type": "tcp_listener", "frame_parser": {"type": "delimiter"}})
    assert sim._wrap_push_tcp_frame(b"abc") == b"abc"
    assert _sim()._wrap_push_tcp_frame(b"abc") == b"abc"


@pytest.mark.asyncio
async def test_a_subscriber_unreachable_three_times_is_dropped(caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)
    sim = _sim(push={"type": "tcp_listener", "register": "subscribe"})
    target = ("127.0.0.1", 1)

    async def refuse(*_a, **_k):
        raise OSError("refused")

    real = asyncio.open_connection
    asyncio.open_connection = refuse
    try:
        sim._push_tcp_subscribers[target] = 0
        await sim._dial_push_tcp(target, b"x")
        assert sim._push_tcp_subscribers == {target: 1}
        await sim._dial_push_tcp(target, b"x")
        assert sim._push_tcp_subscribers == {target: 2}
        await sim._dial_push_tcp(target, b"x")
        assert sim._push_tcp_subscribers == {}
        # gone meanwhile: nothing to count
        await sim._dial_push_tcp(target, b"x")
        assert sim._push_tcp_subscribers == {}
    finally:
        asyncio.open_connection = real
    infos = [r.getMessage() for r in caplog.records if r.name == LOGGER and r.levelno == logging.INFO]
    assert infos[-1] == "Acme Widget Simulator: push subscriber 127.0.0.1:1 unreachable 3 times — removed"


@pytest.mark.asyncio
async def test_a_delivered_notification_resets_the_failures():
    sim = _sim(push={"type": "tcp_listener", "register": "subscribe"})
    got = []

    async def handle(reader, writer):
        got.append(await reader.read(100))
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        sim._push_tcp_subscribers[("127.0.0.1", port)] = 2
        await sim._dial_push_tcp(("127.0.0.1", port), b"HELLO")
        await asyncio.sleep(0.05)
        assert sim._push_tcp_subscribers == {("127.0.0.1", port): 0}
        assert got == [b"HELLO"]
        assert [(e["direction"], e["data_text"]) for e in sim.get_protocol_log()] == [("out", "HELLO")]
        # a delivery to a target no longer subscribed adds nothing back
        sim._push_tcp_subscribers.clear()
        await sim._dial_push_tcp(("127.0.0.1", port), b"AGAIN")
        assert sim._push_tcp_subscribers == {}
    finally:
        server.close()
        await server.wait_closed()


# ── webhook callbacks ──


def test_callbacks_register_once_and_unregister():
    sim = _sim(push={"type": "http_listener"})
    sim.register_callback(" http://a/cb ")
    sim.register_callback("http://a/cb")
    sim.register_callback("")
    sim.register_callback(None)
    assert sim._http_push_callbacks == ["http://a/cb"]
    sim.unregister_callback("http://other")
    sim.unregister_callback(" http://a/cb")
    assert sim._http_push_callbacks == []
    assert [(e["direction"], e["data_text"]) for e in sim.get_protocol_log()] == [
        ("in", "(callback registered: http://a/cb)"),
        ("in", "(callback unregistered: http://a/cb)")]


@pytest.mark.parametrize("msg, ctype", [
    ("<x/>", "text/xml"), ("  <x/>", "text/xml"), ('{"a":1}', "application/json"),
    ("[1]", "application/json"), ("plain", "text/plain"), ("", "text/plain"),
])
def test_callback_content_type(msg, ctype):
    assert YAMLAutoSimulator._callback_content_type(msg) == ctype


# ── the one channel a notification goes out on ──


class _Recorder:
    def __init__(self, sim):
        self.events = []
        sim.push = self.push
        sim.push_datagram = lambda data, addr=None: self.events.append(("dgram", data))
        sim.send_to_listener = lambda data, port: self.events.append(("listener", data, port))
        sim.push_sse_event = lambda data, event=None: self.events.append(("sse", data))
        sim.post_http_callback = self.post
        sim._dial_push_tcp = self.dial

    async def push(self, data):
        self.events.append(("push", data))

    async def post(self, url, msg, headers=None):
        self.events.append(("post", url, msg, headers))

    async def dial(self, target, framed):
        self.events.append(("dial", target, framed))


class _Sock:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    def sendto(self, data, addr):
        if self.fail:
            raise OSError("down")
        self.sent.append((data, addr))

    def close(self):
        self.closed = True


async def _deliver(sim, msg="N1"):
    sim._deliver_notification(msg)
    for _ in range(3):
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_each_channel_carries_the_notification_its_way():
    sim = _sim(push={"type": "multicast", "group": "239.1.2.3", "port": 5000})
    rec = _Recorder(sim)
    sim._mcast_sock = _Sock()
    await _deliver(sim)
    assert sim._mcast_sock.sent == [(b"N1\r", ("239.1.2.3", 5000))] and rec.events == []

    sim = _sim(push={"type": "sse", "path": "/e"})
    rec = _Recorder(sim)
    await _deliver(sim)
    assert rec.events == [("sse", "N1")]

    sim = _sim(push={"type": "tcp_listener", "register": "subscribe"})
    rec = _Recorder(sim)
    await _deliver(sim)
    assert rec.events == []
    sim._push_tcp_subscribers[("127.0.0.1", 9)] = 0
    await _deliver(sim)
    assert rec.events == [("dial", ("127.0.0.1", 9), b"N1\r")]

    sim = _sim(push={"type": "http_listener"})
    rec = _Recorder(sim)
    await _deliver(sim)
    assert rec.events == []
    sim._http_push_callbacks[:] = ["http://a", "http://b"]
    await _deliver(sim, "<x/>")
    assert rec.events == [("post", "http://a", "<x/>", {"Content-Type": "text/xml"}),
                          ("post", "http://b", "<x/>", {"Content-Type": "text/xml"})]

    sim = _sim(push={"type": "udp_listener", "port": 6000})
    rec = _Recorder(sim)
    await _deliver(sim)
    assert rec.events == [("listener", b"N1\r", 6000)]


@pytest.mark.asyncio
async def test_with_no_push_block_the_control_link_carries_it():
    sim = _sim()
    rec = _Recorder(sim)
    await _deliver(sim)
    assert rec.events == []
    sim._clients = {"c": None}
    await _deliver(sim)
    assert rec.events == [("push", b"N1\r")]

    sim = _sim(transport="udp")
    rec = _Recorder(sim)
    await _deliver(sim)
    assert rec.events == [("dgram", b"N1\r")]

    for transport in ("http", "osc"):
        sim = _sim(transport=transport)
        rec = _Recorder(sim)
        sim._clients = {"c": None}
        await _deliver(sim)
        assert rec.events == ([("push", b"N1")] if transport == "http" else [("push", b"N1\r")])


@pytest.mark.asyncio
async def test_the_first_declared_channel_wins():
    sim = _sim(push={"type": "udp_listener", "port": 6000})
    rec = _Recorder(sim)
    sim._clients = {"c": None}
    sim._http_push_callbacks.append("http://a")
    sim._push_http = True
    await _deliver(sim, "M")
    assert [e[0] for e in rec.events] == ["post"]
    sim._push_tcp = {"frame": None, "register": None, "unregister": None}
    rec.events.clear()
    await _deliver(sim, "M")
    assert rec.events == []          # dial-back, nobody subscribed
    sim.sse_paths = ["/e"]
    await _deliver(sim, "M")
    assert rec.events == [("sse", "M")]
    sim._push_multicast = ("239.1.2.3", 5000)
    sim._mcast_sock = None
    rec.events.clear()
    await _deliver(sim, "M")
    assert rec.events == []          # multicast, socket not open


@pytest.mark.asyncio
async def test_a_silenced_or_failing_multicast_sends_nothing(caplog):
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    sim = _sim(push={"type": "multicast", "group": "239.1.2.3", "port": 5000})
    sim._mcast_sock = _Sock(fail=True)
    await _deliver(sim)
    assert [r.getMessage() for r in caplog.records if r.name == LOGGER and r.levelno == logging.DEBUG][-1] \
        == "Multicast notification send failed"
    assert sim.get_protocol_log() == []
    sim._mcast_sock = _Sock()
    sim.inject_error("communication_timeout")
    await _deliver(sim)
    assert sim._mcast_sock.sent == []


@pytest.mark.parametrize("transport, push, expected", [
    ("tcp", None, True), ("udp", None, True), ("http", None, False), ("osc", None, False),
    ("http", {"type": "sse", "path": "/e"}, True),
    ("http", {"type": "http_listener"}, True),
    ("http", {"type": "udp_listener", "port": 6000}, True),
    ("http", {"type": "multicast", "group": "239.1.2.3", "port": 5000}, True),
    ("osc", {"type": "tcp_listener", "register": "subscribe"}, True),
    ("http", {"type": "multicast", "group": "1.2.3.4", "port": 5000}, False),
])
def test_whether_a_notification_can_reach_the_driver(transport, push, expected):
    extra = {"push": push} if push else {}
    sim = _sim(transport=transport, simulator={"notifications": {"level": "L{value}"}}, **extra)
    assert sim._sends_notifications() is expected
    assert sim.to_info_dict()["push_state"] is expected
    sim = _sim(transport=transport, **extra)
    assert sim._sends_notifications() is False


# ── a state change pushed ──


@pytest.mark.asyncio
async def test_a_changed_value_pushes_its_reply_on_the_control_link():
    sim = _sim(simulator={"push_state": True})
    rec = _Recorder(sim)
    sim._clients = {"c": None}
    sim.set_state("level", 5)
    await asyncio.sleep(0)
    assert rec.events == [("push", b"LVL5\r")]
    rec.events.clear()
    sim.set_state("level", 5)          # unchanged: nothing
    sim.set_state("power", True)       # no reply rule: nothing
    sim._handling_command = True
    sim.set_state("level", 6)          # answering a command: nothing
    sim._handling_command = False
    await asyncio.sleep(0)
    assert rec.events == []
    sim._clients = {}
    sim.set_state("level", 7)
    await asyncio.sleep(0)
    assert rec.events == []


@pytest.mark.asyncio
async def test_a_udp_device_pushes_to_the_last_peer():
    sim = _sim(transport="udp", simulator={"push_state": True})
    rec = _Recorder(sim)
    sim.set_state("level", 5)
    assert rec.events == []            # nobody heard from yet
    sim._last_client_addr = ("127.0.0.1", 9)
    sim._udp_transport = object()
    sim.set_state("level", 6)
    assert rec.events == [("dgram", b"LVL6\r")]
    rec.events.clear()
    sim._handling_command = True
    sim.set_state("level", 7)
    assert rec.events == []


@pytest.mark.asyncio
async def test_without_push_state_only_a_notification_goes_out():
    sim = _sim(simulator={"notifications": {"level": {"5": "FIVE", "*": "L={value:03d}"}}})
    rec = _Recorder(sim)
    sim._clients = {"c": None}
    sim.set_state("level", 5)
    sim.set_state("level", 9)
    await asyncio.sleep(0)
    assert rec.events == [("push", b"FIVE\r"), ("push", b"L=009\r")]


@pytest.mark.asyncio
async def test_a_notification_replaces_the_standard_push_only_where_it_can_reach():
    sim = _sim(simulator={"push_state": True, "notifications": {"level": {"5": "FIVE"}}})
    rec = _Recorder(sim)
    sim._clients = {"c": None}
    sim.set_state("level", 5)
    sim.set_state("level", 6)          # no template for 6: the standard push
    await asyncio.sleep(0)
    assert rec.events == [("push", b"FIVE\r"), ("push", b"LVL6\r")]

    # An HTTP device with no push block cannot carry a notification, so the
    # standard push is not replaced: with a client on the stream link (an
    # HTTP device has none in practice) both go out, the standard push first.
    sim = _sim(transport="http", simulator={"push_state": True, "notifications": {"level": "N{value}"}})
    rec = _Recorder(sim)
    sim.set_state("level", 4)
    await asyncio.sleep(0)
    assert rec.events == []
    sim._clients = {"c": None}
    sim.set_state("level", 5)
    await asyncio.sleep(0)
    assert rec.events == [("push", b"LVL5"), ("push", b"N5")]


@pytest.mark.asyncio
async def test_a_notification_carries_other_state_in_the_device_numbers():
    sim = _sim(
        state_variables={"level": {"type": "integer"}, "gain": {"type": "number", "scale": 0.5}},
        simulator={"notifications": {"level": "L{value} G{state.gain:03.0f} X{state.nope}"}},
    )
    rec = _Recorder(sim)
    sim._clients = {"c": None}
    sim._state["gain"] = 4
    sim.set_state("level", 3)
    await asyncio.sleep(0)
    assert rec.events == [("push", b"L3 G008 X{state.nope}\r")]


@pytest.mark.parametrize("template, value, expected", [
    ("{value:d}", True, "1"),
    ("{value:03d}", "7", "007"),
    ("{value:.1f}", "2", "2.0"),
    ("{value:05.1f}", "2.5", "002.5"),
    ("{value:d}", "abc", "abc"),
    ("{value:q}", 5, "5"),
    ("{key}={value}", 5, "level=5"),
    ("{value:>3}", "a", "  a"),
])
def test_render_notification(template, value, expected):
    assert YAMLAutoSimulator._render_notification(template, "level", value) == expected


def test_render_notification_state_refs():
    r = YAMLAutoSimulator._render_notification
    assert r("{state.a} {state.b:02d}", "k", 1, state={"a": "x", "b": 3}) == "x 03"
    assert r("{state.a}", "k", 1) == "{state.a}"
    assert r("{state.a}", "k", 1, state={}) == "{state.a}"


def test_notification_templates_parse_both_shapes():
    sim = _sim(simulator={"notifications": {"level": {1: "ONE", "*": "ANY"}, "power": "P{value}",
                                            "bad": 5}})
    assert sim._notification_map == {"level": {"1": "ONE", "*": "ANY"}, "power": {"*": "P{value}"}}
    assert sim._notification_template("level", 1) == "ONE"
    assert sim._notification_template("level", 2) == "ANY"
    assert sim._notification_template("power", True) == "P{value}"
    assert sim._notification_template("nope", 1) is None


@pytest.mark.asyncio
async def test_stop_closes_the_multicast_socket_and_abandons_dials():
    sim = _sim(push={"type": "multicast", "group": "239.1.2.3", "port": 5000})
    sock = _Sock()
    sim._mcast_sock = sock
    task = asyncio.ensure_future(asyncio.sleep(10))
    sim._push_tcp_tasks.add(task)
    await sim.stop()
    await asyncio.sleep(0)
    assert sim._mcast_sock is None and getattr(sock, "closed", False)
    assert task.cancelled() and sim._push_tcp_tasks == set()


def test_a_multicast_sender_that_cannot_open_is_none(caplog, monkeypatch):
    caplog.set_level(logging.WARNING, logger=LOGGER)
    sim = _sim(push={"type": "multicast", "group": "239.1.2.3", "port": 5000})
    import socket as socket_mod

    def boom(*_a, **_k):
        raise OSError("no sockets")

    monkeypatch.setattr(socket_mod, "socket", boom)
    sim._open_multicast_sender()
    assert sim._mcast_sock is None
    assert _warnings(caplog) == [(LOGGER, "WARNING", "Could not open multicast sender: no sockets")]


@pytest.mark.asyncio
async def test_an_osc_device_pushes_a_changed_value_as_an_osc_message():
    from openavc.transport.osc_codec import osc_encode_message

    sim = _sim(
        transport="osc",
        state_variables={"fader": {"type": "number"}, "mute": {"type": "boolean"}},
        responses=[
            {"address": "/ch/01/fader", "mappings": [{"state": "fader", "arg": 0, "type": "float"}]},
            {"address": "/ch/01/mute", "mappings": [{"state": "mute", "arg": 0, "type": "integer",
                                                      "map": {"1": True, "0": False}}]},
        ],
        simulator={"push_state": True},
    )
    rec = _Recorder(sim)
    sim.set_state("fader", 0.5)
    assert rec.events == []            # no peer yet
    sim._last_client_addr = ("127.0.0.1", 9)
    sim._udp_transport = object()
    sim.set_state("fader", 0.25)
    sim.set_state("mute", True)
    sim._handling_osc = True
    sim.set_state("fader", 0.75)       # answering a message: nothing
    sim._handling_osc = False
    assert rec.events == [
        ("dgram", osc_encode_message("/ch/01/fader", [("f", 0.25)])),
        ("dgram", osc_encode_message("/ch/01/mute", [("i", 1)])),
    ]
