"""Files chosen for a setup action, using the command-file staging store.

An upload belongs to one device, action and parameter. Only the upload handle
crosses the API; paths and file names in ordinary params never become files.
"""

from __future__ import annotations

from typing import Any

from openavc.core.file_staging import StagingError, file_rules_refusal
from openavc.drivers.spec import file_param_names


def setup_owner(device_id: str) -> str:
    return f"setup:{device_id}"


def take_setup_files(
    staging: Any,
    device_id: str,
    action: dict[str, Any],
    params: dict[str, Any],
    files: dict[str, str],
) -> tuple[dict[str, Any], list[str]]:
    """Validate and claim all files, releasing earlier claims on refusal."""
    names = checked_names(action, params, files)
    merged = dict(params)
    claimed: list[str] = []
    try:
        for name in sorted(names):
            definition = action["params"][name]
            upload_id = selected_upload(files, name, definition)
            if not upload_id:
                continue
            file = staging.take(
                upload_id, owner=setup_owner(device_id), command=action["id"], param=name,
            )
            claimed.append(upload_id)
            check_file_rules(name, definition, file)
            merged[name] = file
    except StagingError:
        for upload_id in claimed:
            staging.untake(upload_id)
        raise
    return merged, claimed


def checked_names(action, params, files):
    names = set(file_param_names(action))
    if set(files) - names:
        raise StagingError("This action does not accept those file parameters.")
    if names.intersection(params):
        raise StagingError("Choose a file using the file picker, not a path or file name.")
    return names


def selected_upload(files, name, definition):
    upload_id = files.get(name)
    if not upload_id and definition.get("required"):
        raise StagingError(f"Choose a file for {definition.get('label') or name}.")
    return upload_id


def check_file_rules(name, definition, file):
    refusal = file_rules_refusal(name, definition, file.name, file.size)
    if refusal:
        raise StagingError(*refusal)
