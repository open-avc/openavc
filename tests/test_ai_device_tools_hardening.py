"""Regression tests for the cloud AI device/driver tool hardening.

Covers the audit findings fixed in the server/cloud/tools/device_tools.py group:
  H-040 update_driver_definition built-in guard + save-before-delete
  H-041 install_community_driver GitHub host allowlist (SSRF)
  H-042 update/delete bump the project revision + notify the IDE
  M-079 install_community_driver min_platform_version gate
  M-080 update_device splits connection fields into the connections table
  M-081 update_device honors an `enabled` toggle
  M-082 test_driver_command guards a malformed-escape delimiter
  L-054 install_community_driver rejects an id that diverges from the file
  L-055 set_device_setting rejects a non-primitive value
  L-056 test_device_connection reports a missing port instead of probing :23
  V-API-008 install_community_driver delegates to the canonical REST install
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from openavc.api import rest, ws
from openavc.cloud.ai_tool_handler import AIToolHandler
from openavc.drivers.registry import register_driver, unregister_driver
from openavc.core.engine import Engine
from openavc.core.project_loader import (
    DeviceConfig,
    ProjectConfig,
    ProjectMeta,
    load_project,
    save_project,
)
from openavc.drivers.base import BaseDriver

_GITHUB_URL = "https://raw.githubusercontent.com/open-avc/openavc-drivers/main/acme.avcdriver"


class _NoopTCP(BaseDriver):
    """No-op TCP driver: connect never opens a socket, so the DeviceManager
    can add/update the device without real I/O."""

    DRIVER_INFO: dict[str, Any] = {
        "id": "noop_tcp",
        "name": "Noop TCP",
        "transport": "tcp",
        "default_config": {"port": 4000},
        "state_variables": {},
        "commands": {},
        "device_settings": {"brightness": {"type": "integer", "label": "Brightness"}},
    }

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        return None


def _pure_handler() -> AIToolHandler:
    """A handler with no engine, for tools that early-return before touching one."""
    return AIToolHandler(MagicMock(), MagicMock(), MagicMock())


# ---------------------------------------------------------------------------
# Pure tools (no engine, no filesystem) — H-041, M-079, M-082
# ---------------------------------------------------------------------------

async def test_install_rejects_cloud_metadata_url() -> None:
    """H-041: a link-local SSRF target is refused before any fetch."""
    handler = _pure_handler()
    result = await handler._install_community_driver(
        {"driver_id": "acme", "file_url": "http://169.254.169.254/latest/meta-data/x.avcdriver"}
    )
    # Named by repo, not by host: "it is a GitHub URL" is exactly the test that
    # was never enough, since any repo on that host passed it.
    assert "open-avc/openavc-drivers" in result.get("error", "")


async def test_install_rejects_intranet_url() -> None:
    """H-041: an arbitrary intranet host is refused."""
    handler = _pure_handler()
    result = await handler._install_community_driver(
        {"driver_id": "acme", "file_url": "http://10.0.0.5/driver.py"}
    )
    assert "open-avc/openavc-drivers" in result.get("error", "")


async def test_install_min_platform_gate_request_field() -> None:
    """M-079: a driver requiring a newer platform is blocked up front."""
    handler = _pure_handler()
    result = await handler._install_community_driver(
        {"driver_id": "acme", "file_url": _GITHUB_URL, "min_platform_version": "99.0.0"}
    )
    assert "99.0.0" in result.get("error", "")


async def test_test_driver_command_bad_delimiter() -> None:
    """M-082: a truncated escape in the delimiter returns a clean error rather
    than crashing on an uncaught UnicodeDecodeError."""
    handler = _pure_handler()
    result = await handler._test_driver_command(
        {"host": "127.0.0.1", "command_string": "PWR?", "delimiter": "\\x"}
    )
    assert result["success"] is False
    assert "delimiter" in result["error"].lower()


# ---------------------------------------------------------------------------
# install_community_driver id divergence — L-054 (mocked download)
# ---------------------------------------------------------------------------

async def test_install_rejects_id_divergence(tmp_path, monkeypatch) -> None:
    """L-054: a downloaded driver whose internal id differs from the requested
    id is rejected and the file is removed (not registered under a mismatched
    key that edit/delete can't later find)."""
    repo = tmp_path / "driver_repo"
    monkeypatch.setattr("openavc.system_config.DRIVER_REPO_DIR", repo)

    yaml_text = "id: real_acme\nname: Real Acme\ntransport: tcp\n"

    class _Resp:
        text = yaml_text
        content = yaml_text.encode("utf-8")

        def raise_for_status(self) -> None:
            return None

    class _Client:
        def __init__(self, *a, **k) -> None:
            pass

        async def __aenter__(self) -> "_Client":
            return self

        async def __aexit__(self, *a) -> bool:
            return False

        async def get(self, url: str) -> "_Resp":
            return _Resp()

    monkeypatch.setattr(httpx, "AsyncClient", _Client)

    handler = _pure_handler()
    result = await handler._install_community_driver(
        {"driver_id": "wrong_id", "file_url": _GITHUB_URL}
    )
    assert "real_acme" in result.get("error", "")
    # The mismatched file must not linger in the repo.
    assert not (repo / "wrong_id.avcdriver").exists()


# ---------------------------------------------------------------------------
# install_community_driver delegates to the canonical REST path — V-API-008
# ---------------------------------------------------------------------------

async def test_ai_install_completes_via_rest_path(tmp_path, monkeypatch) -> None:
    """V-API-008: the AI install tool routes through the shared REST install
    handler, so a YAML driver's declared Python companion is fetched too (the
    old forked tool wrote only the .avcdriver, silently breaking discovery) and
    the result reports the devices the shared path promotes from orphaned."""
    from unittest.mock import AsyncMock

    repo = tmp_path / "driver_repo"
    repo.mkdir()
    # The REST handler reads its repo dir + engine wiring, so target those.
    monkeypatch.setattr("openavc.api.routes.drivers._get_driver_repo_dir", lambda: repo)
    monkeypatch.setattr("openavc.api.routes.drivers.register_driver", lambda cls: None)
    monkeypatch.setattr(
        "openavc.api.discovery.refresh_all_device_matches",
        AsyncMock(return_value=None),
        raising=False,
    )
    # A live engine whose orphan promotion the shared path runs + reports.
    fake_engine = MagicMock()
    fake_engine.devices.retry_all_orphans = AsyncMock(return_value=["waiting_dev"])
    rest.set_engine(fake_engine)

    yaml_text = (
        "id: acme_widget\n"
        "name: Acme Widget\n"
        "transport: tcp\n"
        "commands:\n  power_on:\n    label: 'On'\n    send: \"P\\r\"\n    params: {}\n"
        "responses:\n  - match: OK\n    mappings:\n      - group: 0\n        state: ok\n"
        "state_variables:\n  ok:\n    type: string\n    label: OK\n"
        "discovery:\n  python:\n    file: ./acme_widget_discovery.py\n    cross_vendor: true\n"
    )
    companion_text = "async def probe(ctx):\n    pass\n"

    yaml_resp = MagicMock(text=yaml_text, content=yaml_text.encode("utf-8"))
    yaml_resp.raise_for_status = MagicMock()
    companion_resp = MagicMock(text=companion_text, content=companion_text.encode("utf-8"))
    companion_resp.raise_for_status = MagicMock()
    # The install path reads the catalog first, to check the download against
    # the hashes it publishes. An empty catalog means "no hashes" — this test
    # is about the companion landing, not integrity.
    catalog_resp = MagicMock()
    catalog_resp.raise_for_status = MagicMock()
    catalog_resp.json.return_value = {"drivers": []}
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    # Three fetches: catalog, then the YAML download + companion download.
    client.get = AsyncMock(side_effect=[catalog_resp, yaml_resp, companion_resp])
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: client)

    url = "https://raw.githubusercontent.com/open-avc/openavc-drivers/main/acme_widget.avcdriver"
    handler = _pure_handler()
    try:
        result = await handler._install_community_driver(
            {"driver_id": "acme_widget", "file_url": url}
        )
    finally:
        rest.set_engine(None)

    assert result["status"] == "installed"
    assert result["driver_id"] == "acme_widget"
    # The declared companion landed — the whole point of routing through REST.
    assert (repo / "acme_widget.avcdriver").exists()
    assert (repo / "acme_widget_discovery.py").read_text(encoding="utf-8") == companion_text
    # Orphaned devices waiting on this driver are promoted and reported.
    assert result["activated_devices"] == ["waiting_dev"]


# ---------------------------------------------------------------------------
# update_driver_definition built-in guard — H-040
# ---------------------------------------------------------------------------

async def test_update_driver_definition_rejects_builtin(tmp_path, monkeypatch) -> None:
    """H-040: a shipped built-in (read-only definitions dir) can't be edited or
    deleted via the AI path; the file survives the rejected call."""
    builtin = tmp_path / "definitions"
    repo = tmp_path / "driver_repo"
    builtin.mkdir()
    repo.mkdir()
    monkeypatch.setattr("openavc.system_config.DRIVER_DEFINITIONS_DIR", builtin)
    monkeypatch.setattr("openavc.system_config.DRIVER_REPO_DIR", repo)

    builtin_file = builtin / "ship_acme.avcdriver"
    builtin_file.write_text("id: ship_acme\nname: Ship Acme\ntransport: tcp\n", encoding="utf-8")

    handler = _pure_handler()
    result = await handler._update_driver_definition(
        {"driver_id": "ship_acme", "definition": {"id": "ship_acme", "name": "Hacked", "transport": "tcp"}}
    )
    assert "built-in" in result.get("error", "").lower()
    # The shipped file is untouched.
    assert builtin_file.exists()
    assert "Ship Acme" in builtin_file.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Engine-backed tools — M-080, M-081, H-042, L-055, L-056
# ---------------------------------------------------------------------------

@pytest.fixture
async def device_engine(tmp_path):
    """Real engine + live no-op device, wired into the rest module the AI tools
    read via _get_engine()."""
    register_driver(_NoopTCP)
    project_path = str(tmp_path / "project.avc")
    engine = Engine(project_path)
    engine.project = ProjectConfig(
        project=ProjectMeta(id="p", name="P"),
        devices=[
            DeviceConfig(id="dev1", driver="noop_tcp", name="Dev 1", config={"transport": "tcp"}),
            # host-in-config, no port — the L-056 scenario.
            DeviceConfig(id="dev_noport", driver="noop_tcp", name="No Port", config={"transport": "tcp", "host": "1.2.3.4"}),
        ],
        connections={"dev1": {"host": "10.0.0.5", "port": 4000}},
    )
    save_project(project_path, engine.project)
    for device in engine.project.devices:
        await engine.devices.add_device(engine.resolved_device_config(device))

    rest.set_engine(engine)
    ws.set_engine(engine)
    handler = AIToolHandler(MagicMock(), engine.devices, MagicMock())
    try:
        yield handler, engine
    finally:
        await engine.devices.disconnect_all()
        rest.set_engine(None)
        ws.set_engine(None)
        unregister_driver("noop_tcp")


async def test_update_device_splits_connection_fields(device_engine) -> None:
    """M-080: host/port in the AI's config land in the connections table, not
    device.config."""
    handler, engine = device_engine
    result = await handler._update_device(
        {"device_id": "dev1", "config": {"host": "10.0.0.99", "port": 6000, "baud": 9600}}
    )
    assert result == {"status": "updated", "device_id": "dev1"}

    reloaded = load_project(engine.project_path)
    dev = next(d for d in reloaded.devices if d.id == "dev1")
    # Connection fields routed to the connections table...
    assert reloaded.connections["dev1"]["host"] == "10.0.0.99"
    assert reloaded.connections["dev1"]["port"] == 6000
    # ...and protocol fields stay in device.config (no host/port leak).
    assert dev.config.get("baud") == 9600
    assert "host" not in dev.config
    assert "port" not in dev.config


async def test_update_device_honors_enabled(device_engine) -> None:
    """M-081: an `enabled` toggle from the AI is applied (not pinned)."""
    handler, engine = device_engine
    result = await handler._update_device({"device_id": "dev1", "enabled": False})
    assert result["status"] == "updated"
    reloaded = load_project(engine.project_path)
    dev = next(d for d in reloaded.devices if d.id == "dev1")
    assert dev.enabled is False


async def test_update_device_preserves_forward_compat_extra_fields(device_engine) -> None:
    """M-160: the cloud AI device-update tool must preserve forward-compat
    top-level extra fields (extra='allow' / __pydantic_extra__) instead of
    dropping them by rebuilding a fresh DeviceConfig from known fields only.
    """
    handler, engine = device_engine
    idx = next(i for i, d in enumerate(engine.project.devices) if d.id == "dev1")
    old = engine.project.devices[idx]
    # A field a newer platform version wrote that this build doesn't model.
    engine.project.devices[idx] = DeviceConfig(
        id=old.id,
        driver=old.driver,
        name=old.name,
        config=old.config,
        enabled=old.enabled,
        future_field="keep-me",
    )

    await handler._update_device({"device_id": "dev1", "name": "Renamed"})

    reloaded = load_project(engine.project_path)
    dev = next(d for d in reloaded.devices if d.id == "dev1")
    assert dev.name == "Renamed"
    assert dev.model_dump().get("future_field") == "keep-me", (
        "cloud _update_device dropped a forward-compat top-level field — M-160"
    )


async def test_update_device_bumps_revision(device_engine) -> None:
    """H-042: a device update advances the project revision so a stale IDE
    can't silently overwrite it."""
    handler, engine = device_engine
    before = engine._project_revision
    await handler._update_device({"device_id": "dev1", "name": "Renamed"})
    assert engine._project_revision > before


async def test_delete_device_bumps_revision(device_engine) -> None:
    """H-042: a device delete advances the project revision + notifies."""
    handler, engine = device_engine
    before = engine._project_revision
    result = await handler._delete_device({"device_id": "dev1"})
    assert result["status"] == "deleted"
    assert engine._project_revision > before


async def test_set_device_setting_rejects_non_primitive(device_engine) -> None:
    """L-055: a non-primitive setting value is rejected at the tool layer before
    it can reach the driver or the flat-primitive state store."""
    handler, _engine = device_engine
    result = await handler._set_device_setting(
        {"device_id": "dev1", "setting_key": "brightness", "value": [1, 2, 3]}
    )
    assert "error" in result
    assert "string" in result["error"] or "number" in result["error"]


async def test_test_device_connection_reports_missing_port(device_engine) -> None:
    """L-056: a TCP device with no port reports the gap instead of probing :23."""
    handler, _engine = device_engine
    result = await handler._test_device_connection({"device_id": "dev_noport"})
    assert result["success"] is False
    assert "port" in result["error"].lower()
