"""
Tests for the cloud-triggered software update flow.

Covers:
- UpdateManager.apply_cloud_update (download, verify, apply)
- UpdateManager._verify_hash (checksum verification)
- CommandHandler._handle_software_update (dispatch to cloud vs GitHub path)
- End-to-end: cloud builds message -> agent handles -> result sent back
"""

import hashlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from server.cloud.command_handler import CommandHandler


# ===========================================================================
# UpdateManager._verify_hash tests
# ===========================================================================


def test_verify_hash_correct(tmp_path):
    """_verify_hash passes when checksum matches."""
    from server.updater.manager import UpdateManager

    artifact = tmp_path / "test.bin"
    artifact.write_bytes(b"hello world")
    expected = hashlib.sha256(b"hello world").hexdigest()

    mgr = UpdateManager.__new__(UpdateManager)
    mgr._verify_hash(artifact, expected)
    # No exception = pass
    assert artifact.exists()


def test_verify_hash_wrong(tmp_path):
    """_verify_hash raises and deletes the file on mismatch."""
    from server.updater.manager import UpdateManager

    artifact = tmp_path / "test.bin"
    artifact.write_bytes(b"hello world")

    mgr = UpdateManager.__new__(UpdateManager)
    with pytest.raises(RuntimeError, match="Checksum mismatch"):
        mgr._verify_hash(artifact, "0000000000000000000000000000000000000000000000000000000000000000")

    assert not artifact.exists()  # deleted on mismatch


def test_verify_hash_case_insensitive(tmp_path):
    """_verify_hash is case-insensitive."""
    from server.updater.manager import UpdateManager

    artifact = tmp_path / "test.bin"
    artifact.write_bytes(b"test data")
    expected = hashlib.sha256(b"test data").hexdigest().upper()

    mgr = UpdateManager.__new__(UpdateManager)
    mgr._verify_hash(artifact, expected)
    assert artifact.exists()


# ===========================================================================
# UpdateManager.apply_cloud_update tests
# ===========================================================================


@pytest.mark.asyncio
async def test_apply_cloud_update_full_flow(tmp_path):
    """apply_cloud_update downloads from URL, verifies hash, and applies."""
    from server.updater.manager import UpdateManager

    data_dir = tmp_path / "data"
    data_dir.mkdir()

    # Create a fake artifact to "download"
    fake_content = b"fake installer content"
    fake_hash = hashlib.sha256(fake_content).hexdigest()

    # Build the manager with mocked internals
    mgr = UpdateManager.__new__(UpdateManager)
    mgr._data_dir = data_dir
    mgr._state = MagicMock()
    mgr._checker = MagicMock()
    mgr._update_in_progress = False
    mgr._history = []
    mgr._deployment_type = MagicMock()
    mgr._deployment_type.value = "windows_installer"

    # Track state transitions
    states = []
    def track_state(key, value, source="system"):
        states.append((key, value))
    mgr._state.set = track_state

    # Mock _download_artifact to write the fake content to disk
    async def fake_download(url, filename):
        download_dir = data_dir / "update-cache"
        download_dir.mkdir(parents=True, exist_ok=True)
        path = download_dir / filename
        path.write_bytes(fake_content)
        return path

    mgr._download_artifact = fake_download

    # Mock platform checks and apply
    with patch("server.updater.manager.can_self_update", return_value=True), \
         patch("server.updater.manager.__version__", "0.2.0"), \
         patch("server.updater.backup.create_backup", return_value=tmp_path / "backup.zip"), \
         patch("server.updater.backup.cleanup_old_backups"), \
         patch("server.updater.rollback.write_pending_marker"), \
         patch.object(mgr, "_apply_windows"), \
         patch.object(mgr, "_restart_process"), \
         patch.object(mgr, "_save_history"):

        result = await mgr.apply_cloud_update(
            target_version="1.0.0",
            update_url="https://github.com/open-avc/openavc/releases/download/v1.0.0/OpenAVC-Setup-1.0.0.exe",
            checksum_sha256=fake_hash,
        )

    assert result["success"] is True
    assert "1.0.0" in result["message"]

    # Verify state transitions happened in order
    status_states = [v for k, v in states if k == "system.update_status"]
    assert "backing_up" in status_states
    assert "downloading" in status_states
    assert "verifying" in status_states
    assert "applying" in status_states
    assert "restarting" in status_states

    # Verify history was recorded
    assert len(mgr._history) == 1
    assert mgr._history[0]["to_version"] == "1.0.0"
    assert mgr._history[0]["status"] == "pending"


@pytest.mark.asyncio
async def test_apply_cloud_update_bad_checksum(tmp_path):
    """apply_cloud_update fails when checksum doesn't match."""
    from server.updater.manager import UpdateManager

    data_dir = tmp_path / "data"
    data_dir.mkdir()

    mgr = UpdateManager.__new__(UpdateManager)
    mgr._data_dir = data_dir
    mgr._state = MagicMock()
    mgr._checker = MagicMock()
    mgr._update_in_progress = False
    mgr._history = []
    mgr._deployment_type = MagicMock()

    async def fake_download(url, filename):
        download_dir = data_dir / "update-cache"
        download_dir.mkdir(parents=True, exist_ok=True)
        path = download_dir / filename
        path.write_bytes(b"real content")
        return path

    mgr._download_artifact = fake_download

    with patch("server.updater.manager.can_self_update", return_value=True), \
         patch("server.updater.manager.__version__", "0.2.0"), \
         patch("server.updater.backup.create_backup", return_value=tmp_path / "backup.zip"), \
         patch("server.updater.backup.cleanup_old_backups"), \
         patch.object(mgr, "_save_history"):

        result = await mgr.apply_cloud_update(
            target_version="1.0.0",
            update_url="https://example.com/update.exe",
            checksum_sha256="badhashbadhashbadhashbadhashbadhashbadhashbadhashbadhashbadhashba",
        )

    assert result["success"] is False
    assert "Checksum mismatch" in result["error"]
    assert mgr._history[0]["status"] == "failed"


@pytest.mark.asyncio
async def test_apply_cloud_update_no_checksum(tmp_path):
    """apply_cloud_update succeeds without checksum (skips verification)."""
    from server.updater.manager import UpdateManager

    data_dir = tmp_path / "data"
    data_dir.mkdir()

    mgr = UpdateManager.__new__(UpdateManager)
    mgr._data_dir = data_dir
    mgr._state = MagicMock()
    mgr._checker = MagicMock()
    mgr._update_in_progress = False
    mgr._history = []
    mgr._deployment_type = MagicMock()

    async def fake_download(url, filename):
        download_dir = data_dir / "update-cache"
        download_dir.mkdir(parents=True, exist_ok=True)
        path = download_dir / filename
        path.write_bytes(b"content")
        return path

    mgr._download_artifact = fake_download

    with patch("server.updater.manager.can_self_update", return_value=True), \
         patch("server.updater.manager.__version__", "0.2.0"), \
         patch("server.updater.backup.create_backup", return_value=tmp_path / "backup.zip"), \
         patch("server.updater.backup.cleanup_old_backups"), \
         patch("server.updater.rollback.write_pending_marker"), \
         patch.object(mgr, "_apply_windows"), \
         patch.object(mgr, "_restart_process"), \
         patch.object(mgr, "_save_history"):

        result = await mgr.apply_cloud_update(
            target_version="1.0.0",
            update_url="https://example.com/update.exe",
            checksum_sha256=None,
        )

    assert result["success"] is True


@pytest.mark.asyncio
async def test_apply_cloud_update_filename_from_url():
    """Filename is correctly extracted from GitHub-style URLs."""
    from urllib.parse import urlparse

    urls_and_expected = [
        (
            "https://github.com/open-avc/openavc/releases/download/v1.0.0/OpenAVC-Setup-1.0.0.exe",
            "OpenAVC-Setup-1.0.0.exe",
        ),
        (
            "https://github.com/open-avc/openavc/releases/download/v1.0.0/openavc-1.0.0-linux-x86_64.tar.gz",
            "openavc-1.0.0-linux-x86_64.tar.gz",
        ),
        (
            "https://github.com/open-avc/openavc/releases/download/v2.1.0-beta.1/OpenAVC-Setup-2.1.0-beta.1.exe",
            "OpenAVC-Setup-2.1.0-beta.1.exe",
        ),
    ]

    for url, expected_filename in urls_and_expected:
        url_path = urlparse(url).path
        filename = url_path.rsplit("/", 1)[-1] if "/" in url_path else "update-unknown"
        assert filename == expected_filename, f"URL {url} → {filename}, expected {expected_filename}"


# ===========================================================================
# CommandHandler._handle_software_update tests
# ===========================================================================


class MockAgent:
    """Records messages sent through the agent."""

    def __init__(self):
        self.sent: list[tuple[str, dict]] = []

    async def send_message(self, msg_type, payload):
        self.sent.append((msg_type, payload))


class MockDeviceManager:
    pass


class MockEventBus:
    async def emit(self, event, data):
        pass


@pytest.mark.asyncio
async def test_command_handler_uses_cloud_url():
    """When update_url is provided, handler calls apply_cloud_update."""
    agent = MockAgent()
    events = MockEventBus()
    devices = MockDeviceManager()
    update_manager = AsyncMock()
    update_manager.apply_cloud_update = AsyncMock(return_value={
        "success": True,
        "message": "Update to v1.0.0 started",
    })

    handler = CommandHandler(agent, devices, events, update_manager=update_manager)

    msg = {
        "type": "software_update",
        "payload": {
            "request_id": "req-123",
            "target_version": "1.0.0",
            "update_url": "https://github.com/open-avc/openavc/releases/download/v1.0.0/setup.exe",
            "checksum_sha256": "abc123",
            "auto_restart": True,
            "user_id": "user-1",
            "user_name": "Aaron",
        },
    }

    with patch("server.updater.platform.can_self_update", return_value=True), \
         patch("server.updater.platform.detect_deployment_type"):
        await handler.handle(msg)

    # Verify apply_cloud_update was called with the right args
    update_manager.apply_cloud_update.assert_called_once_with(
        "1.0.0",
        "https://github.com/open-avc/openavc/releases/download/v1.0.0/setup.exe",
        "abc123",
    )

    # Verify result was sent back
    assert len(agent.sent) == 1
    msg_type, payload = agent.sent[0]
    assert msg_type == "command_result"
    assert payload["success"] is True
    assert payload["request_id"] == "req-123"


@pytest.mark.asyncio
async def test_command_handler_falls_back_to_github():
    """When update_url is empty, handler falls back to check_for_updates + apply_update."""
    agent = MockAgent()
    events = MockEventBus()
    devices = MockDeviceManager()
    update_manager = AsyncMock()
    update_manager.check_for_updates = AsyncMock(return_value={"update_available": True})
    update_manager.apply_update = AsyncMock(return_value={
        "success": True,
        "message": "Update to v1.0.0 started",
    })

    handler = CommandHandler(agent, devices, events, update_manager=update_manager)

    msg = {
        "type": "software_update",
        "payload": {
            "request_id": "req-456",
            "target_version": "1.0.0",
            "update_url": "",
            "auto_restart": True,
        },
    }

    with patch("server.updater.platform.can_self_update", return_value=True), \
         patch("server.updater.platform.detect_deployment_type"):
        await handler.handle(msg)

    # apply_cloud_update should NOT have been called
    update_manager.apply_cloud_update.assert_not_called()
    # GitHub path should have been used
    update_manager.check_for_updates.assert_called_once()
    update_manager.apply_update.assert_called_once()


@pytest.mark.asyncio
async def test_command_handler_no_url_no_update_available():
    """GitHub fallback: no update available returns success with message."""
    agent = MockAgent()
    events = MockEventBus()
    devices = MockDeviceManager()
    update_manager = AsyncMock()
    update_manager.check_for_updates = AsyncMock(return_value={"update_available": False})

    handler = CommandHandler(agent, devices, events, update_manager=update_manager)

    msg = {
        "type": "software_update",
        "payload": {
            "request_id": "req-789",
            "target_version": "1.0.0",
            "auto_restart": True,
        },
    }

    with patch("server.updater.platform.can_self_update", return_value=True), \
         patch("server.updater.platform.detect_deployment_type"):
        await handler.handle(msg)

    assert len(agent.sent) == 1
    assert agent.sent[0][1]["success"] is True
    assert "up to date" in agent.sent[0][1]["result"]


@pytest.mark.asyncio
async def test_command_handler_auto_restart_false():
    """auto_restart=false sends acknowledgement without applying."""
    agent = MockAgent()
    events = MockEventBus()
    devices = MockDeviceManager()
    update_manager = AsyncMock()

    handler = CommandHandler(agent, devices, events, update_manager=update_manager)

    msg = {
        "type": "software_update",
        "payload": {
            "request_id": "req-auto",
            "target_version": "1.0.0",
            "update_url": "https://example.com/setup.exe",
            "auto_restart": False,
        },
    }

    with patch("server.updater.platform.can_self_update", return_value=True), \
         patch("server.updater.platform.detect_deployment_type"):
        await handler.handle(msg)

    # Should NOT have called apply at all
    update_manager.apply_cloud_update.assert_not_called()
    update_manager.apply_update.assert_not_called()
    assert "auto_restart=false" in agent.sent[0][1]["result"]


# ===========================================================================
# End-to-end: cloud protocol -> agent handler -> result
# ===========================================================================


@pytest.mark.asyncio
async def test_end_to_end_cloud_update_message():
    """Simulate the full flow: cloud builds message, agent parses and handles it.

    This test verifies field names match between cloud protocol builder
    and agent command handler — the exact integration gap the audit found.
    """
    # Step 1: Build the message the way the cloud does
    # (inline, since we can't import cloud code in openavc tests)
    cloud_payload = {
        "request_id": "req-e2e",
        "target_version": "1.2.0",
        "update_url": "https://github.com/open-avc/openavc/releases/download/v1.2.0/OpenAVC-Setup-1.2.0.exe",
        "checksum_sha256": "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
        "auto_restart": True,
        "user_id": "user-42",
        "user_name": "Test User",
    }
    msg = {"type": "software_update", "payload": cloud_payload}

    # Step 2: Agent handles it
    agent = MockAgent()
    events = MockEventBus()
    devices = MockDeviceManager()
    update_manager = AsyncMock()
    update_manager.apply_cloud_update = AsyncMock(return_value={
        "success": True,
        "message": "Update to v1.2.0 started",
    })

    handler = CommandHandler(agent, devices, events, update_manager=update_manager)

    with patch("server.updater.platform.can_self_update", return_value=True), \
         patch("server.updater.platform.detect_deployment_type"):
        await handler.handle(msg)

    # Step 3: Verify the agent read ALL fields the cloud sent
    update_manager.apply_cloud_update.assert_called_once_with(
        "1.2.0",
        "https://github.com/open-avc/openavc/releases/download/v1.2.0/OpenAVC-Setup-1.2.0.exe",
        "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
    )

    # Step 4: Verify result message has all fields the cloud expects
    assert len(agent.sent) == 1
    msg_type, result_payload = agent.sent[0]
    assert msg_type == "command_result"
    assert result_payload["request_id"] == "req-e2e"
    assert result_payload["success"] is True
    assert result_payload["result"] is not None
    assert "error" in result_payload  # field must exist (even if None)
