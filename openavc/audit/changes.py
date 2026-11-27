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
``True`` are different readings of the device. On a variable a device setting
reads back through, two readings the setting's declaration writes as one
value are the same too (``audit/setting_reading``): an option's label and its
value, ``"on"`` and ``True`` for a flag. A free-text value that changed only
in case has still changed.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from openavc.audit.driver_reading import model_for
from openavc.audit.observe import is_platform_key, recorded
from openavc.audit.setting_reading import (
    Setting,
    secret_status_values,
    setting_reads_alike,
    settings_by_variable,
    shown_status_value,
)


def state_label(info: Mapping[str, Any], key: str) -> str:
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


def _same(before: Any, now: Any, setting: Setting | None) -> bool:
    if before == now and type(before) is type(now):
        return True
    return setting is not None and setting_reads_alike(setting, before, now)


def moved_values(
    changes: list[dict[str, Any]], already_moving: list[str], info: dict[str, Any],
) -> list[dict[str, Any]]:
    """Each status value a command's window saw change, once, in the order it
    first moved: what it read before the first change and after the last,
    how many times it changed, and whether it was already changing before
    the command was sent (``already_moving``)."""
    moving = set(already_moving)
    settings = settings_by_variable(info)
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
        item["went_back"] = _same(item["first"], item["last"], settings.get(item["key"]))
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
    is nothing to compare. A value a secret setting reads through reads
    ``***`` (``setting_reading.shown_status_value``).
    """
    commands = getattr(run, "commands", None)
    trials = commands.trials if commands is not None else []
    baseline = _baseline(trials, getattr(run, "settings", None))
    listen = getattr(run, "listen", None)
    if baseline is None or listen is None:
        return []
    now = current_values(listen)
    info = model_for(run, listen).info
    movers = _Movers.of(trials)
    unwatched = getattr(run, "unwatched", None) or {}
    settings = settings_by_variable(info)
    secret = secret_status_values(info)
    out = []
    for key in sorted(now):
        value = now[key]
        if is_platform_key(key) or value is None:
            continue
        before = baseline.get(key)
        if _same(before, value, settings.get(key)):
            continue
        out.append({
            "key": key,
            "label": state_label(info, key),
            "before": shown_status_value(secret, key, recorded(before)),
            "now": shown_status_value(secret, key, recorded(value)),
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
