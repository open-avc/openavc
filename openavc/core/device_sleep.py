"""A device put to sleep on purpose: the bookkeeping behind ``device.<id>.asleep``.

A command may declare ``sleeps_device: <wake command>`` (``drivers/spec.py``):
sending it puts the device to sleep and takes it off the network until the
wake command brings it back (a display whose standby closes its control port,
a computer told to shut down). Without this, such a device read exactly like a
broken one: offline with a fault reason, alerting, every control greyed.

The lifecycle, driven from the device manager's away status (``core/device_away.py``):

1. **Armed.** A successful send of a sleeping command arms the device. Nothing
   is published: it is still connected and says so.
2. **Asleep.** The next connection loss while armed publishes
   ``device.<id>.asleep`` true, ``device.<id>.wake_command``, no
   ``offline_reason`` and "Asleep. <wake> wakes it." as ``offline_detail``,
   instead of the classified fault. A permanent fault (a rejected login, an
   untrusted certificate) means the device answered, so it ends the sleep and
   publishes as itself.
3. **Ends** on a reconnect, on a successful send of the wake command (which
   opens that command's own ``restarts_device_for`` window, so a device that
   does not come back reads as the real fault), and on pause, removal or
   re-registration.
4. **Disarmed without sleeping.** A device still connected
   :data:`DROP_WINDOW_SECONDS` after the sleeping command stayed reachable, so
   a later drop is a fault.

Arms and sleeps are written to ``{data_dir}/device_sleep.json`` so a server
restarted overnight still reads a sleeping computer as asleep rather than
unreachable. An entry for a device that comes back under a different driver,
or one older than :data:`MAX_AGE_SECONDS`, is dropped at load.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)

RECORD_NAME = "device_sleep.json"
# How long after a sleeping command the device has to drop for the drop to be
# the sleep. A display that stays reachable in standby never drops, and its
# arm must not turn a fault next week into "Asleep".
DROP_WINDOW_SECONDS = 15 * 60
# A sleep older than this at startup is forgotten: nobody's computer has been
# off for a week on purpose with OpenAVC watching it the whole time.
MAX_AGE_SECONDS = 7 * 24 * 3600


@dataclass
class Sleep:
    wake: str          # the wake command's id
    wake_label: str    # its label, for the sentence ("Power On")
    driver: str        # the driver id that declared it
    since: float       # wall-clock time the sleeping command was sent
    asleep: bool = False


def asleep_message(wake_label: str) -> str:
    """What ``offline_detail`` says while the device is asleep."""
    return f"Asleep. {wake_label} wakes it."


def command_label(commands: dict[str, Any], name: str) -> str:
    """A command's declared label, else its id made readable."""
    cmd_def = commands.get(name) if isinstance(commands, dict) else None
    label = str(cmd_def.get("label") or "").strip() if isinstance(cmd_def, dict) else ""
    return label or name.replace("_", " ").strip().title()


def wake_command_of(commands: dict[str, Any], command: str) -> str | None:
    """The wake command ``command`` declares, when it is a sleeping command
    whose wake command the driver actually declares; None otherwise."""
    cmd_def = commands.get(command) if isinstance(commands, dict) else None
    if not isinstance(cmd_def, dict):
        return None
    wake = cmd_def.get("sleeps_device")
    if not isinstance(wake, str) or not wake or wake == command:
        return None
    return wake if isinstance(commands.get(wake), dict) else None


def _default_path() -> Path:
    from openavc.system_config import get_data_dir

    return get_data_dir() / RECORD_NAME


class SleepBook:
    """Which devices are armed or asleep, kept in memory and on disk."""

    def __init__(self, path: Callable[[], Path] = _default_path) -> None:
        self._path = path
        self._sleeps: dict[str, Sleep] = {}
        self._loaded = False

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            raw = json.loads(self._path().read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError) as e:
            log.warning("Could not read %s: %s", RECORD_NAME, e)
            return
        now = time.time()
        for device_id, entry in (raw.items() if isinstance(raw, dict) else []):
            try:
                sleep = Sleep(
                    wake=str(entry["wake"]), wake_label=str(entry["wake_label"]),
                    driver=str(entry["driver"]), since=float(entry["since"]),
                    asleep=bool(entry.get("asleep", False)),
                )
            except (KeyError, TypeError, ValueError):
                continue
            if now - sleep.since <= MAX_AGE_SECONDS:
                self._sleeps[str(device_id)] = sleep

    def _save(self) -> None:
        from openavc.utils.fileio import atomic_write_text

        try:
            path = self._path()
            if not self._sleeps:
                path.unlink(missing_ok=True)
                return
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(
                path,
                json.dumps({k: asdict(v) for k, v in self._sleeps.items()}, indent=2),
            )
        except OSError as e:
            log.warning("Could not write %s: %s", RECORD_NAME, e)

    def get(self, device_id: str, driver_id: str | None = None) -> Sleep | None:
        """The device's sleep, or None. With ``driver_id``, a sleep recorded
        under another driver is dropped and None is answered."""
        self._load()
        sleep = self._sleeps.get(device_id)
        if sleep is not None and driver_id is not None and sleep.driver != driver_id:
            self.end(device_id)
            return None
        return sleep

    def arm(self, device_id: str, wake: str, wake_label: str, driver_id: str) -> None:
        self._load()
        self._sleeps[device_id] = Sleep(wake, wake_label, driver_id, time.time())
        self._save()

    def mark_asleep(self, device_id: str) -> None:
        self._load()
        sleep = self._sleeps.get(device_id)
        if sleep is not None and not sleep.asleep:
            sleep.asleep = True
            self._save()

    def end(self, device_id: str) -> Sleep | None:
        """Forget the device's sleep; the one that was there, or None."""
        self._load()
        sleep = self._sleeps.pop(device_id, None)
        if sleep is not None:
            self._save()
        return sleep

    def stayed_awake(self, device_id: str) -> bool:
        """True when the device is armed, not asleep, and the drop window has
        passed: it stayed reachable after the sleeping command."""
        sleep = self.get(device_id)
        return (
            sleep is not None and not sleep.asleep
            and time.time() - sleep.since > DROP_WINDOW_SECONDS
        )
