"""
OpenAVC Cloud — Cloud agent main orchestrator.

The CloudAgent is the entry point for cloud connectivity. It manages
the WebSocket connection, handshake, session, and message pipeline.
All other cloud submodules (heartbeat, state_relay, command_handler)
are wired through the agent.
"""

from __future__ import annotations

import asyncio
import json
import time as _time
from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING

import websockets
from websockets.exceptions import ConnectionClosed, InvalidURI, InvalidHandshake

from server.cloud.handshake import Handshake, HandshakeError
from server.cloud.protocol import (
    PING, ACK, SESSION_ROTATE, SESSION_INVALID,
    CONFIG_UPDATE, CAPABILITIES_UPDATE, THROTTLE, ERROR,
    COMMAND, CONFIG_PUSH, RESTART, DIAGNOSTIC,
    SOFTWARE_UPDATE, TUNNEL_OPEN, TUNNEL_CLOSE, ALERT_RULES_UPDATE,
    AI_TOOL_CALL, GAP_REPORT, GET_PROJECT, GET_DEVICE_COMMANDS,
    build_pong, build_signed_message,
    parse_message, is_handshake_message,
    extract_payload, ProtocolError,
    _now_iso,
)
from server.cloud.session import Session, SessionInvalid
from server.cloud.sequencer import Sequencer
from server.utils.logger import get_logger
from server.version import __version__

if TYPE_CHECKING:
    from server.core.state_store import StateStore
    from server.core.event_bus import EventBus
    from server.core.device_manager import DeviceManager

log = get_logger(__name__)

# Reconnection backoff parameters
BACKOFF_INITIAL = 5
BACKOFF_MULTIPLIER = 2
BACKOFF_MAX = 300  # 5 minutes

# Default capabilities the agent reports
DEFAULT_CAPABILITIES = [
    "monitoring",
    "remote_access",
    "fleet_update",
    "diagnostics",
    "tunnel",
]


class CloudAgent:
    """
    Main cloud agent — manages the connection to the cloud platform.

    Lifecycle:
    1. connect() — establish WSS, perform handshake, enter steady state
    2. Steady state: heartbeat loop, message processing, state relay
    3. On disconnect: reconnect with exponential backoff
    4. stop() — graceful shutdown
    """

    def __init__(
        self,
        state: StateStore,
        events: EventBus,
        devices: DeviceManager,
        cloud_config: dict[str, Any],
    ):
        self.state = state
        self.events = events
        self.devices = devices

        # Cloud configuration
        self._endpoint: str = cloud_config.get("endpoint", "")
        self._system_key: bytes = self._load_system_key(cloud_config.get("system_key", ""))
        self._system_id: str = cloud_config.get("system_id", "")

        # Server-driven config (defaults, overridden by session_start)
        self._config: dict[str, Any] = {
            "heartbeat_interval": cloud_config.get("heartbeat_interval", 30),
            "state_batch_interval": cloud_config.get("state_batch_interval", 2),
            "state_batch_max_size": cloud_config.get("state_batch_max_size", 500),
            "log_level_filter": cloud_config.get("log_level_filter", "warning"),
            "max_logs_per_minute": 100,
            "max_alerts_per_minute": 10,
            "max_messages_per_minute": 300,
            "buffer_size": cloud_config.get("buffer_size", 1000),
            "ack_interval": 10,
            "compression": "none",
            "features": {
                "alerts_enabled": True,
                "log_forwarding": True,
                "state_forwarding": True,
            },
        }

        # Connection state
        self._ws: Any = None  # websockets.WebSocketClientProtocol
        self._session: Session | None = None
        self._sequencer = Sequencer(self._config["buffer_size"])
        self._enabled_capabilities: list[str] = []

        # Subsystems (set after engine wiring)
        self._heartbeat_collector: Any = None  # HeartbeatCollector
        self._state_relay: Any = None  # StateRelay
        self._command_handler: Any = None  # CommandHandler
        self._ai_tool_handler: Any = None  # AIToolHandler
        self._alert_monitor: Any = None  # AlertMonitor
        self._tunnel_handler: Any = None  # TunnelHandler

        # Tasks
        self._recv_task: asyncio.Task | None = None
        self._heartbeat_task: asyncio.Task | None = None
        self._running = False
        self._stopping = False
        self._connected = False

        # Reconnection
        self._backoff = BACKOFF_INITIAL
        self._disconnect_time: str | None = None
        self._reconnect_count = 0

        # Tracking
        self._connected_at: float = 0  # time.time() when connected
        self._last_heartbeat_at: float = 0  # time.time() of last heartbeat sent

        # Throttle tracking: msg_type -> asyncio.Event (cleared when throttled)
        self._throttles: dict[str, asyncio.Event] = {}

        # Application version
        self._version = __version__

    @staticmethod
    def _load_system_key(key_input: str | bytes) -> bytes:
        """Load system key from config (hex string or raw bytes)."""
        if isinstance(key_input, bytes):
            return key_input
        if not key_input:
            return b""
        try:
            return bytes.fromhex(key_input)
        except ValueError:
            return key_input.encode("utf-8")

    # --- Lifecycle ---

    async def connect(self) -> None:
        """
        Start the cloud agent connection loop.

        Connects to the cloud endpoint, performs the handshake, and enters
        steady state. Automatically reconnects on failure.
        """
        if not self._endpoint or not self._system_key:
            log.warning("Cloud agent: no endpoint or system key configured, not starting")
            return

        self._running = True
        self._stopping = False
        log.info(f"Cloud agent: connecting to {self._endpoint}")

        # Start the connection loop as a background task
        asyncio.create_task(self._connection_loop())

    async def stop(self) -> None:
        """Gracefully stop the cloud agent."""
        log.info("Cloud agent: stopping")
        self._stopping = True
        self._running = False

        # Cancel tasks
        for task in [self._recv_task, self._heartbeat_task]:
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        # Stop subsystems
        if self._tunnel_handler:
            await self._tunnel_handler.stop()
        if self._state_relay:
            await self._state_relay.stop()
        if self._alert_monitor:
            await self._alert_monitor.stop()

        # Close WebSocket
        if self._ws:
            try:
                await self._ws.close()
            except (ConnectionClosed, OSError):
                pass  # Best-effort close during shutdown
            self._ws = None

        if self._session:
            self._session.invalidate()
            self._session = None

        self._connected = False
        log.info("Cloud agent: stopped")

    # --- Connection Loop ---

    async def _connection_loop(self) -> None:
        """Main connection loop with exponential backoff reconnection."""
        while self._running and not self._stopping:
            try:
                await self._connect_and_run()
            except HandshakeError as e:
                log.error(f"Cloud agent: handshake failed — {e} (reason: {e.reason})")
                if e.reason in ("invalid_key", "system_revoked", "system_not_found"):
                    # Fatal — don't retry, key is permanently invalid
                    log.error("Cloud agent: system key is invalid or revoked. Stopping.")
                    self._running = False
                    self.state.set("system.cloud.status", "auth_failed", source="cloud")
                    return
            except (ConnectionClosed, OSError, InvalidURI, InvalidHandshake) as e:
                log.warning(f"Cloud agent: connection error — {e}")
            except SessionInvalid as e:
                log.warning(f"Cloud agent: session invalidated — {e}")
            except asyncio.CancelledError:
                return
            except Exception:
                # Catch-all: prevents unknown errors from killing the reconnect loop
                log.exception("Cloud agent: unexpected error in connection loop")

            if not self._running or self._stopping:
                return

            # Record disconnect time
            if self._connected:
                self._disconnect_time = _now_iso()
                self._connected = False
                self.state.set("system.cloud.status", "disconnected", source="cloud")

            # Backoff
            log.info(f"Cloud agent: reconnecting in {self._backoff}s")
            await asyncio.sleep(self._backoff)
            self._backoff = min(self._backoff * BACKOFF_MULTIPLIER, BACKOFF_MAX)
            self._reconnect_count += 1

    async def _connect_and_run(self) -> None:
        """Single connection attempt: connect, handshake, run."""
        self.state.set("system.cloud.status", "connecting", source="cloud")

        # Connect WebSocket
        self._ws = await websockets.connect(
            self._endpoint,
            ping_interval=None,  # We handle our own ping/pong
            ping_timeout=None,
            close_timeout=10,
            max_size=2**20,  # 1MB max message
        )

        try:
            # Perform handshake
            hostname = self.state.get("system.hostname", "openavc")
            project_name = self.state.get("system.project_name", "Unknown")

            handshake = Handshake(
                system_id=self._system_id,
                system_key=self._system_key,
                version=self._version,
                hostname=str(hostname),
                project_name=str(project_name),
                capabilities=DEFAULT_CAPABILITIES,
            )

            result = await handshake.perform(
                send=self._send_raw,
                recv=self._recv_raw,
            )

            # Create session
            self._session = Session(
                session_id=result.session_id,
                session_token=result.session_token,
                signing_key=result.signing_key,
                session_expires=result.session_expires,
                system_key=self._system_key,
            )

            # Apply server config
            self._apply_config(result.config)
            self._enabled_capabilities = result.enabled_capabilities

            # Handle upgrade_required from cloud
            if result.upgrade_required:
                min_ver = result.upgrade_required.get("min_version", "")
                self.state.set("system.cloud_upgrade_required", True, source="cloud")
                self.state.set("system.cloud_min_version", min_ver, source="cloud")
                log.warning(
                    "Cloud requires core v%s or later: %s",
                    min_ver, result.upgrade_required.get("message", ""),
                )
            else:
                self.state.set("system.cloud_upgrade_required", False, source="cloud")
                self.state.set("system.cloud_min_version", "", source="cloud")

            # Apply cloud update policy to UpdateManager
            update_policy = result.config.get("update_policy")
            if update_policy and self._command_handler and hasattr(self._command_handler, '_update_manager'):
                mgr = self._command_handler._update_manager
                if mgr:
                    mgr.apply_update_policy(update_policy)

            # Reset sequencer for new session
            self._sequencer.reset_for_new_session()

            # Handle reconnection resume
            is_reconnect = self._disconnect_time is not None
            if is_reconnect and self._sequencer.buffer_count > 0:
                replay_from = await handshake.send_resume(
                    send=self._send_raw,
                    recv=self._recv_raw,
                    last_ack_seq=self._sequencer.last_ack_seq,
                    buffered_count=self._sequencer.buffer_count,
                    disconnected_at=self._disconnect_time or "",
                )
                await self._replay_buffered(replay_from)

            # Connected!
            self._connected = True
            self._connected_at = _time.time()
            self._backoff = BACKOFF_INITIAL
            self._disconnect_time = None
            self.state.set("system.cloud.status", "connected", source="cloud")
            self.state.set("system.cloud.session_id", result.session_id, source="cloud")
            log.info("Cloud agent: connected and authenticated")

            # Start steady-state tasks
            self._recv_task = asyncio.create_task(self._receive_loop())
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

            # Start state relay if enabled
            if self._state_relay and self._config["features"].get("state_forwarding"):
                await self._state_relay.start()

            # Start alert monitor if enabled
            if self._alert_monitor and self._config["features"].get("alerts_enabled"):
                await self._alert_monitor.start()

            # Wait for tasks to complete (they run until disconnection)
            await asyncio.gather(self._recv_task, self._heartbeat_task)

        finally:
            # Clean up on disconnect — stop subsystems so they re-initialize
            # (and re-send initial state snapshot) on the next connection.
            if self._state_relay:
                await self._state_relay.stop()
            if self._alert_monitor:
                await self._alert_monitor.stop()
            if self._ws:
                try:
                    await self._ws.close()
                except (ConnectionClosed, OSError):
                    pass  # Best-effort close during disconnect cleanup
                self._ws = None

    # --- Message Pipeline ---

    async def send_message(self, msg_type: str, payload: dict[str, Any]) -> None:
        """
        Send a signed, sequenced message to the cloud.

        This is the primary send interface for subsystems (heartbeat,
        state_relay, command_handler).

        Args:
            msg_type: Message type constant.
            payload: Message payload dict.
        """
        if not self._connected or not self._session or not self._ws:
            return

        # Check throttle
        throttle_event = self._throttles.get(msg_type)
        if throttle_event and not throttle_event.is_set():
            log.debug(f"Cloud agent: message type {msg_type} is throttled, buffering")
            return

        # Build message
        msg = {
            "type": msg_type,
            "ts": _now_iso(),
            "payload": payload,
        }

        # Assign sequence number and buffer
        self._sequencer.assign_seq(msg)

        # Sign and serialize immediately (before any await) to prevent
        # shared payload data from being mutated by concurrent tasks
        # between signing and sending.
        self._session.sign_outgoing(msg)
        raw = json.dumps(msg)

        # Send pre-serialized string
        await self._send_raw_str(raw)

    async def _send_raw(self, msg: dict[str, Any]) -> None:
        """Send a raw message dict over the WebSocket."""
        await self._send_raw_str(json.dumps(msg))

    async def _send_raw_str(self, raw: str) -> None:
        """Send a pre-serialized JSON string over the WebSocket."""
        ws = self._ws  # Capture local reference to avoid race
        if not ws:
            return
        try:
            await ws.send(raw)
        except ConnectionClosed:
            raise
        except OSError:
            log.exception("Cloud agent: error sending message")

    async def _recv_raw(self) -> str:
        """Receive a raw message string from the WebSocket."""
        if not self._ws:
            raise ConnectionClosed(None, None)
        return await self._ws.recv()

    async def _receive_loop(self) -> None:
        """Main receive loop — process incoming messages."""
        try:
            while self._running and self._ws:
                raw = await self._recv_raw()
                try:
                    msg = parse_message(raw)
                    await self._handle_message(msg)
                except ProtocolError as e:
                    log.warning(f"Cloud agent: protocol error — {e}")
                except SessionInvalid:
                    raise
                except Exception:
                    # Catch-all: isolates per-message handling errors from the receive loop
                    log.exception("Cloud agent: error handling message")
        except ConnectionClosed:
            log.info("Cloud agent: connection closed")
        except asyncio.CancelledError:
            return

    async def _handle_message(self, msg: dict[str, Any]) -> None:
        """Dispatch an incoming message to the appropriate handler."""
        msg_type = msg.get("type", "")

        # Handshake messages shouldn't arrive during steady state
        if is_handshake_message(msg):
            log.warning(f"Cloud agent: unexpected handshake message '{msg_type}' during steady state")
            return

        # Verify signature on steady-state messages
        if self._session and not self._session.verify_incoming(msg):
            log.warning(f"Cloud agent: invalid signature on '{msg_type}' message, rejecting")
            return

        # Validate downstream sequence
        seq = msg.get("seq")
        if seq is not None and not self._sequencer.validate_downstream_seq(seq):
            return  # Duplicate or out-of-order

        # Report any detected sequence gaps to the cloud
        if seq is not None:
            gap = self._sequencer.pop_gap()
            if gap:
                gap_start, gap_end = gap
                log.info(f"Cloud agent: reporting gap — missing seqs {gap_start}–{gap_end}")
                gap_msg = build_signed_message(
                    GAP_REPORT,
                    {"missing_from": gap_start, "missing_to": gap_end},
                    seq=self._sequencer.next_seq,
                    session_token=self._session.session_token if self._session else "",
                    signing_key=self._session.signing_key if self._session else b"",
                )
                self._sequencer.assign_seq(gap_msg)
                await self._send_raw(gap_msg)

        # Check for session rotation trigger
        if self._session and seq is not None:
            self._session.check_rotation(seq)

        # Dispatch by type
        if msg_type == PING:
            await self._handle_ping(msg)
        elif msg_type == ACK:
            self._sequencer.handle_ack(msg)
        elif msg_type == SESSION_ROTATE:
            if self._session:
                self._session.handle_session_rotate(msg)
        elif msg_type == SESSION_INVALID:
            if self._session:
                self._session.handle_session_invalid(msg)  # Raises SessionInvalid
        elif msg_type == CONFIG_UPDATE:
            self._handle_config_update(msg)
        elif msg_type == CAPABILITIES_UPDATE:
            self._handle_capabilities_update(msg)
        elif msg_type == THROTTLE:
            self._handle_throttle(msg)
        elif msg_type == ERROR:
            self._handle_error(msg)
        elif msg_type in (COMMAND, CONFIG_PUSH, RESTART, DIAGNOSTIC, GET_PROJECT, GET_DEVICE_COMMANDS):
            if self._command_handler:
                await self._command_handler.handle(msg)
        elif msg_type == AI_TOOL_CALL:
            if self._ai_tool_handler:
                await self._ai_tool_handler.handle(msg)
            else:
                log.info("Cloud agent: received ai_tool_call but no AI tool handler")
        elif msg_type == ALERT_RULES_UPDATE:
            await self._handle_alert_rules_update(msg)
        elif msg_type == TUNNEL_OPEN:
            if self._tunnel_handler:
                await self._tunnel_handler.handle_tunnel_open(msg)
            else:
                log.info("Cloud agent: received tunnel_open but no tunnel handler")
        elif msg_type == TUNNEL_CLOSE:
            if self._tunnel_handler:
                await self._tunnel_handler.handle_tunnel_close(msg)
            else:
                log.info("Cloud agent: received tunnel_close but no tunnel handler")
        elif msg_type == SOFTWARE_UPDATE:
            if self._command_handler:
                await self._command_handler.handle(msg)
            else:
                log.info(f"Cloud agent: received {msg_type} but no command handler")
        else:
            log.warning(f"Cloud agent: unknown message type '{msg_type}'")

    # --- Message Handlers ---

    async def _handle_ping(self, msg: dict[str, Any]) -> None:
        """Respond to a ping with a signed pong."""
        payload = extract_payload(msg)
        nonce = payload.get("nonce", "")
        if self._session:
            pong = build_pong(
                seq=self._sequencer.next_seq,
                session_token=self._session.session_token,
                signing_key=self._session.signing_key,
                nonce=nonce,
            )
            self._sequencer.assign_seq(pong)
            # Pong is already signed by build_pong, but we need the correct seq
            # Re-sign with the assigned seq
            pong.pop("sig", None)
            self._session.sign_outgoing(pong)
            await self._send_raw(pong)

    def _handle_config_update(self, msg: dict[str, Any]) -> None:
        """Apply a config update from the server."""
        payload = extract_payload(msg)
        self._apply_config(payload)
        log.info(f"Cloud agent: config updated — {list(payload.keys())}")

    def _handle_capabilities_update(self, msg: dict[str, Any]) -> None:
        """Update enabled capabilities."""
        payload = extract_payload(msg)
        new_caps = payload.get("enabled_capabilities", [])
        old_caps = set(self._enabled_capabilities)
        self._enabled_capabilities = new_caps

        added = set(new_caps) - old_caps
        removed = old_caps - set(new_caps)
        if added:
            log.info(f"Cloud agent: capabilities added — {added}")
        if removed:
            log.info(f"Cloud agent: capabilities removed — {removed}")

    def _handle_throttle(self, msg: dict[str, Any]) -> None:
        """Handle a throttle directive from the server."""
        payload = extract_payload(msg)
        limit_type = payload.get("limit", "")
        retry_after = payload.get("retry_after_seconds", 30)
        log.warning(
            f"Cloud agent: throttled on '{limit_type}' — "
            f"backing off for {retry_after}s"
        )

        # Create or get the throttle event for this message type
        if limit_type not in self._throttles:
            self._throttles[limit_type] = asyncio.Event()
        event = self._throttles[limit_type]
        event.clear()

        # Schedule unthrottle
        asyncio.create_task(self._unthrottle(limit_type, retry_after))

    async def _unthrottle(self, limit_type: str, delay: float) -> None:
        """Release a throttle after the specified delay, then clean up."""
        await asyncio.sleep(delay)
        event = self._throttles.pop(limit_type, None)
        if event:
            event.set()
            log.info(f"Cloud agent: throttle on '{limit_type}' released")

    def _handle_error(self, msg: dict[str, Any]) -> None:
        """Log a server error response."""
        payload = extract_payload(msg)
        code = payload.get("code", "unknown")
        ref_type = payload.get("ref_type", "")
        message = payload.get("message", "")
        log.warning(f"Cloud agent: server error — {code}: {message} (ref: {ref_type})")

    async def _handle_alert_rules_update(self, msg: dict[str, Any]) -> None:
        """Forward alert rules to the alert monitor (future)."""
        payload = extract_payload(msg)
        rules = payload.get("rules", [])
        log.info(f"Cloud agent: received {len(rules)} alert rule(s)")
        await self.events.emit("cloud.alert_rules_update", {"rules": rules})

    # --- Config ---

    def _apply_config(self, config: dict[str, Any]) -> None:
        """Merge server config into local config."""
        for key, value in config.items():
            if key == "features" and isinstance(value, dict):
                self._config.setdefault("features", {}).update(value)
            else:
                self._config[key] = value

    # --- Heartbeat ---

    async def _heartbeat_loop(self) -> None:
        """Send periodic heartbeats at the server-configured interval."""
        try:
            while self._running and self._connected:
                interval = self._config.get("heartbeat_interval", 30)
                await asyncio.sleep(interval)

                if not self._connected or not self._session:
                    break

                if self._heartbeat_collector:
                    metrics = await self._heartbeat_collector.collect()
                    # Store metrics in StateStore for AlertMonitor evaluation
                    for metric_key in ("cpu_percent", "memory_percent", "disk_percent"):
                        if metric_key in metrics:
                            self.state.set(
                                f"system.{metric_key}", metrics[metric_key],
                                source="heartbeat",
                            )
                    await self.send_message("heartbeat", metrics)
                    self._last_heartbeat_at = _time.time()
                else:
                    # Minimal heartbeat without collector — get device count from state
                    device_count = self.state.get("system.device_count", 0) if self.state else 0
                    await self.send_message("heartbeat", {
                        "uptime_seconds": 0,
                        "cpu_percent": 0,
                        "memory_percent": 0,
                        "disk_percent": 0,
                        "device_count": device_count,
                        "devices_connected": 0,
                        "devices_error": 0,
                        "active_ws_clients": 0,
                    })
                    self._last_heartbeat_at = _time.time()
        except asyncio.CancelledError:
            return

    # --- Replay ---

    async def _replay_buffered(self, replay_from_seq: int) -> None:
        """Replay buffered messages after reconnection."""
        messages = self._sequencer.get_replay_messages(replay_from_seq)
        if not messages:
            log.info("Cloud agent: no messages to replay")
            return

        log.info(f"Cloud agent: replaying {len(messages)} buffered message(s)")
        for msg in messages:
            # Re-assign sequence numbers for the new session
            msg.pop("seq", None)
            msg.pop("sig", None)
            msg.pop("session", None)
            self._sequencer.assign_seq(msg)
            if self._session:
                self._session.sign_outgoing(msg)
            await self._send_raw(msg)

        # Clear the buffer after successful replay
        self._sequencer.clear_buffer()

    # --- Subsystem Wiring ---

    def set_heartbeat_collector(self, collector: Any) -> None:
        """Wire the heartbeat collector subsystem."""
        self._heartbeat_collector = collector

    def set_state_relay(self, relay: Any) -> None:
        """Wire the state relay subsystem."""
        self._state_relay = relay

    def set_command_handler(self, handler: Any) -> None:
        """Wire the command handler subsystem."""
        self._command_handler = handler

    def set_ai_tool_handler(self, handler: Any) -> None:
        """Wire the AI tool handler subsystem."""
        self._ai_tool_handler = handler

    def set_alert_monitor(self, monitor: Any) -> None:
        """Wire the alert monitor subsystem."""
        self._alert_monitor = monitor

    def set_tunnel_handler(self, handler: Any) -> None:
        """Wire the tunnel handler subsystem."""
        self._tunnel_handler = handler

    # --- Status ---

    def get_status(self) -> dict[str, Any]:
        """Return cloud agent status info."""
        uptime = 0
        if self._connected and self._connected_at > 0:
            uptime = int(_time.time() - self._connected_at)
        last_hb = ""
        if self._last_heartbeat_at > 0:
            last_hb = datetime.fromtimestamp(
                self._last_heartbeat_at, tz=timezone.utc
            ).isoformat()
        return {
            "connected": self._connected,
            "endpoint": self._endpoint,
            "system_id": self._system_id,
            "session_id": self._session.session_id if self._session else None,
            "enabled_capabilities": self._enabled_capabilities,
            "buffer_count": self._sequencer.buffer_count,
            "reconnect_count": self._reconnect_count,
            "last_heartbeat": last_hb,
            "uptime": uptime,
            "config": {
                "heartbeat_interval": self._config.get("heartbeat_interval"),
                "state_batch_interval": self._config.get("state_batch_interval"),
            },
        }
