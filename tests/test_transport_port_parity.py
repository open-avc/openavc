"""The IDE moves Port with Connection by the same standard ports the server
names when the two disagree.

`STANDARD_TRANSPORT_PORTS` in the IDE's deviceConfigCoerce.ts (Add / Edit
Device: switch Connection from SSH to telnet and a Port still on 22 becomes
23) is a copy of `_WELL_KNOWN_TRANSPORT_PORTS` in core/connection_fault.py
(the device card's "Connection is set to telnet but Port is 22" after a
failed connection). A port added to one and not the other would have the
form move a Port the server then calls a mismatch, or the reverse.
"""

import re
from pathlib import Path

from openavc.core.connection_fault import _WELL_KNOWN_TRANSPORT_PORTS

_TS = (
    Path(__file__).resolve().parents[1]
    / "openavc" / "web" / "programmer" / "src" / "views" / "devices" / "deviceConfigCoerce.ts"
)


def test_the_ide_and_the_server_know_the_same_standard_ports():
    source = _TS.read_text(encoding="utf-8")
    table = re.search(r"STANDARD_TRANSPORT_PORTS[^=]*=\s*\{([^}]*)\}", source)
    assert table, "STANDARD_TRANSPORT_PORTS not found in deviceConfigCoerce.ts"
    ide = {name: int(port) for name, port in re.findall(r'(\w+):\s*"(\d+)"', table.group(1))}

    server = {name: port for port, name in _WELL_KNOWN_TRANSPORT_PORTS.items()}
    # telnet is spelled "tcp" in driver config; both readers accept "telnet".
    server["telnet"] = server["tcp"]
    assert ide == server
