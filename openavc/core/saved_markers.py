"""THE saved-on-this-system markers: what each one is, and whether a value is one.

A value saved on this system is kept in the login file, and the project holds
a marker in its place. There are two kinds:

* the login marker, ``<saved on this system>``: a device's, a plugin's or the
  mesh's login (``core/device_logins.py``, ``core/plugin_secrets.py``). It is
  one fixed string, so two copies of one project stay byte-identical, and it is
  never shaped like a ``{{...}}`` placeholder, which template resolution passes
  through;
* a saved parameter's marker, ``<saved on this system #`` then 16 lowercase hex
  digits then ``>``: a secret command parameter in a macro step or a control's
  action (``core/saved_params.py``). The digits name the saved value, because
  nothing else names a step.

``is_marker(value, kind)`` is the one answer to "is this a marker", asked by
every reader: the save that moves a value into the login file, the send that
fills one in or refuses it, the masking that skips a marker as no secret, and
the Programmer's box that will not save one as a value. ``kind`` is ``LOGIN``,
``PARAM`` or ``ANY`` (either). Comparing a value with ``SAVED_LOGIN`` asks the
login kind too: it is one fixed string.

Each marker is matched whole. Text that only starts like one (``<saved on this
system: see binder>``, a parameter's marker cut short) is a value like any
other: moved into the login file where a secret goes, masked, and sent as
itself. The platform only ever writes whole markers, so every real one is read
alike everywhere.

The Programmer's copies are ``SAVED_LOGIN`` in ``views/devices/deviceLogins.ts``
and the parameter marker in ``components/shared/savedParams.ts``; both run the
cases in ``tests/fixtures/saved_marker_cases.json``, as ``tests/test_saved_markers.py``
does here, and that test refuses a module that spells a marker itself.

Pure stdlib and imports nothing from the package, so it can be copied as it
stands to wherever the same question is asked. The cloud service does: it
takes this module and the cases byte for byte and runs the cases on its copy,
so a change here reaches it when it copies them again.
"""

from __future__ import annotations

import re
from typing import Any

#: What the project holds where a login is saved on this system.
SAVED_LOGIN = "<saved on this system>"

#: How a saved parameter's marker starts; 16 lowercase hex digits and ">" follow.
PARAM_PREFIX = "<saved on this system #"

_PARAM = re.compile(r"<saved on this system #([0-9a-f]{16})>")

#: The kinds ``is_marker`` answers for.
LOGIN = "login"
PARAM = "param"
ANY = "any"
KINDS = (LOGIN, PARAM, ANY)


def param_marker(name: str) -> str:
    """What a parameter holds when the value named ``name`` is saved here."""
    return f"{PARAM_PREFIX}{name}>"


def param_name(value: Any) -> str | None:
    """The name ``value`` gives, when it is a saved parameter's marker."""
    if not isinstance(value, str):
        return None
    found = _PARAM.fullmatch(value)
    return found.group(1) if found else None


def is_marker(value: Any, kind: str) -> bool:
    """Whether ``value`` is a marker of ``kind``: ``LOGIN``, ``PARAM`` or ``ANY``."""
    if kind not in KINDS:
        raise ValueError(f"Not a kind of marker: {kind!r}. Use one of {', '.join(KINDS)}.")
    login = kind != PARAM and isinstance(value, str) and value == SAVED_LOGIN
    return login or (kind != LOGIN and param_name(value) is not None)
