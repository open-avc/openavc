"""The safety net under a change of who can connect that cuts off the
browser making it (openavc/listen_safety_net.py): when its clock starts,
what confirms it, and what going back does.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from openavc import listen, listen_safety_net
from openavc.listen import ListenChoice
from openavc.system_config import SystemConfig
from tests.listen_helpers import ETHERNET

ANY = ListenChoice("any")
ETHERNET_ONLY = ListenChoice("adapter", ETHERNET.identity())
T0 = 1_000_000.0


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAVC_BIND", raising=False)
    config = SystemConfig()
    config._data_dir = tmp_path
    config._file_path = tmp_path / "system.json"
    config.load()
    config.set("network", "accept_connections_from", "adapter")
    config.set("network", "accept_connections_adapter", ETHERNET.identity())
    config.save()
    return config


def _armed(tmp_path, now=T0):
    return listen_safety_net.arm(tmp_path, ANY, ETHERNET_ONLY, new_name="Ethernet", now=now)


class TestTheClock:
    def test_it_starts_when_the_server_listens_on_the_new_choice(self, tmp_path):
        _armed(tmp_path)
        assert listen_safety_net.due(tmp_path, now=T0 + 10_000) is None  # not running yet
        listen_safety_net.note_listening(tmp_path, ETHERNET_ONLY, now=T0 + 30)
        assert listen_safety_net.read(tmp_path)["listening_since"] == T0 + 30
        assert listen_safety_net.due(tmp_path, now=T0 + 30 + 599) is None
        assert listen_safety_net.due(tmp_path, now=T0 + 30 + 600) is not None

    def test_a_second_start_does_not_restart_the_clock(self, tmp_path):
        _armed(tmp_path)
        listen_safety_net.note_listening(tmp_path, ETHERNET_ONLY, now=T0 + 30)
        listen_safety_net.note_listening(tmp_path, ETHERNET_ONLY, now=T0 + 300)
        assert listen_safety_net.read(tmp_path)["listening_since"] == T0 + 30

    def test_a_start_on_some_other_choice_drops_it(self, tmp_path):
        _armed(tmp_path)
        listen_safety_net.note_listening(tmp_path, ANY, now=T0 + 30)
        assert listen_safety_net.read(tmp_path) is None

    def test_a_choice_the_environment_holds_drops_it(self, tmp_path):
        _armed(tmp_path)
        held = ListenChoice("adapter", ETHERNET.identity(), held_by="OPENAVC_BIND")
        listen_safety_net.note_listening(tmp_path, held, now=T0 + 30)
        assert listen_safety_net.read(tmp_path) is None

    def test_settings_is_told_the_deadline(self, tmp_path):
        _armed(tmp_path)
        listen_safety_net.note_listening(tmp_path, ETHERNET_ONLY, now=0)
        assert listen_safety_net.status(tmp_path) == {
            "previous": "Any network",
            "choice": "Ethernet only",
            "running": True,
            "deadline": "1970-01-01T00:10:00+00:00",
        }


class TestConfirming:
    def test_the_programmer_through_the_new_choice_keeps_it(self, tmp_path):
        _armed(tmp_path)
        listen_safety_net.note_listening(tmp_path, ETHERNET_ONLY, now=T0)
        assert listen_safety_net.confirm(tmp_path, ETHERNET_ONLY) == (
            "Kept: Accept connections from Ethernet only."
        )
        assert listen_safety_net.read(tmp_path) is None
        assert listen_safety_net.confirm(tmp_path, ETHERNET_ONLY) is None

    def test_nothing_is_confirmed_before_the_new_choice_runs(self, tmp_path):
        _armed(tmp_path)
        assert listen_safety_net.confirm(tmp_path, ANY) is None
        assert listen_safety_net.read(tmp_path) is not None


class TestGoingBack:
    def test_the_previous_choice_is_written_back_with_a_notice(self, cfg, tmp_path):
        _armed(tmp_path)
        listen_safety_net.note_listening(tmp_path, ETHERNET_ONLY, now=T0)
        restarts: list[str] = []

        async def restart(reason: str) -> None:
            restarts.append(reason)

        went = asyncio.run(listen_safety_net.tick(
            cfg, restart, now=T0 + 601
        ))
        assert went is True and len(restarts) == 1
        network = json.loads((tmp_path / "system.json").read_text())["network"]
        assert network["accept_connections_from"] == "any"
        assert network["accept_connections_adapter"] == {}
        assert network["bind_address"] == "0.0.0.0"
        assert listen_safety_net.read(tmp_path) is None
        assert listen_safety_net.take_notice(tmp_path) == (
            "Went back to Any network because nobody opened the Programmer through "
            "Ethernet within 10 minutes."
        )
        assert listen_safety_net.take_notice(tmp_path) is None

    def test_this_computer_only_names_where_to_confirm(self, cfg, tmp_path):
        listen_safety_net.arm(tmp_path, ANY, ListenChoice("this_computer"), now=T0)
        listen_safety_net.note_listening(tmp_path, ListenChoice("this_computer"), now=T0)
        listen_safety_net.revert(cfg, listen_safety_net.due(tmp_path, now=T0 + 600))
        assert "opened the Programmer on this computer" in listen_safety_net.take_notice(tmp_path)

    def test_before_the_deadline_nothing_happens(self, cfg, tmp_path):
        _armed(tmp_path)
        listen_safety_net.note_listening(tmp_path, ETHERNET_ONLY, now=T0)

        async def restart(reason: str) -> None:
            raise AssertionError("restarted before the deadline")

        assert asyncio.run(listen_safety_net.tick(cfg, restart, now=T0 + 599)) is False
        assert cfg.get("network", "accept_connections_from") == "adapter"

    def test_nothing_armed_nothing_happens(self, cfg):
        async def restart(reason: str) -> None:
            raise AssertionError("restarted with nothing armed")

        assert asyncio.run(listen_safety_net.tick(cfg, restart, now=T0)) is False


def test_the_watcher_runs_the_net_on_every_tick(tmp_path):
    """The deadline is checked on the adapter watcher's ten-second tick,
    whatever the choice."""
    calls: list[int] = []

    async def on_tick() -> None:
        calls.append(1)

    watcher = listen.ListenWatcher(
        lambda reason: None,
        plan=lambda: listen.resolve(ListenChoice("this_computer"), []),
        on_tick=on_tick,
        interval=0,
    )

    async def go():
        for _ in range(3):
            await watcher.tick()

    asyncio.run(go())
    assert calls == [1, 1, 1]
