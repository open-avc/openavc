"""Host admission must not confuse connection lifetime with durable work."""
import asyncio

import pytest

from openavc.core.host_policy import (
    HostCategory,
    HostPolicy,
    HostPolicyDenied,
    PolicySnapshot,
)


class MutableHost(HostPolicy):
    def __init__(self):
        super().__init__()
        self.current = PolicySnapshot(1, True, True)

    def snapshot(self):
        return self.current


def test_optional_interfaces_do_not_disable_control_or_host_management():
    policy = MutableHost()
    policy.current = PolicySnapshot(2, False, False)
    for category in (HostCategory.HOST, HostCategory.RUNTIME):
        lease = policy.admit(category)
        lease.close()
    for category in (HostCategory.PROGRAMMER, HostCategory.PANEL,
                     HostCategory.RENDER, HostCategory.PREVIEW):
        with pytest.raises(HostPolicyDenied):
            policy.admit(category)
    with pytest.raises(ValueError):
        policy.admit("unregistered")


def test_render_is_shared_but_preview_requires_programming():
    policy = MutableHost()
    policy.current = PolicySnapshot(2, False, True)
    for category in (HostCategory.PANEL, HostCategory.RENDER):
        policy.admit(category).close()
    with pytest.raises(HostPolicyDenied):
        policy.admit(HostCategory.PREVIEW)


def test_queued_admission_cannot_commit_after_its_registered_owner_changes():
    policy = MutableHost()
    registration = {"current": object()}
    admitted = registration["current"]
    lease = policy.admit(HostCategory.PROGRAMMER,
                         is_current=lambda: registration["current"] is admitted)
    registration["current"] = object()
    assert policy.snapshot().programmer_enabled  # No host toggle caused this refusal.
    with pytest.raises(HostPolicyDenied):
        lease.begin_commit()
    assert lease.revoked.is_set()
    lease.close()


def test_unknown_or_invalid_state_cannot_enable_optional_interfaces():
    policy = MutableHost()
    policy.current = PolicySnapshot(None, False, False)
    with pytest.raises(HostPolicyDenied):
        policy.admit(HostCategory.PROGRAMMER)
    for values in ((None, True, False), (True, True, True), (1, 1, True), (-1, True, True)):
        with pytest.raises(ValueError):
            PolicySnapshot(*values)


def test_queued_admission_does_not_survive_disable_and_reenable():
    policy = MutableHost()
    queued = policy.admit(HostCategory.PROGRAMMER)
    policy.current = PolicySnapshot(2, False, True)
    policy.refresh()
    policy.current = PolicySnapshot(3, True, True)
    assert queued.revoked.is_set()
    assert not queued.revalidate()
    with pytest.raises(HostPolicyDenied):
        queued.begin_commit()
    queued.close()
    fresh = policy.admit(HostCategory.PROGRAMMER)
    commit = fresh.begin_commit()
    commit.finish("committed")
    fresh.close()


def test_revision_recheck_refuses_queued_work_without_a_polling_interval():
    policy = MutableHost()
    queued = policy.admit(HostCategory.PROGRAMMER)
    # A disable/re-enable can happen between observations. Changed revision is
    # sufficient; a polling task need not have seen the disabled state.
    policy.current = PolicySnapshot(3, True, True)
    assert not queued.revalidate()
    with pytest.raises(HostPolicyDenied):
        queued.begin_commit()
    queued.close()


def test_accepted_runtime_work_does_not_inherit_programming_revocation():
    policy = MutableHost()
    runtime = policy.admit(HostCategory.RUNTIME)
    policy.current = PolicySnapshot(2, False, False)
    commit = runtime.begin_commit()
    commit.finish("committed")
    runtime.close()


@pytest.mark.asyncio
async def test_disconnected_request_does_not_acknowledge_a_running_commit():
    policy = MutableHost()
    committed_work, owner_release = asyncio.Event(), asyncio.Event()
    lease = policy.admit(HostCategory.PROGRAMMER)

    async def owner():
        commit = lease.begin_commit()
        committed_work.set()
        await owner_release.wait()
        commit.finish({"decision": "committed"})

    task = asyncio.create_task(owner())
    await committed_work.wait()
    lease.close()  # HTTP disconnect releases only its admission.
    policy.current = PolicySnapshot(2, False, True)
    result = await policy.apply_current(timeout=0)
    assert not result.observed
    assert result.pending_connections == 0
    assert result.pending_commits == 1
    assert not task.done()
    owner_release.set()
    await task
    result = await policy.apply_current(timeout=0)
    assert result.observed and result.pending_commits == 0


@pytest.mark.asyncio
async def test_disable_waits_for_stream_close_and_commit_completion():
    policy = MutableHost()
    stream = policy.admit(HostCategory.PROGRAMMER)
    commit = stream.begin_commit()
    panel = policy.admit(HostCategory.PANEL)
    policy.current = PolicySnapshot(2, False, True)
    observation = asyncio.create_task(policy.apply_current(timeout=1))
    await asyncio.wait_for(stream.revoked.wait(), 1)
    assert not panel.revoked.is_set() and not observation.done()
    stream.close()
    pending = await policy.apply_current(timeout=0)
    assert pending.pending_connections == 0 and pending.pending_commits == 1
    commit.finish("not_committed")
    result = await observation
    assert result.observed and result.snapshot == policy.current
    panel.close()


@pytest.mark.asyncio
async def test_timeout_and_cancellation_do_not_discard_outstanding_work():
    policy = MutableHost()
    request = policy.admit(HostCategory.PROGRAMMER)
    commit = request.begin_commit()
    request.close()
    policy.current = PolicySnapshot(2, False, False)
    observation = asyncio.create_task(policy.apply_current(timeout=60))
    await asyncio.sleep(0)
    observation.cancel()
    with pytest.raises(asyncio.CancelledError):
        await observation
    pending = await policy.apply_current(timeout=0.001)
    assert not pending.observed and pending.pending_commits == 1
    commit.finish("recovery_required")
    commit.finish("duplicate finish ignored")
    assert commit.receipt == "recovery_required"
    assert (await policy.apply_current(timeout=0)).observed


@pytest.mark.asyncio
async def test_default_policy_preserves_interface_availability():
    policy = HostPolicy()
    leases = [policy.admit(category) for category in HostCategory]
    assert (await policy.apply_current(timeout=0)).observed
    for lease in leases:
        lease.close()
