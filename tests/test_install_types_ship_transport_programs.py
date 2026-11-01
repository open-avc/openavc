"""Every install type ships every program a transport runs.

The SSH transport runs the system's ``ssh``. When the Docker image and the
appliance's system were trimmed to the minimum, nothing asked what the
transports needed, and SSH devices could not connect on either. This pins the
table in ``openavc/system/os_programs.py`` against each install type's build
file in this repo (the appliance's own build is checked in its repo), and pins
how the appliance installs a missing program itself.
"""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

import pytest

from openavc.system import os_programs
from openavc.system.os_programs import SSH_CLIENT, TRANSPORT_PROGRAMS, missing_program_sentence

ROOT = Path(__file__).resolve().parent.parent


def _dockerfile_apt_packages() -> set[str]:
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    # The runtime stage's apt-get install list, line continuations joined.
    joined = text.replace("\\\n", " ")
    packages: set[str] = set()
    for m in re.finditer(r"apt-get install -y --no-install-recommends([^&\n]*)", joined):
        packages.update(m.group(1).split())
    return packages


@pytest.mark.parametrize("transport", sorted(TRANSPORT_PROGRAMS))
def test_docker_image_installs_the_program(transport):
    assert TRANSPORT_PROGRAMS[transport].apt in _dockerfile_apt_packages()


@pytest.mark.parametrize("transport", sorted(TRANSPORT_PROGRAMS))
def test_linux_installer_installs_the_program_on_every_package_manager(transport):
    program = TRANSPORT_PROGRAMS[transport]
    text = (ROOT / "installer" / "install.sh").read_text(encoding="utf-8")
    assert f"command -v {program.command}" in text
    assert f"apt-get install -y -qq {program.apt}" in text
    assert f"dnf install -y -q {program.dnf}" in text
    assert f"pacman -Sy --noconfirm --quiet {program.pacman}" in text


def test_linux_installer_runs_the_check_on_every_install():
    # ensure_system_prereqs runs whether or not Python was already present;
    # install_python does not, so the check must hang off the former.
    text = (ROOT / "installer" / "install.sh").read_text(encoding="utf-8")
    body = text.split("ensure_system_prereqs() {", 1)[1].split("\n}\n", 1)[0]
    assert "ensure_ssh_client" in body
    assert re.search(r"^\s+ensure_system_prereqs$", text.split("main() {", 1)[1], re.M)


def test_pi_image_builds_the_stage_that_carries_the_ssh_client():
    # pi-gen's stage2 installs Debian's `ssh` package, which brings the client
    # (measured: OpenSSH 10.0 on the 0.38.0 image). ENABLE_SSH only turns the
    # server off.
    config = (ROOT / "installer" / "pi-image" / "config").read_text(encoding="utf-8")
    stages = re.search(r'^STAGE_LIST="([^"]*)"', config, re.M).group(1).split()
    assert "stage2" in stages


# --- The device card's sentence, per install type ----------------------------


@pytest.fixture
def deployment(monkeypatch):
    def _set(value: str, platform: str = "linux"):
        monkeypatch.setattr(os_programs, "_deployment", lambda: value)
        monkeypatch.setattr(sys, "platform", platform)
    return _set


def test_a_panel_owner_is_never_told_to_install_anything(deployment):
    deployment("android_appliance")
    sentence = missing_program_sentence()
    assert not re.search(r"(^|\. )Install", sentence)
    assert "sudo" not in sentence and "apt" not in sentence
    assert "internet" in sentence


def test_docker_is_told_to_pull_the_new_image(deployment):
    deployment("docker")
    assert "docker compose pull" in missing_program_sentence()


def test_linux_is_given_the_command_for_its_package_manager(deployment):
    deployment("linux_package")
    sentence = missing_program_sentence()
    assert f"apt install {SSH_CLIENT.apt}" in sentence
    assert f"dnf install {SSH_CLIENT.dnf}" in sentence
    assert "Reconnect" in sentence


def test_windows_is_pointed_at_optional_features(deployment):
    deployment("windows_installer", platform="win32")
    assert "Optional features" in missing_program_sentence()


def test_no_sentence_has_an_em_dash(deployment):
    for value, platform in [
        ("android_appliance", "linux"), ("docker", "linux"), ("linux_package", "linux"),
        ("windows_installer", "win32"), ("macos_app", "darwin"),
    ]:
        deployment(value, platform)
        assert "—" not in missing_program_sentence()


# --- The appliance installs what is missing ----------------------------------


def test_nothing_runs_anywhere_but_the_appliance(monkeypatch):
    monkeypatch.setattr(os_programs, "can_install_programs", lambda: False)
    monkeypatch.setattr(os_programs, "missing_programs", lambda: [SSH_CLIENT])

    async def boom(*_a, **_k):
        raise AssertionError("must not sleep or install")

    monkeypatch.setattr(os_programs.asyncio, "sleep", boom)
    monkeypatch.setattr(os_programs, "install_missing_programs", boom)
    asyncio.run(os_programs.ensure_programs_loop(boom))


def test_nothing_runs_when_nothing_is_missing(monkeypatch):
    monkeypatch.setattr(os_programs, "can_install_programs", lambda: True)
    monkeypatch.setattr(os_programs, "missing_programs", lambda: [])

    async def boom(*_a, **_k):
        raise AssertionError("must not sleep or install")

    monkeypatch.setattr(os_programs.asyncio, "sleep", boom)
    asyncio.run(os_programs.ensure_programs_loop(boom))


def test_only_the_appliance_running_with_install_rights_installs(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(os_programs.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(os_programs.shutil, "which", lambda *_a, **_k: "/usr/bin/apt-get")
    monkeypatch.setattr(os_programs, "_deployment", lambda: "android_appliance")
    assert os_programs.can_install_programs()
    monkeypatch.setattr(os_programs, "_deployment", lambda: "linux_package")
    assert not os_programs.can_install_programs()
    monkeypatch.setattr(os_programs, "_deployment", lambda: "android_appliance")
    monkeypatch.setattr(os_programs.os, "geteuid", lambda: 1000, raising=False)
    assert not os_programs.can_install_programs()


class _FakeHost:
    """Records the steps run; ``ssh`` appears once an install succeeds."""

    def __init__(self, fail_plain: bool, fail_root: bool = False):
        self.fail_plain = fail_plain
        self.fail_root = fail_root
        self.steps: list[list[str]] = []
        self.has_ssh = False

    async def run(self, argv):
        self.steps.append(argv)
        if argv[0] == "apt-get" and "install" in argv:
            as_root = "APT::Sandbox::User=root" in argv
            failed = self.fail_root if as_root else self.fail_plain
            if not failed:
                self.has_ssh = True
            return (not failed), "E: failed" if failed else ""
        return True, ""

    def which(self, cmd, *_a, **_k):
        return "/usr/bin/ssh" if (cmd == "ssh" and self.has_ssh) else None


def _patch_host(monkeypatch, host: _FakeHost, tmp_path):
    monkeypatch.setattr(os_programs, "_run", host.run)
    monkeypatch.setattr(os_programs.shutil, "which", host.which)
    monkeypatch.setattr(os_programs.shutil, "rmtree", lambda *_a, **_k: None)
    monkeypatch.setattr(os_programs.os, "makedirs", lambda *_a, **_k: None)


def test_installs_the_client_and_repairs_an_interrupted_install_first(monkeypatch, tmp_path):
    host = _FakeHost(fail_plain=False)
    _patch_host(monkeypatch, host, tmp_path)
    assert asyncio.run(os_programs.install_missing_programs()) is True
    assert host.steps[0] == ["dpkg", "--configure", "-a"]
    install = next(s for s in host.steps if "install" in s)
    assert SSH_CLIENT.apt in install and "--no-install-recommends" in install
    assert not any("APT::Sandbox::User=root" in s for s in host.steps)


def test_a_refused_download_is_retried_as_root(monkeypatch, tmp_path):
    host = _FakeHost(fail_plain=True)
    _patch_host(monkeypatch, host, tmp_path)
    assert asyncio.run(os_programs.install_missing_programs()) is True
    assert any("APT::Sandbox::User=root" in s for s in host.steps)


def test_no_internet_reports_false_and_keeps_the_system_unchanged(monkeypatch, tmp_path):
    host = _FakeHost(fail_plain=True, fail_root=True)
    _patch_host(monkeypatch, host, tmp_path)
    assert asyncio.run(os_programs.install_missing_programs()) is False


def test_devices_waiting_on_the_client_reconnect_after_the_install(monkeypatch):
    calls: list[str] = []
    host = _FakeHost(fail_plain=False)
    monkeypatch.setattr(os_programs, "can_install_programs", lambda: True)
    monkeypatch.setattr(os_programs, "missing_programs", lambda: [] if host.has_ssh else [SSH_CLIENT])

    async def no_sleep(_s):
        return None

    async def install():
        host.has_ssh = True
        return True

    async def on_installed():
        calls.append("reconnect")

    monkeypatch.setattr(os_programs.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(os_programs, "install_missing_programs", install)
    asyncio.run(os_programs.ensure_programs_loop(on_installed))
    assert calls == ["reconnect"]


def test_engine_reconnects_only_devices_waiting_on_a_missing_client():
    from openavc.core.engine import Engine

    reconnected: list[str] = []

    class _Devices:
        def list_devices(self):
            return [{"id": "a"}, {"id": "b"}, {"id": "c"}]

        async def reconnect_device(self, device_id):
            reconnected.append(device_id)

    class _State:
        values = {
            "device.a.offline_reason": "client_missing",
            "device.b.offline_reason": "auth_failed",
        }

        def get(self, key, default=None):
            return self.values.get(key, default)

    fake = type("E", (), {"devices": _Devices(), "state": _State()})()
    asyncio.run(Engine._retry_client_missing_devices(fake))
    assert reconnected == ["a"]
