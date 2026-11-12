"""The AI's driver tools: what is installed, a driver's definition, writing a
YAML definition, and sending raw text to a device while building one.

Two driver formats reach these doors. A YAML driver is a ``.avcdriver`` file
and the AI may read, create and edit it; a Python driver is a loaded class
with no definition file, so ``get_driver_definition`` answers its loaded
surface marked ``editable: false``, and a YAML definition is never written
over its id. A definition the AI writes is checked strictly (an undeclared
key it invented would otherwise land in a saved driver and do nothing),
saved to the user driver folder, never over a shipped built-in, registered,
and announced to the IDE.

``test_driver_command`` never opens a port a connected project device holds
(``discovery/port_guard.py``): a device that serves one connection at a time
gives the port to the newest one, and its driver keeps a socket that never
answers again.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from openavc.cloud.tools import ToolEditError
from openavc.drivers.registry import (
    is_driver_registered,
    list_registered_drivers,
    register_driver,
)
from openavc.utils.logger import get_logger


def _unescaped(value: str, field: str) -> bytes:
    """``value`` with its backslash escapes (``\\r``, ``\\n``, ``\\x0D``) as the
    bytes they stand for. A truncated one (``\\x``) is a ToolEditError naming
    the field, raised before anything is dialed."""
    try:
        return value.encode().decode("unicode_escape").encode()
    except (UnicodeDecodeError, UnicodeEncodeError) as e:
        raise ToolEditError({
            "success": False,
            "error": f"Invalid {field} escape sequence {value!r}: {e}",
            "response": None,
        }) from e


def _timeout_seconds(input: dict) -> float:
    """The raw command's response timeout: 5 when not sent, and a number sent
    as text ("5") read as one, as the scan's timeout is. Anything else is a
    ToolEditError, raised before anything is dialed."""
    value = input.get("timeout")
    if value is None:
        return 5.0
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ToolEditError({
            "success": False, "error": "timeout must be a number of seconds", "response": None,
        }) from None


def _installed_yaml_entry(filepath: Path) -> dict | None:
    """An ``.avcdriver`` in the driver folder as the listing names it: by its
    own id and name, by the file name when it does not parse, and None for a
    file that parses to something other than a mapping."""
    import yaml

    try:
        data = yaml.safe_load(filepath.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError, ValueError):
        return {"id": filepath.stem, "name": filepath.stem, "format": "avcdriver", "filename": filepath.name}
    if not isinstance(data, dict):
        return None
    return {
        "id": data.get("id", filepath.stem),
        "name": data.get("name", filepath.stem),
        "format": "avcdriver",
        "filename": filepath.name,
    }


def _installed_python_entry(filepath: Path) -> dict:
    return {
        "id": filepath.stem,
        "name": filepath.stem.replace("_", " ").title(),
        "format": "python",
        "filename": filepath.name,
    }


def _user_driver_dir(dirs: list[Path]) -> Path:
    """Where a definition the AI writes is saved: the user driver folder."""
    return dirs[1] if len(dirs) > 1 else dirs[0]


def _refuse_invalid(definition: dict) -> dict | None:
    """The validator's every complaint, strict: the AI is authoring here."""
    from openavc.drivers.driver_loader import validate_driver_definition

    errors = validate_driver_definition(definition, strict=True)
    return {"error": "; ".join(errors)} if errors else None


def _refuse_builtin(driver_id: str, source: Path | None) -> dict | None:
    """A shipped built-in lives in the read-only definitions folder and is
    never modified, as the IDE's read-only gate refuses it too."""
    from openavc.system_config import DRIVER_DEFINITIONS_DIR

    builtin_root = str(Path(DRIVER_DEFINITIONS_DIR).resolve())
    if source and str(source.resolve()).startswith(builtin_root):
        return {"error": f"Driver '{driver_id}' is a built-in and cannot be modified. Duplicate it under a new id first."}
    return None


def _refuse_taken_id(driver_id: str, new_id: str, existing: list[dict]) -> dict | None:
    """A rename onto an id another definition owns would clobber it."""
    if new_id != driver_id and any(d.get("id") == new_id for d in existing):
        return {"error": f"A driver definition with id '{new_id}' already exists"}
    return None


def _replace_definition_file(definition: dict, dirs: list[Path], old_path: Path | None) -> None:
    """Save the replacement first (an atomic temp-write and replace), then drop
    the old file only when the id, and so the file name, changed: a save that
    fails leaves the old definition in place."""
    from openavc.drivers.driver_loader import save_driver_definition

    new_path = save_driver_definition(definition, _user_driver_dir(dirs))
    if old_path and old_path.resolve() != new_path.resolve():
        old_path.unlink(missing_ok=True)


def _register_definition(definition: dict) -> None:
    from openavc.drivers.configurable import create_configurable_driver_class

    register_driver(create_configurable_driver_class(definition))


async def _announce(driver_id: str) -> None:
    from openavc.api.routes.drivers import announce_drivers_changed

    await announce_drivers_changed(driver_id)


class DriverToolsMixin:
    """Installed drivers, driver definitions, and the raw command test."""

    async def _list_drivers(self, input: dict) -> Any:
        return list_registered_drivers()

    async def _get_installed_drivers(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine:
            return {"drivers": []}
        driver_repo = self._get_driver_repo_dir()
        if not driver_repo.exists():
            return {"drivers": []}

        yaml_entries = [_installed_yaml_entry(f) for f in sorted(driver_repo.glob("*.avcdriver"))]
        python_entries = [
            _installed_python_entry(f)
            for f in sorted(driver_repo.glob("*.py"))
            if not f.name.startswith("_")
        ]
        return {"drivers": [e for e in yaml_entries if e is not None] + python_entries}

    async def _get_driver_definition(self, input: dict) -> Any:
        """The driver's surface, from its .avcdriver file where it has one.

        Two formats reach this door and only one of them is a *file*. A
        declarative driver is YAML on disk, so the whole point of the tool is
        the raw protocol strings, response parsers and polling block that
        never survive into ``DRIVER_INFO`` — and it round-trips through
        ``update_driver_definition``. A Python driver has no such file at all,
        so the file lookup misses for a driver that is installed, loaded,
        connected and serving devices. Answering that with "not found" reads
        as "not installed", which has sent callers off to reinstall, install
        something else, or author a duplicate for hardware that already works.

        So the miss falls back to the loaded class: same projection
        ``list_drivers`` returns, marked ``editable: false`` because there is
        no definition file to edit and no shape to save back. Only an id that
        is genuinely not registered gets an error, and it says so.
        """
        from openavc.drivers.driver_loader import list_driver_definitions
        driver_id = input.get("driver_id", "")
        dirs = self._get_driver_dirs()
        for d in list_driver_definitions(dirs):
            if d.get("id") == driver_id:
                d.pop("_source_file", None)
                d["format"] = "avcdriver"
                d["editable"] = True
                return d

        introspected = next(
            (d for d in list_registered_drivers() if d.get("id") == driver_id),
            None,
        )
        if introspected is not None:
            introspected["format"] = "python"
            introspected["editable"] = False
            introspected["note"] = (
                f"'{driver_id}' is a Python-format driver, so it has no declarative "
                ".avcdriver definition file and no raw protocol strings, response "
                "parsers or polling block to read. Everything below is introspected "
                "from the loaded driver class and is the same surface list_drivers "
                "returns. It cannot be edited with update_driver_definition, and it "
                "must not be re-authored as a YAML definition; a definition sharing "
                "this id would shadow the working driver."
            )
            return introspected

        return {
            "error": (
                f"No driver '{driver_id}' is installed. Use get_installed_drivers to see "
                "what is on this system, or search_community_drivers to find one to install."
            )
        }

    async def _create_driver_definition(self, input: dict) -> Any:
        from openavc.drivers.driver_loader import list_driver_definitions, save_driver_definition

        definition = input.get("definition", {})
        if not definition.get("id"):
            return {"error": "Driver definition must have an 'id' field"}

        dirs = self._get_driver_dirs()
        if any(d.get("id") == definition["id"] for d in list_driver_definitions(dirs)):
            return {"error": f"Driver definition '{definition['id']}' already exists"}

        # No YAML file for the id but something is registered under it means a
        # Python driver already owns it. Writing a definition here would load
        # over the top of working hardware, and the usual reason to try is
        # having read the introspected surface get_driver_definition returns
        # for a Python driver and mistaken it for something to save back.
        if is_driver_registered(definition["id"]):
            return {"error": (
                f"Driver '{definition['id']}' is already installed as a Python-format "
                "driver. A YAML definition sharing its id would shadow it. Pick a "
                "different id, or use the driver that is already there."
            )}

        refusal = _refuse_invalid(definition)
        if refusal:
            return refusal

        save_driver_definition(definition, _user_driver_dir(dirs))
        _register_definition(definition)
        await _announce(definition["id"])
        return {"status": "created", "id": definition["id"]}

    async def _update_driver_definition(self, input: dict) -> Any:
        from openavc.drivers.driver_loader import list_driver_definitions, restore_driver_registration

        driver_id = input.get("driver_id", "")
        definition = input.get("definition", {})
        if not definition.get("id"):
            return {"error": "Driver definition must have an 'id' field"}
        dirs = self._get_driver_dirs()

        existing = list_driver_definitions(dirs)
        match = next((d for d in existing if d.get("id") == driver_id), None)
        if match is None:
            return {"error": f"Driver definition '{driver_id}' not found"}

        source = match.get("_source_file")
        old_path = Path(source) if source else None
        new_id = definition["id"]
        refusal = (
            _refuse_builtin(driver_id, old_path)
            or _refuse_invalid(definition)
            or _refuse_taken_id(driver_id, new_id, existing)
        )
        if refusal:
            return refusal

        _replace_definition_file(definition, dirs, old_path)
        _register_definition(definition)
        if new_id != driver_id:
            # The rename removed the user file for the old id. If it was
            # overriding a shipped built-in, re-register the built-in so the
            # old id keeps working; otherwise drop the stale registration.
            restore_driver_registration(driver_id, dirs)
        await _announce(new_id)
        return {"status": "updated", "id": new_id}

    async def _test_driver_command(self, input: dict) -> Any:
        import asyncio
        from openavc.transport.tcp import TCPTransport

        host = input.get("host", "")
        port = input.get("port", 23)
        command_string = input.get("command_string", "")
        delimiter = input.get("delimiter", "\\r\\n")
        if not host or not command_string:
            return {"success": False, "error": "host and command_string are required", "response": None}

        try:
            timeout = _timeout_seconds(input)
            delimiter_bytes = _unescaped(delimiter, "delimiter")
            cmd_data = _unescaped(command_string, "command_string")
        except ToolEditError as refused:
            return refused.result

        # A port a connected project device's driver holds is never opened
        # (see the module docstring).
        holder = await self._raw_port_holder(host, port)
        if holder is not None:
            return {
                "success": False,
                "error": (
                    f"{holder} is connected to {host}:{port} now, and a second connection "
                    f"can take the device from its driver. Send it one of its own commands "
                    f"with send_device_command instead. To send raw text, ask the user to "
                    f"disable {holder} on its device page first and enable it again after."
                ),
                "response": None,
                "in_use_by": holder,
            }

        try:
            transport = await TCPTransport.create(
                host=host, port=port,
                on_data=lambda d: None, on_disconnect=lambda: None,
                delimiter=delimiter_bytes, timeout=timeout,
            )
        except ConnectionError as e:
            return {"success": False, "error": str(e), "response": None}

        try:
            response = await transport.send_and_wait(cmd_data, timeout=timeout)
            response_text = response.decode("ascii", errors="replace")
            return {"success": True, "error": None, "response": response_text}
        except asyncio.TimeoutError:
            return {"success": False, "error": "Timeout waiting for response", "response": None}
        except (OSError, ConnectionError) as e:
            return {"success": False, "error": str(e), "response": None}
        finally:
            await transport.close()

    async def _raw_port_holder(self, host: str, port: Any) -> str | None:
        """The connected project device whose driver holds ``host:port``, or
        None. The same list a Discovery scan and Test Connection leave alone
        (``port_guard.held_ports`` over ``DeviceManager.network_connections``:
        a device connected to the simulator holds nothing at its own address)."""
        from openavc.discovery.network_scanner import resolve_address
        from openavc.discovery.port_guard import held_ports

        try:
            port = int(port)
        except (TypeError, ValueError):
            return None
        engine = self._get_engine()
        connections = getattr(getattr(engine, "devices", None), "network_connections", None)
        if connections is None:
            return None
        try:
            held, _unresolved = await held_ports(connections())
        except Exception:
            get_logger(__name__).exception("Could not read the project's connected devices")
            return None
        if not held:
            return None
        conn = held.get((await resolve_address(host) or host, port))
        return conn.name if conn is not None else None
