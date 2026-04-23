"""
BaseSimulator — abstract base for all device simulators.

Provides state management, error injection, state machine support,
and change notification. Subclasses (TCPSimulator, HTTPSimulator)
add transport-specific server lifecycle.
"""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from typing import Any, Callable

logger = logging.getLogger(__name__)


class BaseSimulator(ABC):
    """Abstract base for device simulators."""

    # Subclasses must set this
    SIMULATOR_INFO: dict = {}

    def __init__(self, device_id: str, config: dict | None = None):
        self.device_id = device_id
        self.config = config or {}

        info = self.SIMULATOR_INFO
        self._state: dict[str, Any] = dict(info.get("initial_state", {}))
        self._error_modes: dict[str, dict] = dict(info.get("error_modes", {}))
        self._active_errors: set[str] = set()
        self._delays: dict[str, float] = dict(info.get("delays", {}))
        self._state_machines: dict[str, StateMachine] = {}
        self._change_listeners: list[Callable] = []
        self._protocol_log: list[dict] = []
        self._port: int = 0
        self._running = False
        self._network_layer = None  # Set by SimulatorManager

        # Build state machines from SIMULATOR_INFO if present
        for name, sm_def in info.get("state_machines", {}).items():
            self._state_machines[name] = StateMachine(
                name=name,
                states=sm_def["states"],
                initial=sm_def["initial"],
                transitions=sm_def["transitions"],
                on_change=lambda key, val: self.set_state(key, val),
            )
            # Set initial state
            self._state[name] = sm_def["initial"]

    # ── Properties ──

    @property
    def driver_id(self) -> str:
        return self.SIMULATOR_INFO.get("driver_id", "unknown")

    @property
    def name(self) -> str:
        return self.SIMULATOR_INFO.get("name", self.driver_id)

    @property
    def category(self) -> str:
        return self.SIMULATOR_INFO.get("category", "generic")

    @property
    def transport(self) -> str:
        return self.SIMULATOR_INFO.get("transport", "tcp")

    @property
    def default_port(self) -> int:
        return self.SIMULATOR_INFO.get("default_port", 0)

    @property
    def port(self) -> int:
        return self._port

    @property
    def running(self) -> bool:
        return self._running

    # ── State Management ──

    @property
    def state(self) -> dict[str, Any]:
        """Current simulator state (read-only copy)."""
        return dict(self._state)

    def set_state(self, key: str, value: Any) -> None:
        """Update a state value. Notifies listeners (UI, protocol log)."""
        old = self._state.get(key)
        if old == value:
            return
        self._state[key] = value
        self._notify_change("state", {
            "device_id": self.device_id,
            "key": key,
            "value": value,
            "old_value": old,
        })

    def get_state(self, key: str, default: Any = None) -> Any:
        """Get a state value."""
        return self._state.get(key, default)

    # ── State Machines ──

    def transition(self, machine_name: str, trigger: str) -> bool:
        """Trigger a state machine transition. Returns True if transition occurred."""
        sm = self._state_machines.get(machine_name)
        if not sm:
            logger.warning("No state machine '%s' on %s", machine_name, self.device_id)
            return False
        return sm.trigger(trigger)

    # ── Error Injection ──

    @property
    def active_errors(self) -> set[str]:
        """Currently active error mode names."""
        return set(self._active_errors)

    @property
    def available_errors(self) -> dict[str, dict]:
        """All defined error modes with descriptions."""
        return dict(self._error_modes)

    def inject_error(self, mode: str) -> None:
        """Activate an error mode."""
        if mode not in self._error_modes:
            logger.warning("Unknown error mode '%s' on %s", mode, self.device_id)
            return
        self._active_errors.add(mode)
        # Apply state changes defined by the error mode
        state_changes = self._error_modes[mode].get("set_state", {})
        for key, value in state_changes.items():
            self.set_state(key, value)
        self._notify_change("error", {
            "device_id": self.device_id,
            "mode": mode,
            "active": True,
        })
        logger.info("Injected error '%s' on %s", mode, self.device_id)

    def clear_error(self, mode: str) -> None:
        """Deactivate an error mode."""
        self._active_errors.discard(mode)
        self._notify_change("error", {
            "device_id": self.device_id,
            "mode": mode,
            "active": False,
        })
        logger.info("Cleared error '%s' on %s", mode, self.device_id)

    def clear_all_errors(self) -> None:
        """Clear all active error modes."""
        for mode in list(self._active_errors):
            self.clear_error(mode)

    def has_error_behavior(self, behavior: str) -> bool:
        """Check if any active error has the given behavior (e.g., 'no_response')."""
        for mode in self._active_errors:
            if self._error_modes.get(mode, {}).get("behavior") == behavior:
                return True
        return False

    # ── Protocol Log ──

    def log_protocol(self, direction: str, data: bytes | str, client_id: str = "") -> None:
        """Log a protocol message. direction: 'in' (from driver) or 'out' (to driver)."""
        entry = {
            "timestamp": time.time(),
            "device_id": self.device_id,
            "direction": direction,
            "data": data if isinstance(data, str) else data.hex(),
            "data_text": data if isinstance(data, str) else _safe_ascii(data),
            "client_id": client_id,
        }
        self._protocol_log.append(entry)
        # Keep log bounded
        if len(self._protocol_log) > 5000:
            self._protocol_log = self._protocol_log[-2500:]
        self._notify_change("protocol", entry)

    def get_protocol_log(self, limit: int = 100) -> list[dict]:
        """Get recent protocol log entries."""
        return self._protocol_log[-limit:]

    def clear_protocol_log(self) -> None:
        self._protocol_log.clear()

    # ── Change Listeners ──

    def add_change_listener(self, listener: Callable) -> None:
        """Register a callback for state/error/protocol changes."""
        self._change_listeners.append(listener)

    def remove_change_listener(self, listener: Callable) -> None:
        self._change_listeners = [fn for fn in self._change_listeners if fn is not listener]

    def _notify_change(self, change_type: str, data: dict) -> None:
        for listener in self._change_listeners:
            try:
                listener(change_type, data)
            except Exception:
                logger.exception("Change listener error")

    # ── Lifecycle ──

    @abstractmethod
    async def start(self, port: int) -> None:
        """Start the simulator server on the given port."""

    @abstractmethod
    async def stop(self) -> None:
        """Stop the simulator server."""

    def to_info_dict(self) -> dict:
        """Serialize simulator info for the API."""
        info = {
            "device_id": self.device_id,
            "device_name": getattr(self, "_device_name", self.device_id),
            "real_host": getattr(self, "_real_host", ""),
            "real_port": getattr(self, "_real_port", 0),
            "driver_id": self.driver_id,
            "name": self.name,
            "category": self.category,
            "transport": self.transport,
            "port": self._port,
            "running": self._running,
            "state": self.state,
            "active_errors": list(self._active_errors),
            "available_errors": {
                k: {"description": v.get("description", "")}
                for k, v in self._error_modes.items()
            },
        }
        controls = self.SIMULATOR_INFO.get("controls")
        if controls:
            info["controls"] = controls
        info["push_state"] = getattr(self, "_push_state", False)
        return info


class StateMachine:
    """Simple state machine with timed auto-transitions."""

    def __init__(
        self,
        name: str,
        states: list[str],
        initial: str,
        transitions: list[dict],
        on_change: Callable[[str, Any], None],
    ):
        self.name = name
        self.states = states
        self.current = initial
        self.transitions = transitions
        self._on_change = on_change
        self._timer_task: asyncio.Task | None = None

    def trigger(self, trigger_name: str) -> bool:
        """Process a trigger. Returns True if a transition occurred."""
        for t in self.transitions:
            if t.get("from") != self.current:
                continue

            # Check for reject
            t_trigger = t.get("trigger")
            if t.get("reject") and (t_trigger == "*" or t_trigger == trigger_name):
                return False

            if t_trigger == trigger_name or t_trigger == "*":
                new_state = t["to"]
                self._enter_state(new_state)
                return True

        return False

    def _enter_state(self, new_state: str) -> None:
        """Transition to a new state and schedule auto-transitions."""
        self.current = new_state
        self._on_change(self.name, new_state)

        # Cancel any pending auto-transition
        if self._timer_task and not self._timer_task.done():
            self._timer_task.cancel()
            self._timer_task = None

        # Check for auto-transitions (after_seconds)
        for t in self.transitions:
            if t.get("from") == new_state and "after_seconds" in t:
                delay = t["after_seconds"]
                target = t["to"]
                self._timer_task = asyncio.ensure_future(
                    self._auto_transition(delay, target)
                )
                break

    async def _auto_transition(self, delay: float, target: str) -> None:
        """Wait and then auto-transition."""
        await asyncio.sleep(delay)
        self._enter_state(target)


def _safe_ascii(data: bytes) -> str:
    """Convert bytes to printable ASCII representation."""
    chars = []
    for b in data:
        if 32 <= b < 127:
            chars.append(chr(b))
        elif b == 10:
            chars.append("\\n")
        elif b == 13:
            chars.append("\\r")
        else:
            chars.append(f"\\x{b:02x}")
    return "".join(chars)
