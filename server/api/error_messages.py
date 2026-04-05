"""
User-friendly error message mapping.

Maps common Python exceptions to actionable messages that AV integrators
can understand and act on, instead of raw tracebacks.
"""

from __future__ import annotations

import errno
import re


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
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return (
            f"Connection timed out for{device_label}. "
            f"The device at {host_label} is not responding. "
            "Check the network connection and that the correct port is configured."
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
        return f"Connection error for{device_label}: {exc}"

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
