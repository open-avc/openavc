"""What a command's window hears from the device while it is open.

A command's window runs from the moment it is sent until it closes
(``audit/commands.py`` decides when). While it is open the audit keeps, on the
command's own record: every status value that changed, each error the driver
published for the device (``device.error``), each time the connection
dropped and came back, and every ``last_error`` the driver wrote, a rewrite of
the same text included. Each error and error write also keeps its
``position``, how long the device's traffic list was when it came, which
places it against the requests sent around it exactly (``audit/exchange.py``).

A window has three states: ``sending`` until the driver's call returns,
``watching`` while it is open, ``done`` once it has closed. Nothing is heard
once it is done, even before the listener is closed: stopping the audit can
close a command's window before its watch has unwound.

A command that restarts the device (``restarts_device_for``) ends
``after_reconnect_seconds`` after the driver is first back, never later than
it would have.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from openavc.audit.observe import recorded

if TYPE_CHECKING:
    from openavc.audit.commands import CommandTrial

SENDING = "sending"
WATCHING = "watching"
DONE = "done"

# State changes a window keeps.
CHANGES_KEPT = 500


class WindowListener:
    """Hears the device for one command's window, from creation until
    :meth:`close`."""

    def __init__(self, trial: "CommandTrial", sandbox: Any, *, after_reconnect_seconds: float) -> None:
        self.trial = trial
        self.sandbox = sandbox
        self.after_reconnect_seconds = after_reconnect_seconds
        self._prefix = f"device.{sandbox.device_id}."
        device_id = sandbox.device_id
        self._subscription = sandbox.state.subscribe(f"{self._prefix}*", self._on_state)
        self._handlers = [
            sandbox.events.on(f"device.error.{device_id}", self._on_error),
            sandbox.events.on(f"device.disconnected.{device_id}", self._on_gone),
            sandbox.events.on(f"device.connected.{device_id}", self._on_back),
        ]
        # The sandbox store keeps every last_error write; the window's are
        # the ones from here on.
        self._error_writes = getattr(sandbox.state, "error_writes", [])
        self._error_writes_from = len(self._error_writes)

    def close(self) -> None:
        """Stop hearing, and keep every ``last_error`` the driver wrote while
        the window was open."""
        own_key = f"{self._prefix}last_error"
        self.trial.error_writes = [
            {"t": t, "error": recorded(value), "position": position}
            for t, key, value, position in self._error_writes[self._error_writes_from:]
            if key == own_key
        ]
        self.sandbox.state.unsubscribe(self._subscription)
        for handler in self._handlers:
            self.sandbox.events.off(handler)

    def _on_state(self, key: str, old: Any, new: Any, _source: str = "") -> None:
        trial = self.trial
        if trial.status == DONE or len(trial.changes) >= CHANGES_KEPT:
            return
        trial.changes.append({
            "t": time.time(), "key": key.removeprefix(self._prefix),
            "old": recorded(old), "new": recorded(new),
        })

    def _on_error(self, _event: str, payload: Any = None) -> None:
        if self.trial.status == DONE:
            return
        error = payload.get("error") if isinstance(payload, dict) else payload
        self.trial.device_errors.append({
            "t": time.time(), "error": str(error or ""),
            "position": len(self.sandbox.observer.traffic),
        })

    def _on_gone(self, _event: str, _payload: Any = None) -> None:
        trial = self.trial
        if trial.status == DONE:
            return
        now = time.time()
        if trial.went_away_at is None:
            trial.went_away_at = now
        if not _drop_open(trial):
            trial.gone_and_back.append([now, None])

    def _on_back(self, _event: str, _payload: Any = None) -> None:
        trial = self.trial
        if trial.status == DONE or not trial.gone_and_back:
            return
        if _drop_open(trial):
            trial.gone_and_back[-1][1] = time.time()
        if trial.back_at is None:
            self._first_back()

    def _first_back(self) -> None:
        trial = self.trial
        trial.back_at = time.time()
        if trial.declared.get("restarts_device_for") and trial.ends_at is not None:
            trial.ends_at = min(trial.ends_at, trial.back_at + self.after_reconnect_seconds)


def _drop_open(trial: "CommandTrial") -> bool:
    """Is the window's latest drop still waiting for the driver to come back?"""
    return bool(trial.gone_and_back) and trial.gone_and_back[-1][1] is None
