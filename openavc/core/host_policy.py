"""Host-controlled interface admission and commit lifetime.

This policy supplements the server's normal authentication. A route must first
authenticate its caller and choose a category from its registered metadata.
Runtime control and host management retain their own authentication requirements.

Admission leases belong to requests/connections. Commit leases belong to the
operation performing a durable change and survive cancellation of its request.
Finish a commit lease only when that operation has a known durable disposition.
All admission, revocation and completion calls run on the server's event loop.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
import math


class HostCategory(str, Enum):
    HOST = "host"
    RUNTIME = "runtime"
    PROGRAMMER = "programmer"
    PANEL = "panel"
    RENDER = "render"
    PREVIEW = "preview"


class HostPolicyDenied(PermissionError):
    """The host no longer permits this interface or operation."""


@dataclass(frozen=True)
class PolicySnapshot:
    revision: int | None
    programmer_enabled: bool
    panel_enabled: bool

    def __post_init__(self) -> None:
        if (type(self.programmer_enabled) is not bool
                or type(self.panel_enabled) is not bool):
            raise ValueError("Interface enablement must be boolean")
        if self.revision is None:
            if self.programmer_enabled or self.panel_enabled:
                raise ValueError("Unknown host state cannot enable interfaces")
        elif type(self.revision) is not int or self.revision < 0:
            raise ValueError("Policy revision must be a nonnegative integer")

    def allows(self, category: HostCategory) -> bool:
        category = HostCategory(category)
        if category in (HostCategory.HOST, HostCategory.RUNTIME):
            return True
        if category in (HostCategory.PROGRAMMER, HostCategory.PREVIEW):
            return self.programmer_enabled
        if category is HostCategory.PANEL:
            return self.panel_enabled
        return self.programmer_enabled or self.panel_enabled


@dataclass(frozen=True)
class PolicyApplication:
    snapshot: PolicySnapshot
    observed: bool
    pending_connections: int
    pending_commits: int


class AdmissionLease:
    def __init__(self, policy: HostPolicy, category: HostCategory,
                 snapshot: PolicySnapshot, principal: object, origin: object,
                 is_current: Callable[[], bool] | None = None):
        self.policy = policy
        self.category = category
        self.snapshot = snapshot
        self.principal = principal
        self.origin = origin
        self._is_current = is_current
        self.revoked = asyncio.Event()
        self.closed = False

    def revalidate(self) -> bool:
        """Recheck queued work against the exact admitted policy revision."""
        current = self.refresh()
        return (not self.closed and not self.revoked.is_set()
                and (self.category in (HostCategory.HOST, HostCategory.RUNTIME)
                     or current == self.snapshot))

    def _check_snapshot(self, current: PolicySnapshot) -> None:
        valid = True
        if self._is_current is not None:
            try:
                valid = self._is_current() is True
            except HostPolicyDenied:
                valid = False
        if not current.allows(self.category) or not valid:
            self.revoked.set()

    def refresh(self) -> PolicySnapshot:
        """Poll this connection without rescanning every other connection."""
        current = self.policy._read_snapshot()
        self._check_snapshot(current)
        return current

    def begin_commit(self) -> CommitLease:
        if not self.revalidate():
            raise HostPolicyDenied("Host permission changed before the operation could start")
        lease = CommitLease(self.policy, self.category)
        self.policy._commits.add(lease)
        return lease

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self.policy._admissions.discard(self)
            self.policy._changed.set()


class CommitLease:
    def __init__(self, policy: HostPolicy, category: HostCategory):
        self.policy = policy
        self.category = category
        self.finished = False
        self.receipt: object = None

    def finish(self, receipt: object) -> None:
        """Record a durable disposition and release the outstanding commit."""
        if not self.finished:
            self.receipt = receipt
            self.finished = True
            self.policy._commits.discard(self)
            self.policy._changed.set()


class HostPolicy:
    """Permissive hosting by default; override snapshot() for a host provider.

    Returning an unknown snapshot disables optional interfaces. Authentication
    stays with the canonical HTTP/WebSocket/command adapter, including for host,
    runtime and preview categories. A verified plugin token selects its registered
    panel or Programmer category at that same adapter boundary.
    """

    def __init__(self) -> None:
        self._admissions: set[AdmissionLease] = set()
        self._commits: set[CommitLease] = set()
        self._changed = asyncio.Event()

    def snapshot(self) -> PolicySnapshot:
        return PolicySnapshot(0, True, True)

    def _read_snapshot(self) -> PolicySnapshot:
        current = self.snapshot()
        if not isinstance(current, PolicySnapshot):
            raise TypeError("Host provider must return a PolicySnapshot")
        return current

    def refresh(self) -> PolicySnapshot:
        current = self._read_snapshot()
        for lease in self._admissions:
            lease._check_snapshot(current)
        return current

    def admit(self, category: HostCategory, principal: object = None,
              origin: object = None, *,
              is_current: Callable[[], bool] | None = None) -> AdmissionLease:
        category = HostCategory(category)
        current = self.refresh()
        if not current.allows(category):
            raise HostPolicyDenied("This interface is disabled by the host")
        lease = AdmissionLease(self, category, current, principal, origin, is_current)
        lease._check_snapshot(current)
        if lease.revoked.is_set():
            raise HostPolicyDenied("The registered interface is no longer available")
        self._admissions.add(lease)
        return lease

    def _pending(self, current: PolicySnapshot) -> tuple[int, int]:
        connections = sum(lease.revoked.is_set() for lease in self._admissions)
        commits = sum(not current.allows(lease.category) for lease in self._commits)
        return connections, commits

    async def apply_current(self, timeout: float = 5.0) -> PolicyApplication:
        """Revoke disabled surfaces and observe their connection/commit drain.

        A timeout reports pending. It never cancels the owner of a durable change
        or treats a disconnected request as proof that its commit finished.
        """
        if not math.isfinite(timeout) or timeout < 0:
            raise ValueError("Policy observation timeout must be finite and nonnegative")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            self._changed.clear()
            current = self.refresh()
            connections, commits = self._pending(current)
            if connections == commits == 0:
                return PolicyApplication(current, True, 0, 0)
            remaining = deadline - loop.time()
            if remaining <= 0:
                return PolicyApplication(current, False, connections, commits)
            try:
                await asyncio.wait_for(self._changed.wait(), remaining)
            except TimeoutError:
                # Re-read the provider and counters before reporting the result.
                # Completion may have raced with expiry of this wait.
                pass
