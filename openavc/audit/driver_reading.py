"""The driver an audit run tests, read: THE one way the Device Audit reads it.

Every step of the audit asks :func:`model_for` for the driver a run tests
(:func:`installed`, by driver id, before a run exists; :func:`reading_of` for
a driver it already holds) instead of reading a ``DRIVER_INFO``, a YAML
class's ``_definition`` or the driver registry for itself:

* **While the run's connection holds a driver** (the run's current attempt,
  or the attempt a caller names): that live instance, so its own
  ``DRIVER_INFO`` (the commands a Python driver builds once connected, a
  device's own protocol merged over its YAML file) and, for a YAML driver,
  the driver model its replies compile from, over the merged definition.
* **Otherwise the installed class**, the registry's for the chosen driver id:
  its ``DRIVER_INFO`` and its file's model. A driver removed since it was
  chosen reads as one that declares nothing.

**Read again on every ask, never kept.** A Python driver may replace its
``DRIVER_INFO`` once connected, or change it in place (a command or a setting
it drops for a device without that feature), and the next ask reads what it
holds then: that is the event the audit follows. A YAML driver's model is the
one the driver already holds (``_driver_model``), so an ask reads nothing
again but each command's declaration.

What a reading answers: the ``DRIVER_INFO`` (``info``; ``section`` for one of
its mappings), the driver model (``model``: its kind and, for a YAML driver,
its query lines), each command read once (``commands``,
``drivers/command_model``) and the commands the poll runs by name
(``polled_commands``, by the runtime's sender's rule,
``driver_model.runs_as_command``).

Asked by: the command list (``command_catalog``), a command's declared effect
(``effects``), the settings step (``settings``), what changed (``changes``),
the connect-and-listen step (``listen``), the commands step (``commands``),
the driver choice and the run's passes (``driver_choice``, ``passes``), the
report's public strings (``redaction``) and the sandbox's check that the
driver is installed (``sandbox``).
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from openavc.drivers import registry
from openavc.drivers.command_model import CommandModel, read_command
from openavc.drivers.driver_model import (
    POLL,
    PYTHON,
    DriverModel,
    query_lines,
    read_driver,
    runs_as_command,
)


@dataclass(frozen=True)
class AuditedDriver:
    """One driver as the audit reads it: ``driver`` is the live instance, the
    installed class, or None for neither; ``info`` its ``DRIVER_INFO``;
    ``model`` its driver model."""

    driver: Any
    info: Mapping[str, Any]
    model: DriverModel

    def section(self, key: str) -> Mapping[str, Any]:
        """One of the ``DRIVER_INFO``'s mappings (``commands``,
        ``state_variables``, ...), empty when it declares none."""
        value = self.info.get(key)
        return value if isinstance(value, Mapping) else {}

    def commands(self) -> Iterator[tuple[str, dict[str, Any], CommandModel]]:
        """Each declared command, in the driver's own order: its name, its
        declaration (empty when it is not a mapping) and its reading."""
        for name, cdef in self.section("commands").items():
            cdef = cdef if isinstance(cdef, dict) else {}
            yield name, cdef, read_command(name, cdef)

    def polled_commands(self) -> set[str]:
        """The commands a YAML driver's poll runs by name: its status
        queries. A start-up line need not be a status query, and a follow-up
        or a read-back is a command's own; an entry's ``when`` gate is not
        read, since a line the poll may send is a status query whether or not
        this device's config sends it now. None for a Python driver, whose
        class owns what it sends."""
        commands = self.model.definition.get("commands")
        return {
            sent.text for sent in query_lines(self.model, (POLL,))
            if runs_as_command(commands, sent.text)
        }


def reading_of(driver: Any) -> AuditedDriver:
    """``driver`` (an instance, a class, or None) as the audit reads it."""
    info = getattr(driver, "DRIVER_INFO", None)
    info = info if isinstance(info, Mapping) else {}
    return AuditedDriver(driver, info, _model(driver, info))


def installed(driver_id: str) -> AuditedDriver:
    """The installed driver ``driver_id``: the registry's class, or a reading
    of nothing when none is installed."""
    return reading_of(registry.get_driver_class(driver_id))


def model_for(run: Any, attempt: Any = None) -> AuditedDriver:
    """The driver ``run`` tests: the one ``attempt``'s connection holds (the
    run's current attempt when none is named), else the installed one."""
    listen = attempt if attempt is not None else run.listen
    driver = listen.sandbox.driver if listen is not None else None
    if driver is None:
        return installed(run.choice.driver_id)
    return reading_of(driver)


def _model(driver: Any, info: Mapping[str, Any]) -> DriverModel:
    """A YAML driver's model over the definition it runs (the one it holds,
    when it holds one over that definition); a Python driver's over its
    ``DRIVER_INFO``."""
    definition = getattr(driver, "_definition", None)
    if not isinstance(definition, Mapping):
        return read_driver(info, PYTHON)
    kept = getattr(driver, "_driver_model", None)
    if isinstance(kept, DriverModel) and kept.definition is definition:
        return kept
    return read_driver(definition)
