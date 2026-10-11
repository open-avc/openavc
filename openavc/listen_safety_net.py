"""The safety net under a change of who can connect that cuts off the
browser making it.

``PATCH /api/system/config`` arms it when the change would leave the asking
browser unable to reach the server (``listen.preview``'s
``keeps_this_connection``; never for the computer's own screen or the cloud
tunnel, which arrive on loopback and keep working under every choice). The
marker, ``listen-safety-net.json`` beside system.json, holds the choice that
was running, the new one, and, from the first start that listens on the new
choice, when that began.

**Confirmed** by opening the Programmer through the new choice: it calls
``POST /api/system/listen/confirm`` when it loads, which can only arrive on a
connection the new choice allows (the computer's own screen and the cloud
tunnel included). The marker goes and "Kept: ..." is said once.

**Reverted** when :data:`DEADLINE_SECONDS` pass after the server began
listening on the new choice with no confirmation: the previous choice is
written back, a notice is left for the next Programmer load ("Went back to
..."), and the server restarts.

This is not the start-up timer the updater removed (``updater/rollback.py``).
That one reverted a new *version* that had started fine but had not yet called
in, and appliances restarted by their own supervisor tripped it. This reverts
a *setting*, only after the person making it was told it would, counts from
when the server is listening on the new choice so a slow start cannot trip
it, and any Programmer load through the new choice cancels it.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from openavc.utils.logger import get_logger

log = get_logger(__name__)

MARKER_NAME = "listen-safety-net.json"
NOTICE_NAME = "listen-notice.json"
DEADLINE_SECONDS = 600


def _write(path: Path, data: dict[str, Any]) -> None:
    from openavc.utils.fileio import atomic_write_text

    atomic_write_text(path, json.dumps(data, indent=2))


def _read(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _choice_dict(choice: Any, adapter_name: str = "") -> dict[str, Any]:
    from openavc import listen

    name = adapter_name or choice.adapter.get("name", "")
    return {
        "mode": choice.mode,
        "adapter": dict(choice.adapter),
        # The choice as Settings lists it, which is how the messages name it.
        "label": listen.choice_label(choice.mode, name),
        "phrase": listen.choice_phrase(choice.mode, name),
        "through": _through(choice.mode, name),
    }


def _through(mode: str, adapter_name: str) -> str:
    """Where opening the Programmer confirms the choice, inside a sentence."""
    if mode == "this_computer":
        return "on this computer"
    if mode == "adapter":
        return f"through {adapter_name}" if adapter_name else "through the chosen network"
    return "from the network"


def _same(choice: Any, stored: dict[str, Any]) -> bool:
    return choice.mode == stored.get("mode") and dict(choice.adapter) == (stored.get("adapter") or {})


def read(data_dir: Path) -> dict[str, Any] | None:
    """The armed marker, or None."""
    return _read(data_dir / MARKER_NAME)


def arm(
    data_dir: Path,
    previous: Any,
    new: Any,
    *,
    previous_name: str = "",
    new_name: str = "",
    now: float | None = None,
) -> dict[str, Any]:
    """Arm it for a change from ``previous`` (the running choice) to ``new``."""
    marker = {
        "previous": _choice_dict(previous, previous_name),
        "choice": _choice_dict(new, new_name),
        "armed_at": now if now is not None else time.time(),
        "listening_since": None,
        "deadline_seconds": DEADLINE_SECONDS,
    }
    _write(data_dir / MARKER_NAME, marker)
    log.info(
        "Accept connections from: changing to %s; going back to %s unless the "
        "Programmer is opened %s within %d minutes of the restart",
        marker["choice"]["phrase"], marker["previous"]["phrase"],
        marker["choice"]["through"], DEADLINE_SECONDS // 60,
    )
    return marker


def disarm(data_dir: Path) -> None:
    try:
        (data_dir / MARKER_NAME).unlink()
    except FileNotFoundError:
        pass
    except OSError:
        log.warning("Could not remove %s", data_dir / MARKER_NAME, exc_info=True)


def note_listening(data_dir: Path, running: Any, now: float | None = None) -> None:
    """Called once the sockets are bound: start the clock when this server
    runs the new choice. A marker for any other choice is stale (the
    environment holds the setting, or the choice changed again) and goes."""
    marker = read(data_dir)
    if marker is None:
        return
    if running.held_by or not _same(running, marker.get("choice") or {}):
        disarm(data_dir)
        return
    if marker.get("listening_since") is None:
        marker["listening_since"] = now if now is not None else time.time()
        _write(data_dir / MARKER_NAME, marker)


def status(data_dir: Path) -> dict[str, Any] | None:
    """What Settings shows while it is armed."""
    marker = read(data_dir)
    if marker is None:
        return None
    since = marker.get("listening_since")
    deadline = None
    if since is not None:
        deadline = datetime.fromtimestamp(
            since + marker.get("deadline_seconds", DEADLINE_SECONDS), tz=timezone.utc
        ).isoformat()
    return {
        "previous": marker["previous"]["label"],
        "choice": marker["choice"]["label"],
        "running": since is not None,
        "deadline": deadline,
    }


def confirm(data_dir: Path, running: Any) -> str | None:
    """Keep the new choice, when this server is running it. Returns the
    sentence the Programmer shows once, or None when nothing was armed."""
    marker = read(data_dir)
    if marker is None or marker.get("listening_since") is None:
        return None
    if not _same(running, marker.get("choice") or {}):
        return None
    disarm(data_dir)
    log.info("Accept connections from: %s confirmed", marker["choice"]["phrase"])
    return f"Kept: Accept connections from {marker['choice']['label']}."


def take_notice(data_dir: Path) -> str | None:
    """The "Went back" sentence, once."""
    path = data_dir / NOTICE_NAME
    notice = _read(path)
    if notice is None:
        return None
    try:
        path.unlink()
    except OSError:
        pass
    return str(notice.get("message") or "") or None


def due(data_dir: Path, now: float | None = None) -> dict[str, Any] | None:
    """The marker, when its deadline has passed unconfirmed."""
    marker = read(data_dir)
    if marker is None or marker.get("listening_since") is None:
        return None
    now = now if now is not None else time.time()
    if now < marker["listening_since"] + marker.get("deadline_seconds", DEADLINE_SECONDS):
        return None
    return marker


def revert(cfg: Any, marker: dict[str, Any], adapters: Any) -> str:
    """Write the previous choice back and leave the notice. The caller
    restarts the server."""
    from openavc import listen

    previous = marker["previous"]
    cfg.set("network", listen.MODE_KEY, previous["mode"])
    cfg.set("network", listen.ADAPTER_KEY, dict(previous.get("adapter") or {}))
    listen.refresh_mirror(cfg, adapters)
    cfg.save()
    minutes = marker.get("deadline_seconds", DEADLINE_SECONDS) // 60
    message = (
        f"Went back to {previous['label']} because nobody opened the Programmer "
        f"{marker['choice']['through']} within {minutes} minutes."
    )
    _write(cfg.data_dir / NOTICE_NAME, {"message": message, "at": time.time()})
    disarm(cfg.data_dir)
    log.warning("Accept connections from: %s", message)
    return message


async def tick(
    cfg: Any,
    restart: Callable[[str], Awaitable[None]],
    *,
    adapters: Any = None,
    now: float | None = None,
) -> bool:
    """Revert and restart when the deadline has passed. True when it did."""
    from openavc import listen

    marker = due(cfg.data_dir, now)
    if marker is None:
        return False
    revert(cfg, marker, adapters if adapters is not None else listen.list_adapters)
    await restart("the safety net went back to the previous choice")
    return True
