"""
Test harness for plugin developers.

Provides a lightweight test environment with real StateStore and EventBus
instances but no actual devices, network, or filesystem access. Includes
helpers for simulating state changes, emitting events, and verifying
plugin behavior.
"""

from typing import Any

from server.core.event_bus import EventBus
from server.core.plugin_api import PluginAPI
from server.core.plugin_registry import PluginRegistry
from server.core.state_store import StateStore


class MockMacroEngine:
    """Minimal macro engine for testing — records execute calls.

    Also covers the plugin-action registration surface PluginLoader uses
    during start/stop, so the mock can stand in for the real engine when
    wired into a real loader.
    """

    def __init__(self):
        self.executed: list[str] = []
        self.registered_actions: list[tuple[str, str]] = []

    async def execute(self, macro_id: str, context: dict | None = None) -> None:
        self.executed.append(macro_id)

    def register_plugin_action(
        self, action_type: str, handler, plugin_id: str, label: str = ""
    ) -> None:
        self.registered_actions.append((plugin_id, action_type))

    def unregister_plugin_actions(self, plugin_id: str) -> None:
        self.registered_actions = [
            (pid, action) for pid, action in self.registered_actions
            if pid != plugin_id
        ]


class MockDeviceManager:
    """Minimal device manager for testing — records command calls."""

    def __init__(self):
        self.commands: list[tuple[str, str, dict | None]] = []

    async def send_command(
        self, device_id: str, command: str, params: dict | None = None
    ) -> Any:
        self.commands.append((device_id, command, params))
        return {"status": "ok"}


class PluginTestHarness:
    """
    Test environment for plugin developers.

    Provides real StateStore and EventBus instances with helpers for
    simulating and verifying plugin behavior.

    Usage:
        harness = PluginTestHarness()
        plugin = MyPlugin()
        await harness.start_plugin(plugin)
        await harness.state_set("device.projector.power", "on")
        assert await harness.state_get("plugin.my_plugin.status") == "running"
        await harness.stop_plugin(plugin)
    """

    def __init__(self):
        self.state = StateStore()
        self.events = EventBus()
        self.macros = MockMacroEngine()
        self.devices = MockDeviceManager()

        # Wire state -> events
        self.state.set_event_bus(self.events)

        # Track log messages
        self._logs: list[dict[str, str]] = []

        # Results returned by the stubbed api.mdns_browse (tests never hit
        # the real network). Set this before/after start_plugin as needed.
        self.mdns_results: list[dict] = []

        # Active plugins
        self._registries: dict[str, PluginRegistry] = {}
        self._apis: dict[str, PluginAPI] = {}

    async def start_plugin(
        self,
        plugin,
        config: dict | None = None,
        capabilities: list[str] | None = None,
    ) -> PluginAPI:
        """Start a plugin with the test harness.

        Args:
            plugin: Plugin instance (must have PLUGIN_INFO and start()).
            config: Override config (defaults to empty dict).
            capabilities: Override capabilities (defaults from PLUGIN_INFO).

        Returns:
            The PluginAPI instance (for direct testing if needed).
        """
        info = plugin.PLUGIN_INFO
        plugin_id = info["id"]

        if capabilities is None:
            capabilities = info.get("capabilities", [])

        registry = PluginRegistry(plugin_id)
        api = PluginAPI(
            plugin_id=plugin_id,
            capabilities=capabilities,
            config=config or {},
            registry=registry,
            state_store=self.state,
            event_bus=self.events,
            macro_engine=self.macros,
            device_manager=self.devices,
            platform_id="test",
            save_config_fn=None,
            log_fn=self._log_fn,
        )

        # Stub mDNS browse so harness runs never touch the network. The
        # capability gate still applies, matching the real PluginAPI.
        async def _stub_mdns_browse(
            service_types: list[str], duration: float = 5.0
        ) -> list[dict]:
            api._require("network_listen")
            _ = (service_types, duration)
            return [dict(r) for r in self.mdns_results]

        api.mdns_browse = _stub_mdns_browse

        self._registries[plugin_id] = registry
        self._apis[plugin_id] = api

        await plugin.start(api)
        return api

    async def stop_plugin(self, plugin) -> None:
        """Stop a plugin and run cleanup."""
        plugin_id = plugin.PLUGIN_INFO["id"]

        await plugin.stop()

        registry = self._registries.pop(plugin_id, None)
        if registry:
            await registry.cleanup(self.state, self.events)
        self._apis.pop(plugin_id, None)

    async def apply_config(self, plugin, new_config: dict) -> bool:
        """Hot-apply new config the way the platform loader does.

        Swaps the live ``api.config`` and awaits the plugin's
        ``on_config_changed`` hook. Returns True when the plugin handled the
        change (the platform would skip the restart). Returns False when the
        plugin doesn't define the hook or declined.
        """
        plugin_id = plugin.PLUGIN_INFO["id"]
        api = self._apis.get(plugin_id)
        hook = getattr(plugin, "on_config_changed", None)
        if api is None or not callable(hook):
            return False
        api._update_config(new_config)
        return await hook(dict(new_config)) is True

    # ──── State Helpers ────

    async def state_set(self, key: str, value: Any, source: str = "test") -> None:
        """Set a state key (simulates external state change)."""
        self.state.set(key, value, source=source)

    async def state_get(self, key: str) -> Any:
        """Get a state key value."""
        return self.state.get(key)

    async def state_get_pattern(self, pattern: str) -> dict[str, Any]:
        """Get all state keys matching a glob pattern."""
        return self.state.get_matching(pattern)

    # ──── Event Helpers ────

    async def emit_event(self, event: str, payload: dict | None = None) -> None:
        """Emit an event (simulates external event)."""
        await self.events.emit(event, payload)

    # ──── Verification Helpers ────

    def log_contains(self, substring: str) -> bool:
        """Check if any log message contains the given substring."""
        return any(substring in entry["message"] for entry in self._logs)

    def get_logs(self, plugin_id: str | None = None) -> list[dict[str, str]]:
        """Get log entries, optionally filtered by plugin ID."""
        if plugin_id is None:
            return list(self._logs)
        return [e for e in self._logs if e["plugin_id"] == plugin_id]

    def get_executed_macros(self) -> list[str]:
        """Get list of macro IDs that were executed."""
        return list(self.macros.executed)

    def get_device_commands(self) -> list[tuple[str, str, dict | None]]:
        """Get list of (device_id, command, params) tuples sent."""
        return list(self.devices.commands)

    # ──── Internal ────

    def _log_fn(self, plugin_id: str, message: str, level: str = "info") -> None:
        self._logs.append({
            "plugin_id": plugin_id,
            "message": message,
            "level": level,
        })
