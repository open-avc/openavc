"""THE words a true / false value reads as in a sentence the server writes: Yes and No.

The server half of the Programmer's ``components/shared/booleanWords.ts``. A
sentence the server writes for a person (a device audit's progress lines, its
results, its summary and timeline) reads a boolean the way the Programmer's
own screens do, so "Auto Attenuation: Yes" on the device page is not "Wrote
true to Auto Attenuation" on the audit. Data keeps true / false: report.json,
the API and the project file are unchanged.

``core/monitors.py`` spells the same two words itself, because it is vendored
on its own and may import nothing from the package; ``tests/test_boolean_words.py``
holds the three copies together.

Pure stdlib.
"""

from __future__ import annotations

from typing import Any

YES = "Yes"
NO = "No"


def boolean_word(value: bool) -> str:
    """The word for a boolean."""
    return YES if value else NO


def value_text(value: Any, empty: str | None = None) -> str:
    """A value as a sentence shows it.

    A boolean reads Yes / No; ``None`` reads ``empty`` when the caller gives
    one; anything else is its text.
    """
    if isinstance(value, bool):
        return boolean_word(value)
    if value is None and empty is not None:
        return empty
    return str(value)
