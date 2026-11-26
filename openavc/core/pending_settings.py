"""Writing a device's settings: live, or queued while the device is away, and
proof that they landed.

The doors are ``set_device_setting`` (a live write), ``await_setting_readback``
(the wait for a live write to read back) and ``store_pending_settings`` (the
queue). A setting written while a device is offline is queued in the device's
config (``pending_settings``, saved with the project) and sent on its next
connect. A live write, the queue's intake and every queued value before it is
sent are checked against the driver's declaration whoever the caller
(``_checked_value``): a queue stored with no driver to check it against, or
carried to a driver that declares other settings, meets the one that sends it,
and a value it refuses leaves the queue unsent and says so. A live write
settles the queue's entry for that setting.

A setting the driver marks secret (``drivers/secret_params.is_secret_device_setting``,
the one rule every setting door asks) is a login, and the queue never holds
one, since the queue is saved with the project and travels in every copy of
it: the intake refuses one (``SecretSettingNotQueued``), and one already
queued (a project that came in holding it) is refused before it is sent and
taken off the queue. A live write sends it, the device's masking learning its
value first, as it learns a secret command parameter's. Every place a queue
or a write is shown (the settings read, the cleared announcement, a write's
answer) gives a secret setting as ``***`` (``shown_settings``).

The rule worth knowing before changing it: a send that does not raise is not
proof the device got it. On UDP, or on a socket being redirected under the
driver, nothing raises and nothing arrives. So a setting the device reports
back (its ``state_key`` names a declared state variable) stays queued until the
read-back agrees inside the confirm window. One the device never reports, while
it reports other values, clears the way a setting with no read-back does.
Anything else stays queued for the next connect and says so on
``device.error.<id>``. A clear only ever drops the entry the pass took, never a
newer value queued since.

Where the queue is kept is the device manager's ``queue keeper``: on its own
it keeps each queue in its record of the device (``InMemoryQueue``); the
engine gives it one that keeps them in the project too, each intake and each
clear a change through the project's change door, taken by the running
system once the file holds it (core/pending_queue.py). The running queue is
never ahead of the file, so a clear whose save fails leaves the entry queued
in both, sent again on the next connect. A queue that comes in with a project
is handed to the device in place (``take_project_queue``), never by adding
the device again, and a connected device is sent it at the bring-up.

Mixed into DeviceManager (core/device_manager.py). Logs under the device
manager's name, which is how the log view files a line as the device's.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

from openavc.core.device_errors import DeviceNotFoundError, device_error_text, not_connected
from openavc.core.device_reconnect import _log_task_exception
from openavc.drivers.base import (
    DeviceSettingValueError,
    UnknownDeviceSettingError,
    validate_device_setting_value,
)
from openavc.drivers.param_labels import param_label
from openavc.drivers.registry import get_driver_class
from openavc.drivers.secret_params import is_secret_device_setting
from openavc.drivers.setting_model import read_setting
from openavc.utils.log_redaction import REDACTED, get_secret_registry, secret_text
from openavc.utils.logger import get_logger

if TYPE_CHECKING:
    from openavc.core.event_bus import EventBus
    from openavc.drivers.base import BaseDriver

# The device manager's logger: utils/log_buffer.py files a line under the
# device category by this name, and the device page shows that category.
log = get_logger("openavc.core.device_manager")

# A key a queue does not hold, told apart from one it holds as None.
_NOT_HELD = object()
# A report the setting's declaration refuses.
_NOT_READ = object()


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


def readback_confirms(key: str, sdef: Any, expected: Any, actual: Any) -> bool:
    """Does a device's reported value agree with the setting we wrote?

    THE rule for it: a queued write's confirmation, a live write's
    (``await_setting_readback``), and every comparison the Device Audit makes
    on a variable a setting reads back through (``audit/setting_reading``).
    ``expected`` is the value as written, after
    ``validate_device_setting_value``; ``actual`` the device's report.

    ``None`` is the platform's "declared, never reported" (see
    ``BaseDriver._init_state_variables``) and is never a confirmation.

    Otherwise the device is allowed to report the same value in its own form —
    ``"70"`` for ``70``, ``"on"`` for ``True``, an enum's label for its wire
    value — so the read-back is put through the setting's own declared schema,
    the same coercion the write went through. A report the schema reads as
    another of the setting's declared ``values`` is that option, never the
    one written, even one that differs only in case. Anything else (a
    report the schema refuses, free text) is compared as text, apart from
    case and surrounding spaces, rather than counting as a mismatch on a
    technicality.
    """
    if actual is None:
        return False
    if actual == expected:
        return True
    try:
        read = validate_device_setting_value(key, sdef, actual)
    except Exception:
        read = _NOT_READ
    if read == expected:
        return True
    if read is not _NOT_READ and _names_options(sdef):
        return False  # another of the setting's own options
    return str(actual).strip().lower() == str(expected).strip().lower()


def _names_options(sdef: Any) -> bool:
    """A declaration that lists the values a setting takes."""
    return isinstance(sdef, dict) and isinstance(sdef.get("values"), list) and bool(sdef["values"])


def readback_state_key(key: str, sdef: Any, state_vars: Any) -> Any:
    """The state variable a device setting reads back through, or None when
    the driver declares no such variable and there is nothing to confirm the
    setting against. The setting's ``state_key``, as ``setting_model``
    reads it."""
    state_key = read_setting(key, sdef).state_key
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
    # key -> its queued value, for each the driver's declaration refused:
    # never sent, and taken off the queue.
    refused: dict[str, Any] = field(default_factory=dict)
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
            if readback_confirms(
                key, self.defs.get(key), expected, self.driver.get_state(state_key)
            ):
                self.confirmed.append(key)
                self.outstanding.pop(key, None)


def _still_taken(pending: dict[str, Any], keys: list[str], taken: dict[str, Any]) -> list[str]:
    """Each of ``keys`` whose queued entry is still the value a pass took
    (``taken``); one absent from ``taken`` counts as taken as it stands."""
    return [
        key for key in keys
        if key in pending and pending[key] == taken.get(key, pending.get(key))
    ]


def remaining_after(pending: dict[str, Any], taken: dict[str, Any]) -> dict[str, Any]:
    """``pending`` without each entry a clear took that is still the value
    it took (``taken``, key -> that value): what a clear leaves of a queue,
    for the project's copy and the running one alike."""
    gone = set(_still_taken(pending, list(taken), taken))
    return {key: value for key, value in pending.items() if key not in gone}


def _hold_queue(config: dict[str, Any], queue: dict[str, Any]) -> dict[str, Any]:
    """Make ``queue`` the queue a device's record holds, with no entry for
    an empty one; the queue it held before."""
    held = config.pop("pending_settings", None) or {}
    if queue:
        config["pending_settings"] = dict(queue)
    return held


def _gains_an_entry(held: dict[str, Any], queue: dict[str, Any]) -> bool:
    """Does ``queue`` hold an entry ``held`` did not, or another value?"""
    return any(held.get(key, _NOT_HELD) != value for key, value in queue.items())


@dataclass(frozen=True)
class PendingClear:
    """Entries a pass is done with: ``taken`` maps each key to the queued
    value the pass took (sent and settled, or refused), and ``refused`` names
    the keys the driver's declaration refused."""

    device_id: str
    taken: dict[str, Any]
    refused: frozenset[str] = frozenset()


def _cleared_payload(clear: PendingClear, pending: dict[str, Any]) -> dict[str, Any]:
    """What ``device.pending_settings_applied`` says: the keys a pass cleared,
    split into those sent and those the driver refused, and the queue left."""
    return {
        "device_id": clear.device_id,
        "applied": [key for key in clear.taken if key not in clear.refused],
        "refused": [key for key in clear.taken if key in clear.refused],
        "remaining": dict(pending),
    }


class SecretSettingNotQueued(DeviceSettingValueError):
    """A setting its driver marks secret, sent to the queue: a login is
    entered on the device's page while the device is online, never queued.
    Names the setting by its label, never its value."""

    def __init__(self, key: str, definition: Any) -> None:
        super().__init__(
            f"{param_label(key, definition)} is a login. "
            f"Enter it on the device's page while the device is online"
        )


def _secret_refusal(driver: Any, key: str) -> SecretSettingNotQueued | None:
    """The queue's refusal of setting ``key`` when ``driver`` (the running
    driver, its class, or None) marks it secret, else None."""
    if not is_secret_device_setting(driver, key):
        return None
    declared = getattr(driver, "DRIVER_INFO", {}).get("device_settings") or {}
    return SecretSettingNotQueued(key, declared.get(key))


def shown_settings(driver: Any, settings: dict[str, Any]) -> dict[str, Any]:
    """``settings`` (key -> value) as a door may answer, log or announce
    them: each secret setting's value as ``***``, an empty one as it is."""
    return {
        key: REDACTED if value not in (None, "") and is_secret_device_setting(driver, key) else value
        for key, value in settings.items()
    }


def _learn_secret(driver: Any, device_id: str, key: str, value: Any) -> None:
    """Have the device's masking learn a secret setting's value, so its
    traffic, a reply that echoes it, and every log line mask it, as a secret
    command parameter's is (``core/command_gates.register_secret_values``)."""
    text = secret_text(value)
    if text is not None and is_secret_device_setting(driver, key):
        get_secret_registry().add_runtime_secret(device_id, text)


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


class InMemoryQueue:
    """The queue keeper of a device manager with nothing else to keep its
    queues in: each in its record of the device, taken at once. The engine's
    keeper (core/pending_queue.py) has the same two doors."""

    def __init__(self, manager: PendingSettingsMixin) -> None:
        self._manager = manager

    async def add(self, device_id: str, settings: dict[str, Any]) -> None:
        """Add checked ``settings`` to the device's queue."""
        self._manager.take_pending(device_id, settings)

    async def drop(self, clear: PendingClear) -> None:
        """Drop the entries ``clear`` took that are still queued as taken."""
        self._manager.drop_taken(clear)
        await self._manager.announce_cleared(clear)


class PendingSettingsMixin:
    """The setting doors, and the queue of settings waiting for a device, from
    intake to clear.

    Its state is set up by ``_init_pending``. From its host (DeviceManager) it
    reads ``events``, ``_devices`` and ``_device_configs`` and asks
    ``_live_driver``, ``_require_control_target`` and ``accepts_commands``
    (core/device_commands.py). Its queue keeper (``keep_queue_with``) decides
    where a queue is kept; the keeper calls ``take_pending``, ``drop_taken``
    and ``announce_cleared`` back.
    """

    events: EventBus
    _devices: dict[str, BaseDriver]
    _device_configs: dict[str, dict[str, Any]]

    def _init_pending(self) -> None:
        # In-flight pending-setting confirmations, keyed by device id — the
        # watcher that decides whether a queued write actually landed before
        # the queue is cleared (see _confirm_pending_settings).
        self._pending_confirm_tasks: dict[str, asyncio.Task] = {}
        self._queue_keeper: Any = InMemoryQueue(self)
        # Devices handed a queue in place while connected, sent it at the
        # next bring-up (take_project_queue, _send_handed_queues).
        self._handed_queues: set[str] = set()

    def keep_queue_with(self, keeper: Any) -> None:
        """Keep every queue with ``keeper`` (``add`` and ``drop``, as
        ``InMemoryQueue`` has them) from now on."""
        self._queue_keeper = keeper

    def queued_settings(self, device_id: str) -> dict[str, Any]:
        """The device's queue as the running system holds it, a copy."""
        config = self._device_configs.get(device_id) or {}
        pending = config.get("pending_settings")
        return dict(pending) if isinstance(pending, dict) else {}

    def queued_settings_shown(self, device_id: str) -> dict[str, Any]:
        """The device's queue as a door may show it: ``queued_settings``
        with each secret setting as ``***``."""
        return shown_settings(self.declaring_driver(device_id), self.queued_settings(device_id))

    def declaring_driver(self, device_id: str) -> Any:
        """What declares the device's settings: its running driver, else the
        installed class of the driver its record names (a disabled device, one
        whose driver failed), else None (its driver is not installed)."""
        driver = self._devices.get(device_id)
        if driver is not None:
            return driver
        record = self._device_configs.get(device_id) or {}
        driver_id = record.get("driver")
        return get_driver_class(driver_id) if isinstance(driver_id, str) else None

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
        # here regardless of caller. A secret's value is learned by the
        # device's masking as given, so a refused one is masked too, and as
        # the driver gets it.
        _learn_secret(driver, device_id, key, value)
        value = _checked_value(driver, device_id, key, value)
        _learn_secret(driver, device_id, key, value)

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

        The rule a queued write is confirmed by (:func:`readback_confirms`
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
        orphaned device stores them as given (``_checked_intake``). A setting
        the driver marks secret is refused (``SecretSettingNotQueued``), and
        then nothing is stored. Kept by
        the queue keeper, which with the engine's saves them to the project
        first and raises when it cannot; the running queue takes them once
        it has.
        """
        if device_id not in self._device_configs:
            raise DeviceNotFoundError(f"Device '{device_id}' not found")
        await self._queue_keeper.add(device_id, self._checked_intake(device_id, settings))

    def take_pending(self, device_id: str, settings: dict[str, Any]) -> None:
        """Add checked ``settings`` to the running queue (a keeper's intake,
        once the queue is kept). A device gone by then has no queue to take."""
        config = self._device_configs.get(device_id)
        if config is None:
            return
        config.setdefault("pending_settings", {}).update(settings)
        log.info(f"[{device_id}] Stored {len(settings)} pending setting(s)")

    def drop_taken(self, clear: PendingClear) -> None:
        """Drop from the running queue each entry ``clear`` took that is
        still the value it took; a queue left empty goes."""
        config = self._device_configs.get(clear.device_id)
        pending = config.get("pending_settings") if config is not None else None
        if isinstance(pending, dict):
            _hold_queue(config, remaining_after(pending, clear.taken))

    async def announce_cleared(self, clear: PendingClear) -> None:
        """Say on ``device.pending_settings_applied`` what a clear took and
        the queue it left."""
        await self.events.emit(
            "device.pending_settings_applied",
            _cleared_payload(clear, self.queued_settings_shown(clear.device_id)),
        )

    def take_project_queue(self, device_id: str, queue: dict[str, Any]) -> None:
        """The device's queue as a project that came in holds it (a reload,
        a restore, a whole-project save), taken in place: a queue is never a
        reason to add the device again. A connected device given an entry it
        did not hold is sent its queue at the next bring-up
        (``_send_handed_queues``), as the reconnect a re-add made used to."""
        config = self._device_configs.get(device_id)
        if config is None:
            return
        held = _hold_queue(config, queue)
        if _gains_an_entry(held, queue) and self.accepts_commands(device_id):
            self._handed_queues.add(device_id)

    async def _send_handed_queues(self, dialed: set[str]) -> None:
        """Send each queue handed in place to a device still connected; one
        the bring-up just ``dialed`` was sent its queue by the connect."""
        handed, self._handed_queues = self._handed_queues, set()
        for device_id in handed - dialed:
            if not self.accepts_commands(device_id):
                continue
            try:
                await self._apply_pending_settings(device_id)
            except Exception:  # Catch-all: one device's queue never costs the others theirs
                log.exception(f"[{device_id}] Sending its queued settings failed")

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

    def _checked_intake(self, device_id: str, settings: dict[str, Any]) -> dict[str, Any]:
        """``settings`` as the queue's intake stores them.

        Validated at intake when the driver is available: a typo'd key or an
        out-of-range value used to sit in the queue and fail on every
        reconnect with only a warn log. A device no driver runs (waiting for
        its driver, disabled, its driver failed) stores as given, there being
        no schema to check against yet; the send checks it
        (:meth:`_send_pending`). A setting the driver marks secret is refused
        either way, by the running driver's declaration or the installed
        class's.
        """
        for key in settings:
            refusal = _secret_refusal(self.declaring_driver(device_id), key)
            if refusal is not None:
                raise refusal
        driver = self._devices.get(device_id)
        if driver is None:
            return dict(settings)
        return {
            key: _checked_value(driver, device_id, key, value)
            for key, value in settings.items()
        }

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

        if run.applied or run.refused:
            await self._clear_applied_pending(
                device_id, run.applied, run.queued, refused=run.refused,
            )
        if run.awaiting:
            self._schedule_pending_confirm(device_id, driver, run.awaiting, run.queued)

    async def _send_pending(self, run: _PendingPass, key: str, value: Any) -> None:
        """Send one queued setting, and note whether it clears now or waits
        for its read-back. One the driver's declaration refuses leaves the
        queue unsent (:meth:`_refuse_pending`); a failed send leaves it
        queued and says so."""
        device_id = run.device_id
        queued = value
        secret = _secret_refusal(run.driver, key)
        if secret is not None:
            # Queued before the queue refused secrets, or by a project that
            # came in holding one: never sent from here, and off the queue,
            # so out of the project.
            await self._refuse_pending(run, key, queued, secret)
            return
        try:
            # Checked against the declaration before it reaches the driver,
            # whose own set_device_setting doesn't validate. Intake checks it
            # too, but a queue can be stored with no driver to check it
            # against (waiting for its driver, disabled, its driver failed),
            # or carried into a driver that declares other settings (an edit,
            # a reload), or hand-edited into the project file.
            value = _checked_value(run.driver, device_id, key, value)
        except DeviceSettingValueError as refusal:
            await self._refuse_pending(run, key, queued, refusal)
            return
        try:
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
                device_id,
                f"Pending setting '{key}' failed to apply: {device_error_text(e)}",
            )

    async def _refuse_pending(
        self, run: _PendingPass, key: str, queued: Any, refusal: DeviceSettingValueError,
    ) -> None:
        """A queued setting this driver's declaration refuses, or a secret
        one: not sent, and taken off the queue, since no connect would ever
        send it. The same refusal intake gives, said on ``device.error.<id>``."""
        run.refused[key] = queued
        log.warning(f"[{run.device_id}] Pending setting '{key}' was not sent: {refusal}")
        await self._report_pending_error(
            run.device_id,
            f"Pending setting '{key}' was not sent: {refusal}. It is no longer queued.",
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
        self, device_id: str, applied_keys: list[str], queued_values: dict[str, Any],
        *, refused: dict[str, Any] | None = None,
    ) -> None:
        """Drop applied keys, and the ``refused`` ones (key -> the value the
        pass took), from the queue, through the queue keeper.

        Only drops a key whose queued entry is still the one this pass took: a
        confirmation can land a poll cycle after the write, and something can
        queue a NEWER value for the same setting in between. Clearing that one
        would throw away a write nobody has made yet. A key absent from
        ``queued_values`` is taken as its entry stands now, and the keeper
        drops that value only, wherever the queue is kept and however long
        its save waits.
        """
        config = self._device_configs.get(device_id)
        if config is None:
            return
        pending = config.get("pending_settings")
        if not isinstance(pending, dict):
            return

        refused = refused or {}
        values = {**queued_values, **refused}
        cleared = _still_taken(pending, [*applied_keys, *refused], values)
        if not cleared:
            return
        taken = {key: values.get(key, pending[key]) for key in cleared}
        await self._queue_keeper.drop(PendingClear(device_id, taken, frozenset(refused)))

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
        ``_CONFIRM_*`` constants for why it is two poll cycles: cycles of the
        interval the driver's poll loop runs at (running_poll_interval), which
        a Python driver may choose for itself, not its config's."""
        interval = driver.running_poll_interval()
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
        driver: BaseDriver,
        awaiting: dict[str, tuple[str, Any]],
        queued_values: dict[str, Any],
    ) -> None:
        """Start (or replace) the read-back watcher for ``driver``, the
        instance the pass sent to. None when that instance no longer runs
        the device (removed, or replaced while the pass was sending): its
        settings stay queued, and a watcher the replacement started is left
        alone."""
        if self._devices.get(device_id) is not driver:
            return
        prior = self._pending_confirm_tasks.pop(device_id, None)
        if prior is not None:
            prior.cancel()
        task = asyncio.create_task(
            self._confirm_pending_settings(device_id, driver, awaiting, queued_values)
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
        driver: BaseDriver,
        awaiting: dict[str, tuple[str, Any]],
        queued_values: dict[str, Any],
    ) -> None:
        """Wait for each written setting to read back on ``driver``, the
        instance it was sent to, then clear or keep it.

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
        applies it again, and the disconnect is already on the card. So does
        one whose instance goes (removed, or replaced): the retire step ends
        the watcher first (core/device_retire.py), and one that wakes to find
        its instance gone judges nothing.
        """
        if self._devices.get(device_id) is not driver:
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
        device that goes or drops first leaves the rest queued, unjudged:
        asked before anything is read, so a watcher that wakes past its
        deadline to find its instance gone says nothing about it."""
        while True:
            if self._device_lost(device_id, watch.driver):
                # Gone or dropped — leave the rest queued for the reconnect.
                watch.outstanding.clear()
                return
            watch.take_confirmed()
            if not watch.outstanding or time.monotonic() >= deadline:
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
            if readback_confirms(key, sdef, expected, actual):
                return True, actual
            if time.monotonic() >= deadline or self._device_lost(device_id, driver):
                return False, actual
            await asyncio.sleep(_CONFIRM_POLL_STEP)
