"""Tests for the agent-side trusted-certificate manager.

Exercises the enrollment -> CSR -> install state machine against a fake
agent (no cloud, no network) with an invented label + zone. The cloud's
side of the contract is stood in by feeding cert_result payloads and
signing certs from the captured CSR.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import ec

from openavc import tls
from openavc.cloud import cert_manager as cm
from openavc.cloud.cert_manager import CertificateManager, generate_key_and_csr
from tests.helpers import make_cloud_cert_pem, sign_csr_like_cloud

LABEL = "ab12cd34ef56ab78"
ZONE = "i.certtest.invalid"


@pytest.fixture(autouse=True)
def _clean_holder():
    """Cloud state lives in a module-level holder — isolate every test."""
    tls.cloud_cert_holder().clear()
    yield
    tls.cloud_cert_holder().clear()


class FakeState:
    def __init__(self):
        self.values = {}

    def set(self, key, value, source=None):
        self.values[key] = value

    def get(self, key, default=None):
        return self.values.get(key, default)


class FakeAgent:
    def __init__(self):
        self.state = FakeState()
        self.connected = True
        self.capabilities = ["monitoring", "trusted_certs"]
        self.sent: list[tuple[str, dict]] = []

    def has_capability(self, capability):
        return capability in self.capabilities

    async def send_message(self, msg_type, payload):
        self.sent.append((msg_type, payload))


class FakeSysCfg:
    def __init__(self, data_dir, enabled=True):
        self.data_dir = data_dir
        self.saved = 0
        self._values = {("tls", "cloud_cert"): enabled}

    def get(self, section, key, default=None):
        return self._values.get((section, key), default)

    def set(self, section, key, value):
        self._values[(section, key)] = value

    def save(self):
        self.saved += 1


@pytest.fixture
def agent():
    return FakeAgent()


@pytest.fixture
def manager(agent, tmp_path):
    return CertificateManager(agent, FakeSysCfg(tmp_path))


def result_msg(payload: dict) -> dict:
    return {"type": "cert_result", "payload": payload}


async def run_happy_path(manager, agent, *, manual: bool = False) -> None:
    """Drive enrollment -> CSR -> issued through the manager."""
    started, reason = await manager.request_certificate(manual=manual)
    assert (started, reason) == (True, "")
    assert agent.sent[-1] == ("cert_request", {})

    await manager.handle_cert_result(
        result_msg({"status": "enrollment", "label": LABEL, "zone": ZONE})
    )
    msg_type, payload = agent.sent[-1]
    assert msg_type == "cert_request"
    chain_pem = sign_csr_like_cloud(payload["csr_pem"])

    await manager.handle_cert_result(
        result_msg({"status": "issued", "label": LABEL, "zone": ZONE,
                    "certificate_chain": chain_pem.decode("ascii")})
    )


# ---------------------------------------------------------------------------
# CSR generation
# ---------------------------------------------------------------------------


def test_csr_sans_exactly_wildcard_and_base():
    key_pem, csr_pem = generate_key_and_csr(LABEL, ZONE)
    csr = x509.load_pem_x509_csr(csr_pem.encode())
    assert csr.is_signature_valid
    san = csr.extensions.get_extension_for_class(x509.SubjectAlternativeName)
    names = set(san.value.get_values_for_type(x509.DNSName))
    assert names == {f"*.{LABEL}.{ZONE}", f"{LABEL}.{ZONE}"}
    # Empty subject — the names live in the SAN extension only
    assert csr.subject.rfc4514_string() == ""
    assert isinstance(csr.public_key(), ec.EllipticCurvePublicKey)
    assert b"PRIVATE KEY" in key_pem


def test_csr_uses_fresh_key_each_call():
    key1, _ = generate_key_and_csr(LABEL, ZONE)
    key2, _ = generate_key_and_csr(LABEL, ZONE)
    assert key1 != key2


# ---------------------------------------------------------------------------
# Enrollment -> issuance -> install
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path_installs_and_reports(manager, agent, tmp_path):
    await run_happy_path(manager, agent)

    cert_path, key_path = tls.cloud_cert_paths(tmp_path)
    assert cert_path.exists() and key_path.exists()
    state = tls.cloud_cert_holder().get()
    assert state is not None
    assert state.hostname_suffix == f"{LABEL}.{ZONE}"

    assert agent.state.get("system.cloud.cert_status") == "installed"
    assert agent.state.get("system.cloud.cert_error") == ""
    assert agent.state.get("system.cloud.cert_hostname") == f"{LABEL}.{ZONE}"
    # Acked to the cloud
    assert ("cert_status", {"state": "installed"}) in agent.sent

    status = manager.get_status()
    assert status["phase"] == "idle"
    assert status["last_error"] == ""
    assert status["hostname_suffix"] == f"{LABEL}.{ZONE}"


@pytest.mark.asyncio
async def test_second_request_while_pending_reports_busy(manager, agent):
    await manager.request_certificate()
    started, reason = await manager.request_certificate(manual=True)
    assert (started, reason) == (False, "busy")


@pytest.mark.asyncio
async def test_unsolicited_issued_result_is_inert(manager, agent, tmp_path):
    cert_pem, _ = make_cloud_cert_pem(LABEL, ZONE)
    await manager.handle_cert_result(
        result_msg({"status": "issued", "certificate_chain": cert_pem.decode("ascii")})
    )
    cert_path, _ = tls.cloud_cert_paths(tmp_path)
    assert not cert_path.exists()
    assert tls.cloud_cert_holder().get() is None
    assert agent.sent == []


@pytest.mark.asyncio
async def test_enrollment_result_when_idle_is_ignored(manager, agent):
    await manager.handle_cert_result(
        result_msg({"status": "enrollment", "label": LABEL, "zone": ZONE})
    )
    assert agent.sent == []
    assert manager.get_status()["phase"] == "idle"


@pytest.mark.asyncio
@pytest.mark.parametrize("label,zone", [
    ("UPPER", ZONE),                  # not lowercase
    ("bad*label", ZONE),              # wildcard injection
    (LABEL, "zone..dots"),            # empty label inside zone
    ("", ZONE),                       # empty
    (LABEL, "zone/../evil"),          # path-ish junk
    ("a.b", ZONE),                    # label must be a single DNS label
])
async def test_invalid_enrollment_names_rejected(manager, agent, label, zone):
    await manager.request_certificate()
    sent_before = len(agent.sent)
    await manager.handle_cert_result(
        result_msg({"status": "enrollment", "label": label, "zone": zone})
    )
    assert len(agent.sent) == sent_before  # no CSR went out
    assert manager.get_status()["last_error"] == "invalid_enrollment"
    await manager.stop()


@pytest.mark.asyncio
async def test_chain_not_matching_key_fails_typed(manager, agent, tmp_path):
    await manager.request_certificate()
    await manager.handle_cert_result(
        result_msg({"status": "enrollment", "label": LABEL, "zone": ZONE})
    )
    # A chain for some other key — install must reject and surface the error
    other_cert, _ = make_cloud_cert_pem(LABEL, ZONE)
    await manager.handle_cert_result(
        result_msg({"status": "issued", "certificate_chain": other_cert.decode("ascii")})
    )
    assert manager.get_status()["last_error"] == "install_failed"
    assert agent.state.get("system.cloud.cert_status") == "error"
    assert tls.cloud_cert_holder().get() is None
    await manager.stop()


# ---------------------------------------------------------------------------
# Typed errors and retry backoff
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_error_result_surfaces_code_and_backs_off_daily(manager, agent):
    await manager.request_certificate()
    await manager.handle_cert_result(
        result_msg({"status": "error", "error": "rate_limited",
                    "detail": "Issuance cap reached"})
    )
    assert agent.state.get("system.cloud.cert_status") == "error"
    assert agent.state.get("system.cloud.cert_error") == "rate_limited"
    status = manager.get_status()
    assert status["last_error"] == "rate_limited"
    assert status["last_error_detail"] == "Issuance cap reached"
    assert status["retry_pending"] is True

    # Automatic attempts are gated for ~a day
    remaining = manager._next_retry_at - time.monotonic()
    assert 23 * 3600 < remaining <= 24 * 3600
    started, reason = await manager.request_certificate()
    assert (started, reason) == (False, "backoff")
    await manager.stop()


@pytest.mark.asyncio
async def test_manual_retry_overrides_backoff(manager, agent):
    await manager.request_certificate()
    await manager.handle_cert_result(
        result_msg({"status": "error", "error": "acme_failed", "detail": "boom"})
    )
    started, reason = await manager.request_certificate(manual=True)
    assert (started, reason) == (True, "")
    assert agent.sent[-1] == ("cert_request", {})
    await manager.stop()


@pytest.mark.asyncio
async def test_busy_error_retries_soon_not_daily(manager, agent):
    await manager.request_certificate()
    await manager.handle_cert_result(
        result_msg({"status": "error", "error": "busy",
                    "detail": "An issuance for this system is already in progress"})
    )
    remaining = manager._next_retry_at - time.monotonic()
    # +1 s tolerance: remaining is (t0 + delay) - t1, which floating-point
    # rounding can nudge a hair past the exact interval. Point is it's a ~5 min
    # retry, not the ~24 h daily backoff.
    assert 0 < remaining <= cm.BUSY_RETRY_INTERVAL + 1
    await manager.stop()


@pytest.mark.asyncio
async def test_success_clears_backoff(manager, agent):
    await manager.request_certificate()
    await manager.handle_cert_result(
        result_msg({"status": "error", "error": "acme_failed", "detail": "boom"})
    )
    await run_happy_path(manager, agent, manual=True)
    assert manager.get_status()["last_error"] == ""
    assert manager.get_status()["retry_pending"] is False
    await manager.stop()


@pytest.mark.asyncio
async def test_stray_error_when_idle_is_logged_not_recorded(manager, agent):
    await manager.handle_cert_result(
        result_msg({"status": "error", "error": "acme_failed", "detail": "old"})
    )
    assert manager.get_status()["last_error"] == ""
    assert manager.get_status()["retry_pending"] is False


@pytest.mark.asyncio
async def test_missing_capability_reports_not_available(manager, agent):
    agent.capabilities = ["monitoring"]
    started, reason = await manager.request_certificate(manual=True)
    assert (started, reason) == (False, "not_available")
    assert agent.state.get("system.cloud.cert_error") == "not_available"
    assert manager.get_status()["retry_pending"] is False  # no pointless retries


@pytest.mark.asyncio
async def test_not_connected_reports_reason(manager, agent):
    agent.connected = False
    started, reason = await manager.request_certificate()
    assert (started, reason) == (False, "not_connected")
    assert agent.sent == []


@pytest.mark.asyncio
async def test_watchdog_times_out_lost_request(manager, agent, monkeypatch):
    monkeypatch.setattr(cm, "RESULT_TIMEOUT", 0.05)
    await manager.request_certificate()
    assert manager.get_status()["phase"] == "enrolling"
    await asyncio.sleep(0.2)
    status = manager.get_status()
    assert status["phase"] == "idle"
    assert status["last_error"] == "timeout"
    await manager.stop()


# ---------------------------------------------------------------------------
# Renewal
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_renew_due_starts_flow(manager, agent):
    await manager.handle_renew_due(
        {"type": "cert_renew_due",
         "payload": {"label": LABEL, "zone": ZONE, "expires_at": "2026-08-01T00:00:00Z"}}
    )
    assert agent.sent[-1] == ("cert_request", {})
    await manager.stop()


@pytest.mark.asyncio
async def test_renew_due_bypasses_local_backoff(manager, agent):
    await manager.request_certificate()
    await manager.handle_cert_result(
        result_msg({"status": "error", "error": "acme_failed", "detail": "boom"})
    )
    sent_before = len(agent.sent)
    await manager.handle_renew_due({"type": "cert_renew_due", "payload": {}})
    assert agent.sent[sent_before:] == [("cert_request", {})]
    await manager.stop()


@pytest.mark.asyncio
async def test_renew_due_when_disabled_notifies_cloud(agent, tmp_path):
    manager = CertificateManager(agent, FakeSysCfg(tmp_path, enabled=False))
    await manager.handle_renew_due({"type": "cert_renew_due", "payload": {}})
    assert agent.sent == [("cert_status", {"state": "disabled"})]


# ---------------------------------------------------------------------------
# Connect-time self-check
# ---------------------------------------------------------------------------


async def start_and_settle(manager):
    await manager.start()
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_self_check_requests_when_no_cert(manager, agent):
    await start_and_settle(manager)
    assert agent.sent[-1] == ("cert_request", {})
    await manager.stop()


@pytest.mark.asyncio
async def test_self_check_quiet_when_cert_fresh(manager, agent, tmp_path):
    cert_pem, key_pem = make_cloud_cert_pem(LABEL, ZONE, age_days=10, lifetime_days=90)
    tls.install_cloud_cert(tmp_path, cert_pem, key_pem)
    await start_and_settle(manager)
    assert agent.sent == []
    assert agent.state.get("system.cloud.cert_status") == "installed"
    await manager.stop()


@pytest.mark.asyncio
async def test_self_check_renews_past_two_thirds_lifetime(manager, agent, tmp_path):
    cert_pem, key_pem = make_cloud_cert_pem(LABEL, ZONE, age_days=70, lifetime_days=90)
    tls.install_cloud_cert(tmp_path, cert_pem, key_pem)
    await start_and_settle(manager)
    assert agent.sent[-1] == ("cert_request", {})
    await manager.stop()


@pytest.mark.asyncio
async def test_self_check_renews_expired_cert(manager, agent, tmp_path):
    # Install rejects expired certs, so write an expired pair directly
    cert_pem, key_pem = make_cloud_cert_pem(LABEL, ZONE, expired=True)
    cert_path, key_path = tls.cloud_cert_paths(tmp_path)
    cert_path.parent.mkdir(parents=True, exist_ok=True)
    cert_path.write_bytes(cert_pem)
    key_path.write_bytes(key_pem)
    await start_and_settle(manager)
    assert agent.sent[-1] == ("cert_request", {})
    await manager.stop()


@pytest.mark.asyncio
async def test_self_check_does_nothing_when_disabled(agent, tmp_path):
    manager = CertificateManager(agent, FakeSysCfg(tmp_path, enabled=False))
    await start_and_settle(manager)
    assert agent.sent == []
    assert agent.state.get("system.cloud.cert_status") == "disabled"


@pytest.mark.asyncio
async def test_self_check_respects_failure_backoff(manager, agent):
    manager._next_retry_at = time.monotonic() + 3600
    await start_and_settle(manager)
    assert agent.sent == []
    await manager.stop()


# ---------------------------------------------------------------------------
# Enable / disable
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enable_sets_flag_and_requests(agent, tmp_path):
    syscfg = FakeSysCfg(tmp_path, enabled=False)
    manager = CertificateManager(agent, syscfg)
    started, reason = await manager.enable()
    assert (started, reason) == (True, "")
    assert syscfg.get("tls", "cloud_cert") is True
    assert syscfg.saved == 1
    assert agent.sent[-1] == ("cert_request", {})
    await manager.stop()


@pytest.mark.asyncio
async def test_disable_removes_cert_and_notifies(manager, agent, tmp_path):
    await run_happy_path(manager, agent)
    syscfg = manager._syscfg

    await manager.disable()

    cert_path, key_path = tls.cloud_cert_paths(tmp_path)
    assert not cert_path.exists() and not key_path.exists()
    assert tls.cloud_cert_holder().get() is None
    assert syscfg.get("tls", "cloud_cert") is False
    assert agent.state.get("system.cloud.cert_status") == "disabled"
    assert agent.state.get("system.cloud.cert_hostname") == ""
    assert agent.sent[-1] == ("cert_status", {"state": "disabled"})
    assert manager.get_status()["hostname_suffix"] == ""


@pytest.mark.asyncio
async def test_disable_never_blocks_on_cloud(manager, agent, tmp_path):
    async def boom(msg_type, payload):
        raise ConnectionError("cloud unreachable")

    agent.send_message = boom
    await manager.disable()  # must not raise
    assert manager._syscfg.get("tls", "cloud_cert") is False


@pytest.mark.asyncio
async def test_stop_discards_pending_flow(manager, agent):
    await manager.request_certificate()
    await manager.handle_cert_result(
        result_msg({"status": "enrollment", "label": LABEL, "zone": ZONE})
    )
    assert manager.get_status()["phase"] == "issuing"
    await manager.stop()
    status = manager.get_status()
    assert status["phase"] == "idle"
    assert manager._pending_key_pem is None


# ---------------------------------------------------------------------------
# Agent dispatch + capability gate
# ---------------------------------------------------------------------------


class DispatchSpy:
    def __init__(self):
        self.called = None

    async def handle_cert_result(self, msg):
        self.called = "cert_result"

    async def handle_renew_due(self, msg):
        self.called = "cert_renew_due"


def make_dispatch_agent(enabled_capabilities):
    from openavc.cloud.agent import CloudAgent

    agent = CloudAgent.__new__(CloudAgent)
    agent._session = None  # skip signature verification path
    agent._enabled_capabilities = enabled_capabilities
    agent._cert_manager = DispatchSpy()
    return agent


@pytest.mark.asyncio
async def test_agent_dispatches_cert_result():
    agent = make_dispatch_agent(["trusted_certs"])
    await agent._handle_message({"type": "cert_result", "payload": {"status": "error"}})
    assert agent._cert_manager.called == "cert_result"


@pytest.mark.asyncio
async def test_agent_gates_renew_due_on_capability():
    agent = make_dispatch_agent(["monitoring"])
    await agent._handle_message({"type": "cert_renew_due", "payload": {}})
    assert agent._cert_manager.called is None


@pytest.mark.asyncio
async def test_agent_dispatches_renew_due_when_capability_enabled():
    agent = make_dispatch_agent(["monitoring", "trusted_certs"])
    await agent._handle_message({"type": "cert_renew_due", "payload": {}})
    assert agent._cert_manager.called == "cert_renew_due"
