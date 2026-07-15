"""
Simulation manager — launches the openavc-simulator subprocess and
redirects device connections to simulated endpoints.

The simulator is a separate application (openavc-simulator/) that runs
fake protocol servers. This module handles:
  - Spawning the simulator process with the right driver/device config
  - Swapping device connection addresses to localhost:sim_port
  - Restoring original connections when simulation stops
  - Preventing duplicate simulator processes
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from server.system_config import APP_DIR, DRIVER_DEFINITIONS_DIR, DRIVER_REPO_DIR
from server.utils.logger import get_logger
from server.utils.spawn import CREATE_NO_WINDOW

log = get_logger(__name__)

# Workspace paths (dev-only — openavc-drivers sibling repo)
_WORKSPACE_ROOT = APP_DIR.parent
_DRIVERS_DIR = _WORKSPACE_ROOT / "openavc-drivers"


class SimulationManager:
    """Manages the simulator subprocess and device connection redirection."""

    def __init__(self, engine: Any):
        self.engine = engine
        self._process: asyncio.subprocess.Process | None = None
        self._original_configs: dict[str, dict] = {}  # device_id → {host, port, transport}
        self._sim_ports: dict[str, int] = {}  # device_id → sim port
        self._active = False
        self._sim_ui_url: str | None = None
        self._starting = False  # prevents concurrent start attempts
        self._monitor_task: asyncio.Task | None = None
        # Background tasks that drain the subprocess's stdout/stderr so its
        # OS pipe buffers don't fill up and block uvicorn writes inside the
        # simulator (which would deadlock and kill the simulator).
        self._drain_tasks: list[asyncio.Task] = []

    @property
    def active(self) -> bool:
        return self._active

    @property
    def simulator_ui_url(self) -> str | None:
        return self._sim_ui_url

    @property
    def simulated_devices(self) -> list[str]:
        return list(self._sim_ports.keys())

    async def start(self, device_ids: list[str] | None = None) -> dict:
        """Start simulation for the specified devices (or all devices).

        Returns dict with device_id → sim_port mappings and the UI URL.
        """
        # Prevent concurrent starts and double-starts
        if self._starting:
            raise RuntimeError("Simulation is already starting")
        if self._active:
            raise RuntimeError("Simulation is already active")

        # Clean up any zombie process from a previous failed start
        await self._cleanup_process()

        self._starting = True
        try:
            return await self._do_start(device_ids)
        except Exception:
            # If start fails, clean up
            self._starting = False
            await self._cleanup_process()
            self._active = False
            self._sim_ports.clear()
            raise
        finally:
            self._starting = False

    def _device_sim_payload(self, device_id: str, cfg: dict) -> dict:
        """Build the launch/sync payload for one device.

        Used by BOTH the initial launch and the incremental ``sync()`` add
        path so the two can't diverge — the sync path historically sent only
        ``{driver_id, port}``, leaving an added device with no friendly name,
        no real host/port, an empty config, and (since v0.5.0) no
        ``child_entities``, so its children were silently absent from the
        simulator. ``child_entities`` lives at the top level of the device
        config (not under ``config``), alongside the connection fields.
        """
        device_cfg = cfg.get("config", {}) or {}
        return {
            "device_id": device_id,
            "driver_id": cfg.get("driver", ""),
            "device_name": cfg.get("name", device_id),
            "real_host": device_cfg.get("host", ""),
            "real_port": device_cfg.get("port", 0),
            "port": 0,  # auto-allocate
            "config": {k: v for k, v in device_cfg.items()
                       if k not in ("host", "port")},
            "child_entities": cfg.get("child_entities") or {},
        }

    async def _do_start(self, device_ids: list[str] | None) -> dict:
        dm = self.engine.devices
        project = self.engine.project
        if not project:
            raise RuntimeError("No project loaded")

        # Check simulator is available
        try:
            import simulator  # noqa: F401
        except ImportError:
            raise RuntimeError(
                "Simulator module not found. Make sure the simulator "
                "package is installed (it should be included with OpenAVC)."
            )

        # Determine which devices to simulate
        if device_ids is None:
            device_ids = list(dm._device_configs.keys())

        if not device_ids:
            raise RuntimeError("No devices in project to simulate")

        # Build simulator config
        devices_config = []
        for device_id in device_ids:
            cfg = dm._device_configs.get(device_id)
            if not cfg:
                log.warning("Device %s not found, skipping simulation", device_id)
                continue
            devices_config.append(self._device_sim_payload(device_id, cfg))

        if not devices_config:
            raise RuntimeError("No devices to simulate")

        # Build driver paths
        driver_paths = []
        if _DRIVERS_DIR.exists():
            driver_paths.append(str(_DRIVERS_DIR))
        if DRIVER_REPO_DIR.exists():
            driver_paths.append(str(DRIVER_REPO_DIR))
        if DRIVER_DEFINITIONS_DIR.exists():
            driver_paths.append(str(DRIVER_DEFINITIONS_DIR))

        if not driver_paths:
            raise RuntimeError("No driver paths found")

        sim_config = {
            "driver_paths": driver_paths,
            "devices": devices_config,
            "ui_port": 19500,
        }

        # Write config to temp file
        config_file = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, prefix="openavc_sim_",
        )
        json.dump(sim_config, config_file)
        config_file.close()
        config_path = config_file.name
        self._config_path = config_path

        log.info("Starting simulator with %d devices...", len(devices_config))
        log.info("Driver paths: %s", driver_paths)
        log.info("Config file: %s", config_path)

        # Spawn the simulator process.
        # In frozen (PyInstaller) builds, sys.executable is the .exe itself,
        # so we use --simulator flag which server/main.py dispatches to the
        # simulator entry point. In normal Python, use -m simulator.
        #
        # --no-auto-shutdown: when launched standalone the simulator stops
        # itself 5s after the last UI tab closes (nice CLI UX). When openavc
        # is the launcher, drivers depend on the simulator staying up
        # regardless of whether the Simulator UI tab is open.
        if getattr(sys, 'frozen', False):
            cmd = [sys.executable, "--simulator", "--config", config_path, "--no-auto-shutdown"]
        else:
            cmd = [sys.executable, "-m", "simulator", "--config", config_path, "--no-auto-shutdown"]

        try:
            self._process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=CREATE_NO_WINDOW,
            )
        except Exception as e:
            raise RuntimeError(f"Failed to start simulator process: {e}")

        # Drain stdout NOW, before the readiness wait. The readiness loop reads
        # only stderr (for uvicorn's ready marker); if nothing reads stdout, a
        # _sim.py that prints a large blob at import time fills the ~64 KB OS
        # pipe buffer and blocks the simulator until this loop times out (~4s).
        # stderr is drained after readiness (the loop owns it until then).
        self._drain_tasks = [
            asyncio.ensure_future(
                self._drain_stream(self._process.stdout, "simulator.stdout"),
            ),
        ]

        # Wait for the simulator to start up
        try:
            await self._await_simulator_ready(self._process)
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"Error waiting for simulator startup: {e}")

        # Now drain stderr too. Once the readiness loop exits, nothing else
        # reads stderr, so uvicorn would eventually block when its pipe buffer
        # fills, freezing the simulator and dropping client connections.
        self._drain_tasks.append(
            asyncio.ensure_future(
                self._drain_stream(self._process.stderr, "simulator.stderr"),
            ),
        )

        self._sim_ui_url = f"http://localhost:{sim_config['ui_port']}"

        # Query the simulator API for actual port assignments instead of
        # assuming sequential allocation (ports may differ if some are busy)
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                for attempt in range(10):
                    try:
                        resp = await session.get(
                            f"{self._sim_ui_url}/api/devices", timeout=aiohttp.ClientTimeout(total=2)
                        )
                        if resp.status == 200:
                            data = await resp.json()
                            for dev in data.get("devices", []):
                                did = dev.get("device_id")
                                port = dev.get("port")
                                if did and port:
                                    self._sim_ports[did] = port
                            break
                    except Exception:
                        await asyncio.sleep(0.3)
        except Exception as e:
            log.warning("Could not query simulator for port assignments: %s", e)

        # Fallback: if API query failed, use sequential assignment
        if not self._sim_ports:
            import socket
            log.warning("Falling back to sequential port assignment")
            port = 19000
            for dev_cfg in devices_config:
                device_id = dev_cfg["device_id"]
                while port < 19500:
                    try:
                        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                            s.bind(("127.0.0.1", port))
                        break
                    except OSError:
                        port += 1
                self._sim_ports[device_id] = port
                port += 1

        self._active = True

        # Redirect device connections
        await self._redirect_connections()

        log.info(
            "Simulation started: %d devices, UI at %s",
            len(self._sim_ports), self._sim_ui_url,
        )

        # Update system state
        self.engine.state.set("system.simulation_active", True, source="simulation")
        self.engine.state.set("system.simulation_ui_url", self._sim_ui_url, source="simulation")

        # Start monitoring the subprocess — if it dies externally, clean up
        self._monitor_task = asyncio.ensure_future(self._monitor_process())

        return {
            "devices": dict(self._sim_ports),
            "ui_url": self._sim_ui_url,
        }

    async def _await_simulator_ready(
        self,
        process: asyncio.subprocess.Process,
    ) -> None:
        """Block until the simulator subprocess reports it's accepting traffic.

        Uvicorn logs ``Uvicorn running on …`` or ``Application startup complete``
        to stderr once it's ready. We poll stderr in 100 ms slices for up to
        4 seconds, log each line as we see it so misbehaving startups aren't
        invisible (the stderr drain task only starts AFTER this loop exits —
        stdout is already being drained), and raise if the process exits early.

        Returns silently on success. Raises RuntimeError if the process exits
        during startup; warns and returns if it stays up but never prints the
        ready marker (probably fine — older uvicorn versions phrased it
        differently).
        """
        ready = False
        for _ in range(40):  # Up to 4 seconds
            await asyncio.sleep(0.1)
            if process.returncode is not None:
                stderr = ""
                if process.stderr:
                    stderr = (await process.stderr.read()).decode(errors="replace")
                # stdout is being drained to the logs by the task started in
                # _do_start, so don't read it here (the read would race the
                # drainer); point at the logs instead.
                raise RuntimeError(
                    f"Simulator exited with code {process.returncode}. "
                    f"stderr: {stderr[:500]} (stdout in simulator.stdout logs)"
                )
            if process.stderr:
                try:
                    chunk = await asyncio.wait_for(
                        process.stderr.read(4096), timeout=0.05
                    )
                except asyncio.TimeoutError:
                    continue
                if not chunk:
                    continue
                text = chunk.decode(errors="replace")
                # Forward each startup line so a misbehaving simulator's
                # diagnostics aren't lost. The stderr drain task only starts
                # AFTER this loop returns, so anything emitted here is otherwise
                # discarded the moment we hit the "ready" condition.
                for line in text.splitlines():
                    if line.strip():
                        log.info("simulator.stderr: %s", line)
                if "Uvicorn running" in text or "Application startup complete" in text:
                    ready = True
                    break

        if not ready and process.returncode is None:
            # Process is running but didn't report ready — assume it's ok.
            log.warning("Simulator started but readiness not confirmed; proceeding")

    async def _drain_stream(self, stream: asyncio.StreamReader | None, label: str) -> None:
        """Read a subprocess pipe forever, forwarding lines to our logger.

        Stops silently when the stream closes (subprocess exit) or the task
        is cancelled.
        """
        if stream is None:
            return
        try:
            while True:
                line = await stream.readline()
                if not line:
                    return
                text = line.decode(errors="replace").rstrip()
                if text:
                    log.debug("[%s] %s", label, text)
        except asyncio.CancelledError:
            return
        except Exception as e:
            log.warning("Stream drain (%s) ended: %s", label, e)

    async def stop(self) -> None:
        """Stop simulation and restore original device connections."""
        if not self._active:
            # Even if the in-memory flag is False, keep state keys honest in
            # case a previous run left them set (e.g. crash during _do_start
            # before the monitor task could clean up).
            self.engine.state.set("system.simulation_active", False, source="simulation")
            self.engine.state.set("system.simulation_ui_url", None, source="simulation")
            return

        log.info("Stopping simulation...")

        # Cancel process monitor
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
        self._monitor_task = None

        # Cancel stream drainers
        for t in self._drain_tasks:
            if not t.done():
                t.cancel()
        self._drain_tasks = []

        # Restore original connections
        await self._restore_connections()

        # Kill the simulator process
        await self._cleanup_process()

        self._sim_ports.clear()
        self._original_configs.clear()
        self._sim_ui_url = None
        self._active = False

        # Update system state
        self.engine.state.set("system.simulation_active", False, source="simulation")
        self.engine.state.set("system.simulation_ui_url", None, source="simulation")

    async def _monitor_process(self) -> None:
        """Watch the simulator subprocess. If it exits, clean up."""
        try:
            while self._active and self._process:
                if self._process.returncode is not None:
                    exit_code = self._process.returncode
                    log.info("Simulator process exited (code %s)", exit_code)
                    await self._restore_connections()
                    # Stop draining the now-closed pipes
                    for t in self._drain_tasks:
                        if not t.done():
                            t.cancel()
                    self._drain_tasks = []
                    self._process = None
                    self._sim_ports.clear()
                    self._original_configs.clear()
                    self._sim_ui_url = None
                    self._active = False
                    self.engine.state.set("system.simulation_active", False, source="simulation")
                    self.engine.state.set("system.simulation_ui_url", None, source="simulation")
                    event = "simulation.stopped" if exit_code == 0 else "simulation.crashed"
                    await self.engine.events.emit(event, {
                        "exit_code": exit_code,
                    })
                    return
                await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            pass

    async def _cleanup_process(self) -> None:
        """Terminate the simulator process if running."""
        if self._process and self._process.returncode is None:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self._process.kill()
                try:
                    await self._process.wait()
                except Exception:
                    pass
            except Exception:
                pass
            log.info("Simulator process stopped")
        self._process = None
        # Stop draining now-closed pipes
        for t in self._drain_tasks:
            if not t.done():
                t.cancel()
        self._drain_tasks = []
        if hasattr(self, "_config_path") and self._config_path:
            Path(self._config_path).unlink(missing_ok=True)
            self._config_path = None

    @staticmethod
    def _driver_transport_is_serial(driver: Any) -> bool:
        """True when the device's effective transport is serial.

        The simulator has no serial server — serial drivers are simulated over
        a TCP loopback stand-in (the same substitution the serial-over-IP
        bridge passthrough makes in Engine.resolved_device_config). Mirrors
        BaseDriver.connect's resolution order: an explicit device-config
        transport wins over the driver's DRIVER_INFO default.
        """
        config = getattr(driver, "config", None) or {}
        driver_info = getattr(driver, "DRIVER_INFO", None) or {}
        transport = config.get("transport") or driver_info.get("transport", "tcp")
        return transport == "serial"

    def _apply_sim_redirect(
        self, driver: Any, device_id: str, sim_port: int
    ) -> None:
        """Point one live driver at the simulator on 127.0.0.1:sim_port and
        record its original connection so _restore_original_config can undo it.

        A serial driver is flipped to TCP for the duration: the simulator
        serves TCP, so the driver must speak TCP to reach it. An HTTPS device
        (``ssl: true``) is flipped to plain HTTP the same way — simulated
        HTTP devices serve plain HTTP, so leaving TLS on would make every
        HTTPS-only device (ClickShare, Hue v2) fail its own simulator. Every
        other transport (tcp/udp/osc/mqtt) is served by the sim directly and
        keeps its declared settings.
        """
        self._original_configs[device_id] = {
            "host": driver.config.get("host", ""),
            "port": driver.config.get("port", 0),
            # Preserve absence as None so restore can delete the override and
            # let the DRIVER_INFO transport apply again (a serial driver has no
            # explicit transport in config until we add one here).
            "transport": driver.config.get("transport"),
            "ssl": driver.config.get("ssl"),
        }
        driver.config["host"] = "127.0.0.1"
        driver.config["port"] = sim_port
        if self._driver_transport_is_serial(driver):
            driver.config["transport"] = "tcp"
        if driver.config.get("ssl"):
            driver.config["ssl"] = False

    @staticmethod
    def _restore_original_config(driver: Any, orig: dict) -> None:
        """Restore a driver's saved connection (host, port, transport, ssl)."""
        driver.config["host"] = orig.get("host", "")
        driver.config["port"] = orig.get("port", 0)
        # Only touch transport when we actually recorded it. A None value means
        # there was no explicit override before redirect — remove the one we
        # added so the driver falls back to its DRIVER_INFO transport.
        if "transport" in orig:
            if orig["transport"] is None:
                driver.config.pop("transport", None)
            else:
                driver.config["transport"] = orig["transport"]
        if "ssl" in orig:
            if orig["ssl"] is None:
                driver.config.pop("ssl", None)
            else:
                driver.config["ssl"] = orig["ssl"]

    async def _redirect_connections(self) -> None:
        """Swap device host/port (and serial→tcp) to point at the simulator."""
        dm = self.engine.devices

        for device_id, sim_port in self._sim_ports.items():
            driver = dm._devices.get(device_id)
            if not driver:
                continue

            # Save original config + redirect to simulator (flips serial→tcp)
            self._apply_sim_redirect(driver, device_id, sim_port)

            log.info(
                "Redirected %s: %s:%s -> 127.0.0.1:%d",
                device_id,
                self._original_configs[device_id]["host"],
                self._original_configs[device_id]["port"],
                sim_port,
            )

            # Reconnect with new config
            try:
                await dm.reconnect_device(device_id)
            except Exception as e:
                log.warning("Failed to reconnect %s to simulator: %s", device_id, e)

    async def _restore_connections(self) -> None:
        """Restore original device host/port and reconnect."""
        dm = self.engine.devices

        for device_id, orig in self._original_configs.items():
            driver = dm._devices.get(device_id)
            if not driver:
                continue

            self._restore_original_config(driver, orig)

            log.info("Restored %s to %s:%s", device_id, orig["host"], orig["port"])

            try:
                await dm.reconnect_device(device_id)
            except Exception as e:
                log.warning("Failed to reconnect %s to real device: %s", device_id, e)

    async def sync(self) -> None:
        """Sync simulated devices with the current project.

        Called after project reload. Starts simulators for new devices,
        stops and restores connections for removed devices.
        """
        if not self._active or not self._process or self._process.returncode is not None:
            return

        dm = self.engine.devices
        current_device_ids = set(dm._device_configs.keys())
        simulated_ids = set(self._sim_ports.keys())

        # New devices that need simulators
        added = current_device_ids - simulated_ids
        # Removed devices that need cleanup
        removed = simulated_ids - current_device_ids

        # Re-apply redirects to existing simulated devices whose driver
        # instances may have been replaced by _sync_devices() during reload
        continuing = simulated_ids & current_device_ids
        for device_id in continuing:
            driver = dm._devices.get(device_id)
            if not driver:
                continue
            sim_port = self._sim_ports[device_id]
            if driver.config.get("host") != "127.0.0.1" or driver.config.get("port") != sim_port:
                self._apply_sim_redirect(driver, device_id, sim_port)
                log.info("Re-applied simulation redirect for %s to port %d", device_id, sim_port)
                try:
                    await dm.reconnect_device(device_id)
                except Exception as e:
                    log.warning("Failed to reconnect %s to simulator after reload: %s", device_id, e)

        if not added and not removed:
            return

        import aiohttp

        sim_api = self._sim_ui_url  # e.g., http://localhost:19500

        # Stop simulators for removed devices
        for device_id in removed:
            log.info("Simulation sync: removing %s", device_id)
            # Restore the original connection if we still have the device.
            orig = self._original_configs.get(device_id)
            if orig:
                driver = dm._devices.get(device_id)
                if driver:
                    self._restore_original_config(driver, orig)
            # Only forget the port slot when the stop actually succeeds (200)
            # or the instance is already gone (404). On any other outcome the
            # subprocess instance keeps running — dropping the slot would leak
            # its port; leaving it tracked lets the next sync retry the stop.
            stopped = False
            try:
                async with aiohttp.ClientSession() as session:
                    resp = await session.post(
                        f"{sim_api}/api/devices/{device_id}/stop",
                        timeout=aiohttp.ClientTimeout(total=5),
                    )
                    if resp.status in (200, 404):
                        stopped = True
                    else:
                        body = await resp.text()
                        log.warning(
                            "Simulator stop for removed device %s returned %s: %s",
                            device_id, resp.status, body[:200],
                        )
            except Exception as e:
                log.warning("Failed to stop simulator for removed device %s: %s", device_id, e)
            if stopped:
                self._original_configs.pop(device_id, None)
                self._sim_ports.pop(device_id, None)

        # Start simulators for new devices — send the SAME full payload as the
        # initial launch (name, real host/port, config, child_entities) so an
        # added device isn't a degraded simulation missing its children.
        for device_id in added:
            cfg = dm._device_configs.get(device_id)
            if not cfg:
                continue
            payload = self._device_sim_payload(device_id, cfg)
            payload.pop("device_id", None)  # carried in the URL path
            log.info("Simulation sync: adding %s (driver=%s)", device_id, payload["driver_id"])
            started_ok = False
            try:
                async with aiohttp.ClientSession() as session:
                    resp = await session.post(
                        f"{sim_api}/api/devices/{device_id}/start",
                        json=payload,
                        timeout=aiohttp.ClientTimeout(total=10),
                    )
                    if resp.status == 200:
                        started_ok = True
                        data = await resp.json()
                        sim_port = data.get("port", 0)
                        if sim_port:
                            self._redirect_device_to_sim(device_id, sim_port)
                            await self._reconnect_quietly(device_id)
                            log.info("Simulation sync: %s on port %d", device_id, sim_port)
                        else:
                            log.warning("Simulator started %s but reported no port", device_id)
                    elif resp.status == 400:
                        # A prior leak may have left an orphaned instance the
                        # simulator now reports as "already simulated". Adopt its
                        # running port instead of leaving the device pointed at
                        # its real address.
                        if not await self._adopt_existing_sim(sim_api, device_id):
                            body = await resp.text()
                            log.warning("Simulator refused device %s: %s", device_id, body[:200])
                    else:
                        body = await resp.text()
                        log.warning("Simulator refused device %s: %s", device_id, body[:200])
            except Exception as e:
                log.warning("Failed to start simulator for new device %s: %s", device_id, e)
                # If /start committed an instance server-side but our handling
                # then failed (e.g. response parse), roll it back so we don't
                # leak the instance + one of only 500 sim ports.
                if started_ok and device_id not in self._sim_ports:
                    await self._best_effort_stop(sim_api, device_id)

        if added or removed:
            log.info("Simulation sync complete: +%d -%d devices", len(added), len(removed))

    def _redirect_device_to_sim(self, device_id: str, sim_port: int) -> None:
        """Record the original config and point one device at the simulator."""
        dm = self.engine.devices
        self._sim_ports[device_id] = sim_port
        driver = dm._devices.get(device_id)
        if driver:
            self._apply_sim_redirect(driver, device_id, sim_port)

    async def _reconnect_quietly(self, device_id: str) -> None:
        try:
            await self.engine.devices.reconnect_device(device_id)
        except Exception as e:
            log.warning("Failed to reconnect %s to simulator: %s", device_id, e)

    async def _adopt_existing_sim(self, sim_api: str, device_id: str) -> bool:
        """Adopt an already-running instance's port after a 400 'already
        simulated' (a prior leak left it). Returns True on success."""
        import aiohttp
        try:
            async with aiohttp.ClientSession() as session:
                resp = await session.get(
                    f"{sim_api}/api/devices", timeout=aiohttp.ClientTimeout(total=5)
                )
                if resp.status != 200:
                    return False
                data = await resp.json()
            for dev in data.get("devices", []):
                if dev.get("device_id") == device_id and dev.get("port"):
                    self._redirect_device_to_sim(device_id, dev["port"])
                    await self._reconnect_quietly(device_id)
                    log.info(
                        "Adopted orphaned simulator instance for %s on port %d",
                        device_id, dev["port"],
                    )
                    return True
        except Exception as e:
            log.warning("Failed to adopt existing simulator instance for %s: %s", device_id, e)
        return False

    async def _best_effort_stop(self, sim_api: str, device_id: str) -> None:
        """POST /stop to roll back a leaked instance; swallow errors."""
        import aiohttp
        try:
            async with aiohttp.ClientSession() as session:
                await session.post(
                    f"{sim_api}/api/devices/{device_id}/stop",
                    timeout=aiohttp.ClientTimeout(total=5),
                )
            log.info("Rolled back leaked simulator instance for %s", device_id)
        except Exception as e:
            log.warning("Failed to roll back simulator instance for %s: %s", device_id, e)

    def status(self) -> dict:
        """Get simulation status for the API."""
        return {
            "active": self._active,
            "starting": self._starting,
            "ui_url": self._sim_ui_url,
            "devices": dict(self._sim_ports),
            "process_alive": (
                self._process is not None
                and self._process.returncode is None
            ),
        }
