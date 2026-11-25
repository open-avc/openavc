"""A device setting's declaration, read: THE answer to which state variable a
setting's current value is read back from.

``read_setting(key, sdef)`` reads one entry of a driver's ``device_settings``:
a YAML definition's or a ``DRIVER_INFO``'s, which share the shape.
``SettingModel.state_key`` is the setting's ``state_key`` when it names a
variable, and the setting's own key when it does not: left out, ``null``,
``""``, or anything that is not text (which the shape walk refuses at the
author's doors), so every reader looks for the value in the same place.

Who asks: the checker, for whether the setting would ever read back
(``setting_checks.device_setting_state_key_errors``, both surfaces); the
runtime, for the value a setting write puts on the wire (``setting_writes``)
and the read-back a queued setting waits on (``core.pending_settings``); the
device's settings answer (``core.device_info``), which serves the variable on
each setting, so the device page reads it as served; the simulator's setting
handlers (``simulator.setting_handlers``); and the Device Audit's Listen and
settings steps (``audit.listen``, ``audit.settings``).

Never logs and never raises: a declaration that is not a mapping reads as one
that declares nothing. Pure: the standard library only, so the checker's
vendored closure can import it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SettingModel:
    """One device setting, read. ``state_key`` is the state variable its
    current value is read back from (never empty). ``not_text`` holds when
    the file wrote a ``state_key`` that is neither text nor null, which the
    shape walk names where a driver file is written and which no other part
    of the setting's checks judges."""

    key: str
    state_key: str
    not_text: bool = False


def read_setting(key: str, sdef: Any) -> SettingModel:
    """The setting ``key`` as its declaration ``sdef`` says."""
    written = sdef.get("state_key") if isinstance(sdef, Mapping) else None
    return SettingModel(
        key=key,
        state_key=written if isinstance(written, str) and written else key,
        not_text=not isinstance(written, (str, type(None))),
    )
