"""Read firmware-owned update policy from the trusted host, never a project.

The process snapshots this policy at startup. Firmware provisioning must supply
the same configuration to the server and any pre-start update helper. A missing
explicit file or invalid configuration keeps generic update operations disabled.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


@dataclass(frozen=True)
class FirmwareHost:
    firmware_version: str = ""
    error: str = ""

    @property
    def guidance(self) -> str:
        if self.error:
            return (
                "OpenAVC's firmware update settings could not be read. "
                "Use the product's firmware update settings; contact product support "
                "if this message remains."
            )
        return (
            "OpenAVC is included in this product's firmware. "
            "Updates are managed through the product's firmware update settings."
        )

    def refusal(self) -> dict:
        return {"success": False, "error": self.guidance,
                "error_code": "firmware_host_invalid" if self.error else "firmware_managed",
                "update_owner": "firmware", "instructions": self.guidance}

    def status(self, current_version: str, deployment: str) -> dict:
        return {
            "current_version": current_version, "deployment_type": deployment,
            "update_owner": "firmware", "firmware_version": self.firmware_version,
            "update_guidance": self.guidance, "update_check_available": False,
            "host_configuration_valid": not self.error,
            "can_self_update": False, "update_available": "", "update_channel": "",
            "update_status": "idle", "update_progress": 0, "update_error": "",
            "staged_version": "", "deferred_version": "", "deferred_attempts": 0,
            "deferred_reason": "", "deferred_final": False,
            "rollback_available": False, "rollback_version": "",
        }


@lru_cache(maxsize=1)
def get_firmware_host() -> FirmwareHost | None:
    """Absence is vanilla; explicit but unreadable configuration never is."""
    explicit = "OPENAVC_FIRMWARE_HOST" in os.environ
    # The implicit host location is a Unix provisioning convention. Windows
    # has no such absolute path; an ordinary install there stays unchanged.
    if not explicit and os.name == "nt":
        return None
    filename = os.environ.get("OPENAVC_FIRMWARE_HOST", "/etc/openavc-firmware-host.json")
    try:
        path = Path(filename)
        if not filename or not path.is_absolute():
            raise ValueError("Use an absolute firmware host configuration path.")
        try:
            path.lstat()
        except FileNotFoundError:
            if not explicit:
                return None
            raise
        with path.open("rb") as stream:
            data = stream.read(16385)
        if len(data) > 16384:
            raise ValueError("Firmware host configuration exceeds 16 KiB.")
        value = json.loads(data)
        if (not isinstance(value, dict)
                or set(value) != {"schema", "update_owner", "firmware_version"}
                or type(value["schema"]) is not int or value["schema"] != 1
                or value["update_owner"] != "firmware"
                or not isinstance(value["firmware_version"], str)
                or not value["firmware_version"].strip()
                or any(ord(c) < 32 for c in value["firmware_version"])):
            raise ValueError("Invalid firmware host configuration.")
        return FirmwareHost(firmware_version=value["firmware_version"])
    except (OSError, ValueError) as exc:
        # Detailed diagnostics stay on the host. The UI does not expose paths
        # or echo malformed input, and it must not claim that firmware is current.
        return FirmwareHost(error=type(exc).__name__)
