"""Whether a control's reading can be believed, judged on the server the way
the panel judges it.

The panel draws a reading from a device that is away as no reading at all,
and a toggle on one presses the way its light is drawn: off. That judgment is
the panel's (``web/panel/modules/availability.js`` ``readingOffline`` and
``deviceIdForKey``); this is its copy, for the server's own account of what a
press would send without a panel (``core/ui_events.resolve_press``, the AI's
simulated press). ``tests/test_press_parity.py`` runs both over one corpus.

The rule:

- A key reports from the device its second segment names
  (``device.<id>.<prop>``, a child's ``device.<id>.<type>.<local>.<prop>``
  too), except a key naming one of the platform's own props
  (``PLATFORM_DEVICE_KEYS`` for a device, ``CHILD_RESERVED_PROPS`` for a
  child): those describe the device rather than come from it, so they keep
  telling the truth while it is away.
- The device is away under every presence word but ``online``
  (``core/device_presence.py``). No word yet is not away: a panel drawn before
  its first snapshot draws normally.
- A device asleep on purpose keeps the reading of a control whose press wakes
  it (``wake_devices``, from ``ui/action_devices.py``).
"""

from __future__ import annotations

from collections.abc import Collection
from typing import Any

from openavc.core.device_presence import ASLEEP, ONLINE
from openavc.drivers.spec import CHILD_RESERVED_PROPS, PLATFORM_DEVICE_KEYS


def reporting_device(key: Any) -> str | None:
    """The device a state key reports from, or None when it names none or
    names one of the platform's own props."""
    if not isinstance(key, str) or not key.startswith("device."):
        return None
    parts = key.split(".")
    if len(parts) < 3 or _names_platform_prop(parts):
        return None
    return parts[1] or None


def _names_platform_prop(parts: list[str]) -> bool:
    if len(parts) == 3:
        return parts[2] in PLATFORM_DEVICE_KEYS
    if len(parts) == 5:
        return parts[4] in CHILD_RESERVED_PROPS
    return False


def reading_offline(key: Any, state: Any, wake_devices: Collection[str] = ()) -> bool:
    """Whether ``key``'s reading cannot be believed: the device it reports
    from is away, and is not asleep with this press waking it.
    ``state`` answers ``get``."""
    device_id = reporting_device(key)
    if device_id is None:
        return False
    word = state.get(f"device.{device_id}.presence")
    if not isinstance(word, str) or word == ONLINE:
        return False
    return not (word == ASLEEP and device_id in wake_devices)
