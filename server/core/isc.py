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
import json
import socket
import uuid
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path
from time import time
from typing import Any, TYPE_CHECKING

from server.utils.logger import get_logger

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

# Keepalive
PING_INTERVAL = 30.0
PING_TIMEOUT = 10.0

# State batch window (seconds)
STATE_BATCH_INTERVAL = 0.2


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
    """Information about a discovered or configured peer instance."""
    instance_id: str
    name: str
    host: str
    port: int
    version: str = ""
    connected: bool = False
    discovered_at: float = field(default_factory=time)
    last_seen: float = field(default_factory=time)
    source: str = "mdns"  # "mdns", "manual", "inbound"


class PeerConnection:
    """Normalised wrapper around a peer WebSocket (inbound or outbound)."""

    def __init__(self, ws: Any, direction: str):
        self._ws = ws
        self.direction = direction  # "inbound" | "outbound"
        self._closed = False

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
    1. Registers this instance on the LAN via mDNS (if zeroconf is available)
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
    ):
        self.state = state
        self.events = events
        self.devices = devices
        self._shared_patterns = list(shared_state_patterns)
        self._auth_key = auth_key
        self.instance_id = instance_id
        self.instance_name = instance_name
        self.http_port = http_port
        self._manual_peers = manual_peers or []

        # Peer tracking
        self._peers: dict[str, PeerInfo] = {}
        self._connections: dict[str, PeerConnection] = {}

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

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start ISC: register mDNS, begin discovery, connect to manual peers."""
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
    ) -> None:
        """Hot-reload ISC config without full restart."""
        self._shared_patterns = list(shared_state_patterns)
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

        for addr in new_set - old_set:
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

    async def send_to(
        self, instance_id: str, event: str, payload: dict[str, Any] | None = None,
    ) -> None:
        """Send an event to a specific peer instance."""
        conn = self._connections.get(instance_id)
        if conn is None:
            raise ConnectionError(f"Not connected to instance '{instance_id}'")
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
        conn = self._connections.get(instance_id)
        if conn is None:
            raise ConnectionError(f"Not connected to instance '{instance_id}'")

        request_id = str(uuid.uuid4())
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        self._pending_commands[request_id] = future
        self._pending_command_peers[request_id] = (future, instance_id)

        await conn.send({
            "type": "isc.command",
            "id": request_id,
            "device": device_id,
            "command": command,
            "params": params or {},
        })

        try:
            return await asyncio.wait_for(future, timeout=10.0)
        except asyncio.TimeoutError:
            self._pending_commands.pop(request_id, None)
            self._pending_command_peers.pop(request_id, None)
            raise TimeoutError(
                f"Command to {instance_id[:8]}:{device_id}.{command} timed out"
            )

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

    async def accept_inbound(self, ws: Any, hello: dict[str, Any]) -> str | None:
        """
        Validate an inbound hello message and register the peer.

        Returns the peer's ``instance_id`` on success, or ``None`` if rejected
        (the WebSocket is closed with a reject message in that case).
        """
        peer_id = hello.get("instance_id", "")
        peer_name = hello.get("name", "")
        peer_auth = hello.get("auth_key", "")
        peer_version = hello.get("version", "")

        if not peer_id:
            await _ws_send_fastapi(ws, {"type": "isc.reject", "reason": "missing instance_id"})
            return None

        if not self._auth_key:
            await _ws_send_fastapi(ws, {"type": "isc.reject", "reason": "auth_not_configured"})
            log.warning(f"ISC: Rejected {peer_id[:8]} — no auth key configured")
            return None

        if peer_auth != self._auth_key:
            await _ws_send_fastapi(ws, {"type": "isc.reject", "reason": "auth_key_mismatch"})
            log.warning(f"ISC: Rejected {peer_id[:8]} — auth key mismatch")
            return None

        # Duplicate check — if we already have an outbound to this peer,
        # the instance with the smaller ID keeps its outbound.
        if peer_id in self._connections:
            if self.instance_id < peer_id:
                await _ws_send_fastapi(ws, {"type": "isc.reject", "reason": "duplicate"})
                return None
            else:
                await self._close_peer(peer_id, emit=False)

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
        self._connections[peer_id] = PeerConnection(ws, "inbound")

        # Send welcome
        await _ws_send_fastapi(ws, {
            "type": "isc.welcome",
            "instance_id": self.instance_id,
            "name": self.instance_name,
            "version": "0.1.0",
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

    async def peer_disconnected(self, peer_id: str) -> None:
        """Called when a peer connection drops (either direction)."""
        await self._close_peer(peer_id, emit=True)

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
        """Build a discovery beacon packet: magic header + JSON payload."""
        payload = json.dumps({
            "instance_id": self.instance_id,
            "name": self.instance_name,
            "port": self.http_port,
            "version": "0.1.0",
            "protocol": ISC_PROTOCOL_VERSION,
        }).encode()
        return DISCOVERY_MAGIC + payload

    def _handle_beacon(self, data: bytes, addr: tuple[str, int]) -> None:
        """Process an incoming discovery beacon from the network."""
        if not data.startswith(DISCOVERY_MAGIC):
            return

        # Rate limit beacon processing: max 1 per source IP per 5 seconds
        now = time()
        source_ip = addr[0]
        if not hasattr(self, "_beacon_rate"):
            self._beacon_rate: dict[str, float] = {}
        last_seen = self._beacon_rate.get(source_ip, 0)
        if now - last_seen < 5.0:
            return
        self._beacon_rate[source_ip] = now

        # Validate source is a private/local IP (not spoofed from internet)
        if not (source_ip.startswith("10.") or source_ip.startswith("192.168.") or
                source_ip.startswith("172.") or source_ip.startswith("127.") or
                source_ip.startswith("169.254.")):
            return

        try:
            payload = json.loads(data[len(DISCOVERY_MAGIC):])
        except (json.JSONDecodeError, UnicodeDecodeError):
            return

        peer_id = payload.get("instance_id", "")
        if not peer_id or peer_id == self.instance_id:
            return  # Skip self

        peer_name = payload.get("name", "")
        peer_port = payload.get("port", 8080)
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

        log.info(f"ISC: Discovered «{peer_name}» ({peer_id[:8]}) at {host}:{peer_port}")

        self._peers[peer_id] = PeerInfo(
            instance_id=peer_id, name=peer_name, host=host, port=peer_port,
            version=payload.get("version", ""), source="discovered",
        )
        self._push_isc_update()

        # Connect if not already connected
        if peer_id not in self._connections:
            self._schedule_connect(peer_id, host, peer_port)

    # ------------------------------------------------------------------
    # Outbound connections
    # ------------------------------------------------------------------

    def _schedule_connect(self, peer_id: str, host: str, port: int) -> None:
        """Schedule a background task to connect to a peer."""
        if peer_id in self._connect_tasks:
            return
        task = asyncio.create_task(self._outbound_loop(peer_id, host, port))
        self._connect_tasks[peer_id] = task

    async def _outbound_loop(self, peer_id: str, host: str, port: int) -> None:
        """Maintain an outbound connection to a peer with reconnection."""
        attempt = 0
        while self._running:
            try:
                await self._outbound_connect(peer_id, host, port)
                attempt = 0  # Reset on successful connection
            except asyncio.CancelledError:
                return
            except Exception as e:
                log.debug(f"ISC: Outbound to {peer_id[:8]} failed: {e}")

            if not self._running:
                return

            # Back off
            idx = min(attempt, len(RECONNECT_DELAYS) - 1)
            delay = RECONNECT_DELAYS[idx]
            attempt += 1
            log.debug(f"ISC: Reconnecting to {peer_id[:8]} in {delay}s")
            await asyncio.sleep(delay)

    async def _outbound_connect(self, peer_id: str, host: str, port: int) -> None:
        """Connect to a peer, authenticate, and run the message loop."""
        import websockets

        url = f"ws://{host}:{port}{ISC_WS_PATH}"
        log.debug(f"ISC: Connecting to {url}")

        async with websockets.connect(url, close_timeout=5) as ws:
            # Send hello
            await ws.send(json.dumps({
                "type": "isc.hello",
                "instance_id": self.instance_id,
                "name": self.instance_name,
                "auth_key": self._auth_key,
                "version": "0.1.0",
                "protocol": ISC_PROTOCOL_VERSION,
            }))

            # Wait for welcome or reject
            resp_text = await asyncio.wait_for(ws.recv(), timeout=10)
            resp = json.loads(resp_text)

            if resp.get("type") == "isc.reject":
                reason = resp.get("reason", "unknown")
                log.warning(f"ISC: Peer {peer_id[:8]} rejected: {reason}")
                if reason == "duplicate":
                    # They already have a connection to us — stop retrying
                    return
                raise ConnectionRefusedError(reason)

            if resp.get("type") != "isc.welcome":
                raise ConnectionError(f"Unexpected response: {resp.get('type')}")

            # Update peer info from welcome
            real_id = resp.get("instance_id", peer_id)
            real_name = resp.get("name", "")
            real_version = resp.get("version", "")

            # If the peer_id was a temporary key (manual peer), remap
            async with self._peer_lock:
                if peer_id != real_id:
                    self._peers.pop(peer_id, None)
                    if peer_id in self._connections:
                        self._connections.pop(peer_id)
                    peer_id = real_id

                peer = self._peers.get(peer_id)
                if peer is None:
                    peer = PeerInfo(
                        instance_id=peer_id, name=real_name, host=host, port=port,
                        source="mdns",
                    )
                    self._peers[peer_id] = peer
                peer.connected = True
                peer.name = real_name or peer.name
                peer.version = real_version
                peer.last_seen = time()

                conn = PeerConnection(ws, "outbound")
                self._connections[peer_id] = conn

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

        # Connection closed normally
        await self._close_peer(peer_id, emit=True)

    # ------------------------------------------------------------------
    # Message handlers
    # ------------------------------------------------------------------

    def _apply_remote_state(self, peer_id: str, changes: dict[str, Any]) -> None:
        """Write remote state changes into the local StateStore."""
        for key, value in changes.items():
            local_key = f"isc.{peer_id}.{key}"
            self.state.set(local_key, value, source="isc")

    async def _handle_remote_command(self, peer_id: str, msg: dict[str, Any]) -> None:
        """Execute a device command requested by a remote peer."""
        request_id = msg.get("id", "")
        device_id = msg.get("device", "")
        command = msg.get("command", "")
        params = msg.get("params", {})

        conn = self._connections.get(peer_id)
        if conn is None:
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
                self.state.set(key, None, source="system")

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

    async def _close_peer(self, peer_id: str, emit: bool = True) -> None:
        """Close a peer connection and update tracking."""
        conn = self._connections.pop(peer_id, None)
        if conn:
            await conn.close()

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
