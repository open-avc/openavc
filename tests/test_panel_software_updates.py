"""Panel software updates: what the server publishes, stages and asks for.

On appliance hardware the device supervisor declares itself in
``supervisor/supervisor.json`` and installs a staged file itself. These tests
stand in for it with files on disk and pin what it relies on: where a file
goes, the order the files are written in, what is refused, and when an
install is asked for.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from openavc.cloud.command_handler import CommandHandler
from openavc.core.project_loader import DisplaySettings
from openavc.core.state_store import StateStore
from openavc.updater import panel_software as ps
from openavc.updater.manager import UpdateManager
from openavc.updater.panel_software import PanelSoftwareUpdates, StageOutcome

NEW = b"panel software build 31"
NEW_SHA = hashlib.sha256(NEW).hexdigest()
SIGNER = "a" * 64
URL = "https://cloud.example.test/panel-software/31"


@pytest.fixture(autouse=True)
def _no_firmware_host(monkeypatch):
    monkeypatch.setattr(ps, "_firmware_host", lambda: None)


def _declare(data_dir: Path, **overrides) -> dict:
    folder = data_dir / "supervisor"
    folder.mkdir(parents=True, exist_ok=True)
    decl = {
        "schema": 1,
        "package": "com.example.panel",
        "version": "0.3.27",
        "version_code": 30,
        "signer_sha256": SIGNER,
        "update_file": str(folder / "update.bin"),
        "request_file": str(folder / "install-request"),
        "request_format": "sha256=<lowercase hex SHA-256 of update_file>",
        "auto_install_idle_seconds": 600,
        "written_at": "2026-11-05T12:00:00Z",
        "last_result": None,
        "last_result_version": None,
        "last_result_message": None,
        "last_result_at": None,
    }
    decl.update(overrides)
    (folder / "supervisor.json").write_text(json.dumps(decl), encoding="utf-8")
    return decl


class _Updates:
    """The two things the stager asks of the update manager."""

    def __init__(self, body: bytes = NEW, fail: Exception | None = None):
        self.body = body
        self.fail = fail
        self.update_in_progress = False
        self.calls: list[tuple[str, Path, int | None]] = []
        self.during = None
        self.gate: asyncio.Event | None = None

    async def download_to(self, url, path, *, progress=None, max_bytes=None):
        path = Path(path)
        self.calls.append((url, path, max_bytes))
        if self.during:
            self.during(path)
        if self.gate is not None:
            await self.gate.wait()
        path.write_bytes(self.body)
        if self.fail is not None:
            raise self.fail
        return len(self.body)


def _panel(tmp_path, updates=None, display=None):
    state = StateStore()
    updates = updates or _Updates()
    panel = PanelSoftwareUpdates(
        state, updates, data_dir=tmp_path, display_settings=lambda: display, poll_seconds=3600,
    )
    return panel, state, updates


def _ns(state) -> dict:
    return state.get_namespace("system.panel_software")


async def _stage(panel, **overrides):
    kwargs = dict(
        update_url=URL,
        checksum_sha256=NEW_SHA,
        target_version="0.3.28",
        target_version_code=31,
        signer_sha256=SIGNER,
        install="when_idle",
    )
    kwargs.update(overrides)
    return await panel.stage(**kwargs)


def _files(tmp_path):
    folder = tmp_path / "supervisor"
    return folder / "update.bin", folder / "install-request"


# ── what is published ────────────────────────────────────────────────────


async def test_with_no_declaration_nothing_is_said_or_staged(tmp_path):
    panel, state, updates = _panel(tmp_path)
    await panel.start()
    try:
        assert _ns(state) == {}
        out = await _stage(panel)
    finally:
        await panel.stop()
    assert (out.success, out.code) == (False, "no_panel_software")
    assert updates.calls == []
    assert _ns(state) == {}


def test_the_declaration_is_published_as_state(tmp_path):
    _declare(
        tmp_path,
        last_result="installed",
        last_result_version="0.3.27",
        last_result_message="Updated from 0.3.26.",
        last_result_at="2026-11-05T12:47:25Z",
    )
    panel, state, _ = _panel(tmp_path)
    panel.refresh()
    assert _ns(state) == {
        "version": "0.3.27",
        "version_code": 30,
        "last_result": "installed",
        "last_result_version": "0.3.27",
        "last_result_message": "Updated from 0.3.26.",
        "last_result_at": "2026-11-05T12:47:25Z",
        "pending_version": "",
        "pending_version_code": None,
        "pending_install": "",
        "install_idle_seconds": 600,
    }


@pytest.mark.parametrize("text", [
    "{not json",
    "[]",
    json.dumps({"schema": 2, "version_code": 30}),
    json.dumps({"schema": 1, "version_code": "30"}),
    json.dumps({"schema": 1}),
])
async def test_an_unusable_declaration_publishes_nothing_and_stages_nothing(tmp_path, text):
    (tmp_path / "supervisor").mkdir()
    (tmp_path / "supervisor" / "supervisor.json").write_text(text, encoding="utf-8")
    panel, state, updates = _panel(tmp_path)
    panel.refresh()
    assert _ns(state) == {}
    out = await _stage(panel)
    assert (out.success, out.code) == (False, "declaration_invalid")
    assert updates.calls == []


def test_the_keys_go_when_the_declaration_does(tmp_path):
    _declare(tmp_path)
    panel, state, _ = _panel(tmp_path)
    panel.refresh()
    assert _ns(state)["version"] == "0.3.27"
    (tmp_path / "supervisor" / "supervisor.json").unlink()
    panel.refresh()
    assert _ns(state) == {}


# ── where a file may go ──────────────────────────────────────────────────


@pytest.mark.parametrize("update_file", [
    "{data}/project.avc",
    "{data}/supervisor/supervisor.json",
    "supervisor/update.bin",
    "{data}/supervisor/../device_logins.json",
    "{data}/supervisor/update.bin.partial",
    "",
])
async def test_a_declared_file_anywhere_but_its_folder_is_refused(tmp_path, update_file):
    _declare(tmp_path, update_file=update_file.format(data=tmp_path))
    (tmp_path / "project.avc").write_text("project", encoding="utf-8")
    panel, _, updates = _panel(tmp_path)
    out = await _stage(panel)
    assert (out.success, out.code) == (False, "declaration_invalid")
    assert updates.calls == []
    assert (tmp_path / "project.avc").read_text(encoding="utf-8") == "project"
    assert not (tmp_path / "device_logins.json").exists()


async def test_the_request_and_the_update_must_be_two_files(tmp_path):
    update, _ = _files(tmp_path)
    _declare(tmp_path, request_file=str(update))
    panel, _, updates = _panel(tmp_path)
    out = await _stage(panel)
    assert (out.success, out.code) == (False, "declaration_invalid")
    assert updates.calls == []


async def test_a_linked_update_file_is_refused(tmp_path):
    _declare(tmp_path)
    update, _ = _files(tmp_path)
    elsewhere = tmp_path / "elsewhere.bin"
    elsewhere.write_bytes(b"not the supervisor's")
    try:
        os.symlink(elsewhere, update)
    except OSError:
        pytest.skip("this platform cannot make a symlink here")
    panel, _, updates = _panel(tmp_path)
    out = await _stage(panel)
    assert (out.success, out.code) == (False, "declaration_invalid")
    assert updates.calls == []
    assert elsewhere.read_bytes() == b"not the supervisor's"


# ── what is refused before anything is downloaded ────────────────────────


@pytest.mark.parametrize("overrides", [
    {"update_url": "http://cloud.example.test/panel-software/31"},
    {"update_url": None},
    {"checksum_sha256": "abc"},
    {"checksum_sha256": None},
    {"target_version_code": 0},
    {"target_version_code": True},
    {"target_version_code": "31"},
    {"install": "now"},
    {"signer_sha256": "not-a-hash"},
])
async def test_a_malformed_request_is_refused(tmp_path, overrides):
    _declare(tmp_path)
    panel, _, updates = _panel(tmp_path)
    out = await _stage(panel, **overrides)
    assert (out.success, out.code) == (False, "invalid_request")
    assert updates.calls == []


@pytest.mark.parametrize("code", [29, 30])
async def test_a_version_at_or_below_the_installed_one_is_refused(tmp_path, code):
    _declare(tmp_path)
    panel, _, updates = _panel(tmp_path)
    out = await _stage(panel, target_version="0.3.27", target_version_code=code)
    assert (out.success, out.code) == (False, "not_newer")
    assert "0.3.27" in out.message
    assert updates.calls == []


async def test_a_different_signer_is_refused_before_downloading(tmp_path):
    _declare(tmp_path)
    panel, _, updates = _panel(tmp_path)
    out = await _stage(panel, signer_sha256="b" * 64)
    assert (out.success, out.code) == (False, "wrong_signer")
    assert updates.calls == []
    # The same signer in capitals is the same signer.
    out = await _stage(panel, signer_sha256=SIGNER.upper())
    assert out.success, out.message


async def test_nothing_is_staged_while_openavc_updates_itself(tmp_path):
    _declare(tmp_path)
    updates = _Updates()
    updates.update_in_progress = True
    panel, _, _ = _panel(tmp_path, updates)
    out = await _stage(panel)
    assert (out.success, out.code) == (False, "server_updating")
    assert updates.calls == []


async def test_a_product_firmware_host_refuses_it(tmp_path, monkeypatch):
    _declare(tmp_path)
    host = SimpleNamespace(guidance="Updates are managed through the product's firmware update settings.")
    monkeypatch.setattr(ps, "_firmware_host", lambda: host)
    panel, state, updates = _panel(tmp_path)
    await panel.start()
    await panel.stop()
    assert _ns(state) == {}
    out = await _stage(panel)
    assert (out.success, out.code, out.message) == (False, "firmware_managed", host.guidance)
    assert updates.calls == []


async def test_too_little_free_space_is_refused_and_leaves_what_was_staged(tmp_path, monkeypatch):
    _declare(tmp_path)
    _, request = _files(tmp_path)
    panel, state, updates = _panel(tmp_path)
    assert (await _stage(panel)).success
    monkeypatch.setattr(ps.shutil, "disk_usage", lambda _p: SimpleNamespace(free=1024 * 1024))
    out = await _stage(panel, target_version="0.3.29", target_version_code=32,
                       checksum_sha256=hashlib.sha256(b"build 32").hexdigest())
    assert (out.success, out.code) == (False, "insufficient_space")
    assert len(updates.calls) == 1
    # The update staged before it is still asked for.
    assert request.read_text(encoding="utf-8") == f"sha256={NEW_SHA}\n"
    assert _ns(state)["pending_version"] == "0.3.28"


async def test_a_second_update_while_one_is_staging_is_refused(tmp_path):
    _declare(tmp_path)
    updates = _Updates()
    updates.gate = asyncio.Event()
    panel, _, _ = _panel(tmp_path, updates)
    first = asyncio.create_task(_stage(panel))
    while not updates.calls:
        await asyncio.sleep(0)
    second = await _stage(panel)
    updates.gate.set()
    assert (second.success, second.code) == (False, "busy")
    assert (await first).success


# ── staging: the order and what is left behind ───────────────────────────


async def test_the_order_the_supervisor_relies_on(tmp_path, monkeypatch):
    _declare(tmp_path)
    update, request = _files(tmp_path)
    update.write_bytes(b"an older staged file")
    request.write_text("sha256=" + "c" * 64 + "\n", encoding="utf-8")
    seen: dict = {}

    def during(path):
        seen["partial"] = path
        seen["request_while_downloading"] = request.exists()
        seen["update_while_downloading"] = update.read_bytes()

    real_write = ps._write_atomic

    def spy_write(path, content):
        if path == request:
            seen["update_when_asked"] = update.read_bytes()
        real_write(path, content)

    monkeypatch.setattr(ps, "_write_atomic", spy_write)
    updates = _Updates()
    updates.during = during
    panel, state, _ = _panel(tmp_path, updates)

    out = await _stage(panel)

    assert out.success, out.message
    assert seen["partial"] == update.with_name("update.bin.partial")
    assert seen["request_while_downloading"] is False      # the old request went first
    assert seen["update_while_downloading"] == b"an older staged file"
    assert seen["update_when_asked"] == NEW                 # the request came last
    assert update.read_bytes() == NEW
    assert not update.with_name("update.bin.partial").exists()
    assert request.read_text(encoding="utf-8") == f"sha256={NEW_SHA}\n"
    assert updates.calls == [(URL, update.with_name("update.bin.partial"), ps.MAX_UPDATE_BYTES)]
    ns = _ns(state)
    assert (ns["pending_version"], ns["pending_version_code"], ns["pending_install"]) == ("0.3.28", 31, "when_idle")
    assert "nobody has touched the panel for 10 minutes" in out.message


async def test_at_the_panel_stages_the_file_and_asks_for_nothing(tmp_path):
    _declare(tmp_path)
    update, request = _files(tmp_path)
    panel, state, _ = _panel(tmp_path)
    out = await _stage(panel, install=None)   # the default
    assert out.success, out.message
    assert "starts it at the panel" in out.message
    assert update.read_bytes() == NEW
    assert not request.exists()
    assert _ns(state)["pending_install"] == "at_panel"


async def test_a_file_that_does_not_match_its_checksum_is_deleted(tmp_path):
    _declare(tmp_path)
    update, request = _files(tmp_path)
    update.write_bytes(b"an older staged file")
    panel, state, _ = _panel(tmp_path, _Updates(body=b"something else"))
    out = await _stage(panel)
    assert (out.success, out.code) == (False, "checksum_mismatch")
    assert update.read_bytes() == b"an older staged file"
    assert not update.with_name("update.bin.partial").exists()
    assert not request.exists()
    assert not (tmp_path / ps.RECORD_NAME).exists()
    assert _ns(state)["pending_install"] == ""


async def test_a_failed_download_leaves_nothing_behind(tmp_path):
    _declare(tmp_path)
    update, request = _files(tmp_path)
    panel, _, _ = _panel(tmp_path, _Updates(fail=OSError("connection reset")))
    out = await _stage(panel)
    assert (out.success, out.code) == (False, "download_failed")
    assert "connection reset" in out.message
    assert not update.exists()
    assert not update.with_name("update.bin.partial").exists()
    assert not request.exists()


async def test_the_same_file_sent_again_is_asked_for_again_without_a_download(tmp_path):
    _declare(tmp_path)
    _, request = _files(tmp_path)
    panel, state, updates = _panel(tmp_path)
    assert (await _stage(panel, install="at_panel")).success
    assert not request.exists()
    out = await _stage(panel, install="when_idle")
    assert out.success, out.message
    assert len(updates.calls) == 1
    assert request.read_text(encoding="utf-8") == f"sha256={NEW_SHA}\n"
    assert _ns(state)["pending_install"] == "when_idle"
    # And back: an update somebody will start at the panel has no request.
    assert (await _stage(panel, install="at_panel")).success
    assert not request.exists()
    assert len(updates.calls) == 1


async def test_a_download_left_by_a_stop_is_removed_at_start(tmp_path):
    _declare(tmp_path)
    update, _ = _files(tmp_path)
    partial = update.with_name("update.bin.partial")
    partial.write_bytes(b"half a file")
    panel, _, _ = _panel(tmp_path)
    await panel.start()
    await panel.stop()
    assert not partial.exists()


# ── the space in use ─────────────────────────────────────────────────────


def test_the_hold_reads_the_key_the_way_the_dim_does():
    state = StateStore()
    d = DisplaySettings(idle_dim_hold_state_key="var.system_on")
    assert d.hold_engaged(state) is False          # nothing has written it
    state.set("var.system_on", True)
    assert d.hold_engaged(state) is True
    state.set("var.system_on", 0)
    assert d.hold_engaged(state) is False
    assert DisplaySettings().hold_engaged(state) is False
    assert d.hold_engaged(None) is False


async def test_a_request_waits_while_the_space_is_in_use(tmp_path):
    _declare(tmp_path)
    _, request = _files(tmp_path)
    display = DisplaySettings(idle_dim_hold_state_key="var.system_on")
    panel, state, _ = _panel(tmp_path, display=display)
    state.set("var.system_on", True)
    await panel.start()
    try:
        out = await _stage(panel)
        assert out.success, out.message
        assert "not in use" in out.message
        assert not request.exists()
        assert _ns(state)["pending_install"] == "held"

        state.set("var.system_on", False)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert request.read_text(encoding="utf-8") == f"sha256={NEW_SHA}\n"
        assert _ns(state)["pending_install"] == "when_idle"

        # The space comes into use before the panel installs it: the request
        # is taken away again.
        state.set("var.system_on", True)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not request.exists()
        assert _ns(state)["pending_install"] == "held"
    finally:
        await panel.stop()


async def test_a_request_found_while_the_space_is_in_use_is_taken_away(tmp_path):
    """A request this server wrote before it stopped, found in a meeting."""
    _declare(tmp_path)
    _, request = _files(tmp_path)
    panel, state, _ = _panel(tmp_path)
    assert (await _stage(panel)).success
    assert request.exists()
    display = DisplaySettings(idle_dim_hold_state_key="var.system_on")
    state.set("var.system_on", True)
    again = PanelSoftwareUpdates(
        state, _Updates(), data_dir=tmp_path, display_settings=lambda: display, poll_seconds=3600,
    )
    again.refresh()
    assert not request.exists()
    assert _ns(state)["pending_install"] == "held"


# ── after the panel acts ─────────────────────────────────────────────────


async def test_once_the_panel_installs_it_nothing_is_waiting(tmp_path):
    _declare(tmp_path)
    update, request = _files(tmp_path)
    panel, state, _ = _panel(tmp_path)
    assert (await _stage(panel)).success
    # The new copy deletes the file it installed from and the request, and
    # declares itself.
    update.unlink()
    request.unlink()
    _declare(
        tmp_path, version="0.3.28", version_code=31,
        last_result="installed", last_result_version="0.3.28",
        last_result_message="Updated from 0.3.27.", last_result_at="2026-11-06T09:00:00Z",
    )
    panel.refresh()
    ns = _ns(state)
    assert (ns["version"], ns["version_code"], ns["last_result"]) == ("0.3.28", 31, "installed")
    assert (ns["pending_version"], ns["pending_install"]) == ("", "")
    assert not (tmp_path / ps.RECORD_NAME).exists()


async def test_a_newer_declaration_ends_the_wait_before_the_file_is_deleted(tmp_path):
    _declare(tmp_path)
    panel, state, _ = _panel(tmp_path)
    assert (await _stage(panel)).success
    _declare(tmp_path, version="0.3.28", version_code=31, last_result="installed")
    panel.refresh()
    assert _ns(state)["pending_install"] == ""


async def test_a_refused_request_is_never_asked_for_again_by_itself(tmp_path):
    _declare(tmp_path)
    _, request = _files(tmp_path)
    panel, state, _ = _panel(tmp_path)
    assert (await _stage(panel)).success
    # The panel refuses it: it removes the request and says why.
    request.unlink()
    _declare(
        tmp_path, last_result="refused", last_result_version="0.3.28",
        last_result_message="The file is not signed with this panel's key.",
    )
    panel.refresh()
    panel.refresh()
    assert not request.exists()
    ns = _ns(state)
    assert (ns["pending_install"], ns["last_result"]) == ("", "refused")
    assert ns["last_result_message"] == "The file is not signed with this panel's key."


async def test_a_file_replaced_by_somebody_else_is_not_reported_as_staged_here(tmp_path):
    _declare(tmp_path)
    update, _ = _files(tmp_path)
    panel, state, _ = _panel(tmp_path)
    assert (await _stage(panel, install="at_panel")).success
    update.write_bytes(b"copied on by hand, a different size")
    panel.refresh()
    assert _ns(state)["pending_install"] == ""


# ── the download the stager uses ─────────────────────────────────────────


def _manager(tmp_path) -> UpdateManager:
    mgr = UpdateManager.__new__(UpdateManager)
    mgr._data_dir = tmp_path
    mgr._state = MagicMock()
    mgr._update_in_progress = False
    return mgr


def _client(length: int, chunks: list[bytes]):
    class _Stream:
        headers = {"content-length": str(length)}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def raise_for_status(self):
            pass

        async def aiter_bytes(self, chunk_size=0):
            for chunk in chunks:
                yield chunk

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def stream(self, method, url):
            return _Stream()

    return _Client


async def test_download_to_writes_the_file_and_reports_progress(tmp_path, monkeypatch):
    monkeypatch.setattr("openavc.updater.manager.internet_client", _client(4, [b"ab", b"cd"]))
    seen: list[int] = []
    size = await _manager(tmp_path).download_to(URL, tmp_path / "f.bin", progress=seen.append)
    assert size == 4
    assert (tmp_path / "f.bin").read_bytes() == b"abcd"
    assert seen == [50, 99]


@pytest.mark.parametrize("length, chunks", [
    (100, [b"x" * 10]),            # announces more than the limit
    (0, [b"x" * 6, b"x" * 6]),     # announces nothing, delivers more
])
async def test_download_to_stops_at_the_limit_and_leaves_nothing(tmp_path, monkeypatch, length, chunks):
    monkeypatch.setattr("openavc.updater.manager.internet_client", _client(length, chunks))
    with pytest.raises(RuntimeError, match="limit"):
        await _manager(tmp_path).download_to(URL, tmp_path / "f.bin", max_bytes=10)
    assert not (tmp_path / "f.bin").exists()


async def test_a_server_download_still_reports_progress_and_size(tmp_path, monkeypatch):
    monkeypatch.setattr("openavc.updater.manager.internet_client", _client(4, [b"ab", b"cd"]))
    # The server download's own free-space floor, answered here rather than
    # by whatever disk runs the test.
    monkeypatch.setattr(
        "openavc.updater.manager.shutil.disk_usage", lambda _p: SimpleNamespace(free=10 * 1024**3),
    )
    mgr = _manager(tmp_path)
    path = await mgr._download_artifact(URL, "a.tar.gz")
    assert path == tmp_path / "update-cache" / "a.tar.gz"
    assert path.read_bytes() == b"abcd"
    mgr._state.set.assert_any_call("system.update_progress", 99, source="system")


# ── the cloud message ────────────────────────────────────────────────────


def _handler(panel):
    agent = MagicMock()
    agent.send_message = AsyncMock()
    events = MagicMock()
    events.emit = AsyncMock()
    return CommandHandler(agent, MagicMock(), events, panel_software=panel), agent


def _message(**payload):
    body = {
        "request_id": "req-1",
        "target_version": "0.3.28",
        "target_version_code": 31,
        "update_url": URL,
        "checksum_sha256": NEW_SHA,
        "signer_sha256": SIGNER,
        "install": "when_idle",
        "user_id": "u-1",
        "user_name": "tech@example.test",
    }
    body.update(payload)
    return {"type": "panel_software_update", "payload": body}


async def test_the_cloud_message_reaches_the_stager_and_answers_with_its_sentence():
    panel = MagicMock()
    panel.stage = AsyncMock(return_value=StageOutcome(True, "Panel software 0.3.28 is ready on this panel."))
    handler, agent = _handler(panel)
    await handler.handle(_message())
    panel.stage.assert_awaited_once_with(
        update_url=URL, checksum_sha256=NEW_SHA, target_version="0.3.28",
        target_version_code=31, signer_sha256=SIGNER, install="when_idle",
    )
    agent.send_message.assert_awaited_once_with("command_result", {
        "request_id": "req-1", "success": True,
        "result": "Panel software 0.3.28 is ready on this panel.", "error": None,
    })


async def test_a_refusal_answers_with_its_code_then_its_sentence():
    panel = MagicMock()
    panel.stage = AsyncMock(return_value=StageOutcome(False, "This panel already has panel software 0.3.28.", "not_newer"))
    handler, agent = _handler(panel)
    await handler.handle(_message())
    agent.send_message.assert_awaited_once_with("command_result", {
        "request_id": "req-1", "success": False, "result": None,
        "error": "not_newer: This panel already has panel software 0.3.28.",
    })


async def test_a_system_without_panel_software_updates_answers_unavailable():
    handler, agent = _handler(None)
    await handler.handle(_message())
    (_, payload), _ = agent.send_message.await_args
    assert payload["success"] is False
    assert payload["error"].startswith("subsystem_unavailable:")


async def test_end_to_end_the_message_stages_the_file(tmp_path):
    _declare(tmp_path)
    update, request = _files(tmp_path)
    panel, _, _ = _panel(tmp_path)
    handler, agent = _handler(panel)
    await handler.handle(_message())
    (_, payload), _ = agent.send_message.await_args
    assert payload["success"] is True, payload
    assert update.read_bytes() == NEW
    assert request.read_text(encoding="utf-8") == f"sha256={NEW_SHA}\n"


# ── the agent's gate ─────────────────────────────────────────────────────


def _agent(enabled: list[str], handler=None):
    from openavc.cloud.agent import CloudAgent

    agent = CloudAgent.__new__(CloudAgent)
    agent._session = None
    agent._connected = False
    agent._enabled_capabilities = enabled
    agent._command_handler = handler
    agent._ai_tool_handler = None
    agent._tunnel_handler = None
    agent.sent = []

    async def spy_send(msg_type, payload):
        agent.sent.append((msg_type, payload))

    agent.send_message = spy_send
    return agent


def test_every_system_announces_it():
    from openavc.cloud.agent import DEFAULT_CAPABILITIES, _CAPABILITY_GATED

    assert "panel_software_update" in DEFAULT_CAPABILITIES
    assert _CAPABILITY_GATED["panel_software_update"] == "panel_software_update"


async def test_without_the_capability_the_agent_refuses_it_in_kind():
    handler = MagicMock()
    handler.handle = AsyncMock()
    agent = _agent(["monitoring", "fleet_update"], handler)
    await agent._handle_message(_message())
    handler.handle.assert_not_awaited()
    assert len(agent.sent) == 1
    msg_type, payload = agent.sent[0]
    assert (msg_type, payload["request_id"], payload["success"]) == ("command_result", "req-1", False)
    assert payload["error"].startswith("capability_disabled:")
    assert "panel_software_update" in payload["error"]


async def test_with_the_capability_the_agent_hands_it_to_the_command_handler():
    handler = MagicMock()
    handler.handle = AsyncMock()
    agent = _agent(["panel_software_update"], handler)
    msg = _message()
    await agent._handle_message(msg)
    handler.handle.assert_awaited_once_with(msg)
    assert agent.sent == []
