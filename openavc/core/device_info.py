"""What the device manager says about its devices: one device's details
(``get_device_info``), the listing (``list_devices``) and where each connected
device connects (``live_connections``). Read-only: nothing here changes a device.

Three kinds of device are described: one with a live driver, one waiting for its
driver to be installed (an orphan, core/device_orphans.py) and one that is
disabled. Product hardware adds its own facts on top (installed equipment).

The rule worth knowing before changing it: where a device connects is read from
the record the manager was handed (``core.device_config.connection_of``), never
from the live driver, whose host a running simulation points at 127.0.0.1. The
one exception is the URL of a link action, built from the driver's own config
with its real address put back. And an orphan's details are the one answer that
carries its raw config, which reaches the cloud AI, so every login in it is
masked.

Mixed into DeviceManager (core/device_manager.py).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TYPE_CHECKING

from openavc.core.device_config import DeviceConnection, connection_of
from openavc.core.device_errors import DeviceNotFoundError
from openavc.core.device_logins import SAVED_LOGIN, login_fields, marked_fields
from openavc.core.installed_equipment import missing_driver_reason
from openavc.utils.log_redaction import redact_config

if TYPE_CHECKING:
    from openavc.core.state_store import StateStore
    from openavc.drivers.base import BaseDriver


# Masking a device config for a caller uses the same credential-field names as
# masking a credential out of the device log — see openavc/utils/log_redaction.py,
# which owns both. Re-exported here because get_device_info's orphaned-device
# branch is the only path that returns raw connection config, and that payload
# flows to the cloud AI (cloud/tools/device_tools.py::_get_device_info): a
# missing driver must not turn a simple get_device_info into a credential dump.
_redact_config = redact_config


# What every listing gives each device, live or not.
_SUMMARY_KEYS = ("id", "name", "driver", "connected", "connection")


def _summary_without_driver(device_id: str, config: dict[str, Any]) -> dict[str, Any]:
    """The summary of a device with no live driver (disabled, or its driver
    not installed): never connected, and where it would connect."""
    return {
        "id": device_id,
        "name": config.get("name", device_id),
        "driver": config.get("driver", ""),
        "connected": False,
        "connection": connection_of(config).address(),
    }


def _orphan_summary(device_id: str, config: dict[str, Any]) -> dict[str, Any]:
    """The summary of a device waiting for its driver, and why."""
    return {
        **_summary_without_driver(device_id, config),
        "orphaned": True,
        "orphan_reason": missing_driver_reason(config.get("driver", "")),
    }


def _orphan_config(config: dict[str, Any]) -> dict[str, Any]:
    """An orphaned device's config with every credential masked by name, and
    each login saved on this system shown as the marker rather than masked."""
    masked = _redact_config(config)
    return {k: (SAVED_LOGIN if config.get(k) == SAVED_LOGIN else v) for k, v in masked.items()}


class DeviceInfoMixin:
    """The device details, the listing and the live connections.

    Holds no state of its own. From its host (DeviceManager) it reads
    ``state``, ``installed_equipment``, ``real_connection``, ``_devices``,
    ``_device_configs``, ``_orphaned_devices`` (core/device_orphans.py) and
    ``_detected_web_ui_urls`` (core/device_web_ui.py).
    """

    state: StateStore
    installed_equipment: Any
    real_connection: Callable[[str], dict[str, Any] | None] | None
    _devices: dict[str, BaseDriver]
    _device_configs: dict[str, dict[str, Any]]
    _orphaned_devices: dict[str, dict[str, Any]]
    _detected_web_ui_urls: dict[str, str]

    def get_device_info(self, device_id: str) -> dict[str, Any]:
        """Return device metadata, status, and capabilities.

        ``connection`` is where the device connects: its resolved transport,
        host, port and bridge (``core.device_config.connection_of``). It is
        read from the record this manager was handed, never from the live
        driver, whose host a running simulation points at 127.0.0.1.
        """
        info = self._device_info(device_id)
        if self.installed_equipment:
            info.update(self.installed_equipment.information(device_id))
        return info

    def _device_info(self, device_id: str) -> dict[str, Any]:
        """Build metadata for active, disabled and missing-driver devices."""
        # Check if orphaned first
        if device_id in self._orphaned_devices:
            config = self._orphaned_devices[device_id]
            return {
                **_orphan_summary(device_id, config),
                "state": self.state.get_namespace(f"device.{device_id}"),
                "commands": {},
                "driver_info": {},
                # Redact credentials: this is the only get_device_info branch
                # that returns raw connection config, and it reaches the cloud
                # AI. A missing driver must not leak the device's password /
                # API key (see _redact_config). A login saved on this system
                # reads as the marker, which is what the project holds.
                "config": _orphan_config(config.get("config", {})),
            }

        driver = self._devices.get(device_id)
        if driver is None:
            # A disabled device
            if device_id not in self._device_configs:
                raise DeviceNotFoundError(f"Device '{device_id}' not found")
            return {
                **_summary_without_driver(device_id, self._device_configs[device_id]),
                "state": self.state.get_namespace(f"device.{device_id}"),
                "commands": {},
                "driver_info": {},
            }

        from openavc.drivers.actions import resolve_device_actions

        config = self._device_configs.get(device_id, {})
        return {
            "id": device_id,
            "name": config.get("name", device_id),
            "driver": config.get("driver", ""),
            "connected": driver.get_state("connected"),
            "connection": connection_of(config).address(),
            "state": self.state.get_namespace(f"device.{device_id}"),
            "commands": driver.DRIVER_INFO.get("commands", {}),
            # Quick Actions strip: driver-declared actions resolved (quick_actions
            # sugar folded in). The IDE filters by visible_when + availability.
            # Link (Open Web UI) URLs substitute {host}/{port} from the driver's
            # own config — the connection-merged dict it connects with. The
            # project-level entry here nests that under "config" and has no
            # host at its top level, so it can't substitute anything.
            "actions": resolve_device_actions(
                driver.DRIVER_INFO,
                self._link_config(device_id, driver),
                detected_web_ui_url=self._detected_web_ui_urls.get(device_id),
            ),
            "driver_info": driver.DRIVER_INFO,
            # The settings whose login is marked saved on this system and is
            # not: the ones the device page asks for under "Needs login".
            # Names only, never a value.
            "needs_login": marked_fields(driver.config),
        }

    def _link_config(self, device_id: str, driver: BaseDriver) -> dict[str, Any]:
        """The config a link action's URL is built from: the driver's own,
        with its real address put back while a simulation holds the device.
        Redirected, its host is this computer's loopback address and its port
        the simulator's, so a link built from them opens the simulator, or
        OpenAVC itself on 8080, instead of the equipment's page."""
        # The driver holds its logins filled in, and a link's URL is shown on
        # the device page and sent to the cloud AI, so a placeholder naming a
        # login is left as written.
        logins = login_fields(str(self._device_configs.get(device_id, {}).get("driver", "")))
        config = {k: v for k, v in (driver.config or {}).items() if k not in logins}
        real = self.real_connection(device_id) if self.real_connection is not None else None
        if not real:
            return config
        merged = dict(config)
        for key, value in real.items():
            if value is None:
                merged.pop(key, None)
            else:
                merged[key] = value
        return merged

    def live_connections(self) -> list[DeviceConnection]:
        """Where each device whose driver is connected right now connects.

        Read from the record this manager was handed (``connection_of``), as
        :meth:`get_device_info` is, so a running simulation, which points the
        live driver at 127.0.0.1, still names the device's own address. A
        serial device, one that sends through a bridge with no connection of
        its own, and one with no network address are left out. A Discovery
        scan connects to none of these (``discovery/port_guard.py``).
        """
        out: list[DeviceConnection] = []
        for device_id, driver in self._devices.items():
            record = self._device_configs.get(device_id)
            if record is None or not driver.get_state("connected"):
                continue
            conn = connection_of(record)
            if conn.transport in ("serial", "bridge") or not conn.host:
                continue
            if not isinstance(conn.port, int):
                continue
            out.append(conn)
        return out

    def list_devices(self) -> list[dict[str, Any]]:
        items = self._list_devices()
        if self.installed_equipment:
            for item in items:
                item.update(self.installed_equipment.information(item["id"]))
        return items

    def _list_devices(self) -> list[dict[str, Any]]:
        """List all devices with summary info (including orphaned and disabled)."""
        live = [self._live_summary(device_id) for device_id in self._devices]
        orphans = [
            _orphan_summary(device_id, config)
            for device_id, config in self._orphaned_devices.items()
            if device_id not in self._devices
        ]
        # What is left has no driver because it is disabled.
        listed = set(self._devices) | set(self._orphaned_devices)
        disabled = [
            {**_summary_without_driver(device_id, config), "enabled": False}
            for device_id, config in self._device_configs.items()
            if device_id not in listed
        ]
        return live + orphans + disabled

    def _live_summary(self, device_id: str) -> dict[str, Any]:
        """A device with a live driver, as the listing gives it: the summary,
        and its command names so callers need no get_device_info per device.
        One whose info cannot be read is listed by its bare id, offline."""
        try:
            info = self.get_device_info(device_id)
            entry = {key: info[key] for key in _SUMMARY_KEYS}
            if info.get("commands"):
                entry["commands"] = list(info["commands"].keys())
            return entry
        except Exception:
            return {"id": device_id, "name": device_id, "connected": False}
