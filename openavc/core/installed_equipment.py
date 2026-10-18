"""Manufacturer-installed equipment, composed with integrator-owned projects."""

import copy
import hashlib
import json
import os
from pathlib import Path

def model_driver_id(model):
    """Model registration identity, independent of the physical unit and firmware build."""
    if not isinstance(model, str) or not model.strip():
        raise InstalledEquipmentError("The product firmware has no product hardware model configured.")
    return "openavc_builtin_" + hashlib.sha256(model.encode("utf-8")).hexdigest()



def validate_backup(path):
    policy = InstalledEquipment.from_environment()
    if not policy.manifest:
        return
    from openavc.core.project_loader import ProjectConfig
    from openavc.core.project_migration import migrate_project
    import zipfile

    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as archive:
            if "project.avc" not in archive.namelist():
                raise ValueError("Backup ZIP does not contain project.avc")
            raw = archive.read("project.avc")
    elif path.name.endswith(".avc.bak"):
        raw = path.read_bytes()
    else:
        raise ValueError(f"Unrecognized backup format: {path.name}")
    data, _ = migrate_project(json.loads(raw))
    policy.compose(ProjectConfig(**data), for_load=True)


class InstalledEquipmentError(ValueError):
    pass


def project_completion_wait(config, *, strict=True):
    """The product hardware's Wait for completion, the one setting its project
    keeps in ``config`` beside the model. None when unset. A value that is not
    a number of seconds in range is refused, or dropped when not ``strict``."""
    from openavc.drivers.equipment import COMPLETION_WAIT_MAX

    wait = config.get("completion_wait")
    if wait is None:
        return None
    if isinstance(wait, bool) or not isinstance(wait, (int, float)) or not 0 <= wait <= COMPLETION_WAIT_MAX:
        if not strict:
            return None
        raise InstalledEquipmentError(
            f"Wait for completion must be a number of seconds from 0 to {COMPLETION_WAIT_MAX}."
        )
    return wait


def _with_completion_wait(config, *sources):
    for source in sources:
        wait = project_completion_wait(source, strict=False)
        if wait is not None:
            return {**config, "completion_wait": wait}
    return config


class InstalledEquipment:
    def __init__(self, manifest=None):
        self.manifest = copy.deepcopy(manifest)
        self.project_provider = None
        if self.manifest and not (
            isinstance(self.manifest.get("integration"), str)
            and self.manifest["integration"].strip()
        ):
            raise InstalledEquipmentError("The product firmware has no product hardware model configured.")

        self.driver_id = model_driver_id(self.manifest["integration"]) if self.manifest else None

    @classmethod
    def from_environment(cls):
        path = os.environ.get("OPENAVC_EQUIPMENT_MANIFEST")
        return cls(json.loads(Path(path).read_text()) if path else None)

    def owns(self, device_id):
        if not self.manifest:
            return False
        project = self.project_provider() if self.project_provider else None
        if project is None:
            return device_id == self.manifest["device_id"]
        matches = [device for device in project.devices if self.matches(device)]
        return len(matches) == 1 and device_id == matches[0].id

    def matches(self, device):
        return bool(self.manifest and device.driver == self.driver_id
                    and device.config.get("integration") == self.manifest["integration"])

    def describe_missing_reference(self, driver_id, project):
        """Explain a saved reference; this confers no hardware ownership or trust."""
        devices = [device for device in project.devices if device.driver == driver_id] if project else []
        if not devices:
            return None
        for device in devices:
            model = device.config.get("integration")
            if not isinstance(model, str) or not model.strip() or model_driver_id(model) != driver_id:
                return None
        return {"model": devices[0].config["integration"],
                "device_names": [device.name or device.id for device in devices]}

    def validate_edit(self, current, proposed, *, reassigned=None):
        if not self.manifest or current is None:
            return
        for before in current.devices:
            if not self.matches(before):
                continue
            if reassigned == before.id:
                if sum(self.matches(device) for device in proposed.devices) != 1:
                    raise InstalledEquipmentError("The project must retain its product hardware.")
                continue
            after = next((d for d in proposed.devices if d.id == before.id), None)
            if after is None or not self.matches(after):
                raise InstalledEquipmentError(
                    "This is the hardware of the product running OpenAVC. It cannot be removed or assigned a different driver. Disable it to stop control in this project."
                )

    def reassign(self, project, source_id, target_info, scripts_dir):
        """Move an unused built-in reference onto existing programming's ID.

        The source ID is retained, including references inside scripts. The
        receiving reference must be unused: no reference or hardware setting
        is discarded to resolve a collision. Called only by the explicit
        reassignment operation while the engine holds its project lock.
        """
        from openavc.core.device_references import find_device_references, find_script_references
        from openavc.drivers.base import normalize_and_validate_command_params, validate_device_setting_value

        source = next((device for device in project.devices if device.id == source_id), None)
        target = next((device for device in project.devices if self.matches(device)), None)
        if source is None or target is None or source is target:
            raise InstalledEquipmentError("Select an existing device to use this product's hardware.")
        refs = find_device_references(project, target.id)
        scripts = find_script_references(project, scripts_dir, target.id)
        monitors = [monitor for monitor in project.monitors
                    if monitor.key == f"device.{target.id}" or monitor.key.startswith(f"device.{target.id}.")]
        variables = [variable for variable in project.variables if variable.source_key and (
            variable.source_key == f"device.{target.id}" or variable.source_key.startswith(f"device.{target.id}."))]
        if refs or scripts or monitors or variables or target.pending_settings or target.child_entities:
            raise InstalledEquipmentError(
                f"'{target.name}' already has programming or saved settings. Keep using that device, "
                "or move its programming and settings before assigning another device to the product hardware."
            )
        if not target_info.get("connected"):
            raise InstalledEquipmentError("The connection to this product's hardware is unavailable. Check the product's status, then try again.")
        info = target_info.get("driver_info", {})
        commands = info.get("commands", {})
        groups = {group.id for group in project.device_groups if source_id in group.device_ids}

        def check_commands(value):
            if isinstance(value, list):
                for item in value:
                    check_commands(item)
            elif isinstance(value, dict):
                group = value.get("group")
                if value.get("device") == source_id or (isinstance(group, str) and group in groups):
                    command = value.get("command")
                    if isinstance(command, str) and command:
                        if command not in commands:
                            raise InstalledEquipmentError(f"The product hardware does not provide command '{command}'. Select compatible equipment or update that programming first.")
                        rules = commands[command].get("params", {})
                        params = value.get("params", {})
                        # Dynamic state references resolve when the program runs.
                        # Required fields can still be checked without resolving them.
                        from openavc.drivers.base import missing_required_params
                        missing = missing_required_params(rules, params)
                        if missing:
                            raise InstalledEquipmentError(f"Command '{command}' needs parameters: {', '.join(missing)}. Update that programming before assigning this equipment.")
                        if not any(isinstance(item, str) and item.startswith("$") for item in params.values()):
                            normalize_and_validate_command_params(command, rules, params)
                for item in value.values():
                    check_commands(item)

        try:
            check_commands(project.model_dump())
        except ValueError as error:
            raise InstalledEquipmentError(str(error)) from error
        for key, value in source.pending_settings.items():
            definition = info.get("device_settings", {}).get(key)
            if definition is None:
                raise InstalledEquipmentError(f"The product hardware does not provide saved setting '{key}'. Review the saved settings before assigning it.")
            try:
                validate_device_setting_value(key, definition, value)
            except ValueError as error:
                raise InstalledEquipmentError(str(error)) from error
        for kind in source.child_entities:
            if kind not in info.get("child_entity_types", {}):
                raise InstalledEquipmentError(f"The product hardware does not provide '{kind}' channels. Review the saved channel labels before assigning it.")
        source.driver = self.driver_id
        source.config = _with_completion_wait(
            {"integration": self.manifest["integration"]}, source.config, target.config)
        project.connections.pop(source.id, None)
        project.connections.pop(target.id, None)
        project.devices.remove(target)
        return target.id

    def refuse_removal(self, device_id):
        if self.owns(device_id):
            raise InstalledEquipmentError(
                "This is the hardware of the product running OpenAVC. Remove its program "
                "bindings if you do not want to use it; the equipment cannot be uninstalled."
            )

    def register(self):
        if self.manifest:
            from openavc.drivers.equipment import EquipmentDriver
            from openavc.drivers.registry import reserve_driver

            class InstalledDriver(EquipmentDriver):
                DRIVER_INFO = {
                    **{key: value for key, value in EquipmentDriver.DRIVER_INFO.items() if key != "discovery"},
                    "id": self.driver_id,
                    "name": "Product hardware",
                    "config_schema": {},
                    "default_config": {},
                }

                async def connect(self):
                    from openavc.drivers.base import ConnectionFaultError

                    if not self.config.get("url") or not self.config.get("token"):
                        await self.disconnect()
                        raise ConnectionFaultError(
                            "The product hardware connection is not configured.",
                            code="invalid_config",
                        )
                    await super().connect()

                async def _request(self, path, payload=None):
                    from openavc.drivers.base import ConnectionFaultError

                    try:
                        return await super()._request(path, payload)
                    except ConnectionFaultError as error:
                        messages = {
                            "auth_failed": "The product hardware connection could not be authenticated.",
                            "connection_refused": "The product hardware connection is unavailable.",
                        }
                        message = messages.get(error.fault_code)
                        if message is None:
                            raise
                        raise ConnectionFaultError(message, code=error.fault_code) from error

            reserve_driver(InstalledDriver)

    def compose(self, project, *, for_load=False):
        from openavc.core.project_loader import DeviceConfig

        # Ordinary installs retain the upstream project object's identity.
        # Compose a private copy only when host equipment must be inserted.
        result = project.model_copy(deep=True) if self.manifest else project
        ids = [device.id for device in result.devices]
        if len(ids) != len(set(ids)):
            raise InstalledEquipmentError(
                "Device IDs must be unique. Remove the duplicate reference."
            )
        # A hostless server uses the normal missing-driver workflow. The
        # project keeps its IDs and bindings until the user reassigns it.
        if not self.manifest:
            return result
        for device in result.devices:
            if device.driver == self.driver_id and not self.matches(device):
                raise InstalledEquipmentError(
                    "This project uses a different product hardware model. "
                    "Open it on a matching product, or on a separate OpenAVC server to reassign the device."
                )
        host = self.manifest
        matches = [device for device in result.devices if self.matches(device)]
        if len(matches) > 1:
            raise InstalledEquipmentError("This project contains more than one reference to this product's hardware.")
        # Only loading a project may change a remote reference into this
        # physical product's local reference. Ordinary edits retain protection.
        same_unit = [device for device in result.devices
                     if device.driver == "openavc_equipment"
                     and device.config.get("reported_identity") == host["identity"]]
        if for_load and same_unit:
            if len(same_unit) != 1 or matches:
                raise InstalledEquipmentError(
                    "The project has more than one reference to this equipment. Resolve its device references before importing."
                )
            selected = same_unit[0]
            if selected.config.get("reported_model") != host["integration"]:
                raise InstalledEquipmentError(
                    "The saved equipment model does not match this product. Its programming has not been reassigned."
                )
            selected.driver = self.driver_id
            selected.config = _with_completion_wait({"integration": host["integration"]}, selected.config)
            result.connections.pop(selected.id, None)
            matches = [selected]
        found = matches[0] if matches else None
        local_id = found.id if found else host["device_id"]
        if found is None and for_load:
            occupied = set(ids) | set(result.connections)
            suffix = 2
            while local_id in occupied:
                local_id = f'{host["device_id"]}_{suffix}'
                suffix += 1
        elif found is None:
            # A plain save cannot disguise replacing the protected reference
            # as loading a new project with a coincidentally matching ID.
            found = next((d for d in result.devices if d.id == local_id), None)
        reference = DeviceConfig(
            id=local_id,
            driver=self.driver_id,
            name=host["name"],
            config={"integration": host["integration"]},
        )
        if found:
            expected = reference.model_dump()
            incoming = found.model_dump()
            expected["name"] = incoming["name"]
            if "completion_wait" in incoming["config"]:
                expected["config"]["completion_wait"] = project_completion_wait(incoming["config"])
            # These fields control this project, not the hardware mapping.
            expected["enabled"] = incoming["enabled"]
            expected["pending_settings"] = incoming["pending_settings"]
            # Channel labels belong to the project; hardware mapping does not.
            expected["child_entities"] = incoming["child_entities"]
            for entries in found.child_entities.values():
                for entry in entries.values():
                    if entry.config or entry.model_extra:
                        raise InstalledEquipmentError(
                            "Only channel labels can be changed here. Use the declared equipment settings."
                        )
            if incoming != expected or not found.name.strip():
                raise InstalledEquipmentError(
                    "The installed equipment identity and mapping cannot be changed. Use its label or declared settings."
                )
        else:
            result.devices.insert(0, reference)
        if local_id in result.connections:
            raise InstalledEquipmentError(
                "Product hardware has no editable network connection. Remove this connection override."
            )
        # A second representation of the same physical endpoint is not a peer.
        for device in result.devices:
            if (
                device.id != local_id
                and device.config.get("identity") == host["identity"]
            ):
                raise InstalledEquipmentError(
                    "This product is already in the project. Use its existing device."
                )
        return result

    def resolve(self, device):
        if not self.matches(device):
            return None
        # No project field selects code, endpoint, credentials or state storage.
        result = device.model_dump()
        result["config"] = copy.deepcopy(self.manifest["connection"])
        result["config"]["identity"] = self.manifest["identity"]
        result["config"]["integration"] = self.manifest["integration"]
        wait = project_completion_wait(device.config, strict=False)
        if wait is not None:
            result["config"]["completion_wait"] = wait
        return result

    async def test_connection(self):
        from openavc.core.installed_diagnostics import test_connection
        return await test_connection(self.manifest)

    def information(self, device_id):
        if not self.owns(device_id):
            return {}
        from openavc.drivers.equipment import COMPLETION_WAIT_DEFAULT, COMPLETION_WAIT_MAX

        return {
            "ownership": "manufacturer",
            "completion_wait": {"default": COMPLETION_WAIT_DEFAULT, "max": COMPLETION_WAIT_MAX},
            "physical_identity": self.manifest["identity"],
            "integration": self.manifest["integration"],
            "permitted_actions": [
                "rename",
                "enable",
                "disable",
                "pending_settings",
                "command",
                "settings",
                "child_labels",
                "reconnect",
                "test",
            ],
        }
