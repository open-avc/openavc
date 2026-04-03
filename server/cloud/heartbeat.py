"""
OpenAVC Cloud — Heartbeat metric collection.

Collects system metrics (CPU, memory, disk, uptime, temperature) and
OpenAVC-specific metrics (device counts, WebSocket clients). Runs on
a configurable interval controlled by the cloud server.

Cross-platform: works on Linux and Windows with graceful fallback
for platform-specific features like CPU temperature.
"""

from __future__ import annotations

import time
from typing import Any, TYPE_CHECKING

from server.utils.logger import get_logger

if TYPE_CHECKING:
    from server.core.state_store import StateStore
    from server.core.device_manager import DeviceManager

log = get_logger(__name__)

# psutil is optional — used for metrics if available
try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False
    log.info("Heartbeat: psutil not available, using basic metrics only")


class HeartbeatCollector:
    """
    Collects system and application metrics for heartbeat messages.

    Metrics are collected on-demand when collect() is called. The caller
    (CloudAgent) is responsible for the timing interval.
    """

    def __init__(self, state: StateStore, devices: DeviceManager, ws_client_count_fn=None):
        """
        Args:
            state: The StateStore for reading system state.
            devices: The DeviceManager for device counts.
            ws_client_count_fn: Optional callable returning the number of
                                active WebSocket clients.
        """
        self._state = state
        self._devices = devices
        self._ws_client_count_fn = ws_client_count_fn
        self._start_time = time.time()

        # Resolve data directory for disk usage reporting
        from server.system_config import get_data_dir
        self._data_dir = str(get_data_dir())

    async def collect(self) -> dict[str, Any]:
        """
        Collect all metrics and return as a heartbeat payload dict.

        Returns:
            Dict matching the heartbeat payload schema.
        """
        metrics: dict[str, Any] = {
            "uptime_seconds": int(time.time() - self._start_time),
            "cpu_percent": self._get_cpu_percent(),
            "memory_percent": self._get_memory_percent(),
            "disk_percent": self._get_disk_percent(),
            "device_count": self._get_device_count(),
            "devices_connected": self._get_devices_connected(),
            "devices_error": self._get_devices_error(),
            "active_ws_clients": self._get_ws_client_count(),
        }

        temp = self._get_temperature()
        if temp is not None:
            metrics["temperature_celsius"] = temp

        return metrics

    # --- System Metrics ---

    def _get_cpu_percent(self) -> float:
        """Get CPU usage percentage."""
        if HAS_PSUTIL:
            try:
                return psutil.cpu_percent(interval=0)
            except (OSError, AttributeError):
                pass  # psutil can fail on restricted environments
        return 0.0

    def _get_memory_percent(self) -> float:
        """Get memory usage percentage."""
        if HAS_PSUTIL:
            try:
                return psutil.virtual_memory().percent
            except (OSError, AttributeError):
                pass  # psutil can fail on restricted environments
        return 0.0

    def _get_disk_percent(self) -> float:
        """Get disk usage percentage for the partition holding user data."""
        if HAS_PSUTIL:
            try:
                return psutil.disk_usage(self._data_dir).percent
            except (OSError, AttributeError):
                pass  # psutil can fail on restricted environments or missing mounts
        return 0.0

    def _get_temperature(self) -> float | None:
        """
        Get CPU temperature in Celsius.

        Only available on Linux with psutil. Returns None on Windows
        or if temperature sensors aren't accessible.
        """
        if not HAS_PSUTIL:
            return None

        try:
            temps = psutil.sensors_temperatures()
            if not temps:
                return None

            # Try common sensor names
            for name in ("coretemp", "cpu_thermal", "cpu-thermal", "soc_thermal"):
                if name in temps and temps[name]:
                    return temps[name][0].current

            # Fall back to first available sensor
            for sensors in temps.values():
                if sensors:
                    return sensors[0].current
        except (AttributeError, OSError, KeyError):
            pass  # sensors_temperatures not available on this platform

        return None

    # --- Application Metrics ---

    def _get_device_count(self) -> int:
        """Total number of configured devices."""
        try:
            return len(self._devices.list_devices())
        except (AttributeError, TypeError):
            return 0  # DeviceManager may not be fully initialized

    def _get_devices_connected(self) -> int:
        """Number of devices currently connected."""
        try:
            count = 0
            for device in self._devices.list_devices():
                device_id = device.get("id", "")
                status = self._state.get(f"device.{device_id}.connected")
                if status:
                    count += 1
            return count
        except (AttributeError, TypeError):
            return 0  # DeviceManager or StateStore may not be fully initialized

    def _get_devices_error(self) -> int:
        """Number of devices in error state."""
        try:
            count = 0
            for device in self._devices.list_devices():
                device_id = device.get("id", "")
                error = self._state.get(f"device.{device_id}.error")
                if error:
                    count += 1
            return count
        except (AttributeError, TypeError):
            return 0  # DeviceManager or StateStore may not be fully initialized

    def _get_ws_client_count(self) -> int:
        """Number of active local WebSocket clients."""
        if self._ws_client_count_fn:
            try:
                return self._ws_client_count_fn()
            except (TypeError, AttributeError):
                pass  # Callable may not be properly configured
        return 0
