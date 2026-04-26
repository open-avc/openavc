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

log = get_logger(__name__)

# Workspace paths (dev-only — openavc-drivers sibling repo)
_WORKSPACE_ROOT = APP_DIR.parent
_DRIVERS_DIR = _WORKSPACE_ROOT / "openavc-drivers"


class SimulationManager:
    """Manages the simulator subprocess and device connection redirection."""

    def __init__(self, engine: Any):
        self.engine = engine
        self._process: asyncio.subprocess.Process | None = None
        self._original_configs: dict[str, dict] = {}  # device_id → {host, port}
        self._sim_ports: dict[str, int] = {}  # device_id → sim port
        self._active = False
        self._sim_ui_url: str | None = None
        self._starting = False  # prevents concurrent start attempts
        self._monitor_task: asyncio.Task | None = None

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

            driver_id = cfg.get("driver", "")
            device_cfg = cfg.get("config", {})

            devices_config.append({
                "device_id": device_id,
                "driver_id": driver_id,
                "device_name": cfg.get("name", device_id),
                "real_host": device_cfg.get("host", ""),
                "real_port": device_cfg.get("port", 0),
                "port": 0,  # auto-allocate
                "config": {k: v for k, v in device_cfg.items()
                           if k not in ("host", "port")},
            })

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
        if getattr(sys, 'frozen', False):
            cmd = [sys.executable, "--simulator", "--config", config_path]
        else:
            cmd = [sys.executable, "-m", "simulator", "--config", config_path]

        try:
            self._process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except Exception as e:
            raise RuntimeError(f"Failed to start simulator process: {e}")

        # Wait for the simulator to start up
        # Read stderr for startup messages (uvicorn logs to stderr)
        try:
            ready = False
            start_output = []
            for _ in range(40):  # Up to 4 seconds
                await asyncio.sleep(0.1)
                if self._process.returncode is not None:
                    # Process exited — read all output for error message
                    stderr = ""
                    if self._process.stderr:
                        stderr = (await self._process.stderr.read()).decode(errors="replace")
                    stdout = ""
                    if self._process.stdout:
                        stdout = (await self._process.stdout.read()).decode(errors="replace")
                    raise RuntimeError(
                        f"Simulator exited with code {self._process.returncode}. "
                        f"stderr: {stderr[:500]} stdout: {stdout[:500]}"
                    )
                # Check if stderr has "Uvicorn running" (means it's ready)
                if self._process.stderr:
                    try:
                        chunk = await asyncio.wait_for(
                            self._process.stderr.read(4096), timeout=0.05
                        )
                        if chunk:
                            text = chunk.decode(errors="replace")
                            start_output.append(text)
                            if "Uvicorn running" in text or "Application startup complete" in text:
                                ready = True
                                break
                    except asyncio.TimeoutError:
                        pass

            if not ready and self._process.returncode is None:
                # Process is running but didn't report ready — assume it's ok
                log.warning("Simulator started but readiness not confirmed; proceeding")
                ready = True

        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"Error waiting for simulator startup: {e}")

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

    async def stop(self) -> None:
        """Stop simulation and restore original device connections."""
        if not self._active:
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
                    log.info("Simulator process exited externally (code %s)", exit_code)
                    await self._restore_connections()
                    self._process = None
                    self._sim_ports.clear()
                    self._original_configs.clear()
                    self._sim_ui_url = None
                    self._active = False
                    self.engine.state.set("system.simulation_active", False, source="simulation")
                    self.engine.state.set("system.simulation_ui_url", None, source="simulation")
                    await self.engine.events.emit("simulation.crashed", {
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
        if hasattr(self, "_config_path") and self._config_path:
            Path(self._config_path).unlink(missing_ok=True)
            self._config_path = None

    async def _redirect_connections(self) -> None:
        """Swap device host/port to point at the simulator."""
        dm = self.engine.devices

        for device_id, sim_port in self._sim_ports.items():
            driver = dm._devices.get(device_id)
            if not driver:
                continue

            # Save original config
            self._original_configs[device_id] = {
                "host": driver.config.get("host", ""),
                "port": driver.config.get("port", 0),
            }

            # Redirect to simulator
            driver.config["host"] = "127.0.0.1"
            driver.config["port"] = sim_port

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

            driver.config["host"] = orig["host"]
            driver.config["port"] = orig["port"]

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
                self._original_configs[device_id] = {
                    "host": driver.config.get("host", ""),
                    "port": driver.config.get("port", 0),
                }
                driver.config["host"] = "127.0.0.1"
                driver.config["port"] = sim_port
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
            # Restore original connection if we have it
            orig = self._original_configs.pop(device_id, None)
            if orig:
                driver = dm._devices.get(device_id)
                if driver:
                    driver.config["host"] = orig["host"]
                    driver.config["port"] = orig["port"]
            # Tell simulator to stop this device
            try:
                async with aiohttp.ClientSession() as session:
                    await session.post(f"{sim_api}/api/devices/{device_id}/stop")
            except Exception as e:
                log.warning("Failed to stop simulator for removed device %s: %s", device_id, e)
            self._sim_ports.pop(device_id, None)

        # Start simulators for new devices
        for device_id in added:
            cfg = dm._device_configs.get(device_id)
            if not cfg:
                continue
            driver_id = cfg.get("driver", "")
            log.info("Simulation sync: adding %s (driver=%s)", device_id, driver_id)
            try:
                async with aiohttp.ClientSession() as session:
                    resp = await session.post(
                        f"{sim_api}/api/devices/{device_id}/start",
                        json={"driver_id": driver_id, "port": 0},
                    )
                    if resp.status == 200:
                        data = await resp.json()
                        sim_port = data.get("port", 0)
                        if sim_port:
                            self._sim_ports[device_id] = sim_port
                            # Redirect connection
                            driver = dm._devices.get(device_id)
                            if driver:
                                self._original_configs[device_id] = {
                                    "host": driver.config.get("host", ""),
                                    "port": driver.config.get("port", 0),
                                }
                                driver.config["host"] = "127.0.0.1"
                                driver.config["port"] = sim_port
                                try:
                                    await dm.reconnect_device(device_id)
                                except Exception as e:
                                    log.warning("Failed to reconnect %s to simulator: %s", device_id, e)
                            log.info("Simulation sync: %s on port %d", device_id, sim_port)
                    else:
                        body = await resp.text()
                        log.warning("Simulator refused device %s: %s", device_id, body[:200])
            except Exception as e:
                log.warning("Failed to start simulator for new device %s: %s", device_id, e)

        if added or removed:
            log.info("Simulation sync complete: +%d -%d devices", len(added), len(removed))

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
