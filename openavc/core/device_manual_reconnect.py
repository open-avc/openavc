"""Taking a device's connection over from the retry loop on request: Reconnect
for one clean attempt, a setup action for its whole run.

Reconnect (the device page's button; also how starting or stopping a
simulation hands a device over, and how a device that stopped retrying for a
missing program is retried once it is installed) ends any pause and any running
retry, starts from a clean slate (no failed-reconnect mark, no reason, no
attempt count), closes the driver and dials it once, then flushes the settings
queued while the device was away. A setup action brings its own transport, so
for its run the retry loop is stood down (``begin_setup``) and handed back
afterwards (``end_setup``); its ``request_reconnect`` reconnects the SAME driver
instance (``reconnect_in_place``), so the handler's ``self`` stays valid.

The rule worth knowing before changing it: whoever takes the connection over
puts the device on the intentional-disconnect list first, or closing its
transport reads as a drop and starts a retry loop racing the attempt. Reconnect
takes it off again when its one attempt ends, whatever happened; a setup action
holds it for the whole run, so ``reconnect_in_place`` leaves the list alone. A
failed attempt goes back to the retry loop unless the device refused its login
(more logins can trip a lockout).

Mixed into DeviceManager (core/device_manager.py). Logs under the device
manager's name, which is how the log view files a line as the device's.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from openavc.drivers.base import ConnectEndedError
from openavc.utils.logger import get_logger

if TYPE_CHECKING:
    from openavc.core.state_store import StateStore
    from openavc.drivers.base import BaseDriver

# The device manager's logger: utils/log_buffer.py files a line under the
# device category by this name, and the device page shows that category.
log = get_logger("openavc.core.device_manager")


class DeviceManualReconnectMixin:
    """Reconnect, and a setup action's hold on the device's connection.

    Holds no state of its own. From its host (DeviceManager) it reads
    ``state``, ``_devices`` and ``_intentional_disconnect``, and asks
    ``_live_driver`` and ``_connect_driver``; ``is_paused`` and ``_end_pause``
    (core/device_pause.py); ``_cancel_reconnect``, ``_start_reconnect``,
    ``_paused_for_refused_login`` and ``_refresh_usb_serial_port``
    (core/device_reconnect.py); ``_set_offline_reason``,
    ``_clear_offline_reason`` and ``_failure_text`` (core/device_away.py);
    ``_note_bridge_offline_if_down`` (core/device_bridges.py);
    ``_apply_pending_settings`` (core/pending_settings.py) and
    ``_schedule_web_ui_probe`` (core/device_web_ui.py).
    """

    state: StateStore
    _devices: dict[str, BaseDriver]
    _intentional_disconnect: set[str]

    async def reconnect_device(self, device_id: str) -> None:
        """Force disconnect and reconnect a device."""
        driver = self._live_driver(device_id)
        # A manual reconnect overrides a test-panel pause — clear the pause
        # bookkeeping so the flag can't go stale and the TTL backstop can't
        # fire a redundant resume later.
        if self.is_paused(device_id):
            self._end_pause(device_id)
        # Cancel any existing auto-reconnect task first
        await self._cancel_reconnect(device_id)
        self.state.set(f"device.{device_id}.reconnect_failed", None, source="device_manager")
        self._clear_offline_reason(device_id)
        self.state.set(f"device.{device_id}.reconnect_attempt", None, source="device_manager")
        # Suppress auto-reconnect during intentional disconnect
        self._intentional_disconnect.add(device_id)
        try:
            await self._disconnect_keeping_silence(driver)
            await self._reconnect_once(device_id, driver)
            # Probed whether or not the control protocol connected, as at
            # bring-up. Stopping simulation hands a device back through here,
            # and one added while simulating has not been probed at its own
            # address yet.
            self._schedule_web_ui_probe(device_id)
        finally:
            self._intentional_disconnect.discard(device_id)

    @staticmethod
    async def _disconnect_keeping_silence(driver: BaseDriver) -> None:
        """Close the driver for Reconnect. Reconnect is "try again" with
        nothing changed: a device dropped for its silence still stopped
        answering if this attempt gets no answer either, so its sentence
        outlives the disconnect, which forgets it."""
        silence = getattr(driver, "_silence_verdict", None)
        try:
            await driver.disconnect()
        except Exception:
            pass
        if silence is not None:
            driver._silence_verdict = silence

    async def _reconnect_once(self, device_id: str, driver: BaseDriver) -> None:
        """Reconnect's one attempt. A failure is published and, unless the
        device refused its login, handed to the reconnect loop."""
        try:
            self._refresh_usb_serial_port(device_id, driver)
            await self._connect_driver(device_id, driver)
            log.info(f"Reconnected device: {device_id}")
            # The reason was cleared up front by reconnect_device.
            self._note_bridge_offline_if_down(device_id, driver, driver.config or {})
            # Flush the queue, like every other path that brings a device up,
            # or a setting queued while the device was away survives the
            # Reconnect button (and the simulator's redirect, which reconnects
            # through here) and waits for some later connect nobody asks for.
            await self._apply_pending_settings(device_id)
        except ConnectEndedError:
            pass  # ended by a newer press, a pause or a removal
        except Exception as e:
            self.state.set(f"device.{device_id}.connected", False, source="device_manager")
            code = self._set_offline_reason(device_id, driver, exc=e)
            log.warning(f"Reconnect failed for {device_id}: {self._failure_text(device_id, e)}")
            # The manual attempt was this action's one try.
            if not self._paused_for_refused_login(device_id, code):
                self._start_reconnect(device_id)

    async def begin_setup(self, device_id: str) -> None:
        """Suppress auto-reconnect for the duration of a setup action.

        A setup action opens its own out-of-band transport, independent of the
        device's normal (often failing) one. Cancel any running reconnect loop
        and add the device to the intentional-disconnect set so the
        auto-reconnect machinery doesn't race the handler's own connection. The
        device's live transport (if any) is left untouched — an offline device
        is already down, and the handler doesn't use it. Pair with ``end_setup``.
        """
        self._live_driver(device_id)
        await self._cancel_reconnect(device_id)
        self._intentional_disconnect.add(device_id)

    async def end_setup(self, device_id: str) -> None:
        """Re-enable auto-reconnect after a setup action. If the device didn't
        come back online during the run (the handler didn't reconnect, or its
        reconnect failed), resume the normal auto-reconnect loop so it keeps
        trying. Idempotent and safe if the device was removed mid-run.
        """
        self._intentional_disconnect.discard(device_id)
        driver = self._devices.get(device_id)
        if driver is not None and not driver.get_state("connected"):
            self._start_reconnect(device_id)

    async def reconnect_in_place(self, device_id: str) -> None:
        """Reconnect the existing driver instance using its current config.

        Used by a setup action's ``request_reconnect`` after a config update —
        it reconnects the *same* driver instance (so the handler's `self` stays
        valid) with whatever settings ``request_config_update`` merged into
        ``self.config``. Does not touch the intentional-disconnect set: the
        setup runner owns that suppression for the whole run. Raises on connect
        failure so the handler can see it.
        """
        driver = self._live_driver(device_id)
        self._clear_offline_reason(device_id)
        try:
            await driver.disconnect()
        except Exception:
            pass
        await self._connect_driver(device_id, driver)
