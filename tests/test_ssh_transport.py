"""Tests for the SSH transport (openavc/transport/ssh.py).

These exercise the platform transport's pure logic — the ``ssh`` argument
vector, the askpass/env wiring for password auth, and host-key policy mapping —
without spawning a real ``ssh`` process, plus the not-connected contract. A
live round-trip test is provided but skipped unless OPENAVC_SSH_TEST_HOST is
set. Uses an invented host/user; no real product or network is named.
"""

from __future__ import annotations

import os

import pytest

from openavc.core.connection_fault import INVALID_CONFIG, ConnectionFaultError
from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.transport.ssh import (
    LEGACY_SSH_ALGORITHMS,
    SSHTransport,
    _write_askpass_helper,
)

HOST = "acme-switch.invalid"
USER = "avc"


def _noop(*_a, **_k):
    return None


def _make(**kwargs) -> SSHTransport:
    kwargs.setdefault("ssh_binary", "/usr/bin/ssh")  # avoid PATH lookup in tests
    return SSHTransport(HOST, 22, USER, _noop, _noop, **kwargs)


def test_argv_key_auth_uses_publickey_only():
    argv = _make(auth_method="key", key_path="/keys/id_ed25519").build_argv()
    assert argv[0] == "/usr/bin/ssh"
    assert argv[-1] == f"{USER}@{HOST}"
    assert "-tt" in argv
    assert "PreferredAuthentications=publickey" in argv
    assert "PasswordAuthentication=no" in argv
    assert "BatchMode=yes" in argv
    # The identity file and IdentitiesOnly are present for key auth.
    assert "-i" in argv and "/keys/id_ed25519" in argv
    assert "IdentitiesOnly=yes" in argv


def test_argv_password_auth_disables_pubkey():
    argv = _make(auth_method="password", password="s3cret").build_argv()
    assert "PubkeyAuthentication=no" in argv
    assert "PreferredAuthentications=password,keyboard-interactive" in argv
    assert "NumberOfPasswordPrompts=1" in argv
    assert "BatchMode=no" in argv
    # No identity file when using a password.
    assert "-i" not in argv


def test_argv_port_and_target():
    t = SSHTransport(HOST, 2222, USER, _noop, _noop, ssh_binary="ssh")
    argv = t.build_argv()
    assert "-p" in argv and "2222" in argv
    assert argv[-1] == f"{USER}@{HOST}"


@pytest.mark.parametrize(
    "policy,expect_strict,expect_devnull",
    [
        ("accept-new", "StrictHostKeyChecking=accept-new", False),
        ("strict", "StrictHostKeyChecking=yes", False),
        ("off", "StrictHostKeyChecking=no", True),
    ],
)
def test_host_key_policy(policy, expect_strict, expect_devnull):
    argv = _make(host_key_policy=policy, known_hosts_path="/data/known_hosts").build_argv()
    assert expect_strict in argv
    kh = argv[argv.index("UserKnownHostsFile=" + (os.devnull if expect_devnull
                                                  else "/data/known_hosts"))]
    assert kh.endswith(os.devnull if expect_devnull else "/data/known_hosts")


def test_extra_ssh_options_are_appended():
    argv = _make(extra_ssh_options=["Ciphers=aes256-ctr"]).build_argv()
    assert "Ciphers=aes256-ctr" in argv


def test_legacy_algorithms_are_offered_by_default():
    """Old gear that offers only hmac-sha1 has to be reachable.

    A real AC-MXNET-SW8P offers exactly one MAC, and OpenSSH dropped it in 8.8,
    so without this the handshake fails before authentication is even tried.
    """
    argv = _make().build_argv()
    for option in LEGACY_SSH_ALGORITHMS:
        assert option in argv


def test_legacy_algorithms_only_add_never_replace():
    """Every compat entry uses '+', so a modern device still picks its best.

    Dropping the '+' from any of these would silently PIN the connection to the
    weak algorithm instead of adding it as a fallback, which is the one way this
    feature could make a good connection worse.
    """
    for option in LEGACY_SSH_ALGORITHMS:
        keyword, _, value = option.partition("=")
        assert value.startswith("+"), f"{keyword} must add, not replace"


def test_legacy_algorithms_can_be_turned_off():
    argv = _make(legacy_algorithms=False).build_argv()
    for option in LEGACY_SSH_ALGORITHMS:
        assert option not in argv


def test_device_options_come_before_the_compat_list():
    """ssh takes the FIRST value for a keyword, so a per-device override wins.

    If the compat list were emitted first, a device that needed a different MAC
    set could not get one -- the escape hatch would be inert.
    """
    argv = _make(extra_ssh_options=["MACs=hmac-sha2-512"]).build_argv()
    mine = argv.index("MACs=hmac-sha2-512")
    compat = argv.index("MACs=+hmac-sha1")
    assert mine < compat


@pytest.mark.parametrize("blank", ["", "   ", None])
def test_blank_username_is_refused_as_config_not_network(blank):
    """A blank username makes the destination a bare ``@host``, which OpenSSH
    answers with its usage message and a non-zero exit -- so the transport sees
    a process that died without connecting and the device reads as unreachable.
    Refuse before spawning, typed so the card names the field."""
    t = SSHTransport(HOST, 22, blank, _noop, _noop, ssh_binary="/usr/bin/ssh")
    with pytest.raises(ConnectionFaultError, match="needs a username") as exc:
        t.build_argv()
    assert exc.value.fault_code == INVALID_CONFIG


@pytest.mark.parametrize("blank", ["", "   ", None])
def test_blank_host_is_refused_as_config_not_network(blank):
    """The quieter half: ``ssh admin@`` does not print usage, it reports
    ``Connection refused`` -- a network verdict about an address nobody
    entered, which sends the integrator to check cabling."""
    t = SSHTransport(blank, 22, USER, _noop, _noop, ssh_binary="/usr/bin/ssh")
    with pytest.raises(ConnectionFaultError, match="needs an address") as exc:
        t.build_argv()
    assert exc.value.fault_code == INVALID_CONFIG


def test_destination_is_checked_before_the_ssh_binary(monkeypatch):
    """Order matters for the message: a box with no ssh installed AND a blank
    username should be told about the field it can fix, not sent hunting for
    OpenSSH.

    ``which`` is patched to None deliberately -- without it this test passes on
    any machine that HAS ssh no matter which check runs first, so it would
    assert nothing about ordering on every developer box and most CI runners.
    """
    monkeypatch.setattr("openavc.transport.ssh.shutil.which", lambda _x: None)
    t = SSHTransport(HOST, 22, "", _noop, _noop, ssh_binary=None)
    with pytest.raises(ConnectionFaultError, match="needs a username"):
        t.build_argv()


def test_a_username_that_is_merely_unusual_still_builds():
    """The guard rejects blank, not odd. Values that are real are passed
    through exactly as typed -- no trimming, no rewriting."""
    argv = SSHTransport(HOST, 22, "admin.svc-01", _noop, _noop,
                        ssh_binary="/usr/bin/ssh").build_argv()
    assert argv[-1] == f"admin.svc-01@{HOST}"


def test_resolve_binary_missing(monkeypatch):
    monkeypatch.setattr("openavc.transport.ssh.shutil.which", lambda _x: None)
    t = SSHTransport(HOST, 22, USER, _noop, _noop)  # no ssh_binary -> PATH lookup
    with pytest.raises(ConnectionError, match="OpenSSH client"):
        t.build_argv()


def test_env_password_wires_askpass_and_password():
    t = _make(auth_method="password", password="hunter2")
    try:
        env = t.build_env()
        assert env["SSH_ASKPASS_REQUIRE"] == "force"
        assert env["OPENAVC_SSH_PASSWORD"] == "hunter2"
        askpass = env["SSH_ASKPASS"]
        assert os.path.exists(askpass)
        # The helper echoes the env var, not the literal password.
        with open(askpass) as f:
            body = f.read()
        assert "OPENAVC_SSH_PASSWORD" in body
        assert "hunter2" not in body
    finally:
        t._cleanup_askpass()
    assert not os.path.exists(askpass)


def test_env_key_auth_has_no_askpass():
    env = _make(auth_method="key").build_env()
    assert "SSH_ASKPASS" not in env
    assert "OPENAVC_SSH_PASSWORD" not in env


def test_askpass_helper_is_self_cleaning_content():
    path = _write_askpass_helper()
    try:
        assert os.path.exists(path)
        with open(path) as f:
            body = f.read()
        assert "OPENAVC_SSH_PASSWORD" in body
    finally:
        os.remove(path)


@pytest.mark.asyncio
async def test_send_when_not_connected_raises():
    t = _make()
    with pytest.raises(ConnectionError):
        await t.send(b"show version\n")


@pytest.mark.asyncio
async def test_verify_without_process_is_false():
    t = _make()
    assert await t.verify(timeout=0.1) is False


def test_connected_false_before_spawn():
    assert _make().connected is False


@pytest.mark.skipif(
    not os.environ.get("OPENAVC_SSH_TEST_HOST"),
    reason="set OPENAVC_SSH_TEST_HOST=user@host[:port] for a live SSH round-trip",
)
@pytest.mark.asyncio
async def test_live_roundtrip():
    target = os.environ["OPENAVC_SSH_TEST_HOST"]
    user, _, hostport = target.partition("@")
    host, _, port = hostport.partition(":")
    chunks: list[bytes] = []
    t = await SSHTransport.create(
        host, int(port or 22), user, lambda d: chunks.append(d), _noop,
        auth_method=os.environ.get("OPENAVC_SSH_TEST_AUTH", "key"),
        password=os.environ.get("OPENAVC_SSH_TEST_PASSWORD"),
    )
    try:
        import asyncio
        await asyncio.sleep(2.0)
        assert t.connected
    finally:
        await t.close()


@pytest.mark.asyncio
async def test_async_on_data_exception_is_logged(caplog):
    """An async on_data callback that raises is supervised like the other
    transports — held with a strong ref and logged, never silently dropped."""
    import asyncio
    import logging

    async def failing_handler(data: bytes):
        raise RuntimeError("handler boom")

    t = SSHTransport(
        HOST, 22, USER, failing_handler, _noop, ssh_binary="/usr/bin/ssh"
    )
    with caplog.at_level(logging.ERROR, logger="openavc.transport.ssh"):
        t._deliver(b"payload")
        assert t._bg_tasks, "async handler task must be strongly referenced"
        for _ in range(10):
            await asyncio.sleep(0)
    assert any("on_data task" in r.message for r in caplog.records)
    assert not t._bg_tasks  # self-pruned once settled


# --- device config -> transport plumbing -------------------------------------
#
# The option list and the legacy flag are only worth anything if a DEVICE can
# reach them. `extra_ssh_options` shipped as a constructor parameter that
# nothing ever passed, which is indistinguishable from a parameter that does
# not work, so these cover the wiring rather than the transport.

@pytest.mark.parametrize("raw,expect", [
    (None, []),
    ("", []),
    ([], []),
    (["MACs=+hmac-md5"], ["MACs=+hmac-md5"]),
    (("A=1", " B=2 "), ["A=1", "B=2"]),
    ("A=1", ["A=1"]),
    ("A=1,B=2", ["A=1", "B=2"]),
    ("A=1\nB=2", ["A=1", "B=2"]),
    ("  A=1 , , B=2  ", ["A=1", "B=2"]),
])
def test_ssh_option_list_takes_a_list_or_a_line_of_text(raw, expect):
    from openavc.drivers.base import _ssh_option_list
    assert _ssh_option_list(raw) == expect


def test_device_config_reaches_the_ssh_transport(monkeypatch):
    """A device's extra options and legacy flag must arrive at SSHTransport."""
    import asyncio

    from openavc.drivers import base as base_mod

    seen: dict = {}

    async def _fake_create(**kwargs):
        seen.update(kwargs)
        return object()

    monkeypatch.setattr(
        "openavc.transport.ssh.SSHTransport.create", _fake_create)

    class _Driver(base_mod.BaseDriver):
        DRIVER_INFO = {"id": "acme_cli", "name": "Acme CLI", "transport": "ssh"}

        async def get_state(self):
            return {}

        async def send_command(self, command, params=None):
            return None

    drv = _Driver("acme_1", {
        "host": "acme-switch.invalid",
        "port": 22,
        "username": "avc",
        "transport": "ssh",
        "extra_ssh_options": "Ciphers=aes256-ctr",
        "ssh_legacy_algorithms": False,
    }, StateStore(), EventBus())
    asyncio.run(drv.connect())

    assert seen["extra_ssh_options"] == ["Ciphers=aes256-ctr"]
    assert seen["legacy_algorithms"] is False


def test_legacy_algorithms_default_on_when_the_device_says_nothing(monkeypatch):
    import asyncio

    from openavc.drivers import base as base_mod

    seen: dict = {}

    async def _fake_create(**kwargs):
        seen.update(kwargs)
        return object()

    monkeypatch.setattr(
        "openavc.transport.ssh.SSHTransport.create", _fake_create)

    class _Driver(base_mod.BaseDriver):
        DRIVER_INFO = {"id": "acme_cli", "name": "Acme CLI", "transport": "ssh"}

        async def get_state(self):
            return {}

        async def send_command(self, command, params=None):
            return None

    drv = _Driver("acme_2", {
        "host": "acme-switch.invalid", "port": 22, "username": "avc",
        "transport": "ssh",
    }, StateStore(), EventBus())
    asyncio.run(drv.connect())

    assert seen["legacy_algorithms"] is True
    assert seen["extra_ssh_options"] == []
