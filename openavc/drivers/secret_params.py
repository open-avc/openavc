"""Which parameters of a command on a device hold a secret: THE answer every
reader asks.

A parameter is secret by its own declaration, the rule every setting follows
(``spec.is_secret_setting``): it is marked ``secret: true`` (or carries the
older ``type: password``), or it is named ``password``, the one name that
declares itself. No other name counts: a ``pin`` or a ``token`` the driver
does not mark is not a secret here, so it is neither saved on this system nor
filled in from it. The outbound redactors (the log, the Device Audit, a
problem report, the cloud AI's log tool) also mask what is named like a
credential (``utils/log_redaction.is_secret_key``): a net over what they send
away, never what decides a secret.

The declaration read is the device's: the commands its driver declares, with
the device's own protocol merged over them (``drivers/inline_merge``). Two
places hold it, and one builder (``inline_merge.own_commands``) makes both:

* a running driver's ``DRIVER_INFO``, which the send reads
  (``core/command_gates``): the merge has run there, and a Python driver may
  have added commands of its own once it ran (no catalog driver declares a
  secret parameter in one);
* ``inline_merge.declared_commands`` for a project's device with no driver
  running, which the save reads (``core/saved_params``), and so does a
  problem report.

So a device whose own command declares a secret parameter has it saved and
filled in, and one whose own command replaces a driver command of the same
name is judged on the device's command, at the save and at the send alike.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from openavc.drivers.inline_merge import declared_commands
from openavc.drivers.spec import is_secret_setting


def _params(definition: Any) -> dict[str, Any]:
    params = definition.get("params") if isinstance(definition, dict) else None
    return params if isinstance(params, dict) else {}


def _definition(commands: Any, command: Any) -> Any:
    return commands.get(command) if isinstance(commands, dict) else None


def is_secret_param(commands: Any, command: Any, key: Any) -> bool:
    """Whether parameter ``key`` of ``command`` holds a secret, by the
    declaration in ``commands`` (a ``DRIVER_INFO`` ``commands`` block)."""
    return is_secret_setting(key, _params(_definition(commands, command)).get(key))


def secret_params_of(definition: Any, keys: Iterable[Any]) -> frozenset[str]:
    """Which of ``keys`` are secret by one command's ``definition`` (an entry
    of a ``commands`` block)."""
    declared = _params(definition)
    return frozenset(str(key) for key in keys if is_secret_setting(key, declared.get(key)))


def secret_params(commands: Any, command: Any, keys: Iterable[Any]) -> frozenset[str]:
    """Which of ``keys`` (the parameters a step or a send holds) are secret
    for ``command`` by the declaration in ``commands``."""
    return secret_params_of(_definition(commands, command), keys)


def device_commands(driver_class: Any, config: dict[str, Any]) -> dict[str, Any]:
    """The ``commands`` a project's device declares, with no driver running
    (``inline_merge.declared_commands``); none for a driver not installed."""
    return declared_commands(driver_class, config) if driver_class is not None else {}
