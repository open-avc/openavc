"""A plugin's own ``api.save_config`` on its way to the project file.

THE two views of a plugin's config while one is: the plugin's own
(``PluginAPI.config``) holds what it saved at once, so a save it builds on
that one keeps it; the platform's (``PluginAPI._config``: what a project
change compares with, ``PluginLoader.get_running_config``) moves only once
the file holds the save (``OwnSave.taken``). A save that fails leaves both on
the saved config (``OwnSave.dropped``). And THE rule for which waiting save
may still be written (``OwnSave.current``): none that a newer change reached
the plugin before.

The state is the ``PluginAPI``'s own attributes (``_config``, ``_unsaved``,
``_handed``, ``_saves``, ``_saved``, ``_retired``); the engine's side of the
save is ``core/project_logins.py`` ``_save_plugin_config``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openavc.core.plugin_api import PluginAPI


class OwnSave:
    """One ``api.save_config`` on its way to the project file. It may land
    after a newer change has reached the plugin: a config the platform
    handed it (a hot apply, a restart, a stop), or a later save of its own
    that reached the file first. Written then, it would put the older config
    back, so the engine writes it only while it is ``current``."""

    def __init__(self, api: PluginAPI, config: dict) -> None:
        self.config = config
        self._api = api
        api._saves += 1
        self._number = api._saves
        self._handed = api._handed

    def current(self) -> bool:
        """Whether this save is still the newest thing the plugin has asked
        for or been handed."""
        api = self._api
        if api._retired or api._handed != self._handed:
            return False
        return self._number > api._saved

    def taken(self) -> None:
        """The file holds this save: the platform's copy moves to it, and
        the plugin's own view reads the platform's once nothing newer waits.
        Nothing when the save is no longer current."""
        if not self.current():
            return
        api = self._api
        api._config = self.config
        api._saved = self._number
        if api._unsaved is self.config:
            api._unsaved = None

    def dropped(self) -> None:
        """This save will not reach the file: the plugin's own view reads the
        saved config again, unless it has saved again since."""
        if self._api._unsaved is self.config:
            self._api._unsaved = None
