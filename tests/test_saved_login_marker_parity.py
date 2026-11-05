"""The Programmer knows the marker a project holds where a login is saved on
this system.

`SAVED_LOGIN` in the IDE's deviceLogins.ts is a copy of `SAVED_LOGIN` in
core/device_logins.py. The device dialogs show a field holding it as "Saved on
this system" and send it back untouched to keep the saved login. A copy that
drifted would show the marker as a password in a box, where text typed after
it was saved as part of the password, and a box left empty after Change would
remove the saved login instead of keeping it.
"""

import re
from pathlib import Path

from openavc.core.device_logins import SAVED_LOGIN

_TS = (
    Path(__file__).resolve().parents[1]
    / "openavc" / "web" / "programmer" / "src" / "views" / "devices" / "deviceLogins.ts"
)


def test_the_programmer_and_the_server_use_the_same_marker():
    source = _TS.read_text(encoding="utf-8")
    found = re.search(r'export const SAVED_LOGIN\s*=\s*"([^"]*)"', source)
    assert found, "SAVED_LOGIN not found in deviceLogins.ts"
    assert found.group(1) == SAVED_LOGIN
