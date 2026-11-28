"""The panel's lists of the platform's own device keys are the server's.

A control bound to a key the platform writes about a device (whether it is
connected, its name, why it is away, its web page) keeps drawing while the
device is unreachable; anything else under `device.` is a reading, and blanks.
The panel decides which is which from two sets, `DEVICE_PLATFORM_PROPS` and,
for a child entity, `CHILD_PLATFORM_PROPS`. They are rendered from
`core/device_info.PLATFORM_DEVICE_KEYS` and `drivers/spec.CHILD_RESERVED_PROPS`
into `web/panel/modules/device_keys.gen.js` by
`python -m openavc.core.device_keys_gen`, so a key the platform starts writing
reaches every panel when the file is regenerated, and this test fails until it
is.
"""

from __future__ import annotations

import re
from pathlib import Path

from openavc.core.device_info import PLATFORM_DEVICE_KEYS
from openavc.core.device_keys_gen import PANEL_ARTIFACT, render
from openavc.drivers.spec import CHILD_RESERVED_PROPS

REPO = Path(__file__).resolve().parents[1]
GENERATED = REPO / PANEL_ARTIFACT
AVAILABILITY_JS = REPO / "openavc" / "web" / "panel" / "modules" / "availability.js"
SET_NAMES = ("DEVICE_PLATFORM_PROPS", "CHILD_PLATFORM_PROPS")


def _generated_set(name: str) -> set[str]:
    match = re.search(rf"export const {name} = new Set\(\[(.*?)\]\);", GENERATED.read_text(encoding="utf-8"), re.S)
    assert match, f"no {name} in {GENERATED.name}"
    return set(re.findall(r'"([a-z_]+)"', match.group(1)))


def test_the_committed_file_matches_a_fresh_render():
    assert GENERATED.is_file(), f"{PANEL_ARTIFACT} is missing -- run 'python -m openavc.core.device_keys_gen'"
    committed = GENERATED.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert committed == render(PANEL_ARTIFACT), (
        f"{PANEL_ARTIFACT} does not match the platform's device keys -- regenerate with "
        "'python -m openavc.core.device_keys_gen' (never edit it by hand)"
    )


def test_the_panel_keeps_drawing_every_device_key_the_platform_writes():
    assert _generated_set("DEVICE_PLATFORM_PROPS") == set(PLATFORM_DEVICE_KEYS)


def test_the_panel_keeps_drawing_every_reserved_child_prop():
    assert _generated_set("CHILD_PLATFORM_PROPS") == set(CHILD_RESERVED_PROPS)


def test_availability_reads_the_generated_sets_and_writes_none_of_its_own():
    src = AVAILABILITY_JS.read_text(encoding="utf-8")
    imported = re.search(r"import \{([^}]*)\} from '\./device_keys\.gen\.js';", src)
    assert imported, "availability.js no longer imports the platform's keys from device_keys.gen.js"
    assert {name.strip() for name in imported.group(1).split(",")} == set(SET_NAMES)
    for name in SET_NAMES:
        assert not re.search(rf"\b(?:const|let|var) {name}\b", src), f"availability.js writes its own {name}"
