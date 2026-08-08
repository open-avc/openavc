"""Failure-path fidelity for the auto-simulator's telnet_login handshake.

The simulator mirrors the driver-side `auth:` handshake so telnet-login
drivers can be exercised end-to-end without hardware. These tests cover the
paths beyond happy-path prompting:

- the handshake is skipped when the *driver* would skip it (skip_if_empty +
  blank username in the device config) so the sim doesn't eat the first two
  real commands as credentials;
- the declared line_ending is honored when reading credential lines (an
  ending like "\r" never produces the "\n" readline() waits for);
- the designated bad credential ("invalid" as username or password) plays
  out the rejection: failure_pattern when declared, otherwise a username
  re-prompt — the signals the driver classifies as auth_failed.

Platform tests: invented device ("acme_secure"), synthetic payloads.
"""

import asyncio

from openavc.simulator.yaml_auto import YAMLAutoSimulator


def _driver_def(auth_overrides: dict | None = None, **auth_removals) -> dict:
    auth = {
        "type": "telnet_login",
        "username_prompt": "login: ",
        "password_prompt": "Password: ",
        "success_pattern": "Welcome ",
        "failure_pattern": "Login incorrect",
        "line_ending": "\r\n",
        "timeout_seconds": 2,
    }
    auth.update(auth_overrides or {})
    for key, remove in auth_removals.items():
        if remove:
            auth.pop(key, None)
    return {
        "id": "acme_secure",
        "name": "Acme Secure Device",
        "transport": "tcp",
        "state_variables": {},
        "commands": {},
        "responses": [],
        "auth": auth,
    }


class _FakeWriter:
    def __init__(self) -> None:
        self.written = bytearray()

    def write(self, data: bytes) -> None:
        self.written.extend(data)

    async def drain(self) -> None:
        pass


def _make(driver_def: dict, config: dict | None = None):
    sim = YAMLAutoSimulator("dev1", config, driver_def=driver_def)
    reader = asyncio.StreamReader()
    writer = _FakeWriter()
    return sim, reader, writer


async def test_skips_when_driver_would_skip():
    """skip_if_empty (default true) + blank username in the device config
    means the driver never authenticates — the sim must not prompt either,
    or it would swallow the first two real commands as credentials."""
    sim, reader, writer = _make(_driver_def(), config={"username": ""})
    reader.feed_data(b"REAL COMMAND 1\r\n")

    ok = await sim.authenticate_client(reader, writer, "c1")

    assert ok is True
    assert writer.written == b""  # no prompts were sent
    # The pending real command was NOT consumed as a credential.
    assert await asyncio.wait_for(reader.readline(), 1) == b"REAL COMMAND 1\r\n"


async def test_skip_if_empty_false_still_prompts():
    sim, reader, writer = _make(
        _driver_def({"skip_if_empty": False}), config={"username": ""}
    )
    reader.feed_data(b"\r\nsecret\r\n")

    ok = await sim.authenticate_client(reader, writer, "c1")

    assert ok is True
    assert b"login: " in writer.written
    assert b"Password: " in writer.written


async def test_good_credentials_reach_success_banner():
    sim, reader, writer = _make(_driver_def(), config={"username": "admin"})
    reader.feed_data(b"admin\r\nsecret\r\n")

    ok = await sim.authenticate_client(reader, writer, "c1")

    assert ok is True
    assert writer.written.endswith(b"Welcome \r\n")


async def test_invalid_credential_emits_failure_pattern():
    sim, reader, writer = _make(_driver_def(), config={"username": "admin"})
    reader.feed_data(b"admin\r\ninvalid\r\n")

    ok = await sim.authenticate_client(reader, writer, "c1")

    assert ok is False
    assert writer.written.endswith(b"Login incorrect\r\n")
    assert b"Welcome " not in writer.written


async def test_invalid_username_also_rejects():
    sim, reader, writer = _make(_driver_def(), config={"username": "admin"})
    reader.feed_data(b"invalid\r\nsecret\r\n")

    ok = await sim.authenticate_client(reader, writer, "c1")

    assert ok is False
    assert writer.written.endswith(b"Login incorrect\r\n")


async def test_invalid_credential_reprompts_without_failure_pattern():
    """With no declared failure banner the sim re-prompts for the username —
    the post-password re-prompt is the rejection signal the driver's
    telnet_login split recognizes."""
    sim, reader, writer = _make(
        _driver_def(failure_pattern=True), config={"username": "admin"}
    )
    reader.feed_data(b"admin\r\ninvalid\r\n")

    ok = await sim.authenticate_client(reader, writer, "c1")

    assert ok is False
    # A second username prompt was emitted after the password stage.
    assert writer.written.count(b"login: ") == 2


async def test_cr_only_line_ending_is_honored():
    """readline() waits for a \\n that never arrives when the driver's
    declared ending is "\\r" — the sim must read to the actual ending."""
    sim, reader, writer = _make(
        _driver_def({"line_ending": "\r", "success_pattern": "OK"}),
        config={"username": "admin"},
    )
    reader.feed_data(b"admin\rsecret\r")

    # Well under the 2s auth timeout: a hang here means readline() regressed.
    ok = await asyncio.wait_for(
        sim.authenticate_client(reader, writer, "c1"), timeout=1.0
    )

    assert ok is True
    assert writer.written.endswith(b"OK\r")


async def test_cr_line_ending_invalid_credential():
    sim, reader, writer = _make(
        _driver_def({"line_ending": "\r"}), config={"username": "admin"}
    )
    reader.feed_data(b"admin\rinvalid\r")

    ok = await asyncio.wait_for(
        sim.authenticate_client(reader, writer, "c1"), timeout=1.0
    )

    assert ok is False
    assert writer.written.endswith(b"Login incorrect\r")


async def test_eof_mid_handshake_fails_cleanly():
    sim, reader, writer = _make(_driver_def(), config={"username": "admin"})
    reader.feed_data(b"admin\r\n")
    reader.feed_eof()

    # readline() on an EOF'd reader returns b"" immediately; the credential
    # decodes to "" and auth completes without the success path hanging.
    ok = await asyncio.wait_for(
        sim.authenticate_client(reader, writer, "c1"), timeout=1.0
    )

    # An empty password isn't the designated bad credential — the sim admits
    # it (credential *validation* is not the sim's job); the point is no
    # hang and no crash on a dropped client.
    assert ok is True
