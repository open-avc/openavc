"""Where a generated simulator's notifications go.

A driver's ``push:`` block says how the real device sends what it says
unprompted: to a multicast group, down an event stream (SSE), by dialling out
to the subscribers that registered (``tcp_listener``), by POSTing to the
callback URLs a controller registered (``http_listener``), or as datagrams to a
listener port (``udp_listener``). The ``simulator:`` section's
``notifications:`` templates are what it says. A driver with no push block, or
one whose block resolves to nothing, pushes on the control link: every
connected TCP client, or the last UDP peer that spoke to the device.

The channel is chosen in one place, ``_push_channel``: the first declared
channel that resolved to a target, in the order ``_PUSH_CHANNELS`` lists them,
each row naming the attribute that holds its target and the method that sends.
``_sends_notifications`` (whether the device counts as pushing) and
``_deliver_notification`` (where a notification goes) both ask it, so the two
cannot disagree. The targets are read when a notification goes out, never
cached, so a target set after construction takes effect.

``PushChannels`` is a mixin of ``YAMLAutoSimulator``; the server bases provide
the sends (``push``, ``push_datagram``, ``push_sse_event``,
``post_http_callback``, ``send_to_listener``). It logs as
``openavc.simulator.yaml_auto``: the simulator validator mutes that logger
while it builds a simulator to probe.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import socket
from typing import Any

from openavc.drivers.compiled_protocol import safe_substitute
from openavc.simulator.state_replies import map_key

logger = logging.getLogger("openavc.simulator.yaml_auto")

# The state keys a notification template names as ``{state.<key>}``.
_NOTIFICATION_STATE_REF = re.compile(r"\{state\.([\w.]+)(?::[^{}]*)?\}")

#: Each declared channel: the attribute holding its resolved target and the
#: method that sends on it, in the order a notification picks them; the first
#: whose target is set carries it.
_PUSH_CHANNELS: tuple[tuple[str, str], ...] = (
    ("_push_multicast", "_send_multicast"),          # multicast
    ("sse_paths", "_send_sse"),                       # sse
    ("_push_tcp", "_send_dial_back"),                 # tcp_listener
    ("_push_http", "_send_callbacks"),                # http_listener
    ("_push_udp_listener", "_send_to_listener_port"),  # udp_listener
)


def _resolved(value: Any, fields: dict) -> Any:
    """A push field with its ``{config_field}`` templates filled in."""
    if isinstance(value, str) and "{" in value:
        return safe_substitute(value, fields)
    return value


def _push_block(driver_def: dict, kind: str) -> dict | None:
    """The driver's ``push:`` block when it is of ``kind``."""
    push = driver_def.get("push")
    if not isinstance(push, dict) or push.get("type") != kind:
        return None
    return push


class PushChannels:
    """The push targets a driver declares and the notifications sent on them."""

    # Host attributes (YAMLAutoSimulator and its server bases). Also asked of
    # the host: _instance_config, _get_delimiter and _to_wire
    # (YAMLAutoSimulator); _silenced, log_protocol and send_to_listener
    # (BaseSimulator); push (TCPSimulator); push_datagram (the datagram
    # server); push_sse_event and post_http_callback (HTTPServerMixin).
    _driver_def: dict
    name: str
    _is_udp: bool
    _is_osc: bool
    _is_http: bool
    _clients: dict
    _last_peer_ip: str
    _last_client_addr: tuple[str, int] | None
    _udp_transport: Any
    _state: dict

    # Before __init__ reaches its assignment (set_state can fire during
    # construction).
    _notification_map: dict[str, dict[str, str]] = {}

    def _set_up_push(self) -> None:
        """Resolve the driver's push block to its target, one per kind; at
        most one holds a target, because a block has one type."""
        # Multicast: notifications go to the resolved group:port only. The
        # sender socket is opened in start() and closed in stop().
        self._push_multicast = self._resolve_push_multicast()
        self._mcast_sock: socket.socket | None = None
        # SSE: a GET on one of these paths with Accept: text/event-stream is
        # held open and each notification is an event (data: <msg>\n\n).
        # `sse_paths` is the HTTP server's own name for the paths it holds.
        self.sse_paths = self._resolve_push_sse()
        # A session block makes the served stream name a session the way the
        # device does (header / opening event); the driver's register
        # commands then address it, and the simulator: section's handlers
        # answer them like any other request.
        sse = _push_block(self._driver_def, "sse")
        self.sse_session = (
            dict(sse["session"]) if sse and isinstance(sse.get("session"), dict) else None
        )
        # Dial-out: the driver's register/unregister commands are watched for
        # (recognized by their {listener_port} templates) and each
        # notification dials every registered subscriber once, wrapped in the
        # declared frame container.
        self._push_tcp = self._resolve_push_tcp_listener()
        # (host, port) -> consecutive delivery failures. Real devices prune
        # subscribers they can't reach; the simulator drops one after 3
        # straight failures so a departed platform doesn't get dialed forever.
        self._push_tcp_subscribers: dict[tuple[str, int], int] = {}
        self._push_tcp_tasks: set[asyncio.Task] = set()
        # HTTP callback: script handlers record the URL the controller
        # registers (register_callback() in the handler namespace), and each
        # notification is POSTed to every registered URL.
        self._push_http = _push_block(self._driver_def, "http_listener") is not None
        self._http_push_callbacks: list[str] = []
        # Listener port: each notification is one datagram to the port the
        # config names, on this computer (where the platform is), from this
        # device's own port number (BaseSimulator.send_to_listener), and NOT
        # to the control link: a device set to send its changes to an address
        # sends them only there.
        self._push_udp_listener = self._resolve_push_udp_listener()

    # ── Resolving the push block ──

    def _resolve_push_multicast(self) -> tuple[str, int] | None:
        """The (group, port) a ``multicast`` block resolves to, or None.
        ``{config_field}`` templates resolve against the same fields the real
        driver resolves them from."""
        push = _push_block(self._driver_def, "multicast")
        if push is None:
            return None
        fields = self._instance_config()
        group = str(_resolved(push.get("group", ""), fields) or "").strip()
        try:
            port = int(str(_resolved(push.get("port"), fields)).strip())
        except (TypeError, ValueError):
            port = 0
        try:
            is_mcast = ipaddress.IPv4Address(group).is_multicast
        except (ipaddress.AddressValueError, ValueError):
            is_mcast = False
        if not is_mcast or not (0 < port < 65536):
            logger.warning(
                "push block did not resolve to a multicast group/port "
                "(group=%r port=%r) — simulator will not emit notifications",
                group, push.get("port"),
            )
            return None
        return (group, port)

    def _resolve_push_sse(self) -> list[str]:
        """The event-stream paths an ``sse`` block resolves to."""
        push = _push_block(self._driver_def, "sse")
        if push is None:
            return []
        fields = self._instance_config()
        raw = push.get("path")
        raw_paths = [raw] if isinstance(raw, str) else list(raw or [])
        paths = [str(_resolved(p, fields)).strip() for p in raw_paths]
        good = [p for p in paths if p.startswith("/")]
        if not good:
            logger.warning(
                "push block did not resolve to event-stream path(s) "
                "(path=%r) — simulator will not emit notifications",
                push.get("path"),
            )
        return good

    def _resolve_push_udp_listener(self) -> int | None:
        """The port a ``udp_listener`` block sends its packets to, or None."""
        push = _push_block(self._driver_def, "udp_listener")
        if push is None:
            return None
        raw = _resolved(push.get("port"), self._instance_config())
        try:
            port = int(str(raw).strip())
        except (TypeError, ValueError):
            port = 0
        if not (0 < port < 65536):
            logger.warning(
                "push block did not resolve to a UDP listener port "
                "(port=%r) — simulator will not send notifications",
                push.get("port"),
            )
            return None
        return port

    def _resolve_push_tcp_listener(self) -> dict | None:
        """A ``tcp_listener`` block for dial-out emission.

        Returns ``{"frame": <frame_parser cfg or None>, "register": <compiled
        regex or None>, "unregister": ...}``. The register/unregister matchers
        are built from the named commands' own path/send templates, with the
        ``{listener_port}`` token becoming a port capture group — so the
        simulator recognizes the registration exactly as the driver sends it.
        """
        push = _push_block(self._driver_def, "tcp_listener")
        if push is None:
            return None
        commands = self._driver_def.get("commands") or {}
        return {
            "frame": push.get("frame_parser")
            if isinstance(push.get("frame_parser"), dict)
            else None,
            "register": _registration_matcher(commands, push.get("register")),
            "unregister": _registration_matcher(commands, push.get("unregister")),
        }

    # ── Dial-back subscribers (tcp_listener) ──

    def _watch_push_registration(self, text: str) -> bool:
        """Track dial-back subscribers from register/unregister commands.

        Observe-only bookkeeping run before normal dispatch; returns True
        when ``text`` was a registration command (callers use that to give
        an empty-success default response if no handler answers it).
        """
        if not self._push_tcp:
            return False
        for key, add in (("register", True), ("unregister", False)):
            matcher = self._push_tcp.get(key)
            m = matcher.match(text) if matcher else None
            if not m:
                continue
            try:
                port = int(m.group("lport"))
            except (IndexError, ValueError):
                # Template carried no {listener_port}; nothing to track.
                return True
            target = (self._last_peer_ip or "127.0.0.1", port)
            if add:
                self._push_tcp_subscribers[target] = 0
                self.log_protocol(
                    "in", f"(push subscriber registered: {target[0]}:{port})"
                )
            else:
                self._push_tcp_subscribers.pop(target, None)
                self.log_protocol(
                    "in", f"(push subscriber removed: {target[0]}:{port})"
                )
            return True
        return False

    def _wrap_push_tcp_frame(self, payload: bytes) -> bytes:
        """Wrap a notification payload in the declared frame container.

        Inverse of the platform's StructFrameParser: zeroed reserve regions
        around a length field that counts ``len(payload) - length_adjust``.
        Without a struct_frame declaration the payload goes out raw.
        """
        frame = (self._push_tcp or {}).get("frame")
        if not frame or frame.get("type") != "struct_frame":
            return payload
        length_size = int(frame.get("length_size", 2))
        endian = "little" if frame.get("length_endian") == "little" else "big"
        length_value = len(payload) - int(frame.get("length_adjust", 0))
        return (
            bytes(int(frame.get("header_reserve", 0)))
            + max(0, length_value).to_bytes(length_size, endian)
            + bytes(int(frame.get("mid_reserve", 0)))
            + payload
            + bytes(int(frame.get("trailer_reserve", 0)))
        )

    def _emit_push_tcp(self, payload: bytes) -> None:
        """Dial each registered subscriber and push one framed notification."""
        if self._silenced:
            return
        framed = self._wrap_push_tcp_frame(payload)
        for target in list(self._push_tcp_subscribers):
            task = asyncio.ensure_future(self._dial_push_tcp(target, framed))
            self._push_tcp_tasks.add(task)
            task.add_done_callback(self._push_tcp_tasks.discard)

    async def _dial_push_tcp(self, target: tuple[str, int], framed: bytes) -> None:
        """One outbound notification connection: connect, send, close.

        Mirrors real dial-back devices, which prune subscribers they cannot
        reach — three consecutive failed deliveries drop the registration.
        """
        try:
            _reader, writer = await asyncio.wait_for(
                asyncio.open_connection(target[0], target[1]), timeout=2.0
            )
            writer.write(framed)
            await writer.drain()
            writer.close()
            if target in self._push_tcp_subscribers:
                self._push_tcp_subscribers[target] = 0
            self.log_protocol("out", framed)
        except (OSError, asyncio.TimeoutError):
            failures = self._push_tcp_subscribers.get(target)
            if failures is None:
                return
            if failures + 1 >= 3:
                del self._push_tcp_subscribers[target]
                logger.info(
                    "%s: push subscriber %s:%d unreachable 3 times — removed",
                    self.name, target[0], target[1],
                )
            else:
                self._push_tcp_subscribers[target] = failures + 1

    # ── Webhook callbacks (http_listener) ──

    def register_callback(self, url: Any) -> None:
        """Record a notification callback URL (script-handler namespace).

        A handler matching the device's registration command calls this with
        the URL the controller supplied; subsequent state-change
        notifications POST there. Registering an already-known URL is a
        no-op, matching re-registration on reconnect.
        """
        url = str(url or "").strip()
        if url and url not in self._http_push_callbacks:
            self._http_push_callbacks.append(url)
            self.log_protocol("in", f"(callback registered: {url})")

    def unregister_callback(self, url: Any) -> None:
        """Drop a previously registered callback URL (script-handler
        namespace — for handlers matching the device's deregistration
        command)."""
        url = str(url or "").strip()
        if url in self._http_push_callbacks:
            self._http_push_callbacks.remove(url)
            self.log_protocol("in", f"(callback unregistered: {url})")

    @staticmethod
    def _callback_content_type(msg: str) -> str:
        """Content type for a rendered notification, from the payload's shape.

        Purely for protocol-log realism — the platform's dispatch reads only
        the body.
        """
        stripped = msg.lstrip()
        if stripped.startswith("<"):
            return "text/xml"
        if stripped.startswith(("{", "[")):
            return "application/json"
        return "text/plain"

    # ── Opening and closing what the channels hold ──

    def _open_multicast_sender(self) -> None:
        """Open the multicast emission socket (TTL 1, loopback enabled so a
        same-host platform listener receives the frames)."""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
            sock.setblocking(False)
            self._mcast_sock = sock
            logger.info(
                "%s multicast notifications to %s:%d",
                self.name, self._push_multicast[0], self._push_multicast[1],
            )
        except OSError as e:
            self._mcast_sock = None
            logger.warning("Could not open multicast sender: %s", e)

    def _open_push_channels(self) -> None:
        """Open what a channel holds while the device runs: the multicast
        sender."""
        if self._push_multicast:
            self._open_multicast_sender()

    def _close_push_channels(self) -> None:
        """Abandon in-flight dial-out notifications and close the multicast
        socket."""
        for task in list(self._push_tcp_tasks):
            task.cancel()
        self._push_tcp_tasks.clear()
        if self._mcast_sock is not None:
            try:
                self._mcast_sock.close()
            except OSError:
                pass
            self._mcast_sock = None

    # ── Notifications ──

    def _read_notifications(self, notifications: Any) -> None:
        """The ``notifications:`` templates: ``{state_key: {value: template}}``
        or ``{state_key: template}`` for any value (filed under ``*``).
        ``{key}`` is the state key, ``{value}`` the new value."""
        self._notification_map = {}
        for key, value_map in notifications.items():
            if isinstance(value_map, dict):
                self._notification_map[key] = {
                    str(k): str(v) for k, v in value_map.items()
                }
            elif isinstance(value_map, str):
                self._notification_map[key] = {"*": value_map}

    def _notification_template(self, key: str, value: Any) -> str | None:
        """The `notifications:` template for ``key`` taking ``value``: the
        one written for that value, else the key's ``*`` template, else
        None."""
        value_map = self._notification_map.get(key) if self._notification_map else None
        if not value_map:
            return None
        return value_map.get(map_key(value)) or value_map.get("*") or None

    def _push_channel(self) -> str | None:
        """The sender of the declared channel a notification goes out on: the
        first in ``_PUSH_CHANNELS`` holding a target, else None (the control
        link)."""
        for target, sender in _PUSH_CHANNELS:
            if getattr(self, target):
                return sender
        return None

    def _sends_notifications(self) -> bool:
        """Whether a ``notifications:`` template can reach the driver: the map
        has one and the device has a channel for it. An HTTP device with no
        push block of its own has none, and neither has an OSC one, which
        pushes only with ``push_state``, as OSC messages."""
        if not self._notification_map:
            return False
        return self._push_channel() is not None or not (self._is_http or self._is_osc)

    def _send_notification(self, template: str, key: str, value: Any) -> None:
        """Render a ``notifications:`` template for ``key``'s new value and
        deliver it. Values go in as the device's own numbers."""
        self._deliver_notification(
            self._render_notification(
                template, key, self._to_wire(key, value),
                state={
                    ref: self._to_wire(ref, self._state[ref])
                    for ref in _NOTIFICATION_STATE_REF.findall(template)
                    if ref in self._state
                },
            )
        )

    def _deliver_notification(self, msg: str) -> None:
        """Send one unsolicited message the way this device pushes.

        The one door for every notification a simulator emits: a rendered
        ``notifications:`` template on a state change, or a script handler's
        ``notify(text)``. A driver with a declared channel gets its
        notifications on that channel ONLY — real devices with a dedicated
        notice feed never send those frames on the control connection.
        """
        data = (msg + self._get_delimiter()).encode()
        sender = self._push_channel()
        if sender is None:
            self._send_on_control_link(data)
        else:
            getattr(self, sender)(msg, data)

    def _peer_heard(self) -> bool:
        """Whether a datagram peer has spoken to the device: the only one a
        connectionless device can push to."""
        return bool(self._last_client_addr and self._udp_transport)

    def _control_link_open(self) -> bool:
        """Whether anyone is on the control link to push to: a UDP peer that
        spoke to the device, or a connected stream client."""
        return self._peer_heard() if self._is_udp else bool(self._clients)

    def _send_on_control_link(self, data: bytes) -> None:
        """Send on the control link: the last UDP peer that spoke to the
        device (the peer a real UDP server answers, and the only one it
        knows), else every connected stream client."""
        if self._is_udp and not self._is_osc:
            self.push_datagram(data)
        elif self._clients:
            asyncio.ensure_future(self.push(data))

    def _send_multicast(self, _msg: str, data: bytes) -> None:
        if self._mcast_sock is not None and not self._silenced:
            try:
                self._mcast_sock.sendto(data, self._push_multicast)
                self.log_protocol("out", data)
            except OSError:
                logger.debug("Multicast notification send failed", exc_info=True)

    def _send_sse(self, msg: str, _data: bytes) -> None:
        # SSE events are self-framed — no delimiter appended.
        self.push_sse_event(msg)

    def _send_dial_back(self, _msg: str, data: bytes) -> None:
        # One outbound connection per registered subscriber, each carrying
        # one framed notification.
        if self._push_tcp_subscribers:
            self._emit_push_tcp(data)

    def _send_callbacks(self, msg: str, _data: bytes) -> None:
        # HTTP-callback bodies are self-framed — no delimiter appended. No
        # registered callback (the controller never sent its registration
        # command) means no delivery, matching a real device.
        for url in list(self._http_push_callbacks):
            asyncio.ensure_future(self.post_http_callback(
                url, msg,
                headers={"Content-Type": self._callback_content_type(msg)},
            ))

    def _send_to_listener_port(self, _msg: str, data: bytes) -> None:
        # One datagram per notification to the port the device is set to
        # send to; nobody registers, so it goes whether or not the platform
        # is listening, as on the device.
        self.send_to_listener(data, self._push_udp_listener)

    @staticmethod
    def _render_notification(
        template: str, key: str, value: Any, state: dict[str, Any] | None = None,
    ) -> str:
        """Render a notification template. `{value}` / `{key}` substitute as
        before; an optional format spec (`{value:d}`, `{value:04X}`) formats
        the value — booleans coerce to int first so `{value:d}` renders a
        protocol's 0/1 instead of 'True'/'False'. `{state.<key>}` puts in
        another state value from ``state`` (the device's own numbers), with
        the same format specs, for a notice that reports several fields at
        once; a key ``state`` does not hold is left as written."""

        def repl(m: re.Match) -> str:
            name = m.group(1)
            if name == "value":
                val: Any = value
            elif name == "key":
                val = key
            else:
                ref = name[len("state."):]
                if state is None or ref not in state:
                    return m.group(0)
                val = state[ref]
            return _formatted(val, m.group(2))

        return re.sub(r"\{(value|key|state\.[\w.]+)(?::([^{}]*))?\}", repl, template)


def _formatted(val: Any, spec: str | None) -> str:
    """A notification slot's text: ``val`` through its format spec, a boolean
    as 0/1, a numeric string coerced when the spec is numeric; as text when
    the spec does not apply."""
    if not spec:
        return str(val)
    if isinstance(val, bool):
        val = int(val)
    try:
        return format(val, spec)
    except (ValueError, TypeError):
        if isinstance(val, str):
            for conv in (int, float):
                try:
                    return format(conv(val), spec)
                except (ValueError, TypeError):
                    continue
        return str(val)


def _registration_matcher(commands: dict, cmd_name: Any) -> re.Pattern | None:
    """The pattern a register/unregister command arrives as: its own path or
    send template, ``{listener_port}`` a port capture, any other token one
    run of non-separators, an HTTP body or trailing whitespace allowed."""
    cmd = commands.get(cmd_name) if isinstance(cmd_name, str) else None
    if not isinstance(cmd, dict):
        return None
    if "path" in cmd or "method" in cmd:
        template = f"{cmd.get('method', 'GET').upper()} {cmd.get('path', '/')}"
    else:
        template = str(cmd.get("send", ""))
    if not template:
        return None
    pattern = re.escape(template)
    pattern = pattern.replace(re.escape("{listener_port}"), r"(?P<lport>\d+)")
    pattern = re.sub(r"\\\{\w+\\\}", r"[^&\\s|]+", pattern)
    return re.compile(r"^" + pattern + r"\s*(?:\|.*)?$")
