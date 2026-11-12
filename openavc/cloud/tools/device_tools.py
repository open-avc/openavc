"""The AI's device tools: the project's devices and device groups, a device's
settings, its connection test and its commands.

An edit lands through ``apply_tool_edit``: the change is made to a copy of
the project (``_edit_device``, ``_append_device``, ``_remove_device``,
``_add_group``, ``_edit_group``), and a ``ToolEditError`` raised there aborts
it with the tool's answer. A device's config is stored the way every door
stores it (``core.device_config.split_connection_fields``); ``update_device``
merges what it is sent into what the device has, where the IDE replaces it,
because a generator sends only the field it means to change.

These tools never take a device login (see "device logins" below). A
driver's required field left blank is refused as the Add and Edit Device
dialogs refuse it: ``add_device`` refuses every blank one, and
``update_device`` asks only when the settings or the driver change, and then
only for a gap the edit makes (``required_config.gaps_an_edit_makes``), as
``PATCH /api/devices/{id}`` does. An older device saved before its driver
required a field still takes a new address; its answer names each required
field still blank, so the model can ask for it.
"""

from typing import Any

from openavc.cloud.state_relay import cloud_state_value
from openavc.cloud.tools import ToolEditError, apply_tool_edit
from openavc.core.device_logins import SAVED_LOGIN, login_fields, login_settings_in, marked_fields
from openavc.core.device_config import split_connection_fields
from openavc.core.device_manager import DeviceNotFoundError
from openavc.core.required_config import (
    gaps_an_edit_makes,
    merged_device_config,
    missing_for_driver,
    required_sentence,
)
from openavc.core.state_store import is_flat_primitive
from openavc.drivers.param_labels import param_label
from openavc.drivers.registry import (
    get_driver_class,
    is_driver_registered,
)
from openavc.drivers.spec import is_secret_setting
from openavc.utils.logger import get_logger


def _refuse_missing_required(driver_id: str, merged: dict, before: dict | None = None) -> None:
    """Refuse a device whose driver requires a field ``merged`` leaves blank.

    With ``before`` (the device's merged config ahead of an edit on the same
    driver), only when the edit made one of those gaps. The sentence is the
    one the Add and Edit Device dialogs show; the keys are what to send back
    in ``config``. See core/required_config.py.
    """
    if before is None:
        missing = missing_for_driver(driver_id, merged)
    else:
        missing = gaps_an_edit_makes(driver_id, before, merged)
    if missing:
        raise ToolEditError({
            "error": required_sentence([label for _key, label in missing]),
            "missing_config": [key for key, _label in missing],
        })


# --- device logins ------------------------------------------------------------
#
# A device login (the connection password, and every setting its driver marks
# secret) is saved on this system only, in the login file, and is entered on
# the device's page in the Programmer. These tools never take one: whatever
# they are sent ends up in the cloud chat history and is sent to the model on
# every later turn, and a value read back masked (``***``) or as the marker
# would otherwise be saved as the login itself. A login the driver requires is
# left waiting instead (the marker over nothing), which the device page asks
# for under Needs login and the driver refuses to dial without.


def _setting_labels(driver_id: str, keys: list[str]) -> list[str]:
    """Each setting by the name the device page shows it under."""
    cls = get_driver_class(driver_id)
    schema = (cls.DRIVER_INFO.get("config_schema") if cls is not None else None) or {}
    return [param_label(key, schema.get(key)) for key in keys]


def _joined(labels: list[str]) -> str:
    if len(labels) == 1:
        return labels[0]
    return f"{', '.join(labels[:-1])} and {labels[-1]}"


def _login_refusal(labels: list[str], *, adding: bool = False, under_edit: bool = True) -> str:
    """The answer to a login sent to a device tool."""
    one = len(labels) == 1
    what = f"{_joined(labels)} {'is a login' if one else 'are logins'}."
    it = "it" if one else "them"
    where = "on the device's page in the Programmer"
    if adding:
        return f"{what} Add the device without {it}, then enter {it} {where}, under Edit. Nothing was added."
    return f"{what} Enter {it} {where}{', under Edit' if under_edit else ''}. Nothing was changed."


def _refuse_logins(drivers: list[str], keys: list[str], *, adding: bool) -> None:
    """Refuse ``keys`` (sent in a device's config) that hold a login on any of
    ``drivers`` (the one the device ends up on first), in any form: a value,
    ``***``, the marker, or null."""
    found = {k for d in drivers for k in login_settings_in(d, keys)}
    logins = [k for k in keys if k in found]
    if logins:
        raise ToolEditError({
            "error": _login_refusal(_setting_labels(drivers[0], logins), adding=adding),
            "login_settings": logins,
        })


def _wait_for_required_logins(
    driver_id: str, protocol_config: dict, connection: dict,
) -> None:
    """Leave each login ``driver_id`` requires and nothing fills waiting:
    the marker goes into that empty field (the connection row for the
    password), so the device is saved and asks for it under Needs login."""
    from openavc.drivers.spec import CONNECTION_FIELDS

    logins = login_fields(driver_id)
    for key, _label in missing_for_driver(
        driver_id, merged_device_config(driver_id, protocol_config, connection),
    ):
        if key in logins:
            (connection if key in CONNECTION_FIELDS else protocol_config)[key] = SAVED_LOGIN


def _can_wait_for_login(device: Any) -> bool:
    """A device the device list can show under Needs login: enabled, on a loaded driver."""
    return device is not None and device.enabled and is_driver_registered(device.driver)


def _needs_login(engine: Any, device_id: str) -> dict[str, Any]:
    """What to tell the person when the device waits for a login after an
    edit (the device list's Needs login, as ``Engine.logins_needed`` reads
    it): the settings by name, and where they are entered. Empty otherwise."""
    project = getattr(engine, "project", None)
    device = next((d for d in getattr(project, "devices", None) or [] if d.id == device_id), None)
    if not _can_wait_for_login(device):
        return {}
    waiting = marked_fields(engine.resolved_device_config(device).get("config"))
    if not waiting:
        return {}
    return {
        "needs_login": waiting,
        "needs_login_note": (
            f"Needs login: enter {_joined(_setting_labels(device.driver, waiting))} on this "
            "device's page in the Programmer. It does not connect until then."
        ),
    }


def _warn_not_installed(tool: str, driver_id: str) -> None:
    """Log a driver the registry does not have; the device is saved anyway
    (its driver may not be loaded yet)."""
    if not is_driver_registered(driver_id):
        get_logger(__name__).warning(
            "%s: driver '%s' not in registry (may not be loaded yet)", tool, driver_id,
        )


def _settle_required_fields(
    driver_id: str, protocol: dict, connection: dict, before: dict | None = None,
) -> None:
    """A login ``driver_id`` requires is left waiting (the marker, written into
    ``protocol`` or ``connection``); any other required field left blank is
    refused with the Add and Edit Device dialogs' sentence, only when the edit
    made a gap if ``before`` is given (``_refuse_missing_required``)."""
    _wait_for_required_logins(driver_id, protocol, connection)
    _refuse_missing_required(driver_id, merged_device_config(driver_id, protocol, connection), before)


def _still_blank(engine: Any, device_id: str) -> dict[str, Any]:
    """The required fields the device still leaves blank after a saved edit
    (an older device saved before its driver required them), and what to do
    about them. Empty otherwise."""
    project = getattr(engine, "project", None)
    device = next((d for d in getattr(project, "devices", None) or [] if d.id == device_id), None)
    if device is None:
        return {}
    missing = missing_for_driver(
        device.driver,
        merged_device_config(device.driver, device.config, project.connections.get(device_id, {})),
    )
    if not missing:
        return {}
    one = len(missing) == 1
    it = "it" if one else "them"
    return {
        "missing_config": [key for key, _label in missing],
        "missing_config_note": (
            f"Still blank: {_joined([label for _key, label in missing])}, which its driver "
            f"requires. Ask for {it} and send {it} in config."
        ),
    }


def _has_device(project: Any, device_id: str) -> bool:
    return any(d.id == device_id for d in project.devices)


def _device_index(project: Any, device_id: str) -> int:
    for index, device in enumerate(project.devices):
        if device.id == device_id:
            return index
    raise ToolEditError({"error": f"Device '{device_id}' not found"})


def _selects_product_hardware(policy: Any, device_id: str, input: dict) -> bool:
    """The edit puts a device other than the product's own onto its hardware driver."""
    return bool(
        policy and policy.manifest
        and input.get("driver") == policy.driver_id
        and not policy.owns(device_id)
    )


async def _reassign_product_hardware(engine: Any, device_id: str, input: dict) -> dict:
    """Product hardware keeps its manufacturer connection: selected under
    another device id it is reassigned there, never given a connection."""
    if input.get("config"):
        return {"error": "Product hardware uses its manufacturer connection. Select it without connection overrides."}
    try:
        await engine.reassign_installed_equipment(device_id, name=input.get("name"), enabled=input.get("enabled"))
    except ValueError as error:
        return {"error": str(error)}
    return {"status": "updated", "device_id": device_id}


def _merge_sent_config(existing: Any, row: dict, driver_id: str, sent: dict | None) -> tuple[dict, dict]:
    """``update_device``'s config, merged into what the device has: the
    protocol config and the connections row (``row``, the device's own).

    Both halves merge (connection fields into the row, the rest into the
    protocol config, as ``split_connection_fields`` places them), and a field
    sent as null is removed, back to the driver's default. The REST editor
    replaces the protocol half because the IDE always sends the whole config;
    a generator sends the field it means to change, and replacing would read
    ``{"host": ...}`` as "and drop every other setting". Nothing is written.
    """
    raw_config = sent or {}
    _refuse_logins([driver_id, existing.driver], [str(k) for k in raw_config], adding=False)
    protocol_sent, connection_sent = split_connection_fields(raw_config)
    protocol = {**existing.config, **protocol_sent}
    connection = {**row, **connection_sent}
    return (
        {k: v for k, v in protocol.items() if v is not None},
        {k: v for k, v in connection.items() if v is not None},
    )


def _settle_on_driver(project: Any, existing: Any, driver_id: str, input: dict) -> dict:
    """The config an edit to the settings or the driver leaves, its
    connections row written, with the required fields of ``driver_id``
    settled (``_settle_required_fields``): on the device's own driver only a
    gap the edit makes is refused, judged against the config it had before."""
    row = dict(project.connections.get(existing.id, {}))
    before = None
    if driver_id == existing.driver:
        before = merged_device_config(existing.driver, existing.config, row)
    config, connection = dict(existing.config), row
    if "config" in input:
        config, connection = _merge_sent_config(existing, row, driver_id, input.get("config"))
    _settle_required_fields(driver_id, config, connection, before)
    if connection:
        project.connections[existing.id] = connection
    else:
        project.connections.pop(existing.id, None)
    return config


def _edited_record(existing: Any, input: dict, driver_id: str, config: dict) -> Any:
    """The device record after an edit, re-validated from the existing record's
    full dump rather than rebuilt from the tool input: the model allows extra
    fields, so a rebuild would drop a field a newer platform wrote, and with it
    the pending settings and the child entities' labels and settings."""
    from openavc.core.project_loader import DeviceConfig

    merged = existing.model_dump()
    merged.update({
        "driver": driver_id,
        "name": input.get("name", existing.name),
        "config": config,
        "enabled": input.get("enabled", existing.enabled),
    })
    return DeviceConfig.model_validate(merged)


def _edit_device(project: Any, device_id: str, input: dict) -> None:
    """Apply an ``update_device`` edit to ``project`` (a copy), or raise
    ToolEditError. The required fields are asked only when the settings or
    the driver change, and then only for a gap the edit makes, as PATCH
    /api/devices/{id} asks: an older device's blank field stops neither a
    rename nor a new address. Every gap is the edit's on a new driver."""
    index = _device_index(project, device_id)
    existing = project.devices[index]
    driver_id = input.get("driver", existing.driver)
    if "driver" in input and input["driver"] != existing.driver:
        _warn_not_installed("update_device", input["driver"])
    config = existing.config
    if "config" in input or "driver" in input:
        config = _settle_on_driver(project, existing, driver_id, input)
    project.devices[index] = _edited_record(existing, input, driver_id, config)


def _new_device(device_id: str, input: dict) -> tuple[Any, dict]:
    """``add_device``'s record and its connections row, or ToolEditError for
    a login sent in its config."""
    from openavc.core.project_loader import DeviceConfig

    driver_id = input.get("driver", "")
    raw_config = input.get("config") or {}
    _refuse_logins([driver_id], [str(k) for k in raw_config], adding=True)
    protocol, connection = split_connection_fields(raw_config)
    record = DeviceConfig(
        id=device_id,
        driver=driver_id,
        name=input.get("name", device_id),
        config=protocol,
        enabled=input.get("enabled", True),
    )
    return record, connection


def _append_device(project: Any, new_device: Any, connection: dict) -> None:
    """Add ``new_device`` to ``project`` (a copy) with its connections row, or
    raise ToolEditError: its id taken meanwhile, or a required field blank."""
    if _has_device(project, new_device.id):
        raise ToolEditError({"error": f"Device '{new_device.id}' already exists"})
    protocol = new_device.config
    _settle_required_fields(new_device.driver, protocol, connection)
    new_device.config = protocol
    project.devices.append(new_device)
    if connection:
        project.connections[new_device.id] = connection


def _remove_device(project: Any, device_id: str) -> None:
    """Take ``device_id`` out of ``project`` (a copy), with everything that
    names it by id alone, or raise ToolEditError."""
    from openavc.core.device_references import drop_device_from_groups
    from openavc.core.monitors import drop_monitors_for_device

    original_count = len(project.devices)
    project.devices = [d for d in project.devices if d.id != device_id]
    if len(project.devices) == original_count:
        raise ToolEditError({"error": f"Device '{device_id}' not found"})
    # Its connections row: left behind, it hands a stale host/port to any
    # device later added with the same id.
    project.connections.pop(device_id, None)
    project.monitors = drop_monitors_for_device(project.monitors, device_id)
    # Its group memberships, as the REST door drops them: a dangling member
    # can only be removed, and until it is the group calls it an offline
    # device forever.
    project.device_groups = drop_device_from_groups(project.device_groups, device_id)


def _add_group(project: Any, group_id: str, input: dict) -> None:
    """Add ``add_device_group``'s group to ``project`` (a copy), or raise ToolEditError."""
    from openavc.core.project_loader import DeviceGroup

    if any(g.id == group_id for g in project.device_groups):
        raise ToolEditError({"error": f"Group '{group_id}' already exists"})
    device_ids = input.get("device_ids", [])
    if device_ids:
        _refuse_unknown_members(project, device_ids)
    project.device_groups.append(
        DeviceGroup(id=group_id, name=input.get("name", group_id), device_ids=device_ids),
    )


def _edit_group(project: Any, group_id: str, input: dict) -> None:
    """Apply ``update_device_group``'s edit to ``project`` (a copy), or raise ToolEditError."""
    existing = next((g for g in project.device_groups if g.id == group_id), None)
    if existing is None:
        raise ToolEditError({"error": f"Group '{group_id}' not found"})
    if "device_ids" in input:
        _refuse_unknown_members(project, input["device_ids"])
        existing.device_ids = input["device_ids"]
    if "name" in input:
        existing.name = input["name"]


def _refuse_unknown_members(project: Any, device_ids: list) -> None:
    known = {d.id for d in project.devices}
    unknown = [did for did in device_ids if did not in known]
    if unknown:
        raise ToolEditError({"error": f"Device(s) not found in project: {', '.join(unknown)}"})


class DeviceToolsMixin:
    """Devices, device groups, a device's settings, its connection test and commands."""

    # ===== DEVICE TOOLS =====

    async def _list_devices(self, input: dict) -> Any:
        return self._devices.list_devices()

    async def _get_device_info(self, input: dict) -> Any:
        device_id = input.get("device_id", "")
        info = self._devices.get_device_info(device_id)
        # `commands` is the driver's own command table, and `driver_info` is
        # the whole driver record with that same table inside it. Sent once.
        driver_info = info.get("driver_info")
        if isinstance(driver_info, dict) and driver_info.get("commands") == info.get("commands"):
            info = {
                **info,
                "driver_info": {k: v for k, v in driver_info.items() if k != "commands"},
            }
        # The device's state goes as the state read tools send it.
        if isinstance(info.get("state"), dict):
            info = {**info, "state": {k: cloud_state_value(v) for k, v in info["state"].items()}}
        return info

    async def _update_device(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}
        device_id = input.get("device_id", "")
        if _selects_product_hardware(getattr(engine, "installed_equipment", None), device_id, input):
            return await _reassign_product_hardware(engine, device_id, input)

        # The devices reconcile hot-swaps the runtime device with the new
        # resolved config, and the revision bump means a stale IDE PUT gets
        # a 409 instead of silently reverting this edit.
        err = await apply_tool_edit(engine, lambda project: _edit_device(project, device_id, input))
        if err:
            return err
        return {
            "status": "updated", "device_id": device_id,
            **_needs_login(engine, device_id), **_still_blank(engine, device_id),
        }

    async def _delete_device(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}
        device_id = input.get("device_id", "")
        try:
            engine.installed_equipment.refuse_removal(device_id)
        except ValueError as error:
            return {"error": str(error)}
        # Collect impact before deleting
        impact = self._find_references("device", device_id)

        # The devices reconcile removes the runtime device and sweeps its
        # orphaned device.<id>.* state keys; the revision bump means a stale
        # tab can't resurrect the deleted device on its next save.
        err = await apply_tool_edit(engine, lambda project: _remove_device(project, device_id))
        if err:
            return err
        result: dict = {"status": "deleted", "device_id": device_id}
        if impact:
            result["impact"] = impact
        return result

    async def _add_device(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}

        device_id = input.get("id", "")
        if not device_id:
            return {"error": "Device ID is required"}
        if _has_device(engine.project, device_id):
            return {"error": f"Device '{device_id}' already exists"}

        driver_id = input.get("driver", "")
        if driver_id:
            _warn_not_installed("add_device", driver_id)
        try:
            new_device, conn_overrides = _new_device(device_id, input)
        except ToolEditError as refused:
            return refused.result

        # The devices reconcile hot-adds the runtime device from the
        # resolved (driver-defaults + connection-table) config.
        err = await apply_tool_edit(engine, lambda project: _append_device(project, new_device, conn_overrides))
        if err:
            return err

        return {"status": "created", "id": device_id, **_needs_login(engine, device_id)}

    async def _add_device_group(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}
        group_id = input.get("id", "")
        if not group_id:
            return {"error": "Group ID is required"}

        # The device_groups reconcile reloads the macro engine's group table.
        err = await apply_tool_edit(engine, lambda project: _add_group(project, group_id, input))
        if err:
            return err
        return {"status": "created", "id": group_id}

    async def _update_device_group(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}
        group_id = input.get("id", "")

        err = await apply_tool_edit(engine, lambda project: _edit_group(project, group_id, input))
        if err:
            return err
        return {"status": "updated", "id": group_id}

    async def _delete_device_group(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}
        group_id = input.get("id", "")

        def mutate(project):
            original_count = len(project.device_groups)
            project.device_groups = [g for g in project.device_groups if g.id != group_id]
            if len(project.device_groups) == original_count:
                raise ToolEditError({"error": f"Group '{group_id}' not found"})

        err = await apply_tool_edit(engine, mutate)
        if err:
            return err
        return {"status": "deleted", "id": group_id}

    async def _send_device_command(self, input: dict) -> Any:
        device_id = input.get("device_id", "")
        command = input.get("command", "")
        params = input.get("params", {})
        await self._events.emit("ai.device_command", {
            "device_id": device_id, "command": command, "params": params,
        })
        try:
            await self._devices.send_command(device_id, command, params)
        except Exception as e:
            # Worded as Send Command words it: the answer is kept in the cloud
            # chat history, and a driver's own text is not for it.
            from openavc.api.error_messages import describe_command_failure

            engine = self._get_engine()
            return {"error": describe_command_failure(
                e, device_id, command, state=self._devices.state,
                project=getattr(engine, "project", None),
                driver=self._devices.get_driver(device_id),
            ).programmer}
        return "OK"

    async def _test_device_connection(self, input: dict) -> Any:
        # The same test as the device page's Test Connection, at the address
        # the device manager dials: the driver's default port, the device's
        # config and the connections table, layered.
        from openavc.core.device_reachability import run_connection_test

        device_id = input.get("device_id", "")
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"success": False, "error": "No project loaded", "latency_ms": None}

        device = next((d for d in engine.project.devices if d.id == device_id), None)
        if device is None:
            return {"success": False, "error": f"Device '{device_id}' not found", "latency_ms": None}
        policy = getattr(engine, "installed_equipment", None)
        if policy is not None and policy.owns(device_id):
            return await policy.test_connection()
        return await run_connection_test(device, engine)

    async def _get_device_settings(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine:
            return {"error": "Engine not available"}

        device_id = input.get("device_id", "")
        if not device_id:
            return {"error": "device_id is required"}

        try:
            settings = engine.devices.get_device_settings(device_id)
            return {"device_id": device_id, "settings": settings}
        except DeviceNotFoundError:
            return {"error": f"Device '{device_id}' not found"}

    async def _set_device_setting(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine:
            return {"error": "Engine not available"}

        device_id = input.get("device_id", "")
        setting_key = input.get("setting_key", "")
        value = input.get("value")

        if not device_id or not setting_key:
            return {"error": "device_id and setting_key are required"}

        # State holds flat primitives only — reject a list/dict/other before it
        # reaches the driver (which str()-ifies it into the protocol) or the
        # state store. Shared definition, see state_store.
        if not is_flat_primitive(value):
            return {"error": "Setting value must be a string, number, boolean, or null"}

        driver = engine.devices.get_driver(device_id)
        declared = (driver.DRIVER_INFO.get("device_settings") or {}) if driver is not None else {}
        if is_secret_setting(setting_key, declared.get(setting_key)):
            definition = declared.get(setting_key)
            return {
                "error": _login_refusal([param_label(setting_key, definition)], under_edit=False),
                "login_settings": [setting_key],
            }

        try:
            await engine.devices.set_device_setting(device_id, setting_key, value)
        except Exception as e:
            # Worded as the device page words it: a refused value or an unknown
            # key names what was wrong, which the generator's next attempt
            # depends on, and a driver's own text is not for the cloud chat
            # history.
            from openavc.api.error_messages import describe_setting_failure

            return {"error": describe_setting_failure(
                e, device_id, setting_key, state=engine.state,
                project=getattr(engine, "project", None),
                driver=engine.devices.get_driver(device_id),
            )}
        return {"success": True, "device_id": device_id, "key": setting_key, "value": value}
