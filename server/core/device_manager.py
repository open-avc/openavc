"""
OpenAVC DeviceManager — manages all device driver instances.

Handles:
- Instantiating drivers from project config
- Connection lifecycle (connect, reconnect on failure, disconnect)
- Routing commands to the correct device
- Exposing device metadata
"""

from __future__ import annotations

import asyncio
from typing import Any, TYPE_CHECKING

from server.core.event_bus import EventBus
from server.core.state_store import StateStore
from server.utils.logger import get_logger

log = get_logger(__name__)


def _log_task_exception(task: asyncio.Task) -> None:
    """Log unhandled exceptions from fire-and-forget tasks."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        log.error("Unhandled exception in background task: %s", exc, exc_info=exc)


if TYPE_CHECKING:
    from server.drivers.base import BaseDriver

# Driver registry — maps driver ID strings to driver classes
_DRIVER_REGISTRY: dict[str, type[BaseDriver]] = {}


def register_driver(driver_class: type[BaseDriver]) -> None:
    """Register a driver class in the global registry."""
    driver_id = driver_class.DRIVER_INFO.get("id", "")
    if driver_id:
        _DRIVER_REGISTRY[driver_id] = driver_class
        log.debug(f"Registered driver: {driver_id}")


def unregister_driver(driver_id: str) -> bool:
    """Remove a driver class from the global registry. Returns True if removed."""
    removed = _DRIVER_REGISTRY.pop(driver_id, None) is not None
    if removed:
        log.info(f"Unregistered driver: {driver_id}")
    return removed


def is_driver_registered(driver_id: str) -> bool:
    """Check if a driver ID is registered in the global registry."""
    return driver_id in _DRIVER_REGISTRY


def get_driver_registry() -> list[dict[str, Any]]:
    """Return metadata for all registered drivers."""
    return [
        {
            "id": driver_class.DRIVER_INFO.get("id", ""),
            "name": driver_class.DRIVER_INFO.get("name", ""),
            "manufacturer": driver_class.DRIVER_INFO.get("manufacturer", ""),
            "category": driver_class.DRIVER_INFO.get("category", ""),
            "description": driver_class.DRIVER_INFO.get("description", ""),
            "version": driver_class.DRIVER_INFO.get("version", ""),
            "author": driver_class.DRIVER_INFO.get("author", ""),
            "commands": driver_class.DRIVER_INFO.get("commands", {}),
            "config_schema": driver_class.DRIVER_INFO.get("config_schema", {}),
            "default_config": driver_class.DRIVER_INFO.get("default_config", {}),
            "state_variables": driver_class.DRIVER_INFO.get("state_variables", {}),
            "help": driver_class.DRIVER_INFO.get("help", {}),
            "discovery": driver_class.DRIVER_INFO.get("discovery", {}),
            "device_settings": driver_class.DRIVER_INFO.get("device_settings", {}),
        }
        for driver_class in _DRIVER_REGISTRY.values()
    ]


def _load_builtin_drivers() -> None:
    """Import and register all built-in and community drivers."""
    # GenericTCP is a built-in utility driver — always imported directly
    from server.drivers.generic_tcp import GenericTCPDriver

    register_driver(GenericTCPDriver)

    # Load .avcdriver YAML definitions and .py Python drivers from
    # both the built-in definitions directory and driver_repo/
    from server.drivers.driver_loader import load_all_drivers
    from server.system_config import DRIVER_DEFINITIONS_DIR, DRIVER_REPO_DIR

    driver_dirs = [
        DRIVER_DEFINITIONS_DIR,
        DRIVER_REPO_DIR,
    ]
    loaded = load_all_drivers(driver_dirs)
    if loaded:
        log.info(f"Loaded {loaded} driver(s) from definition/driver files")


# Load built-in drivers on module import
_load_builtin_drivers()


class DeviceManager:
    """Manages all device driver instances."""

    def __init__(self, state: StateStore, events: EventBus):
        self.state = state
        self.events = events
        self._devices: dict[str, BaseDriver] = {}
        self._device_configs: dict[str, dict[str, Any]] = {}
        self._reconnect_tasks: dict[str, asyncio.Task] = {}
        self._orphaned_devices: dict[str, dict[str, Any]] = {}  # devices with missing drivers

        # Auto-reconnect when a device transport drops mid-session
        self.events.on(
            "device.disconnected.*", self._on_device_disconnected
        )

    async def add_device(self, device_config: dict[str, Any]) -> None:
        """
        Instantiate a driver, register its state variables, and connect.

        Args:
            device_config: Dict with id, driver, name, config keys.
        """
        device_id = device_config["id"]
        driver_id = device_config["driver"]
        name = device_config.get("name", device_id)
        config = device_config.get("config", {})

        enabled = device_config.get("enabled", True)
        if not enabled:
            self._device_configs[device_id] = device_config
            self.state.set(f"device.{device_id}.name", name, source="config")
            self.state.set(f"device.{device_id}.connected", False, source="config")
            self.state.set(f"device.{device_id}.enabled", False, source="config")
            log.info(f"Device {device_id} is disabled, skipping connection")
            return

        # Look up driver class
        driver_class = _DRIVER_REGISTRY.get(driver_id)
        if driver_class is None:
            log.warning(f"Driver '{driver_id}' not found for device '{device_id}' — device is orphaned")
            self._orphaned_devices[device_id] = device_config
            self._device_configs[device_id] = device_config
            self.state.set(f"device.{device_id}.name", name, source="config")
            self.state.set(f"device.{device_id}.connected", False, source="config")
            self.state.set(f"device.{device_id}.orphaned", True, source="config")
            self.state.set(
                f"device.{device_id}.orphan_reason",
                f"Driver '{driver_id}' is not installed",
                source="config",
            )
            await self.events.emit("device.orphaned", {"device_id": device_id, "driver": driver_id})
            return

        # Create driver instance
        driver = driver_class(device_id, config, self.state, self.events)
        self._devices[device_id] = driver
        self._device_configs[device_id] = device_config

        # Set device name in state
        self.state.set(
            f"device.{device_id}.name", name, source=f"device.{device_id}"
        )

        log.info(f"Added device '{device_id}' ({name}) using driver '{driver_id}'")

        # Attempt connection
        try:
            await driver.connect()
            # Apply pending settings after successful connect
            await self._apply_pending_settings(device_id)
        except Exception as e:
            log.warning(f"Failed to connect '{device_id}': {e}")
            self._start_reconnect(device_id)

    async def remove_device(self, device_id: str) -> None:
        """Disconnect and remove a device (handles both active and orphaned)."""
        # Cancel reconnect if running — await so reconnect loop finishes
        await self._cancel_reconnect(device_id)

        driver = self._devices.pop(device_id, None)
        if driver:
            try:
                await driver.disconnect()
            except Exception:
                log.exception(f"Error disconnecting '{device_id}'")

        # Also clean up orphan tracking
        self._orphaned_devices.pop(device_id, None)

        self._device_configs.pop(device_id, None)

        # Clear all state keys for this device
        device_keys = self.state.get_namespace(f"device.{device_id}.")
        for key in device_keys:
            self.state.delete(f"device.{device_id}.{key}")

        log.info(f"Removed device '{device_id}'")

    async def update_device(self, device_id: str, new_config: dict[str, Any]) -> None:
        """
        Update a device by disconnecting and re-adding with new config.

        Handles both active devices and orphaned devices (driver reassignment).

        Args:
            device_id: The existing device ID.
            new_config: Full device config dict (id, driver, name, config).
        """
        if device_id in self._devices or device_id in self._orphaned_devices:
            await self.remove_device(device_id)
        elif device_id in self._device_configs:
            # Disabled device — just clean up config
            self._device_configs.pop(device_id, None)
        else:
            raise ValueError(f"Device '{device_id}' not found")
        await self.add_device(new_config)

    async def send_command(
        self, device_id: str, command: str, params: dict[str, Any] | None = None
    ) -> Any:
        """Send a command to a device by ID."""
        driver = self._devices.get(device_id)
        if driver is None:
            raise ValueError(f"Device '{device_id}' not found")
        if not driver.get_state("connected"):
            raise ConnectionError(f"Device '{device_id}' is not connected")
        return await driver.send_command(command, params)

    def get_device_info(self, device_id: str) -> dict[str, Any]:
        """Return device metadata, status, and capabilities."""
        # Check if orphaned first
        if device_id in self._orphaned_devices:
            config = self._orphaned_devices[device_id]
            return {
                "id": device_id,
                "name": config.get("name", device_id),
                "driver": config.get("driver", ""),
                "connected": False,
                "orphaned": True,
                "orphan_reason": f"Driver '{config.get('driver', '')}' is not installed",
                "state": self.state.get_namespace(f"device.{device_id}"),
                "commands": {},
                "driver_info": {},
                "config": config.get("config", {}),
            }

        driver = self._devices.get(device_id)
        if driver is None:
            # Check disabled devices
            if device_id in self._device_configs:
                config = self._device_configs[device_id]
                return {
                    "id": device_id,
                    "name": config.get("name", device_id),
                    "driver": config.get("driver", ""),
                    "connected": False,
                    "state": self.state.get_namespace(f"device.{device_id}"),
                    "commands": {},
                    "driver_info": {},
                }
            raise ValueError(f"Device '{device_id}' not found")

        config = self._device_configs.get(device_id, {})
        return {
            "id": device_id,
            "name": config.get("name", device_id),
            "driver": config.get("driver", ""),
            "connected": driver.get_state("connected"),
            "state": self.state.get_namespace(f"device.{device_id}"),
            "commands": driver.DRIVER_INFO.get("commands", {}),
            "driver_info": driver.DRIVER_INFO,
        }

    def list_devices(self) -> list[dict[str, Any]]:
        """List all devices with summary info (including orphaned and disabled)."""
        result = []
        seen = set()

        # Active devices
        for device_id in self._devices:
            seen.add(device_id)
            try:
                info = self.get_device_info(device_id)
                entry: dict[str, Any] = {
                    "id": info["id"],
                    "name": info["name"],
                    "driver": info["driver"],
                    "connected": info["connected"],
                }
                # Include command names so callers don't need get_device_info per device
                if info.get("commands"):
                    entry["commands"] = list(info["commands"].keys())
                result.append(entry)
            except Exception:
                result.append({"id": device_id, "name": device_id, "connected": False})

        # Orphaned devices (driver not found)
        for device_id, config in self._orphaned_devices.items():
            if device_id not in seen:
                seen.add(device_id)
                result.append({
                    "id": device_id,
                    "name": config.get("name", device_id),
                    "driver": config.get("driver", ""),
                    "connected": False,
                    "orphaned": True,
                    "orphan_reason": f"Driver '{config.get('driver', '')}' is not installed",
                })

        # Disabled devices
        for device_id, config in self._device_configs.items():
            if device_id not in seen:
                seen.add(device_id)
                result.append({
                    "id": device_id,
                    "name": config.get("name", device_id),
                    "driver": config.get("driver", ""),
                    "connected": False,
                    "enabled": False,
                })

        return result

    def get_device_configs(self) -> dict[str, dict[str, Any]]:
        """Return a shallow copy of the device config dict (device_id → config)."""
        return dict(self._device_configs)

    def get_device_config(self, device_id: str) -> dict[str, Any] | None:
        """Return a single device's config dict, or None if not tracked."""
        return self._device_configs.get(device_id)

    async def retry_orphaned_device(self, device_id: str) -> bool:
        """Re-attempt adding an orphaned device (e.g., after installing its driver).

        Returns True if the device was successfully activated, False if still orphaned.
        """
        if device_id not in self._orphaned_devices:
            raise ValueError(f"Device '{device_id}' is not orphaned")

        config = self._orphaned_devices[device_id]
        driver_id = config.get("driver", "")

        # Check if the driver is now available
        if driver_id not in _DRIVER_REGISTRY:
            return False

        # Remove from orphan tracking and re-add normally
        await self.remove_device(device_id)
        await self.add_device(config)
        return device_id not in self._orphaned_devices

    async def set_device_setting(
        self, device_id: str, key: str, value: Any
    ) -> Any:
        """Set a device setting value on a device by ID."""
        driver = self._devices.get(device_id)
        if driver is None:
            raise ValueError(f"Device '{device_id}' not found")
        if not driver.get_state("connected"):
            raise ConnectionError(f"Device '{device_id}' is not connected")

        # Validate the setting exists
        settings = driver.DRIVER_INFO.get("device_settings", {})
        if key not in settings:
            raise ValueError(f"Unknown device setting '{key}' for device '{device_id}'")

        return await driver.set_device_setting(key, value)

    def get_device_settings(self, device_id: str) -> dict[str, Any]:
        """Return device settings metadata with current values from state."""
        driver = self._devices.get(device_id)
        if driver is None:
            raise ValueError(f"Device '{device_id}' not found")

        settings_def = driver.DRIVER_INFO.get("device_settings", {})
        result: dict[str, Any] = {}
        for key, setting in settings_def.items():
            state_key = setting.get("state_key", key)
            current_value = driver.get_state(state_key)
            result[key] = {
                **setting,
                "current_value": current_value,
            }
        return result

    async def reload_driver(self, driver_id: str) -> list[str]:
        """
        Reconnect all devices using a given driver after it has been reloaded.

        Finds all active devices using the specified driver_id, disconnects them,
        and re-adds them so they pick up the new driver class from the registry.
        Also retries any orphaned devices that were waiting for this driver.

        Returns a list of device IDs that were reconnected.
        """
        reconnected: list[str] = []

        # Find active devices using this driver
        affected = [
            (did, cfg)
            for did, cfg in self._device_configs.items()
            if cfg.get("driver") == driver_id and did in self._devices
        ]

        for device_id, config in affected:
            try:
                await self.remove_device(device_id)
                await self.add_device(config)
                reconnected.append(device_id)
                log.info(f"Reconnected device '{device_id}' after driver reload")
            except Exception:
                log.exception(f"Failed to reconnect '{device_id}' after driver reload")

        # Retry orphaned devices that were waiting for this driver
        orphaned_for_driver = [
            did for did, cfg in self._orphaned_devices.items()
            if cfg.get("driver") == driver_id
        ]
        for device_id in orphaned_for_driver:
            try:
                activated = await self.retry_orphaned_device(device_id)
                if activated:
                    reconnected.append(device_id)
                    log.info(f"Activated orphaned device '{device_id}' after driver reload")
            except Exception:
                log.exception(f"Failed to activate orphaned device '{device_id}'")

        return reconnected

    def get_devices_using_driver(self, driver_id: str) -> list[str]:
        """Return list of device IDs that use the given driver."""
        return [
            did for did, cfg in self._device_configs.items()
            if cfg.get("driver") == driver_id
        ]

    async def connect_all(self) -> list[str]:
        """Connect all devices (called at startup). Returns list of failed device IDs."""
        failed: list[str] = []
        for device_id, driver in self._devices.items():
            if not driver.get_state("connected"):
                try:
                    await driver.connect()
                except Exception as e:
                    log.warning(f"Failed to connect '{device_id}': {e}")
                    failed.append(device_id)
                    self._start_reconnect(device_id)
        return failed

    async def disconnect_all(self) -> None:
        """Disconnect all devices gracefully (called at shutdown)."""
        # Cancel all reconnect tasks first — await each so loops finish cleanly
        for device_id in list(self._reconnect_tasks.keys()):
            await self._cancel_reconnect(device_id)

        for device_id, driver in self._devices.items():
            try:
                await driver.disconnect()
            except Exception:
                log.exception(f"Error disconnecting '{device_id}'")

    # --- Pending Settings ---

    async def _apply_pending_settings(self, device_id: str) -> None:
        """Apply any pending device settings after a successful connect."""
        config = self._device_configs.get(device_id, {})
        pending = config.get("pending_settings", {})
        if not pending:
            return

        driver = self._devices.get(device_id)
        if driver is None:
            return

        applied_keys: list[str] = []
        for key, value in pending.items():
            try:
                await driver.set_device_setting(key, value)
                applied_keys.append(key)
                log.info(f"[{device_id}] Applied pending setting '{key}' = {value!r}")
            except Exception as e:
                log.warning(f"[{device_id}] Failed to apply pending setting '{key}': {e}")

        if applied_keys:
            # Clear applied settings from pending
            for key in applied_keys:
                pending.pop(key, None)

            # If all pending settings were applied, remove the dict entirely
            if not pending:
                config.pop("pending_settings", None)

            # Notify the engine to persist the change
            await self.events.emit(
                "device.pending_settings_applied",
                {"device_id": device_id, "applied": applied_keys},
            )

    async def store_pending_settings(
        self, device_id: str, settings: dict[str, Any]
    ) -> None:
        """Store pending settings for a device (will be applied on next connect)."""
        config = self._device_configs.get(device_id)
        if config is None:
            raise ValueError(f"Device '{device_id}' not found")

        if "pending_settings" not in config:
            config["pending_settings"] = {}
        config["pending_settings"].update(settings)
        log.info(f"[{device_id}] Stored {len(settings)} pending setting(s)")

    # --- Reconnection ---

    async def _on_device_disconnected(self, event: str, payload: dict[str, Any]) -> None:
        """Handle device.disconnected.* events — trigger auto-reconnect."""
        # Extract device_id from event name: "device.disconnected.<id>"
        parts = event.split(".", 2)
        if len(parts) < 3:
            return
        device_id = parts[2]

        # Only reconnect if device still exists and isn't being removed
        if device_id not in self._devices:
            return

        # Check the device isn't disabled
        config = self._device_configs.get(device_id, {})
        if not config.get("enabled", True):
            return

        log.info(f"[{device_id}] Transport disconnected — starting auto-reconnect")
        self._start_reconnect(device_id)

    def _start_reconnect(self, device_id: str) -> None:
        """Start a background reconnect loop for a device."""
        if device_id in self._reconnect_tasks:
            return  # Already reconnecting
        task = asyncio.create_task(self._reconnect_loop(device_id))
        task.add_done_callback(_log_task_exception)
        self._reconnect_tasks[device_id] = task

    async def _cancel_reconnect(self, device_id: str) -> None:
        """Cancel a running reconnect task and wait for it to finish."""
        task = self._reconnect_tasks.pop(device_id, None)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    async def _reconnect_loop(self, device_id: str, max_attempts: int = 120) -> None:
        """
        Background task that attempts to reconnect a disconnected device.
        Exponential backoff: 2s, 4s, 8s, 16s, 30s max.
        Gives up after max_attempts (default 120 = ~1 hour at 30s intervals).
        """
        delays = [2, 4, 8, 16, 30]
        attempt = 0

        try:
            while attempt < max_attempts:
                # Check device still exists before each attempt
                driver = self._devices.get(device_id)
                if driver is None:
                    log.debug(f"[{device_id}] Device removed, stopping reconnect")
                    return

                delay = delays[min(attempt, len(delays) - 1)]
                log.info(
                    f"[{device_id}] Reconnect attempt {attempt + 1}/{max_attempts} in {delay}s..."
                )
                await asyncio.sleep(delay)

                # Re-check after sleep — device may have been removed
                if device_id not in self._devices:
                    log.debug(f"[{device_id}] Device removed during wait, stopping reconnect")
                    return

                try:
                    # Stop polling before reconnect to prevent race conditions
                    # (poll firing while transport is being replaced)
                    await driver.stop_polling()
                    await driver.connect()
                    log.info(f"[{device_id}] Reconnected successfully")
                    # Apply pending settings after successful reconnect
                    await self._apply_pending_settings(device_id)
                    return
                except Exception as e:
                    log.warning(f"[{device_id}] Reconnect failed: {e}")
                    attempt += 1

            # Exhausted all attempts
            log.warning(
                f"[{device_id}] Gave up reconnecting after {max_attempts} attempts. "
                f"Use the Reconnect button or restart the server to try again."
            )
            self.state.set(
                f"device.{device_id}.reconnect_failed", True,
                source="device_manager",
            )
        except asyncio.CancelledError:
            log.debug(f"[{device_id}] Reconnect cancelled")
        finally:
            self._reconnect_tasks.pop(device_id, None)

    async def reconnect_device(self, device_id: str) -> None:
        """Force disconnect and reconnect a device."""
        if device_id not in self._devices:
            raise ValueError(f"Device '{device_id}' not found")
        driver = self._devices[device_id]
        # Cancel any existing auto-reconnect task first
        await self._cancel_reconnect(device_id)
        # Clear stale reconnect_failed state
        self.state.set(f"device.{device_id}.reconnect_failed", None, source="device_manager")
        # Disconnect and reconnect
        try:
            await driver.disconnect()
        except Exception:
            pass
        try:
            await driver.connect()
            self.state.set(f"device.{device_id}.connected", True, source="device_manager")
            log.info(f"Reconnected device: {device_id}")
        except Exception as e:
            self.state.set(f"device.{device_id}.connected", False, source="device_manager")
            log.warning(f"Reconnect failed for {device_id}: {e}")
            self._start_reconnect(device_id)
