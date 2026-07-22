"""External IR code database (IRDB) search endpoints.

Backs the IR Codes editor's "Search database" action: browse a large
crowd-sourced remote-code database by brand and device, then take one rendered
function as a Pronto code. Search-and-fetch-one, not a bulk import — the database
is fetched at runtime and never bundled (see :mod:`server.core.ir_database`).
"""

from typing import Any

from fastapi import APIRouter

from server.api.errors import api_error as _api_error
from server.core.ir_database import IRDB_HOMEPAGE, IRDB_ISSUES, IRDB_NOTICE, IrDatabase

router = APIRouter()

# One shared, in-memory-cached client for the whole server.
_db = IrDatabase()

_META = {"notice": IRDB_NOTICE, "homepage": IRDB_HOMEPAGE, "issues": IRDB_ISSUES}


@router.get("/ir-db/brands")
async def ir_db_brands() -> dict[str, Any]:
    """List every brand in the database (the frontend filters as the user types)."""
    try:
        brands = await _db.brands()
    except Exception as e:  # noqa: BLE001 — surfaced as a friendly API error
        raise _api_error(503, "Could not reach the IR code database", e)
    if not brands:
        raise _api_error(
            503,
            "Could not reach the IR code database. Check the server's internet "
            "connection and try again.",
            ConnectionError("irdb index empty"),
        )
    return {"brands": brands, **_META}


@router.get("/ir-db/devices")
async def ir_db_devices(brand: str) -> dict[str, Any]:
    """List the code sets (device types + code numbers) for one brand."""
    try:
        devices = await _db.devices(brand)
    except Exception as e:  # noqa: BLE001
        raise _api_error(503, "Could not reach the IR code database", e)
    if not devices and not await _db.available():
        # An empty result with an unreachable index is a connectivity failure,
        # not a real "brand has no devices" — surface it like /ir-db/brands does
        # so the UI shows "database unreachable" instead of "no devices".
        raise _api_error(
            503,
            "Could not reach the IR code database. Check the server's internet "
            "connection and try again.",
            ConnectionError("irdb index empty"),
        )
    return {"brand": brand, "devices": devices, **_META}


@router.get("/ir-db/functions")
async def ir_db_functions(path: str) -> dict[str, Any]:
    """List one code set's functions, each rendered to Pronto where supported.

    ``path`` is a code-set path from the ``devices`` response; it is validated
    against the index before any fetch.
    """
    try:
        functions = await _db.functions(path)
    except ValueError as e:
        raise _api_error(400, str(e), e)
    except ConnectionError as e:
        raise _api_error(503, "Could not reach the IR code database", e)
    except Exception as e:  # noqa: BLE001
        raise _api_error(500, "Failed to load codes from the IR database", e)
    return {"path": path, "functions": functions, **_META}
