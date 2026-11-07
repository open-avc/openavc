"""A device's Open Web UI page, found without its driver naming one: the part
of DeviceManager that decides when to look and keeps what was found.

A driver that leaves ``web_ui`` unset gets auto-detection; how a page is found
(the ports, the order, the URL) is core/web_ui_probe.py. The rule worth knowing
before changing this side: a device is probed once, at add time and again on a
connect until a page is found, and never when there is nothing honest to find.
That means a driver that set ``web_ui`` itself, a device with no host, an
HTTP-transport device (its page is its own configured address), and a device a
running simulation holds (its host is then this computer, which serves
OpenAVC's own page). Between the probe and a Discovery scan's seed, the first
URL found wins. A found URL lives in memory only, and is published as
``device.<id>.web_ui_url`` so a page already showing the device sees it arrive.

Mixed into DeviceManager (core/device_manager.py).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, TYPE_CHECKING

from openavc.core.device_reconnect import _log_task_exception

if TYPE_CHECKING:
    from openavc.core.state_store import StateStore
    from openavc.drivers.base import BaseDriver


def _auto_detects_web_ui(driver: Any) -> bool:
    """True for a driver that leaves ``web_ui`` unset, and so asks the
    platform to find the device's page."""
    return driver is not None and driver.DRIVER_INFO.get("web_ui") is None


def _probe_host(driver: Any) -> str | None:
    """The host to probe for the device's page, or None: it has no host, or
    it is an HTTP device, whose page comes straight from its configured
    address in the action resolver."""
    config = getattr(driver, "config", None) or {}
    host = config.get("host")
    if not host:
        return None
    transport = config.get("transport") or driver.DRIVER_INFO.get("transport")
    if transport == "http":
        return None
    return str(host)


class DeviceWebUiMixin:
    """Probing for a device's Open Web UI page, and keeping the URL found.

    Its state is set up by ``_init_web_ui``. From its host (DeviceManager) it
    reads ``state``, ``_devices`` and ``redirected_to_simulator`` and asks
    ``_simulation_gap``.
    """

    state: StateStore
    _devices: dict[str, BaseDriver]
    redirected_to_simulator: Callable[[str], bool] | None

    def _init_web_ui(self) -> None:
        # Auto-detected Open Web UI URLs, keyed by device id (see web_ui_probe).
        # Ephemeral by design — re-detected on add/connect, never persisted.
        self._detected_web_ui_urls: dict[str, str] = {}
        # In-flight web-UI probes, keyed by device id — holds a task reference
        # (so it isn't GC'd) and dedupes the add-time and connect-time triggers.
        self._web_ui_probe_tasks: dict[str, asyncio.Task] = {}

    def _forget_web_ui(self, device_id: str) -> None:
        """Drop a removed device's URL and stop its probe."""
        self._detected_web_ui_urls.pop(device_id, None)
        probe = self._web_ui_probe_tasks.pop(device_id, None)
        if probe is not None:
            probe.cancel()

    def _cancel_web_ui_probes(self) -> None:
        """Stop every probe in flight."""
        for task in list(self._web_ui_probe_tasks.values()):
            task.cancel()
        self._web_ui_probe_tasks.clear()

    def _schedule_web_ui_probe(self, device_id: str) -> None:
        """Kick off a one-shot Open Web UI probe for an auto-mode device.

        Idempotent and cheap to call at add time and on every (re)connect. Skips
        when the driver forced ``web_ui`` on or off, when a URL was already
        detected, when the device has no host to reach, or when it's an
        HTTP-transport device (its URL comes straight from config in the action
        resolver — no probe needed). The detected URL is stashed on
        ``_detected_web_ui_urls`` and surfaced by ``get_device_info``.

        Skipped too while a running simulation holds the device. Connected to
        the simulator, its host is this computer's loopback address, where
        the probe finds whatever this computer serves (OpenAVC itself on
        8080), and a simulated device has no web page of its own; with no
        simulator for its driver, it is kept off its own address altogether.
        A URL found at the device's own address before simulation started is
        kept.
        """
        host = self._web_ui_probe_host(device_id)
        if host is None:
            return
        task = asyncio.create_task(self._run_web_ui_probe(device_id, host))
        self._web_ui_probe_tasks[device_id] = task
        task.add_done_callback(lambda t, d=device_id: self._web_ui_probe_tasks.pop(d, None))
        task.add_done_callback(_log_task_exception)

    def _web_ui_probe_host(self, device_id: str) -> str | None:
        """The host to probe for this device's page now, or None when it is
        not to be probed (see :meth:`_schedule_web_ui_probe`)."""
        driver = self._devices.get(device_id)
        if not _auto_detects_web_ui(driver):
            return None
        if device_id in self._detected_web_ui_urls or device_id in self._web_ui_probe_tasks:
            return None
        if self._held_by_simulation(device_id):
            return None
        return _probe_host(driver)

    def _held_by_simulation(self, device_id: str) -> bool:
        """Is a running simulation holding this device: redirected to its
        simulator, or kept off its own address for want of one?"""
        if self._simulation_gap(device_id) is not None:
            return True
        redirected = self.redirected_to_simulator
        return bool(redirected is not None and redirected(device_id))

    async def _run_web_ui_probe(self, device_id: str, host: str) -> None:
        """Probe the device's host for a web UI and record any URL found."""
        from openavc.core.web_ui_probe import probe_web_ui

        url = await probe_web_ui(host)
        # Guard against a device removed while the probe was in flight.
        if device_id in self._devices:
            self._keep_first_web_ui_url(device_id, url)

    def seed_web_ui_url(self, device_id: str, url: str) -> None:
        """Record a web UI URL detected outside the probe (e.g. from a discovery
        scan's already-known open ports), so the button shows immediately.

        Only when the driver is in auto-detect mode (``web_ui`` unset); first
        writer wins, so a probe already in flight doesn't override it.
        """
        if _auto_detects_web_ui(self._devices.get(device_id)):
            self._keep_first_web_ui_url(device_id, url)

    def _keep_first_web_ui_url(self, device_id: str, url: str | None) -> None:
        """Record ``url`` unless one was found first (a probe and a discovery
        seed can both arrive; the first one stands)."""
        if url and device_id not in self._detected_web_ui_urls:
            self._record_web_ui_url(device_id, url)

    def _record_web_ui_url(self, device_id: str, url: str) -> None:
        """Keep a detected web UI URL for ``get_device_info`` and publish it as
        ``device.<id>.web_ui_url``. The probe finishes after a device page has
        fetched the device's actions, so the state change is what tells that
        page the Open Web UI action now exists. ``remove_device`` clears the
        key with the rest of the device's state."""
        self._detected_web_ui_urls[device_id] = url
        self.state.set(f"device.{device_id}.web_ui_url", url, source="device_manager")
