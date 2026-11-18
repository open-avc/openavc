"""THE in-use rule: a driver a device in the project uses is never taken away.

Every door that takes a driver away (the Driver Builder's delete, the Installed
page's uninstall and the Code view's delete) asks ``refuse_while_in_use`` while
``holding`` the id, once it knows the files it would remove and before it
removes any. While a project device is on the driver the door is refused
(``DriverInUse``, 409), naming each device as the Installed page lists it
(``DeviceOnDriver``: its name, or its id when it has none), so the IDE can offer
to open each one and choose another driver. The IDE's own copy of the rule,
asked before a delete is sent, is ``devicesOnDriver`` in
``web/programmer/src/components/shared/driverInUse.ts``.

**The project's devices, not the running ones.** A device waiting for its
driver, or one that failed to start, still names the driver in the project, and
would wait for a driver that never comes back once its file is gone.

**Every id the removed files serve.** A Python file saved under another id and
not reloaded since still serves its devices on the id it was loaded with, so the
ids a door holds a removal to are the one it names and each id registered from a
Python file it removes (``ids_served_by``).

There is no FastAPI here: ``DriverInUse`` is a ``DriverRefused``, and
``api/routes/drivers.in_use_or_answer`` is how the API answers it.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openavc.drivers import driver_change
from openavc.drivers.driver_change import DriverRefused
from openavc.drivers.python_modules import imported_from
from openavc.drivers.registry import registered_driver_classes


@dataclass(frozen=True)
class DeviceOnDriver:
    """A project device on a driver, named as the Installed page lists it."""

    id: str
    name: str

    def answer(self) -> dict[str, str]:
        """The device as a refusal names it to the IDE."""
        return {"id": self.id, "name": self.name}


class DriverInUse(DriverRefused):
    """A door refused because project devices use the driver (``devices``)."""

    def __init__(self, devices: Collection[DeviceOnDriver]) -> None:
        super().__init__(409, in_use_sentence(devices))
        self.devices = tuple(devices)


def in_use_sentence(devices: Collection[DeviceOnDriver]) -> str:
    """What a refused door says: who uses the driver, and the way out."""
    names = [device.name for device in devices]
    if len(names) == 1:
        return f"{names[0]} uses this driver. Open the device and choose another driver first."
    return (
        f"{len(names)} devices use this driver: {', '.join(names)}. "
        "Open each one and choose another driver first."
    )


def devices_on(project: Any, driver_ids: Collection[str]) -> list[DeviceOnDriver]:
    """Each device in ``project`` on one of ``driver_ids``, in the project's order."""
    if project is None:
        return []
    return [
        DeviceOnDriver(device.id, device.name or device.id)
        for device in project.devices
        if device.driver in driver_ids
    ]


def project_devices_on(driver_ids: Collection[str]) -> list[DeviceOnDriver]:
    """``devices_on`` the running project (none before the engine starts)."""
    engine = driver_change._running_engine()  # the engine every change follows up on
    return devices_on(getattr(engine, "project", None), driver_ids)


def ids_served_by(files: Iterable[Path]) -> list[str]:
    """Every id whose registered class was imported from one of the Python
    driver ``files``."""
    python = [path for path in files if path.suffix == ".py"]
    return [
        driver_id
        for driver_id, driver_class in registered_driver_classes()
        if any(imported_from(driver_class, path) for path in python)
    ]


def refuse_while_in_use(driver_id: str, removing: Iterable[Path] = ()) -> None:
    """Refuse (``DriverInUse``) taking ``driver_id`` away, with the files
    ``removing``, while a project device is on it or on an id one of those
    files serves."""
    devices = project_devices_on({driver_id, *ids_served_by(removing)})
    if devices:
        raise DriverInUse(devices)
