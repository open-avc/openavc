"""
Network condition simulation — latency, jitter, packet drops, connection instability.

Applied as a transport-level layer between the driver and the simulator's
protocol handler. Independent of device-specific behavior.

Can be set globally or per-device.
"""

from __future__ import annotations

import asyncio
import random
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class NetworkConditions:
    """Network condition parameters."""

    latency_ms: float = 0.0
    """Base latency in milliseconds added to every message."""

    jitter_pct: float = 0.0
    """Jitter as a percentage of latency (0-100). Randomizes actual delay."""

    drop_rate_pct: float = 0.0
    """Percentage of messages silently dropped (0-50)."""

    instability: str = "off"
    """Connection instability level: off, low, medium, high."""

    def to_dict(self) -> dict:
        return {
            "latency_ms": self.latency_ms,
            "jitter_pct": self.jitter_pct,
            "drop_rate_pct": self.drop_rate_pct,
            "instability": self.instability,
        }

    @classmethod
    def from_dict(cls, d: dict) -> NetworkConditions:
        return cls(
            latency_ms=d.get("latency_ms", 0.0),
            jitter_pct=d.get("jitter_pct", 0.0),
            drop_rate_pct=d.get("drop_rate_pct", 0.0),
            instability=d.get("instability", "off"),
        )


# ── Presets ──

PRESETS: dict[str, NetworkConditions] = {
    "perfect": NetworkConditions(
        latency_ms=0, jitter_pct=0, drop_rate_pct=0, instability="off",
    ),
    "typical_lan": NetworkConditions(
        latency_ms=2, jitter_pct=10, drop_rate_pct=0, instability="off",
    ),
    "busy_network": NetworkConditions(
        latency_ms=50, jitter_pct=20, drop_rate_pct=2, instability="off",
    ),
    "flaky_wifi": NetworkConditions(
        latency_ms=200, jitter_pct=50, drop_rate_pct=10, instability="low",
    ),
    "barely_working": NetworkConditions(
        latency_ms=1000, jitter_pct=80, drop_rate_pct=25, instability="high",
    ),
}

# Instability levels: probability of random disconnect per message cycle
INSTABILITY_DISCONNECT_PROB = {
    "off": 0.0,
    "low": 0.002,     # ~0.2% chance per message
    "medium": 0.01,   # ~1% chance per message
    "high": 0.05,     # ~5% chance per message
}


class NetworkConditionLayer:
    """Applies network conditions to simulator communication.

    Used by TCPSimulator and HTTPSimulator to degrade connections
    in a realistic way for testing.
    """

    def __init__(self):
        self._global: NetworkConditions = NetworkConditions()
        self._per_device: dict[str, NetworkConditions] = {}

    @property
    def global_conditions(self) -> NetworkConditions:
        return self._global

    def set_global(self, conditions: NetworkConditions) -> None:
        self._global = conditions
        logger.info("Global network conditions set: %s", conditions.to_dict())

    def set_global_preset(self, preset_name: str) -> NetworkConditions:
        """Apply a named preset globally."""
        conditions = PRESETS.get(preset_name)
        if not conditions:
            raise ValueError(
                f"Unknown preset '{preset_name}'. "
                f"Available: {', '.join(PRESETS.keys())}"
            )
        self._global = NetworkConditions(**conditions.to_dict())
        logger.info("Global network preset applied: %s", preset_name)
        return self._global

    def set_device(self, device_id: str, conditions: NetworkConditions) -> None:
        """Set per-device override."""
        self._per_device[device_id] = conditions

    def clear_device(self, device_id: str) -> None:
        """Remove per-device override (falls back to global)."""
        self._per_device.pop(device_id, None)

    def get_conditions(self, device_id: str) -> NetworkConditions:
        """Get effective conditions for a device (per-device or global)."""
        return self._per_device.get(device_id, self._global)

    async def apply_latency(self, device_id: str) -> None:
        """Apply latency delay for a device. Call before processing response."""
        cond = self.get_conditions(device_id)
        if cond.latency_ms <= 0:
            return

        base = cond.latency_ms / 1000.0  # convert to seconds
        if cond.jitter_pct > 0:
            jitter_range = base * (cond.jitter_pct / 100.0)
            actual = base + random.uniform(-jitter_range, jitter_range)
            actual = max(0, actual)
        else:
            actual = base

        if actual > 0:
            await asyncio.sleep(actual)

    def should_drop(self, device_id: str) -> bool:
        """Check if this message should be dropped."""
        cond = self.get_conditions(device_id)
        if cond.drop_rate_pct <= 0:
            return False
        return random.random() * 100 < cond.drop_rate_pct

    def should_disconnect(self, device_id: str) -> bool:
        """Check if the connection should be randomly dropped."""
        cond = self.get_conditions(device_id)
        prob = INSTABILITY_DISCONNECT_PROB.get(cond.instability, 0.0)
        if prob <= 0:
            return False
        return random.random() < prob

    def to_dict(self) -> dict:
        """Serialize all conditions for the API."""
        return {
            "global": self._global.to_dict(),
            "per_device": {
                k: v.to_dict() for k, v in self._per_device.items()
            },
            "presets": list(PRESETS.keys()),
        }
