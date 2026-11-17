"""Driver-definition CRUD: the ``.avcdriver`` definitions the Driver Builder
lists, reads, checks, saves, deletes and reloads.

Create, replace and patch each refuse what is theirs to refuse (an id already
taken; a definition that is not there, a built-in, a rename onto another
driver's id), then save through one function, ``_save_definition``: the strict
contract check, then ``driver_change.admit`` (the platform floor, the file
written beside, the loader's own check on it, the swap that puts it in place
and takes the replaced copy of the id away, the registration), then
``driver_change.changed`` (the simulation, the devices, Discovery, the
announcement), as every door that writes a driver does. A save the loader
would skip at the next start is refused now, with the copy that was there
unchanged. A shipped built-in in the read-only definitions folder is never
written or deleted here; a user copy that overrides one can be, and the
built-in serves the id again after.

Installing, uploading and removing drivers is ``routes/drivers.py``; the
refusal for an invalid definition lives there because its import doors ask it
too.
"""

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from openavc.api.auth import authenticate_claimed_request, require_claimed_auth
from openavc.api.errors import StructuredApiError
from openavc.api.host_policy import EndpointPolicy, ProgrammerRoute, endpoint_policy
from openavc.api.models import DriverDefinitionRequest
from openavc.api.routes.drivers import admit_or_answer, definition_invalid
from openavc.core.host_policy import HostCategory
from openavc.drivers.driver_change import DriverChange, Gates, changed
from openavc.drivers.registry import register_driver

router = APIRouter(route_class=ProgrammerRoute)

# A save names no sentence of its own above the loader's reasons: it answers
# under definition_invalid's heading ("The driver was not saved. Fix this
# first"), the one its contract check answers under.
_SAVE_GATES = Gates(not_a_definition="")


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
    issues = validate_driver_issues(draft, strict=True)
    return {"issues": issues + _missing_companion_issues(draft)}


def _missing_companion_issues(draft: dict[str, Any]) -> list[dict[str, str]]:
    """The discovery file the draft names and the driver folder lacks, as the
    issue the save would refuse it for (``missing_companion``)."""
    from openavc.drivers.driver_loader import missing_companion
    from openavc.system_config import DRIVER_REPO_DIR

    missing = missing_companion(draft, Path(DRIVER_REPO_DIR))
    if missing is None:
        return []
    return [{"severity": "error", "message": missing, "path": "discovery.python.file"}]


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
    to debug later. Then ``driver_change.admit``: the platform floor, read off
    the definition in hand, never the stored copy, so lowering a floor that
    blocks the save is always possible (the Builder's "import a definition"
    lands here too); the file written beside the folder and held to the
    loader's own check, which refuses a definition naming a discovery file the
    folder lacks; the swap, which puts it in place and takes away the copy of
    ``replacing`` it replaces in one step; the registration. A refusal or a
    failure leaves the folder and the registry as they were.

    A rename leaves devices on the old id waiting: if the removed file was
    overriding a shipped built-in, the built-in is registered again so the old
    id keeps working, otherwise its registration is dropped. Then
    ``driver_change.changed`` for the new id and the old: the devices on each
    are built again from what serves it now.
    """
    from openavc.drivers.driver_loader import (
        definition_file,
        replaced_definition,
        restore_driver_registration,
        validate_driver_definition,
    )

    errors = validate_driver_definition(driver_def, strict=True)
    if errors:
        raise definition_invalid(errors)

    repo = Path(dirs[1])  # driver_repo/
    repo.mkdir(parents=True, exist_ok=True)
    name, data = definition_file(driver_def)
    replaced = (
        replaced_definition(replacing, dirs, keep=repo / name) if replacing is not None else None
    )
    admit_or_answer(
        DriverChange(repo, name, {name: data}, removes=[replaced.name] if replaced else []),
        _SAVE_GATES,
        failed="The driver was not saved, and the copy you had is unchanged. The Programmer's Log has the details.",
    )

    new_id = driver_def["id"]
    renamed = [replacing] if replacing is not None and replacing != new_id else []
    for old_id in renamed:
        restore_driver_registration(old_id, dirs)
    outcome = await changed(new_id, *renamed)
    return {"driver_id": new_id, "devices_reconnected": outcome.reconnected}


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
    # built-in: re-register the built-in so the id keeps working, and build
    # its devices again on it; or drop the stale registration when nothing
    # serves the id anymore, and leave its devices as they are.
    restored = restore_driver_registration(driver_id, dirs)
    if restored:
        await changed(driver_id)
    else:
        await changed(gone=[driver_id])
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
    from openavc.drivers.driver_loader import (
        find_driver_file_by_id,
        read_library_definition,
    )
    from openavc.drivers.configurable import create_configurable_driver_class

    filepath = find_driver_file_by_id(_get_driver_dirs(), driver_id)
    if filepath is None:
        raise HTTPException(
            status_code=404,
            detail=f"No .avcdriver file declaring '{driver_id}' found on disk",
        )

    read = read_library_definition(filepath)
    driver_def = read.definition
    if driver_def is None:
        raise definition_invalid(
            list(read.reasons),
            preamble=(
                f"Driver file '{filepath.name}' did not load. "
                + ("Fix this first" if len(read.reasons) == 1 else "Fix these first")
            ),
        )

    # Re-register, then build the devices again so they pick up the disk
    # version without a full project reload. A renamed id on disk leaves
    # devices on the requested id orphaned, so they are built again too.
    driver_class = create_configurable_driver_class(driver_def)
    register_driver(driver_class)
    renamed = [driver_id] if driver_def["id"] != driver_id else []
    outcome = await changed(driver_def["id"], *renamed)

    return {
        "status": "reloaded",
        "driver_id": driver_def["id"],
        "file": filepath.name,
        "devices_reconnected": outcome.reconnected,
    }
