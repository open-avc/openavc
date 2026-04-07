"""
Simulation manager — launches the openavc-simulator subprocess and
redirects device connections to simulated endpoints.

The simulator is a separate application (openavc-simulator/) that runs
fake protocol servers. This module handles:
  - Spawning the simulator process with the right driver/device config
  - Swapping device connection addresses to localhost:sim_port
  - Restoring original connections when simulation stops
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from server.utils.logger import get_logger

log = get_logger(__name__)

# Path to the simulator project (sibling of openavc/)
_SIMULATOR_DIR = Path(__file__).parent.parent.parent.parent / "openavc-simulator"
_DRIVERS_DIR = Path(__file__).parent.parent.parent.parent / "openavc-drivers"


class SimulationManager:
    """Manages the simulator subprocess and device connection redirection."""

    def __init__(self, engine: Any):
        self.engine = engine
        self._process: asyncio.subprocess.Process | None = None
        self._original_configs: dict[str, dict] = {}  # device_id → {host, port}
        self._sim_ports: dict[str, int] = {}  # device_id → sim port
        self._active = False
        self._sim_ui_url: str | None = None

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
        if self._active:
            raise RuntimeError("Simulation is already active")

        dm = self.engine.devices
        project = self.engine.project
        if not project:
            raise RuntimeError("No project loaded")

        # Determine which devices to simulate
        if device_ids is None:
            device_ids = list(dm._device_configs.keys())

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
                "port": 0,  # auto-allocate
                "config": {k: v for k, v in device_cfg.items()
                           if k not in ("host", "port")},
            })

        if not devices_config:
            raise RuntimeError("No devices to simulate")

        # Build driver paths
        driver_paths = [str(_DRIVERS_DIR)]
        driver_repo = Path(__file__).parent.parent.parent / "driver_repo"
        if driver_repo.exists():
            driver_paths.append(str(driver_repo))
        # Also include built-in driver definitions
        builtin_defs = Path(__file__).parent.parent / "drivers" / "definitions"
        if builtin_defs.exists():
            driver_paths.append(str(builtin_defs))

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

        log.info("Starting simulator with %d devices...", len(devices_config))

        # Spawn the simulator process
        try:
            self._process = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "simulator", "--config", config_file.name,
                cwd=str(_SIMULATOR_DIR),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            raise RuntimeError(
                f"Simulator not found at {_SIMULATOR_DIR}. "
                "Make sure openavc-simulator is installed."
            )

        # Wait for the simulator to start (watch stdout for port assignments)
        # The simulator logs port assignments to stderr via uvicorn
        # Give it a few seconds to start up
        await asyncio.sleep(2.0)

        if self._process.returncode is not None:
            stderr = ""
            if self._process.stderr:
                stderr = (await self._process.stderr.read()).decode()
            raise RuntimeError(f"Simulator process exited immediately: {stderr[:500]}")

        # Assign ports based on auto-allocation (19000 + index)
        for i, dev_cfg in enumerate(devices_config):
            device_id = dev_cfg["device_id"]
            sim_port = 19000 + i
            self._sim_ports[device_id] = sim_port

        self._sim_ui_url = f"http://localhost:{sim_config['ui_port']}"
        self._active = True

        # Redirect device connections
        await self._redirect_connections()

        log.info(
            "Simulation started: %d devices, UI at %s",
            len(self._sim_ports), self._sim_ui_url,
        )

        # Update system state
        self.engine.state.set("system.simulation_active", True, source="simulation")

        return {
            "devices": dict(self._sim_ports),
            "ui_url": self._sim_ui_url,
        }

    async def stop(self) -> None:
        """Stop simulation and restore original device connections."""
        if not self._active:
            return

        log.info("Stopping simulation...")

        # Restore original connections
        await self._restore_connections()

        # Kill the simulator process
        if self._process and self._process.returncode is None:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self._process.kill()
                await self._process.wait()
            log.info("Simulator process stopped")

        self._process = None
        self._sim_ports.clear()
        self._original_configs.clear()
        self._sim_ui_url = None
        self._active = False

        # Update system state
        self.engine.state.set("system.simulation_active", False, source="simulation")

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
                "Redirected %s: %s:%s → 127.0.0.1:%d",
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

    def status(self) -> dict:
        """Get simulation status for the API."""
        return {
            "active": self._active,
            "ui_url": self._sim_ui_url,
            "devices": dict(self._sim_ports),
            "process_alive": (
                self._process is not None
                and self._process.returncode is None
            ),
        }
