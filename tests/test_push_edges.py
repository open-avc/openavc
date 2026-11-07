"""The driver push runtime at its edges (``DRIVER_INFO["push"]``).

What each shape does when its channel will not open, how the registration
commands run and how often a failing one is reported, the SSE options read
from the block, what a disconnect closes and de-registers, and which
deliveries reach the response path. An invented device (acme_widget) with
the listeners replaced by recording fakes; the dedicated push test files
cover each shape against real sockets.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.base import BaseDriver
from openavc.transport import http_listener, multicast_listener, tcp_listener, udp_listener

LOGGER = "openavc.drivers.base"


class _Acme(BaseDriver):
    DRIVER_INFO: dict[str, Any] = {
        "id": "acme_widget",
        "name": "Acme Widget",
        "transport": "tcp",
        "state_variables": {},
        "commands": {
            "arm": {"params": {}},
            "arm_zone": {"params": {"zone": {"type": "child_id", "child_type": "zone"}}},
            "arm_other": {"params": {"x": {"type": "child_id", "child_type": "other"}, "y": "z"}},
            "arm_typed": {"params": {"n": {"type": "integer", "child_type": "zone"}}},
            "bare": {},
        },
        "child_entity_types": {"zone": {"id_format": {"type": "integer"}}},
    }

    def __init__(self, config: dict[str, Any] | None = None, push: Any = None) -> None:
        super().__init__("w1", dict(config or {}), StateStore(), EventBus())
        self.DRIVER_INFO = {**type(self).DRIVER_INFO, "push": push}
        self.sent: list[tuple[str, Any]] = []
        self.refuse: set[str] = set()
        self.received: list[bytes] = []

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        self.sent.append((command, params))
        if command in self.refuse:
            raise RuntimeError(f"{command} refused")

    async def on_data_received(self, data: bytes) -> None:
        self.received.append(data)


class _Handle:
    def __init__(self, port: int = 0, path: str = "", fail_close: bool = False) -> None:
        self.port = port
        self.path = path
        self.closed = 0
        self.fail_close = fail_close

    async def close(self) -> None:
        self.closed += 1
        if self.fail_close:
            raise OSError("close failed")


def _messages(caplog: pytest.LogCaptureFixture, level: int | None = None) -> list[str]:
    return [
        r.getMessage() for r in caplog.records
        if r.name == LOGGER and (level is None or r.levelno == level)
    ]


def _recording_subscribe(calls: list[dict[str, Any]], result: Any = None, error: Exception | None = None):
    async def subscribe(**kwargs: Any) -> Any:
        calls.append(kwargs)
        if error is not None:
            raise error
        return result
    return subscribe


# --- which shape -------------------------------------------------------------


async def test_no_push_block_or_a_non_dict_opens_nothing(caplog):
    for push in (None, "multicast", ["sse"]):
        drv = _Acme(push=push)
        with caplog.at_level(logging.DEBUG, logger=LOGGER):
            await drv._start_push()
        assert drv._push_subscription is None
    assert _messages(caplog) == []


async def test_an_unknown_push_type_warns_and_opens_nothing(caplog):
    drv = _Acme(push={"type": "carrier_pigeon"})
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        await drv._start_push()
    assert _messages(caplog) == ["[w1] push: type 'carrier_pigeon' is not supported at runtime"]
    assert drv._push_subscription is None


# --- multicast ---------------------------------------------------------------


async def test_multicast_refuses_a_group_or_port_that_did_not_resolve(caplog, monkeypatch):
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(multicast_listener, "subscribe", _recording_subscribe(calls))
    cases = [
        ({"type": "multicast", "group": "10.0.0.1", "port": 5000}, "'10.0.0.1' / port 5000"),
        ({"type": "multicast", "group": "239.1.1.1", "port": "{p}"}, "'239.1.1.1' / port '{p}'"),
        ({"type": "multicast", "group": "239.1.1.1", "port": 0}, "'239.1.1.1' / port 0"),
        ({"type": "multicast", "group": "239.1.1.1", "port": 65536}, "'239.1.1.1' / port 65536"),
    ]
    for push, named in cases:
        caplog.clear()
        drv = _Acme(push=push)
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            await drv._start_push()
        assert _messages(caplog) == [
            f"[w1] push: cannot subscribe — group {named} did not resolve to a "
            f"multicast address and port (check the device's notification settings fields)"
        ]
    assert calls == []


async def test_multicast_subscribes_with_the_resolved_group_and_counts_deliveries(monkeypatch):
    calls: list[dict[str, Any]] = []
    handle = _Handle()
    monkeypatch.setattr(multicast_listener, "subscribe", _recording_subscribe(calls, handle))
    drv = _Acme(
        config={"host": "10.0.0.9", "grp": "239.1.1.1", "p": "5000"},
        push={"type": "multicast", "group": "{grp}", "port": "{p}"},
    )
    await drv._start_push()
    assert drv._push_subscription is handle
    (call,) = calls
    assert {k: call[k] for k in ("group", "port", "source_ip", "name")} == {
        "group": "239.1.1.1", "port": 5000, "source_ip": "10.0.0.9", "name": "w1",
    }
    await call["callback"](b"hello\rworld\r", ("10.0.0.9", 1))
    assert drv._push_receives == 1
    assert drv.received == [b"hello", b"world"]


async def test_multicast_that_cannot_open_warns_and_keeps_no_subscription(caplog, monkeypatch):
    monkeypatch.setattr(
        multicast_listener, "subscribe", _recording_subscribe([], error=OSError("in use")),
    )
    drv = _Acme(push={"type": "multicast", "group": "239.1.1.1", "port": 5000})
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        await drv._start_push()
    assert _messages(caplog) == [
        "[w1] push: could not open multicast listener on 239.1.1.1:5000: in use"
    ]
    assert drv._push_subscription is None


# --- tcp_listener --------------------------------------------------------------


async def test_tcp_listener_takes_port_zero_and_refuses_one_that_did_not_resolve(caplog, monkeypatch):
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(tcp_listener, "subscribe", _recording_subscribe(calls, _Handle(port=41000)))
    drv = _Acme(push={"type": "tcp_listener", "port": 0, "register": "arm"})
    await drv._start_push()
    assert drv.config["listener_port"] == 41000
    assert calls[0]["port"] == 0
    assert calls[0]["frame_parser_factory"] is None
    assert drv.sent == [("arm", {})]
    for bad in ("{nope}", -1, 65536):
        caplog.clear()
        drv = _Acme(push={"type": "tcp_listener", "port": bad, "register": "arm"})
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            await drv._start_push()
        assert _messages(caplog) == [
            f"[w1] push: cannot listen — port {bad!r} did not resolve to a TCP port "
            f"(check the device's notification settings fields)"
        ]
        assert drv.sent == []


async def test_tcp_listener_builds_a_fresh_frame_parser_per_connection(monkeypatch):
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(tcp_listener, "subscribe", _recording_subscribe(calls, _Handle(port=1)))
    drv = _Acme(push={
        "type": "tcp_listener", "port": 0,
        "frame_parser": {
            "type": "struct_frame", "header_reserve": 6, "length_size": 2,
            "length_endian": "big", "length_adjust": -8, "mid_reserve": 4,
            "trailer_reserve": 10,
        },
    })
    await drv._start_push()
    factory = calls[0]["frame_parser_factory"]
    assert factory is not None and factory() is not factory()


async def test_tcp_listener_that_cannot_open_runs_no_registration(caplog, monkeypatch):
    monkeypatch.setattr(tcp_listener, "subscribe", _recording_subscribe([], error=OSError("busy")))
    drv = _Acme(push={"type": "tcp_listener", "port": 9000, "register": "arm"})
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        await drv._start_push()
    assert _messages(caplog) == [
        "[w1] push: could not open TCP listener on port 9000: busy (is another program using it?)"
    ]
    assert drv.sent == []
    assert "listener_port" not in drv.config
    assert drv._push_subscription is None


# --- udp_listener ---------------------------------------------------------------


async def test_udp_listener_refuses_port_zero_and_names_the_simulators_source_port(caplog, monkeypatch):
    calls: list[dict[str, Any]] = []
    handle = _Handle()
    monkeypatch.setattr(udp_listener, "subscribe", _recording_subscribe(calls, handle))
    drv = _Acme(push={"type": "udp_listener", "port": 0})
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        await drv._start_push()
    assert _messages(caplog) == [
        "[w1] push: cannot listen — port 0 did not resolve to a UDP port 1-65535 "
        "(check the device's notification settings fields)"
    ]
    drv = _Acme(config={"host": "10.0.0.5"}, push={"type": "udp_listener", "port": 7000})
    await drv._start_push()
    drv2 = _Acme(push={"type": "udp_listener", "port": 7000})
    drv2.udp_redirect = ("127.0.0.1", 19123)
    await drv2._start_push()
    assert [(c["port"], c["source_ip"], c["source_port"]) for c in calls] == [
        (7000, "10.0.0.5", None), (7000, "", 19123),
    ]
    assert drv._push_subscription is handle


async def test_udp_listener_that_cannot_open_says_why(caplog, monkeypatch):
    for error, hint in (
        (PermissionError("denied"), "a port below 1024 needs permission to listen on"),
        (OSError("in use"), "is another program using it?"),
    ):
        caplog.clear()
        monkeypatch.setattr(udp_listener, "subscribe", _recording_subscribe([], error=error))
        drv = _Acme(push={"type": "udp_listener", "port": 514})
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            await drv._start_push()
        assert _messages(caplog) == [
            f"[w1] push: could not listen on UDP port 514: {error} ({hint}). "
            f"Changes show at the next poll."
        ]
        assert drv._push_subscription is None


# --- registration ---------------------------------------------------------------


def test_register_entries_read_every_accepted_form():
    drv = _Acme()
    assert drv._push_register_entries({}) == []
    assert drv._push_register_entries({"register": ""}) == []
    assert drv._push_register_entries({"register": " arm "}) == [{"command": "arm"}]
    entry = {"command": "arm_zone", "each_child": "zone"}
    assert drv._push_register_entries({
        "register": ["arm", "  ", 5, {"cmd": "x"}, {"command": 7}, entry],
    }) == [{"command": "arm"}, entry]


def test_a_when_field_reads_like_a_switch():
    drv = _Acme(config={"on": "yes", "off": "off", "zero": 0, "word": "maybe", "empty": ""})
    assert drv._push_entry_enabled({"command": "arm"}) is True
    assert [drv._push_entry_enabled({"when": f}) for f in ("on", "off", "zero", "word", "empty", "unset")] == [
        True, False, False, True, False, False,
    ]


def test_child_id_param_names_the_parameter_for_that_child_type():
    drv = _Acme()
    assert drv._child_id_param("arm_zone", "zone") == "zone"
    assert drv._child_id_param("arm_other", "other") == "x"
    assert drv._child_id_param("arm_other", "zone") is None
    assert drv._child_id_param("arm", "zone") is None
    assert drv._child_id_param("arm_typed", "zone") is None
    assert drv._child_id_param("bare", "zone") is None
    assert drv._child_id_param("missing", "zone") is None
    drv.DRIVER_INFO = {**drv.DRIVER_INFO, "commands": None}
    assert drv._child_id_param("arm_zone", "zone") is None


async def test_registration_runs_each_entry_in_order_once_per_child(caplog):
    drv = _Acme(config={"flag": False})
    drv.register_child("zone", 2)
    drv.register_child("zone", 1)
    push = {"register": [
        {"command": "arm", "when": "flag"},
        {"command": "arm", "each_child": "nope"},
        {"command": "arm_zone", "each_child": "zone"},
        "arm",
    ]}
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        await drv._run_push_register(push)
    assert drv.sent == [("arm_zone", {"zone": 2}), ("arm_zone", {"zone": 1}), ("arm", {})]
    assert _messages(caplog) == [
        "[w1] push: register command 'arm' has no child_id parameter for 'nope'; skipping"
    ]


async def test_a_refused_registration_warns_once_until_it_succeeds(caplog):
    drv = _Acme()
    drv.register_child("zone", 3)
    drv.refuse = {"arm", "arm_zone"}
    push = {"register": ["arm", {"command": "arm_zone", "each_child": "zone"}]}
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        await drv._run_push_register(push)
        await drv._run_push_register(push)
        drv.refuse = set()
        await drv._run_push_register(push)
        drv.refuse = {"arm"}
        await drv._run_push_register(push)
    failed = (
        "[w1] push: registration command {} failed: {} refused — the device will not "
        "push what it registers until this succeeds (polling still covers it)"
    )
    assert _messages(caplog, logging.WARNING) == [
        failed.format("arm", "arm"),
        failed.format("arm_zone {'zone': 3}", "arm_zone"),
        failed.format("arm", "arm"),
    ]
    assert _messages(caplog, logging.DEBUG) == [
        failed.format("arm", "arm"),
        failed.format("arm_zone {'zone': 3}", "arm_zone"),
    ]
    assert drv._push_register_failed == {"arm"}


async def test_a_named_session_runs_registration_and_a_lost_one_withdraws_the_token():
    drv = _Acme(push={"type": "sse", "register": "arm"})
    await drv._on_push_session("abc")
    assert drv.push_session == "abc"
    assert drv.sent == [("arm", {})]
    await drv._on_push_session(None)
    assert drv.push_session == ""
    await drv._on_push_session("")
    assert drv.sent == [("arm", {})]
    drv.DRIVER_INFO = {**drv.DRIVER_INFO, "push": None}
    await drv._on_push_session("def")
    assert drv.push_session == "def"
    assert drv.sent == [("arm", {})]


# --- sse --------------------------------------------------------------------------


class _HttpTransport:
    connected = True

    def __init__(self) -> None:
        self.opened: list[tuple[str, dict[str, Any]]] = []

    def open_event_stream(self, path: str, callback: Any, **kwargs: Any) -> _Handle:
        self.opened.append((path, kwargs))
        return _Handle(path=path)


async def test_sse_needs_the_http_transport(caplog):
    for transport in (None, object()):
        caplog.clear()
        drv = _Acme(push={"type": "sse", "path": "/events"})
        drv.transport = transport
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            await drv._start_push()
        assert _messages(caplog) == ["[w1] push: type 'sse' requires the HTTP transport"]
        assert drv._push_subscription is None


async def test_sse_reads_its_paths_timeout_and_session(caplog):
    drv = _Acme(config={"ch": "7"}, push={
        "type": "sse", "path": ["/a/{ch}", "{missing}", "relative", "/b"],
        "idle_timeout": "not a number", "session": {"id_field": "sid"},
    })
    drv.transport = _HttpTransport()
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        await drv._start_push()
    assert _messages(caplog) == [
        "[w1] push: a session block needs a single event-stream path; ignoring the session",
        "[w1] push: event-stream path '{missing}' did not resolve to a URL path "
        "(check the device's config fields)",
        "[w1] push: event-stream path 'relative' did not resolve to a URL path "
        "(check the device's config fields)",
    ]
    assert [(p, kw) for p, kw in drv.transport.opened] == [
        ("/a/7", {"idle_timeout": 0.0, "name": "w1", "session": None, "on_session": None}),
        ("/b", {"idle_timeout": 0.0, "name": "w1", "session": None, "on_session": None}),
    ]
    assert [h.path for h in drv._push_subscription] == ["/a/7", "/b"]


async def test_sse_with_one_path_keeps_its_session_and_a_numeric_timeout():
    session = {"id_field": "sid"}
    drv = _Acme(push={"type": "sse", "path": " /only ", "idle_timeout": "12.5", "session": session})
    drv.transport = _HttpTransport()
    await drv._start_push()
    ((path, kw),) = drv.transport.opened
    assert path == "/only"
    assert kw["idle_timeout"] == 12.5
    assert kw["session"] is session
    assert kw["on_session"] == drv._on_push_session
    drv2 = _Acme(push={"type": "sse", "path": "/x", "idle_timeout": None, "session": "yes"})
    drv2.transport = _HttpTransport()
    await drv2._start_push()
    assert drv2.transport.opened[0][1]["session"] is None
    assert drv2.transport.opened[0][1]["idle_timeout"] == 0.0


async def test_sse_with_no_usable_path_leaves_no_subscription():
    drv = _Acme(push={"type": "sse", "path": []})
    drv.transport = _HttpTransport()
    await drv._start_push()
    assert drv._push_subscription is None
    drv = _Acme(push={"type": "sse"})
    drv.transport = _HttpTransport()
    await drv._start_push()
    assert drv._push_subscription is None


# --- http_listener --------------------------------------------------------------


async def test_http_listener_publishes_its_callback_url(caplog, monkeypatch):
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(http_listener, "subscribe", _recording_subscribe(calls, _Handle(path="/api/push/w1")))
    monkeypatch.setattr(http_listener, "callback_url", lambda host, path: f"http://me{path}?for={host}")
    drv = _Acme(config={"host": "10.0.0.3"}, push={"type": "http_listener"})
    with caplog.at_level(logging.INFO, logger=LOGGER):
        await drv._start_push()
    assert drv.push_callback_url == "http://me/api/push/w1?for=10.0.0.3"
    assert calls[0]["device_id"] == "w1" and calls[0]["source_ip"] == "10.0.0.3"
    assert _messages(caplog) == ["[w1] push: inbound HTTP callback at http://me/api/push/w1?for=10.0.0.3"]


async def test_http_listener_that_cannot_register_warns_with_the_traceback(caplog, monkeypatch):
    monkeypatch.setattr(http_listener, "subscribe", _recording_subscribe([], error=ValueError("no room")))
    drv = _Acme(push={"type": "http_listener"})
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        await drv._start_push()
    (record,) = [r for r in caplog.records if r.name == LOGGER]
    assert record.getMessage() == "[w1] push: could not register the inbound HTTP callback"
    assert record.exc_info is not None
    assert drv.push_callback_url == ""
    assert drv._push_subscription is None


# --- stopping -----------------------------------------------------------------------


class _Link:
    def __init__(self, connected: Any = True) -> None:
        self.connected = connected


async def test_stop_closes_every_handle_after_unregistering_and_clears_the_rest(caplog):
    drv = _Acme(push={"type": "sse", "unregister": "disarm", "session": {}})
    order: list[str] = []

    async def send_command(command: str, params: Any = None) -> None:
        order.append(f"send {command} in session {drv._push_session!r}")

    drv.send_command = send_command  # type: ignore[method-assign]
    handles = [_Handle(path="/a", fail_close=True), _Handle(path="/b")]
    drv._push_subscription = handles
    drv._push_session = "s1"
    drv._push_callback_url = "http://x"
    drv._push_register_failed = {"arm"}
    drv._connected = True
    drv.transport = _Link()
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        await drv._stop_push()
    assert order == ["send disarm in session 's1'"]
    assert [h.closed for h in handles] == [1, 1]
    assert _messages(caplog, logging.DEBUG) == ["[w1] Error closing push subscription"]
    assert (drv._push_subscription, drv._push_session, drv._push_callback_url) == (None, "", "")
    assert drv._push_register_failed == set()


async def test_stop_with_nothing_open_sends_nothing():
    drv = _Acme(push={"type": "tcp_listener", "unregister": "disarm"})
    drv._push_session = "stale"
    drv._push_callback_url = "http://x"
    drv._connected = True
    drv.transport = _Link()
    await drv._stop_push()
    assert drv.sent == []
    assert (drv._push_session, drv._push_callback_url) == ("", "")


async def test_unregister_runs_only_while_the_session_is_up(caplog):
    def ready(push: Any, connected: bool = True, link: Any = "up", session: str = "") -> _Acme:
        drv = _Acme(push=push)
        drv._connected = connected
        drv.transport = None if link is None else _Link(link == "up")
        drv._push_session = session
        return drv

    skipped = [
        ready(None),
        ready({"type": "tcp_listener"}),
        ready({"type": "tcp_listener", "unregister": ""}),
        ready({"type": "tcp_listener", "unregister": "disarm"}, connected=False),
        ready({"type": "tcp_listener", "unregister": "disarm"}, link=None),
        ready({"type": "tcp_listener", "unregister": "disarm"}, link="down"),
        ready({"type": "sse", "unregister": "disarm", "session": {}}),
    ]
    for drv in skipped:
        await drv._push_unregister()
        assert drv.sent == []
    sent = [
        ready({"type": "tcp_listener", "unregister": "disarm"}),
        ready({"type": "sse", "unregister": "disarm", "session": {}}, session="s"),
        ready({"type": "sse", "unregister": "disarm", "session": "not a block"}),
    ]
    for drv in sent:
        await drv._push_unregister()
        assert drv.sent == [("disarm", None)]
    refusing = ready({"type": "tcp_listener", "unregister": "disarm"})
    refusing.refuse = {"disarm"}
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        await refusing._push_unregister()
    assert _messages(caplog) == [
        "[w1] push: de-registration command 'disarm' failed: disarm refused"
    ]


# --- deliveries ---------------------------------------------------------------------


async def test_a_datagram_splits_on_the_delimiter_and_skips_blank_frames():
    drv = _Acme()
    await drv._handle_push_datagram(b"one\r \r\rtwo\r", ("h", 1))
    assert drv.received == [b"one", b"two"]
    drv.DRIVER_INFO = {**drv.DRIVER_INFO, "delimiter": ""}
    await drv._handle_push_datagram(b"whole\rthing", ("h", 1))
    await drv._handle_push_datagram(b"", ("h", 1))
    assert drv.received == [b"one", b"two", b"whole\rthing"]


async def test_an_event_or_http_body_dispatches_whole_and_blank_ones_are_dropped():
    class _Req:
        def __init__(self, body: Any) -> None:
            self.body = body

    drv = _Acme()
    await drv._handle_push_event(b'{"a": 1}\n{"b": 2}')
    await drv._handle_push_event(b"  \n")
    await drv._handle_push_http(_Req(b"<x/>\r\n<y/>"))
    await drv._handle_push_http(_Req(b" "))
    await drv._handle_push_http(_Req(None))
    assert drv.received == [b'{"a": 1}\n{"b": 2}', b"<x/>\r\n<y/>"]


def test_a_counted_handler_returns_what_the_handler_returns():
    drv = _Acme()
    counted = drv._counting_push(lambda *args: ("got", args))
    assert counted(1, 2) == ("got", (1, 2))
    assert counted() == ("got", ())
    assert drv._push_receives == 2
