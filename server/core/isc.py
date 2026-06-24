"""
OpenAVC Inter-System Communication (ISC).

Enables multiple OpenAVC instances to discover each other on the LAN
and communicate in real time via a WebSocket mesh:
- UDP broadcast auto-discovery (zero external dependencies)
- Selective state sharing (configurable glob patterns)
- Remote device command proxying
- Event forwarding and broadcast
- Manual peer configuration for cross-subnet setups

Architecture:
  - Discovery uses UDP broadcast beacons on port 19872 (no mDNS/zeroconf).
  - Outbound connections use the ``websockets`` client library.
  - Inbound connections arrive via the FastAPI endpoint in ``server/api/isc_ws.py``.
  - Both directions are normalised through ``PeerConnection`` so the
    ISCManager handles messages identically regardless of direction.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac as hmac_mod
import itertools
import json
import secrets
import socket
import uuid
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path
from time import time
from typing import Any, TYPE_CHECKING

from server.utils.logger import get_logger
from server.version import __version__ as _platform_version

if TYPE_CHECKING:
    from server.core.device_manager import DeviceManager
    from server.core.event_bus import EventBus
    from server.core.state_store import StateStore

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ISC_PROTOCOL_VERSION = "1"
ISC_WS_PATH = "/isc/ws"

# UDP discovery
DISCOVERY_PORT = 19872
DISCOVERY_MAGIC = b"OPENAVC1"      # 8-byte header to filter stray packets
BEACON_INTERVAL = 5.0              # seconds between broadcast beacons
BEACON_TTL = 15.0                  # consider a peer gone after this many seconds without a beacon

# Reconnection back-off (seconds)
RECONNECT_DELAYS = [5, 10, 20, 40, 60]
# Auth-rejection back-off (seconds). When a peer rejects us with auth_failed or
# auth_not_configured, retrying every minute forever is wasteful — auth keys
# only change on a project reload. Stretch the wait to multi-minute intervals
# and let reload() reset state when the user fixes the config.
AUTH_REJECT_DELAY = 300


class _ISCAuthRejected(ConnectionRefusedError):
    """Outbound connect was rejected for an auth reason — backoff is long."""

# Keepalive
PING_INTERVAL = 30.0
PING_TIMEOUT = 10.0

# Cap on the inbound auth-fail dedupe map. It is keyed by the peer's
# pre-auth (attacker-controlled) instance_id, so an unbounded map is a slow
# LAN memory-growth vector. The map is only a log-dedupe aid, not security
# state, so it's safe to drop wholesale once the cap is hit.
MAX_AUTH_FAIL_ENTRIES = 1024

# State batch window (seconds)
STATE_BATCH_INTERVAL = 0.2

# Flat-primitive types accepted from remote peers (plus None). Anything else
# is dropped rather than stored, preserving the store's flat-primitive
# invariant that WS broadcast / bindings / condition_eval / cloud relay rely on.
_REMOTE_STATE_PRIMITIVES = (bool, int, float, str)


# ---------------------------------------------------------------------------
# HMAC Challenge-Response Auth
# ---------------------------------------------------------------------------

def _derive_isc_hmac_key(auth_key: str) -> bytes:
    """Derive a fixed-length HMAC key from the user-provided auth key string."""
    return hashlib.sha256(auth_key.encode("utf-8")).digest()


def _compute_isc_hmac(auth_key: str, nonce: str) -> str:
    """Compute HMAC-SHA256(derived_key, nonce) and return as hex."""
    key = _derive_isc_hmac_key(auth_key)
    return hmac_mod.new(key, nonce.encode("utf-8"), hashlib.sha256).hexdigest()


# Mutual-auth domain separation. The inbound side proves key possession over
# the *client's* nonce (so a keyless rogue server can't extract an outbound
# instance's HMAC and relay it — see the chosen-message-oracle finding), and
# the outbound side answers over the *server's* nonce. The distinct prefixes
# stop a proof from one direction being replayed as the other.
_SERVER_PROOF_PREFIX = "isc-server:"
_CLIENT_PROOF_PREFIX = "isc-client:"


def _server_proof(auth_key: str, client_nonce: str) -> str:
    """HMAC the inbound side returns so the outbound side can verify it."""
    return _compute_isc_hmac(auth_key, _SERVER_PROOF_PREFIX + client_nonce)


def _client_proof(auth_key: str, server_nonce: str) -> str:
    """HMAC the outbound side returns to answer the inbound challenge."""
    return _compute_isc_hmac(auth_key, _CLIENT_PROOF_PREFIX + server_nonce)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_or_create_instance_id(project_path: Path) -> str:
    """Return a persistent instance UUID, creating one on first run."""
    id_file = project_path.parent / ".instance_id"
    if id_file.exists():
        instance_id = id_file.read_text(encoding="utf-8").strip()
        if instance_id:
            return instance_id
    instance_id = str(uuid.uuid4())
    try:
        id_file.write_text(instance_id, encoding="utf-8")
    except OSError:
        pass  # Non-fatal — use ephemeral ID
    return instance_id


def _is_private_ip(ip: str) -> bool:
    """Check if an IP address is in a private/local range."""
    import ipaddress
    try:
        addr = ipaddress.IPv4Address(ip)
        return addr.is_private or addr.is_loopback or addr.is_link_local
    except (ValueError, ipaddress.AddressValueError):
        return False


def _get_local_ip() -> str:
    """Detect the primary local IP address."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _parse_peer_address(addr: str) -> tuple[str, int]:
    """Parse ``"host:port"`` into (host, port). Default port = 8080."""
    if ":" in addr:
        host, port_str = addr.rsplit(":", 1)
        return host.strip(), int(port_str)
    return addr.strip(), 8080


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class PeerInfo:
    """Information about a discovered or configured peer instance.

    ``scheme`` is "http" or "https" — derived from the peer's beacon. Older
    peers without scheme info default to "http" (backward compatible).
    """
    instance_id: str
    name: str
    host: str
    port: int
    version: str = ""
    connected: bool = False
    discovered_at: float = field(default_factory=time)
    last_seen: float = field(default_factory=time)
    source: str = "discovered"  # "discovered", "manual", "inbound"
    scheme: str = "http"  # set to "https" when the peer advertises TLS


class PeerConnection:
    """Normalised wrapper around a peer WebSocket (inbound or outbound)."""

    # Process-wide monotonic counter. Used to identify a specific connection
    # instance so a stale orphan's late disconnect can't kill the live one
    # at the same peer_id (see A54 / A55 in the pre-release audit).
    _id_counter = itertools.count(1)

    def __init__(self, ws: Any, direction: str):
        self._ws = ws
        self.direction = direction  # "inbound" | "outbound"
        self._closed = False
        self.connection_id = next(PeerConnection._id_counter)

    async def send(self, msg: dict[str, Any]) -> None:
        text = json.dumps(msg)
        if self.direction == "inbound":
            await self._ws.send_text(text)   # FastAPI WebSocket
        else:
            await self._ws.send(text)         # websockets library

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self._ws.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# ISCManager
# ---------------------------------------------------------------------------

class ISCManager:
    """
    Inter-System Communication manager.

    Manages the full lifecycle of peer discovery and communication:
    1. Announces this instance on the LAN via UDP broadcast beacons (port 19872)
    2. Discovers other OpenAVC instances automatically
    3. Establishes WebSocket connections to peers
    4. Shares configured state keys with connected peers
    5. Receives and stores remote state under ``isc.<peer_id>.<key>``
    6. Forwards device commands and events between instances
    """

    def __init__(
        self,
        state: StateStore,
        events: EventBus,
        devices: DeviceManager,
        shared_state_patterns: list[str],
        auth_key: str,
        instance_id: str,
        instance_name: str,
        http_port: int,
        manual_peers: list[str] | None = None,
        allowed_remote_commands: list[str] | None = None,
    ):
        self.state = state
        self.events = events
        self.devices = devices
        self._shared_patterns = list(shared_state_patterns)
        self._auth_key = auth_key
        # Glob allowlist (matched against "<device_id>.<command>") gating which
        # device commands a peer may execute on us. Empty = deny all remote
        # commands. Authenticating to the mesh does not by itself grant device
        # control; the operator opts in per device/command.
        self._allowed_remote_commands = list(allowed_remote_commands or [])
        self.instance_id = instance_id
        self.instance_name = instance_name
        self.http_port = http_port
        self._manual_peers = manual_peers or []

        # Peer tracking
        self._peers: dict[str, PeerInfo] = {}
        self._connections: dict[str, PeerConnection] = {}

        # Auth-reject de-duplication: once we've logged that a peer rejected
        # the inbound auth, drop subsequent identical rejections to debug to
        # avoid the log flood A57 calls out. Reset by reload() when the user
        # changes the auth key.
        self._inbound_auth_fails: dict[str, int] = {}

        # Background tasks
        self._tasks: list[asyncio.Task] = []
        self._connect_tasks: dict[str, asyncio.Task] = {}

        self._running = False

        # Outgoing state batch (shared-key changes queued for peers)
        self._outgoing_batch: dict[str, Any] = {}

        # State subscriptions & event handler IDs
        self._state_sub_ids: list[str] = []
        self._event_handler_ids: list[str] = []

        # Lock for peer/connection dict modifications
        self._peer_lock = asyncio.Lock()

        # Pending remote-command futures
        self._pending_commands: dict[str, asyncio.Future] = {}
        self._pending_command_peers: dict[str, tuple[asyncio.Future, str]] = {}  # request_id -> (future, peer_id)

        # UDP discovery
        self._discovery_sock: socket.socket | None = None
        self._discovery_transport: Any = None  # asyncio DatagramTransport
        self._beacon_rate: dict[str, float] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start ISC: begin UDP-beacon discovery, connect to manual peers."""
        if self._running:
            return
        self._running = True
        log.info(
            f"ISC starting (id={self.instance_id[:8]}…, "
            f"name={self.instance_name}, "
            f"{len(self._shared_patterns)} shared patterns, "
            f"{len(self._manual_peers)} manual peers)"
        )

        # Subscribe to local state changes for sharing with peers
        for pattern in self._shared_patterns:
            sub_id = self.state.subscribe(pattern, self._on_local_state_change)
            self._state_sub_ids.append(sub_id)

        # Listen for ISC events from scripts / macros
        hid = self.events.on("isc.send_to", self._on_isc_send_to_event)
        self._event_handler_ids.append(hid)
        hid = self.events.on("isc.broadcast", self._on_isc_broadcast_event)
        self._event_handler_ids.append(hid)

        # Start background tasks
        self._tasks.append(asyncio.create_task(self._state_batch_loop()))
        self._tasks.append(asyncio.create_task(self._ping_loop()))

        # Start UDP discovery
        await self._start_discovery()

        # Schedule manual peer connections
        for addr in self._manual_peers:
            host, port = _parse_peer_address(addr)
            # Use address as temporary peer key until we learn the instance_id
            tmp_key = f"manual:{host}:{port}"
            self._peers[tmp_key] = PeerInfo(
                instance_id=tmp_key, name=addr, host=host, port=port,
                source="manual",
            )
            self._schedule_connect(tmp_key, host, port)

        self.state.set("system.isc.enabled", True, source="system")
        log.info("ISC started")

    async def stop(self) -> None:
        """Stop ISC gracefully."""
        if not self._running:
            return
        self._running = False
        log.info("ISC stopping…")

        # Cancel background tasks
        for task in self._tasks:
            task.cancel()
        for task in self._connect_tasks.values():
            task.cancel()
        all_tasks = self._tasks + list(self._connect_tasks.values())
        if all_tasks:
            await asyncio.gather(*all_tasks, return_exceptions=True)
        self._tasks.clear()
        self._connect_tasks.clear()

        # Close all peer connections
        for peer_id in list(self._connections):
            await self._close_peer(peer_id, emit=False)

        # Stop UDP discovery
        self._stop_discovery()

        # Unsubscribe from state changes
        for sub_id in self._state_sub_ids:
            self.state.unsubscribe(sub_id)
        self._state_sub_ids.clear()

        # Unsubscribe event handlers
        for hid in self._event_handler_ids:
            self.events.off(hid)
        self._event_handler_ids.clear()

        # Clean up ISC state keys
        self._clear_isc_state()

        # Cancel pending commands
        for future in self._pending_commands.values():
            if not future.done():
                future.cancel()
        self._pending_commands.clear()

        self._peers.clear()
        self.state.set("system.isc.enabled", False, source="system")
        log.info("ISC stopped")

    async def reload(
        self,
        shared_state_patterns: list[str],
        auth_key: str,
        manual_peers: list[str],
        allowed_remote_commands: list[str] | None = None,
    ) -> None:
        """Hot-reload ISC config without full restart."""
        self._shared_patterns = list(shared_state_patterns)
        self._allowed_remote_commands = list(allowed_remote_commands or [])

        # Capture the old auth key before overwriting it so we can detect
        # rotation and force-disconnect existing peers below.
        old_auth_key = self._auth_key
        self._auth_key = auth_key

        # Re-subscribe state listeners
        for sub_id in self._state_sub_ids:
            self.state.unsubscribe(sub_id)
        self._state_sub_ids.clear()
        for pattern in self._shared_patterns:
            sub_id = self.state.subscribe(pattern, self._on_local_state_change)
            self._state_sub_ids.append(sub_id)

        # Handle manual peers changes
        new_set = set(manual_peers)
        old_set = set(self._manual_peers)
        self._manual_peers = list(manual_peers)

        # If the auth key rotated, force-disconnect every active connection
        # — both manual and discovered. Existing sockets still hold the old
        # key on both sides; new handshakes will use the new key. Manual
        # peers are re-scheduled below; discovered peers will be re-found
        # via UDP discovery.
        auth_key_changed = old_auth_key != auth_key
        if auth_key_changed:
            for peer_id in list(self._connections.keys()):
                await self._close_peer(peer_id, emit=True)
            # Auth key rotated — clear the inbound auth-fail dedupe map so
            # the next failure (if any) is logged at WARNING instead of
            # silently re-deduped against an unrelated key.
            self._inbound_auth_fails.clear()

        # Drop peers removed from the manual list. After handshake the peer
        # may be re-keyed from "manual:host:port" to the real instance_id,
        # so we match by (source="manual", host, port) — plus the legacy
        # tmp_key in case handshake never completed.
        for addr in old_set - new_set:
            host, port = _parse_peer_address(addr)
            tmp_key = f"manual:{host}:{port}"
            to_remove = [
                peer_id for peer_id, peer in self._peers.items()
                if peer.source == "manual" and peer.host == host and peer.port == port
            ]
            if tmp_key in self._peers and tmp_key not in to_remove:
                to_remove.append(tmp_key)
            for peer_id in to_remove:
                # Cancel any pending reconnect attempt before closing.
                task = self._connect_tasks.pop(peer_id, None)
                if task and not task.done():
                    task.cancel()
                await self._close_peer(peer_id, emit=True)
                self._peers.pop(peer_id, None)

        for addr in new_set - old_set:
            host, port = _parse_peer_address(addr)
            tmp_key = f"manual:{host}:{port}"
            if tmp_key not in self._peers:
                self._peers[tmp_key] = PeerInfo(
                    instance_id=tmp_key, name=addr, host=host, port=port,
                    source="manual",
                )
                self._schedule_connect(tmp_key, host, port)

        # If the auth key changed but the manual peer list didn't, the
        # re-scheduling above won't fire for those peers — kick a reconnect
        # for every manual peer so they re-handshake with the new key.
        if auth_key_changed:
            for addr in new_set & old_set:
                host, port = _parse_peer_address(addr)
                tmp_key = f"manual:{host}:{port}"
                if tmp_key not in self._peers:
                    self._peers[tmp_key] = PeerInfo(
                        instance_id=tmp_key, name=addr, host=host, port=port,
                        source="manual",
                    )
                self._schedule_connect(tmp_key, host, port)

        log.info(f"ISC reloaded ({len(self._shared_patterns)} patterns, {len(self._manual_peers)} manual peers)")

    # ------------------------------------------------------------------
    # Public API (used by REST endpoints & script proxy)
    # ------------------------------------------------------------------

    def _require_connection(self, instance_id: str) -> PeerConnection:
        """Resolve a live peer connection or raise a precise ConnectionError.

        Distinguishes a peer that's known but still mid-handshake (the
        connection is keyed by the real instance_id only once the handshake
        completes) from one we've never heard of, so callers get an accurate
        message instead of a bare "not connected".
        """
        conn = self._connections.get(instance_id)
        if conn is not None:
            return conn
        if instance_id in self._peers:
            raise ConnectionError(
                f"Peer '{instance_id}' is known but not connected yet "
                f"(handshake pending)"
            )
        raise ConnectionError(f"Not connected to instance '{instance_id}'")

    async def send_to(
        self, instance_id: str, event: str, payload: dict[str, Any] | None = None,
    ) -> None:
        """Send an event to a specific peer instance."""
        conn = self._require_connection(instance_id)
        await conn.send({
            "type": "isc.event",
            "event": event,
            "payload": payload or {},
        })

    async def broadcast(
        self, event: str, payload: dict[str, Any] | None = None,
    ) -> None:
        """Send an event to all connected peers."""
        msg = {"type": "isc.event", "event": event, "payload": payload or {}}
        for peer_id, conn in list(self._connections.items()):
            try:
                await conn.send(msg)
            except Exception:
                log.debug(f"ISC: broadcast to {peer_id[:8]} failed")

    async def send_command(
        self,
        instance_id: str,
        device_id: str,
        command: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Send a device command to a remote instance and wait for the result."""
        conn = self._require_connection(instance_id)

        request_id = str(uuid.uuid4())
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        self._pending_commands[request_id] = future
        self._pending_command_peers[request_id] = (future, instance_id)

        # try/finally guarantees both pending maps are cleaned even if the
        # send() raises after registration or the wait_for times out —
        # otherwise the future leaks when _close_peer never runs for this
        # peer (e.g. a leaked connection entry).
        try:
            await conn.send({
                "type": "isc.command",
                "id": request_id,
                "device": device_id,
                "command": command,
                "params": params or {},
            })
            return await asyncio.wait_for(future, timeout=10.0)
        except asyncio.TimeoutError:
            raise TimeoutError(
                f"Command to {instance_id[:8]}:{device_id}.{command} timed out"
            )
        finally:
            self._pending_commands.pop(request_id, None)
            self._pending_command_peers.pop(request_id, None)

    def get_instances(self) -> list[dict[str, Any]]:
        """List all discovered/configured peer instances."""
        return [
            {
                "instance_id": p.instance_id,
                "name": p.name,
                "host": p.host,
                "port": p.port,
                "version": p.version,
                "connected": p.connected,
                "source": p.source,
                "last_seen": p.last_seen,
            }
            for p in self._peers.values()
        ]

    def get_status(self) -> dict[str, Any]:
        """ISC status summary."""
        connected = sum(1 for p in self._peers.values() if p.connected)
        return {
            "enabled": True,
            "instance_id": self.instance_id,
            "instance_name": self.instance_name,
            "peer_count": len(self._peers),
            "connected_count": connected,
            "shared_patterns": self._shared_patterns,
            "auth_key_set": bool(self._auth_key),
        }

    # ------------------------------------------------------------------
    # Inbound connection handling (called by isc_ws.py)
    # ------------------------------------------------------------------

    def _log_inbound_auth_fail(self, peer_id: str, reason: str) -> None:
        """Log an inbound auth rejection, downgrading repeats to debug (A57)."""
        n = self._inbound_auth_fails.get(peer_id, 0)
        if n == 0 and len(self._inbound_auth_fails) >= MAX_AUTH_FAIL_ENTRIES:
            # A fresh attacker-chosen instance_id would grow the map without
            # bound. Drop the accumulated counters (log-dedupe only) so memory
            # stays capped; the worst case is a few repeat rejections logging
            # at WARNING again.
            log.debug("ISC: auth-fail dedupe map full (%d), clearing", len(self._inbound_auth_fails))
            self._inbound_auth_fails.clear()
        n += 1
        self._inbound_auth_fails[peer_id] = n
        if n == 1:
            log.warning(f"ISC: Rejected {peer_id[:8]} — {reason}")
        else:
            log.debug(f"ISC: Rejected {peer_id[:8]} — {reason} (attempt #{n})")

    async def accept_inbound(self, ws: Any, hello: dict[str, Any]) -> str | None:
        """
        Validate an inbound hello message and register the peer.

        Returns the peer's ``instance_id`` on success, or ``None`` if rejected
        (the WebSocket is closed with a reject message in that case).
        """
        peer_id = hello.get("instance_id", "")
        peer_name = hello.get("name", "")
        peer_version = hello.get("version", "")

        if not peer_id:
            await _ws_send_fastapi(ws, {"type": "isc.reject", "reason": "missing instance_id"})
            return None

        if not self._auth_key:
            await _ws_send_fastapi(ws, {"type": "isc.reject", "reason": "auth_not_configured"})
            self._log_inbound_auth_fail(peer_id, "no auth key configured")
            return None

        # Mutual HMAC challenge-response. We send our own nonce *and* a proof
        # over the peer's client_nonce, so the peer can confirm we hold the key
        # before it discloses its own HMAC. This denies a keyless rogue server
        # the chosen-message oracle it would otherwise use to relay an honest
        # instance's response into a forged auth elsewhere. A peer that omits
        # client_nonce (older client) just gets a proof over the empty string.
        peer_client_nonce = hello.get("client_nonce", "")
        if not isinstance(peer_client_nonce, str):
            peer_client_nonce = ""
        nonce = secrets.token_hex(32)
        await _ws_send_fastapi(ws, {
            "type": "isc.challenge",
            "nonce": nonce,
            "server_proof": _server_proof(self._auth_key, peer_client_nonce),
        })

        try:
            auth_text = await asyncio.wait_for(ws.receive_text(), timeout=10.0)
            auth_msg = json.loads(auth_text)
        except (asyncio.TimeoutError, json.JSONDecodeError):
            await _ws_send_fastapi(ws, {"type": "isc.reject", "reason": "auth_timeout"})
            return None

        if auth_msg.get("type") != "isc.auth":
            await _ws_send_fastapi(ws, {"type": "isc.reject", "reason": "expected isc.auth"})
            return None

        expected_response = _client_proof(self._auth_key, nonce)
        if not hmac_mod.compare_digest(expected_response, str(auth_msg.get("response", ""))):
            await _ws_send_fastapi(ws, {"type": "isc.reject", "reason": "auth_failed"})
            self._log_inbound_auth_fail(peer_id, "HMAC verification failed")
            return None

        # Auth succeeded — clear any prior fail count so a future failure is
        # logged at WARNING again (the peer recovered or rotated keys).
        self._inbound_auth_fails.pop(peer_id, None)

        # Atomic duplicate-detect + register under _peer_lock. An in-flight
        # outbound (still in handshake, not yet in _connections) also counts
        # as a duplicate — otherwise a simultaneous bidirectional connect
        # leaves both sides with an orphan socket per peer (A54).
        async with self._peer_lock:
            existing = self._connections.get(peer_id)
            has_outbound_task = peer_id in self._connect_tasks

            if existing is not None or has_outbound_task:
                if self.instance_id < peer_id:
                    # Our id is smaller — the outbound direction is canonical;
                    # reject this inbound. Lock released by context manager on
                    # the return below.
                    await _ws_send_fastapi(ws, {"type": "isc.reject", "reason": "duplicate"})
                    log.debug(
                        f"ISC: Rejected inbound from {peer_id[:8]} "
                        f"(outbound direction is canonical)"
                    )
                    return None

                # Their id is smaller — the inbound direction is canonical.
                # Close any existing connection (identity-safe: we hold the
                # lock and pop+close in one step).
                if existing is not None:
                    self._connections.pop(peer_id, None)
                    await existing.close()
                # Cancel any in-flight outbound attempt. Pop first so a fresh
                # _schedule_connect() can run again if needed; cancel() is
                # non-blocking so it's safe inside the lock.
                if has_outbound_task:
                    task = self._connect_tasks.pop(peer_id, None)
                    if task is not None and not task.done():
                        task.cancel()

            log.info(f"ISC: Accepted inbound peer «{peer_name}» ({peer_id[:8]})")

            # Register peer
            peer = self._peers.get(peer_id)
            if peer is None:
                peer = PeerInfo(
                    instance_id=peer_id, name=peer_name, host="", port=0,
                    version=peer_version, source="inbound",
                )
                self._peers[peer_id] = peer
            peer.connected = True
            peer.name = peer_name
            peer.version = peer_version
            peer.last_seen = time()

            # Store connection
            conn = PeerConnection(ws, "inbound")
            self._connections[peer_id] = conn

        # Send welcome
        await _ws_send_fastapi(ws, {
            "type": "isc.welcome",
            "instance_id": self.instance_id,
            "name": self.instance_name,
            "version": _platform_version,
            "protocol": ISC_PROTOCOL_VERSION,
        })

        # Send initial shared state
        shared = self._get_shared_state()
        if shared:
            await _ws_send_fastapi(ws, {"type": "isc.state", "changes": shared})

        await self.events.emit("isc.peer_connected", {
            "instance_id": peer_id, "name": peer_name,
        })
        self._push_isc_update()
        return peer_id

    async def handle_message(self, peer_id: str, msg: dict[str, Any]) -> None:
        """Process a message from a connected peer (either direction)."""
        peer = self._peers.get(peer_id)
        if peer:
            peer.last_seen = time()

        msg_type = msg.get("type", "")

        if msg_type == "isc.state":
            self._apply_remote_state(peer_id, msg.get("changes", {}))

        elif msg_type == "isc.command":
            await self._handle_remote_command(peer_id, msg)

        elif msg_type == "isc.command_result":
            self._handle_command_result(msg)

        elif msg_type == "isc.event":
            await self._handle_remote_event(peer_id, msg)

        elif msg_type == "isc.pong":
            pass  # Keepalive acknowledged

        elif msg_type == "isc.ping":
            conn = self._connections.get(peer_id)
            if conn:
                try:
                    await conn.send({"type": "isc.pong"})
                except Exception:
                    pass

        else:
            log.debug(f"ISC: Unknown message type from {peer_id[:8]}: {msg_type}")

    async def peer_disconnected(self, peer_id: str, conn: PeerConnection | None = None) -> None:
        """Called when a peer connection drops (either direction).

        ``conn`` should be the exact PeerConnection instance that owned the
        socket. If the tracked connection at ``peer_id`` no longer matches
        (e.g. it was replaced by a fresh reconnection while this orphan was
        still alive), the disconnect is ignored — see A55.
        """
        await self._close_peer(peer_id, emit=True, conn=conn)

    # ------------------------------------------------------------------
    # UDP broadcast discovery
    # ------------------------------------------------------------------

    async def _start_discovery(self) -> None:
        """Start UDP beacon broadcasting and listening. Non-fatal on failure."""
        try:
            loop = asyncio.get_running_loop()

            # Create a UDP socket for broadcast send + receive
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setblocking(False)
            sock.bind(("", DISCOVERY_PORT))
            self._discovery_sock = sock

            # Wrap in asyncio datagram transport
            transport, _ = await loop.create_datagram_endpoint(
                lambda: _DiscoveryProtocol(self),
                sock=sock,
            )
            self._discovery_transport = transport

            # Start beacon broadcast loop
            self._tasks.append(asyncio.create_task(self._beacon_loop()))

            ip = _get_local_ip()
            log.info(f"ISC: UDP discovery started on port {DISCOVERY_PORT} (local IP: {ip})")
        except Exception:
            log.exception("ISC: UDP discovery startup failed — manual peers still work")

    def _stop_discovery(self) -> None:
        """Close the UDP discovery socket."""
        if self._discovery_transport:
            self._discovery_transport.close()
            self._discovery_transport = None
        self._discovery_sock = None

    async def _beacon_loop(self) -> None:
        """Broadcast our presence every BEACON_INTERVAL seconds."""
        beacon = self._build_beacon()
        try:
            while self._running:
                # Reap peers that have gone silent past BEACON_TTL so ghosts
                # don't linger forever and inflate peer_count.
                self._prune_stale_peers()
                if self._discovery_transport:
                    self._discovery_transport.sendto(
                        beacon, ("255.255.255.255", DISCOVERY_PORT),
                    )
                await asyncio.sleep(BEACON_INTERVAL)
        except asyncio.CancelledError:
            return
        except Exception:
            log.debug("ISC: Beacon loop error (non-fatal)")

    def _build_beacon(self) -> bytes:
        """Build a discovery beacon packet: magic header + JSON payload.

        ``scheme`` and ``tls_port`` were added in Phase 6 of the HTTPS plan.
        They are additive — older peers ignore unknown JSON keys, newer peers
        default to ``scheme="http"`` when the field is missing — so
        ISC_PROTOCOL_VERSION stays "1".
        """
        from server import config
        body: dict[str, Any] = {
            "instance_id": self.instance_id,
            "name": self.instance_name,
            "port": self.http_port,
            "version": _platform_version,
            "protocol": ISC_PROTOCOL_VERSION,
        }
        if config.TLS_ENABLED:
            body["scheme"] = "https"
            body["tls_port"] = config.TLS_PORT
        payload = json.dumps(body).encode()
        return DISCOVERY_MAGIC + payload

    def _handle_beacon(self, data: bytes, addr: tuple[str, int]) -> None:
        """Process an incoming discovery beacon from the network."""
        if not data.startswith(DISCOVERY_MAGIC):
            return

        # Rate limit beacon processing: max 1 per source IP per 5 seconds
        now = time()
        source_ip = addr[0]
        last_seen = self._beacon_rate.get(source_ip, 0)
        if now - last_seen < 5.0:
            return
        self._beacon_rate[source_ip] = now
        if len(self._beacon_rate) > 500:
            cutoff = now - 30.0
            self._beacon_rate = {ip: t for ip, t in self._beacon_rate.items() if t > cutoff}

        # Validate source is a private/local IP (not spoofed from internet)
        if not _is_private_ip(source_ip):
            return

        try:
            payload = json.loads(data[len(DISCOVERY_MAGIC):])
        except (json.JSONDecodeError, UnicodeDecodeError):
            return

        peer_id = payload.get("instance_id", "")
        if not peer_id or peer_id == self.instance_id:
            return  # Skip self

        peer_name = payload.get("name", "")
        # The peer's beacon may advertise scheme="https" + a separate tls_port.
        # In that case we connect to wss://host:tls_port rather than ws://host:port
        # — outbound WebSocket clients don't follow HTTP 301 redirects.
        peer_scheme = payload.get("scheme", "http")
        if peer_scheme == "https":
            peer_port = int(payload.get("tls_port") or payload.get("port", 8443))
        else:
            peer_port = int(payload.get("port", 8080))
        host = addr[0]  # Sender's IP from the UDP packet

        existing = self._peers.get(peer_id)
        if existing and existing.connected:
            # Already connected — just update last_seen
            existing.last_seen = time()
            return

        if existing and existing.source == "discovered":
            # Known but not connected — update last_seen
            existing.last_seen = time()
            return

        log.info(f"ISC: Discovered «{peer_name}» ({peer_id[:8]}) at {peer_scheme}://{host}:{peer_port}")

        self._peers[peer_id] = PeerInfo(
            instance_id=peer_id, name=peer_name, host=host, port=peer_port,
            version=payload.get("version", ""), source="discovered",
            scheme=peer_scheme,
        )
        self._push_isc_update()

        # Connect if not already connected
        if peer_id not in self._connections:
            self._schedule_connect(peer_id, host, peer_port, peer_scheme)

    # ------------------------------------------------------------------
    # Outbound connections
    # ------------------------------------------------------------------

    def _schedule_connect(self, peer_id: str, host: str, port: int, scheme: str = "http") -> None:
        """Schedule a background task to connect to a peer.

        ``scheme`` is "http" (default) or "https" — set by callers that have
        TLS info from the peer's beacon. Manual-config callers leave it at
        the default; they're expected to connect over plain ws://.
        """
        if peer_id in self._connect_tasks:
            return
        task = asyncio.create_task(self._outbound_loop(peer_id, host, port, scheme))
        self._connect_tasks[peer_id] = task
        # Self-remove once the loop ends (e.g. it returned after losing a
        # bidirectional tie-break). Otherwise the completed task lingers and a
        # later beacon's _schedule_connect short-circuits on membership, so we
        # never re-dial a peer whose canonical inbound connection later drops.
        task.add_done_callback(lambda t, pid=peer_id: self._on_connect_task_done(pid, t))

    def _on_connect_task_done(self, peer_id: str, task: asyncio.Task) -> None:
        """Drop a finished outbound task from tracking (identity-checked)."""
        if self._connect_tasks.get(peer_id) is task:
            self._connect_tasks.pop(peer_id, None)

    async def _outbound_loop(self, peer_id: str, host: str, port: int, scheme: str = "http") -> None:
        """Maintain an outbound connection to a peer with reconnection."""
        attempt = 0
        auth_fails = 0
        delay: float | None = None
        while self._running:
            try:
                await self._outbound_connect(peer_id, host, port, scheme)
                attempt = 0  # Reset on successful connection
                auth_fails = 0
                delay = None
            except asyncio.CancelledError:
                return
            except _ISCAuthRejected as e:
                # Auth keys only change on project reload; retrying every
                # minute floods both logs and the peer. Stretch the backoff
                # and log only the first failure at WARNING. (A57)
                auth_fails += 1
                if auth_fails == 1:
                    log.warning(
                        f"ISC: Peer {peer_id[:8]} auth rejected ({e}); "
                        f"backing off to {AUTH_REJECT_DELAY}s until configuration changes"
                    )
                else:
                    log.debug(
                        f"ISC: Peer {peer_id[:8]} auth rejected ({e}); "
                        f"attempt #{auth_fails}"
                    )
                delay = AUTH_REJECT_DELAY
            except ConnectionRefusedError as e:
                if "duplicate" in str(e):
                    log.debug(f"ISC: Peer {peer_id[:8]} already connected (duplicate), stopping outbound")
                    return
                log.debug(f"ISC: Outbound to {peer_id[:8]} refused: {e}")
            except Exception as e:
                log.debug(f"ISC: Outbound to {peer_id[:8]} failed: {e}")

            if not self._running:
                return

            # Back off — auth rejections use the longer fixed delay; otherwise
            # walk the standard escalating schedule.
            if delay is None:
                idx = min(attempt, len(RECONNECT_DELAYS) - 1)
                delay = RECONNECT_DELAYS[idx]
                attempt += 1
            log.debug(f"ISC: Reconnecting to {peer_id[:8]} in {delay}s")
            await asyncio.sleep(delay)
            delay = None

    async def _outbound_connect(self, peer_id: str, host: str, port: int, scheme: str = "http") -> None:
        """Connect to a peer, authenticate, and run the message loop.

        When ``scheme`` is "https", connects via wss:// with an unverified
        SSL context — peers are self-signed by design (loopback / local LAN
        trust model). See openavc-https-tls-plan.md §"ISC (Inter-System
        Communication) — TLS-aware peers".
        """
        import websockets

        ssl_ctx = None
        if scheme == "https":
            import ssl as _ssl
            ssl_ctx = _ssl._create_unverified_context()
            url = f"wss://{host}:{port}{ISC_WS_PATH}"
        else:
            url = f"ws://{host}:{port}{ISC_WS_PATH}"
        log.debug(f"ISC: Connecting to {url}")

        async with websockets.connect(url, close_timeout=5, ssl=ssl_ctx) as ws:
            # Send hello with our own nonce so the peer must prove key
            # possession over it before we disclose our HMAC.
            client_nonce = secrets.token_hex(32)
            await ws.send(json.dumps({
                "type": "isc.hello",
                "instance_id": self.instance_id,
                "name": self.instance_name,
                "version": _platform_version,
                "protocol": ISC_PROTOCOL_VERSION,
                "client_nonce": client_nonce,
            }))

            # Wait for challenge or reject
            resp_text = await asyncio.wait_for(ws.recv(), timeout=10)
            resp = json.loads(resp_text)

            if resp.get("type") == "isc.reject":
                reason = resp.get("reason", "unknown")
                if reason in ("auth_failed", "auth_not_configured"):
                    raise _ISCAuthRejected(reason)
                log.warning(f"ISC: Peer {peer_id[:8]} rejected: {reason}")
                raise ConnectionRefusedError(reason)

            if resp.get("type") != "isc.challenge":
                raise ConnectionError(f"Expected isc.challenge, got: {resp.get('type')}")

            # Verify the peer's proof over our client_nonce BEFORE answering.
            # A keyless rogue server can't produce it, so we never hand it an
            # HMAC it could relay into a forged auth elsewhere. (Mutual auth.)
            expected_server_proof = _server_proof(self._auth_key, client_nonce)
            if not hmac_mod.compare_digest(
                expected_server_proof, str(resp.get("server_proof", ""))
            ):
                raise _ISCAuthRejected("server_auth_failed")

            # Respond to the peer's challenge with our domain-separated proof.
            nonce = resp.get("nonce", "")
            await ws.send(json.dumps({
                "type": "isc.auth",
                "response": _client_proof(self._auth_key, nonce),
            }))

            # Wait for welcome or reject
            welcome_text = await asyncio.wait_for(ws.recv(), timeout=10)
            resp = json.loads(welcome_text)

            if resp.get("type") == "isc.reject":
                reason = resp.get("reason", "unknown")
                if reason in ("auth_failed", "auth_not_configured"):
                    raise _ISCAuthRejected(reason)
                log.warning(f"ISC: Peer {peer_id[:8]} rejected: {reason}")
                raise ConnectionRefusedError(reason)

            if resp.get("type") != "isc.welcome":
                raise ConnectionError(f"Expected isc.welcome, got: {resp.get('type')}")

            # Update peer info from welcome
            real_id = resp.get("instance_id", peer_id)
            real_name = resp.get("name", "")
            real_version = resp.get("version", "")

            # If the peer_id was a temporary key (manual peer), remap.
            # Then apply the duplicate tie-break against any existing
            # connection at the real id (A56: close the loser instead of
            # silently overwriting).
            async with self._peer_lock:
                if peer_id != real_id:
                    self._peers.pop(peer_id, None)
                    existing_tmp = self._connections.pop(peer_id, None)
                    if existing_tmp is not None:
                        # Shouldn't normally happen, but if a connection was
                        # tracked under the temp key, close it before remap.
                        await existing_tmp.close()
                    peer_id = real_id

                existing = self._connections.get(peer_id)
                if existing is not None:
                    if self.instance_id > peer_id:
                        # Their id is smaller — inbound direction is canonical;
                        # this outbound loses the tie-break. Abort cleanly so
                        # _outbound_loop catches the "duplicate" reason and
                        # stops retrying.
                        log.debug(
                            f"ISC: Outbound to {peer_id[:8]} loses tie-break "
                            f"(inbound direction is canonical)"
                        )
                        raise ConnectionRefusedError("duplicate")
                    # Our id is smaller — outbound direction is canonical;
                    # close the existing inbound (or stale outbound) before
                    # replacing it.
                    self._connections.pop(peer_id, None)
                    await existing.close()

                peer = self._peers.get(peer_id)
                if peer is None:
                    peer = PeerInfo(
                        instance_id=peer_id, name=real_name, host=host, port=port,
                        source="discovered",
                    )
                    self._peers[peer_id] = peer
                peer.connected = True
                peer.name = real_name or peer.name
                peer.version = real_version
                peer.last_seen = time()

                conn = PeerConnection(ws, "outbound")
                self._connections[peer_id] = conn

            # conn is now registered in _connections. Everything below can
            # raise (abnormal disconnect, a handler exception), so guarantee
            # cleanup in a finally — otherwise the entry leaks as 'connected'
            # forever and the batch/ping loops keep sending to a dead socket.
            # Pass `conn` so a stale disconnect can't pop a newer inbound that
            # replaced this one in the meantime (A55).
            try:
                log.info(f"ISC: Connected to «{peer.name}» ({peer_id[:8]}) at {host}:{port}")

                # Send our shared state
                shared = self._get_shared_state()
                if shared:
                    await conn.send({"type": "isc.state", "changes": shared})

                await self.events.emit("isc.peer_connected", {
                    "instance_id": peer_id, "name": peer.name,
                })
                self._push_isc_update()

                # Message loop
                async for raw in ws:
                    if not self._running:
                        break
                    msg = json.loads(raw)
                    await self.handle_message(peer_id, msg)
            finally:
                await self._close_peer(peer_id, emit=True, conn=conn)

    # ------------------------------------------------------------------
    # Message handlers
    # ------------------------------------------------------------------

    def _apply_remote_state(self, peer_id: str, changes: dict[str, Any]) -> None:
        """Write remote state changes into the local StateStore.

        Remote peers are only semi-trusted, so the wire shape is validated
        here: a buggy or malicious peer must not be able to store a nested
        object under ``isc.<peer>.<key>`` and break the flat-primitive state
        invariant. Non-dict payloads, non-string keys, and non-primitive
        values are dropped (not coerced — we don't want to persist a peer's
        arbitrary blob even as a string).
        """
        if not isinstance(changes, dict):
            log.debug("ISC: ignoring non-dict isc.state from %s", peer_id[:8])
            return
        for key, value in changes.items():
            if not isinstance(key, str):
                continue
            if value is not None and not isinstance(value, _REMOTE_STATE_PRIMITIVES):
                log.debug(
                    "ISC: dropping non-primitive remote state %r from %s (%s)",
                    key, peer_id[:8], type(value).__name__,
                )
                continue
            local_key = f"isc.{peer_id}.{key}"
            self.state.set(local_key, value, source="isc")

    def _is_remote_command_allowed(self, device_id: str, command: str) -> bool:
        """Check a remote device command against the operator's allowlist.

        Patterns are globs matched against ``"<device_id>.<command>"``. An
        empty allowlist denies every remote command: passing the shared-key
        handshake authenticates a peer to the mesh but does not, on its own,
        grant it control of any device. The operator opts in per device or
        command (e.g. ``projector1.*`` or ``*.power_off``; ``*`` allows all).
        """
        if not self._allowed_remote_commands:
            return False
        target = f"{device_id}.{command}"
        return any(fnmatch(target, pattern) for pattern in self._allowed_remote_commands)

    async def _handle_remote_command(self, peer_id: str, msg: dict[str, Any]) -> None:
        """Execute a device command requested by a remote peer."""
        request_id = msg.get("id", "")
        device_id = msg.get("device", "")
        command = msg.get("command", "")
        params = msg.get("params", {})

        conn = self._connections.get(peer_id)
        if conn is None:
            return

        if not self._is_remote_command_allowed(device_id, command):
            log.warning(
                "ISC: peer %s denied command %s.%s (not in allowed_remote_commands)",
                peer_id[:8], device_id, command,
            )
            await conn.send({
                "type": "isc.command_result",
                "id": request_id,
                "success": False,
                "error": "command not authorized by remote instance's ISC policy",
            })
            return

        try:
            result = await self.devices.send_command(device_id, command, params)
            await conn.send({
                "type": "isc.command_result",
                "id": request_id,
                "success": True,
                "result": result,
            })
        except Exception as e:
            await conn.send({
                "type": "isc.command_result",
                "id": request_id,
                "success": False,
                "error": str(e),
            })

    def _handle_command_result(self, msg: dict[str, Any]) -> None:
        """Resolve a pending remote-command future."""
        request_id = msg.get("id", "")
        future = self._pending_commands.pop(request_id, None)
        self._pending_command_peers.pop(request_id, None)
        if future is None or future.done():
            return
        if msg.get("success"):
            future.set_result(msg.get("result"))
        else:
            future.set_exception(RuntimeError(msg.get("error", "Remote command failed")))

    async def _handle_remote_event(self, peer_id: str, msg: dict[str, Any]) -> None:
        """Emit a remote event on the local EventBus."""
        event = msg.get("event", "")
        payload = msg.get("payload", {})
        if event:
            await self.events.emit(
                f"isc.{peer_id}.{event}",
                {"source_instance": peer_id, **payload},
            )

    # ------------------------------------------------------------------
    # State sharing
    # ------------------------------------------------------------------

    def _on_local_state_change(
        self, key: str, old_value: Any, new_value: Any, source: str,
    ) -> None:
        """Callback: local state changed — queue for peer broadcast."""
        if source == "isc":
            return  # Don't echo remote state back
        if key.startswith("isc."):
            return  # Don't share ISC namespace
        self._outgoing_batch[key] = new_value

    async def _state_batch_loop(self) -> None:
        """Periodically flush queued state changes to all connected peers."""
        try:
            while self._running:
                await asyncio.sleep(STATE_BATCH_INTERVAL)
                if self._outgoing_batch:
                    batch = dict(self._outgoing_batch)
                    self._outgoing_batch.clear()
                    msg = {"type": "isc.state", "changes": batch}
                    for peer_id, conn in list(self._connections.items()):
                        try:
                            await conn.send(msg)
                        except Exception:
                            log.debug(f"ISC: Failed to send state batch to {peer_id[:8]}")
        except asyncio.CancelledError:
            return

    def _get_shared_state(self) -> dict[str, Any]:
        """Return current values of all state keys matching shared patterns."""
        result: dict[str, Any] = {}
        snapshot = self.state.snapshot()
        for key, value in snapshot.items():
            if key.startswith("isc."):
                continue
            if self._is_shared_key(key):
                result[key] = value
        return result

    def _is_shared_key(self, key: str) -> bool:
        """Check if a key matches any shared state pattern."""
        return any(fnmatch(key, pattern) for pattern in self._shared_patterns)

    def _clear_isc_state(self) -> None:
        """Remove all isc.* keys from the state store."""
        snapshot = self.state.snapshot()
        for key in snapshot:
            if key.startswith("isc."):
                # delete() removes the key entirely; set(key, None) would leave
                # a ghost None-valued key in the snapshot every subscriber sees.
                self.state.delete(key, source="system")

    def _clear_peer_state(self, peer_id: str) -> None:
        """Delete the isc.<peer_id>.* keys a peer published into our store.

        Called when a peer disconnects or is pruned so stale shared state can't
        keep feeding UI bindings, macro skip_if guards, and trigger conditions
        long after the source is gone (a cross-room control firing on stale
        state). On reconnect the peer re-sends its full shared snapshot.
        """
        prefix = f"isc.{peer_id}."
        for key in [k for k in self.state.snapshot() if k.startswith(prefix)]:
            self.state.delete(key, source="isc")

    def _prune_stale_peers(self) -> None:
        """Drop discovered/inbound peers gone silent past BEACON_TTL.

        A live peer keeps last_seen fresh through beacons and messages even
        while its WS is briefly down, so only a genuinely-gone peer trips the
        TTL. Manual peers are operator-configured (not discovered) and keep
        their reconnect loop, so they're never pruned. Pruning also cancels a
        dangling reconnect task and clears the peer's stale shared state.
        """
        now = time()
        stale = [
            pid for pid, p in self._peers.items()
            if p.source != "manual"
            and not p.connected
            and now - p.last_seen > BEACON_TTL
        ]
        for pid in stale:
            task = self._connect_tasks.pop(pid, None)
            if task and not task.done():
                task.cancel()
            self._peers.pop(pid, None)
            self._clear_peer_state(pid)
        if stale:
            log.debug("ISC: pruned %d stale peer(s)", len(stale))
            self._push_isc_update()

    # ------------------------------------------------------------------
    # Keepalive
    # ------------------------------------------------------------------

    async def _ping_loop(self) -> None:
        """Send periodic pings to all connected peers."""
        try:
            while self._running:
                await asyncio.sleep(PING_INTERVAL)
                msg = {"type": "isc.ping"}
                for peer_id, conn in list(self._connections.items()):
                    try:
                        await conn.send(msg)
                    except Exception:
                        log.debug(f"ISC: Ping failed to {peer_id[:8]}")
        except asyncio.CancelledError:
            return

    # ------------------------------------------------------------------
    # Event bridge (scripts can emit isc.send_to / isc.broadcast)
    # ------------------------------------------------------------------

    async def _on_isc_send_to_event(self, event: str, payload: dict[str, Any]) -> None:
        instance_id = payload.get("instance_id", "")
        evt = payload.get("event", "")
        data = payload.get("payload", {})
        if instance_id and evt:
            try:
                await self.send_to(instance_id, evt, data)
            except Exception as e:
                log.warning(f"ISC send_to failed: {e}")

    async def _on_isc_broadcast_event(self, event: str, payload: dict[str, Any]) -> None:
        evt = payload.get("event", "")
        data = payload.get("payload", {})
        if evt:
            await self.broadcast(evt, data)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _close_peer(
        self,
        peer_id: str,
        emit: bool = True,
        conn: PeerConnection | None = None,
    ) -> None:
        """Close a peer connection and update tracking.

        If ``conn`` is provided, only pop the tracked entry if it is the
        same PeerConnection instance. This prevents an orphan socket's
        late disconnect from killing a legitimate fresh reconnection at
        the same peer_id (A55). The orphan socket itself is still closed.
        """
        existing = self._connections.get(peer_id)

        if conn is not None and existing is not conn:
            # Stale disconnect — the tracked connection has been replaced
            # by a newer one (or removed entirely). Close the orphan's
            # socket so its resources free, but don't touch the live entry.
            log.debug(
                f"ISC: Ignoring stale disconnect for {peer_id[:8]} "
                f"(connection replaced)"
            )
            try:
                await conn.close()
            except Exception:
                pass
            return

        self._connections.pop(peer_id, None)
        if existing is not None:
            await existing.close()

        # Clean up pending command futures for this peer
        stale_ids = [
            rid for rid, (future, pid) in self._pending_command_peers.items()
            if pid == peer_id and not future.done()
        ]
        for rid in stale_ids:
            self._pending_command_peers.pop(rid, None)
            future = self._pending_commands.pop(rid, None)
            if future and not future.done():
                future.set_exception(RuntimeError(f"Peer {peer_id} disconnected"))

        peer = self._peers.get(peer_id)
        if peer and peer.connected:
            peer.connected = False
            # Clear the peer's shared state so stale isc.<peer>.* values don't
            # keep driving bindings/triggers after the source is gone.
            self._clear_peer_state(peer_id)
            log.info(f"ISC: Peer «{peer.name}» ({peer_id[:8]}) disconnected")
            if emit:
                await self.events.emit("isc.peer_disconnected", {
                    "instance_id": peer_id,
                    "name": peer.name,
                })
                self._push_isc_update()

    def _push_isc_update(self) -> None:
        """Push ISC peer list to WebSocket clients (via state)."""
        self.state.set("system.isc.peer_count", len(self._peers), source="system")
        connected = sum(1 for p in self._peers.values() if p.connected)
        self.state.set("system.isc.connected_count", connected, source="system")

    def _get_ws(self, instance_id: str) -> PeerConnection | None:
        """Get a peer's WebSocket connection."""
        return self._connections.get(instance_id)

    def get_connection(self, peer_id: str) -> PeerConnection | None:
        """Return the live PeerConnection for ``peer_id`` or None.

        Public so isc_ws.py can hand the exact instance back to
        ``peer_disconnected`` for identity-checked cleanup (see A55).
        """
        return self._connections.get(peer_id)


# ---------------------------------------------------------------------------
# UDP discovery protocol (asyncio DatagramProtocol)
# ---------------------------------------------------------------------------

class _DiscoveryProtocol(asyncio.DatagramProtocol):
    """Receives UDP discovery beacons and forwards them to the ISCManager."""

    def __init__(self, isc_manager: ISCManager):
        self._isc = isc_manager

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self._isc._handle_beacon(data, addr)

    def error_received(self, exc: Exception) -> None:
        log.debug(f"ISC: UDP discovery error: {exc}")

    def connection_lost(self, exc: Exception | None) -> None:
        pass


# ---------------------------------------------------------------------------
# FastAPI WebSocket helper
# ---------------------------------------------------------------------------

async def _ws_send_fastapi(ws: Any, msg: dict[str, Any]) -> None:
    """Send a JSON message via a FastAPI WebSocket."""
    await ws.send_text(json.dumps(msg))
