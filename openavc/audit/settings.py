"""Device settings: write one, see it read back, put the old value back.

A driver's device settings (``device_settings``) are values that live on the
device itself: a name, a network option, a mode it keeps across restarts. The
audit tests the one the person picks, the way production writes it:

- **The write** goes through the sandbox manager's ``set_device_setting``: the
  setting must be declared, the device connected, and the value passes the
  platform's ``validate_device_setting_value`` gate (which also puts it in its
  declared type).
- **The read-back** is the device manager's own rule for a queued write
  (``DeviceManager.await_setting_readback``): the device reports the value,
  in its own form if it likes, within two poll cycles and some slack.
- **The old value goes back** the same way at once, and is read back too.

**Nothing is written that cannot be put back.** The audit writes a setting
only when it can read the value the setting has now (its ``state_key`` is a
status value the device has reported) and that value is one the setting
accepts (the same gate the write passes), never writes the value it already
has, and puts back any setting still changed when the driver stops: Test
another driver, Connect again, Finish, Cancel or a timeout. "Put it back"
retries a restore that did not read back.

One setting at a time, and never while a command is being watched, so a
read-back belongs to the write that asked for it.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from openavc.audit.commands import error_sentence, same_value
from openavc.audit.session import AuditError
from openavc.core.state_store import is_flat_primitive
from openavc.drivers.base import validate_device_setting_value
from openavc.utils.logger import get_logger

if TYPE_CHECKING:
    from openavc.audit.passes import DriverRun
    from openavc.audit.session import AuditSession

log = get_logger(__name__)

# The longest the audit waits for a setting it puts back as the driver stops,
# so ending an audit never waits out a long poll cycle.
STOP_READBACK_SECONDS = 5.0

WRITING = "writing"
RESTORING = "restoring"
DONE = "done"

NO_SUCH_SETTING = "{driver} has no device setting named {key}."
CANNOT_READ = (
    "OpenAVC cannot read {label}'s value from the device, so it could not put it "
    "back. The audit does not change it."
)
CANNOT_PUT_BACK = (
    "{label} is {value} now, which is not a value OpenAVC can write back ({why}), so "
    "the audit does not change it."
)
SAME_VALUE = "{label} is {value} already. Choose a different value to test writing it."
BUSY = "Wait for {label} to finish before changing a setting."
NOTHING_TO_PUT_BACK = "{label} was not changed by the audit, so there is nothing to put back."
NOT_CONNECTED_YET = "Connect the driver first, on the Connect and listen step."


# Parts of a setting's declaration the wizard's value field has no use for.
_NOT_FOR_THE_FIELD = frozenset({"write", "label", "help", "state_key", "setup"})


def _shown(value: Any) -> Any:
    return value if is_flat_primitive(value) else repr(value)


def _value_text(value: Any) -> str:
    if value is None:
        return "nothing"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


@dataclass
class SettingTrial:
    """One setting written and put back."""

    number: int
    key: str
    label: str
    # The value it had (as the device reported it, and as it is written
    # back), and the value asked for, in the setting's declared type.
    original: Any
    value: Any
    started_at: float
    restore_value: Any = None
    status: str = WRITING
    # Each half: {"at", "error", "confirmed", "value", "after"}; ``restore``
    # also says whether the audit did it on its own as the driver stopped.
    write: dict[str, Any] = field(default_factory=dict)
    restore: dict[str, Any] | None = None
    # The device's state before the write (for "what changed").
    before: dict[str, Any] = field(default_factory=dict)

    def put_back(self) -> bool:
        """The original value is back, as far as the device says."""
        return bool(self.restore and self.restore.get("confirmed"))

    def needs_putting_back(self) -> bool:
        """The write reached the device and the original is not confirmed back."""
        return not self.write.get("error") and "at" in self.write and not self.put_back()

    def to_dict(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "key": self.key,
            "label": self.label,
            "original": _shown(self.original),
            "value": _shown(self.value),
            "started_at": self.started_at,
            "status": self.status,
            "write": dict(self.write),
            "restore": dict(self.restore) if self.restore else None,
            "summary": setting_sentence(self) if self.status == DONE else "",
        }


class SettingsPass:
    """The device settings of one driver run."""

    def __init__(
        self,
        session: "AuditSession",
        run: "DriverRun",
        *,
        stop_readback_seconds: float = STOP_READBACK_SECONDS,
    ) -> None:
        self.session = session
        self.run = run
        self.stop_readback_seconds = stop_readback_seconds
        self.trials: list[SettingTrial] = []
        self._task: asyncio.Task | None = None
        self._final: list[dict[str, Any]] | None = None

    # -- what there is --------------------------------------------------------

    def _live(self):
        listen = self.run.listen
        if listen is None or not listen.sandbox.started:
            raise AuditError(NOT_CONNECTED_YET)
        return listen

    def _definitions(self) -> dict[str, Any]:
        listen = self.run.listen
        driver = listen.sandbox.driver if listen is not None else None
        if driver is None:
            from openavc.drivers.registry import get_driver_class

            driver = get_driver_class(self.run.choice.driver_id)
        info = getattr(driver, "DRIVER_INFO", {}) or {}
        return info

    def catalog(self) -> list[dict[str, Any]]:
        """Every device setting the driver declares, with the value it has
        now and whether the audit can test it (and why not)."""
        listen = self.run.listen
        if self._final is not None and (listen is None or not listen.sandbox.started):
            return self._final
        info = self._definitions()
        variables = info.get("state_variables") or {}
        state = listen.sandbox.device_state() if listen is not None and listen.sandbox.started else {}
        out = []
        for key, sdef in (info.get("device_settings") or {}).items():
            sdef = sdef if isinstance(sdef, dict) else {}
            state_key = sdef.get("state_key", key)
            readable = state_key in variables
            current = state.get(state_key) if readable else None
            label = str(sdef.get("label") or key)
            reason = "" if readable and current is not None else CANNOT_READ.format(label=label)
            out.append({
                "key": key,
                "label": label,
                "help": str(sdef.get("help") or ""),
                # What the value field needs (type, allowed values, range): the
                # declaration as the driver wrote it, less how it is written.
                "definition": {
                    k: v for k, v in sdef.items() if k not in _NOT_FOR_THE_FIELD
                },
                "state_key": state_key,
                "value": _shown(current),
                "can_write": not reason,
                "reason": reason,
            })
        return out

    def current(self) -> SettingTrial | None:
        if self.trials and self.trials[-1].status != DONE:
            return self.trials[-1]
        return None

    # -- writing ----------------------------------------------------------------

    async def write(self, key: str, value: Any) -> SettingTrial:
        """Write ``value``, read it back, put the old value back, read that
        back (in the background)."""
        listen = self._live()
        self._refuse_if_busy()
        entry = next((s for s in self.catalog() if s["key"] == key), None)
        if entry is None:
            raise AuditError(NO_SUCH_SETTING.format(
                driver=self.run.choice.identity.get("name") or self.run.choice.driver_id, key=key,
            ))
        if not entry["can_write"]:
            raise AuditError(entry["reason"])
        sdef = (self._definitions().get("device_settings") or {}).get(key)
        try:
            typed = validate_device_setting_value(key, sdef, value)
        except ValueError as exc:
            raise AuditError(str(exc)) from exc
        original = listen.sandbox.device_state().get(entry["state_key"])
        if same_value(typed, original):
            raise AuditError(SAME_VALUE.format(label=entry["label"], value=_value_text(original)))
        try:
            restore_value = validate_device_setting_value(key, sdef, original)
        except ValueError as exc:
            raise AuditError(CANNOT_PUT_BACK.format(
                label=entry["label"], value=_value_text(original), why=exc,
            )) from exc
        trial = SettingTrial(
            number=len(self.trials) + 1,
            key=key,
            label=entry["label"],
            original=original,
            value=typed,
            restore_value=restore_value,
            started_at=time.time(),
            before=dict(listen.sandbox.device_state()),
        )
        self.trials.append(trial)
        self.session.enter_step("commands")
        self.session.add_timeline(
            "setting.writing",
            f"Writing {trial.label}: {_value_text(typed)} (it was {_value_text(original)}).",
            run=self.run.index, setting=key,
        )
        self._publish(trial)
        self._task = self.session.track_task(asyncio.create_task(self._round_trip(trial)))
        return trial

    async def put_back(self, key: str) -> SettingTrial:
        """"Put it back": restore the original of the last write of ``key``
        whose restore did not read back."""
        self._live()
        self._refuse_if_busy()
        trial = next((t for t in reversed(self.trials) if t.key == key), None)
        if trial is None or not trial.needs_putting_back():
            label = trial.label if trial else key
            raise AuditError(NOTHING_TO_PUT_BACK.format(label=label))
        trial.status = RESTORING
        self._publish(trial)
        self._task = self.session.track_task(asyncio.create_task(self._restore_only(trial)))
        return trial

    def _refuse_if_busy(self) -> None:
        busy = self.current()
        if busy is not None:
            raise AuditError(BUSY.format(label=busy.label))
        commands = self.run.commands
        if commands is not None:
            if commands.batch is not None and commands.batch.get("status") == "running":
                raise AuditError(BUSY.format(label="the status queries"))
            if commands.current() is not None:
                raise AuditError(BUSY.format(label=commands.current().label))

    async def _one(self, value: Any, *, key: str, timeout: float | None = None) -> dict[str, Any]:
        """One write of ``value`` and its read-back."""
        sandbox = self.run.listen.sandbox
        started = time.time()
        half: dict[str, Any] = {"at": started, "error": "", "confirmed": False, "value": None}
        try:
            await sandbox.manager.set_device_setting(sandbox.device_id, key, value)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            half["error"] = error_sentence(exc)
            return half
        confirmed, actual = await sandbox.manager.await_setting_readback(
            sandbox.device_id, key, value, timeout=timeout,
        )
        half.update({
            "confirmed": confirmed,
            "value": _shown(actual),
            "after": round(time.time() - started, 2),
        })
        return half

    async def _round_trip(self, trial: SettingTrial) -> None:
        try:
            trial.write = await self._one(trial.value, key=trial.key)
            self.session.add_timeline(
                "setting.written", _write_sentence(trial.label, trial.write, trial.value),
                run=self.run.index, setting=trial.key,
            )
            if trial.write.get("error"):
                return
            trial.status = RESTORING
            self._publish(trial)
            await self._restore(trial)
        finally:
            trial.status = DONE
            self._publish(trial)

    async def _restore_only(self, trial: SettingTrial) -> None:
        try:
            await self._restore(trial)
        finally:
            trial.status = DONE
            self._publish(trial)

    async def _restore(self, trial: SettingTrial, *, automatic: bool = False,
                       timeout: float | None = None) -> None:
        half = await self._one(trial.restore_value, key=trial.key, timeout=timeout)
        half["automatic"] = automatic
        trial.restore = half
        self.session.add_timeline(
            "setting.restored", _restore_sentence(trial.label, half, trial.original),
            run=self.run.index, setting=trial.key,
        )

    async def stop(self) -> None:
        """Before the driver stops: finish or cancel what is running, and put
        back every setting the audit changed that is not back yet."""
        task = self._task
        if task is not None and not task.done() and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        listen = self.run.listen
        running = listen is not None and listen.sandbox.started
        for trial in self.trials:
            if trial.needs_putting_back() and running:
                try:
                    await self._restore(
                        trial, automatic=True, timeout=self.stop_readback_seconds,
                    )
                except Exception:
                    log.warning("Putting back %s failed as the audit stopped", trial.key,
                                exc_info=True)
            trial.status = DONE
        if running:
            self._final = self.catalog()
        self._publish()

    # -- reading ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        current = self.current()
        return {
            "catalog": self.catalog(),
            "current": current.number if current is not None else None,
            "trials": [t.to_dict() for t in self.trials],
        }

    def report_record(self) -> dict[str, Any]:
        return {
            "catalog": self.catalog(),
            "trials": [t.to_dict() for t in self.trials],
        }

    def _publish(self, trial: SettingTrial | None = None) -> None:
        current = self.current()
        self.session.publish({
            "type": "audit.settings",
            "run": self.run.index,
            "settings": {
                "catalog": self.catalog(),
                "current": current.number if current is not None else None,
                "trials": [trial.to_dict()] if trial is not None else [],
            },
        })


def _read_back(half: dict[str, Any]) -> str:
    """What the device said after one write."""
    if half.get("confirmed"):
        return f"the device reported it back after {half['after']} s."
    if half.get("value") is None:
        return "the device did not report it back."
    return f"the device still reports {_value_text(half['value'])}."


def _write_sentence(label: str, half: dict[str, Any], value: Any) -> str:
    if half.get("error"):
        return f"Could not write {label} = {_value_text(value)}: {half['error']}"
    return f"Wrote {label} = {_value_text(value)}: {_read_back(half)}"


def _restore_sentence(label: str, half: dict[str, Any], value: Any) -> str:
    stopped = " as the driver stopped" if half.get("automatic") else ""
    if half.get("error"):
        return f"Could not put {label} back to {_value_text(value)}{stopped}: {half['error']}"
    return f"Put {label} back to {_value_text(value)}{stopped}: {_read_back(half)}"


def setting_sentence(trial: SettingTrial) -> str:
    """What happened to a setting, as the timeline, the wizard and the
    summary say it."""
    text = _write_sentence(trial.label, trial.write, trial.value)
    if trial.restore is not None:
        text += " " + _restore_sentence(trial.label, trial.restore, trial.original)
    elif trial.write and not trial.write.get("error"):
        text += " It was not put back."
    return text


def settings_for(session: "AuditSession", run: "DriverRun", **timings: Any) -> SettingsPass:
    """The run's settings pass, made when the driver first connects."""
    if run.settings is None:
        run.settings = SettingsPass(session, run)
        run.extra["settings"] = run.settings.to_dict
    for name, value in timings.items():
        setattr(run.settings, name, value)
    return run.settings
