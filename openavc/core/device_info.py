"""What the device manager says about its devices: one device's details
(``get_device_info``), its settings with their current values
(``get_device_settings``), the listing (``list_devices``), where each running
device connects now (``dialled_record``, ``connections_now``), each connected
one (``live_connections``) and, less the simulated ones, the ports held on the
network (``network_connections``). Read-only: nothing here changes a device.

Every device is described from its record (core/device_records.py), which
says its state: running, disabled, waiting for its driver to be installed
(orphaned, core/device_orphans.py), its driver could not set it up
(core/device_build_failures.py), or its settings could not be worked out.
Each answer carries that state as ``status``. Product hardware adds its own
facts on top (installed equipment).

The rule worth knowing before changing it: where a device connects is read from
the record the manager was handed (``core.device_config.connection_of``). A
device's details name the address that record sets, never the live driver's
host, which a running simulation points at 127.0.0.1. Where it connects now is
that record with the simulation redirect on its driver laid over it
(``device_config.dialled_record``, the redirect from ``simulation_redirect``),
which names the simulator on this computer while a simulation points the
device there. The one exception is the URL of a link action, built from the
driver's own config with its real address put back.
And an orphan's details are the one answer that carries its raw config, which
reaches the cloud AI, so every login in it is masked.

Mixed into DeviceManager (core/device_manager.py).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TYPE_CHECKING

from openavc.core.device_config import DeviceConnection, connection_of, dialled_record
from openavc.core.device_errors import DeviceNotFoundError
from openavc.core.device_logins import SAVED_LOGIN, login_fields, marked_fields
from openavc.core.device_records import ORPHANED, RUNNING, DeviceRecord
from openavc.core.installed_equipment import is_product_hardware_id, missing_driver_reason
from openavc.drivers.refused_files import refusal_for
from openavc.drivers.setting_model import read_setting
# The device.<id>.<key> keys the device manager writes for every device: the
# driver contract's list, where a reply rule's gate reads it too. The AI's
# property check and the device audit import it from here.
from openavc.drivers.spec import PLATFORM_DEVICE_KEYS as PLATFORM_DEVICE_KEYS
from openavc.drivers.spec import transport_has
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
_SUMMARY_KEYS = ("id", "name", "driver", "status", "connected", "connection")


def _summary_without_driver(record: DeviceRecord) -> dict[str, Any]:
    """The summary of a device no driver runs: its state, never connected,
    and where it would connect (none when its settings could not be worked
    out). ``enabled`` is there, False, only when the project turns it off."""
    config = record.config
    summary = {
        "id": record.device_id,
        "name": config.get("name", record.device_id),
        "driver": config.get("driver", ""),
        "status": record.status,
        "connected": False,
        "connection": connection_of(config).address() if record.resolved else None,
    }
    if config.get("enabled", True) is False:
        summary["enabled"] = False
    return summary


def _orphan_summary(record: DeviceRecord) -> dict[str, Any]:
    """The summary of a device waiting for its driver, and why: the sentence
    (``orphan_reason``), and, only when the driver's file is installed but did
    not load, its reasons alone (``driver_load_error``)."""
    driver_id = record.config.get("driver", "")
    summary = {
        **_summary_without_driver(record),
        "orphaned": True,
        "orphan_reason": missing_driver_reason(driver_id),
    }
    refusal = None if is_product_hardware_id(driver_id) else refusal_for(driver_id)
    if refusal is not None:
        summary["driver_load_error"] = refusal.detail
    return summary


def _orphan_config(config: dict[str, Any]) -> dict[str, Any]:
    """An orphaned device's config with every credential masked by name, and
    each login saved on this system shown as the marker rather than masked."""
    masked = _redact_config(config)
    return {k: (SAVED_LOGIN if config.get(k) == SAVED_LOGIN else v) for k, v in masked.items()}


class DeviceInfoMixin:
    """The device details, its settings, the listing and the live connections.

    Holds no state of its own. From its host (DeviceManager) it reads
    ``state``, ``installed_equipment``, ``real_connection``,
    ``simulation_redirect``, ``_devices``, ``_device_configs`` and
    ``_detected_web_ui_urls`` (core/device_web_ui.py), and asks
    ``_live_driver``, ``device_record`` and ``device_records``
    (core/device_records.py).
    """

    state: StateStore
    installed_equipment: Any
    real_connection: Callable[[str], dict[str, Any] | None] | None
    simulation_redirect: Callable[[str], dict[str, Any] | None] | None
    _devices: dict[str, BaseDriver]
    _device_configs: dict[str, dict[str, Any]]
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

    def get_device_settings(self, device_id: str) -> dict[str, Any]:
        """Each device setting's declaration, with the state variable its
        value is read back from (``state_key``, as ``setting_model`` reads
        it: never empty) and its current value there."""
        driver = self._live_driver(device_id)

        settings_def = driver.DRIVER_INFO.get("device_settings", {})
        result: dict[str, Any] = {}
        for key, setting in settings_def.items():
            state_key = read_setting(key, setting).state_key
            result[key] = {
                **setting,
                "state_key": state_key,
                "current_value": driver.get_state(state_key),
            }
        return result

    def _device_info(self, device_id: str) -> dict[str, Any]:
        """Build metadata for a device in any state (its record)."""
        record = self.device_record(device_id)
        if record is None:
            raise DeviceNotFoundError(f"Device '{device_id}' not found")
        if record.status == ORPHANED:
            config = record.config
            return {
                **_orphan_summary(record),
                "state": self.state.get_namespace(f"device.{device_id}"),
                "commands": {},
                "command_confirms": {},
                "driver_info": {},
                # Redact credentials: this is the only get_device_info branch
                # that returns raw connection config, and it reaches the cloud
                # AI. A missing driver must not leak the device's password /
                # API key (see _redact_config). A login saved on this system
                # reads as the marker, which is what the project holds.
                "config": _orphan_config(config.get("config", {})),
            }

        if record.status != RUNNING:
            # Disabled, or its driver or its settings failed: why is in its
            # state (offline_reason, offline_detail).
            return {
                **_summary_without_driver(record),
                "state": self.state.get_namespace(f"device.{device_id}"),
                "commands": {},
                "command_confirms": {},
                "driver_info": {},
            }

        from openavc.drivers.actions import resolve_device_actions
        from openavc.drivers.command_model import command_confirms

        driver = self._devices[device_id]
        config = self._device_configs.get(device_id, {})
        info = {
            "id": device_id,
            "name": config.get("name", device_id),
            "driver": config.get("driver", ""),
            "status": RUNNING,
            "connected": driver.get_state("connected"),
            "connection": connection_of(config).address(),
            "state": self.state.get_namespace(f"device.{device_id}"),
            "commands": driver.DRIVER_INFO.get("commands", {}),
            # What each command asks before a person sends it from Send
            # Command, read once here so the page shows, never decides.
            "command_confirms": command_confirms(driver.DRIVER_INFO.get("commands")),
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
        # A product describing itself (drivers/equipment.py): what of its
        # description OpenAVC reads past or works around, for the device page.
        model_notices = getattr(driver, "model_notices", ())
        if model_notices:
            info["model_notices"] = list(model_notices)
        return info

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

    def dialled_record(self, device_id: str) -> dict[str, Any] | None:
        """The record a running device's driver dials now, or None when no
        driver runs it: the record this manager was handed, with the
        simulation redirect on that driver laid over it
        (``device_config.dialled_record``). The two are of one generation,
        since the redirect is read off the driver that record built."""
        record = self._device_configs.get(device_id)
        if record is None or device_id not in self._devices:
            return None
        redirect = self.simulation_redirect
        return dialled_record(record, redirect(device_id) if redirect is not None else None)

    def connections_now(self) -> list[DeviceConnection]:
        """Where each device a driver runs connects now, connected or not:
        ``connection_of`` over :meth:`dialled_record`. A device a running
        simulation points at the simulator is named there, ``simulated``."""
        out: list[DeviceConnection] = []
        for device_id in self._devices:
            record = self.dialled_record(device_id)
            if record is not None:
                out.append(connection_of(record))
        return out

    def live_connections(self) -> list[DeviceConnection]:
        """Where each device whose driver is connected right now connects
        (:meth:`connections_now`): a simulated one at the simulator. A device
        whose transport dials no address (serial, one that sends through a
        bridge with no connection of its own) and one with no network address
        are left out.
        """
        return [
            conn for conn in self.connections_now()
            if self._devices[conn.device_id].get_state("connected")
            and transport_has(conn.transport, "addressed") and conn.host
            and isinstance(conn.port, int)
        ]

    def network_connections(self) -> list[DeviceConnection]:
        """The ports connected devices hold on the network right now:
        :meth:`live_connections` less every device connected to the simulator,
        which holds nothing at its own address. A Discovery scan connects to
        none of these ports (``discovery/port_guard.py``) and identifies the
        device found at one from its driver; a simulated device's address is
        scanned as if it were not in the project.
        """
        return [conn for conn in self.live_connections() if not conn.simulated]

    def list_devices(self) -> list[dict[str, Any]]:
        items = self._list_devices()
        if self.installed_equipment:
            for item in items:
                item.update(self.installed_equipment.information(item["id"]))
        return items

    def _list_devices(self) -> list[dict[str, Any]]:
        """Every device the system has, each with its state (its record)."""
        return [self._summary_of(record) for record in self.device_records()]

    def _summary_of(self, record: DeviceRecord) -> dict[str, Any]:
        if record.status == RUNNING:
            return self._live_summary(record.device_id)
        if record.status == ORPHANED:
            return _orphan_summary(record)
        return _summary_without_driver(record)

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
            return {"id": device_id, "name": device_id, "status": RUNNING, "connected": False}
