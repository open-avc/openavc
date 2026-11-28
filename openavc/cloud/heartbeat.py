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

from openavc.cloud.protocol import build_heartbeat_payload
from openavc.core.device_presence import AWAY_ON_PURPOSE, in_trouble
from openavc.utils.logger import get_logger

if TYPE_CHECKING:
    from openavc.core.state_store import StateStore
    from openavc.core.device_manager import DeviceManager

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

    def __init__(
        self,
        state: StateStore,
        devices: DeviceManager,
        ws_client_count_fn=None,
        start_time: float | None = None,
    ):
        """
        Args:
            state: The StateStore for reading system state.
            devices: The DeviceManager for device counts.
            ws_client_count_fn: Optional callable returning the number of
                                active WebSocket clients.
            start_time: When the SYSTEM came up, as ``time.monotonic()`` (the
                wall clock can be set days away after boot). The engine
                passes its own, because this collector is built when the cloud
                subsystem starts and that is not when the box did.
        """
        self._state = state
        self._devices = devices
        self._ws_client_count_fn = ws_client_count_fn
        # Falls back to now only for a caller that has nothing better to offer
        # (the engine always does). Measuring from construction is what made
        # the portal disagree with the instance: the collector is created at
        # cloud-subsystem start, so the two figures differed by however long
        # the box had run before pairing -- 2h 4m locally against 9m in the
        # portal, minutes after pairing, which is exactly when a new customer
        # is looking at it -- and the cloud number reset on any agent restart
        # while the room had not gone anywhere.
        self._start_time = start_time or time.monotonic()

        # Resolve data directory for disk usage reporting
        from openavc.system_config import get_data_dir
        self._data_dir = str(get_data_dir())

        # Prime psutil's CPU sampling. cpu_percent(interval=0) measures the
        # delta since the previous call, so the very first call returns a
        # meaningless 0.0 — take that throwaway reading now so the first
        # heartbeat reports a real value.
        if HAS_PSUTIL:
            try:
                psutil.cpu_percent(interval=0)
            except (OSError, AttributeError):
                pass  # psutil can fail on restricted environments

    async def collect(self) -> dict[str, Any]:
        """
        Collect all metrics and return as a heartbeat payload dict.

        Returns:
            Dict matching the heartbeat payload schema.
        """
        return build_heartbeat_payload(
            uptime_seconds=int(time.monotonic() - self._start_time),
            cpu_percent=self._get_cpu_percent(),
            memory_percent=self._get_memory_percent(),
            disk_percent=self._get_disk_percent(),
            device_count=self._get_device_count(),
            devices_connected=self._get_devices_connected(),
            devices_error=self._get_devices_error(),
            active_ws_clients=self._get_ws_client_count(),
            temperature_celsius=self._get_temperature(),
            notify_only=self._get_notify_only(),
        )

    def _get_notify_only(self) -> bool:
        """Is this system set to be told about updates rather than take them.

        Read every heartbeat rather than cached at startup: it is a Settings
        toggle, and a stale answer here is exactly the confusion the fleet
        operator is being spared.
        """
        from openavc.system_config import get_system_config
        try:
            return bool(get_system_config().get("updates", "notify_only", False))
        except Exception:  # a metric must never take the heartbeat down
            return False

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
        """Number of devices currently connected, as the device manager
        lists them."""
        try:
            return sum(1 for device in self._devices.list_devices() if device.get("connected"))
        except (AttributeError, TypeError):
            return 0  # DeviceManager or StateStore may not be fully initialized

    def _get_devices_error(self) -> int:
        """Number of devices in trouble, as the alert monitor judges them
        (core/device_presence.py ``in_trouble``): kept away by a fault the
        connection-fault classifier published, or paused past the hour a pause
        stays away on purpose. Command errors are transient events, not state.
        """
        try:
            now = time.time()
            return sum(
                1 for device in self._devices.list_devices()
                if self._in_trouble(device.get("id", ""), now)
            )
        except (AttributeError, TypeError):
            return 0  # DeviceManager or StateStore may not be fully initialized

    def _in_trouble(self, device_id: str, now: float) -> bool:
        """Whether one device counts in ``devices_error``. The pause is asked
        only for a word that is away on purpose, the one case it decides."""
        word = self._state.get(f"device.{device_id}.presence")
        if word not in AWAY_ON_PURPOSE:
            return in_trouble(word)
        pause = self._devices.pause_held(device_id)
        return in_trouble(word, pause.seconds(now) if pause is not None else None)

    def _get_ws_client_count(self) -> int:
        """Number of active local WebSocket clients."""
        if self._ws_client_count_fn:
            try:
                return self._ws_client_count_fn()
            except (TypeError, AttributeError):
                pass  # Callable may not be properly configured
        return 0
