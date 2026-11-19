"""
SimulatorManager — discovers drivers, creates simulators, manages lifecycle.

Scans driver directories for:
  - .avcdriver files → auto-generates simulators (YAML auto-gen)
  - *_sim.py files → loads Python simulator classes

Manages port allocation, start/stop, and provides the registry
that the REST API and UI query.

**Why a device is not running is decided here, in** :meth:`SimulatorManager.start_device`,
**by the step that failed**: no simulator for its driver (none was found, even
after a rescan), its simulator could not be built (the constructor raised, or
the driver's ``_sim.py`` failed to import), or it was built and could not
start (a port it could not open). Each is a :class:`SimulatorRefusal` with its
cause from ``core/connection_fault.py``'s ``SIMULATOR_*`` vocabulary and the
error, kept until the device starts and listed beside the running devices
(``GET /api/devices``), so the server words the device card from it.
"""

from __future__ import annotations

import logging
import socket
from pathlib import Path

import yaml

from openavc.core.connection_fault import SimulatorCause, simulator_error_text
from openavc.drivers.spec import without_null_sections, without_null_settings
from openavc.simulator.base import BaseSimulator
from openavc.simulator.simulator_file import load_simulator_folder
from openavc.simulator.yaml_auto import YAMLAutoSimulator
from openavc.simulator.network_conditions import NetworkConditionLayer

logger = logging.getLogger(__name__)

# Port range for auto-allocation
PORT_RANGE_START = 19000
PORT_RANGE_END = 19499
# How many ports the per-device range spans. Kept as a width rather than a
# second absolute number so moving the base (two OpenAVC instances on one
# machine need distinct ranges) moves the whole window with it.
PORT_RANGE_WIDTH = PORT_RANGE_END - PORT_RANGE_START
# How many ports a single device will try before giving up. Only spent when a
# port passes the bindability probe and then fails the real bind, so this is a
# race budget, not a scan — the scan is _allocate_port's job.
_BIND_RACE_RETRIES = 3


class SimulatorInfo:
    """Metadata about a discovered simulator (not yet running)."""

    def __init__(
        self,
        driver_id: str,
        name: str,
        category: str,
        transport: str,
        default_port: int,
        source: str,
        simulator_class: type[BaseSimulator] | None = None,
        avcdriver_path: Path | None = None,
    ):
        self.driver_id = driver_id
        self.name = name
        self.category = category
        self.transport = transport
        self.default_port = default_port
        self.source = source  # "yaml_auto", "yaml_enhanced", "python"
        self.simulator_class = simulator_class
        self.avcdriver_path = avcdriver_path


class SimulatorRefusal(Exception):
    """Why a device was not started: ``cause`` (a ``SIMULATOR_*`` code from
    core/connection_fault.py) and ``detail``, the error as one line (empty
    when there is none)."""

    def __init__(self, device_id: str, driver_id: str, cause: str, detail: str = "") -> None:
        super().__init__(detail or cause)
        self.device_id = device_id
        self.driver_id = driver_id
        self.cause = cause
        self.detail = detail

    def as_dict(self) -> dict[str, str]:
        return {
            "device_id": self.device_id,
            "driver_id": self.driver_id,
            "cause": self.cause,
            "detail": self.detail,
        }


def _served_definitions(root: Path) -> list[Path]:
    """The ``.avcdriver`` files under ``root`` that serve their ids, as the
    server's loader decides it (``driver_loader.serving_files``), reading each
    folder that holds a definition as one of its directories, ``root`` first."""
    from openavc.drivers.driver_loader import serving_files

    folders = sorted({path.parent for path in root.rglob("*.avcdriver")})
    served = serving_files(folders)
    return [path for path in served.values() if path.suffix == ".avcdriver"]


class SimulatorManager:
    """Central coordinator for device simulators."""

    def __init__(self, port_range_start: int | None = None):
        self._available: dict[str, SimulatorInfo] = {}
        self._driver_paths: list[str] = []  # what discover() last scanned
        # driver id -> why its _sim.py did not import, from the last scan. A
        # driver whose simulator file is broken is not "without a simulator".
        self._load_errors: dict[str, str] = {}
        # device id -> why its last start failed, until it starts.
        self._refusals: dict[str, SimulatorRefusal] = {}
        self._instances: dict[str, BaseSimulator] = {}  # keyed by device_id
        self._allocated_ports: set[int] = set()
        self._change_listeners: list = []
        self.network = NetworkConditionLayer()
        self._port_start = (
            PORT_RANGE_START if port_range_start is None else int(port_range_start)
        )
        self._port_end = self._port_start + PORT_RANGE_WIDTH

    # ── Discovery ──

    def discover(self, driver_paths: list[str]) -> dict[str, SimulatorInfo]:
        """Scan driver directories for simulation-capable drivers.

        Finds .avcdriver files (auto-gen) and *_sim.py files (Python).
        Returns {driver_id: SimulatorInfo}.
        """
        self._available.clear()
        self._load_errors.clear()
        self._driver_paths = list(driver_paths)

        for path_str in driver_paths:
            path = Path(path_str)
            if not path.exists():
                logger.warning("Driver path does not exist: %s", path)
                continue
            self._scan_directory(path)

        logger.info(
            "Discovered %d simulation-capable drivers: %s",
            len(self._available),
            ", ".join(sorted(self._available.keys())),
        )
        return dict(self._available)

    def rescan(self) -> dict[str, SimulatorInfo]:
        """Read the driver folders again (``discover``), as a driver change on
        the server asks: a renamed driver file, a new or changed ``_sim.py``.
        A running device goes on as it is until it is started again. Nothing
        is forgotten before a first scan."""
        if not self._driver_paths:
            return dict(self._available)
        return self.discover(self._driver_paths)

    def _scan_directory(self, root: Path) -> None:
        """Scan a directory tree for drivers and simulators."""
        # The definitions the server serves (YAML auto-gen): one per id, the
        # copy its loader registers, and none for an id a Python driver serves
        # (its simulator is its own _sim.py).
        for avcdriver_path in _served_definitions(root):
            try:
                self._load_yaml_driver(avcdriver_path)
            except Exception:
                logger.exception("Failed to load YAML driver: %s", avcdriver_path)

        # The Python simulators (*_sim.py): one class per driver id in the
        # folder. A driver id none of them serves (its file failed to import,
        # or two classes name it) keeps why, for the device's card. A later
        # folder's simulator for an id replaces an earlier folder's, as its
        # driver does.
        found = load_simulator_folder(sorted(root.rglob("*_sim.py")))
        self._load_errors.update(found.refused)
        for driver_id, simulator_class in found.classes.items():
            self._register_python_simulator(driver_id, simulator_class)

    def _load_yaml_driver(self, path: Path) -> None:
        """Load a YAML driver and register it for auto-generation."""
        with open(path, encoding="utf-8") as f:
            driver_def = without_null_sections(yaml.safe_load(f))

        if not driver_def or not isinstance(driver_def, dict):
            return

        driver_id = driver_def.get("id", "")
        if not driver_id:
            logger.warning("YAML driver missing 'id': %s", path)
            return

        has_simulator_section = "simulator" in driver_def
        source = "yaml_enhanced" if has_simulator_section else "yaml_auto"

        self._available[driver_id] = SimulatorInfo(
            driver_id=driver_id,
            name=driver_def.get("name", driver_id) + " Simulator",
            category=driver_def.get("category", "generic"),
            transport=driver_def.get("transport", "tcp"),
            default_port=driver_def.get("default_config", {}).get("port", 0),
            source=source,
            avcdriver_path=path,
        )
        logger.debug("Discovered YAML driver: %s (%s)", driver_id, source)

    def _register_python_simulator(
        self, driver_id: str, simulator_class: type[BaseSimulator],
    ) -> None:
        # A key written as a null reads as absent, as the device reads it.
        info = without_null_settings(simulator_class.SIMULATOR_INFO)
        self._available[driver_id] = SimulatorInfo(
            driver_id=driver_id,
            name=info.get("name", driver_id),
            category=info.get("category", "generic"),
            transport=info.get("transport", "tcp"),
            default_port=info.get("default_port", 0),
            source="python",
            simulator_class=simulator_class,
        )
        logger.debug("Discovered Python simulator: %s (%s)", driver_id, simulator_class.__module__)

    # ── Instance Management ──

    async def start_device(
        self,
        driver_id: str,
        device_id: str,
        port: int = 0,
        config: dict | None = None,
        device_name: str = "",
        real_host: str = "",
        real_port: int = 0,
        child_entities: dict | None = None,
    ) -> BaseSimulator:
        """Start a simulator instance for a device.

        Args:
            driver_id: The driver to simulate (must be in available)
            device_id: Unique device identifier
            port: Port to listen on (0 = auto-allocate)
            config: Device-specific config (passwords, IDs, etc.)

        Returns:
            The started simulator instance.

        Raises:
            ValueError: the device is already simulated.
            SimulatorRefusal: it could not be started, and why (kept until it
                starts, :meth:`list_refusals`).
        """
        if device_id in self._instances:
            raise ValueError(f"Device '{device_id}' is already simulated")

        shown = {
            "device_name": device_name or device_id,
            "real_host": real_host,
            "real_port": real_port,
            "child_entities": child_entities or {},
        }
        try:
            info = self._simulator_for(driver_id, device_id)
            simulator = self._build(info, device_id, config, shown)
            port = await self._start_refusing(simulator, device_id, driver_id, port)
        except SimulatorRefusal as refusal:
            self._refusals[device_id] = refusal
            logger.warning(
                "Could not simulate %s (driver=%s): %s",
                device_id, driver_id, refusal.detail or refusal.cause,
            )
            raise
        self._refusals.pop(device_id, None)
        self._instances[device_id] = simulator

        logger.info(
            "Started simulator for %s (driver=%s, port=%d)",
            device_id, driver_id, port,
        )
        return simulator

    async def start_requested(self, devices: list[dict]) -> None:
        """Start the devices a launch config asks for. One that cannot be run
        is kept with why (:meth:`list_refusals`), which ``GET /api/devices``
        lists for the server that launched the simulator; the rest still start."""
        for device in devices:
            try:
                await self.start_device(
                    driver_id=device["driver_id"],
                    device_id=device["device_id"],
                    device_name=device.get("device_name", ""),
                    real_host=device.get("real_host", ""),
                    real_port=device.get("real_port", 0),
                    port=device.get("port", 0),
                    config=device.get("config"),
                    child_entities=device.get("child_entities"),
                )
            except SimulatorRefusal:
                pass  # logged and kept by start_device
            except Exception:
                logger.exception(
                    "Failed to start simulator for %s (driver=%s)",
                    device.get("device_id"), device.get("driver_id"),
                )

    def _simulator_for(self, driver_id: str, device_id: str) -> SimulatorInfo:
        """The simulator ``driver_id`` has, or the refusal saying it has none
        (or that its ``_sim.py`` did not import)."""
        info = self._available.get(driver_id)
        if info is None and self._driver_paths:
            # The driver directories are walked once, at startup. A driver
            # installed after that — which is exactly what installing a
            # missing driver mid-session does, straight into driver_repo —
            # is absent from the scan, so the device it unblocks would be
            # refused a simulator and left dialing its real address. Re-scan
            # before refusing.
            logger.info(
                "Rescanning driver paths for unknown driver '%s'", driver_id
            )
            self.discover(self._driver_paths)
            info = self._available.get(driver_id)
        if info is not None:
            return info
        load_error = self._load_errors.get(driver_id)
        if load_error:
            raise SimulatorRefusal(device_id, driver_id, SimulatorCause.BUILD_FAILED, load_error)
        raise SimulatorRefusal(
            device_id, driver_id, SimulatorCause.MISSING,
            f"No simulator available for driver '{driver_id}'",
        )

    def _build(
        self, info: SimulatorInfo, device_id: str, config: dict | None, shown: dict,
    ) -> BaseSimulator:
        """The device's simulator, built and given what the project says about
        the device (``shown``). Anything that raises on the way is the
        BUILD_FAILED refusal: a simulator class that refuses the project's
        details was not built for this device either."""
        try:
            simulator = self._create_instance(info, device_id, config)
            self._dress(simulator, shown)
        except Exception as e:
            logger.exception("Building the simulator for %s failed", device_id)
            raise SimulatorRefusal(
                device_id, info.driver_id, SimulatorCause.BUILD_FAILED, simulator_error_text(e),
            ) from e
        return simulator

    def _dress(self, simulator: BaseSimulator, shown: dict) -> None:
        # Store display metadata from the project
        simulator._device_name = shown["device_name"]
        simulator._real_host = shown["real_host"]
        simulator._real_port = shown["real_port"]
        # v0.5.0 child entities ({child_type: {padded_id: {label, config}}}).
        # Passed through so Python _sim.py simulators can model per-child
        # state/responses; the BaseSimulator default is an empty dict.
        simulator.set_child_entities(shown["child_entities"])
        # Inject network conditions layer
        simulator._network_layer = self.network
        # Add change listener for broadcasting
        simulator.add_change_listener(self._on_simulator_change)

    async def _start_refusing(
        self, simulator: BaseSimulator, device_id: str, driver_id: str, port: int,
    ) -> int:
        try:
            return await self._start_on_a_port(simulator, device_id, port)
        except Exception as e:
            raise SimulatorRefusal(
                device_id, driver_id, SimulatorCause.START_FAILED, simulator_error_text(e),
            ) from e

    def list_refusals(self) -> list[SimulatorRefusal]:
        """Every device whose last start failed and has not started since."""
        return list(self._refusals.values())

    async def _start_on_a_port(
        self, simulator: BaseSimulator, device_id: str, port: int,
    ) -> int:
        """Start ``simulator`` on ``port`` (0 = allocate one) and return the
        number it started on, moving to the next one if the bind loses a race
        with something else claiming it in between.

        A bind/startup failure must release the reserved port so a transient
        collision doesn't permanently burn a slot from the pool. That release
        is why the retry lives here: without it, the caller's next device asks
        for the lowest free port and is handed the same one back.
        ``_allocate_port`` screens for bindability, so reaching this retry at
        all means the port went bad between the probe and the bind -- rare, and
        precisely the case a single attempt cannot survive.

        Only an auto-allocated port may be moved: a caller that named one
        asked for that port specifically and gets its error.
        """
        auto = port == 0
        attempts = _BIND_RACE_RETRIES if auto else 1
        # A port that just failed to bind stays marked allocated for the rest
        # of the loop, so the next _allocate_port call steps over it instead of
        # handing back the number that only just failed. Everything except the
        # winner is released once we are done either way.
        tried: set[int] = set()
        try:
            for attempt in range(attempts):
                chosen = self._allocate_port() if auto else port
                tried.add(chosen)
                self._allocated_ports.add(chosen)
                try:
                    await simulator.start(chosen)
                    break
                except OSError as e:
                    await self._stop_failed_start(simulator, device_id)
                    if not auto or attempt == attempts - 1:
                        raise
                    logger.warning(
                        "Port %d failed to bind for %s (%s); trying another",
                        chosen, device_id, e,
                    )
        except Exception:
            self._allocated_ports -= tried
            raise
        self._allocated_ports -= tried - {chosen}
        return chosen

    @staticmethod
    async def _stop_failed_start(simulator: BaseSimulator, device_id: str) -> None:
        """A start can fail halfway: a stream server listening, the UDP side
        of its number refused (side_receiver). What it opened is closed before
        the next number or the error, or that server keeps the number it was
        refused on."""
        try:
            await simulator.stop()
        except Exception:
            logger.exception("Stopping %s after its start failed", device_id)

    async def stop_device(self, device_id: str) -> None:
        """Stop a simulator instance."""
        simulator = self._instances.pop(device_id, None)
        if not simulator:
            raise ValueError(f"Device '{device_id}' is not simulated")

        await simulator.stop()
        self._allocated_ports.discard(simulator.port)
        logger.info("Stopped simulator for %s", device_id)

    async def stop_all(self) -> None:
        """Stop all running simulator instances."""
        for device_id in list(self._instances.keys()):
            try:
                await self.stop_device(device_id)
            except Exception:
                logger.exception("Error stopping simulator for %s", device_id)

    def get_instance(self, device_id: str) -> BaseSimulator | None:
        """Get a running simulator instance by device ID."""
        return self._instances.get(device_id)

    def list_instances(self) -> list[BaseSimulator]:
        """List all running simulator instances."""
        return list(self._instances.values())

    def list_available(self) -> dict[str, SimulatorInfo]:
        """List all discovered (available) simulators."""
        return dict(self._available)

    # ── Change Listeners (for WebSocket broadcasting) ──

    def add_change_listener(self, listener) -> None:
        self._change_listeners.append(listener)

    def remove_change_listener(self, listener) -> None:
        self._change_listeners = [fn for fn in self._change_listeners if fn is not listener]

    def _on_simulator_change(self, change_type: str, data: dict) -> None:
        for listener in self._change_listeners:
            try:
                listener(change_type, data)
            except Exception:
                logger.exception("Change listener error in manager")

    # ── Internal ──

    def _create_instance(
        self,
        info: SimulatorInfo,
        device_id: str,
        config: dict | None,
    ) -> BaseSimulator:
        """Create a simulator instance from SimulatorInfo."""
        if info.source == "python":
            if not info.simulator_class:
                raise ValueError(f"No simulator class for {info.driver_id}")
            return info.simulator_class(device_id=device_id, config=config)

        elif info.source in ("yaml_auto", "yaml_enhanced"):
            if not info.avcdriver_path:
                raise ValueError(f"No .avcdriver path for {info.driver_id}")
            return YAMLAutoSimulator.from_avcdriver(
                path=info.avcdriver_path,
                device_id=device_id,
                config=config,
            )

        else:
            raise ValueError(f"Unknown simulator source: {info.source}")

    def _port_is_bindable(self, port: int) -> bool:
        """Whether this process could actually listen on ``port`` right now.

        A trial bind, not a connect, and the difference is the whole reason
        this exists. "Is anyone listening?" and "may I listen?" are different
        questions, and on Windows they routinely disagree: Hyper-V, WSL and
        Docker reserve blocks of ports at boot, and a reserved port has nobody
        listening on it *and* refuses to be bound (WSAEACCES / WinError 10013).
        A connect probe calls that port free, hands it out, and the bind fails
        a moment later inside the simulator.

        Overridable so tests can decide what is bindable without depending on
        which ports happen to be free on the machine running them.
        """
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False

    def _allocate_port(self) -> int:
        """Allocate the lowest usable port from the range.

        Scans the whole range each call so a port released by a failed start or
        a stopped device is reused, instead of a monotonic cursor that only ever
        advances and shrinks the usable pool until restart.

        "Usable" means unreserved by us *and* actually bindable. Checking only
        our own set is what turned one unusable port into a whole simulation
        with no devices in it: a failed start releases its port (deliberately —
        a transient collision must not burn a slot), so the next device asked
        for the lowest free port, got the same dead number, and failed the same
        way. Eleven devices, eleven identical failures, and a simulator that
        came up reporting no error of its own.
        """
        for port in range(self._port_start, self._port_end + 1):
            if port in self._allocated_ports:
                continue
            if not self._port_is_bindable(port):
                logger.debug("Port %d is not bindable; trying the next one", port)
                continue
            return port
        raise RuntimeError(
            f"No available ports in range {self._port_start}-{self._port_end}"
        )
