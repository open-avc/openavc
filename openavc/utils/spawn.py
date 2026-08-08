"""Shared creation flags for child processes.

On Windows, a console program (ping, ssh, pip, schtasks, ...) launched from a
process that has no console of its own gets a brand-new visible console window.
The server normally runs with a console (a dev terminal) or as a service where
no desktop is visible, so this stays hidden — but a server relaunched by the
in-app restart runs console-less on the user's desktop, and a discovery scan
spawns one ping per address: hundreds of console windows popping open at once.

Every subprocess spawn in server/ must pass ``creationflags=CREATE_NO_WINDOW``
(enforced by tests/test_spawn_no_window.py). The child then gets an invisible
console instead of a visible one. On POSIX this is 0, which subprocess accepts
as a no-op, so call sites don't need a platform check.
"""

import subprocess

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
