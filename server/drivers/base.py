"""
OpenAVC BaseDriver — abstract base class for all device drivers.

Every device driver inherits from this class. A driver encapsulates all
knowledge of how to communicate with a specific piece of AV equipment.

Subclasses must:
    1. Set DRIVER_INFO with metadata, commands, state variables, config schema
    2. Implement send_command()
    3. Optionally override connect(), disconnect(), on_data_received(), poll()

Auto-transport: The default connect() reads DRIVER_INFO["transport"] and
self.config to create a TCP or serial transport automatically. Drivers with
custom connection logic (e.g., PJLink greeting handshake) can override
connect() as before.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any

from server.core.event_bus import EventBus
from server.core.state_store import StateStore
from server.transport.frame_parsers import FrameParser
from server.utils.logger import get_logger

log = get_logger(__name__)


class BaseDriver(ABC):
    """Abstract base class for all device drivers."""

    # Subclasses MUST override this with their metadata dict
    DRIVER_INFO: dict[str, Any] = {}

    def __init__(
        self,
        device_id: str,
        config: dict[str, Any],
        state: StateStore,
        events: EventBus,
    ):
        self.device_id = device_id
        self.config = config
        self.state = state
        self.events = events
        self.transport: Any = None
        self._poll_task: asyncio.Task | None = None
        self._connected = False
        self._last_poll_success: float = 0.0
        # Last transport error captured before the live transport is torn down
        # on a failure path, so the DeviceManager's connection-fault classifier
        # can still read it after self.transport has been nulled. Cleared at
        # the start of each connect() attempt so a stale cause can't leak into
        # a later, unrelated failure.
        self._last_transport_error: str = ""
        # Strong refs to fire-and-forget tasks (disconnect cleanup) so the GC
        # can't collect them mid-run — a bare create_task is only weakly held.
        self._bg_tasks: set[asyncio.Task] = set()
        # Registered child entities: {child_type: {local_id: register_epoch}}.
        # The inner mapping is a dict (not a set) so it preserves insertion
        # order, which makes list_children() output stable for tests and IDE
        # displays. The value is a monotonic registration epoch (always
        # truthy) used by poll_children to detect a child that was
        # deregistered+re-registered mid-poll (its state was reset) so a stale
        # write doesn't clobber the reset (ABA guard).
        self._children: dict[str, dict[int, int]] = {}
        self._child_register_seq = 0
        # Set by the platform only for the duration of a run_setup_action call;
        # backs request_config_update / request_reconnect. None at all other times.
        self._setup_context: Any = None
        # Project-side child metadata: {child_type: {local_id_padded: {label, config}}}.
        # Populated by DeviceManager.add_device after construction (via
        # set_project_child_entities) so existing driver subclasses with a
        # fixed __init__ signature don't need to change. register_child
        # consults this to seed the per-child `label` state key without
        # the driver having to plumb the project schema itself.
        self._project_child_entities: dict[str, dict[str, dict[str, Any]]] = {}

        # Initialize state variables from DRIVER_INFO
        self._init_state_variables()

    def set_project_child_entities(
        self, child_entities: dict[str, dict[str, dict[str, Any]]] | None,
    ) -> None:
        """Set the project-side child metadata for this driver instance.

        Called by DeviceManager from the project's DeviceConfig.child_entities
        after construction. Keeps the BaseDriver __init__ signature stable
        for existing driver subclasses that define their own __init__.
        """
        self._project_child_entities = dict(child_entities or {})

    @property
    def connected(self) -> bool:
        """True only if both driver and transport report connected."""
        if not self._connected:
            return False
        if self.transport is None:
            return False
        return getattr(self.transport, "connected", False)

    @property
    def last_transport_error(self) -> str:
        """The transport's last error string, retained across teardown.

        Read by the DeviceManager's connection-fault classifier — the live
        transport is nulled on every failure path, so the raw cause (an SSH
        ``Permission denied``, a refused socket) would otherwise be lost before
        the offline reason is computed.
        """
        return self._last_transport_error

    def _stash_transport_error(self) -> None:
        """Capture the live transport's ``last_error`` before it's torn down.

        A no-op when there's no transport or it reports no error, so it never
        overwrites a real cause with an empty string.
        """
        transport = self.transport
        if transport is not None:
            err = getattr(transport, "last_error", "") or ""
            if err:
                self._last_transport_error = err

    @staticmethod
    def _numeric_default(var_def: dict[str, Any], *, as_int: bool) -> int | float:
        """Default for an integer/number/float state var: its declared ``min``
        (or 0), coerced to the right numeric type.

        A non-numeric ``min`` (e.g. a hand-edited driver with ``min: "low"``)
        is an authoring bug; fall back to 0 with a warning rather than crashing
        driver instantiation with an uncaught ValueError.
        """
        raw = var_def.get("min", 0)
        try:
            return int(raw) if as_int else float(raw)
        except (TypeError, ValueError):
            log.warning(
                "state variable declares a non-numeric 'min' %r; defaulting to 0",
                raw,
            )
            return 0 if as_int else 0.0

    def _init_state_variables(self) -> None:
        """Register all state variables from DRIVER_INFO with default values."""
        state_vars = self.DRIVER_INFO.get("state_variables", {})
        for prop_name, prop_info in state_vars.items():
            var_type = prop_info.get("type", "string")
            if var_type == "boolean":
                default: Any = False
            elif var_type == "integer":
                default = self._numeric_default(prop_info, as_int=True)
            elif var_type in ("number", "float"):
                # 'float' is an accepted type alias for 'number' (driver loader
                # + schema). Both must seed a numeric 0.0, not '' — otherwise a
                # consumer reading the var before the first poll gets a string
                # where a number is expected.
                default = self._numeric_default(prop_info, as_int=False)
            elif var_type == "enum":
                values = prop_info.get("values", [])
                default = values[0] if values else ""
            else:
                default = ""
            self.set_state(prop_name, default)
        # Always set a connected state
        self.set_state("connected", False)

    # --- Connection lifecycle (concrete with auto-transport) ---

    async def connect(self) -> None:
        """
        Establish connection to the device using auto-transport.

        Reads DRIVER_INFO["transport"] and self.config to create the
        appropriate transport (TCP or serial). Override this method for
        custom connection logic (e.g., greeting handshakes).
        """
        # Start each attempt with a clean slate so a previous failure's cause
        # can't be misattributed to this one by the fault classifier.
        self._last_transport_error = ""
        if self.transport:
            try:
                await self.transport.close()
            except Exception:
                pass
            self.transport = None

        # A device's config may override the driver's default transport (e.g. a
        # CLI driver that defaults to "ssh" in production but connects over raw
        # "tcp" to a CLI simulator). Falls back to the driver default when unset,
        # so existing devices are unaffected.
        transport_type = self.config.get("transport") or self.DRIVER_INFO.get(
            "transport", "tcp"
        )
        frame_parser = self._create_frame_parser()
        delimiter = self._resolve_delimiter()

        # Get control interface binding (if configured)
        from server.system_config import get_system_config
        control_ip = get_system_config().get("network", "control_interface")

        if transport_type == "tcp":
            from server.transport.tcp import TCPTransport

            host = self.config.get("host", "")
            port = self._required_port()
            delay = self.config.get("inter_command_delay", 0.0)

            self.transport = await TCPTransport.create(
                host=host,
                port=port,
                on_data=self.on_data_received,
                on_disconnect=self._handle_transport_disconnect,
                delimiter=delimiter,
                frame_parser=frame_parser,
                inter_command_delay=delay,
                name=self.device_id,
                local_addr=(control_ip, 0) if control_ip else None,
            )
        elif transport_type == "serial":
            from server.transport.serial_transport import SerialTransport

            serial_port = self.config.get("port", "")
            delay = self.config.get("inter_command_delay", 0.0)
            baudrate, bytesize, parity, stopbits = self._coerce_serial_params(
                self.config
            )

            self.transport = await SerialTransport.create(
                port=serial_port,
                baudrate=baudrate,
                on_data=self.on_data_received,
                on_disconnect=self._handle_transport_disconnect,
                delimiter=delimiter,
                frame_parser=frame_parser,
                inter_command_delay=delay,
                bytesize=bytesize,
                parity=parity,
                stopbits=stopbits,
                name=self.device_id,
            )
        elif transport_type == "udp":
            from server.transport.udp import UDPTransport

            host = self.config.get("host", "")
            port = self._required_port()
            delay = self.config.get("inter_command_delay", 0.0)

            self.transport = UDPTransport(
                host=host,
                port=port,
                on_data=self.on_data_received,
                on_disconnect=self._handle_transport_disconnect,
                inter_command_delay=delay,
                name=self.device_id,
            )
            await self.transport.open(
                local_addr=control_ip or None,
            )
        elif transport_type == "osc":
            from server.transport.osc import OSCTransport

            host = self.config.get("host", "")
            port = self._required_port()
            listen_port = self.config.get("listen_port", 0)
            delay = self.config.get("inter_command_delay", 0.0)

            self.transport = OSCTransport(
                host=host,
                port=port,
                listen_port=listen_port,
                on_data=self.on_data_received,
                on_disconnect=self._handle_transport_disconnect,
                inter_command_delay=delay,
                name=self.device_id,
            )
            await self.transport.open(
                local_addr=control_ip or None,
            )
        elif transport_type == "http":
            from server.transport.http_client import HTTPClientTransport

            host = self.config.get("host", "")
            # Don't use .get("port", 80) — the sentinel-default makes an
            # explicit `port: 80, ssl: true` indistinguishable from "not set",
            # so the next branch silently rewrites it to 443 (A66). Read
            # without a default and apply the scheme-appropriate fallback only
            # when port is genuinely missing.
            port = self.config.get("port")
            use_ssl = self.config.get("ssl", False)
            scheme = "https" if use_ssl else "http"
            if port is None:
                port = 443 if use_ssl else 80
            base_url = f"{scheme}://{host}:{port}"

            # Build credentials from config
            auth_type = self.config.get("auth_type", "none")
            credentials = {}
            if auth_type in ("basic", "digest"):
                credentials["username"] = self.config.get("username", "")
                credentials["password"] = self.config.get("password", "")
            elif auth_type == "bearer":
                credentials["token"] = self.config.get("token", "")
            elif auth_type == "api_key":
                credentials["header"] = self.config.get("api_key_header", "X-API-Key")
                credentials["key"] = self.config.get("api_key", "")

            self.transport = HTTPClientTransport(
                base_url=base_url,
                auth_type=auth_type,
                credentials=credentials,
                verify_ssl=self.config.get("verify_ssl", True),
                default_headers=self.config.get("default_headers", {}),
                timeout=self.config.get("timeout", 10.0),
                name=self.device_id,
                local_address=control_ip or None,
            )
            await self.transport.open()
        elif transport_type == "ssh":
            from server.transport.ssh import SSHTransport

            host = self.config.get("host", "")
            port = int(self.config.get("port", 22) or 22)
            username = self.config.get("username", "")
            auth_method = self.config.get("ssh_auth_method", "key")
            known_hosts = self.config.get("known_hosts_path")
            if not known_hosts:
                from server.system_config import get_data_dir
                known_hosts = str(get_data_dir() / "ssh" / "known_hosts")

            self.transport = await SSHTransport.create(
                host=host,
                port=port,
                username=username,
                on_data=self.on_data_received,
                on_disconnect=self._handle_transport_disconnect,
                auth_method=auth_method,
                password=self.config.get("password") or None,
                key_path=self.config.get("key_path") or None,
                known_hosts_path=known_hosts,
                host_key_policy=self.config.get("host_key_policy", "accept-new"),
                connect_timeout=float(self.config.get("connect_timeout", 15.0)),
                inter_command_delay=self.config.get("inter_command_delay", 0.0),
                name=self.device_id,
            )
        else:
            raise ValueError(f"Unsupported transport type: {transport_type}")

        # For connectionless transports (OSC, HTTP), verify the remote host
        # is actually reachable before reporting connected. TCP and serial
        # validate during open/create. UDP is genuinely connectionless and
        # has no transport-level probe — UDP drivers MUST declare a positive
        # `poll_interval` so the periodic poll() round-trip is the reachability
        # signal; without it, `connected` stays True against a dead host
        # forever (A68). Set verify_timeout: 0 in config to skip the
        # pre-connect probe on OSC/HTTP.
        verify_timeout = self.config.get("verify_timeout", 3.0)
        if verify_timeout > 0 and hasattr(self.transport, "verify"):
            if not await self.transport.verify(timeout=verify_timeout):
                # Retain the transport's underlying cause (refused / timeout)
                # before tearing it down — the raise below only says "not
                # responding", which the classifier would read as no_response.
                self._stash_transport_error()
                if self.transport:
                    await self.transport.close()
                    self.transport = None
                raise ConnectionError(
                    f"Device at {self.config.get('host', '?')}:"
                    f"{self.config.get('port', '?')} is not responding"
                )

        try:
            # Device-specific session setup (e.g. an SSH/CLI driver entering
            # privileged mode and disabling output paging) runs before we
            # report connected so the first poll sees a ready session. A raise
            # here aborts the connection and the transport is cleaned up below.
            await self._post_connect()
            self._connected = True
            self.set_state("connected", True)
            await self.events.emit(f"device.connected.{self.device_id}")
            log.info(f"[{self.device_id}] Connected via {transport_type}")
        except Exception:
            # Clean up transport if post-connect setup fails. Stash the
            # transport's last error first (e.g. an SSH auth failure surfaces
            # as ssh stderr on the transport, not in this exception).
            self._stash_transport_error()
            if self.transport:
                await self.transport.close()
                self.transport = None
            self._connected = False
            raise

        # Start polling if configured
        poll_interval = self.config.get("poll_interval", 0)
        if poll_interval > 0:
            await self.start_polling(poll_interval)

    async def disconnect(self) -> None:
        """
        Gracefully close the connection.

        Stops polling, closes transport, and updates state.
        Override for custom disconnect logic.
        """
        await self.stop_polling()
        if self.transport:
            await self.transport.close()
            self.transport = None
        self._connected = False
        self.set_state("connected", False)
        await self.events.emit(f"device.disconnected.{self.device_id}")
        log.info(f"[{self.device_id}] Disconnected")

    # --- Abstract: drivers must implement ---

    @abstractmethod
    async def send_command(self, command: str, params: dict[str, Any] | None = None) -> Any:
        """
        Execute a named command.

        Translates the command name + params into protocol-specific bytes
        and sends via the transport.
        """

    # --- Device Settings ---

    async def set_device_setting(self, key: str, value: Any) -> Any:
        """
        Write a device setting value to the device.

        Override in subclasses to handle device-specific write logic.
        The default implementation raises NotImplementedError so callers
        know the driver hasn't implemented settings writes.

        Args:
            key: The setting key from DRIVER_INFO["device_settings"].
            value: The new value to write.

        Returns:
            Result of the write operation (driver-specific).
        """
        raise NotImplementedError(
            f"Driver {self.DRIVER_INFO.get('id', '?')} does not implement set_device_setting"
        )

    # --- Setup / provisioning actions ---
    #
    # A "setup action" is a driver-declared provisioning wizard (an action of
    # kind:"setup", see server/drivers/actions.py). Unlike a command, it can run
    # while the device is OFFLINE, brings its own out-of-band transport, reports
    # multi-step progress, and may rewrite the device's connection config and
    # reconnect on success. The platform invokes run_setup_action with a live
    # `progress` callback and, for the duration of the call, a setup context that
    # backs request_config_update / request_reconnect. Everything device-specific
    # (which transport, which commands, what the config delta is) lives in the
    # driver's handler; the platform stays generic.

    async def run_setup_action(
        self,
        action_id: str,
        params: dict[str, Any],
        progress: Callable[..., Awaitable[None]],
    ) -> dict[str, Any]:
        """Run a declared setup action. May run while the device is offline.

        ``progress(step, pct=None)`` is awaitable and streams a live progress
        line to the UI (``pct`` is an optional 0-100 percentage). The handler
        may open its own transports independent of the device's normal one, and
        may call ``await self.request_config_update({...})`` to persist new
        connection settings and ``await self.request_reconnect()`` to bring the
        device back online over them. Return a result dict; raise to report
        failure. Default raises NotImplementedError.
        """
        raise NotImplementedError(
            f"Driver {self.DRIVER_INFO.get('id', '?')} does not implement "
            f"run_setup_action"
        )

    async def request_config_update(self, delta: dict[str, Any]) -> None:
        """Persist a connection/config delta for this device (from a setup
        action handler). Connection fields land in the project's connections
        table, the rest in the device config; the live driver's ``self.config``
        is updated so the next connect() uses the new settings.

        Only callable from inside ``run_setup_action`` — the platform installs
        the backing context for the duration of the run.
        """
        if self._setup_context is None:
            raise RuntimeError(
                "request_config_update is only available during a setup action"
            )
        await self._setup_context.apply_config_update(delta)

    async def request_reconnect(self) -> None:
        """Reconnect the device using its current (possibly just-updated)
        config. Typically the final step of a setup action. Only callable from
        inside ``run_setup_action``.
        """
        if self._setup_context is None:
            raise RuntimeError(
                "request_reconnect is only available during a setup action"
            )
        await self._setup_context.reconnect()

    def _set_setup_context(self, context: Any) -> None:
        """Platform-only: install (or clear, with None) the setup context that
        backs request_config_update / request_reconnect for one setup run.
        """
        self._setup_context = context

    # --- Optional overrides ---

    async def on_data_received(self, data: bytes) -> None:
        """
        Called by the transport when data arrives from the device.

        Override in the driver to implement protocol-specific parsing.
        Default: no-op.
        """

    async def poll(self) -> None:
        """
        Called periodically to request device status.

        Override to send status query commands. Default: no-op.

        Contract: implementations MUST propagate transport-level errors
        (ConnectionError, TimeoutError, OSError, httpx.ConnectError,
        httpx.TimeoutException). The polling loop catches these and counts
        them toward the missed-poll watchdog. Swallowing transport errors
        here causes `device.<id>.connected` to lie when the device is
        unreachable.

        Protocol-level errors (unexpected response shape, expected device
        states like "in standby") may be handled inside poll() — those
        indicate the device is reachable but not in a queryable state.
        """

    async def _post_connect(self) -> None:
        """Hook for device-specific session setup after the transport opens and
        before the device is marked connected.

        CLI-over-SSH/telnet drivers override this to read the login banner,
        enter privileged mode, and disable output paging. Raising aborts the
        connection (the caller closes the transport). Default: no-op.
        """

    @staticmethod
    def _coerce_serial_params(config: dict[str, Any]) -> tuple[int, int, str, int | float]:
        """Coerce + validate serial params from untyped project config.

        Project config is untyped JSON, so an integrator / AI tool / hand-edit
        can store ``bytesize: "8"`` or ``stopbits: "1.5"`` as strings, or a
        flat-out invalid value. pyserial does exact membership tests and raises
        a bare ValueError at connect that the device manager then buries under
        ~120 generic reconnect attempts. Coerce string forms to the right type
        and raise a clear, actionable error for genuinely invalid values.

        Returns ``(baudrate, bytesize, parity, stopbits)``.
        """
        try:
            baudrate = int(config.get("baudrate", 9600))
        except (TypeError, ValueError):
            raise ValueError(
                f"Invalid baudrate {config.get('baudrate')!r} (must be an integer)"
            )
        try:
            bytesize = int(config.get("bytesize", 8))
        except (TypeError, ValueError):
            raise ValueError(
                f"Invalid bytesize {config.get('bytesize')!r} (must be 5, 6, 7, or 8)"
            )
        if bytesize not in (5, 6, 7, 8):
            raise ValueError(f"Invalid bytesize {bytesize} (must be 5, 6, 7, or 8)")

        parity = str(config.get("parity", "N")).upper()
        if parity not in ("N", "E", "O", "M", "S"):
            raise ValueError(
                f"Invalid parity {config.get('parity')!r} (must be one of N, E, O, M, S)"
            )

        raw_stopbits = config.get("stopbits", 1)
        try:
            stopbits_f = float(raw_stopbits)
        except (TypeError, ValueError):
            raise ValueError(
                f"Invalid stopbits {raw_stopbits!r} (must be 1, 1.5, or 2)"
            )
        # pyserial wants ints for 1/2 and the float 1.5 for one-and-a-half.
        stopbits: int | float
        if stopbits_f == 1.0:
            stopbits = 1
        elif stopbits_f == 2.0:
            stopbits = 2
        elif stopbits_f == 1.5:
            stopbits = 1.5
        else:
            raise ValueError(
                f"Invalid stopbits {raw_stopbits!r} (must be 1, 1.5, or 2)"
            )
        return baudrate, bytesize, parity, stopbits

    def _required_port(self) -> int:
        """Return ``config['port']`` for TCP/UDP/OSC, or raise a clear error.

        Driver ``default_config.port`` is layered in by
        ``Engine.resolved_device_config`` before instantiation, so a
        properly-declared driver always has a port here. A missing port
        means the driver definition skipped declaring one — surface that
        as a config error instead of silently dialing port 23.
        """
        port = self.config.get("port")
        if port is None or port == "":
            driver_id = self.DRIVER_INFO.get("id", "?")
            raise ConnectionError(
                f"Device '{self.device_id}': missing 'port' in config "
                f"(driver '{driver_id}' must declare default_config.port "
                f"or the device must override it)"
            )
        try:
            return int(port)
        except (TypeError, ValueError) as e:
            driver_id = self.DRIVER_INFO.get("id", "?")
            raise ConnectionError(
                f"Device '{self.device_id}': invalid port {port!r} "
                f"(driver '{driver_id}')"
            ) from e

    def _create_frame_parser(self) -> FrameParser | None:
        """
        Hook: return a custom FrameParser for this driver.

        Override for binary protocols that need length-prefix or callable
        parsing. Return None to use delimiter-based framing (the default).
        """
        return None

    def _resolve_delimiter(self) -> bytes | None:
        """
        Hook: resolve the message delimiter for this driver.

        Checks DRIVER_INFO["delimiter"] first, then self.config["delimiter"],
        then falls back to b"\\r". Override for custom logic.
        """
        from server.transport.binary_helpers import encode_escape_sequences

        # Check DRIVER_INFO
        delim = self.DRIVER_INFO.get("delimiter")
        if delim is not None:
            if isinstance(delim, bytes):
                return delim
            return encode_escape_sequences(delim)

        # Check config
        delim = self.config.get("delimiter")
        if delim is not None:
            if isinstance(delim, bytes):
                return delim
            return encode_escape_sequences(delim)

        # Default
        return b"\r"

    async def _verify_reachable(
        self, host: str, port: int, timeout: float = 3.0
    ) -> bool:
        """
        Verify that a TCP host:port is reachable.

        Drivers that don't use a platform transport (raw httpx clients,
        websocket clients, etc.) should call this in connect() before
        setting connected=True, so that loading the project against an
        unreachable host fails fast instead of declaring a phantom
        connection that has to time out via the watchdog.

        Returns True if a TCP connection can be opened within the timeout,
        False otherwise.
        """
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port),
                timeout=timeout,
            )
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            return True
        except (OSError, asyncio.TimeoutError):
            return False

    def _handle_transport_disconnect(self) -> None:
        """
        Standard disconnect handler for transport callbacks.

        Sets connected state to False, then schedules cleanup (stop polling,
        close the now-dead transport, emit the disconnect event). Override for
        custom disconnect behavior.
        """
        self._connected = False
        self.set_state("connected", False)
        log.warning(f"[{self.device_id}] Connection lost")
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            log.warning(
                f"[{self.device_id}] No event loop during disconnect — "
                f"polling/transport may not be cleaned up"
            )
            return
        # Keep a strong reference until the task finishes — a bare create_task
        # is only weakly held and can be GC'd before it runs, orphaning the
        # poll loop or skipping the disconnect event.
        task = loop.create_task(self._on_disconnect_cleanup())
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    async def _on_disconnect_cleanup(self) -> None:
        """Stop polling, close the dead transport, and emit the disconnect event.

        Closing the transport here (not only on the graceful ``disconnect()``
        path) releases the socket / bound UDP port / pooled HTTP client
        immediately instead of holding it for the whole reconnect-backoff
        window (up to ~1h). The live ref is nulled so auto-reconnect rebuilds
        it; the disconnect event is emitted last so the reconnect's connect()
        doesn't race the close. Closing is safe in the watchdog path too — a
        transport's close() sets its own connected=False before tearing down,
        so it never re-enters this handler.
        """
        await self.stop_polling()
        # Capture the transport's last error before nulling it, so the
        # DeviceManager can classify the offline reason from the event handler
        # (which runs after this teardown).
        self._stash_transport_error()
        transport = self.transport
        self.transport = None
        if transport is not None:
            try:
                await transport.close()
            except Exception:
                log.debug(
                    f"[{self.device_id}] Error closing transport on disconnect",
                    exc_info=True,
                )
        try:
            await self.events.emit(f"device.disconnected.{self.device_id}")
        except Exception:
            log.debug(
                f"[{self.device_id}] Failed to emit disconnect event",
                exc_info=True,
            )

    # --- Polling ---

    async def start_polling(self, interval: float) -> None:
        """Start a background polling loop at the given interval (seconds)."""
        if interval <= 0:
            return
        await self.stop_polling()
        self._poll_task = asyncio.create_task(self._poll_loop(interval))
        log.debug(f"[{self.device_id}] Polling started (every {interval}s)")

    async def stop_polling(self) -> None:
        """Cancel the polling background task."""
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None
            log.debug(f"[{self.device_id}] Polling stopped")

    async def _poll_loop(self, interval: float) -> None:
        """Background loop that calls self.poll() periodically.

        Tracks whether each poll() returned cleanly. When N consecutive polls
        raise a transport-level error (ConnectionError, TimeoutError, OSError,
        or any httpx HTTP error), marks the device disconnected and exits.
        Protocol-level errors (e.g., ValueError on unexpected response shape)
        are logged via device.error.<id> but do not penalize the watchdog —
        the device is reachable, just misbehaving.

        Drivers MUST propagate transport errors from poll(). Swallowing
        httpx.ConnectError and friends in a driver's poll() causes connected
        state to lie.
        """
        import time
        try:
            import httpx
            httpx_errors: tuple = (httpx.HTTPError,)
        except ImportError:
            httpx_errors = ()

        # Clamp to >= 1: a config value of 0 (or negative, or non-numeric) would
        # make `dry_polls >= max_dry_polls` true after the first poll and mark a
        # healthy device disconnected. The watchdog can't be disabled by setting
        # 0 — the minimum is one missed poll.
        try:
            max_dry_polls = max(int(self.config.get("max_missed_polls", 3)), 1)
        except (TypeError, ValueError):
            max_dry_polls = 3
        dry_polls = 0
        # Seed at loop start so we don't false-positive before the first poll.
        self._last_poll_success = time.monotonic()

        try:
            while True:
                try:
                    await self.poll()
                    self._last_poll_success = time.monotonic()
                    dry_polls = 0
                except (ConnectionError, TimeoutError, OSError) as exc:
                    log.warning(
                        f"[{self.device_id}] Poll failed (connection): {exc}"
                    )
                    dry_polls += 1
                except httpx_errors as exc:
                    log.warning(
                        f"[{self.device_id}] Poll failed (HTTP): {exc}"
                    )
                    dry_polls += 1
                except Exception as exc:
                    log.exception(
                        f"[{self.device_id}] Unexpected error during poll"
                    )
                    try:
                        await self.events.emit(
                            f"device.error.{self.device_id}",
                            {"device_id": self.device_id, "error": str(exc)},
                        )
                    except Exception:
                        log.exception(
                            f"[{self.device_id}] Failed to emit device.error"
                        )

                if dry_polls >= max_dry_polls:
                    log.warning(
                        f"[{self.device_id}] No response for "
                        f"{dry_polls} poll cycles — marking disconnected"
                    )
                    self._handle_transport_disconnect()
                    return

                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            return

    # --- Convenience helpers ---

    def set_state(self, property_name: str, value: Any) -> None:
        """Set a state value under this device's namespace."""
        var_def = self.DRIVER_INFO.get("state_variables", {}).get(property_name)
        self._warn_on_type_mismatch(property_name, value, var_def)
        self.state.set(
            f"device.{self.device_id}.{property_name}",
            value,
            source=f"device.{self.device_id}",
        )

    def _warn_on_type_mismatch(
        self, prop_label: str, value: Any, var_def: dict | None
    ) -> None:
        """Log a debug message when a state value doesn't match its declared
        type. Helps catch driver bugs without being load-bearing — listeners
        still receive the value as written.
        """
        if not var_def or value is None:
            return
        declared = var_def.get("type", "string")
        if declared == "integer" and not isinstance(value, int):
            log.debug(
                f"[{self.device_id}] State '{prop_label}' declared as "
                f"integer but got {type(value).__name__}: {value!r}"
            )
        elif declared in ("number", "float") and not isinstance(value, (int, float)):
            log.debug(
                f"[{self.device_id}] State '{prop_label}' declared as "
                f"{declared} but got {type(value).__name__}: {value!r}"
            )
        elif declared == "boolean" and not isinstance(value, bool):
            log.debug(
                f"[{self.device_id}] State '{prop_label}' declared as "
                f"boolean but got {type(value).__name__}: {value!r}"
            )
        elif declared == "enum" and "values" in var_def:
            if str(value) not in [str(v) for v in var_def["values"]]:
                log.debug(
                    f"[{self.device_id}] State '{prop_label}' value "
                    f"{value!r} not in declared enum values {var_def['values']}"
                )

    def set_states(self, updates: dict[str, Any]) -> None:
        """Set multiple state values atomically (listeners see all changes at once)."""
        namespaced = {
            f"device.{self.device_id}.{k}": v for k, v in updates.items()
        }
        self.state.set_batch(namespaced, source=f"device.{self.device_id}")

    def get_state(self, property_name: str) -> Any:
        """Get a state value from this device's namespace."""
        return self.state.get(f"device.{self.device_id}.{property_name}")

    # --- Child entities ---
    #
    # A "child entity" is a sub-unit owned by this device: an encoder/decoder
    # on a video matrix controller, a zone on a DSP, a video wall slot on
    # a presentation switcher, etc. Drivers declare child types in
    # DRIVER_INFO["child_entity_types"] and register live instances via
    # register_child(). State for each child lives under the convention
    #
    #     device.<parent_id>.<child_type>.<local_id_padded>.<property>
    #
    # so existing fnmatch subscribers and the IDE can address children
    # without driver authors inventing their own key shapes. The platform
    # always injects a boolean `online` key per registered child so that
    # listeners can distinguish "configured but offline" from "not configured".
    # See openavc-device-children-plan.md design §1-§3 for the full design.

    # State keys the platform always provides for every registered child,
    # in addition to whatever the driver declares in
    # child_entity_types[<type>].state_variables. `online` is driver-managed
    # (the driver sets it as it observes connectivity). `label` is the
    # user-set friendly name, sourced from the project file on registration
    # and writable through the IDE / REST.
    _CHILD_RESERVED_PROPS: tuple[str, ...] = ("online", "label")

    def _child_type_def(self, child_type: str) -> dict[str, Any]:
        """Return the DRIVER_INFO child_entity_types[<type>] definition.

        Raises ValueError if the driver didn't declare this child type.
        """
        types = self.DRIVER_INFO.get("child_entity_types", {})
        if child_type not in types:
            raise ValueError(
                f"Driver {self.DRIVER_INFO.get('id', '?')} did not declare "
                f"child_entity_types[{child_type!r}]"
            )
        return types[child_type]

    def _effective_child_schema(self, child_type: str) -> dict[str, dict[str, Any]]:
        """Schema for one child instance's state variables, with the
        platform-managed `online` and `label` keys injected if the driver
        didn't already declare them.
        """
        declared = dict(self._child_type_def(child_type).get("state_variables", {}))
        declared.setdefault("online", {"type": "boolean"})
        declared.setdefault("label", {"type": "string"})
        return declared

    def _format_child_id(self, child_type: str, local_id: int) -> str:
        """Validate ``local_id`` against the declared id_format and return
        its string form (zero-padded to ``id_format.pad_width``).

        v1 only supports integer local IDs. ``local_id`` must lie inside
        [id_format.min, id_format.max] (defaults: min=1, max=unbounded).
        """
        type_def = self._child_type_def(child_type)
        id_format = type_def.get("id_format", {})
        id_kind = id_format.get("type", "integer")
        if id_kind != "integer":
            raise ValueError(
                f"Child type {child_type!r} id_format.type {id_kind!r} not "
                f"supported (only 'integer' is supported in v1)"
            )
        # bool is a subclass of int in Python; reject it so that
        # register_child("encoder", True) doesn't silently land at ID 1.
        if not isinstance(local_id, int) or isinstance(local_id, bool):
            raise TypeError(
                f"Child {child_type} local_id must be int, got "
                f"{type(local_id).__name__}: {local_id!r}"
            )
        min_id = id_format.get("min", 1)
        max_id = id_format.get("max")
        if local_id < min_id:
            raise ValueError(
                f"Child {child_type} local_id {local_id} < min {min_id}"
            )
        if max_id is not None and local_id > max_id:
            raise ValueError(
                f"Child {child_type} local_id {local_id} > max {max_id}"
            )
        pad = id_format.get("pad_width", 0)
        return f"{local_id:0{pad}d}" if pad else str(local_id)

    def _child_state_key(self, child_type: str, local_id: int, prop: str) -> str:
        padded = self._format_child_id(child_type, local_id)
        return f"device.{self.device_id}.{child_type}.{padded}.{prop}"

    def _child_state_prefix(self, child_type: str, local_id: int) -> str:
        padded = self._format_child_id(child_type, local_id)
        return f"device.{self.device_id}.{child_type}.{padded}"

    def _validate_child_prop(self, child_type: str, prop: str) -> None:
        schema = self._effective_child_schema(child_type)
        if prop not in schema:
            raise ValueError(
                f"Child {child_type} property {prop!r} not declared in "
                f"child_entity_types[{child_type!r}].state_variables"
            )

    @staticmethod
    def _default_for_var_def(var_def: dict[str, Any]) -> Any:
        """Default value for a declared state variable, matching the rules
        used by _init_state_variables() so per-child defaults are consistent
        with per-device ones.
        """
        var_type = var_def.get("type", "string")
        if var_type == "boolean":
            return False
        if var_type == "integer":
            return BaseDriver._numeric_default(var_def, as_int=True)
        if var_type in ("number", "float"):
            return BaseDriver._numeric_default(var_def, as_int=False)
        if var_type == "enum":
            values = var_def.get("values", [])
            return values[0] if values else ""
        return ""

    def register_child(
        self,
        child_type: str,
        local_id: int,
        initial_state: dict[str, Any] | None = None,
    ) -> None:
        """Tell the platform a child entity exists. Creates its state keys
        in one atomic batch.

        Subsequent calls with the same (child_type, local_id) are a silent
        no-op so drivers can call this opportunistically from a poll loop
        without re-initializing state. To overwrite a child's state, use
        ``set_child_state`` / ``set_child_state_batch`` instead.

        ``initial_state`` overrides per-prop defaults. The platform-managed
        ``online`` key defaults to True if not specified in ``initial_state``.
        Unknown props in ``initial_state`` raise ValueError.
        """
        self._format_child_id(child_type, local_id)   # validates id range
        bucket = self._children.setdefault(child_type, {})
        if local_id in bucket:
            return  # idempotent — already registered
        # Stamp a fresh registration epoch. A deregister+re-register (which
        # resets the child's state) bumps it, so poll_children can tell a
        # re-registered child from the one it snapshotted (ABA guard).
        self._child_register_seq += 1
        bucket[local_id] = self._child_register_seq

        schema = self._effective_child_schema(child_type)
        overrides = dict(initial_state or {})

        # Reject unknown props up-front so the driver sees the error before
        # we touch the state store.
        for prop in overrides:
            if prop not in schema:
                # Roll back the registration record so a retry can succeed
                # after the driver fixes the call.
                del bucket[local_id]
                if not bucket:
                    del self._children[child_type]
                raise ValueError(
                    f"Child {child_type} initial_state property {prop!r} "
                    f"not declared in child_entity_types[{child_type!r}]"
                    f".state_variables"
                )

        # Project-side label: if the project file has a ChildEntityConfig
        # entry for this (type, padded_id), use its `label` as the default
        # so listeners see the user's name immediately, not "" then a
        # delayed update once the IDE re-pushes it.
        padded = self._format_child_id(child_type, local_id)
        project_entry = self._project_child_entities.get(child_type, {}).get(padded)
        project_label = project_entry.get("label", "") if project_entry else ""

        updates: dict[str, Any] = {}
        for prop, var_def in schema.items():
            if prop == "online":
                value = overrides.get("online", True)
            elif prop == "label":
                value = overrides.get("label", project_label)
            elif prop in overrides:
                value = overrides[prop]
            else:
                value = self._default_for_var_def(var_def)
            updates[self._child_state_key(child_type, local_id, prop)] = value

        self.state.set_batch(updates, source=f"device.{self.device_id}")

    def deregister_child(self, child_type: str, local_id: int) -> None:
        """Remove a child entity and delete all of its state keys.

        Silent no-op if the child isn't registered (so drivers can call this
        eagerly during reconciliation without first checking).
        """
        bucket = self._children.get(child_type)
        if bucket is None or local_id not in bucket:
            return

        prefix_dot = self._child_state_prefix(child_type, local_id) + "."
        # Snapshot before mutating; iterating the live store while deleting
        # would be unsafe. The snapshot is cheap (dict copy) at the scale
        # we expect per child (~10-30 keys).
        keys_to_delete = [
            k for k in self.state.snapshot().keys() if k.startswith(prefix_dot)
        ]
        for k in keys_to_delete:
            self.state.delete(k, source=f"device.{self.device_id}")

        del bucket[local_id]
        if not bucket:
            del self._children[child_type]

    def list_children(self, child_type: str) -> list[int]:
        """Local IDs of currently-registered children of ``child_type``,
        in insertion order. Returns an empty list if none are registered or
        the type is unknown to the platform tracker.
        """
        bucket = self._children.get(child_type)
        if bucket is None:
            return []
        return list(bucket.keys())

    def set_child_state(
        self, child_type: str, local_id: int, prop: str, value: Any
    ) -> None:
        """Set one state key on a child entity, validated against its
        declared schema.

        Raises ValueError if ``prop`` is not in the child type's
        ``state_variables`` (the synthetic ``online`` key is always allowed).
        """
        self._validate_child_prop(child_type, prop)
        schema = self._effective_child_schema(child_type)
        self._warn_on_type_mismatch(
            f"{child_type}.{self._format_child_id(child_type, local_id)}.{prop}",
            value,
            schema[prop],
        )
        self.state.set(
            self._child_state_key(child_type, local_id, prop),
            value,
            source=f"device.{self.device_id}",
        )

    def set_child_state_batch(
        self, child_type: str, local_id: int, updates: dict[str, Any]
    ) -> None:
        """Atomically set several state keys on one child entity.

        Validates every prop in ``updates`` before any write, so a single
        bad prop causes the entire batch to abort.
        """
        for prop in updates:
            self._validate_child_prop(child_type, prop)
        namespaced = {
            self._child_state_key(child_type, local_id, prop): v
            for prop, v in updates.items()
        }
        self.state.set_batch(namespaced, source=f"device.{self.device_id}")

    def set_children_state_batch(
        self, updates: list[tuple[str, int, dict[str, Any]]]
    ) -> None:
        """Atomically set state keys across many children in one transaction.

        Each entry is ``(child_type, local_id, {prop: value, ...})``. Listeners
        and the cloud relay see the complete delta, not a half-applied state.
        Use this for poll responses that touch dozens or hundreds of children
        at once.
        """
        for child_type, _local_id, child_updates in updates:
            for prop in child_updates:
                self._validate_child_prop(child_type, prop)
        namespaced: dict[str, Any] = {}
        for child_type, local_id, child_updates in updates:
            for prop, value in child_updates.items():
                namespaced[self._child_state_key(child_type, local_id, prop)] = value
        if namespaced:
            self.state.set_batch(namespaced, source=f"device.{self.device_id}")

    def get_child_entity_types(self) -> dict[str, dict[str, Any]]:
        """Return the driver's declared child entity types as
        ``{type_name: definition}``, with each definition's
        ``state_variables`` replaced by the *effective* schema (the
        platform-managed ``online`` and ``label`` keys injected if the
        driver didn't already declare them).

        Returns ``{}`` if the driver doesn't declare ``child_entity_types``.
        Used by the REST API and IDE to expose the per-type schema to
        clients without leaking driver-private helpers.
        """
        raw = self.DRIVER_INFO.get("child_entity_types", {})
        result: dict[str, dict[str, Any]] = {}
        for ctype, definition in raw.items():
            merged_def = dict(definition)
            merged_def["state_variables"] = self._effective_child_schema(ctype)
            result[ctype] = merged_def
        return result

    def format_child_id(self, child_type: str, local_id: int) -> str:
        """Validate ``local_id`` against the declared id_format and return
        its padded string form. Public wrapper around the internal helper.
        """
        return self._format_child_id(child_type, local_id)

    def is_child_registered(self, child_type: str, local_id: int) -> bool:
        """True if ``register_child(child_type, local_id)`` has been called
        and ``deregister_child`` hasn't been called since.
        """
        return local_id in self._children.get(child_type, {})

    def get_child_state(
        self, child_type: str, local_id: int,
    ) -> dict[str, Any]:
        """Return the live state for one registered child as
        ``{property: value}``. Returns an empty dict if the child isn't
        currently registered.
        """
        if not self.is_child_registered(child_type, local_id):
            return {}
        return self.state.get_namespace(
            self._child_state_prefix(child_type, local_id)
        )

    async def refresh_children(self) -> Any:
        """Re-discover child entities from the device.

        Default implementation raises ``NotImplementedError``. Drivers that
        can re-poll their controller's child list override this to reconcile
        ``register_child`` / ``deregister_child`` calls against the latest
        device state. Called from the REST API
        ``POST /api/devices/{id}/children/refresh`` endpoint.
        """
        raise NotImplementedError(
            f"Driver {self.DRIVER_INFO.get('id', '?')} does not implement "
            f"refresh_children"
        )

    async def poll_children(
        self,
        child_type: str,
        fetch: Callable[[list[int]], Awaitable[dict[int, dict[str, Any]]]],
        batch_size: int = 50,
        inter_batch_delay: float = 0.1,
    ) -> None:
        """Paginated polling helper.

        Splits the currently-registered ``child_type`` IDs into batches of
        ``batch_size``, awaits ``fetch(batch_ids)`` for each (pausing
        ``inter_batch_delay`` seconds between batches so polls don't saturate
        slow controllers), then applies the whole poll's results in ONE atomic
        ``set_children_state_batch``. Applying per-poll rather than per-batch
        means a subscriber never observes a half-applied multi-batch snapshot
        (some children on this poll's values, the rest still on the last
        poll's). The coalescing delay is bounded by the fetch time and is
        negligible against typical poll intervals.

        ``fetch`` returns ``{local_id: {prop: value, ...}}``. A result for a
        child that isn't registered, or that was deregistered (or
        deregistered+re-registered, which resets its state) since the poll
        started, is dropped — so a concurrent ``refresh_children`` can't have
        its reset clobbered by a stale write (ABA guard).
        """
        ids = self.list_children(child_type)
        if not ids:
            return
        # Snapshot each child's registration epoch at poll start. A child whose
        # live epoch later differs (re-registered) or is gone (deregistered) is
        # dropped at apply time.
        start_bucket = self._children.get(child_type, {})
        epochs = {lid: start_bucket.get(lid) for lid in ids}

        collected: list[tuple[int, dict[str, Any]]] = []
        last_start = (len(ids) - 1) // max(batch_size, 1) * max(batch_size, 1)
        for i in range(0, len(ids), batch_size):
            batch_ids = ids[i:i + batch_size]
            results = await fetch(batch_ids)
            collected.extend(results.items())
            if i != last_start and inter_batch_delay > 0:
                await asyncio.sleep(inter_batch_delay)

        live = self._children.get(child_type, {})
        updates: list[tuple[str, int, dict[str, Any]]] = []
        for lid, props in collected:
            expected = epochs.get(lid)
            # Keep only children that were in the start snapshot AND whose
            # registration epoch hasn't changed since (drops ghosts, removed,
            # and re-registered children).
            if expected is not None and live.get(lid) == expected:
                updates.append((child_type, lid, props))
        if updates:
            self.set_children_state_batch(updates)
