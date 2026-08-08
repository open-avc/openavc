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
import os
import socket
import sys
import tempfile
from pathlib import Path
from typing import Any

from openavc.system_config import APP_DIR, DRIVER_DEFINITIONS_DIR, DRIVER_REPO_DIR
from openavc.utils.logger import get_logger
from openavc.utils.spawn import CREATE_NO_WINDOW

log = get_logger(__name__)

# Workspace paths (dev-only — openavc-drivers sibling repo)
_WORKSPACE_ROOT = APP_DIR.parent
_DRIVERS_DIR = _WORKSPACE_ROOT / "openavc-drivers"

# Defaults for the two ports the simulator subprocess listens on. These are
# the fallbacks only — read them through ``simulator_ui_port()`` and
# ``simulator_device_port_base()`` below, which go through the layered config
# (system.json / env) like every other port. They stay named because the
# failure messages say which port they mean.
SIMULATOR_UI_PORT = 19500
SIMULATOR_DEVICE_PORT_BASE = 19000


def simulator_ui_port() -> int:
    """The port the simulator serves its own UI and API on.

    Resolved per call rather than cached at import: the whole point of making
    it configurable is that a second instance on the same machine can move it,
    and someone who changes it in Settings should get the new value on the
    next Start Simulation rather than after a server restart.
    """
    from openavc.system_config import get_system_config

    value = get_system_config().get("simulation", "ui_port")
    try:
        return int(value)
    except (TypeError, ValueError):
        return SIMULATOR_UI_PORT


def simulator_device_port_base() -> int:
    """First port of the per-device simulator range.

    Configurable for the same reason as the UI port, and it has to move with
    it: two instances that shared this range would collide on the per-device
    listeners even with distinct UI ports.
    """
    from openavc.system_config import get_system_config

    value = get_system_config().get("simulation", "device_port_base")
    try:
        return int(value)
    except (TypeError, ValueError):
        return SIMULATOR_DEVICE_PORT_BASE

# What a failed bind looks like across platforms. Linux/macOS raise EADDRINUSE
# with the wording below; Windows says "only one usage of each socket address"
# for WSAEADDRINUSE, and neither the errno number (48 on macOS, 98 on Linux)
# nor the phrasing is shared, so match on the parts that are.
_ADDRESS_IN_USE_MARKERS = (
    "address already in use",
    "only one usage of each socket address",
    "errno 48",
    "errno 98",
    "10048",
)


def _port_is_taken(port: int, host: str = "127.0.0.1") -> bool:
    """True when something is already listening on ``host:port``.

    A connect rather than a trial bind: on loopback it answers instantly, and
    it sidesteps the SO_REUSEADDR / TIME_WAIT differences between platforms
    that make "could I bind this?" a different question from "is anyone
    serving here?" — the second is the one worth asking.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.25)
        try:
            return probe.connect_ex((host, port)) == 0
        except OSError:
            return False


def _is_address_in_use(stderr: str) -> bool:
    """True when the subprocess said it could not bind its listening socket."""
    lowered = stderr.lower()
    return any(marker in lowered for marker in _ADDRESS_IN_USE_MARKERS)


def _port_in_use_message(ui_port: int | None = None) -> str:
    """What to tell someone whose Simulator UI port is already occupied."""
    port = simulator_ui_port() if ui_port is None else ui_port
    return (
        f"The simulator could not start: port {port}, which it "
        f"serves the Simulator UI on, is already in use. Usually that is "
        f"another OpenAVC instance simulating on this machine, or a simulator "
        f"left running by a previous instance that exited without stopping "
        f"it. Stop the other instance's simulation (or end the leftover "
        f"process listening on port {port}), or give this instance its own "
        f'ports under "simulation" in system.json, and start simulation '
        f"again."
    )


def _startup_failure_message(returncode: int | None, stderr: str) -> str:
    """Explain why the simulator subprocess died before it was ready.

    The raw form of this was a truncated stderr blob ending in
    ``[Errno 48] error while attempting to bind on address
    ('127.0.0.1', 19500): address already in use`` — which never said that
    19500 is the simulator's own UI port, that something else already holds
    it, or what to do about it. That is the single most common way this fails,
    because the simulator outlives a server that exits without stopping it and
    the port is fixed, so a second instance on the same machine hits it every
    time. The pre-flight check in ``_do_start`` catches it before we spawn
    anything; this stays as the backstop for the narrow window where something
    else claims the port between that check and uvicorn's own bind. Everything
    we cannot name keeps the original blob, which is still the best thing to
    show.
    """
    if _is_address_in_use(stderr):
        return _port_in_use_message()
    return (
        f"Simulator exited with code {returncode}. "
        f"stderr: {stderr[:500]} (stdout in simulator.stdout logs)"
    )


class SimulationManager:
    """Manages the simulator subprocess and device connection redirection."""

    def __init__(self, engine: Any):
        self.engine = engine
        self._process: asyncio.subprocess.Process | None = None
        self._original_configs: dict[str, dict] = {}  # device_id → {host, port, transport}
        self._sim_ports: dict[str, int] = {}  # device_id → sim port
        # device_id → whether that simulator terminates TLS. Decides whether an
        # https device keeps its scheme while redirected (see
        # _apply_sim_redirect).
        self._sim_tls: dict[str, bool] = {}
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
            self._sim_tls.clear()
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
            import openavc.simulator  # noqa: F401
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

        # Build driver paths. The simulator scans these in order with
        # last-write-wins on duplicate ids, so list them lowest-precedence
        # first: the user's driver_repo goes last to mirror the runtime
        # loader, where a user copy overrides a same-id built-in.
        driver_paths = []
        if _DRIVERS_DIR.exists():
            driver_paths.append(str(_DRIVERS_DIR))
        if DRIVER_DEFINITIONS_DIR.exists():
            driver_paths.append(str(DRIVER_DEFINITIONS_DIR))
        if DRIVER_REPO_DIR.exists():
            driver_paths.append(str(DRIVER_REPO_DIR))

        if not driver_paths:
            raise RuntimeError("No driver paths found")

        ui_port = simulator_ui_port()
        sim_config = {
            "driver_paths": driver_paths,
            "devices": devices_config,
            "ui_port": ui_port,
            "device_port_base": simulator_device_port_base(),
            # Who to follow. The simulator exits when this process is gone.
            # Needed because the paths that strand a simulator are exactly the
            # ones where no shutdown code of ours runs: SIGKILL, an OOM kill,
            # a hard crash, and our own os._exit(0) restart watchdog. The
            # graceful path already stops it from this side.
            "parent_pid": os.getpid(),
        }

        # Write config to temp file
        config_file = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, prefix="openavc_sim_",
        )
        json.dump(sim_config, config_file)
        config_file.close()
        config_path = config_file.name
        self._config_path = config_path

        # Ask before spawning, rather than reading the wreckage afterwards.
        # Uvicorn reports a failed bind on stderr *after* "Application startup
        # complete", sometimes in the same read and sometimes in a later one,
        # so the readiness loop could see the marker, declare success, and go
        # on to query the UI port — which the other instance's simulator
        # answered. This instance then reported it was simulating with its
        # devices redirected to a simulator it did not own. Checking first is
        # deterministic and needs no guesses about uvicorn's wording.
        if _port_is_taken(ui_port):
            raise RuntimeError(_port_in_use_message(ui_port))

        log.info("Starting simulator with %d devices...", len(devices_config))
        log.info("Driver paths: %s", driver_paths)
        log.info("Config file: %s", config_path)

        # Spawn the simulator process.
        # In frozen (PyInstaller) builds, sys.executable is the .exe itself,
        # so we use --simulator flag which openavc/main.py dispatches to the
        # simulator entry point. In normal Python, use -m openavc.simulator.
        #
        # --no-auto-shutdown: when launched standalone the simulator stops
        # itself 5s after the last UI tab closes (nice CLI UX). When openavc
        # is the launcher, drivers depend on the simulator staying up
        # regardless of whether the Simulator UI tab is open.
        if getattr(sys, 'frozen', False):
            cmd = [sys.executable, "--simulator", "--config", config_path, "--no-auto-shutdown"]
        else:
            cmd = [sys.executable, "-m", "openavc.simulator", "--config", config_path, "--no-auto-shutdown"]

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
                                    self._sim_tls[did] = bool(dev.get("tls"))
                            break
                    except Exception:
                        await asyncio.sleep(0.3)
        except Exception as e:
            log.warning("Could not query simulator for port assignments: %s", e)

        # Fallback: if API query failed, use sequential assignment
        if not self._sim_ports:
            import socket
            log.warning("Falling back to sequential port assignment")
            port = simulator_device_port_base()
            for dev_cfg in devices_config:
                device_id = dev_cfg["device_id"]
                while port < ui_port:
                    try:
                        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                            s.bind(("127.0.0.1", port))
                        break
                    except OSError:
                        port += 1
                self._sim_ports[device_id] = port
                port += 1

        self._active = True
        # Let the device manager explain a device this run could not simulate,
        # instead of it reporting a refused socket at the device's real address.
        self.engine.devices.unsimulated_driver = self._unsimulated_driver

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
        during startup, or if it reported a failed bind; warns and returns if
        it stays up but never prints the ready marker (probably fine — older
        uvicorn versions phrased it differently).

        A failed bind is checked *before* the ready marker, and that ordering
        is the whole point. Uvicorn emits ``Application startup complete`` and
        then the bind error, and both routinely land in the same 4 KB read —
        so the marker won, this returned success, and the caller went on to
        query ``localhost:19500``, which the *other* instance's simulator
        answered. That instance then believed it was simulating while its
        devices pointed at a simulator it did not own, and the only trace was
        a ``Simulator process exited (code 1)`` line logged after the fact.
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
                    _startup_failure_message(process.returncode, stderr)
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
                if _is_address_in_use(text):
                    raise RuntimeError(_startup_failure_message(None, text))
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

    def _unsimulated_driver(self, device_id: str) -> str | None:
        """The driver id of a device this run has no simulator for, else None.

        Derived rather than recorded: a device is unsimulated exactly when a
        run is active and it never got a port. That covers the start path and
        every later sync from one place, so a refusal cannot be missed by a
        call site that forgot to note it.
        """
        if not self._active or device_id in self._sim_ports:
            return None
        config = self.engine.devices.get_device_config(device_id)
        if config is None:
            return None
        return config.get("driver") or ""

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

        # Devices point back at their real addresses from here on, so a refused
        # socket means what it usually means again.
        self.engine.devices.unsimulated_driver = None

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
        self._sim_tls.clear()
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
                    self._sim_tls.clear()
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

    # Transports with no simulator server of their own. Each is a raw byte
    # pipe, so the simulator serves it over TCP and the driver is flipped to
    # TCP to reach it — the same substitution the serial-over-IP bridge
    # passthrough makes in Engine.resolved_device_config.
    #
    # ``ssh`` was missing here until 2026-07-31, so an SSH driver kept its
    # declared transport under simulation and tried to open an SSH session
    # against the simulator's plain TCP socket. netgear_m4250_m4350 ships
    # `simulated: true` and could not in fact be simulated without the user
    # first hand-editing `transport: tcp` into its device config.
    _TCP_STAND_IN_TRANSPORTS = frozenset({"serial", "ssh"})

    @staticmethod
    def _driver_transport_needs_tcp_stand_in(driver: Any) -> bool:
        """True when the device's effective transport has no simulator server.

        Mirrors BaseDriver.connect's resolution order: an explicit
        device-config transport wins over the driver's DRIVER_INFO default.
        """
        config = getattr(driver, "config", None) or {}
        driver_info = getattr(driver, "DRIVER_INFO", None) or {}
        transport = config.get("transport") or driver_info.get("transport", "tcp")
        return transport in SimulationManager._TCP_STAND_IN_TRANSPORTS

    def _apply_sim_redirect(
        self, driver: Any, device_id: str, sim_port: int
    ) -> None:
        """Point one live driver at the simulator on 127.0.0.1:sim_port and
        record its original connection so _restore_original_config can undo it.

        A driver whose transport has no simulator server (serial, ssh) is
        flipped to TCP for the duration: the simulator serves TCP, so the
        driver must speak TCP to reach it. Every other transport
        (tcp/udp/osc/http/mqtt) is served by the sim directly and keeps its
        declared settings.

        An HTTPS device (``ssl: true``) keeps its scheme when its simulator
        terminates TLS, which is what an HTTPS-only device's simulator does —
        the driver then reaches the simulator exactly the way it reaches the
        hardware. Certificate verification is what gets turned off instead:
        the simulator's cert is a throwaway self-signed one. A simulator that
        serves plain HTTP still gets the old flip, so a device whose driver
        speaks https to a simulator that doesn't isn't left dialing TLS into a
        plaintext socket.
        """
        self._original_configs[device_id] = {
            "host": driver.config.get("host", ""),
            "port": driver.config.get("port", 0),
            # Preserve absence as None so restore can delete the override and
            # let the DRIVER_INFO transport apply again (a serial driver has no
            # explicit transport in config until we add one here).
            "transport": driver.config.get("transport"),
            "ssl": driver.config.get("ssl"),
            "verify_ssl": driver.config.get("verify_ssl"),
        }
        driver.config["host"] = "127.0.0.1"
        driver.config["port"] = sim_port
        if self._driver_transport_needs_tcp_stand_in(driver):
            driver.config["transport"] = "tcp"
        if driver.config.get("ssl"):
            if self._sim_tls.get(device_id):
                driver.config["verify_ssl"] = False
            else:
                driver.config["ssl"] = False

    @staticmethod
    def _restore_original_config(driver: Any, orig: dict) -> None:
        """Restore a driver's saved connection (host, port, transport, ssl)."""
        driver.config["host"] = orig.get("host", "")
        driver.config["port"] = orig.get("port", 0)
        # Only touch a key we actually recorded. A None value means there was
        # no explicit setting before the redirect — remove the one we added so
        # the driver's own default applies again (its DRIVER_INFO transport,
        # or verification back on for a device that never turned it off).
        for key in ("transport", "ssl", "verify_ssl"):
            if key not in orig:
                continue
            if orig[key] is None:
                driver.config.pop(key, None)
            else:
                driver.config[key] = orig[key]

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
                            self._redirect_device_to_sim(
                                device_id, sim_port, bool(data.get("tls"))
                            )
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

    def _redirect_device_to_sim(
        self, device_id: str, sim_port: int, sim_tls: bool = False
    ) -> None:
        """Record the original config and point one device at the simulator.

        ``sim_tls`` is what the simulator reported about itself, and decides
        whether an https device keeps its scheme (see _apply_sim_redirect).
        """
        dm = self.engine.devices
        self._sim_ports[device_id] = sim_port
        self._sim_tls[device_id] = sim_tls
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
                    self._redirect_device_to_sim(
                        device_id, dev["port"], bool(dev.get("tls"))
                    )
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
