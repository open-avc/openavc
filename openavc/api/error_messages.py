"""
User-friendly error message mapping.

Maps common Python exceptions to actionable messages that AV integrators
can understand and act on, instead of raw tracebacks.
"""

from __future__ import annotations

import errno
import re
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from openavc.core.project_loader import ProjectConfig
    from openavc.core.state_store import StateStore


def device_error_label(
    device_id: str, state: StateStore, project: ProjectConfig | None = None,
    *, exc: Exception | None = None,
) -> str:
    """Use the configured name even after disabling removes live device state."""
    from openavc.core.device_manager import DeviceNotFoundError

    for device in getattr(project, "devices", ()):
        if device.id == device_id and device.name:
            return str(device.name)
    fallback = "The requested equipment" if isinstance(exc, DeviceNotFoundError) else device_id
    return str(state.get(f"device.{device_id}.name") or fallback)


def friendly_error(exc: Exception, device: str = "", host: str = "") -> str:
    """
    Convert a Python exception to a user-friendly error message.

    Args:
        exc: The exception to translate.
        device: Optional device name/ID for context.
        host: Optional host/IP for context.

    Returns:
        A human-readable error string with actionable guidance.
    """
    device_label = f" '{device}'" if device else ""
    host_label = host or "the device"

    # Connection refused
    if isinstance(exc, ConnectionRefusedError):
        return (
            f"Could not connect to{device_label}. "
            f"Check that the device is powered on and the IP address ({host_label}) is correct."
        )

    # Timeout
    if isinstance(exc, (
        TimeoutError, asyncio.TimeoutError,
        httpx.ConnectTimeout, httpx.ReadTimeout, httpx.WriteTimeout,
    )):
        target = f"{device} ({host})" if device and host else device or host or "The device"
        return (
            f"{target} did not respond in time. "
            "Check its power, network connection and configured port, then retry."
        )

    # Connection reset / broken pipe
    if isinstance(exc, (ConnectionResetError, BrokenPipeError)):
        return (
            f"Lost connection to{device_label}. "
            "The device closed the connection unexpectedly. It may have rebooted or the network dropped."
        )

    # Connection aborted
    if isinstance(exc, ConnectionAbortedError):
        return (
            f"Connection to{device_label} was aborted. "
            "The device or network terminated the connection."
        )

    # General connection error (catch-all for ConnectionError subclasses)
    if isinstance(exc, ConnectionError):
        msg = str(exc)
        # A commanded restart, refused while the device is still coming back.
        # Same refusal as "not connected" and a different instruction: nobody
        # needs to go and look at anything, they need to wait. Read off the
        # attribute rather than the message so the sentence can be reworded
        # without this branch quietly stopping to match.
        restart_seconds = getattr(exc, "restart_seconds", None)
        if restart_seconds is not None:
            unit = "second" if restart_seconds == 1 else "seconds"
            return (
                f"{device} is restarting. It should be back in about "
                f"{restart_seconds} {unit}."
            ) if device else msg
        # Our own refusal is already a sentence somebody can read -- the device
        # manager raises "Device 'x' is not connected" when a command is sent
        # to a device that is not there. Wrapping that produced "Connection
        # error for 'Ceiling Projector': Device 'projector' is not connected",
        # which says the same thing twice and hands the reader an internal id
        # they have never seen. It is the commonest failure a panel reports, so
        # it is the sentence that has to be right.
        if "not connected" in msg:
            return f"{device} is not connected." if device else msg
        return f"Connection error for{device_label}: {msg}"

    # OS-level network errors
    if isinstance(exc, OSError):
        err = getattr(exc, "errno", None)

        # No route to host
        if err == errno.EHOSTUNREACH or _matches_errno(exc, 113):
            return (
                f"No route to host. Check that {host_label} is on the same network "
                "and the IP address is correct."
            )

        # Network unreachable
        if err == errno.ENETUNREACH:
            return (
                "Network unreachable. Check that the server has a valid network connection."
            )

        # Address already in use
        if err == errno.EADDRINUSE:
            return (
                f"Address already in use. Another process may be using the same port for {host_label}."
            )

        # Permission denied (file or network)
        if isinstance(exc, PermissionError):
            return (
                "Permission denied. Check credentials, file permissions, or firewall rules."
            )

        # Disk-related errors
        if err == errno.ENOSPC:
            return "Disk is full. Free up disk space and try again."

        if err == errno.EROFS:
            return "File system is read-only. Check disk mount permissions."

    # ValueError from our own code (e.g., "Device 'x' not found")
    if isinstance(exc, ValueError):
        from openavc.core.device_manager import DeviceNotFoundError

        if isinstance(exc, DeviceNotFoundError):
            target = device or "The requested equipment"
            return f"{target} is unavailable. Contact support."
        msg = str(exc)
        # Already human-readable messages from device_manager / macro_engine
        if "not found" in msg or "not connected" in msg or "blocked" in msg:
            return msg
        return f"Invalid value: {msg}"

    # RuntimeError (e.g., conditional depth limit)
    if isinstance(exc, RuntimeError):
        return str(exc)

    # Generic fallback: type name + message
    return f"Unexpected error: {exc}"


def friendly_save_error(exc: Exception) -> str:
    """
    Convert a save/file-write exception to a user-friendly message.
    """
    if isinstance(exc, PermissionError):
        return (
            "Could not save the project. Permission denied. "
            "Ensure the project directory has write permissions."
        )

    if isinstance(exc, OSError):
        err = getattr(exc, "errno", None)
        if err == errno.ENOSPC:
            return (
                "Could not save the project. The disk is full. "
                "Free up disk space and try again."
            )
        if err == errno.EROFS:
            return "Could not save the project. The file system is read-only."
        return (
            f"Could not save the project: {exc}. "
            "Check disk space and directory permissions."
        )

    return f"Could not save the project: {exc}"


def _matches_errno(exc: Exception, code: int) -> bool:
    """Check if an OSError matches a specific errno, including in the string repr."""
    if getattr(exc, "errno", None) == code:
        return True
    # Some platforms embed errno in the message string
    return bool(re.search(rf"\[Errno {code}\]", str(exc)))


# Lazy import to avoid circular dependency at module level
import asyncio  # noqa: E402
