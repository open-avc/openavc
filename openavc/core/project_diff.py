"""Project change classification for the engine's reconciler.

``ProjectOrigin`` says HOW a project change arrived (an incremental edit vs.
a whole new project); ``ProjectDiff`` says WHAT changed, section by section.
``Engine.apply_project()`` uses both to reconcile only the subsystems a
change actually touches instead of tearing down and rebuilding the runtime.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from openavc.core.project_loader import ProjectConfig


class ProjectOrigin(Enum):
    """How a project change entered the engine.

    EDIT — an incremental save (IDE autosave, device edit, remote tool).
    The reconciler applies only what changed: no driver-library rescan, no
    startup-trigger re-fire, and running macros are cancelled only when the
    macro definitions themselves changed.

    LOAD — a whole new project arrived: engine start, an explicit reload
    from disk, library open, create-blank, backup restore, or a fleet
    config push. Every section is treated as dirty, the driver library is
    rescanned, and startup triggers fire.
    """

    EDIT = "edit"
    LOAD = "load"


@dataclass
class ProjectDiff:
    """Section-level dirty flags between two ``ProjectConfig`` objects.

    Derived sections (``driver_dependencies``, ``plugin_dependencies``) and
    ``openavc_version`` are ignored: they are recomputed on save and drive
    no runtime state.
    """

    devices: bool = False
    connections: bool = False
    device_groups: bool = False
    variables: bool = False
    macros: bool = False
    plugins: bool = False
    ui: bool = False
    scripts: bool = False
    isc: bool = False
    project_meta: bool = False
    # Script granularity for the EDIT path: configs to (re)load and ids to
    # unload. Empty for LOAD — a new project can replace script FILES while
    # the configs stay identical, so only a full script reload is safe there.
    scripts_to_reload: list[dict[str, Any]] = field(default_factory=list)
    scripts_to_unload: list[str] = field(default_factory=list)

    @property
    def requires_trigger_rebuild(self) -> bool:
        """True when triggers must be stopped before reconciling and rebuilt
        after. Two reasons a section lands here: its reconcile deletes state
        keys (``var.*``, ``device.*``, ``plugin.*``) that a state_change
        trigger could otherwise fire on mid-cleanup, or it carries the
        trigger definitions themselves (macros)."""
        return (
            self.variables
            or self.devices
            or self.connections
            or self.plugins
            or self.macros
        )

    @property
    def any_dirty(self) -> bool:
        return (
            self.devices or self.connections or self.device_groups
            or self.variables or self.macros or self.plugins or self.ui
            or self.scripts or self.isc or self.project_meta
        )

    @classmethod
    def all_dirty(cls) -> ProjectDiff:
        """A diff with every section dirty (used for LOAD origin)."""
        return cls(
            devices=True, connections=True, device_groups=True,
            variables=True, macros=True, plugins=True, ui=True,
            scripts=True, isc=True, project_meta=True,
        )

    @classmethod
    def compute(cls, old: ProjectConfig | None, new: ProjectConfig) -> ProjectDiff:
        """Compare two projects section by section (pure in-memory model
        equality — no disk access)."""
        if old is None:
            return cls.all_dirty()
        diff = cls(
            devices=old.devices != new.devices,
            connections=old.connections != new.connections,
            device_groups=old.device_groups != new.device_groups,
            variables=old.variables != new.variables,
            macros=old.macros != new.macros,
            plugins=old.plugins != new.plugins,
            ui=old.ui != new.ui,
            scripts=old.scripts != new.scripts,
            isc=old.isc != new.isc,
            project_meta=old.project != new.project,
        )
        if diff.scripts:
            old_by_id = {s.id: s for s in old.scripts}
            new_ids = {s.id for s in new.scripts}
            diff.scripts_to_unload = [sid for sid in old_by_id if sid not in new_ids]
            diff.scripts_to_reload = [
                s.model_dump() for s in new.scripts if s != old_by_id.get(s.id)
            ]
        return diff
