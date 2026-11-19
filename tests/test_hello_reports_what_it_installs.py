"""The hello tells the cloud whether this system installs an update sent to
it, and which release file it installs one from.

The updater owns both answers (updater/platform.py): the cloud offers a
system only what its hello says it takes, so the answer it reports is the one
the updater acts on when the update arrives. The contract's deployment modes
carry the file each mode takes, which is what the cloud reads for an agent
whose hello reports neither.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from openavc.cloud import protocol
from openavc.cloud.handshake import Handshake
from openavc.cloud.wire_tables import VOCABULARIES
from openavc.updater import platform as update_platform
from openavc.updater.host import get_firmware_host
from openavc.updater.platform import DeploymentType, artifact_kind, can_self_update


@pytest.fixture(autouse=True)
def _no_firmware_host(monkeypatch):
    monkeypatch.delenv("OPENAVC_FIRMWARE_HOST", raising=False)
    get_firmware_host.cache_clear()
    yield
    get_firmware_host.cache_clear()


@pytest.mark.parametrize("deployment, kind", [
    (DeploymentType.WINDOWS_INSTALLER, "installer"),
    (DeploymentType.LINUX_PACKAGE, "package"),
    (DeploymentType.MACOS_APP, "package"),
    (DeploymentType.ANDROID_APPLIANCE, "package"),
    (DeploymentType.DOCKER, "none"),
    (DeploymentType.GIT_DEV, "none"),
    (DeploymentType.UNKNOWN, "none"),
])
def test_each_install_type_names_the_file_it_updates_from(deployment, kind):
    assert artifact_kind(deployment) == kind
    assert can_self_update(deployment) == (kind != "none")


def test_an_update_is_installed_by_a_service_with_no_firmware_owner(monkeypatch):
    monkeypatch.setattr(update_platform.restart, "is_service_managed", lambda: True)
    assert update_platform.installs_updates(DeploymentType.LINUX_PACKAGE) is True
    assert update_platform.installs_updates(DeploymentType.DOCKER) is False


def test_a_copy_started_by_hand_installs_no_update(monkeypatch):
    monkeypatch.setattr(update_platform.restart, "is_service_managed", lambda: False)
    assert update_platform.installs_updates(DeploymentType.LINUX_PACKAGE) is False


def test_a_system_whose_firmware_owns_its_updates_installs_none(monkeypatch, tmp_path):
    host = tmp_path / "host.json"
    host.write_text(json.dumps(
        {"schema": 1, "update_owner": "firmware", "firmware_version": "1.2.3"}))
    monkeypatch.setenv("OPENAVC_FIRMWARE_HOST", str(host))
    get_firmware_host.cache_clear()
    monkeypatch.setattr(update_platform.restart, "is_service_managed", lambda: True)
    assert update_platform.installs_updates(DeploymentType.ANDROID_APPLIANCE) is False


def _sent_hello(monkeypatch, deployment: DeploymentType, managed: bool) -> dict:
    monkeypatch.setattr(update_platform, "detect_deployment_type", lambda: deployment)
    monkeypatch.setattr(update_platform.restart, "is_service_managed", lambda: managed)
    sent: list[dict] = []

    async def send(msg):
        sent.append(msg)

    async def recv():
        raise ConnectionError("closed after the hello")

    handshake = Handshake("sys-1", b"k" * 32, "0.41.0", ["monitoring"])
    with pytest.raises(ConnectionError):
        asyncio.run(handshake.perform(send, recv))
    return sent[0]["payload"]


@pytest.mark.parametrize("deployment, managed, updates, kind", [
    (DeploymentType.WINDOWS_INSTALLER, True, True, "installer"),
    (DeploymentType.MACOS_APP, True, True, "package"),
    (DeploymentType.LINUX_PACKAGE, False, False, "package"),
    (DeploymentType.DOCKER, True, False, "none"),
])
def test_the_hello_reports_the_updaters_answers(monkeypatch, deployment, managed, updates, kind):
    payload = _sent_hello(monkeypatch, deployment, managed)
    assert payload["deployment_mode"] == deployment.value
    assert payload["updates_itself"] is updates
    assert payload["artifact_kind"] == kind


def test_the_artifact_kinds_are_the_updaters():
    kinds = {artifact_kind(d) for d in DeploymentType}
    assert set(protocol.vocabulary("artifact_kinds")) == kinds


def test_each_deployment_mode_carries_the_file_it_takes():
    """What the cloud reads for an agent whose hello reports no artifact_kind:
    each mode flagged with the kind it takes, a mode that takes none with none."""
    kinds = set(protocol.vocabulary("artifact_kinds")) - {"none"}
    for entry in VOCABULARIES["deployment_modes"]:
        expected = artifact_kind(DeploymentType(entry.value))
        assert set(entry.flags) & kinds == ({expected} - {"none"}), entry.value


def test_the_hello_fields_name_their_vocabulary_and_release():
    fields = {f.name: f for f in protocol.MESSAGE_ROWS["hello"].fields}
    assert fields["updates_itself"].type == "boolean"
    assert fields["artifact_kind"].vocabulary == "artifact_kinds"
    assert fields["updates_itself"].since == fields["artifact_kind"].since == "0.41.0"
