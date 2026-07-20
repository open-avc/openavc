"""Tests for the SSH transport (server/transport/ssh.py).

These exercise the platform transport's pure logic — the ``ssh`` argument
vector, the askpass/env wiring for password auth, and host-key policy mapping —
without spawning a real ``ssh`` process, plus the not-connected contract. A
live round-trip test is provided but skipped unless OPENAVC_SSH_TEST_HOST is
set. Uses an invented host/user; no real product or network is named.
"""

from __future__ import annotations

import os

import pytest

from server.transport.ssh import SSHTransport, _write_askpass_helper

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


def test_resolve_binary_missing(monkeypatch):
    monkeypatch.setattr("server.transport.ssh.shutil.which", lambda _x: None)
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
    with caplog.at_level(logging.ERROR, logger="server.transport.ssh"):
        t._deliver(b"payload")
        assert t._bg_tasks, "async handler task must be strongly referenced"
        for _ in range(10):
            await asyncio.sleep(0)
    assert any("on_data task" in r.message for r in caplog.records)
    assert not t._bg_tasks  # self-pruned once settled
