"""The AI's driver tools: what is installed, a driver's definition, writing a
YAML definition, and sending raw text to a device while building one.

Each tool is a door the Programmer already has, and calls that door's own
code rather than a copy of it, answering in the AI's shape (an ``{"error":
...}`` for a refusal, never an HTTP status): get_installed_drivers is Drivers
> Installed (``installed_drivers``); get_driver_definition, create and
update are the Driver Builder's read, create and replace
(``served_definition``, ``create_definition``, ``replace_definition``: the
contract check, the platform floor, the file written, the replaced copy
taken away, the registration, the devices on the driver built again,
Discovery and the announcement, all holding the driver's id); and
test_driver_command is the Builder's raw TCP probe (``raw_tcp_probe``).

Two driver formats reach these doors. A YAML driver is a ``.avcdriver`` file
and the AI may read, create and edit it; a Python driver is a loaded class
with no definition file, so ``get_driver_definition`` answers its loaded
surface marked ``editable: false``, as it does a YAML driver still loaded
after its file was removed, which it says is one. The marks a read adds (``READ_MARKS``)
are dropped before a save, so a definition the AI read saves back as it is.

A definition the AI reads carries its file's revision (``_revision``), and its
update is held to it, as the Builder's save is: a definition read before
another door changed the driver is refused instead of undoing that change, and
one that carries no revision is refused too, so the AI reads the driver before
it changes it. Create and update answer the ``_revision`` they stored, which
the next update sends back.
Create and update also answer with the checker's issue records, the ones the
Driver Builder's Validate shows (errors and warnings, each with its place): a
refused draft names each error, and a saved one the warnings it was saved
with, which never refuse it.

``test_driver_command`` never opens a port a connected project device holds
(``discovery/port_guard.py``): a device that serves one connection at a time
gives the port to the newest one, and its driver keeps a socket that never
answers again.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import partial
from typing import Any

from fastapi import HTTPException

from openavc.drivers.refused_files import not_loaded_sentence
from openavc.drivers.registry import list_registered_drivers
from openavc.utils.logger import get_logger

#: The keys get_driver_definition adds to a driver's surface, which are not
#: the driver's: a save drops them.
READ_MARKS = ("format", "editable", "note")

# The raw command's fields, read as the Builder's probe reads them.
_PROBE_FIELDS = ("host", "port", "command_string", "delimiter", "timeout")


def _marked(surface: dict[str, Any], format: str, note: str | None = None) -> dict[str, Any]:
    """A driver's surface as get_driver_definition answers it (``READ_MARKS``):
    editable only when it was read from its definition file (no ``note``).
    Its ``_revision`` comes first, so a read the cloud cuts short still names
    the copy it was."""
    marks: dict[str, Any] = {"format": format, "editable": note is None}
    if note:
        marks["note"] = note
    first = {"_revision": surface["_revision"]} if "_revision" in surface else {}
    return {**first, **surface, **marks}


def _loaded_surface(driver_id: str) -> dict[str, Any] | None:
    """A registered driver no definition file serves, as get_driver_definition
    answers it: its loaded surface, marked by the class's kind. A declarative
    driver here is one whose file is gone; only a Python class is Python."""
    from openavc.drivers.configurable import ConfigurableDriver
    from openavc.drivers.registry import get_driver_class

    surface = next((d for d in list_registered_drivers() if d.get("id") == driver_id), None)
    if surface is None:
        return None
    if issubclass(get_driver_class(driver_id), ConfigurableDriver):
        return _marked(surface, "avcdriver", (
            f"'{driver_id}' is loaded, but its .avcdriver definition file is no longer in the "
            "driver folder, so there is no definition to read or update. Everything below is "
            "the loaded driver's surface. create_driver_definition saves a new definition "
            "under this id."
        ))
    return _marked(surface, "python", (
        f"'{driver_id}' is a Python-format driver, so it has no declarative "
        ".avcdriver definition file and no raw protocol strings, response "
        "parsers or polling block to read. Everything below is introspected "
        "from the loaded driver class and is the same surface list_drivers "
        "returns. It cannot be edited with update_driver_definition, and it "
        "must not be re-authored as a YAML definition; a definition sharing "
        "this id would shadow the working driver."
    ))


def _drafted(definition: dict[str, Any]) -> dict[str, Any]:
    """A definition the AI sent, as the save takes it: without the marks its
    read added, or the listing's own keys."""
    from openavc.api.routes.driver_definitions import _strip_listing_decorations

    return _strip_listing_decorations(
        {key: value for key, value in definition.items() if key not in READ_MARKS}
    )


def _issues(definition: dict) -> list[dict[str, str]]:
    """Every issue the checker finds in a draft, errors and warnings, each
    with its place: the records the Driver Builder's Validate answers
    (``validate_driver_issues``), strict because the AI is authoring here,
    with an id the library already serves kept, as the save keeps it."""
    from openavc.api.routes.driver_definitions import _get_driver_dirs
    from openavc.drivers.driver_file_name import id_installed
    from openavc.drivers.driver_loader import validate_driver_issues

    kept = id_installed(_get_driver_dirs(), definition.get("id"))
    return validate_driver_issues(definition, strict=True, keeps_id=kept)


def _refused(
    refused: HTTPException, issues: list[dict[str, str]], changed_elsewhere: str
) -> dict[str, Any]:
    """A refusal of the Builder's save as the AI answers it: the reasons
    beneath it when it has them, else its sentence, and every issue the
    checker found in the draft beside it. A save held to an older revision
    than the file's is ``changed_elsewhere``, with no issues: they are a
    draft's that will not be saved."""
    fields = getattr(refused, "fields", {})
    if fields.get("code") == "driver_changed":
        return {"error": changed_elsewhere}
    errors = fields.get("errors")
    answer: dict[str, Any] = {"error": "; ".join(errors) if errors else str(refused.detail)}
    if issues:
        answer["issues"] = issues
    return answer


def _saved(status: str, saved: dict[str, Any], issues: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "status": status,
        "id": saved["driver_id"],
        "issues": issues,
        "devices_reconnected": saved["devices_reconnected"],
        "_revision": saved["revision"],
    }


def _changed_elsewhere(driver_id: str, read_at: Any) -> str:
    """Why the AI's update of ``driver_id`` was held back: the definition it
    sent was read before the file changed, or carried no revision at all."""
    if read_at:
        return (
            f"'{driver_id}' was changed after you read it, in the Driver Builder or another "
            "session, so your update was not saved. Read it again with get_driver_definition "
            "and make your change to that copy."
        )
    return (
        f"Read '{driver_id}' with get_driver_definition first, and send back the definition "
        "it answers with your change made to it: its _revision says which copy you changed."
    )


def _no_id(definition: Any) -> dict[str, str] | None:
    driver_id = definition.get("id") if isinstance(definition, dict) else None
    if isinstance(driver_id, str) and driver_id:
        return None
    return {"error": "Driver definition must have an 'id' field"}


async def _through_the_builder(
    door: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
    status: str,
    sent: Any,
    changed_elsewhere: str = "",
) -> dict[str, Any]:
    """A definition the AI sent, saved through one of the Driver Builder's
    save doors (``door``) and answered in the AI's shape, with the checker's
    records for the draft beside the outcome."""
    missing = _no_id(sent)
    if missing:
        return missing
    definition = _drafted(sent)
    issues = _issues(definition)
    try:
        saved = await door(definition)
    except HTTPException as refused:
        return _refused(refused, issues, changed_elsewhere)
    return _saved(status, saved, issues)


def _not_sent(error: str) -> dict[str, Any]:
    return {"success": False, "error": error, "response": None}


def _probe(input: dict) -> Any:
    """The raw command as the Builder's probe takes it: TCP, every field the
    AI left out at the probe's default, a field the probe cannot take a
    ValidationError naming it."""
    from openavc.api.models import TestCommandRequest

    sent = {field: input[field] for field in _PROBE_FIELDS if input.get(field) is not None}
    return TestCommandRequest(**sent, transport="tcp")


class DriverToolsMixin:
    """Installed drivers, driver definitions, and the raw command test."""

    async def _list_drivers(self, input: dict) -> Any:
        return list_registered_drivers()

    async def _get_installed_drivers(self, input: dict) -> Any:
        from openavc.api.routes.drivers import installed_drivers

        if not self._get_engine():
            return {"drivers": []}
        return {"drivers": installed_drivers(self._get_driver_repo_dir())}

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
        from openavc.api.routes.driver_definitions import served_definition

        driver_id = input.get("driver_id", "")
        definition = served_definition(driver_id)
        if definition is not None:
            return _marked(definition, "avcdriver")
        loaded = _loaded_surface(driver_id)
        if loaded is not None:
            return loaded

        not_loaded = not_loaded_sentence(driver_id)
        if not_loaded:
            return {
                "error": (
                    f"{not_loaded}. Install it again from the community catalog, or "
                    "replace its file, to use it."
                )
            }
        return {
            "error": (
                f"No driver '{driver_id}' is installed. Use get_installed_drivers to see "
                "what is on this system, or search_community_drivers to find one to install."
            )
        }

    async def _create_driver_definition(self, input: dict) -> Any:
        from openavc.api.routes.driver_definitions import create_definition

        return await _through_the_builder(create_definition, "created", input.get("definition"))

    async def _update_driver_definition(self, input: dict) -> Any:
        """The Builder's replace, always held to the revision the sent
        definition was read at: one that names none names the empty
        revision, which no file has (module docstring)."""
        from openavc.api.routes.driver_definitions import replace_definition

        driver_id = input.get("driver_id", "")
        sent = input.get("definition")
        read_at = sent.get("_revision") if isinstance(sent, dict) else None
        replace = partial(replace_definition, driver_id, read_at=read_at or "")
        return await _through_the_builder(
            replace, "updated", sent, _changed_elsewhere(driver_id, read_at)
        )

    async def _test_driver_command(self, input: dict) -> Any:
        from pydantic import ValidationError

        from openavc.api.errors import validation_error_lines
        from openavc.api.routes.driver_test import raw_tcp_probe

        if not input.get("host") or not input.get("command_string"):
            return _not_sent("host and command_string are required")
        try:
            probe = _probe(input)
        except ValidationError as invalid:
            return _not_sent("; ".join(validation_error_lines(invalid.errors())))

        # A port a connected project device's driver holds is never opened
        # (see the module docstring).
        holder = await self._raw_port_holder(probe.host, probe.port)
        if holder is not None:
            return {
                **_not_sent(
                    f"{holder} is connected to {probe.host}:{probe.port} now, and a second "
                    f"connection can take the device from its driver. Send it one of its own "
                    f"commands with send_device_command instead. To send raw text, ask the "
                    f"user to disable {holder} on its device page first and enable it again after."
                ),
                "in_use_by": holder,
            }

        try:
            result = await raw_tcp_probe(probe)
        except HTTPException as refused:
            return _not_sent(str(refused.detail))
        received = result["received"]
        return {
            "success": result["success"],
            "error": result["error"],
            "response": received[0] if received else None,
        }

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
