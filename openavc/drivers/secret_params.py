"""Which parameters of a command on a device, and which of its settings, hold
a secret: THE answer every reader asks.

A parameter is secret by its own declaration, the rule every setting follows
(``spec.is_secret_setting``): it is marked ``secret: true`` (or carries the
older ``type: password``), or it is named ``password``, the one name that
declares itself, even on a command that lists no such parameter or on a
driver that is not installed. No other name counts: a ``pin`` or a ``token`` the driver
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

A device setting is secret by the same rule, on the ``device_settings`` its
driver declares (``is_secret_device_setting``). Every setting door asks it: the
cloud AI's write refuses one, the queue of settings waiting for a device never
takes one, and a live write has the device's masking learn its value
(``core/pending_settings``). What the device reports for one is read by the
same rule (``reported_secret_values``, under ``secret_setting_state_keys``):
the driver's state writes have the device's masking learn it
(``drivers/state_writes``), and the Device Audit and a problem report read it
themselves.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from openavc.drivers.inline_merge import declared_commands
from openavc.drivers.setting_model import read_setting
from openavc.drivers.spec import is_secret_setting
from openavc.utils.log_redaction import MIN_SECRET_LEN, secret_text


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
    (``inline_merge.declared_commands``); none for a driver not installed,
    where only a parameter named ``password`` is a secret."""
    return declared_commands(driver_class, config) if driver_class is not None else {}


def is_secret_device_setting(driver: Any, key: Any) -> bool:
    """Whether setting ``key`` of a device holds a secret, by the
    ``device_settings`` its driver declares: ``driver`` is the running driver
    or its class, or None for a device whose driver is not installed, where
    only a setting named ``password`` is one."""
    info = getattr(driver, "DRIVER_INFO", None)
    declared = info.get("device_settings") if isinstance(info, dict) else None
    return is_secret_setting(key, declared.get(key) if isinstance(declared, dict) else None)


def secret_setting_state_keys(device_settings: Any) -> frozenset[str]:
    """The status values a device reports a secret setting's value under:
    the ``state_key`` (``drivers/setting_model.read_setting``) of each device
    setting its driver declares (``DRIVER_INFO["device_settings"]``) that is
    secret by :func:`~openavc.drivers.spec.is_secret_setting`."""
    if not isinstance(device_settings, dict):
        return frozenset()
    return frozenset(
        read_setting(str(key), sdef).state_key
        for key, sdef in device_settings.items() if is_secret_setting(key, sdef)
    )


def reported_secret_values(
    device_settings: Any,
    reported: Iterable[tuple[Any, Any]],
    *,
    floor: int = MIN_SECRET_LEN,
) -> set[str]:
    """THE collector's other source: what a device reports for the settings
    its driver marks secret.

    ``device_settings`` is the driver's declaration; ``reported`` the
    device's status values as ``(name, value)`` pairs, each named under the
    device (``door_code``), a value and the one before it alike. A value under
    a secret setting's status value (:func:`secret_setting_state_keys`) is
    read as its text (``log_redaction.secret_text``), whatever its name says: a setting
    is secret by its mark, so a ``door_code`` the driver marks is one. The
    driver's state writes have the device's masking learn each one before
    anyone reads it (``drivers/state_writes``); a report, which reads the
    values itself, passes a lower ``floor``.
    """
    keys = secret_setting_state_keys(device_settings)
    if not keys:
        return set()
    values: set[str] = set()
    for name, value in reported:
        text = secret_text(value) if name in keys else None
        if text is not None and len(text) >= max(floor, 1):
            values.add(text)
    return values
