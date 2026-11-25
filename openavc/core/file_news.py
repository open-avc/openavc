"""THE announcement that a file tree beside the running project changed:
its uploaded images and sounds (``assets/``), its custom controls (``ui/``)
or its saved themes (``themes/``), the trees ``core/project_unit`` names.

They are files, not project data, so a change to one moves nothing a
project change announces. Every door that writes or removes one of them
outside a project change, the Programmer's and the AI assistant's alike,
announces the tree it changed here, and a replace of the whole project that
swaps them (``Engine.replace_with``) announces every tree once the new
project runs, so a list or a picture of those files anywhere is drawn again
from what is on disk, whichever door changed it:

- ``assets/``: the ``project.assets`` state key, the catalog of the files as
  they are now (JSON, since a state value is flat). A plugin such as the
  Audio Player reads it, and the Programmer's asset list loads again on it.
- ``ui/``: ``ui.files`` to every client, with a version a panel and the
  Builder's canvas put on each custom control's URL and the path that
  changed (``.``, the whole tree).
- ``themes/``: ``themes.changed`` to every client, with the id of the theme
  that changed (none for the whole tree). The Builder loads its theme list
  and the theme it draws again, and a panel drawing that theme fetches it
  again.

A door announces once the project files' hold is let go
(``Engine.holding_project_files``): a plugin that hears ``project.assets``
may change the project at once. A failed announcement is logged, never
raised, because the file it is about is already written.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from openavc.core import project_unit
from openavc.utils.logger import get_logger

log = get_logger(__name__)

#: The ``path`` a ``ui.files`` frame carries when the whole tree changed.
WHOLE_TREE = "."


def publish_assets(engine: Any) -> None:
    """``project.assets`` set to the catalog of the running project's
    ``assets/`` folder: one ``{name, size, extension, type}`` per file, as
    the asset list answers it. Also called at start."""
    from openavc.api.assets import asset_catalog

    try:
        assets_dir = Path(engine.project_path).parent / project_unit.ASSETS_DIR
        catalog = asset_catalog(assets_dir) if assets_dir.is_dir() else []
        engine.state.set("project.assets", json.dumps(catalog), source="system")
    except Exception:
        log.exception("An asset changed, but publishing project.assets failed")


async def assets_changed(engine: Any) -> None:
    """An image or a sound was saved or removed."""
    publish_assets(engine)


async def ui_files_changed(engine: Any, path: str = WHOLE_TREE) -> None:
    """A custom control's file at ``path`` (under ``ui/``) was saved or
    removed. A panel draws a control again only when it redraws, so without
    this frame one already showing that control keeps the copy it has. The
    version is milliseconds rather than a count, so it still moves forward
    across a restart, when a browser may hold a copy stamped with the last
    count."""
    await _broadcast(engine, {
        "type": "ui.files",
        "version": str(time.time_ns() // 1_000_000),
        "path": path,
    })


async def themes_changed(engine: Any, theme_id: str | None = None) -> None:
    """The theme ``theme_id`` was saved, imported or removed; with none, any
    of the project's themes may have."""
    message: dict[str, Any] = {"type": "themes.changed"}
    if theme_id is not None:
        message["theme_id"] = theme_id
    await _broadcast(engine, message)


_WHOLE_TREE_NEWS = {
    project_unit.ASSETS_DIR: assets_changed,
    project_unit.UI_DIR: ui_files_changed,
    project_unit.THEMES_DIR: themes_changed,
}


async def trees_changed(engine: Any, trees: Iterable[str] = project_unit.FILE_TREES) -> None:
    """Each of ``trees`` changed as a whole (a replace swapped it)."""
    for tree in trees:
        await _WHOLE_TREE_NEWS[tree](engine)


async def _broadcast(engine: Any, message: dict[str, Any]) -> None:
    try:
        await engine.broadcast_ws(message)
    except Exception:
        log.exception(f"A project file changed, but sending {message['type']} failed")
