"""Writing a device's settings: live, or queued while the device is away, and
proof that they landed.

The doors are ``set_device_setting`` (a live write), ``await_setting_readback``
(the wait for a live write to read back) and ``store_pending_settings`` (the
queue). A setting written while a device is offline is queued in the device's
config (``pending_settings``, saved with the project) and sent on its next
connect. A live write and the queue's intake are checked against the driver's
declaration whoever the caller (``_checked_value``), and a live write settles
the queue's entry for that setting.

The rule worth knowing before changing it: a send that does not raise is not
proof the device got it. On UDP, or on a socket being redirected under the
driver, nothing raises and nothing arrives. So a setting the device reports
back (its ``state_key`` names a declared state variable) stays queued until the
read-back agrees inside the confirm window. One the device never reports, while
it reports other values, clears the way a setting with no read-back does.
Anything else stays queued for the next connect and says so on
``device.error.<id>``. A clear only ever drops the entry the pass took, never a
newer value queued since.

Mixed into DeviceManager (core/device_manager.py). Logs under the device
manager's name, which is how the log view files a line as the device's.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

from openavc.core.device_errors import DeviceNotFoundError, not_connected
from openavc.core.device_reconnect import _log_task_exception
from openavc.drivers.base import UnknownDeviceSettingError, validate_device_setting_value
from openavc.utils.logger import get_logger

if TYPE_CHECKING:
    from openavc.core.event_bus import EventBus
    from openavc.drivers.base import BaseDriver

# The device manager's logger: utils/log_buffer.py files a line under the
# device category by this name, and the device page shows that category.
log = get_logger("openavc.core.device_manager")


# A queued write used to be cleared the moment ``set_device_setting`` returned
# without raising, which on a datagram transport (or a socket being redirected
# under it) means nothing at all: the write vanished, the log said "Applied",
# and the value was gone. So a write whose value the device reports back is
# held in the queue until the read-back agrees.
#
# The window is two poll cycles plus slack. Two, not one, because the poll in
# flight when the write went out is still carrying the OLD value, and its reply
# lands after ours — one cycle would call a good write a loss. A device that
# isn't polled has only its own reply to the write, which arrives in
# milliseconds; the short window covers that and nothing else can arrive later.
_CONFIRM_UNPOLLED_WINDOW = 5.0
_CONFIRM_SLACK = 10.0
# A ceiling so an exotic poll cadence can't park a watcher for an hour. Above
# it the platform stops waiting and falls back to what it can see at the
# deadline, which for an un-refreshed key is "never reported" -> cleared.
_CONFIRM_MAX_WINDOW = 600.0
# How often the watcher re-reads state. Short, so a device that echoes the
# write confirms in a blink rather than sitting "pending" on the device card.
_CONFIRM_POLL_STEP = 0.25


def _readback_confirms(key: str, sdef: Any, expected: Any, actual: Any) -> bool:
    """Does a device's reported value agree with the setting we wrote?

    ``None`` is the platform's "declared, never reported" (see
    ``BaseDriver._init_state_variables``) and is never a confirmation.

    Otherwise the device is allowed to report the same value in its own form —
    ``"70"`` for ``70``, ``"on"`` for ``True``, an enum's label for its wire
    value — so the read-back is put through the setting's own declared schema,
    the same coercion the write went through. A value that schema refuses
    (out of range, not in ``values``) falls back to a plain text comparison
    rather than counting as a mismatch on a technicality.
    """
    if actual is None:
        return False
    if actual == expected:
        return True
    try:
        if validate_device_setting_value(key, sdef, actual) == expected:
            return True
    except Exception:
        pass
    return str(actual).strip().lower() == str(expected).strip().lower()


def readback_state_key(key: str, sdef: Any, state_vars: Any) -> Any:
    """The state variable a device setting reads back through, or None when
    the driver declares no such variable and there is nothing to confirm the
    setting against. The setting's ``state_key``, else its own key."""
    state_key = sdef.get("state_key", key) if isinstance(sdef, dict) else key
    return state_key if state_key in state_vars else None


def _unconfirmed_detail(key: str, expected: Any, actual: Any) -> str:
    """What a written setting that did not read back says, and that it stays queued."""
    if actual is None:
        return (
            f"Pending setting '{key}' was sent but the device has "
            f"reported nothing back, so there is no way to tell "
            f"whether {expected!r} landed. It stays queued and will "
            f"be sent again on the next connection."
        )
    return (
        f"Pending setting '{key}' was sent but the device still "
        f"reports {actual!r} instead of {expected!r}. It stays "
        f"queued and will be sent again on the next connection."
    )


@dataclass
class _PendingPass:
    """One pass over a device's queue: what it cleared on send and what waits."""

    device_id: str
    driver: Any
    defs: dict[str, Any]
    state_vars: dict[str, Any]
    applied: list[str] = field(default_factory=list)
    # The value as it sat in the QUEUE, before this pass coerced it — that
    # is what _clear_applied_pending compares against to tell "the entry I
    # took" from "an entry queued since".
    queued: dict[str, Any] = field(default_factory=dict)
    # key -> (the state variable it reads back through, the value sent)
    awaiting: dict[str, tuple[str, Any]] = field(default_factory=dict)


@dataclass
class _ReadbackWatch:
    """The written settings one watcher is still waiting to see reported."""

    driver: Any
    defs: dict[str, Any]
    outstanding: dict[str, tuple[str, Any]]
    confirmed: list[str] = field(default_factory=list)

    def take_confirmed(self) -> None:
        """Move each setting the device now reports as written to ``confirmed``."""
        for key, (state_key, expected) in list(self.outstanding.items()):
            if _readback_confirms(
                key, self.defs.get(key), expected, self.driver.get_state(state_key)
            ):
                self.confirmed.append(key)
                self.outstanding.pop(key, None)


def _checked_value(driver: BaseDriver, device_id: str, key: str, value: Any) -> Any:
    """``value`` for setting ``key`` checked against the driver's declaration
    and coerced to its declared type, for a live write and for the queue's
    intake, whoever the caller. A key the driver does not declare is refused,
    naming the setting and the device."""
    defs = driver.DRIVER_INFO.get("device_settings", {})
    if key not in defs:
        raise UnknownDeviceSettingError(
            f"Unknown device setting '{key}' for device '{device_id}'"
        )
    return validate_device_setting_value(key, defs[key], value)


class PendingSettingsMixin:
    """The setting doors, and the queue of settings waiting for a device, from
    intake to clear.

    Its state is set up by ``_init_pending``. From its host (DeviceManager) it
    reads ``events``, ``_devices`` and ``_device_configs`` and asks
    ``_live_driver``, ``_require_control_target`` and ``accepts_commands``
    (core/device_commands.py).
    """

    events: EventBus
    _devices: dict[str, BaseDriver]
    _device_configs: dict[str, dict[str, Any]]

    def _init_pending(self) -> None:
        # In-flight pending-setting confirmations, keyed by device id — the
        # watcher that decides whether a queued write actually landed before
        # the queue is cleared (see _confirm_pending_settings).
        self._pending_confirm_tasks: dict[str, asyncio.Task] = {}

    async def set_device_setting(
        self, device_id: str, key: str, value: Any
    ) -> Any:
        """Set a device setting value on a device by ID."""
        driver = self._live_driver(device_id)
        self._require_control_target(device_id)
        if not self.accepts_commands(device_id):
            raise not_connected(device_id)

        # Runtime value gate — the IDE editor's min/max/values/regex checks
        # are an authoring aid; scripts, macros, cloud, and raw REST bypass
        # them, so the write is validated (and coerced to the declared type)
        # here regardless of caller.
        value = _checked_value(driver, device_id, key, value)

        result = await driver.set_device_setting(key, value)
        # A live write settles the question the queue was holding. Without
        # this, a queued value the device never confirmed stays in the project
        # and is re-sent on the next connect — overwriting what was just set
        # here, days later, for no reason anyone could see.
        await self._clear_applied_pending(device_id, [key], {})
        return result

    async def await_setting_readback(
        self, device_id: str, key: str, expected: Any, *, timeout: float | None = None,
    ) -> tuple[bool, Any]:
        """Wait for a device setting just written to read back.

        The rule a queued write is confirmed by (:func:`_readback_confirms`
        within :meth:`_confirm_window`), for a caller that wrote the setting
        live and wants to know the same thing: ``(True, value)`` once the
        device reports ``expected`` (in its own form, if it likes), else
        ``(False, last value)`` when the window closes, the device drops or it
        goes. ``expected`` is the value as written, after
        ``validate_device_setting_value``. ``timeout`` shortens the window.

        A setting whose ``state_key`` is not a declared state variable has
        nothing to read back: ``(False, None)`` at once.
        """
        self._live_driver(device_id)
        return await self._wait_for_readback(device_id, key, expected, timeout)

    async def store_pending_settings(
        self, device_id: str, settings: dict[str, Any]
    ) -> None:
        """Store pending settings for a device (will be applied on next connect).

        Validated against the driver's schema when it is installed; an
        orphaned device stores them as given (``_queue_pending``).
        """
        config = self._device_configs.get(device_id)
        if config is None:
            raise DeviceNotFoundError(f"Device '{device_id}' not found")
        self._queue_pending(device_id, config, settings)

    def _cancel_pending_confirm(self, device_id: str) -> None:
        """Stop a device's read-back watcher, if one is running."""
        confirm = self._pending_confirm_tasks.pop(device_id, None)
        if confirm is not None:
            confirm.cancel()

    def _cancel_all_pending_confirms(self) -> None:
        """Stop every read-back watcher."""
        for task in list(self._pending_confirm_tasks.values()):
            task.cancel()
        self._pending_confirm_tasks.clear()

    def _queue_pending(
        self, device_id: str, config: dict[str, Any], settings: dict[str, Any]
    ) -> None:
        """Add ``settings`` to the queue in ``config`` (``store_pending_settings``,
        for a device the manager holds a config for).

        Validated at intake when the driver is available: a typo'd key or an
        out-of-range value used to sit in the queue and fail on every
        reconnect with only a warn log. Orphaned devices (driver not
        installed) store as-is — there's no schema to check against yet.
        """
        driver = self._devices.get(device_id)
        if driver is not None:
            settings = {
                key: _checked_value(driver, device_id, key, value)
                for key, value in settings.items()
            }

        if "pending_settings" not in config:
            config["pending_settings"] = {}
        config["pending_settings"].update(settings)
        log.info(f"[{device_id}] Stored {len(settings)} pending setting(s)")

    async def _apply_pending_settings(self, device_id: str) -> None:
        """Apply any pending device settings after a successful connect.

        A send that doesn't raise is not proof the device got it — on UDP, or
        on a socket being redirected under the driver, nothing raises and
        nothing arrives. So a setting whose value the device reports back (its
        ``state_key`` names a declared state variable) is NOT cleared here: it
        is handed to :meth:`_confirm_pending_settings`, which clears it once
        the read-back agrees and leaves it queued for the next connect when it
        doesn't. A setting with no read-back is cleared on send as before —
        there is nothing to check it against, and holding it forever would
        re-write it on every reconnect.
        """
        config = self._device_configs.get(device_id, {})
        pending = config.get("pending_settings", {})
        if not pending:
            return

        driver = self._devices.get(device_id)
        if driver is None:
            return

        run = _PendingPass(
            device_id,
            driver,
            defs=driver.DRIVER_INFO.get("device_settings", {}),
            state_vars=driver.DRIVER_INFO.get("state_variables", {}),
        )
        for key, value in pending.items():
            await self._send_pending(run, key, value)

        if run.applied:
            await self._clear_applied_pending(device_id, run.applied, run.queued)
        if run.awaiting:
            self._schedule_pending_confirm(device_id, run.awaiting, run.queued)

    async def _send_pending(self, run: _PendingPass, key: str, value: Any) -> None:
        """Send one queued setting, and note whether it clears now or waits
        for its read-back. A failure leaves it queued and says so."""
        device_id = run.device_id
        queued = value
        try:
            # Coerce against the schema before the value reaches the driver.
            # store_pending_settings coerces at intake, but a value can
            # enter the queue by a path that bypasses it (a project reload of
            # a hand-edited file), and the driver's own set_device_setting
            # doesn't validate.
            if key in run.defs:
                value = validate_device_setting_value(key, run.defs[key], value)
            self._require_control_target(device_id)
            await run.driver.set_device_setting(key, value)
            run.queued[key] = queued
            state_key = readback_state_key(key, run.defs.get(key), run.state_vars)
            if state_key is not None:
                run.awaiting[key] = (state_key, value)
                log.info(
                    f"[{device_id}] Sent pending setting '{key}' = {value!r} "
                    f"— holding it until the device reports it back"
                )
            else:
                run.applied.append(key)
                log.info(f"[{device_id}] Applied pending setting '{key}' = {value!r}")
        except Exception as e:
            log.warning(f"[{device_id}] Failed to apply pending setting '{key}': {e}")
            # Surface the failure beyond the server log — the key stays
            # queued and is retried on the next connect, but silently
            # retrying forever hid real problems (a firmware that
            # rejects the value, a bad queued write).
            await self._report_pending_error(
                device_id, f"Pending setting '{key}' failed to apply: {e}",
            )

    async def _report_pending_error(self, device_id: str, error: str) -> None:
        """Say on ``device.error.<id>`` what happened to a queued setting; a
        failure to say it is logged, never raised."""
        try:
            await self.events.emit(
                f"device.error.{device_id}",
                {
                    "device_id": device_id,
                    "error": error,
                    "source": "pending_settings",
                },
            )
        except Exception:
            log.exception(f"[{device_id}] Failed to emit device.error")

    async def _clear_applied_pending(
        self, device_id: str, applied_keys: list[str], queued_values: dict[str, Any]
    ) -> None:
        """Drop applied keys from the queue and have the engine persist it.

        Only drops a key whose queued entry is still the one this pass took: a
        confirmation can land a poll cycle after the write, and something can
        queue a NEWER value for the same setting in between. Clearing that one
        would throw away a write nobody has made yet. A key absent from
        ``queued_values`` carries no such opinion and is dropped as-is.
        """
        config = self._device_configs.get(device_id)
        if config is None:
            return
        pending = config.get("pending_settings")
        if not isinstance(pending, dict):
            return

        cleared = [
            key for key in applied_keys
            if key in pending
            and pending[key] == queued_values.get(key, pending.get(key))
        ]
        if not cleared:
            return
        for key in cleared:
            pending.pop(key, None)

        # If all pending settings were applied, remove the dict entirely
        if not pending:
            config.pop("pending_settings", None)

        # Notify the engine to persist the change
        await self.events.emit(
            "device.pending_settings_applied",
            {"device_id": device_id, "applied": cleared, "remaining": dict(pending)},
        )

    @staticmethod
    def _device_is_reporting(driver: BaseDriver) -> bool:
        """Has this device reported any declared reading at all?

        The question the read-back check has to answer when a written
        setting's own state variable is still ``None``: is this a device that
        doesn't report that particular value, or a device that isn't
        answering? Both look identical from the one key. A device that has
        reported nothing has said nothing, and silence is not agreement --
        which is exactly the shape the write vanished in.

        ``connected`` is excluded: the platform writes it, the device doesn't.
        """
        for prop in driver.DRIVER_INFO.get("state_variables", {}):
            if prop == "connected":
                continue
            if driver.get_state(prop) is not None:
                return True
        return False

    def _confirm_window(self, driver: BaseDriver) -> float:
        """Seconds to wait for a written setting to read back. See the
        ``_CONFIRM_*`` constants for why it is two poll cycles."""
        try:
            interval = float((getattr(driver, "config", None) or {}).get("poll_interval", 0) or 0)
        except (TypeError, ValueError):
            interval = 0.0
        if interval <= 0:
            return _CONFIRM_UNPOLLED_WINDOW
        return min(interval * 2 + _CONFIRM_SLACK, _CONFIRM_MAX_WINDOW)

    def _device_lost(self, device_id: str, driver: BaseDriver) -> bool:
        """Has the device a read-back is waiting on gone (removed, or replaced
        by a new driver) or dropped? Either ends the wait."""
        return self._devices.get(device_id) is not driver or not driver.get_state(
            "connected"
        )

    def _schedule_pending_confirm(
        self,
        device_id: str,
        awaiting: dict[str, tuple[str, Any]],
        queued_values: dict[str, Any],
    ) -> None:
        """Start (or replace) the read-back watcher for a device."""
        prior = self._pending_confirm_tasks.pop(device_id, None)
        if prior is not None:
            prior.cancel()
        task = asyncio.create_task(
            self._confirm_pending_settings(device_id, awaiting, queued_values)
        )
        self._pending_confirm_tasks[device_id] = task

        def _forget(finished: asyncio.Task, d: str = device_id) -> None:
            if self._pending_confirm_tasks.get(d) is finished:
                self._pending_confirm_tasks.pop(d, None)

        task.add_done_callback(_forget)
        task.add_done_callback(_log_task_exception)

    async def _confirm_pending_settings(
        self,
        device_id: str,
        awaiting: dict[str, tuple[str, Any]],
        queued_values: dict[str, Any],
    ) -> None:
        """Wait for each written setting to read back, then clear or keep it.

        Three outcomes per key, at the end of the window:

        * the device reports the value we wrote — cleared, the write landed;
        * the device reports something else — the write did not take. The key
          stays queued (so the next connect re-sends it) and a
          ``device.error.<id>`` says so. This is the case that used to be
          logged as "Applied" and silently lost;
        * the device never reports that value, *and is reporting others* —
          nothing to check against, so it is cleared exactly as an
          unread-back setting is. One real driver declares 28 settings its
          hardware never reads back; holding those forever would re-write
          them on every reconnect. A device that has reported **nothing** is
          a different case and falls under the previous one: silence is not
          agreement, and that is the shape the write vanished in.

        A device that drops mid-window leaves everything queued: the reconnect
        applies it again, and the disconnect is already on the card.
        """
        driver = self._devices.get(device_id)
        if driver is None:
            return
        watch = _ReadbackWatch(
            driver, driver.DRIVER_INFO.get("device_settings", {}), dict(awaiting),
        )
        deadline = time.monotonic() + self._confirm_window(driver)
        await self._watch_readbacks(device_id, watch, deadline)
        unreported = await self._judge_unconfirmed(device_id, watch)

        settled = watch.confirmed + unreported
        if settled:
            await self._clear_applied_pending(device_id, settled, queued_values)

    async def _watch_readbacks(
        self, device_id: str, watch: _ReadbackWatch, deadline: float,
    ) -> None:
        """Re-read until every setting reads back or the window closes. A
        device that goes or drops first leaves the rest queued, unjudged."""
        while True:
            watch.take_confirmed()
            if not watch.outstanding or time.monotonic() >= deadline:
                return
            if self._device_lost(device_id, watch.driver):
                # Gone or dropped — leave the rest queued for the reconnect.
                watch.outstanding.clear()
                return
            await asyncio.sleep(_CONFIRM_POLL_STEP)

    async def _judge_unconfirmed(self, device_id: str, watch: _ReadbackWatch) -> list[str]:
        """At the window's end, the settings that did not read back: those a
        talking device never reports are returned to be cleared, and every
        other one stays queued and says why."""
        reporting = self._device_is_reporting(watch.driver)
        unreported: list[str] = []
        for key, (state_key, expected) in watch.outstanding.items():
            actual = watch.driver.get_state(state_key)
            if actual is None and reporting:
                unreported.append(key)
                log.info(
                    f"[{device_id}] Pending setting '{key}' = {expected!r} sent; "
                    f"the device never reports '{state_key}', so there is "
                    f"nothing to confirm it against"
                )
                continue
            detail = _unconfirmed_detail(key, expected, actual)
            log.warning(f"[{device_id}] {detail}")
            await self._report_pending_error(device_id, detail)
        return unreported

    async def _wait_for_readback(
        self, device_id: str, key: str, expected: Any, timeout: float | None,
    ) -> tuple[bool, Any]:
        """``await_setting_readback``, for a device the manager holds."""
        driver = self._devices[device_id]
        sdef = driver.DRIVER_INFO.get("device_settings", {}).get(key)
        state_key = readback_state_key(
            key, sdef, driver.DRIVER_INFO.get("state_variables", {}),
        )
        if state_key is None:
            return False, None
        window = self._confirm_window(driver)
        deadline = time.monotonic() + (window if timeout is None else min(window, timeout))
        while True:
            actual = driver.get_state(state_key)
            if _readback_confirms(key, sdef, expected, actual):
                return True, actual
            if time.monotonic() >= deadline or self._device_lost(device_id, driver):
                return False, actual
            await asyncio.sleep(_CONFIRM_POLL_STEP)
