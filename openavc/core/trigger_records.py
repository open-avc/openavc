"""What the trigger engine keeps of each trigger, by trigger id, across a
rebuild of the triggers (``TriggerEngine.load_triggers``, on any project
change): the cooldown stamp it publishes in the state,
``system.trigger.<id>.last_fired``, its in-process cooldown baseline, its last
run (``LastRun``) and its last scheduled fire.

``drop_gone`` is THE rule for what of a trigger outlives it: every record is
kept for an id a trigger still has, so a hot reload keeps both the cooldown
and the no-double-fire guarantees, and dropped for one nobody has any more. A
record for a gone id is a leak, and an id that comes back must not inherit a
stranger's cooldown or run. A change that is undone hands back what its
rebuild dropped: ``copy_of`` takes the records before the change, and
``put_back`` restores each one that is gone, keeping one written since as the
newer.

The stamp lives in the state store, which is in memory: it keeps a cooldown
across a hot reload but not across a restart, since the state persister
writes only variables flagged ``persist``, never ``system.*`` keys.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, NamedTuple

if TYPE_CHECKING:
    from openavc.core.state_store import StateStore
    from openavc.core.trigger_engine import LastRun

_STAMP_PREFIX = "system.trigger."
_STAMP_SUFFIX = ".last_fired"


def stamp_key(trigger_id: str) -> str:
    """Where a trigger publishes when it last started its macro."""
    return f"{_STAMP_PREFIX}{trigger_id}{_STAMP_SUFFIX}"


def _stamped_id(key: str) -> str:
    """The trigger id a stamp key names, dots and all."""
    return key[len(_STAMP_PREFIX):-len(_STAMP_SUFFIX)]


def published_stamps(state: StateStore) -> dict[str, Any]:
    """Every trigger's stamp in the state, by state key."""
    return state.get_matching(stamp_key("*"))


def stamp_of(state: StateStore, trigger_id: str) -> float:
    """The stamp a trigger published, read back at a rebuild; 0 for none."""
    stamp = state.get(stamp_key(trigger_id))
    return float(stamp) if stamp and isinstance(stamp, (int, float)) else 0.0


class TriggerRecords(NamedTuple):
    """The records by trigger id: the stamps (by state key), the in-process
    cooldown baselines, the last runs and the last scheduled fires. Either
    the engine's own maps, or a copy of them taken before a project change
    for its undo."""

    stamps: dict[str, Any]
    cooldowns: dict[str, float]
    last_runs: dict[str, LastRun]
    schedules: dict[str, datetime]

    def by_id(self) -> tuple[dict[str, Any], ...]:
        """The records keyed by trigger id, which is all but the stamps."""
        return (self.cooldowns, self.last_runs, self.schedules)


def drop_gone(state: StateStore, kept: TriggerRecords, live_ids: set[str]) -> None:
    """Drop every record of an id no trigger has any more."""
    for records in kept.by_id():
        _drop(records, live_ids)
    for key in [k for k in kept.stamps if _stamped_id(k) not in live_ids]:
        state.delete(key, source="system")


def _drop(records: dict[str, Any], live_ids: set[str]) -> None:
    for trigger_id in [k for k in records if k not in live_ids]:
        del records[trigger_id]


def copy_of(kept: TriggerRecords) -> TriggerRecords:
    """The records as they stand, for a change's undo to put back."""
    return TriggerRecords(*(dict(records) for records in kept))


def put_back(state: StateStore, taken: TriggerRecords, kept: TriggerRecords) -> None:
    """Each record ``taken`` holds that ``kept`` no longer does. One still
    kept stays as it is: written since the records were taken (a fire
    handed over as the triggers stopped, a run that ended during the change),
    it is the newer, and the rebuild that follows drops whatever belongs to a
    trigger only the change had."""
    for taken_records, now in zip(taken.by_id(), kept.by_id()):
        _fill(now, taken_records)
    missing = {k: v for k, v in taken.stamps.items() if not state.has(k)}
    for key, value in missing.items():
        state.set(key, value, source="system")


def _fill(now: dict[str, Any], taken: dict[str, Any]) -> None:
    for trigger_id, record in taken.items():
        now.setdefault(trigger_id, record)
