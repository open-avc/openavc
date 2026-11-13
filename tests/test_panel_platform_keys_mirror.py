"""The panel's lists of the platform's own device keys, held to the server's.

A control bound to a key the platform writes about a device (whether it is
connected, its name, why it is away) keeps drawing while the device is
unreachable; anything else under `device.` is a reading, and blanks. The panel
decides which is which from two hand-written sets in
`web/panel/modules/availability.js`: `DEVICE_PLATFORM_PROPS` and, for a child
entity, `CHILD_PLATFORM_PROPS`. The server's lists are
`core/device_info.PLATFORM_DEVICE_KEYS` (every key the device manager writes)
and `drivers/spec.CHILD_RESERVED_PROPS`. A key added on the server only would
blank on every panel while its device is away.

Read with a regex rather than executed, like `test_theme_studio_names_mirror.py`:
the point is to catch one side being edited without the other.
"""

from __future__ import annotations

import re
from pathlib import Path

from openavc.core.device_info import PLATFORM_DEVICE_KEYS
from openavc.drivers.spec import CHILD_RESERVED_PROPS

AVAILABILITY_JS = (
    Path(__file__).resolve().parents[1] / "openavc" / "web" / "panel" / "modules" / "availability.js"
)

#: Written by the platform for every device, but read by the panel as a reading
#: from the device, so a control bound to it blanks while the device is away.
#: Whether the panel should keep drawing it is undecided; until it is, this
#: test holds the panel's list to the server's less this key.
NOT_ON_THE_PANELS_LIST = {"web_ui_url"}


def _panel_set(name: str) -> set[str]:
    text = AVAILABILITY_JS.read_text(encoding="utf-8")
    match = re.search(rf"const {name} = new Set\(\[(.*?)\]\);", text, re.S)
    assert match, f"no {name} in {AVAILABILITY_JS.name}"
    return set(re.findall(r"'([a-z_]+)'", match.group(1)))


def test_the_panel_keeps_drawing_every_device_key_the_platform_writes():
    assert _panel_set("DEVICE_PLATFORM_PROPS") == set(PLATFORM_DEVICE_KEYS) - NOT_ON_THE_PANELS_LIST


def test_the_panel_keeps_drawing_every_reserved_child_prop():
    assert _panel_set("CHILD_PLATFORM_PROPS") == set(CHILD_RESERVED_PROPS)


def test_the_exception_is_still_one_the_platform_writes():
    # When the panel learns the key, or the platform stops writing it, the
    # exception goes too.
    assert NOT_ON_THE_PANELS_LIST <= set(PLATFORM_DEVICE_KEYS)
    assert not NOT_ON_THE_PANELS_LIST & _panel_set("DEVICE_PLATFORM_PROPS")
