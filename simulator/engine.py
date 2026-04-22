"""
SimulatorManager — discovers drivers, creates simulators, manages lifecycle.

Scans driver directories for:
  - .avcdriver files → auto-generates simulators (YAML auto-gen)
  - *_sim.py files → loads Python simulator classes

Manages port allocation, start/stop, and provides the registry
that the REST API and UI query.
"""

from __future__ import annotations

import importlib.util
import inspect
import logging
from pathlib import Path

import yaml

from simulator.base import BaseSimulator
from simulator.tcp_simulator import TCPSimulator
from simulator.http_simulator import HTTPSimulator
from simulator.udp_simulator import UDPSimulator
from simulator.osc_simulator import OSCSimulator
from simulator.yaml_auto import YAMLAutoSimulator
from simulator.network_conditions import NetworkConditionLayer

logger = logging.getLogger(__name__)

# Port range for auto-allocation
PORT_RANGE_START = 19000
PORT_RANGE_END = 19499


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


class SimulatorManager:
    """Central coordinator for device simulators."""

    def __init__(self):
        self._available: dict[str, SimulatorInfo] = {}
        self._instances: dict[str, BaseSimulator] = {}  # keyed by device_id
        self._allocated_ports: set[int] = set()
        self._next_port = PORT_RANGE_START
        self._change_listeners: list = []
        self.network = NetworkConditionLayer()

    # ── Discovery ──

    def discover(self, driver_paths: list[str]) -> dict[str, SimulatorInfo]:
        """Scan driver directories for simulation-capable drivers.

        Finds .avcdriver files (auto-gen) and *_sim.py files (Python).
        Returns {driver_id: SimulatorInfo}.
        """
        self._available.clear()

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

    def _scan_directory(self, root: Path) -> None:
        """Scan a directory tree for drivers and simulators."""
        # Find all .avcdriver files (YAML auto-gen)
        for avcdriver_path in root.rglob("*.avcdriver"):
            try:
                self._load_yaml_driver(avcdriver_path)
            except Exception:
                logger.exception("Failed to load YAML driver: %s", avcdriver_path)

        # Find all *_sim.py files (Python simulators)
        for sim_path in root.rglob("*_sim.py"):
            try:
                self._load_python_simulator(sim_path)
            except Exception:
                logger.exception("Failed to load Python simulator: %s", sim_path)

    def _load_yaml_driver(self, path: Path) -> None:
        """Load a YAML driver and register it for auto-generation."""
        with open(path, encoding="utf-8") as f:
            driver_def = yaml.safe_load(f)

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

    def _load_python_simulator(self, path: Path) -> None:
        """Load a Python simulator class from a _sim.py file."""
        module_name = f"sim_{path.stem}_{id(path)}"
        spec = importlib.util.spec_from_file_location(module_name, path)
        if not spec or not spec.loader:
            return

        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception:
            logger.exception("Failed to import simulator: %s", path)
            return

        # Find BaseSimulator subclasses in the module
        for _name, obj in inspect.getmembers(module, inspect.isclass):
            if (
                issubclass(obj, BaseSimulator)
                and obj not in (BaseSimulator, TCPSimulator, HTTPSimulator, UDPSimulator, OSCSimulator)
                and hasattr(obj, "SIMULATOR_INFO")
                and obj.SIMULATOR_INFO.get("driver_id")
            ):
                driver_id = obj.SIMULATOR_INFO["driver_id"]
                self._available[driver_id] = SimulatorInfo(
                    driver_id=driver_id,
                    name=obj.SIMULATOR_INFO.get("name", driver_id),
                    category=obj.SIMULATOR_INFO.get("category", "generic"),
                    transport=obj.SIMULATOR_INFO.get("transport", "tcp"),
                    default_port=obj.SIMULATOR_INFO.get("default_port", 0),
                    source="python",
                    simulator_class=obj,
                )
                logger.debug("Discovered Python simulator: %s from %s", driver_id, path)

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
    ) -> BaseSimulator:
        """Start a simulator instance for a device.

        Args:
            driver_id: The driver to simulate (must be in available)
            device_id: Unique device identifier
            port: Port to listen on (0 = auto-allocate)
            config: Device-specific config (passwords, IDs, etc.)

        Returns:
            The started simulator instance.
        """
        if device_id in self._instances:
            raise ValueError(f"Device '{device_id}' is already simulated")

        info = self._available.get(driver_id)
        if not info:
            raise ValueError(
                f"No simulator available for driver '{driver_id}'. "
                f"Available: {', '.join(sorted(self._available.keys()))}"
            )

        # Create the simulator instance
        simulator = self._create_instance(info, device_id, config)

        # Store display metadata from the project
        simulator._device_name = device_name or device_id
        simulator._real_host = real_host
        simulator._real_port = real_port

        # Allocate port
        if port == 0:
            port = self._allocate_port()
        self._allocated_ports.add(port)

        # Inject network conditions layer
        simulator._network_layer = self.network

        # Add change listener for broadcasting
        simulator.add_change_listener(self._on_simulator_change)

        # Start it
        await simulator.start(port)
        self._instances[device_id] = simulator

        logger.info(
            "Started simulator for %s (driver=%s, port=%d)",
            device_id, driver_id, port,
        )
        return simulator

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

    def _allocate_port(self) -> int:
        """Allocate the next available port from the range."""
        while self._next_port <= PORT_RANGE_END:
            port = self._next_port
            self._next_port += 1
            if port not in self._allocated_ports:
                return port
        raise RuntimeError(
            f"No available ports in range {PORT_RANGE_START}-{PORT_RANGE_END}"
        )
