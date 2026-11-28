"""Render the platform's own device keys into the panel.

The keys the platform writes about every device (what it is called, whether it
is enabled and reachable, why not, its web page) and about every child entity
describe the device rather than come from it, so a panel control bound to one
keeps drawing while the device is away. The lists are
``core/device_info.PLATFORM_DEVICE_KEYS`` and ``drivers/spec.CHILD_RESERVED_PROPS``;
this writes both to ``openavc/web/panel/modules/device_keys.gen.js``, which
``modules/availability.js`` reads.

Data only. Run:  python -m openavc.core.device_keys_gen
A test compares the committed file against a fresh render, so a key added on
the server without regenerating, or a file edited by hand, fails CI.
"""

from __future__ import annotations

import json
from collections.abc import Iterable

from openavc.core.device_info import PLATFORM_DEVICE_KEYS
from openavc.drivers.spec import CHILD_RESERVED_PROPS
from openavc.utils.generated import write_artifacts

PANEL_ARTIFACT = "openavc/web/panel/modules/device_keys.gen.js"

#: The committed files.
ARTIFACTS = (PANEL_ARTIFACT,)

BANNER = """\
// GENERATED FILE - DO NOT EDIT.
// Rendered from the platform's device keys (core/device_info.PLATFORM_DEVICE_KEYS
// and drivers/spec.CHILD_RESERVED_PROPS).
// Regenerate with:  python -m openavc.core.device_keys_gen
// A test compares this file against a fresh render, so hand edits fail CI.
//
// `DEVICE_PLATFORM_PROPS` is every `device.<id>.<key>` the platform writes for
// a device, and `CHILD_PLATFORM_PROPS` every `device.<id>.<type>.<local>.<key>`
// it writes for a child entity. They describe the device rather than come from
// it, so a control bound to one keeps drawing while the device is away.
"""


def _set(name: str, keys: Iterable[str]) -> str:
    lines = "".join(f"    {json.dumps(key)},\n" for key in sorted(keys))
    return f"export const {name} = new Set([\n{lines}]);\n"


def render(artifact: str) -> str:
    assert artifact == PANEL_ARTIFACT, artifact
    return (
        BANNER
        + "\n"
        + _set("DEVICE_PLATFORM_PROPS", PLATFORM_DEVICE_KEYS)
        + _set("CHILD_PLATFORM_PROPS", CHILD_RESERVED_PROPS)
    )


def main() -> int:
    return write_artifacts(ARTIFACTS, render)


if __name__ == "__main__":
    raise SystemExit(main())
