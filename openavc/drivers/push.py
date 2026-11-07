"""Push notifications: the channel a device uses to tell the driver about a change.

A driver declares the channel in ``DRIVER_INFO["push"]``, and its ``type`` names
one of five shapes:

* ``multicast`` — the device announces to a multicast group the driver joins.
* ``tcp_listener`` — the device dials back to a shared inbound TCP port.
* ``udp_listener`` — the device sends datagrams to a shared inbound UDP port.
* ``http_listener`` — the device POSTs (a webhook, a GENA NOTIFY) to a
  per-device path on the platform's own web listener.
* ``sse`` — the driver holds open the device's event stream over its HTTP
  session, optionally a named device session.

The rules worth knowing before changing anything here:

* A channel that cannot open never takes the device down. Polling still
  covers it, so each failure is a logged gap and the connect goes on.
* Every delivery counts as the device answering (``_counting_push``): the poll
  loop's silence check reads ``_push_receives``, so a device that only pushes
  is not dropped as silent.
* Device-side registrations do not survive a reboot, a link cut or a new
  session, so ``register`` runs again on every (re)open, and a registration
  the device refuses is warned about once until it succeeds again.

``PushChannel`` is a mixin that ``BaseDriver`` inherits, so every name stays on
the driver object: Python drivers override ``_handle_push_datagram``,
``_handle_push_http`` and ``_on_push_session`` and read ``push_callback_url``
and ``push_session``. ``BaseDriver.__init__`` calls ``_init_push()``, and
``BaseDriver`` supplies what this needs from its host: ``DRIVER_INFO``,
``device_id``, ``config``, ``transport``, ``_connected``, ``udp_redirect``,
``send_command()``, ``on_data_received()``, ``_resolve_delimiter()`` and
``list_children()``.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from typing import Any

from openavc.core.condition_eval import reads_as_on
from openavc.utils.logger import get_logger

# These lines have always logged under the driver runtime's name, which the
# IDE's log view shows as their source.
log = get_logger("openavc.drivers.base")


def resolve_push_value(value: Any, config: dict[str, Any]) -> Any:
    """Resolve a push-block value: ``{config_field}`` tokens substitute from
    ``config``; unknown fields are left verbatim so the warning a failed
    subscription logs names exactly what did not resolve."""
    if isinstance(value, str) and "{" in value:
        return re.sub(
            r"\{(\w+)\}",
            lambda m: str(config.get(m.group(1), m.group(0))),
            value,
        )
    return value


def push_port(push_def: dict[str, Any], config: dict[str, Any]) -> int | None:
    """The port a push block's ``port`` names, resolved from ``config``, or
    None when it is not a number. What the multicast and dial-back listeners
    bind, and what ``core/push_ports.py`` records for the host firewall."""
    raw = resolve_push_value(push_def.get("port"), config)
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return None


def _port_in_range(port: int | None, lowest: int = 1) -> bool:
    """A resolved port a listener can bind: ``lowest`` to 65535. The TCP
    dial-back listener takes 0, an ephemeral port the OS picks."""
    return port is not None and lowest <= port < 65536


def _is_child_id_param(pdef: Any, child_type: str) -> bool:
    """A command parameter that takes a child id of ``child_type``."""
    return (
        isinstance(pdef, dict)
        and pdef.get("type") == "child_id"
        and pdef.get("child_type") == child_type
    )


def _sse_paths(push_def: dict[str, Any]) -> list[Any]:
    """The event-stream path or paths an sse block names, unresolved."""
    raw_path = push_def.get("path")
    return [raw_path] if isinstance(raw_path, str) else list(raw_path or [])


def _sse_idle_timeout(push_def: dict[str, Any]) -> float:
    """The sse block's ``idle_timeout`` in seconds; 0.0 (none) when unset or
    not a number."""
    try:
        return float(push_def.get("idle_timeout") or 0)
    except (TypeError, ValueError):
        return 0.0


class PushChannel:
    """The push half of ``BaseDriver``: opening, registering, closing and
    delivering the channel ``DRIVER_INFO["push"]`` declares (see the module
    docstring)."""

    # Supplied by the host class.
    DRIVER_INFO: dict[str, Any]
    device_id: str
    config: dict[str, Any]
    transport: Any
    udp_redirect: tuple[str, int] | None

    def _init_push(self) -> None:
        """The closed channel, set up by ``BaseDriver.__init__``."""
        # Push-notification subscription (DRIVER_INFO["push"] — a multicast
        # group membership, an inbound TCP dial-back listener, a list of SSE
        # event-stream handles, an HTTP push-listener registration, or a
        # shared inbound UDP port).
        # Started by connect() once the session
        # is up — before any on_connect arming commands run — and stopped on
        # both disconnect paths. None when the driver declares no push block
        # or the subscription failed (polling still covers it).
        self._push_subscription: Any = None
        # http_listener shape: the callback URL the device must deliver to,
        # rebuilt on every (re)connect. Exposed as push_callback_url so
        # registration commands can hand it to the device.
        self._push_callback_url: str = ""
        # sse shape with a `session:` block: the id the device gave the open
        # stream, so a registration command can address it ({push_session}).
        # Empty outside a live session.
        self._push_session: str = ""
        # Register entries that failed on their last run, so a device that
        # refuses the same registration on every reopen warns once and then
        # drops to debug until it succeeds again.
        self._push_register_failed: set[str] = set()
        # Push deliveries received on this connection (any shape). They prove
        # the device is there as well as a reply on the transport does, so the
        # poll loop's silence check counts them (_io_counts).
        self._push_receives = 0

    def _counting_push(self, handler: Callable[..., Any]) -> Callable[..., Any]:
        """Wrap a push handler so each delivery counts as the device answering
        (the silence check). Sync on the outside: it returns whatever the
        handler returns, so a listener that awaits a coroutine still does."""
        def counted(*args: Any) -> Any:
            self._push_receives += 1
            return handler(*args)
        return counted

    def _resolve_push_value(self, value: Any) -> Any:
        """Resolve a push-block value from this device's config
        (:func:`resolve_push_value`)."""
        return resolve_push_value(value, self.config)

    # --- Opening the channel ------------------------------------------------

    async def _start_push(self) -> None:
        """Subscribe to the driver's declared push channel, if any.

        ``multicast``, ``sse``, ``tcp_listener``, ``http_listener`` and
        ``udp_listener`` all exist today. Never raises: a device whose push
        channel can't be
        opened still connects and polls — the gap is logged so the user can
        see why changes aren't instant.
        """
        push_def = self.DRIVER_INFO.get("push")
        if not isinstance(push_def, dict):
            return
        ptype = push_def.get("type")
        if ptype == "multicast":
            await self._start_push_multicast(push_def)
        elif ptype == "sse":
            self._start_push_sse(push_def)
        elif ptype == "tcp_listener":
            await self._start_push_tcp_listener(push_def)
        elif ptype == "http_listener":
            await self._start_push_http_listener(push_def)
        elif ptype == "udp_listener":
            await self._start_push_udp_listener(push_def)
        else:
            # The loader rejects unknown/unsupported types for catalog
            # drivers; this is the runtime backstop for hand-installed files.
            log.warning(
                f"[{self.device_id}] push: type "
                f"{ptype!r} is not supported at runtime"
            )

    def _push_source_ip(self) -> str:
        """The device's own address: the only source a listener accepts."""
        return str(self.config.get("host", "") or "")

    async def _start_push_multicast(self, push_def: dict[str, Any]) -> None:
        """Join the device's multicast notification group."""
        from openavc.transport.multicast_listener import (
            is_multicast_group,
            subscribe,
        )

        group = str(self._resolve_push_value(push_def.get("group", "")) or "")
        raw_port = self._resolve_push_value(push_def.get("port"))
        port = push_port(push_def, self.config)
        if not is_multicast_group(group) or not _port_in_range(port):
            log.warning(
                f"[{self.device_id}] push: cannot subscribe — group "
                f"{group!r} / port {raw_port!r} did not resolve to a "
                f"multicast address and port (check the device's "
                f"notification settings fields)"
            )
            return
        try:
            self._push_subscription = await subscribe(
                group=group,
                port=port,
                source_ip=self._push_source_ip(),
                callback=self._counting_push(self._handle_push_datagram),
                name=self.device_id,
            )
        except OSError as e:
            log.warning(
                f"[{self.device_id}] push: could not open multicast "
                f"listener on {group}:{port}: {e}"
            )

    async def _start_push_tcp_listener(self, push_def: dict[str, Any]) -> None:
        """Open the shared inbound listener the device dials back to.

        The subscription starts before on_connect / registration commands run,
        so the first frame a freshly-registered device pushes is never missed.
        The actual bound port is injected into the device config as
        ``listener_port`` — the reserved substitution token registration
        commands use (``my_port={listener_port}``). When the push block names
        a ``register`` command it runs here, which also re-arms the device on
        every reconnect (device-side registrations don't survive a reboot or
        a link cut).
        """
        from openavc.transport import tcp_listener
        from openavc.transport.frame_parsers import build_frame_parser

        raw_port = self._resolve_push_value(push_def.get("port"))
        port = push_port(push_def, self.config)
        if not _port_in_range(port, lowest=0):
            log.warning(
                f"[{self.device_id}] push: cannot listen — port {raw_port!r} "
                f"did not resolve to a TCP port (check the device's "
                f"notification settings fields)"
            )
            return

        frame_cfg = push_def.get("frame_parser")
        if isinstance(frame_cfg, dict):
            def factory() -> Any:
                return build_frame_parser(frame_cfg)
        else:
            factory = None

        try:
            sub = await tcp_listener.subscribe(
                port=port,
                source_ip=self._push_source_ip(),
                callback=self._counting_push(self._handle_push_datagram),
                name=self.device_id,
                frame_parser_factory=factory,
            )
        except OSError as e:
            log.warning(
                f"[{self.device_id}] push: could not open TCP listener on "
                f"port {port}: {e} (is another program using it?)"
            )
            return
        self._push_subscription = sub
        # Reserved substitution token: commands, on_connect entries, and poll
        # queries can reference {listener_port} to tell the device where to
        # dial back (resolves an ephemeral port-0 bind to the real port).
        self.config["listener_port"] = sub.port

        await self._run_push_register(push_def)

    async def _start_push_udp_listener(self, push_def: dict[str, Any]) -> None:
        """Open the shared UDP port the device sends its packets to.

        The device is set (on the device, by whoever installs it) to send to
        this computer's address and the port the push block names; nothing
        registers. Each datagram from the device's own address arrives at
        :meth:`_handle_push_datagram`, which a YAML driver's response rules
        read and a Python driver overrides to decode a binary packet.
        """
        from openavc.transport import udp_listener

        raw_port = self._resolve_push_value(push_def.get("port"))
        port = push_port(push_def, self.config)
        if not _port_in_range(port):
            log.warning(
                f"[{self.device_id}] push: cannot listen — port {raw_port!r} "
                f"did not resolve to a UDP port 1-65535 (check the device's "
                f"notification settings fields)"
            )
            return
        try:
            self._push_subscription = await udp_listener.subscribe(
                port=port,
                source_ip=self._push_source_ip(),
                callback=self._counting_push(self._handle_push_datagram),
                name=self.device_id,
                # Connected to the simulator: every simulated device sends
                # from 127.0.0.1, so its packets are told apart by the port
                # its simulator sends from (udp_listener's docstring).
                source_port=(
                    self.udp_redirect[1] if self.udp_redirect is not None else None
                ),
            )
        except OSError as e:
            hint = (
                "a port below 1024 needs permission to listen on"
                if isinstance(e, PermissionError)
                else "is another program using it?"
            )
            log.warning(
                f"[{self.device_id}] push: could not listen on UDP port "
                f"{port}: {e} ({hint}). Changes show at the next poll."
            )

    def _start_push_sse(self, push_def: dict[str, Any]) -> None:
        """Open the driver's declared SSE event stream(s).

        SSE rides the driver's own HTTP session (auth + TLS settings apply),
        so it needs the HTTP transport — no listener, no source demux. The
        stream owns reconnect/backoff; a stream that can't connect is a
        logged gap covered by polling, never a device fault. A ``session:``
        block turns the stream into a device session: the stream reports the
        id the device gave it and the registration commands run against it,
        on every (re)open.
        """
        transport = self.transport
        if transport is None or not hasattr(transport, "open_event_stream"):
            log.warning(
                f"[{self.device_id}] push: type 'sse' requires the HTTP "
                f"transport"
            )
            return
        paths = _sse_paths(push_def)
        idle_timeout = _sse_idle_timeout(push_def)
        session = self._sse_session(push_def, paths)
        streams = []
        for raw in paths:
            path = self._sse_path(raw)
            if path is None:
                continue
            streams.append(
                transport.open_event_stream(
                    path,
                    self._counting_push(self._handle_push_event),
                    idle_timeout=idle_timeout,
                    name=self.device_id,
                    session=session,
                    on_session=self._on_push_session if session else None,
                )
            )
        if streams:
            self._push_subscription = streams

    def _sse_session(
        self, push_def: dict[str, Any], paths: list[Any],
    ) -> dict[str, Any] | None:
        """The sse block's ``session:`` block, or None for a plain stream."""
        session = push_def.get("session")
        if not isinstance(session, dict):
            return None
        if len(paths) > 1:
            # One session per stream would leave {push_session} ambiguous;
            # the loader refuses this shape, this is the runtime backstop.
            log.warning(
                f"[{self.device_id}] push: a session block needs a single "
                f"event-stream path; ignoring the session"
            )
            return None
        return session

    def _sse_path(self, raw: Any) -> str | None:
        """One event-stream path resolved from the config, or None (and a
        warning) when it does not resolve to a URL path."""
        path = str(self._resolve_push_value(raw) or "").strip()
        if not path.startswith("/"):
            log.warning(
                f"[{self.device_id}] push: event-stream path {raw!r} "
                f"did not resolve to a URL path (check the device's "
                f"config fields)"
            )
            return None
        return path

    async def _start_push_http_listener(self, push_def: dict[str, Any]) -> None:
        """Register an inbound HTTP callback for this device.

        The platform's web listener receives the device's POSTs (webhook
        registrations, GENA NOTIFY) at a per-device path; the body feeds the
        normal response path. The callback URL the device must be told about
        is exposed as :attr:`push_callback_url` — send it in an ``on_connect``
        registration command (``{push_callback_url}`` substitutes in YAML
        command bodies).
        """
        from openavc.transport.http_listener import callback_url, subscribe

        host = self._push_source_ip()
        try:
            self._push_subscription = await subscribe(
                device_id=self.device_id,
                source_ip=host,
                callback=self._counting_push(self._handle_push_http),
                name=self.device_id,
            )
        except Exception:
            log.warning(
                f"[{self.device_id}] push: could not register the inbound "
                f"HTTP callback",
                exc_info=True,
            )
            return
        self._push_callback_url = callback_url(
            host, self._push_subscription.path
        )
        log.info(
            f"[{self.device_id}] push: inbound HTTP callback at "
            f"{self._push_callback_url}"
        )

    @property
    def push_callback_url(self) -> str:
        """The URL a device must deliver push notifications to (http_listener
        shape), or ``""`` outside an active subscription. Python drivers that
        manage their own device-side registration (e.g. GENA SUBSCRIBE) read
        it after ``super().connect()``."""
        return self._push_callback_url

    # --- Registering with the device ----------------------------------------

    def _push_register_entries(self, push_def: dict[str, Any]) -> list[dict[str, Any]]:
        """Normalize ``push.register`` to a list of entry dicts.

        Accepted forms: one command name; a list of names; or dicts of
        ``{command, when?, each_child?}`` -- ``when`` names a config field
        the entry needs truthy (an integrator's opt-in switch), ``each_child``
        names a child type the command runs once per registered child of,
        the child's local id passed as the command's child_id parameter.
        The loader validates the shape; this only reads it.
        """
        raw = push_def.get("register")
        if raw is None or raw == "":
            return []
        items = raw if isinstance(raw, list) else [raw]
        entries: list[dict[str, Any]] = []
        for item in items:
            if isinstance(item, str) and item.strip():
                entries.append({"command": item.strip()})
            elif isinstance(item, dict) and isinstance(item.get("command"), str):
                entries.append(item)
        return entries

    def _push_entry_enabled(self, entry: dict[str, Any]) -> bool:
        """``when: <config_field>``: run only while that field is truthy."""
        field = entry.get("when")
        if field is None:
            return True
        return reads_as_on(self.config.get(str(field)))

    def _child_id_param(self, command: str, child_type: str) -> str | None:
        """The name of ``command``'s child_id parameter for ``child_type``."""
        cmd = (self.DRIVER_INFO.get("commands") or {}).get(command)
        params = cmd.get("params") if isinstance(cmd, dict) else None
        if not isinstance(params, dict):
            return None
        return next(
            (pname for pname, pdef in params.items()
             if _is_child_id_param(pdef, child_type)),
            None,
        )

    async def _run_push_register(self, push_def: dict[str, Any]) -> None:
        """Run the push block's registration command(s).

        Called once the channel is open: after the dial-back listener binds
        (tcp_listener) or the device has named the session (sse), and again
        on every reconnect or reopen, since device-side registrations do not
        survive a reboot, a link cut or a new session. A failure never takes
        the device down -- polling still covers it -- and is logged once per
        entry until that entry succeeds again, because a registration the
        device refuses is refused on every reopen.
        """
        for entry in self._push_register_entries(push_def):
            if not self._push_entry_enabled(entry):
                continue
            command = str(entry["command"])
            for params in self._push_register_runs(command, entry):
                await self._send_push_register(command, params)

    def _push_register_runs(
        self, command: str, entry: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """The params of each run one entry makes: one run with none, or one
        per registered child of its ``each_child`` type (none, with a
        warning, when the command takes no child id of that type)."""
        child_type = entry.get("each_child")
        if not child_type:
            return [{}]
        pname = self._child_id_param(command, str(child_type))
        if pname is None:
            log.warning(
                f"[{self.device_id}] push: register command "
                f"{command!r} has no child_id parameter for "
                f"{child_type!r}; skipping"
            )
            return []
        return [
            {pname: local_id}
            for local_id in self.list_children(str(child_type))
        ]

    async def _send_push_register(self, command: str, params: dict[str, Any]) -> None:
        """Send one registration; a refusal is warned about once until that
        registration succeeds again, then drops to debug."""
        label = f"{command} {params}" if params else command
        try:
            await self.send_command(command, params)
        except Exception as e:
            msg = (
                f"[{self.device_id}] push: registration command "
                f"{label} failed: {e} — the device will not push "
                f"what it registers until this succeeds (polling "
                f"still covers it)"
            )
            if label in self._push_register_failed:
                log.debug(msg)
            else:
                log.warning(msg)
                self._push_register_failed.add(label)
        else:
            self._push_register_failed.discard(label)

    async def _on_push_session(self, session_id: str | None) -> None:
        """The sse stream named its session (or lost it).

        With an id, the registration commands run against it: ``{push_session}``
        substitutes the id into their paths and bodies. With None the token
        is withdrawn, so a command sent between sessions carries the literal
        token (loud in the device log) rather than a stale id.
        """
        if not session_id:
            self._push_session = ""
            return
        self._push_session = session_id
        push_def = self.DRIVER_INFO.get("push")
        if isinstance(push_def, dict):
            await self._run_push_register(push_def)

    @property
    def push_session(self) -> str:
        """The id of the live sse session (``push.session`` shape), or ``""``
        outside one. Python drivers that register in code read it from the
        ``_on_push_session`` hook."""
        return self._push_session

    # --- Closing the channel ------------------------------------------------

    async def _stop_push(self) -> None:
        """Drop the push subscription(s) (no-op when none is active)."""
        sub = self._push_subscription
        self._push_subscription = None
        self._push_callback_url = ""
        self._push_register_failed.clear()
        if sub is None:
            self._push_session = ""
            return
        # De-register while the session id is still known (an sse
        # unregister addresses the session), then withdraw it.
        await self._push_unregister()
        self._push_session = ""
        for handle in sub if isinstance(sub, list) else [sub]:
            try:
                await handle.close()
            except Exception:
                log.debug(
                    f"[{self.device_id}] Error closing push subscription",
                    exc_info=True,
                )

    async def _push_unregister(self) -> None:
        """Best-effort de-registration on a graceful disconnect.

        Devices in the dial-back shape hold a limited subscriber list (the
        Panasonic cameras allow 5) and keep a slot busy as long as deliveries
        succeed — so a device that is being removed while its shared listener
        port stays open for others would hold its slot indefinitely. Only
        attempted while the session is still up: on transport loss (and on
        the stale-subscription drop at reconnect) there is nobody to talk to.
        """
        push_def = self.DRIVER_INFO.get("push")
        if not isinstance(push_def, dict):
            return
        unregister = push_def.get("unregister")
        if not unregister or not self._push_link_up():
            return
        if isinstance(push_def.get("session"), dict) and not self._push_session:
            # An sse session that never opened has nothing to end.
            return
        try:
            await asyncio.wait_for(
                self.send_command(str(unregister)), timeout=5.0
            )
        except Exception as e:
            log.debug(
                f"[{self.device_id}] push: de-registration command "
                f"{unregister!r} failed: {e}"
            )

    def _push_link_up(self) -> bool:
        """The driver is connected and its transport is up: somebody is there
        to receive a de-registration."""
        return (
            self._connected
            and self.transport is not None
            and self.transport.connected
        )

    # --- Deliveries ----------------------------------------------------------

    async def _handle_push_datagram(
        self, data: bytes, source: tuple[str, int]
    ) -> None:
        """Feed a push datagram through the normal response path.

        One datagram may carry several protocol frames; when the driver
        declares a delimiter, split on it and dispatch each frame separately
        (first-match-wins response matching would otherwise apply only one
        rule to the whole datagram).
        """
        delimiter = self._resolve_delimiter()
        if delimiter:
            for part in data.split(delimiter):
                if part.strip():
                    await self.on_data_received(part)
        elif data:
            await self.on_data_received(data)

    async def _handle_push_event(self, data: bytes) -> None:
        """Feed one SSE event's data block through the normal response path.

        Unlike a datagram, an SSE event is already one complete framed
        message (the transport assembled its data lines), so it dispatches
        whole — exactly like an HTTP poll response body. No delimiter split:
        SSE payloads are JSON bodies, and splitting one on a line delimiter
        would break multi-field parsing.
        """
        if data.strip():
            await self.on_data_received(data)

    async def _handle_push_http(self, request: Any) -> None:
        """Feed one inbound HTTP push body through the normal response path.

        Like an SSE event, a push request's body is one complete framed
        message (an XML/JSON document per delivery), so it dispatches whole —
        exactly like an HTTP poll response body. Python drivers that need
        the request's headers (e.g. GENA's SID/SEQ) override this.
        """
        body = request.body
        if body and body.strip():
            await self.on_data_received(body)
