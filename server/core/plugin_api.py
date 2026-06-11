"""
Scoped Plugin API — the plugin's only interface to the OpenAVC runtime.

Each plugin instance receives its own PluginAPI with:
- Capability enforcement (undeclared capabilities raise PluginPermissionError)
- Namespace isolation (state writes restricted to plugin.<id>.*)
- Event auto-prefixing (emitted events prefixed with plugin.<id>.)
- Automatic registration tracking for cleanup
"""

import asyncio
import uuid
from pathlib import Path
from typing import Any, Callable, Coroutine

from server.utils.logger import get_logger
from server.utils.net_safety import assert_safe_outbound_url

log = get_logger(__name__)

# Sentinel for "key absent" so variable_set can tell a missing key apart from
# one explicitly set to None.
_MISSING = object()

# Hop-by-hop headers that must not be forwarded across a proxy boundary
# (RFC 7230 §6.1). Stripped in both directions by PluginAPI.proxy_to.
_HOP_BY_HOP = frozenset({
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
})


class PluginPermissionError(Exception):
    """Raised when a plugin attempts an action it hasn't declared."""


class PluginAPI:
    """
    Scoped API provided to each plugin instance.

    Method calls are gated by declared capabilities.
    All registrations (subscriptions, state keys, tasks) are tracked
    and automatically cleaned up on stop/uninstall.
    """

    def __init__(
        self,
        plugin_id: str,
        capabilities: list[str],
        config: dict[str, Any],
        registry,  # PluginRegistry
        state_store,  # StateStore
        event_bus,  # EventBus
        macro_engine,  # MacroEngine
        device_manager,  # DeviceManager
        platform_id: str,
        save_config_fn: Callable | None = None,
        log_fn: Callable | None = None,
        failure_reporter: Callable | None = None,
        success_reporter: Callable | None = None,
    ):
        self._plugin_id = plugin_id
        self._capabilities = set(capabilities)
        self._config = dict(config)
        self._registry = registry
        self._state = state_store
        self._events = event_bus
        self._macros = macro_engine
        self._devices = device_manager
        self._platform_id = platform_id
        self._save_config_fn = save_config_fn
        self._log_fn = log_fn
        self._failure_reporter = failure_reporter
        self._success_reporter = success_reporter
        self._periodic_tasks: dict[str, asyncio.Task] = {}
        self._data_dir: Path | None = None

    def _require(self, capability: str) -> None:
        if capability not in self._capabilities:
            raise PluginPermissionError(
                f"Plugin '{self._plugin_id}' requires capability '{capability}' "
                f"but only declared: {sorted(self._capabilities)}"
            )

    # ──── State ────

    async def state_get(self, key: str) -> Any:
        """Read any state key. Requires: state_read."""
        self._require("state_read")
        return self._state.get(key)

    async def state_get_pattern(self, pattern: str) -> dict[str, Any]:
        """Read all state keys matching a glob pattern. Requires: state_read."""
        self._require("state_read")
        return self._state.get_matching(pattern)

    async def state_set(self, key: str, value: Any) -> None:
        """Set a state key. Requires: state_write.

        Plugins can ONLY set keys in: plugin.<plugin_id>.*
        Values must be flat primitives (str, int, float, bool, None).
        """
        self._require("state_write")

        # Namespace enforcement — auto-prefix if bare key given
        prefix = f"plugin.{self._plugin_id}."
        if not key.startswith(prefix):
            key = f"{prefix}{key}"

        # Flat primitive enforcement
        if value is not None and not isinstance(value, (str, int, float, bool)):
            raise PluginPermissionError(
                f"Plugin state values must be flat primitives, got {type(value).__name__}"
            )

        self._state.set(key, value, source=f"plugin.{self._plugin_id}")
        self._registry.track_state_key(key)

    async def variable_set(self, variable_id: str, value: Any) -> None:
        """Set a user-defined variable value. Requires: variable_write.

        Writes to var.<variable_id> in the state store. User variables are
        shared room-logic state, so writing to them is gated by a separate
        capability from plugin-namespace state_write.

        A var.* key the plugin *creates* (one that doesn't already exist —
        declared user variables are seeded at startup before plugins run) is
        tracked so it's removed on stop/uninstall. Writing to a pre-existing /
        declared user variable does not track it, so a shared variable is never
        deleted out from under the project.
        """
        self._require("variable_write")
        if value is not None and not isinstance(value, (str, int, float, bool)):
            raise PluginPermissionError(
                f"Variable values must be flat primitives, got {type(value).__name__}"
            )
        key = f"var.{variable_id}"
        created = self._state.get(key, _MISSING) is _MISSING
        self._state.set(key, value, source=f"plugin.{self._plugin_id}")
        if created:
            self._registry.track_variable_key(key)

    async def state_subscribe(self, pattern: str, callback: Callable) -> str:
        """Subscribe to state changes matching a glob pattern. Requires: state_read.

        Callback: async (key: str, value: Any, old_value: Any) -> None
        Returns subscription ID. Automatically unsubscribed on stop.
        """
        self._require("state_read")

        # Wrap to match internal StateStore signature (key, old_value, new_value, source)
        # and provide the plugin-facing signature (key, value, old_value)
        failure_reporter = self._failure_reporter
        success_reporter = self._success_reporter

        async def _wrapper(key: str, old_value: Any, new_value: Any, source: str) -> None:
            try:
                result = callback(key, new_value, old_value)
                if asyncio.iscoroutine(result):
                    await result
                if success_reporter:
                    success_reporter()
            except Exception:  # Catch-all: isolates plugin callback errors from engine
                log.exception(
                    f"Plugin '{self._plugin_id}' state callback error for key '{key}'"
                )
                if failure_reporter:
                    failure_reporter()

        sub_id = self._state.subscribe(pattern, _wrapper)
        self._registry.track_state_subscription(sub_id)
        return sub_id

    async def state_unsubscribe(self, subscription_id: str) -> None:
        """Remove a state subscription."""
        self._state.unsubscribe(subscription_id)
        try:
            self._registry.state_subscriptions.remove(subscription_id)
        except ValueError:
            pass

    # ──── Events ────

    async def event_emit(self, event_name: str, payload: dict | None = None) -> None:
        """Emit an event. Requires: event_emit.

        Auto-prefixed: plugin.<plugin_id>.<event_name>
        """
        self._require("event_emit")
        full_event = f"plugin.{self._plugin_id}.{event_name}"
        await self._events.emit(full_event, payload)

    async def event_subscribe(self, pattern: str, callback: Callable) -> str:
        """Subscribe to events matching a glob. Requires: event_subscribe.

        Callback: async (event_name: str, payload: dict) -> None
        Can subscribe to ANY event (not just plugin events).
        Automatically unsubscribed on stop.
        """
        self._require("event_subscribe")

        failure_reporter = self._failure_reporter
        success_reporter = self._success_reporter

        async def _wrapper(event_name: str, payload: dict[str, Any] | None) -> None:
            try:
                result = callback(event_name, payload or {})
                if asyncio.iscoroutine(result):
                    await result
                if success_reporter:
                    success_reporter()
            except Exception:  # Catch-all: isolates plugin callback errors from engine
                log.exception(
                    f"Plugin '{self._plugin_id}' event callback error for '{event_name}'"
                )
                if failure_reporter:
                    failure_reporter()

        handler_id = self._events.on(pattern, _wrapper)
        self._registry.track_event_subscription(handler_id)
        return handler_id

    async def event_unsubscribe(self, subscription_id: str) -> None:
        """Remove an event subscription."""
        self._events.off(subscription_id)
        try:
            self._registry.event_subscriptions.remove(subscription_id)
        except ValueError:
            pass

    # ──── Actions ────

    async def macro_execute(self, macro_id: str) -> None:
        """Execute a macro by ID. Requires: macro_execute."""
        self._require("macro_execute")
        await self._macros.execute(macro_id)

    async def device_command(
        self, device_id: str, command: str, params: dict | None = None
    ) -> Any:
        """Send a command to a device. Requires: device_command."""
        self._require("device_command")
        return await self._devices.send_command(device_id, command, params)

    # ──── Network Discovery ────

    async def mdns_browse(
        self, service_types: list[str], duration: float = 5.0
    ) -> list[dict]:
        """Browse the local network for mDNS/DNS-SD services. Requires: network_listen.

        Sends PTR queries for the given service types (e.g.
        ``["_elg._tcp.local."]``) and listens for ``duration`` seconds.
        Returns a list of dicts, one per responding host::

            {
                "ip": "192.168.1.40",
                "port": 5343,                  # from the SRV record, may be None
                "hostname": "device.local.",   # may be None
                "instance_name": "My Device",  # may be None
                "service_type": "_elg._tcp.local.",
                "txt": {"sn": "ABC123", ...},  # TXT records
            }

        Uses the platform's stdlib mDNS listener (one shared implementation
        for discovery, advertising, and plugins). Multicast does not cross
        Docker bridge networks, NAT, or VLANs — callers should always offer
        a manual fallback alongside browse results.
        """
        self._require("network_listen")
        from server.discovery.mdns_scanner import MDNSScanner

        if not service_types or not all(
            isinstance(s, str) and s.strip() for s in service_types
        ):
            raise ValueError("service_types must be a non-empty list of strings")
        duration = max(1.0, min(float(duration), 60.0))
        scanner = MDNSScanner(service_types=list(service_types))
        results = await scanner.start(duration=duration)
        out = []
        for r in results.values():
            out.append({
                "ip": r.ip,
                "port": r.port,
                "hostname": r.hostname,
                "instance_name": r.instance_name,
                "service_type": r.service_type,
                "txt": dict(r.txt_records),
            })
        out.sort(key=lambda d: d["ip"])
        return out

    # ──── HTTP Endpoints ────

    def register_router(self, router) -> None:
        """Mount a FastAPI ``APIRouter`` under ``/api/plugins/<id>/ext/*``.

        Requires: http_endpoints.

        Call this from ``start()``. The engine mounts the router after the
        plugin starts and removes it on stop. Routes are reachable from the
        authenticated Programmer IDE (HTTP Basic / X-API-Key) and from this
        plugin's own sandboxed panel iframe. The iframe cannot attach
        programmer credentials, so the platform mints a plugin-scoped token,
        injects it into the iframe as ``openavc:init.ext_token``, and the
        iframe presents it in the ``X-OpenAVC-Plugin-Token`` header. Both
        paths are accepted automatically; the plugin author writes ordinary
        route handlers.

        Define routes relative to the mount point — a handler decorated
        ``@router.post("/whep/{stream_id}")`` is served at
        ``/api/plugins/<id>/ext/whep/{stream_id}``.
        """
        self._require("http_endpoints")
        from fastapi import APIRouter

        if not isinstance(router, APIRouter):
            raise TypeError(
                "register_router expects a fastapi.APIRouter, "
                f"got {type(router).__name__}"
            )
        self._registry.http_router = router

    async def proxy_to(
        self, url: str, request, *, timeout: float = 30.0, allow_internal: bool = False
    ):
        """Proxy an incoming request to ``url`` and return the upstream response.

        Requires: http_endpoints.

        Buffered pass-through (httpx) sized for small request/response bodies
        such as WHEP SDP signaling — forwards the method, query string, body,
        and headers (minus host / hop-by-hop). Not for streaming media.

        SSRF guard: the upstream host must be public and the scheme http(s).
        Loopback, RFC1918, link-local (incl. cloud metadata 169.254.169.254),
        and other reserved address space are refused. A plugin proxying to its
        own localhost sidecar must pass ``allow_internal=True`` (an explicit,
        auditable opt-in) — a ``ValueError`` is raised otherwise.
        """
        self._require("http_endpoints")
        import httpx
        from starlette.responses import Response

        await assert_safe_outbound_url(url, allow_internal=allow_internal)

        body = await request.body()
        fwd_headers = {
            k: v
            for k, v in request.headers.items()
            if k.lower() not in _HOP_BY_HOP and k.lower() not in ("host", "content-length")
        }
        async with httpx.AsyncClient(timeout=timeout) as client:
            upstream = await client.request(
                request.method,
                url,
                content=body,
                headers=fwd_headers,
                params=dict(request.query_params),
            )
        resp_headers = {
            k: v
            for k, v in upstream.headers.items()
            if k.lower() not in _HOP_BY_HOP
            and k.lower() not in ("content-length", "content-encoding")
        }
        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            headers=resp_headers,
        )

    # ──── Background Tasks ────

    def create_task(self, coro: Coroutine, name: str | None = None) -> asyncio.Task:
        """Create a managed background task. Automatically cancelled on stop."""
        task_name = f"plugin.{self._plugin_id}.{name or 'task'}"

        async def _safe_wrapper():
            try:
                await coro
            except asyncio.CancelledError:
                raise
            except Exception:  # Catch-all: isolates plugin task errors from engine
                log.exception(f"Plugin '{self._plugin_id}' task '{task_name}' failed")

        task = asyncio.create_task(_safe_wrapper(), name=task_name)
        self._registry.track_task(task)

        def _on_done(t: asyncio.Task):
            self._registry.untrack_task(t)

        task.add_done_callback(_on_done)
        return task

    def create_periodic_task(
        self, coro_fn: Callable, interval_seconds: float, name: str | None = None
    ) -> str:
        """Create a repeating background task. Calls coro_fn() every interval.

        Automatically cancelled on stop. Returns task ID.
        """
        task_id = f"periodic_{uuid.uuid4().hex[:8]}"

        async def _periodic_loop():
            while True:
                try:
                    result = coro_fn()
                    if asyncio.iscoroutine(result):
                        await result
                except asyncio.CancelledError:
                    raise
                except Exception:  # Catch-all: isolates plugin periodic task errors
                    log.exception(
                        f"Plugin '{self._plugin_id}' periodic task '{name or task_id}' error"
                    )
                await asyncio.sleep(interval_seconds)

        task = asyncio.create_task(
            _periodic_loop(),
            name=f"plugin.{self._plugin_id}.{name or task_id}",
        )
        self._registry.track_task(task)
        self._registry.track_periodic_task(task_id)
        self._periodic_tasks[task_id] = task

        def _on_done(t: asyncio.Task):
            self._registry.untrack_task(t)
            self._periodic_tasks.pop(task_id, None)

        task.add_done_callback(_on_done)
        return task_id

    def cancel_task(self, task_id: str) -> None:
        """Cancel a managed periodic task by ID."""
        task = self._periodic_tasks.pop(task_id, None)
        if task and not task.done():
            task.cancel()

    def _cancel_all_tasks(self) -> None:
        """Cancel every periodic task this API created (loader stop path).

        Defense in depth alongside the registry: a periodic task that keeps
        firing after its plugin stops executes actions from beyond the grave
        (a leaked hold-repeat once drove a volume macro 4x/second for over
        half an hour). The loader also reaps by task name as the final
        backstop.
        """
        for task in self._periodic_tasks.values():
            if not task.done():
                task.cancel()
        self._periodic_tasks.clear()

    # ──── Configuration ────

    @property
    def config(self) -> dict:
        """This plugin's saved configuration (from the project file). Read-only."""
        return dict(self._config)

    def _update_config(self, new_config: dict) -> None:
        """Swap the live config (loader-internal, used by hot config apply).

        Called by the plugin loader right before invoking a plugin's
        ``on_config_changed`` hook, so ``api.config`` already reflects the
        new values when the hook runs.
        """
        self._config = dict(new_config)

    async def save_config(self, config: dict) -> None:
        """Save updated configuration to the project file.

        ``config`` must be a JSON-serializable dict (the project file is JSON).
        A non-serializable or self-referential value is rejected here, before
        it is written into the engine's in-memory project — otherwise it would
        poison the shared project model and block every subsequent save for the
        rest of the session. On a save failure the in-memory config is reverted
        so ``config`` / ``get_running_config`` never report an unpersisted value
        as saved.
        """
        if not isinstance(config, dict):
            raise PluginPermissionError(
                f"Plugin config must be a dict, got {type(config).__name__}"
            )
        import json
        try:
            json.dumps(config)
        except (TypeError, ValueError, RecursionError) as e:
            raise PluginPermissionError(
                f"Plugin config must be JSON-serializable: {e}"
            )
        previous = self._config
        self._config = dict(config)
        if self._save_config_fn:
            try:
                await self._save_config_fn(self._plugin_id, config)
            except Exception:
                self._config = previous
                raise

    # ──── Identity & Logging ────

    @property
    def plugin_id(self) -> str:
        """This plugin's unique ID."""
        return self._plugin_id

    @property
    def platform(self) -> str:
        """Current platform identifier (win_x64, linux_x64, linux_arm64, etc.)."""
        return self._platform_id

    @property
    def data_dir(self) -> Path:
        """Per-plugin persistent data directory. Created on first access.

        Use for sidecar binaries, downloaded models, cached state, certs,
        recorded segments — anything that should outlive plugin updates and
        (by default) plugin uninstall. The user can opt to discard this
        directory at uninstall time; otherwise it persists across
        install/update/reinstall cycles so the plugin doesn't need to
        re-download large assets.

        Distinct from the plugin's code directory (managed by the installer)
        and from `config` (per-project, lives in the .avc project file).
        """
        if self._data_dir is None:
            from server.system_config import PLUGIN_DATA_DIR
            path = PLUGIN_DATA_DIR / self._plugin_id
            path.mkdir(parents=True, exist_ok=True)
            self._data_dir = path
        return self._data_dir

    _VALID_LOG_LEVELS = {"debug", "info", "warning", "error", "critical"}

    def log(self, message: str, level: str = "info") -> None:
        """Log a message. Appears in System Log with plugin name as source."""
        if level not in self._VALID_LOG_LEVELS:
            level = "info"
        if self._log_fn:
            self._log_fn(self._plugin_id, message, level)
        else:
            getattr(log, level, log.info)(f"[Plugin:{self._plugin_id}] {message}")
