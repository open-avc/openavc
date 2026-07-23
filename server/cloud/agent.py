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
from urllib.parse import urlparse

import websockets
from websockets.exceptions import ConnectionClosed, InvalidURI, InvalidHandshake

from server.cloud.handshake import Handshake, HandshakeError
from server.cloud.protocol import (
    PING, ACK, SESSION_ROTATE, SESSION_INVALID,
    CONFIG_UPDATE, CAPABILITIES_UPDATE, THROTTLE, ERROR,
    COMMAND, CONFIG_PUSH, RESTART, DIAGNOSTIC,
    SOFTWARE_UPDATE, TUNNEL_OPEN, TUNNEL_CLOSE, ALERT_RULES_UPDATE,
    AI_TOOL_CALL, GAP_REPORT, GET_PROJECT, GET_DEVICE_COMMANDS,
    CERT_RESULT, CERT_RENEW_DUE,
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

# Upper bound on a cloud-supplied throttle retry_after (defense in depth: the
# value crosses the trust boundary, so a hostile/buggy cloud can't pin a
# message type off forever). 1 hour is far beyond any legitimate backpressure.
THROTTLE_MAX_SECONDS = 3600

# How many consecutive downstream signature failures the agent tolerates before
# tearing down the session and reconnecting. A persistent failure is a key
# desync or tamper signal; silently dropping every message forever (no commands,
# no session rotation) is worse than forcing a fresh handshake.
MAX_CONSECUTIVE_SIG_FAILURES = 5

# Default capabilities the agent reports
DEFAULT_CAPABILITIES = [
    "monitoring",
    "remote_access",
    "fleet_update",
    "diagnostics",
    "tunnel",
    "trusted_certs",
]

# Downstream messages that require a specific capability before the agent will
# dispatch them. Per spec §13.8, the agent is a defense-in-depth gate: if the
# cloud sends a tunnel_open while "tunnel" isn't in the negotiated
# enabled_capabilities, the agent ignores the message instead of acting on it.
# Keep this map in sync with the canonical capability vocabulary in
# `DEFAULT_CAPABILITIES` above and in `openavc-cloud/api/ws/handler.py`.
_CAPABILITY_GATED: dict[str, str] = {
    "tunnel_open": "tunnel",
    "tunnel_close": "tunnel",
    "diagnostic": "diagnostics",
    # `software_update` is the fleet-update message type. Gating it here
    # means an agent whose `fleet_update` capability was revoked by the
    # cloud (via `features_disabled` in `upgrade_required`) silently
    # ignores stray pushes instead of trying to apply an update it's not
    # supposed to. See A59 in the pre-release audit.
    "software_update": "fleet_update",
    # The highest-risk remote-control messages: they drive AV hardware,
    # full_replace the project, and restart the service. The spec model is
    # that the agent enforces capabilities as defense-in-depth, so a plan that
    # only grants `monitoring` (or an account whose `remote_access` was revoked
    # mid-session) cannot execute them even if a cloud-side authorization bug
    # sends them. Reads (`get_project`/`get_device_commands`) and AI tool calls
    # stay ungated — they have their own gating and are not state-mutating.
    "command": "remote_access",
    "config_push": "remote_access",
    "restart": "remote_access",
    # Renewal nudges are cloud-initiated; if the plan/session doesn't include
    # trusted certs, don't act on them. `cert_result` stays ungated — it only
    # answers a request this agent chose to send, and without the in-memory
    # pending key a pushed chain can't install anything anyway.
    "cert_renew_due": "trusted_certs",
}


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
        self._cert_manager: Any = None  # CertificateManager

        # Tasks
        self._recv_task: asyncio.Task | None = None
        self._heartbeat_task: asyncio.Task | None = None
        self._connection_loop_task: asyncio.Task | None = None
        self._running = False
        self._stopping = False
        self._connected = False

        # Consecutive downstream signature-verification failures (reset on any
        # successful verify). A persistent run forces a session teardown.
        self._sig_failure_count = 0

        # Reconnection
        self._backoff = BACKOFF_INITIAL
        self._disconnect_time: str | None = None
        self._reconnect_count = 0

        # Tracking
        self._connected_at: float = 0  # time.time() when connected
        self._last_heartbeat_at: float = 0  # time.time() of last heartbeat sent

        # Throttle tracking: msg_type -> asyncio.Event (cleared when throttled).
        # The release tasks and their deadlines are tracked so shutdown/reconnect
        # can cancel them (no GC of pending tasks, no stale task from a prior
        # connection releasing a throttle the cloud set on the new one) and so
        # overlapping throttles release on the LATEST deadline, not the first.
        self._throttles: dict[str, asyncio.Event] = {}
        self._throttle_tasks: dict[str, asyncio.Task] = {}
        self._throttle_deadlines: dict[str, float] = {}

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

    @staticmethod
    def _endpoint_is_encrypted(endpoint: str) -> bool:
        """True if the agent may send confidential payloads over this endpoint.

        Requires ``wss://`` (TLS). Plain ``ws://`` is permitted only for
        loopback hosts so local development against a dev cloud still works;
        any other cleartext endpoint is rejected so the system-key-derived auth
        proof and signed traffic never cross an unencrypted link.
        """
        try:
            parsed = urlparse(endpoint)
        except (ValueError, TypeError):
            return False
        if parsed.scheme == "wss":
            return True
        if parsed.scheme == "ws":
            host = (parsed.hostname or "").lower()
            return host in ("localhost", "127.0.0.1", "::1")
        return False

    @staticmethod
    def _normalize_capabilities(value: Any) -> list[str] | None:
        """Validate a capabilities payload into a clean ``list[str]``.

        Capabilities cross the cloud trust boundary at both the handshake and
        the mid-session ``capabilities_update``. A malformed payload (not a list,
        or non-string entries) must not be stored unvalidated — the gate consults
        this list to decide whether to dispatch high-risk messages. Returns the
        filtered string list, or ``None`` if the payload isn't a list at all.
        """
        if not isinstance(value, list):
            return None
        return [c for c in value if isinstance(c, str)]

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

        # Fail closed on a cleartext endpoint: never start the loop (and so never
        # send the auth proof) over an unencrypted transport.
        if not self._endpoint_is_encrypted(self._endpoint):
            log.error(
                "Cloud agent: refusing to connect to non-encrypted endpoint %r — "
                "wss:// is required (ws:// is allowed only for loopback dev).",
                self._endpoint,
            )
            self.state.set("system.cloud.status", "insecure_endpoint", source="cloud")
            return

        self._running = True
        self._stopping = False
        log.info(f"Cloud agent: connecting to {self._endpoint}")

        # Start the connection loop as a background task. Keep the handle so
        # stop() can cancel a loop that's mid-reconnect or sleeping in backoff,
        # otherwise the detached task can resurrect the connection/subsystems
        # after a graceful shutdown.
        self._connection_loop_task = asyncio.create_task(self._connection_loop())

    async def stop(self) -> None:
        """Gracefully stop the cloud agent."""
        log.info("Cloud agent: stopping")
        self._stopping = True
        self._running = False

        # Cancel the connection loop first. If it's mid-reconnect this lands a
        # CancelledError on its current await (running _connect_and_run's finally,
        # which closes the WS and stops the relay/alert subsystems), and if it's
        # asleep in backoff this wakes it immediately instead of blocking
        # shutdown for up to BACKOFF_MAX seconds. Without this the detached loop
        # can re-establish the connection and restart subsystems we tear down
        # below.
        loop_task = self._connection_loop_task
        self._connection_loop_task = None
        if loop_task and not loop_task.done():
            loop_task.cancel()
            try:
                await loop_task
            except asyncio.CancelledError:
                pass

        # Cancel tasks
        for task in [self._recv_task, self._heartbeat_task]:
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        # Cancel any pending throttle-release tasks so they don't outlive the
        # agent (a large retry_after can leave one sleeping for an hour).
        for task in list(self._throttle_tasks.values()):
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._throttle_tasks.clear()
        self._throttle_deadlines.clear()

        # Cancel any in-flight AI tool tasks (the receive loop is stopped, so
        # no new ones will start) before tearing down subsystems.
        if self._ai_tool_handler:
            try:
                await self._ai_tool_handler.shutdown()
            except Exception:
                log.debug("Error shutting down AI tool handler", exc_info=True)

        # Stop subsystems
        if self._tunnel_handler:
            await self._tunnel_handler.stop()
        if self._state_relay:
            await self._state_relay.stop()
        if self._alert_monitor:
            await self._alert_monitor.stop()
        if self._cert_manager:
            await self._cert_manager.stop()

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
                if e.reason in ("unknown_system", "no_key", "bad_system_id"):
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
        # Defense in depth: never send the system-key-derived auth proof or
        # signed payloads over a cleartext transport. connect() already gates
        # this, but re-assert at the point of connection so a reconfigured
        # endpoint can't downgrade us. Stop the loop rather than reconnecting.
        if not self._endpoint_is_encrypted(self._endpoint):
            log.error("Cloud agent: refusing insecure endpoint %r — wss:// required.", self._endpoint)
            self.state.set("system.cloud.status", "insecure_endpoint", source="cloud")
            self._running = False
            return

        self.state.set("system.cloud.status", "connecting", source="cloud")

        # Clear stale throttles from the previous connection, cancelling any
        # in-flight release task so it can't fire against the new connection.
        for task in list(self._throttle_tasks.values()):
            if not task.done():
                task.cancel()
        self._throttle_tasks.clear()
        self._throttle_deadlines.clear()
        for event in self._throttles.values():
            event.set()
        self._throttles.clear()

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
            self._enabled_capabilities = self._normalize_capabilities(
                result.enabled_capabilities
            ) or []

            # Handle upgrade_required from cloud
            if result.upgrade_required:
                min_ver = result.upgrade_required.get("min_version", "")
                self.state.set("system.cloud_upgrade_required", True, source="cloud")
                self.state.set("system.cloud_min_version", min_ver, source="cloud")
                log.warning(
                    "Cloud requires core v%s or later: %s",
                    min_ver, result.upgrade_required.get("message", ""),
                )
                # Apply the per-feature gate from `features_disabled`. The
                # cloud still sends the full `enabled_capabilities` list
                # for back-compat, so we have to subtract the disabled
                # features here. Without this, an outdated agent would
                # accept feature messages the cloud expects it to reject
                # (spec openavc-update-spec.md:326-335). See A59.
                features_disabled = result.upgrade_required.get("features_disabled") or []
                if features_disabled:
                    removed = [c for c in self._enabled_capabilities if c in features_disabled]
                    if removed:
                        self._enabled_capabilities = [
                            c for c in self._enabled_capabilities if c not in features_disabled
                        ]
                        log.warning(
                            "Cloud agent: disabled capabilities due to outdated core: %s",
                            removed,
                        )
            else:
                self.state.set("system.cloud_upgrade_required", False, source="cloud")
                self.state.set("system.cloud_min_version", "", source="cloud")

            # Apply the cloud update policy to the UpdateManager. Always apply,
            # even when update_policy is absent: session_start carries the full
            # authoritative config, so a missing policy means the documented
            # default 'manual' (apply_update_policy treats {} as manual and tears
            # down any auto loop), not "keep whatever loop was running before".
            await self._sync_update_policy(result.config)

            # Capture the prior session's last-acked seq before the reset
            # clears it: reset_for_new_session zeroes it so the new session's
            # acks (which restart from seq 1) are accounted for. The value is
            # sent in the resume message as a diagnostic — the cloud logs it
            # but always replies "replay everything" (replay_from_seq=1),
            # since the buffer holds only unacked messages anyway.
            prior_last_ack = self._sequencer.last_ack_seq

            # Reset sequencer for new session
            self._sequencer.reset_for_new_session()

            # Handle reconnection resume. Resume failure (e.g. cloud restarted
            # and lost the prior session) must not tear down the fresh session
            # we just established — drop the buffered messages and continue.
            # Buffered payloads are heartbeats and state batches; losing them
            # is acceptable, looping the connection forever is not.
            is_reconnect = self._disconnect_time is not None
            if is_reconnect and self._sequencer.buffer_count > 0:
                buffered = self._sequencer.buffer_count
                try:
                    replay_from = await handshake.send_resume(
                        send=self._send_raw,
                        recv=self._recv_raw,
                        last_ack_seq=prior_last_ack,
                        buffered_count=buffered,
                        disconnected_at=self._disconnect_time or "",
                    )
                    await self._replay_buffered(replay_from)
                except HandshakeError as e:
                    log.warning(
                        "Cloud agent: resume rejected by cloud (%s); "
                        "dropping %d buffered message(s) and continuing on the fresh session.",
                        e.reason, buffered,
                    )
                    self._sequencer.clear_buffer()

            # A stop() may have landed during the handshake/resume awaits. Bail
            # before spinning up steady-state tasks and subsystems so we don't
            # resurrect what stop() is tearing down.
            if self._stopping or not self._running:
                return

            # Connected!
            self._connected = True
            self._connected_at = _time.time()
            self._backoff = BACKOFF_INITIAL
            self._disconnect_time = None
            self._sig_failure_count = 0
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

            # Trusted-certificate connect-time self-check (renews certs for
            # instances that were offline through their renewal window).
            if self._cert_manager:
                await self._cert_manager.start()

            # Run until either steady-state task finishes (a clean disconnect
            # returns from the receive loop; an error raises), then re-raise the
            # first real error to drive reconnect.
            await self._await_steady_state(self._recv_task, self._heartbeat_task)

        finally:
            # Clean up on disconnect — stop subsystems so they re-initialize
            # (and re-send initial state snapshot) on the next connection.
            if self._state_relay:
                await self._state_relay.stop()
            if self._alert_monitor:
                await self._alert_monitor.stop()
            if self._cert_manager:
                await self._cert_manager.stop()
            if self._ws:
                try:
                    await self._ws.close()
                except (ConnectionClosed, OSError):
                    pass  # Best-effort close during disconnect cleanup
                self._ws = None

    @staticmethod
    async def _await_steady_state(recv_task: asyncio.Task, heartbeat_task: asyncio.Task) -> None:
        """Wait for the first steady-state task to finish, then tear down.

        ``asyncio.gather`` would leave the surviving sibling running against the
        closing socket (and its exception unretrieved) if the other task raised.
        Instead wait for FIRST_COMPLETED, cancel + await the still-pending
        sibling, then re-raise the first real exception so reconnect is driven by
        the original error (a clean return from the receive loop raises nothing
        and the caller reconnects normally).
        """
        done, pending = await asyncio.wait(
            {recv_task, heartbeat_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        for task in done:
            exc = task.exception()
            if exc is not None:
                raise exc

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

        # Check global and per-type throttle
        global_throttle = self._throttles.get("global")
        if global_throttle and not global_throttle.is_set():
            log.debug("Cloud agent: global throttle active, dropping message")
            return
        throttle_event = self._throttles.get(msg_type)
        if throttle_event and not throttle_event.is_set():
            log.debug(f"Cloud agent: message type {msg_type} is throttled, dropping")
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

        # Verify signature on steady-state messages. A signature mismatch is a
        # tamper or key-desync signal: dropping the message is right, but a
        # *persistent* run means every downstream command/rotation is silently
        # lost forever, so after a threshold we tear down the session and
        # reconnect (a fresh handshake re-derives the signing key).
        if self._session and not self._session.verify_incoming(msg):
            self._sig_failure_count += 1
            log.warning(
                f"Cloud agent: invalid signature on '{msg_type}' message, rejecting "
                f"({self._sig_failure_count}/{MAX_CONSECUTIVE_SIG_FAILURES})"
            )
            if self._sig_failure_count >= MAX_CONSECUTIVE_SIG_FAILURES:
                log.error(
                    "Cloud agent: %d consecutive signature failures — tearing down "
                    "session to re-handshake.",
                    self._sig_failure_count,
                )
                raise SessionInvalid("persistent downstream signature failure")
            return

        # A message verified (or there's no session yet): reset the failure run.
        self._sig_failure_count = 0

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

        # Capability gating: silently drop messages the cloud sent for features
        # this session didn't negotiate. Spec §13.8 line 1216: "If `tunnel` is
        # not enabled, the agent does not listen for `tunnel_open` messages."
        required = _CAPABILITY_GATED.get(msg_type)
        if required and required not in self._enabled_capabilities:
            log.warning(
                f"Cloud agent: dropping {msg_type} — '{required}' capability "
                f"not enabled for this session (enabled: {self._enabled_capabilities})"
            )
            return

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
            await self._handle_config_update(msg)
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
        elif msg_type == CERT_RESULT:
            if self._cert_manager:
                await self._cert_manager.handle_cert_result(msg)
            else:
                log.info("Cloud agent: received cert_result but no certificate manager")
        elif msg_type == CERT_RENEW_DUE:
            if self._cert_manager:
                await self._cert_manager.handle_renew_due(msg)
            else:
                log.info("Cloud agent: received cert_renew_due but no certificate manager")
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

    async def _handle_config_update(self, msg: dict[str, Any]) -> None:
        """Apply a mid-session config update from the server."""
        payload = extract_payload(msg)
        self._apply_config(payload)
        log.info(f"Cloud agent: config updated — {list(payload.keys()) if isinstance(payload, dict) else payload}")

        # A config_update is a partial merge, so only re-apply the update policy
        # when this message actually carries one — but when it does, push it
        # through to the UpdateManager immediately. Otherwise an operator
        # reverting a room to 'manual' (or narrowing its maintenance window)
        # would be ignored until the next reconnect, with the auto loop still
        # firing updates against the just-revoked intent.
        if isinstance(payload, dict) and "update_policy" in payload:
            mgr = getattr(self._command_handler, "_update_manager", None)
            if mgr:
                await mgr.apply_update_policy(payload.get("update_policy") or {})

    def _handle_capabilities_update(self, msg: dict[str, Any]) -> None:
        """Update enabled capabilities mid-session (e.g. plan change)."""
        payload = extract_payload(msg)
        new_caps = self._normalize_capabilities(payload.get("enabled_capabilities"))
        if new_caps is None:
            log.warning(
                "Cloud agent: ignoring malformed capabilities_update "
                "(enabled_capabilities is not a list) — keeping current capabilities"
            )
            return
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
        # retry_after crosses the trust boundary — coerce and clamp it so a bad
        # value can't crash the sleep or pin the type off forever.
        try:
            retry_after = float(payload.get("retry_after_seconds", 30))
        except (TypeError, ValueError):
            retry_after = 30.0
        retry_after = max(0.0, min(retry_after, THROTTLE_MAX_SECONDS))
        log.warning(
            f"Cloud agent: throttled on '{limit_type}' — "
            f"backing off for {retry_after}s"
        )

        # Create or get the throttle event for this message type
        if limit_type not in self._throttles:
            self._throttles[limit_type] = asyncio.Event()
        event = self._throttles[limit_type]
        event.clear()

        # Release on the LATEST of any existing and the new deadline, so a
        # second (shorter) throttle for the same type can't release early while
        # a longer one is in effect. Cancel the prior timer and schedule one for
        # the merged deadline. The task is tracked so shutdown/reconnect can
        # cancel it (no GC of a pending task, no stale task leaking across
        # connections).
        loop = asyncio.get_running_loop()
        deadline = max(self._throttle_deadlines.get(limit_type, 0.0), loop.time() + retry_after)
        self._throttle_deadlines[limit_type] = deadline

        old = self._throttle_tasks.pop(limit_type, None)
        if old and not old.done():
            old.cancel()
        task = asyncio.create_task(self._unthrottle(limit_type, deadline - loop.time()))
        self._throttle_tasks[limit_type] = task

    async def _unthrottle(self, limit_type: str, delay: float) -> None:
        """Release a throttle after the specified delay, then clean up."""
        try:
            if delay > 0:
                await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        event = self._throttles.pop(limit_type, None)
        self._throttle_deadlines.pop(limit_type, None)
        self._throttle_tasks.pop(limit_type, None)
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
        """Merge server config into local config.

        ``config`` crosses the cloud trust boundary (session_start / config_update).
        A non-dict value (protocol drift, or an attacker past the handshake) would
        otherwise raise in ``.items()`` and, on the session_start path, trap the
        agent in a permanent reconnect loop — so reject it and keep prior config.
        """
        if not isinstance(config, dict):
            log.warning("Cloud agent: ignoring non-dict config payload (%s)", type(config).__name__)
            return
        for key, value in config.items():
            if key == "features" and isinstance(value, dict):
                self._config.setdefault("features", {}).update(value)
            else:
                self._config[key] = value

    async def _sync_update_policy(self, config: dict[str, Any]) -> None:
        """Push the session_start update policy through to the UpdateManager.

        Session_start carries the full authoritative config, so this always
        applies — an absent ``update_policy`` resolves to the documented
        ``manual`` default (``apply_update_policy({})`` tears down any auto loop)
        rather than leaving a previously-started auto loop running.
        """
        mgr = getattr(self._command_handler, "_update_manager", None)
        if mgr:
            policy = config.get("update_policy") if isinstance(config, dict) else None
            await mgr.apply_update_policy(policy or {})

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

        # Drop the stale pre-reconnect buffer entries up front. `assign_seq`
        # below re-buffers each replayed message under its new seq, so the
        # buffer ends up holding exactly the replayed set (not duplicated old +
        # new entries). Crucially we do NOT clear after the loop: the re-buffered
        # messages must be retained until the cloud acks them, so a second
        # reconnect before that ack can replay them again (retain-until-acked).
        self._sequencer.clear_buffer()

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

    def set_cert_manager(self, manager: Any) -> None:
        """Wire the trusted-certificate manager subsystem."""
        self._cert_manager = manager

    @property
    def cert_manager(self) -> Any:
        """The trusted-certificate manager (None when not wired)."""
        return self._cert_manager

    # --- Status ---

    @property
    def connected(self) -> bool:
        """True while a session is established and steady state is running."""
        return self._connected

    def has_capability(self, capability: str) -> bool:
        """True if the current session negotiated the given capability."""
        return capability in self._enabled_capabilities

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
