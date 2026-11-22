"""THE rule for which form a YAML command, or a device setting's write, is
sent in: an IR code, a ``udp:`` datagram, an OSC message, an HTTP request or
a line of the device's protocol.

The rules worth knowing before changing anything here:

* **The table is spec's.** ``spec.SEND_FORMS`` names each form and the fields
  that choose it, in the order the runtime tries them; ``SEND_FORM_ORIGINS``
  says which forms a command and a setting's write can take (a write has no
  IR code or datagram). This module only reads it.
* **A field fills its form unless it is null or empty**: None, false, zero,
  or an empty string, list or mapping (``fills``), which is how the runtime
  always read ``send`` and ``ir``. So ``path: ""`` left beside a ``send`` is
  no HTTP request, and the command sends its line. Nothing is trimmed: a
  ``send`` that is a line feed is a real keepalive. A field of the wrong kind
  that is not empty (``udp: "x"``) still declares its form: the checker
  names it, and the sender refuses it as not sent.
* **Two answers, for two kinds of reader.** ``declared_forms`` is every form
  a block fills, which the checker refuses unless it is exactly one;
  ``command_form`` is the first of them, the one the runtime sends, which the
  runtime's senders, the Builder's dry run, the Live Test and the simulator
  ask. A block that fills none has no form (None), which is not the line
  form: such a command has nothing to send.
* **An address or IR code left empty** (``left_empty``): a command whose only
  form field is an ``address`` or ``ir:`` written but empty fills no form, so
  it sends nothing. Until this rule both counted as a form, so a driver saved
  with one (the Builder's Address box cleared) passed every door: the
  author's doors refuse it and a door loading the driver names it
  (``left_empty_forms``, a driver model problem), where every other command
  with no form is refused at every door.
* The Driver Builder's ``commandForm`` (``validateDriver.ts``) reads the same
  table from ``types.gen.ts``; ``tests/fixtures/command_form_cases.json`` is
  the one case file both are held to.

Purity contract, as for ``avcdriver_semantic``: the standard library and
``spec`` only, so the checker's vendored copy (openavc-drivers' catalog CI,
openavc-sdk) can import it.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from openavc.drivers.spec import SEND_FORM_ORIGINS, SEND_FORMS

__all__ = [
    "FORM_LEFT_EMPTY", "SEND_FORMS", "command_form", "declared_forms", "fills", "left_empty",
    "left_empty_forms",
]

# The code of a command whose only form field is an address or IR code left
# empty, and what is said at that field.
FORM_LEFT_EMPTY = "form_left_empty"
_LEFT_EMPTY_WORDS = "is empty, so the command sends nothing; fill it in or remove the command"

# The fields that were a form whatever they held, in SEND_FORMS order.
_ONCE_A_FORM = ("ir", "address")


def fills(value: Any) -> bool:
    """Whether a form field says something: anything but None, false, zero,
    or an empty string, list or mapping (the truth of the value, which is
    how the runtime has always read ``send`` and ``ir``)."""
    return bool(value)


def declared_forms(block: Any, origin: str = "command") -> list[str]:
    """Every form ``block`` fills a field of, in the order the runtime tries
    them; none for a block that is not a mapping.

    ``origin`` is ``"command"`` or ``"setting_write"``
    (``spec.SEND_FORM_ORIGINS``)."""
    if not isinstance(block, Mapping):
        return []
    takes = SEND_FORM_ORIGINS[origin]
    return [
        form
        for form, fields in SEND_FORMS
        if form in takes and any(fills(block.get(field)) for field in fields)
    ]


def command_form(block: Any, origin: str = "command") -> str | None:
    """The form the runtime sends ``block`` in: the first it fills, or None
    when it fills none."""
    forms = declared_forms(block, origin)
    return forms[0] if forms else None


def left_empty(block: Any) -> str | None:
    """The field of a command that fills no form but writes an ``ir:`` or an
    ``address`` left empty (null aside), or None."""
    if not isinstance(block, Mapping) or declared_forms(block):
        return None
    return next((field for field in _ONCE_A_FORM if block.get(field) is not None), None)


def left_empty_forms(commands: Any) -> list[tuple[str, str]]:
    """Each command of ``commands`` left with an empty address or IR code:
    the field's place and the words said there."""
    if not isinstance(commands, Mapping):
        return []
    found = ((name, left_empty(block)) for name, block in commands.items())
    return [(f"commands.{name}.{field}", _LEFT_EMPTY_WORDS) for name, field in found if field]
