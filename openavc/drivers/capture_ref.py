"""THE capture-reference reading: which capture of a reply's match a text rule names.

A text (regex) response rule reads each value, and each child's id, out of one
capture of its ``match:``. The driver contract writes that reference in one of
two forms, ``"$N"`` (a dollar sign, then the capture's number) or the number
itself, and every place a rule names a capture takes either:

* a flat ``set:`` value (``"$2"``) and a ``mappings:`` entry's ``group``;
* a ``child_set:`` id (``"$1"`` or ``{group, map}``) and a child value
  (``"$2"`` or ``{group, map, type}``);
* a ``child_list:``'s list, item id, page start and item values.

``capture_group`` is that reading of one reference (a long form's ``group``
is read by passing it), used by the runtime (``compiled_protocol``,
``child_routes``), the auto-simulator (``simulator/reply_rules``) and the
checker, so each place reads ``{group: "$2"}`` as capture 2 and nothing else
reads a reference for itself. Capture 0 is the whole match. A ``mappings:``
entry that names no group reads the whole match; the caller passes the default
its field documents (a ``child_list``'s list capture defaults to 1).

Anything else is not a reference: ``"$x"``, ``"$ 2"``, ``"$+2"``, ``"$-1"``,
a negative number, a boolean, a float and number text without its dollar
sign. Each reader that gets ``None`` skips what it cannot read and the checker
says why. The floor a field adds (a child's id and a list's captures start at
1) and the bound a pattern's capture count sets stay with the field's reader.

Purity contract, as ``spec``: standard library only, and no logger. It is part
of the driver contract the community catalog vendors (openavc-drivers'
``scripts/vendor_platform_contract.py``) and the SDK syncs.
"""

from __future__ import annotations

import re
from typing import Any

_REF = re.compile(r"\$(\d+)")


def names_a_capture(value: Any) -> bool:
    """Whether ``value`` is written as a reference to a capture: text that
    starts with ``$``. Whether it reads as one is ``capture_group``'s."""
    return isinstance(value, str) and value.startswith("$")


def capture_group(ref: Any, default: int | None = 0) -> int | None:
    """The capture number ``ref`` names, or None when it names none.

    ``ref`` is ``"$N"`` or a whole number, 0 or more. A reference that is
    absent (``None``) reads as ``default``."""
    if ref is None:
        return default
    if isinstance(ref, bool):
        return None
    if isinstance(ref, int):
        return ref if ref >= 0 else None
    if isinstance(ref, str):
        written = _REF.fullmatch(ref)
        return int(written.group(1)) if written else None
    return None
