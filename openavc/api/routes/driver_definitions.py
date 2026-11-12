"""Driver-definition CRUD: the ``.avcdriver`` definitions the Driver Builder
lists, reads, checks, saves, deletes and reloads.

Create, replace and patch each refuse what is theirs to refuse (an id already
taken; a definition that is not there, a built-in, a rename onto another
driver's id), then save through one function, ``_save_definition``: the strict
contract check, the platform floor, the write to the user driver folder before
any other copy of the id is removed, the registration, the devices on it
reconnected, and the announcement to every open Programmer. A shipped built-in
in the read-only definitions folder is never written or deleted here; a user
copy that overrides one can be, and the built-in serves the id again after.

Installing, uploading and removing drivers is ``routes/drivers.py``; the floor
check and the refusal for an invalid definition live there because its import
doors ask them too.
"""

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from openavc.api._engine import _get_engine
from openavc.api.auth import authenticate_claimed_request, require_claimed_auth
from openavc.api.errors import StructuredApiError
from openavc.api.host_policy import EndpointPolicy, ProgrammerRoute, endpoint_policy
from openavc.api.models import DriverDefinitionRequest
from openavc.api.routes.drivers import (
    announce_drivers_changed,
    definition_invalid,
    enforce_declared_floor,
)
from openavc.core.host_policy import HostCategory
from openavc.drivers.registry import register_driver

router = APIRouter(route_class=ProgrammerRoute)


def _strip_listing_decorations(definition: dict) -> dict:
    """Drop the bookkeeping the listing endpoint adds to a definition.

    ``GET /driver-definitions`` decorates each definition with where it came
    from — ``source`` ("builtin"/"user"), and historically ``_source_file``.
    Those are not driver-format keys, and an editor that loaded a definition
    from that endpoint hands them straight back when it saves. The authoring
    gate is strict about undeclared keys on purpose, so without this the
    server rejects a save over a key the server itself added. Same carve-out
    the Builder's own validator makes (validateDriver.ts).
    """
    return {
        key: value
        for key, value in definition.items()
        if key != "source" and not key.startswith("_")
    }


def _id_taken(driver_id: str) -> StructuredApiError:
    """409 for a save that would give a driver an id another driver has.

    Worded as the Driver Builder's own check words it (``validateDriver.ts``),
    so the editor's header points at the ID field instead of saying it twice.
    """
    return definition_invalid(
        [f'Another driver named "{driver_id}" already exists. Choose a different ID.'],
        status_code=409,
    )


def _get_driver_dirs() -> list[Path]:
    """Get directories containing driver definitions."""
    from openavc.system_config import DRIVER_DEFINITIONS_DIR, DRIVER_REPO_DIR
    return [
        DRIVER_DEFINITIONS_DIR,
        DRIVER_REPO_DIR,
    ]


@router.get("/driver-definitions")
async def list_driver_definitions() -> dict[str, Any]:
    """List all JSON driver definitions.

    Adds a `source` field to each entry: `"builtin"` for drivers that ship
    in the platform's read-only definitions directory, `"user"` for drivers
    that live in the user/community driver_repo (created via the Driver
    Builder or installed from the community catalog). The Driver Builder
    uses this to gate edit-in-place vs. customize-a-copy.
    """
    from openavc.drivers.driver_loader import list_driver_definitions as _list
    from openavc.system_config import DRIVER_DEFINITIONS_DIR

    dirs = _get_driver_dirs()
    definitions = _list(dirs)
    builtin_root = str(Path(DRIVER_DEFINITIONS_DIR).resolve())
    for d in definitions:
        source_file = d.pop("_source_file", "")
        try:
            resolved = str(Path(source_file).resolve()) if source_file else ""
            d["source"] = "builtin" if resolved.startswith(builtin_root) else "user"
        except OSError:
            d["source"] = "user"
    return {"definitions": definitions}


@router.get("/driver-definitions/{driver_id}")
async def get_driver_definition(driver_id: str) -> dict:
    """Get a single JSON driver definition by ID."""
    from openavc.drivers.driver_loader import list_driver_definitions as _list

    definition = _current_definition(driver_id, _list(_get_driver_dirs()))
    definition.pop("_source_file", None)
    return definition


@router.post("/driver-definitions/validate")
async def validate_driver_definition_draft(
    body: Any = Body(...),
) -> dict[str, Any]:
    """Validate a driver definition without saving it.

    Answers the question the Driver Builder asks on every keystroke: what is
    wrong with this draft? The rules are the platform's own — the same
    function the save routes call, at the same ``strict`` setting — so what
    the editor shows and what a save accepts cannot disagree.

    Each issue carries a ``path`` (``commands.mute``, ``state_variables.volume``,
    ``config_schema.password``, ``responses[2]``, or ``""`` for a whole-driver
    rule) so the editor can put it on the right tab beside the right control.

    Deliberately takes the raw body rather than ``DriverDefinitionRequest``:
    that model would reject a mistyped field with a 422 before any rule ran,
    and a mistyped field is exactly what an author needs a readable message
    about. A draft that isn't a mapping comes back as one issue, not an error.
    """
    from openavc.drivers.driver_loader import validate_driver_issues

    if not isinstance(body, dict):
        return {
            "issues": [
                {
                    "severity": "error",
                    "message": "Driver definition must be a mapping",
                    "path": "",
                }
            ]
        }

    # Same carve-out the save routes make: the listing endpoint decorates a
    # definition with where it came from, and an editor that loaded one hands
    # those keys straight back.
    draft = _strip_listing_decorations(body)

    # strict: this is an authoring surface, so an undeclared key is a typo to
    # show now rather than a mystery when the save refuses it.
    return {"issues": validate_driver_issues(draft, strict=True)}


def _definition_not_found(driver_id: str) -> HTTPException:
    """404 for a definition id no file in the library declares."""
    return HTTPException(status_code=404, detail=f"Driver definition '{driver_id}' not found")


def _current_definition(driver_id: str, existing: list[dict[str, Any]]) -> dict[str, Any]:
    """The listed definition that serves ``driver_id``, or the 404."""
    current = next((d for d in existing if d.get("id") == driver_id), None)
    if current is None:
        raise _definition_not_found(driver_id)
    return current


def _refuse_built_in_edit(driver_id: str, dirs: Any) -> None:
    """Built-in drivers are read-only: editing one in place would unlink the
    shipped file from the install tree (no recovery). The Driver Builder forks
    a built-in to an editable copy with a new id instead."""
    from openavc.drivers.driver_loader import is_builtin_driver

    if is_builtin_driver(driver_id, dirs):
        raise HTTPException(
            status_code=403,
            detail="Built-in drivers are read-only. Customize a copy to edit.",
        )


def _refuse_rename_onto_another(
    new_id: str, driver_id: str, existing: list[dict[str, Any]], dirs: Any
) -> None:
    """A rename may not take an id that is already served: a built-in's is
    read-only, and any other driver's would be saved over."""
    from openavc.drivers.driver_loader import is_builtin_driver

    if new_id == driver_id:
        return
    if is_builtin_driver(new_id, dirs):
        raise HTTPException(
            status_code=403,
            detail=f"Driver id '{new_id}' belongs to a read-only built-in driver.",
        )
    if any(d.get("id") == new_id for d in existing):
        raise _id_taken(new_id)


async def _save_definition(
    driver_def: dict[str, Any], dirs: Any, *, replacing: str | None = None
) -> dict[str, Any]:
    """Check, store, register and bring up a definition the Driver Builder saves.

    The three save doors (create, replace, patch) refuse what is theirs to
    refuse first, then all come here. The contract check is strict: this is an
    authoring gate, so an undeclared key is a typo to reject now, not a mystery
    to debug later. The platform floor comes after it, so an author holding a
    broken draft still gets the errors that describe the draft; it is read off
    the definition in hand, never the stored copy, so lowering a floor that
    blocks the save is always possible, and it is checked at every save
    because a save is what registers the driver (the Builder's "import a
    definition" lands here too).

    The new file is written to the user driver folder first, replacing a
    same-named old one in place, and only then are other copies of
    ``replacing`` removed, so the driver is never absent from disk. The devices
    on it reconnect to the new class without a project reload. A rename leaves
    devices on the old id waiting: if the removed file was overriding a shipped
    built-in, the built-in is registered again so the old id keeps working,
    otherwise its registration is dropped, and those devices are retried too.
    """
    from openavc.drivers.driver_loader import (
        delete_driver_definition,
        restore_driver_registration,
        save_driver_definition,
        validate_driver_definition,
    )
    from openavc.drivers.configurable import create_configurable_driver_class

    errors = validate_driver_definition(driver_def, strict=True)
    if errors:
        raise definition_invalid(errors)
    enforce_declared_floor(driver_def.get("min_platform_version"))

    saved = save_driver_definition(driver_def, dirs[1])  # driver_repo/
    if replacing is not None:
        delete_driver_definition(replacing, dirs, keep=saved)

    register_driver(create_configurable_driver_class(driver_def))
    engine = _get_engine()
    new_id = driver_def["id"]
    reconnected = await engine.devices.reload_driver(new_id)
    if replacing is not None and new_id != replacing:
        restore_driver_registration(replacing, dirs)
        reconnected = reconnected + await engine.devices.reload_driver(replacing)

    await announce_drivers_changed(new_id)
    return {"driver_id": new_id, "devices_reconnected": reconnected}


def _requested_definition(body: DriverDefinitionRequest) -> dict[str, Any]:
    """Only what the client sent: unset model defaults (manufacturer,
    delimiter, empty containers) stay out of the saved YAML, explicit nulls are
    dropped, and the listing's own bookkeeping keys are stripped."""
    return _strip_listing_decorations(body.model_dump(exclude_unset=True, exclude_none=True))


@router.post("/driver-definitions")
async def create_driver_definition(body: DriverDefinitionRequest) -> dict:
    """Create a new JSON driver definition."""
    from openavc.drivers.driver_loader import list_driver_definitions as _list

    dirs = _get_driver_dirs()
    driver_def = _requested_definition(body)
    if any(d.get("id") == driver_def["id"] for d in _list(dirs)):
        raise _id_taken(driver_def["id"])

    saved = await _save_definition(driver_def, dirs)
    return {"status": "created", **saved}


@router.put("/driver-definitions/{driver_id}")
async def update_driver_definition(driver_id: str, body: DriverDefinitionRequest) -> dict:
    """Update an existing JSON driver definition."""
    from openavc.drivers.driver_loader import list_driver_definitions as _list

    dirs = _get_driver_dirs()
    driver_def = _requested_definition(body)
    existing = _list(dirs)
    _current_definition(driver_id, existing)
    _refuse_built_in_edit(driver_id, dirs)
    _refuse_rename_onto_another(driver_def.get("id", ""), driver_id, existing, dirs)

    saved = await _save_definition(driver_def, dirs, replacing=driver_id)
    return {"status": "updated", **saved}


def _merge_patch(current: Any, patch: Any) -> Any:
    """JSON Merge Patch (RFC 7386): objects merge recursively, a null value
    deletes the key, and anything else (arrays included) replaces wholesale.

    A shallow top-level merge here silently destroyed sibling entries: a
    PATCH updating one command replaced the entire ``commands`` block and
    persisted the truncated driver, breaking every device bound to the
    dropped commands.
    """
    if not isinstance(patch, dict):
        return patch
    merged = dict(current) if isinstance(current, dict) else {}
    for key, value in patch.items():
        if value is None:
            merged.pop(key, None)
        else:
            merged[key] = _merge_patch(merged.get(key), value)
    return merged


@router.patch("/driver-definitions/{driver_id}")
async def patch_driver_definition(driver_id: str, body: dict) -> dict:
    """Partially update a driver definition (JSON Merge Patch semantics)."""
    from openavc.drivers.driver_loader import list_driver_definitions as _list

    dirs = _get_driver_dirs()
    current = _current_definition(driver_id, _list(dirs))
    _refuse_built_in_edit(driver_id, dirs)

    # A patch never changes the id. The floor is read off the merge, so a
    # patch carrying a lower floor (or clearing it) is the way out of one.
    merged = _merge_patch(current, body)
    merged["id"] = driver_id
    saved = await _save_definition(_strip_listing_decorations(merged), dirs, replacing=driver_id)
    return {"status": "updated", **saved}


@router.delete("/driver-definitions/{driver_id}")
async def delete_driver_definition_endpoint(driver_id: str) -> dict:
    """Delete a JSON driver definition."""
    from openavc.drivers.driver_loader import (
        delete_driver_definition,
        is_builtin_driver,
        restore_driver_registration,
    )

    dirs = _get_driver_dirs()

    # Built-in drivers ship inside the install tree and can't be deleted —
    # unlinking one would remove it permanently with no recovery.
    if is_builtin_driver(driver_id, dirs):
        raise HTTPException(
            status_code=403,
            detail="Built-in drivers can't be deleted.",
        )

    deleted = delete_driver_definition(driver_id, dirs)
    if not deleted:
        raise _definition_not_found(driver_id)
    # The deleted file may have been a user copy overriding a shipped
    # built-in: re-register the built-in so the id keeps working, or drop
    # the stale registration when nothing serves the id anymore.
    restored = restore_driver_registration(driver_id, dirs)
    if restored:
        # Reconnect devices on this driver so they pick up the restored class.
        await _get_engine().devices.reload_driver(driver_id)
    await announce_drivers_changed(driver_id)
    return {"status": "deleted", "driver_id": driver_id, "builtin_restored": restored}


@router.post("/driver-definitions/{driver_id}/reload", dependencies=[Depends(require_claimed_auth)])
@endpoint_policy(EndpointPolicy(HostCategory.PROGRAMMER, authenticate_claimed_request))
async def reload_driver_definition(driver_id: str) -> dict:
    """Re-read a YAML (``.avcdriver``) driver from disk and reconnect its devices.

    The Driver Builder re-registers a driver when you save it, but a
    ``.avcdriver`` file hand-edited on disk (outside the Builder — e.g. while
    developing a driver in ``driver_repo/``) doesn't take effect until a full
    project reload or restart. This mirrors the Python driver hot-reload route
    for declarative YAML drivers.
    """
    import yaml as _yaml

    from openavc.drivers.driver_loader import (
        find_driver_file_by_id,
        load_driver_file,
        validate_driver_definition,
    )
    from openavc.drivers.configurable import create_configurable_driver_class

    filepath = find_driver_file_by_id(_get_driver_dirs(), driver_id)
    if filepath is None:
        raise HTTPException(
            status_code=404,
            detail=f"No .avcdriver file declaring '{driver_id}' found on disk",
        )

    driver_def = load_driver_file(filepath)
    if driver_def is None:
        # load_driver_file logs the cause; surface the concrete validation
        # errors when we can so a hand-edit mistake is correctable.
        try:
            raw = _yaml.safe_load(filepath.read_text(encoding="utf-8"))
            errors = validate_driver_definition(raw) if isinstance(raw, dict) else ["not a YAML mapping"]
        except (OSError, _yaml.YAMLError):
            errors = []
        raise definition_invalid(
            errors,
            preamble=f"Driver file '{filepath.name}' is invalid or its "
                     f"discovery companion is missing (see server log)",
        )

    # Re-register, then reconnect live devices so they pick up the disk version
    # without a full project reload (mirrors the Python hot-reload path).
    driver_class = create_configurable_driver_class(driver_def)
    register_driver(driver_class)

    engine = _get_engine()
    reconnected = await engine.devices.reload_driver(driver_def["id"])
    # A renamed id on disk leaves devices on the requested id orphaned — retry.
    if driver_def["id"] != driver_id:
        reconnected = reconnected + await engine.devices.reload_driver(driver_id)

    return {
        "status": "reloaded",
        "driver_id": driver_def["id"],
        "file": filepath.name,
        "devices_reconnected": reconnected,
    }
