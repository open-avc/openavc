"""What update history says should be running, against what is.

``update-history.json`` (newest row first) is written only by the updater. An
older installer run by hand, an old update task or a restored disk image
changes the program without writing a row, so the running version can be older
than the one the last update installed, and nothing else says so. The manager
asks ``note_an_older_version`` once at startup and reports the answer in the
update status the Updates page shows, until an update or a rollback records a
newer row. A newer version installed by hand is the ordinary offline upgrade
and says nothing. It is allowed either way, so nothing is blocked.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable

from openavc.updater.checker import is_newer

log = logging.getLogger(__name__)

_INSTALLED = ("success", "applied")


def _expected_version(row: dict[str, Any]) -> str | None:
    """What one row says should be running.

    A success (an update or a rollback) installed its target, named by
    ``running_version`` when the row was confirmed at a startup that recorded
    it; a reverted update left its starting version. "" means the row cannot
    say: a rollback whose target was not known, or a row confirmed before
    ``running_version`` was recorded while the release tag and the bundled
    version differed (its ``to_version`` is the tag, and its ``note`` says so).
    None means the row moved nothing (a failed or pending update).
    """
    status = row.get("status")
    if status == "rolled_back":
        return str(row.get("from_version") or "")
    if status not in _INSTALLED:
        return None
    if row.get("running_version"):
        return str(row["running_version"])
    if row.get("note"):
        return ""
    return str(row.get("to_version") or "")


def last_installed_version(history: Iterable[dict[str, Any]]) -> str:
    """The version the newest row that moved the version says should run, or ""."""
    for row in history:
        expected = _expected_version(row)
        if expected is not None:
            return expected
    return ""


def note_an_older_version(history: Iterable[dict[str, Any]], running: str) -> str:
    """The version the last update installed when ``running`` is older, else "".

    Logs one warning when it is, which is what makes such a system findable in
    its log.
    """
    installed = last_installed_version(history)
    if not installed or not is_newer(installed, running):
        return ""
    log.warning(
        "Running v%s, older than v%s, which the last update installed: "
        "something other than an update changed the version",
        running, installed,
    )
    return installed
