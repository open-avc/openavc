"""THE list of the files that make up a project on disk.

A project is its project file (``project.avc``, or whatever the system was
pointed at) and what sits beside it in the same folder: its scripts, its
file trees (uploaded images and audio, custom controls, saved themes) and
its saved variable values. Every door that backs the live project up or
replaces it asks this list which entries are the project's, rather than
naming them itself, so an entry the project gains is carried by every door
or by none:

- a project backup, the zip under Backups (``core/backup_manager``), and
  the one a start takes of what it is about to replace (``set_aside``);
- the swap when a whole project replaces the live one, a restore, an open
  from the library, a blank project, and a start's starter, restored backup
  or Recovery Project (``core/project_swap``);
- the backup an update or a rollback takes of a project kept at the data
  root, where the system's own files sit beside it (``updater/backup``).

Names only, so the updater reads it without the project runtime. What a
tree may hold and how its files are written into a zip or copied is the
tree's own module's: ``core/asset_tree`` for ``assets/``, ``core/custom_ui``
for ``ui/``, ``core/theme_tree`` for ``themes/``.

Not in the list: the ``backups/`` folder itself; the record of what the
last start had to do to get a project (``core/project_recovery``'s
``recovery.json``), which belongs to the start rather than to the project:
no project backup holds it, and a replace moves it aside with the project it
is about (``core/project_swap``); and the system's own files kept in the
project's folder (``SYSTEM_BESIDE_PROJECT``), which no project backup,
export or replace touches. An update's backup carries the project's whole
folder when the project has one of its own, so it holds all of these, and
a rollback puts back everything but the system's own files
(``updater/backup``).
"""

from __future__ import annotations

from openavc.core.asset_tree import ASSETS_DIR_NAME
from openavc.core.custom_ui import UI_DIR_NAME

#: The project's scripts: the ``.py`` files directly in this folder.
SCRIPTS_DIR = "scripts"
#: Uploaded images and audio, folders intact.
ASSETS_DIR = ASSETS_DIR_NAME
#: Hand-written custom controls, one folder each.
UI_DIR = UI_DIR_NAME
#: The project's own themes, one ``<id>.json`` each.
THEMES_DIR = "themes"
#: The saved variable values (``core/state_persister``).
STATE_FILE = "state.json"

#: The file trees beside the project file, each carried whole. A door that
#: replaces the project replaces every one of them, because a file left
#: behind belongs to a project that is no longer loaded and shows up in the
#: new one's lists.
FILE_TREES = (ASSETS_DIR, UI_DIR, THEMES_DIR)

#: Everything beside the project file that belongs to the project.
BESIDE_PROJECT = (SCRIPTS_DIR, *FILE_TREES, STATE_FILE)

#: This system's pairing with OpenAVC Cloud (``cloud/config``).
CLOUD_PAIRING_FILE = "cloud.json"
#: This system's instance id (``core/isc``).
INSTANCE_ID_FILE = ".instance_id"

#: The system's own files, kept in the project's folder but not the
#: project's: a project replaced keeps them, and so does a rollback, which
#: puts the projects back as they were before an update but never these. A
#: pairing made or removed since the update stays as it is.
SYSTEM_BESIDE_PROJECT = (CLOUD_PAIRING_FILE, INSTANCE_ID_FILE)
