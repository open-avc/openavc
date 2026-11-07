"""What changed: the status values the audit's commands and settings moved.

Two readings of the device's state, both for the person to act on:

- :func:`moved_values`, per command: each value its window saw change, once,
  what it read before the first change and after the last, and whether it was
  already changing before the command went out (a meter, a clock) or ended
  where it began (a reconnect re-reading it, a pulse).
- :func:`changed_values`, per run: every value that reads differently now
  from before the first command or setting the audit sent, with the command
  whose window saw it move last. A command cannot be undone in general, so
  the wizard lists these for the person to put back themselves if they need
  to; the settings the audit wrote it puts back on its own
  (``audit/settings.py``). A value that changes without the audit is marked
  ``on_its_own`` rather than left out, so the person can tell the two apart.

A value is "the same" only when it is equal and of the same type: ``1`` and
``True`` are different readings of the device.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from openavc.audit.observe import is_platform_key, recorded


def state_label(info: dict[str, Any], key: str) -> str:
    """A status value's name as a person reads it: the driver's label, and a
    child's with its type and id ("Channel 01 Mute")."""
    spec = (info.get("state_variables") or {}).get(key)
    if isinstance(spec, dict):
        return str(spec.get("label") or key)
    ctype, _, rest = key.partition(".")
    local_id, _, prop = rest.partition(".")
    cdef = (info.get("child_entity_types") or {}).get(ctype) if prop else None
    if not isinstance(cdef, dict):
        return key
    pspec = (cdef.get("state_variables") or {}).get(prop)
    plabel = pspec.get("label") if isinstance(pspec, dict) else None
    return f"{cdef.get('label') or ctype} {local_id} {plabel or prop}"


def current_values(listen: Any) -> dict[str, Any]:
    """The device's status values now: live while the driver is connected,
    else as its kept status table last read them."""
    if listen.sandbox.started:
        return listen.sandbox.device_state()
    return final_values(listen.status_table())


def final_values(table: dict[str, Any]) -> dict[str, Any]:
    """A kept status table's values, as device state keys."""
    out = {v["name"]: v["value"] for v in table.get("variables", []) if v.get("reported")}
    for ctype, by_id in (table.get("children") or {}).items():
        for local_id, props in by_id.items():
            for prop, value in props.items():
                out[f"{ctype}.{local_id}.{prop}"] = value
    return out


def _same(before: Any, now: Any) -> bool:
    return before == now and type(before) is type(now)


def moved_values(
    changes: list[dict[str, Any]], already_moving: list[str], info: dict[str, Any],
) -> list[dict[str, Any]]:
    """Each status value a command's window saw change, once, in the order it
    first moved: what it read before the first change and after the last,
    how many times it changed, and whether it was already changing before
    the command was sent (``already_moving``)."""
    moving = set(already_moving)
    out: dict[str, dict[str, Any]] = {}
    for change in changes:
        key = change["key"]
        if is_platform_key(key):
            continue
        item = out.get(key)
        if item is None:
            out[key] = {
                "key": key, "label": state_label(info, key), "first": change["old"],
                "last": change["new"], "times": 1, "already_moving": key in moving,
            }
        else:
            item["last"] = change["new"]
            item["times"] += 1
    for item in out.values():
        # It ended where it began (a reconnect re-reading it, a pulse): not
        # something the command left changed.
        item["went_back"] = _same(item["first"], item["last"])
    return list(out.values())


def changed_values(run: Any) -> list[dict[str, Any]]:
    """Status values that read differently now from before the audit sent
    its first command or setting in this run, each ``{"key", "label",
    "before", "now", "by", "on_its_own"}``. ``by`` is the command whose
    window saw the value move last (``{"number", "label"}``), or null when it
    moved while nothing was being watched (at the device itself, say).
    ``on_its_own`` is true for a value that was already changing when that
    command, or any other, was sent, or that changed while nothing was being
    watched after it (a meter, a clock), or only then: the audit did not leave
    it that way. A value the device has stopped reporting is left out: there
    is nothing to compare.
    """
    commands = getattr(run, "commands", None)
    trials = commands.trials if commands is not None else []
    baseline = _baseline(trials, getattr(run, "settings", None))
    listen = getattr(run, "listen", None)
    if baseline is None or listen is None:
        return []
    now = current_values(listen)
    info = _driver_info(run, listen)
    movers = _Movers.of(trials)
    unwatched = getattr(run, "unwatched", None) or {}
    out = []
    for key in sorted(now):
        value = now[key]
        if is_platform_key(key) or value is None:
            continue
        before = baseline.get(key)
        if _same(before, value):
            continue
        out.append({
            "key": key,
            "label": state_label(info, key),
            "before": recorded(before),
            "now": recorded(value),
            "by": movers.by.get(key),
            "on_its_own": movers.on_its_own(key, unwatched),
        })
    return out


def _baseline(trials: list[Any], settings: Any) -> dict[str, Any] | None:
    """The device's state as the audit's first command or setting found it,
    or None before the audit has sent either."""
    firsts = [(t.sent_at, t.before) for t in trials]
    if settings is not None:
        firsts += [(t.started_at, t.before) for t in settings.trials]
    if not firsts:
        return None
    return min(firsts, key=lambda pair: pair[0])[1]


def _driver_info(run: Any, listen: Any) -> dict[str, Any]:
    info = getattr(listen.sandbox.driver, "DRIVER_INFO", None) or {}
    if info:
        return info
    from openavc.drivers.registry import get_driver_class

    return getattr(get_driver_class(run.choice.driver_id), "DRIVER_INFO", {}) or {}


@dataclass
class _Movers:
    """Which command's window saw each value move last, and which values the
    commands found already changing as they went out."""

    by: dict[str, dict[str, Any]] = field(default_factory=dict)
    sent_at: dict[str, float] = field(default_factory=dict)
    moving_then: dict[str, bool] = field(default_factory=dict)
    # A value any command found already changing when it went out moves on
    # its own (a meter), even when the last window that saw it move did not
    # catch it moving first.
    anyway: set[str] = field(default_factory=set)

    @classmethod
    def of(cls, trials: list[Any]) -> "_Movers":
        movers = cls()
        for trial in trials:
            for change in trial.changes:
                key = change["key"]
                movers.by[key] = {"number": trial.number, "label": trial.label}
                movers.sent_at[key] = trial.sent_at
                movers.moving_then[key] = key in trial.already_moving
        movers.anyway = {key for trial in trials for key in trial.already_moving}
        return movers

    def on_its_own(self, key: str, unwatched: dict[str, Any]) -> bool:
        """Changing without the audit: it was already changing when the last
        command that moved it was sent, or it changed unwatched after that
        command (or it moved only unwatched)."""
        if self.moving_then.get(key, False) or key in self.anyway:
            return True
        latest = unwatched.get(key)
        return bool(latest) and latest[-1] > self.sent_at.get(key, 0.0)
