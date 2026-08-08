"""Host hardware and OS-level REST endpoints.

What the *machine* running OpenAVC has and can be told to do: its network
adapters, its serial ports, a reboot, and the SSH toggle. The work itself is
done by ``server/host_control.py`` (via the root privileged helper) and by the
discovery/transport modules that already enumerate the hardware.

Note on the two "network" surfaces: adapter *enumeration* is here, while host
network *configuration* is ``routes/network.py`` under ``/api/system/network/``.
They are deliberately not merged — this router requires programmer auth, while
network.py is loopback-or-auth so the on-device setup screen can reach it
before a credential exists.
"""

from typing import Any

from fastapi import APIRouter, HTTPException, Request

router = APIRouter()


@router.get("/network/adapters")
async def get_network_adapters() -> dict[str, Any]:
    """List available network adapters for control interface selection."""
    from openavc.discovery.network_scanner import get_network_adapters as _get_adapters
    return {"adapters": _get_adapters()}


@router.get("/system/serial-ports")
async def get_serial_ports() -> dict[str, Any]:
    """List the serial ports on this server host for the device connection picker.

    Covers USB-to-serial adapters (with vendor / serial-number identity where the
    OS exposes it) and any built-in UARTs. The ports are the server's own — a USB
    adapter must be plugged into the machine running OpenAVC, not the browser's.
    """
    from openavc.transport.serial_transport import list_serial_ports
    return {"ports": list_serial_ports()}


@router.post("/system/reboot")
async def reboot_system() -> dict[str, str]:
    """Reboot the host via the root privileged helper (Pi appliance).

    The server runs with NoNewPrivileges=true, so it cannot reboot directly
    (sudo can't elevate). The root-owned helper performs the reboot; this just
    submits the request. Available only where the helper is installed.
    """
    from openavc import host_control
    from openavc.utils.logger import get_logger

    if not host_control.helper_available():
        raise HTTPException(
            status_code=501,
            detail="Reboot is not available on this deployment. Restart the device manually.",
        )

    get_logger(__name__).info("System reboot requested via API")
    if not host_control.request_reboot():
        raise HTTPException(status_code=500, detail="Could not submit reboot request.")
    return {"status": "rebooting"}


@router.get("/system/ssh")
async def get_ssh_status() -> dict[str, Any]:
    """SSH availability and current state for the Settings toggle.

    ``supported`` is true only on a Pi appliance (the privileged helper is
    installed); the toggle is hidden otherwise. ``enabled`` reflects whether
    sshd is running now (null if it couldn't be determined).
    """
    from openavc import host_control
    return host_control.ssh_status()


@router.post("/system/ssh")
async def set_ssh_state(request: Request) -> dict[str, Any]:
    """Enable or disable SSH on a Pi appliance.

    SSH ships off; enabling it allows logging in as the ``openavc`` user with
    the admin password. The root helper performs the change; this waits briefly
    for its result. Returns ``{success, enabled, pending, error}`` — ``pending`` means
    the change was submitted but unconfirmed before the timeout.
    """
    from openavc import host_control
    if not host_control.helper_available():
        raise HTTPException(status_code=501, detail="SSH control is not available on this deployment.")
    try:
        body = await request.json()
    except (ValueError, TypeError):
        body = {}
    if not isinstance(body, dict) or "enabled" not in body:
        raise HTTPException(status_code=422, detail="Missing 'enabled' boolean.")
    enabled = bool(body["enabled"])
    result = await host_control.set_ssh(enabled)
    # host_control speaks the privileged helper's on-disk protocol ("ok"); the
    # API surface says "success" for command-style calls that can fail at 200.
    return {
        "success": bool(result.get("ok")),
        "error": result.get("error", ""),
        "pending": bool(result.get("pending")),
        "enabled": enabled,
    }
