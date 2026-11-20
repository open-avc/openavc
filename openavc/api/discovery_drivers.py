"""The driver a scanned device is added with, as Discovery's add and Install
& Add decide it.

A scanned device is added only with a driver this server runs
(``refuse_driver_not_installed``): one that is not installed, or whose file
did not load, is refused in the words a device page gives for it
(``core/installed_equipment.missing_driver_reason``), then what to do instead,
with nothing saved. The project would otherwise gain a device that never
connects.

Install & Add installs a driver that is not here from this server's copy of
the community index (``install_from_the_index``): the address and the floor
the index lists (``api/driver_catalog.catalog_install``), the copy the scan
matched against, never an address the caller sends. One the index does not
list is refused with nothing installed.
"""

from __future__ import annotations

from fastapi import HTTPException

from openavc.drivers.registry import get_driver_class


def refuse_driver_not_installed(driver_id: str) -> None:
    """Refuse (422) adding a scanned device with ``driver_id`` when this server
    does not run it; nothing for a driver it runs."""
    if get_driver_class(driver_id) is None:
        raise HTTPException(status_code=422, detail=not_added(driver_id))


def not_added(driver_id: str) -> str:
    """Why a scanned device was not added with ``driver_id``, which is not
    here, and what to do instead."""
    from openavc.core.installed_equipment import is_product_hardware_id, missing_driver_reason
    from openavc.drivers.refused_files import refusal_for

    if refusal_for(driver_id) is not None:
        instead = "Fix its driver file, or choose another driver for this device."
    elif is_product_hardware_id(driver_id):
        instead = "Choose another driver for this device."
    else:
        instead = "Install it first, or choose another driver for this device."
    return f"{missing_driver_reason(driver_id).rstrip('.')}. The device was not added. {instead}"


async def install_from_the_index(driver_id: str) -> None:
    """Install ``driver_id`` as this server's copy of the community index
    lists it; refused (404) when the index lists no file for it."""
    from openavc.api.driver_catalog import catalog_install
    from openavc.api.rest import install_community_driver

    install = await catalog_install(driver_id)
    if install is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Driver '{driver_id}' is not installed and is not in the community "
                "driver library. The device was not added. Import the driver's file "
                "first, or choose another driver for this device."
            ),
        )
    await install_community_driver(install)
