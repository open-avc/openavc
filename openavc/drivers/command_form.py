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

__all__ = ["SEND_FORMS", "command_form", "declared_forms", "fills"]


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
