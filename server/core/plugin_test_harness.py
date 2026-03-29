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
    """Minimal macro engine for testing — records execute calls."""

    def __init__(self):
        self.executed: list[str] = []

    async def execute(self, macro_id: str, context: dict | None = None) -> None:
        self.executed.append(macro_id)


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
