"""What three readers make of a device's state, pinned for every state it can
be in.

The alert monitor asks whether the device's absence is on purpose (asleep or
restarting because we asked: nothing is wrong, so no alert fires), the cloud
heartbeat counts the devices in trouble, and the command gate decides whether
a press goes out. Each is read at the moment ``connected`` is written, which
is when the monitor judges a drop, and again once the publishing has settled,
beside the presence word the state publishes (``device.<id>.presence``,
core/device_presence.py), which is what each of them reads.

The devices are invented (``acme``), driven through a real DeviceManager: the
staged states by a Python driver the manager adopts, the held connections over
a real socket on loopback.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from openavc.cloud.alert_monitor import AlertMonitor
from openavc.cloud.heartbeat import HeartbeatCollector
from openavc.core import device_sleep
from openavc.core.connection_fault import UNREACHABLE, ConnectionFaultError
from openavc.core.device_errors import DriverBuildError
from openavc.core.device_manager import DeviceManager
from openavc.core.device_sleep import SleepBook
from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.base import BaseDriver
from openavc.drivers.configurable import create_configurable_driver_class
from openavc.drivers.registry import register_driver, unregister_driver

WIDGET = "acme_widget_presence"
COMMANDS: dict[str, Any] = {
    "reboot": {"label": "Reboot", "restarts_device_for": 60},
    "power_off": {"label": "Power Off", "sleeps_device": "power_on"},
    "power_on": {"label": "Power On", "available_offline": True, "restarts_device_for": 40},
    "set_level": {"label": "Set Level"},
}


class _Widget(BaseDriver):
    DRIVER_INFO: dict[str, Any] = {
        "id": WIDGET,
        "name": "Acme Widget",
        "manufacturer": "Acme",
        "category": "utility",
        "transport": "tcp",
        "state_variables": {},
        "commands": COMMANDS,
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.sent: list[str] = []

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        self.sent.append(command)
        return "ok"


class _Broken(_Widget):
    DRIVER_INFO = {**_Widget.DRIVER_INFO, "id": "acme_widget_broken"}

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise RuntimeError("the widget's driver could not start")


class _Agent:
    async def send_message(self, msg_type: str, payload: dict) -> None:
        pass


def _monitor(dm: DeviceManager) -> AlertMonitor:
    return AlertMonitor(_Agent(), dm.state, EventBus())


class _Readers:
    """The three readers of one device, and what they answered the last time
    ``connected`` was written false."""

    def __init__(self, dm: DeviceManager, device_id: str = "widget") -> None:
        self.dm, self.device_id = dm, device_id
        self.monitor = _monitor(dm)
        self.heartbeat = HeartbeatCollector(dm.state, dm)
        self.at_drop: dict[str, Any] | None = None
        self.word_at_drop: str | None = None
        dm.state.subscribe(f"device.{device_id}.connected", self._on_connected)

    def _on_connected(self, key: str, old: Any, new: Any, source: str) -> None:
        if old is True and new is False:
            self.at_drop = self.now()
            self.word_at_drop = self.word()

    def word(self) -> str | None:
        """The published word, held to the one the manager works out now."""
        published = self.dm.state.get(f"device.{self.device_id}.presence")
        assert published == self.dm.presence_of(self.device_id)
        return published

    def now(self, command: str = "set_level") -> dict[str, Any]:
        return {
            "quiet": self.monitor._quiet_device(self.device_id),
            "trouble": self.heartbeat._get_devices_error(),
            "goes_out": self.dm.command_goes_out(self.device_id, command),
        }


@pytest.fixture
def book_path(tmp_path, monkeypatch):
    path = tmp_path / device_sleep.RECORD_NAME
    monkeypatch.setattr(device_sleep, "_default_path", lambda: path)
    return path


@pytest.fixture
def widget_class():
    register_driver(_Widget)
    register_driver(_Broken)
    try:
        yield _Widget
    finally:
        unregister_driver(WIDGET)
        unregister_driver("acme_widget_broken")


async def _manager(book_path, **entry: Any) -> tuple[DeviceManager, _Readers]:
    dm = DeviceManager(state=StateStore(), events=EventBus())
    dm._sleep = SleepBook(device_sleep._default_path)
    config = {"id": "widget", "driver": WIDGET, "name": "Widget",
              "config": {"host": "10.0.0.9", "port": 4001}, **entry}
    try:
        await dm.add_device(config, defer_connect=True)
    except DriverBuildError:
        pass
    return dm, _Readers(dm)


def _connect(dm: DeviceManager) -> BaseDriver:
    driver = dm._devices["widget"]
    driver._connected = True
    driver.set_state("connected", True)
    dm._clear_offline_reason("widget")
    return driver


def _drop(dm: DeviceManager, driver: BaseDriver, code: str = UNREACHABLE) -> None:
    driver._connected = False
    driver.set_state("connected", False)
    dm._set_offline_reason("widget", driver, exc=ConnectionFaultError("", code=code))


NOT_QUIET_FINE_REFUSED = {"quiet": False, "trouble": 0, "goes_out": False}


# --- staged states ------------------------------------------------------------


async def test_online(book_path, widget_class):
    dm, readers = await _manager(book_path)
    _connect(dm)
    assert readers.now() == {"quiet": False, "trouble": 0, "goes_out": True}
    assert readers.word() == "online"


async def test_registered_and_not_yet_dialed(book_path, widget_class):
    dm, readers = await _manager(book_path)
    assert readers.now() == NOT_QUIET_FINE_REFUSED
    assert readers.now("power_on")["goes_out"] is True
    assert readers.word() == "connecting"


async def test_an_ordinary_drop(book_path, widget_class):
    dm, readers = await _manager(book_path)
    _drop(dm, _connect(dm))
    assert readers.at_drop == NOT_QUIET_FINE_REFUSED
    assert readers.word_at_drop == "connecting"
    assert readers.now() == {"quiet": False, "trouble": 1, "goes_out": False}
    assert readers.word() == "offline"


async def test_a_drop_inside_a_commanded_restart(book_path, widget_class):
    dm, readers = await _manager(book_path)
    driver = _connect(dm)
    await dm.send_command("widget", "reboot", {})
    _drop(dm, driver)
    assert readers.at_drop == {"quiet": True, "trouble": 0, "goes_out": False}
    assert readers.word_at_drop == "restarting"
    assert readers.now() == {"quiet": True, "trouble": 0, "goes_out": False}
    assert readers.now("power_on")["goes_out"] is True
    assert readers.word() == "restarting"


async def test_a_drop_after_a_sleeping_command(book_path, widget_class):
    dm, readers = await _manager(book_path)
    driver = _connect(dm)
    await dm.send_command("widget", "power_off", {})
    _drop(dm, driver)
    assert readers.at_drop == {"quiet": True, "trouble": 0, "goes_out": False}
    assert readers.word_at_drop == "asleep"
    assert readers.now() == {"quiet": True, "trouble": 0, "goes_out": False}
    assert readers.now("power_on")["goes_out"] is True
    assert readers.word() == "asleep"


async def test_a_restart_sent_and_not_yet_gone(book_path, widget_class):
    # Arming a window writes nothing: the device is online, and judged as
    # online until it drops, which the word then says is on purpose.
    dm, readers = await _manager(book_path)
    _connect(dm)
    await dm.send_command("widget", "reboot", {})
    assert readers.now() == {"quiet": False, "trouble": 0, "goes_out": True}
    assert readers.word() == "online"


async def test_paused_from_online(book_path, widget_class):
    dm, readers = await _manager(book_path)
    _connect(dm)
    await dm.pause_device("widget")
    assert readers.at_drop == NOT_QUIET_FINE_REFUSED
    assert readers.now() == NOT_QUIET_FINE_REFUSED
    assert readers.now("power_on")["goes_out"] is True
    assert readers.word() == "paused"


async def test_paused_on_top_of_a_fault(book_path, widget_class):
    # Paused, whatever kept it away before: counted like a device paused while
    # it was online.
    dm, readers = await _manager(book_path)
    _drop(dm, _connect(dm))
    await dm.pause_device("widget")
    assert readers.now() == {"quiet": False, "trouble": 0, "goes_out": False}
    assert readers.word() == "paused"


async def test_paused_while_asleep(book_path, widget_class):
    dm, readers = await _manager(book_path)
    driver = _connect(dm)
    await dm.send_command("widget", "power_off", {})
    _drop(dm, driver)
    await dm.pause_device("widget")
    assert readers.now() == {"quiet": True, "trouble": 0, "goes_out": False}
    assert readers.word() == "asleep"


async def test_disabled(book_path, widget_class):
    dm, readers = await _manager(book_path, enabled=False)
    assert readers.now() == NOT_QUIET_FINE_REFUSED
    assert readers.word() == "disabled"


async def test_waiting_for_its_driver(book_path, widget_class):
    dm, readers = await _manager(book_path, driver="acme_widget_not_installed")
    assert readers.now() == NOT_QUIET_FINE_REFUSED
    assert readers.word() == "waiting_for_driver"


@pytest.mark.parametrize(("enabled", "word", "trouble"), [(True, "offline", 1), (False, "disabled", 0)])
async def test_settings_that_could_not_be_worked_out(book_path, widget_class, enabled, word, trouble):
    # Turned off in the project, it is disabled, whatever kept it from running.
    from openavc.core.device_sync import UnresolvedDevice

    dm = DeviceManager(state=StateStore(), events=EventBus())
    readers = _Readers(dm)
    entry = {"id": "widget", "name": "Widget", "driver": WIDGET, "enabled": enabled}
    await dm.keep_unresolved(UnresolvedDevice(entry, "its settings", ValueError("a map, not a list")))
    assert readers.now() == {"quiet": False, "trouble": trouble, "goes_out": False}
    assert readers.word() == word


async def test_a_driver_that_could_not_set_it_up(book_path, widget_class):
    dm, readers = await _manager(book_path, driver="acme_widget_broken")
    assert readers.now() == {"quiet": False, "trouble": 1, "goes_out": False}
    assert readers.word() == "offline"


# --- held connections, over a real socket -------------------------------------

_POLL = 0.1


@pytest.fixture
async def switchable_server():
    """A TCP server that answers ``PWR?`` with ``PWR=ON`` while ``mode`` is
    "answer" and says nothing while it is "silent"."""
    server_state: dict[str, Any] = {"mode": "silent", "lines": [], "connections": 0}

    async def handle(reader, writer):
        server_state["connections"] += 1
        buf = b""
        try:
            while chunk := await reader.read(4096):
                buf += chunk
                while b"\r" in buf:
                    line, buf = buf.split(b"\r", 1)
                    server_state["lines"].append(line)
                    if line == b"PWR?" and server_state["mode"] == "answer":
                        writer.write(b"PWR=ON\r")
                        await writer.drain()
        except (ConnectionError, OSError):
            pass
        finally:
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    yield server.sockets[0].getsockname()[1], server_state
    server.close()
    await server.wait_closed()


def _polled_definition() -> dict[str, Any]:
    return {
        "id": "acme_widget_polled_presence",
        "name": "Acme Widget",
        "manufacturer": "Acme",
        "category": "utility",
        "version": "1.0.0",
        "transport": "tcp",
        "delimiter": "\r",
        "default_config": {},
        "state_variables": {"power": {"type": "string", "label": "Power"}},
        "commands": {
            "power_off": {"label": "Power Off", "send": "PWR 0\\r", "sleeps_device": "power_on"},
            "power_on": {
                "label": "Power On", "send": "PWR 1\\r",
                "available_offline": True, "restarts_device_for": 40,
            },
            "set_level": {"label": "Set Level", "send": "LVL 1\\r"},
        },
        "responses": [{"match": r"PWR=(\w+)", "set": {"power": "$1"}}],
        "polling": {"queries": ["PWR?\r"]},
    }


@pytest.fixture
async def held_widget(monkeypatch, book_path, switchable_server):
    monkeypatch.setattr(DeviceManager, "_reconnect_delay", lambda self, device_id, attempt: 0.05)
    port, server_state = switchable_server
    definition = _polled_definition()
    register_driver(create_configurable_driver_class(definition))
    dm = DeviceManager(StateStore(), EventBus())
    dm._sleep = SleepBook(device_sleep._default_path)
    readers = _Readers(dm)
    try:
        yield dm, readers, server_state, port, definition
    finally:
        await dm.disconnect_all()
        unregister_driver(definition["id"])


async def _until(predicate: Any, timeout: float) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return predicate()


async def _add(dm: DeviceManager, port: int, definition: dict[str, Any]) -> None:
    await dm.add_device({
        "id": "widget", "driver": definition["id"], "name": "Widget",
        "config": {"host": "127.0.0.1", "port": port, "poll_interval": _POLL, "max_missed_polls": 3},
    })


def _held(dm: DeviceManager) -> bool:
    driver = dm._devices.get("widget")
    return driver is not None and driver._held is True


async def test_held_after_it_stopped_answering(held_widget):
    dm, readers, server_state, port, definition = held_widget
    await _add(dm, port, definition)
    assert await _until(lambda: _held(dm) and dm.state.get("device.widget.offline_reason"), 5.0)
    assert readers.now() == {"quiet": False, "trouble": 1, "goes_out": True}
    assert readers.word() == "offline"


async def test_held_while_asleep(held_widget):
    dm, readers, server_state, port, definition = held_widget
    server_state["mode"] = "answer"
    await _add(dm, port, definition)
    assert await _until(lambda: dm.state.get("device.widget.connected") is True, 3.0)
    await dm.send_command("widget", "power_off", {})
    server_state["mode"] = "silent"
    assert await _until(lambda: _held(dm) and dm.state.get("device.widget.asleep"), 5.0)
    assert readers.now() == {"quiet": True, "trouble": 0, "goes_out": True}
    assert readers.now("power_on")["goes_out"] is True
    assert readers.word() == "asleep"


async def test_the_word_stays_put_while_a_silent_device_is_held_and_dropped(held_widget):
    """A device whose port opens and that never answers is reconnected, held
    and dropped on a cycle; its word says offline once, not once a cycle."""
    dm, readers, server_state, port, definition = held_widget
    words: list[str] = []
    dm.state.subscribe("device.widget.presence", lambda key, old, new, source: words.append(new))
    await _add(dm, port, definition)
    assert await _until(lambda: server_state["connections"] >= 5, 6.0)
    assert words == ["connecting", "online", "connecting", "offline"]
