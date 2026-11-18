"""THE driver id rule: what a driver may be called where its id is first
given, and what is said of a driver already installed under an id outside it.

An id is held to ``spec.DRIVER_ID_PATTERN``, made of ``spec.DRIVER_ID_CHARS``
(lowercase letters, digits and underscores), judged whole (``is_driver_id``):
the pattern's ``$`` would let a trailing newline through under ``re.match``.

Where an id is first given (a new driver, a rename, a new import, Validate,
``drivers.check``, the AI's driver tool, catalog CI, the Code view's New
Driver, a community install), an id outside the pattern is refused, naming
the characters it may use (``id_refusal``; ``id_refusals`` at the place
``id``). A driver already installed under such an id (the Code view's New
Driver let a hyphen through) keeps loading, opening and saving under it: its
devices name it by that id. The YAML checker (``avcdriver_semantic``) refuses
in strict mode unless the door says the save keeps an id the library already
serves (``keeps_id``, from ``driver_file_name.keeps_installed_id``); a Python
driver's refusal is ``python_info.python_driver_refusals``. The Installed
entry of a driver kept under such an id says so in the IDE's words, from the
same characters (``driverId.ts``).

Purity contract: standard library plus ``spec``, like every module the
definition walk imports; vendored into openavc-drivers and openavc-sdk with
it.
"""

from __future__ import annotations

import re
from typing import Any

from openavc.drivers.spec import DRIVER_ID_PATTERN

_DRIVER_ID = re.compile(DRIVER_ID_PATTERN)

# The characters, as the sentences name them.
_ALLOWED = "lowercase letters, digits and underscores"


def is_driver_id(value: Any) -> bool:
    """True when ``value`` is text the id pattern takes whole."""
    return isinstance(value, str) and _DRIVER_ID.fullmatch(value) is not None


def _breaks_the_rule(value: Any) -> bool:
    """An id written as text that the pattern does not take. A value that is
    not text is the shape walk's to name, and a missing id the definition's
    required fields'."""
    return isinstance(value, str) and not is_driver_id(value)


def id_refusal(value: Any) -> str | None:
    """Why a door that gives a driver its id refuses ``value``, naming what
    it may hold; None when the pattern takes it. A door that answers in its
    own words puts this after them."""
    if not _breaks_the_rule(value):
        return None
    if not value.strip():
        return f"an empty id names no driver; use only {_ALLOWED}"
    return f"{value!r} can use only {_ALLOWED}"


def id_refusals(value: Any) -> list[str]:
    """``id_refusal`` as the checkers say it, at the place ``id``."""
    refusal = id_refusal(value)
    return [] if refusal is None else [f"id: {refusal}"]
