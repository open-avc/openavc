"""
Regression tests for the cloud-agent hardening pass (audit findings
H-017..H-021, M-028..M-032, L-022, L-023).

These exercise CloudAgent in isolation (built via ``__new__`` with only the
attributes each path touches) so the trust-boundary and lifecycle behaviors are
pinned without standing up a real WebSocket connection.
"""

import asyncio

import pytest
from websockets.exceptions import ConnectionClosed

from server.cloud.agent import (
    CloudAgent,
    DEFAULT_CAPABILITIES,
    _CAPABILITY_GATED,
    MAX_CONSECUTIVE_SIG_FAILURES,
    THROTTLE_MAX_SECONDS,
)
from server.cloud.session import Session, SessionInvalid
from server.cloud.sequencer import Sequencer
from server.cloud.crypto import generate_system_key, derive_signing_key


# --- Test doubles ---------------------------------------------------------


class _StateRecorder:
    """Minimal StateStore stand-in recording set()/get()."""

    def __init__(self):
        self.values: dict = {}

    def set(self, key, value, source=None):
        self.values[key] = value

    def get(self, key, default=None):
        return self.values.get(key, default)


class _RecordingMgr:
    """Stands in for UpdateManager, recording apply_update_policy calls."""

    def __init__(self):
        self.calls: list = []

    async def apply_update_policy(self, policy):
        self.calls.append(policy)


class _CmdHandler:
    def __init__(self, mgr=None):
        self._update_manager = mgr


def _bare_agent() -> CloudAgent:
    """An agent with exactly the attributes stop() and dispatch touch."""
    agent = CloudAgent.__new__(CloudAgent)
    agent._stopping = False
    agent._running = True
    agent._connected = True
    agent._connection_loop_task = None
    agent._recv_task = None
    agent._heartbeat_task = None
    agent._watchdog_task = None
    agent._throttles = {}
    agent._throttle_tasks = {}
    agent._throttle_deadlines = {}
    agent._ai_tool_handler = None
    agent._tunnel_handler = None
    agent._state_relay = None
    agent._alert_monitor = None
    agent._cert_manager = None
    agent._ws = None
    agent._session = None
    agent._sig_failure_count = 0
    return agent


# === H-019 — TLS/wss enforcement =========================================


def test_endpoint_encryption_classification():
    f = CloudAgent._endpoint_is_encrypted
    # wss is always allowed
    assert f("wss://cloud.openavc.com/agent/v1")
    # ws is allowed only for loopback (local dev)
    assert f("ws://localhost:8000/agent/v1")
    assert f("ws://127.0.0.1:8000")
    assert f("ws://[::1]:8000")
    # cleartext to any non-loopback host is rejected
    assert not f("ws://cloud.openavc.com/agent/v1")
    assert not f("ws://192.168.1.5:8080")
    # wrong/absent scheme
    assert not f("http://cloud.openavc.com")
    assert not f("")
    assert not f("cloud.openavc.com/agent/v1")


@pytest.mark.asyncio
async def test_connect_refuses_cleartext_endpoint():
    """H-019: connect() never starts the loop over a cleartext non-loopback endpoint."""
    agent = CloudAgent.__new__(CloudAgent)
    agent._endpoint = "ws://cloud.openavc.com/agent/v1"
    agent._system_key = b"key"
    agent.state = _StateRecorder()
    agent._running = False
    agent._stopping = False
    agent._connection_loop_task = None

    await agent.connect()

    assert agent._connection_loop_task is None  # loop never started
    assert agent._running is False
    assert agent.state.values.get("system.cloud.status") == "insecure_endpoint"


@pytest.mark.asyncio
async def test_connect_accepts_wss_endpoint():
    """H-019: a wss endpoint starts the connection loop."""
    agent = CloudAgent.__new__(CloudAgent)
    agent._endpoint = "wss://cloud.openavc.com/agent/v1"
    agent._system_key = b"key"
    agent.state = _StateRecorder()
    agent._running = False
    agent._stopping = False
    agent._connection_loop_task = None

    started = asyncio.Event()

    async def _noop_loop():
        started.set()
        await asyncio.sleep(3600)

    agent._connection_loop = _noop_loop  # avoid real network
    await agent.connect()

    assert agent._connection_loop_task is not None
    await asyncio.wait_for(started.wait(), 1)

    agent._connection_loop_task.cancel()
    try:
        await agent._connection_loop_task
    except asyncio.CancelledError:
        pass


# === H-018 / M-029 / M-033 — shutdown cancels everything =================


@pytest.mark.asyncio
async def test_stop_cancels_connection_loop_task():
    """H-018: stop() cancels the orphaned connection loop so it can't resurrect."""
    agent = _bare_agent()

    async def _forever():
        await asyncio.sleep(3600)

    loop_task = asyncio.create_task(_forever())
    agent._connection_loop_task = loop_task

    await agent.stop()

    assert loop_task.cancelled() or loop_task.done()
    assert agent._connection_loop_task is None


@pytest.mark.asyncio
async def test_stop_cancels_pending_throttle_tasks():
    """M-029: pending throttle-release tasks are cancelled at shutdown."""
    agent = _bare_agent()

    async def _forever():
        await asyncio.sleep(3600)

    t = asyncio.create_task(_forever())
    agent._throttle_tasks["state_batch"] = t
    agent._throttle_deadlines["state_batch"] = 999.0

    await agent.stop()

    assert t.cancelled() or t.done()
    assert agent._throttle_tasks == {}
    assert agent._throttle_deadlines == {}


@pytest.mark.asyncio
async def test_stop_shuts_down_ai_tool_handler():
    """M-033: in-flight AI tool tasks are cancelled via handler.shutdown()."""
    agent = _bare_agent()
    called: list = []

    class _AI:
        async def shutdown(self):
            called.append(True)

    agent._ai_tool_handler = _AI()
    await agent.stop()
    assert called == [True]


# === H-020 — remote-control messages gated on remote_access ==============


def test_remote_control_messages_gated_on_remote_access():
    for mt in ("command", "config_push", "restart"):
        assert _CAPABILITY_GATED.get(mt) == "remote_access"
    # vocabulary stays in sync with what the cloud advertises
    assert "remote_access" in DEFAULT_CAPABILITIES


# === H-021 — non-dict config can't crash the agent =======================


def test_apply_config_ignores_non_dict():
    agent = CloudAgent.__new__(CloudAgent)
    agent._config = {"heartbeat_interval": 30, "features": {}}
    for bad in ("string", ["list"], None, 42):
        agent._apply_config(bad)  # must not raise
    assert agent._config["heartbeat_interval"] == 30


def test_apply_config_merges_valid_dict():
    agent = CloudAgent.__new__(CloudAgent)
    agent._config = {"heartbeat_interval": 30, "features": {"a": True}}
    agent._apply_config({"heartbeat_interval": 60, "features": {"b": False}})
    assert agent._config["heartbeat_interval"] == 60
    assert agent._config["features"] == {"a": True, "b": False}


# === H-017 / M-028 — update policy reaches the UpdateManager ==============


@pytest.mark.asyncio
async def test_config_update_reapplies_update_policy():
    """H-017: a config_update carrying update_policy is pushed to the manager."""
    agent = CloudAgent.__new__(CloudAgent)
    agent._config = {"features": {}}
    mgr = _RecordingMgr()
    agent._command_handler = _CmdHandler(mgr)

    await agent._handle_config_update(
        {"payload": {"update_policy": {"policy": "manual"}}}
    )
    assert mgr.calls == [{"policy": "manual"}]


@pytest.mark.asyncio
async def test_config_update_without_policy_does_not_touch_manager():
    """H-017: a partial config_update without update_policy leaves the policy alone."""
    agent = CloudAgent.__new__(CloudAgent)
    agent._config = {"features": {}}
    mgr = _RecordingMgr()
    agent._command_handler = _CmdHandler(mgr)

    await agent._handle_config_update({"payload": {"heartbeat_interval": 60}})
    assert mgr.calls == []


@pytest.mark.asyncio
async def test_session_start_absent_policy_resets_to_manual():
    """M-028: session_start with no update_policy applies the manual default."""
    agent = CloudAgent.__new__(CloudAgent)
    mgr = _RecordingMgr()
    agent._command_handler = _CmdHandler(mgr)

    await agent._sync_update_policy({})  # absent => manual ({} tears down auto loop)
    assert mgr.calls == [{}]

    await agent._sync_update_policy({"update_policy": {"policy": "auto"}})
    assert mgr.calls[-1] == {"policy": "auto"}


@pytest.mark.asyncio
async def test_sync_update_policy_no_manager_is_noop():
    agent = CloudAgent.__new__(CloudAgent)
    agent._command_handler = _CmdHandler(mgr=None)
    await agent._sync_update_policy({"update_policy": {"policy": "auto"}})  # no raise
    # also tolerate a handler with no _update_manager attribute at all
    agent._command_handler = object()
    await agent._sync_update_policy({})


# === M-032 — capability payloads are validated ===========================


def test_normalize_capabilities():
    f = CloudAgent._normalize_capabilities
    assert f(["a", "b"]) == ["a", "b"]
    assert f(["a", 1, None, "b", 2.0]) == ["a", "b"]
    assert f([]) == []
    assert f("nope") is None
    assert f(None) is None
    assert f({"a": 1}) is None


def test_capabilities_update_rejects_malformed_payload():
    agent = CloudAgent.__new__(CloudAgent)
    agent._enabled_capabilities = ["monitoring", "tunnel"]
    agent._handle_capabilities_update(
        {"payload": {"enabled_capabilities": "not-a-list"}}
    )
    # unchanged — a malformed payload must not wipe capabilities
    assert agent._enabled_capabilities == ["monitoring", "tunnel"]


def test_capabilities_update_filters_non_strings():
    agent = CloudAgent.__new__(CloudAgent)
    agent._enabled_capabilities = ["monitoring"]
    agent._handle_capabilities_update(
        {"payload": {"enabled_capabilities": ["remote_access", 5, "tunnel", None]}}
    )
    assert agent._enabled_capabilities == ["remote_access", "tunnel"]


# === M-031 — persistent signature failure tears down the session =========


class _FailVerifySession:
    def verify_incoming(self, msg):
        return False


class _OkVerifySession:
    def verify_incoming(self, msg):
        return True


@pytest.mark.asyncio
async def test_persistent_sig_failure_tears_down_session():
    agent = CloudAgent.__new__(CloudAgent)
    agent._session = _FailVerifySession()
    agent._sig_failure_count = 0
    agent._enabled_capabilities = []

    # The first MAX-1 failures are silently dropped (returns, no raise).
    for _ in range(MAX_CONSECUTIVE_SIG_FAILURES - 1):
        await agent._handle_message({"type": "command", "payload": {}})
    assert agent._sig_failure_count == MAX_CONSECUTIVE_SIG_FAILURES - 1

    # The threshold failure tears down the session for a fresh handshake.
    with pytest.raises(SessionInvalid):
        await agent._handle_message({"type": "command", "payload": {}})


@pytest.mark.asyncio
async def test_sig_failure_count_resets_on_success():
    agent = CloudAgent.__new__(CloudAgent)
    agent._sequencer = Sequencer()
    agent._enabled_capabilities = []
    agent._sig_failure_count = 0

    agent._session = _FailVerifySession()
    await agent._handle_message({"type": "ack", "payload": {}})  # fails verify
    assert agent._sig_failure_count == 1

    agent._session = _OkVerifySession()
    await agent._handle_message({"type": "ack", "payload": {"last_seq": 0}})  # verifies
    assert agent._sig_failure_count == 0


# === M-030 — steady-state teardown cancels the surviving sibling =========


@pytest.mark.asyncio
async def test_await_steady_state_cancels_sibling_on_error():
    async def _raiser():
        raise ConnectionClosed(None, None)

    async def _sleeper():
        await asyncio.sleep(3600)

    recv = asyncio.create_task(_sleeper())
    hb = asyncio.create_task(_raiser())

    with pytest.raises(ConnectionClosed):
        await CloudAgent._await_steady_state(recv, hb)
    assert recv.cancelled()


@pytest.mark.asyncio
async def test_await_steady_state_clean_return_no_raise():
    async def _returns():
        return None

    async def _sleeper():
        await asyncio.sleep(3600)

    recv = asyncio.create_task(_returns())
    hb = asyncio.create_task(_sleeper())

    await CloudAgent._await_steady_state(recv, hb)  # clean disconnect, no raise
    assert hb.cancelled()


# === L-022 / M-029 — overlapping throttles release on the longest deadline


@pytest.mark.asyncio
async def test_overlapping_throttle_uses_longest_deadline():
    agent = CloudAgent.__new__(CloudAgent)
    agent._throttles = {}
    agent._throttle_tasks = {}
    agent._throttle_deadlines = {}

    # Long throttle first.
    agent._handle_throttle({"payload": {"limit": "state_batch", "retry_after_seconds": 10}})
    first_task = agent._throttle_tasks["state_batch"]
    deadline1 = agent._throttle_deadlines["state_batch"]

    # A second, shorter throttle must NOT shorten the deadline, and replaces the
    # prior timer task (so it can't fire early).
    agent._handle_throttle({"payload": {"limit": "state_batch", "retry_after_seconds": 0.05}})
    assert agent._throttle_deadlines["state_batch"] == deadline1
    assert agent._throttle_tasks["state_batch"] is not first_task

    # The short interval passes but the type stays throttled (10s deadline holds)
    # and the replaced timer has actually been cancelled.
    await asyncio.sleep(0.1)
    assert not agent._throttles["state_batch"].is_set()
    assert first_task.cancelled()

    final_task = agent._throttle_tasks["state_batch"]
    final_task.cancel()
    try:
        await final_task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_throttle_retry_after_coerced_and_clamped():
    agent = CloudAgent.__new__(CloudAgent)
    agent._throttles = {}
    agent._throttle_tasks = {}
    agent._throttle_deadlines = {}

    # Non-numeric retry_after must not crash; defaults to 30s.
    agent._handle_throttle({"payload": {"limit": "x", "retry_after_seconds": "nope"}})
    assert "x" in agent._throttle_deadlines

    # An absurd retry_after is clamped to THROTTLE_MAX_SECONDS.
    loop_time = asyncio.get_event_loop().time()
    agent._handle_throttle({"payload": {"limit": "y", "retry_after_seconds": 10 ** 9}})
    assert agent._throttle_deadlines["y"] <= loop_time + THROTTLE_MAX_SECONDS + 1

    for t in list(agent._throttle_tasks.values()):
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass


# === L-023 — replayed messages are retained until acked ==================


@pytest.mark.asyncio
async def test_replay_retains_buffer_until_acked():
    agent = CloudAgent.__new__(CloudAgent)
    seq = Sequencer()
    agent._sequencer = seq

    system_key = generate_system_key()
    signing_key = derive_signing_key(system_key, b"salt", "s1")
    agent._session = Session("s1", "tok", signing_key, "2099-01-01T00:00:00Z", system_key)

    sent: list = []

    class _WS:
        async def send(self, raw):
            sent.append(raw)

    agent._ws = _WS()

    # Two control responses buffered on the prior session.
    seq.assign_seq({"type": "command_result", "payload": {"request_id": "r1"}})
    seq.assign_seq({"type": "command_result", "payload": {"request_id": "r2"}})
    assert seq.buffer_count == 2
    seq.reset_for_new_session()  # buffer preserved for replay

    await agent._replay_buffered(1)

    # Both re-sent on the fresh session...
    assert len(sent) == 2
    # ...and crucially still buffered (retain-until-acked), so a second
    # reconnect before the ack can replay them again.
    assert seq.buffer_count == 2

    # The ack is what clears them.
    seq.handle_ack({"payload": {"last_seq": seq.next_seq - 1}})
    assert seq.buffer_count == 0
