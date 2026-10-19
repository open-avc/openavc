"""Typed commissioning results and bounded, non-replaying result reservations.

This owns result retention, not device execution, cancellation or project commits.
The runner claims a reservation synchronously on its event loop, acquires its
existing per-device admission without yielding, and dispatches at most once. An
unknown capability never creates work. The HTTP adapter must supply current
request admission on every access and recheck it at response send; the retained
original grant must outlive the prepare request without refreshing its session.

Only declared ingress adapters construct these results. Diagnostic text is
protected, not sanitized. Generated credentials enter publish_secret separately,
before any awaited save/reconnect; they never enter progress or result JSON.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
import math
import re
import secrets
import time
from typing import Any
from uuid import uuid4

from openavc.core.outbound_messages import RoomText


class SetupUnavailable(PermissionError):
    """Unknown, expired or revoked result; never an instruction to retry work."""


class SetupCapacityError(RuntimeError):
    """Capacity must be reserved before a commissioning effect can begin."""


class SetupConflict(ValueError):
    """A request does not match its prepared target, inputs or result contract."""


class Outcome(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DEGRADED = "degraded"
    UNVERIFIED = "unverified"


class StageState(str, Enum):
    NOT_REQUESTED = "not_requested"
    REPORTED_COMPLETE = "reported_complete"
    FAILED = "failed"
    UNKNOWN = "unknown"


def _code(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9_.-]{0,79}", value):
        raise ValueError("A declared setup code is required")
    return value


def _json_bytes(value: Any, limit: int) -> bytes:
    # No default=str: malformed results must not become successful empty output.
    try:
        data = json.dumps(value, allow_nan=False, ensure_ascii=True,
                          sort_keys=True, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError("Setup data must be finite JSON") from exc
    if len(data) > limit:
        raise SetupCapacityError("Setup data exceeds its reserved capacity")
    return data


def encode_setup_credentials(values: dict[str, str]) -> bytes:
    """Validate the complete credential payload against its reserved byte budget.

    Ingress adapters and the store use the identical JSON representation, which
    includes field names, punctuation and escaped Unicode/control characters.
    """
    if (not isinstance(values, dict) or not values or any(
            not isinstance(k, str) or not k or not isinstance(v, str) or not v
            for k, v in values.items())):
        raise ValueError("Declared nonempty setup credentials required")
    return _json_bytes(values, 16384)


@dataclass(frozen=True)
class SetupField:
    """A declared diagnostic value, visible only with the protected finding."""

    label: str
    value: str | bool | int | float | None

    def payload(self) -> dict:
        if not isinstance(self.label, str) or not self.label.strip():
            raise TypeError("Setup field label required")
        if self.value is not None and type(self.value) not in (str, bool, int, float):
            raise TypeError("Setup field value must be a flat primitive")
        if type(self.value) is float and not math.isfinite(self.value):
            raise ValueError("Setup field value must be finite")
        return {"label": self.label, "value": self.value}


@dataclass(frozen=True)
class SetupFinding:
    code: str
    outcome: Outcome
    detail: str = ""
    fields: tuple[SetupField, ...] = ()

    def payload(self) -> dict:
        if not isinstance(self.outcome, Outcome) or not isinstance(self.detail, str):
            raise TypeError("Typed setup finding required")
        return {"code": _code(self.code), "outcome": self.outcome.value, "detail": self.detail,
                **({"fields": [value.payload() for value in self.fields]} if self.fields else {})}


@dataclass(frozen=True)
class SetupResult:
    outcome: Outcome
    summary: RoomText
    findings: tuple[SetupFinding, ...] = ()
    config_save: StageState = StageState.NOT_REQUESTED
    reconnect: StageState = StageState.NOT_REQUESTED

    def payload(self) -> dict:
        if (not isinstance(self.outcome, Outcome) or not isinstance(self.summary, RoomText)
                or not isinstance(self.config_save, StageState)
                or not isinstance(self.reconnect, StageState)):
            raise TypeError("Typed setup result required")
        return {"outcome": self.outcome.value, "summary": str(self.summary),
                "findings": [finding.payload() for finding in self.findings],
                "config_save": self.config_save.value, "reconnect": self.reconnect.value}


@dataclass(frozen=True)
class SetupProgress:
    code: str
    detail: str
    percent: float | None = None


@dataclass(frozen=True)
class PreparedSetup:
    run_id: str
    capability: str = field(repr=False)


@dataclass(eq=False)
class _ResultSlot:
    run_id: str
    request: bytes = field(repr=False)
    target: object = field(repr=False)
    original_grant: Callable[[], bool] = field(repr=False)
    principal: str | None = field(repr=False)
    deadline: float
    phase: str = "prepared"
    revoked: bool = False
    sequence: int = 0
    progress: bytes | None = field(default=None, repr=False)
    result: bytes | None = field(default=None, repr=False)
    secret: bytes | None = field(default=None, repr=False)
    secret_generation: int = 0
    retrieved_generation: int = 0
    acknowledged_generation: int = 0
    secret_state: str = "none"


class SetupResultStore:
    """Single-event-loop owner. None of its transitions yields or invokes a task.

    Each slot reserves a fixed upper bound, including unused secret capacity.
    Active slots are never evicted by age, acknowledgement or revocation. A stuck
    run therefore applies bounded admission backpressure instead of losing a new
    credential. Only the runner's terminal disposition starts the retention clock.
    """

    def __init__(self, *, max_slots: int = 64, per_principal: int = 8,
                 prepare_ttl: float = 300, result_ttl: float = 600,
                 clock: Callable[[], float] = time.monotonic):
        if (type(max_slots) is not int or max_slots < 1 or type(per_principal) is not int
                or per_principal < 1 or not math.isfinite(prepare_ttl) or prepare_ttl <= 0
                or not math.isfinite(result_ttl) or result_ttl <= 0):
            raise ValueError("Positive setup retention bounds required")
        self._slots: dict[bytes, _ResultSlot] = {}
        self._max_slots = max_slots
        self._per_principal = per_principal
        self._prepare_ttl = prepare_ttl
        self._result_ttl = result_ttl
        self._clock = clock

    @staticmethod
    def _allowed(check: Callable[[], bool]) -> bool:
        try:
            return check() is True
        except Exception:
            return False

    @staticmethod
    def _digest(capability: str) -> bytes:
        if not isinstance(capability, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", capability):
            raise SetupUnavailable("This setup result is unavailable")
        return hashlib.sha256(capability.encode("utf-8")).digest()

    @staticmethod
    def _erase(slot: _ResultSlot) -> None:
        slot.secret = None
        slot.secret_state = "unavailable"
        slot.progress = None
        slot.result = None

    def prune(self) -> None:
        now = self._clock()
        for key, slot in tuple(self._slots.items()):
            if not slot.revoked and not self._allowed(slot.original_grant):
                slot.revoked = True
                self._erase(slot)
            if slot.phase in ("prepared", "terminal") and (slot.revoked or now >= slot.deadline):
                self._erase(slot)
                del self._slots[key]

    def prepare(self, request: dict, *, target: object, original_grant: Callable[[], bool],
                current: Callable[[], bool], principal: str | None = None) -> PreparedSetup:
        """Freeze validated device/action/contract/parameters; perform no effects."""
        self.prune()
        if not self._allowed(current) or not self._allowed(original_grant):
            raise SetupUnavailable("Setup permission is no longer available")
        if not isinstance(request, dict) or target is None:
            raise ValueError("A setup request and actual target are required")
        if principal is not None and (not isinstance(principal, str) or not principal):
            raise ValueError("A canonical principal or None is required")
        request_bytes = _json_bytes(request, 16384)
        if len(self._slots) >= self._max_slots or (principal is not None and sum(
                slot.principal == principal for slot in self._slots.values()) >= self._per_principal):
            raise SetupCapacityError("Setup result capacity is full; finish an existing attempt")
        capability = secrets.token_urlsafe(32)
        key = self._digest(capability)
        while key in self._slots:
            capability = secrets.token_urlsafe(32)
            key = self._digest(capability)
        slot = _ResultSlot(uuid4().hex, request_bytes, target, original_grant,
                           principal, self._clock() + self._prepare_ttl)
        self._slots[key] = slot
        return PreparedSetup(slot.run_id, capability)

    def _access(self, capability: str, current: Callable[[], bool], principal: str | None) -> _ResultSlot:
        self.prune()
        slot = self._slots.get(self._digest(capability))
        if (slot is None or slot.revoked or not self._allowed(current)
                or (slot.principal is not None and slot.principal != principal)):
            raise SetupUnavailable("This setup result is unavailable")
        return slot

    def access_guard(self, capability: str, *, principal: str | None = None,
                     secret_generation: int | None = None) -> Callable[[], bool]:
        """Additional send-time guard, beside the response/socket's own admission.

        A protected snapshot is not itself authority. Secret responses bind the
        returned generation so acknowledgement, save or replacement while a send
        waits cannot release the old bytes. Never retain a closed HTTP lease here.
        """
        def available() -> bool:
            try:
                slot = self._access(capability, lambda: True, principal)
                return secret_generation is None or (
                    slot.secret is not None and slot.secret_generation == secret_generation)
            except SetupUnavailable:
                return False
        return available

    def request_data(self, capability: str, *, current: Callable[[], bool],
                     principal: str | None = None) -> dict:
        """Runner-only frozen inputs; these must never enter an HTTP/WS result."""
        return json.loads(self._access(capability, current, principal).request)

    def dispatch_inputs(self, slot: _ResultSlot) -> dict:
        self._owned_active(slot)
        return json.loads(slot.request)

    def require_inputs(self, slot: _ResultSlot, request: dict) -> None:
        """Recheck exact JSON types after a runner admission wait."""
        self._owned_active(slot)
        if _json_bytes(request, 16384) != slot.request:
            raise SetupConflict("The setup inputs changed before dispatch")

    def claim(self, capability: str, request: dict, *, target: object,
              current: Callable[[], bool], principal: str | None = None) -> _ResultSlot | None:
        """Consume once. None means already consumed; caller returns status only.

        The caller must hold its existing runner admission before this synchronous
        transition. Task-construction failure is completed as unavailable/failed,
        never reset to prepared. Target identity is a pre-start guard, not a W2
        target lease or permission to keep using a replaced driver during a run.
        """
        slot = self._access(capability, current, principal)
        if _json_bytes(request, 16384) != slot.request:
            raise SetupConflict("Setup inputs changed; this attempt cannot be reused")
        if slot.phase != "prepared":
            return None
        if target is not slot.target:
            raise SetupConflict("The setup target changed before start")
        slot.phase = "claimed"
        return slot

    def _owned_active(self, slot: _ResultSlot) -> None:
        if not any(value is slot for value in self._slots.values()) or slot.phase not in ("claimed", "running"):
            raise SetupConflict("Setup result reservation is not active")

    def running(self, slot: _ResultSlot) -> None:
        self._owned_active(slot)
        slot.phase = "running"

    def progress(self, slot: _ResultSlot, code: str, detail: str, percent: float | None) -> None:
        self._owned_active(slot)
        if (not isinstance(detail, str) or (percent is not None and (
                type(percent) not in (int, float) or not math.isfinite(percent) or not 0 <= percent <= 100))):
            raise ValueError("Invalid setup progress")
        payload = _json_bytes({"code": _code(code), "detail": detail, "percent": percent,
                               "sequence": slot.sequence + 1}, 16384)
        self.prune()
        slot.sequence += 1
        if not slot.revoked:
            slot.progress = payload

    def publish_secret(self, slot: _ResultSlot, values: dict[str, str]) -> int:
        """Called by a declared adapter before saving/reconnecting can raise."""
        self._owned_active(slot)
        data = encode_setup_credentials(values)
        self.prune()
        slot.secret_generation += 1
        if not slot.revoked:
            slot.secret = data
            slot.secret_state = "available"
        return slot.secret_generation

    def secret_saved(self, slot: _ResultSlot, generation: int) -> None:
        self._owned_active(slot)
        if type(generation) is not int or generation < 1 or generation > slot.secret_generation:
            raise SetupConflict("Unknown setup credential generation")
        if generation == slot.secret_generation:
            slot.secret = None
            slot.secret_state = "unavailable" if slot.revoked else "reported_saved"

    def saving_generation(self, slot: _ResultSlot, delta: dict) -> int | None:
        """Match only an explicitly registered credential, before awaiting save.

        This does not discover secret-looking keys or manufacture a commit
        receipt. The existing save owner still determines its disposition.
        """
        self._owned_active(slot)
        if slot.secret is not None:
            values = json.loads(slot.secret)
            if all(delta.get(key) == value for key, value in values.items()):
                return slot.secret_generation
        return None

    def complete(self, slot: _ResultSlot, result: SetupResult) -> None:
        self._owned_active(slot)
        if not isinstance(result, SetupResult):
            raise TypeError("A typed setup result is required")
        payload = _json_bytes(result.payload(), 32768)
        self.prune()
        slot.phase = "terminal"
        slot.deadline = self._clock() + self._result_ttl
        if not slot.revoked:
            slot.result = payload
        # Execution has ended; the store no longer retains the driver instance.
        slot.target = None

    def status(self, capability: str, *, current: Callable[[], bool], principal: str | None = None) -> dict:
        slot = self._access(capability, current, principal)
        return self._status(slot)

    @staticmethod
    def _status(slot: _ResultSlot) -> dict:
        return {"run_id": slot.run_id, "status": slot.phase,
                "progress": json.loads(slot.progress) if slot.progress else None,
                "result": json.loads(slot.result) if slot.result else None,
                "secret": {"state": slot.secret_state, "generation": slot.secret_generation}}

    def retrieve(self, capability: str, *, current: Callable[[], bool], principal: str | None = None) -> dict:
        slot = self._access(capability, current, principal)
        if slot.secret is None:
            raise SetupUnavailable("No retained credential is available for this setup")
        slot.retrieved_generation = slot.secret_generation
        return {"generation": slot.secret_generation, "values": json.loads(slot.secret)}

    def acknowledge(self, capability: str, generation: int, *, current: Callable[[], bool],
                    principal: str | None = None) -> None:
        slot = self._access(capability, current, principal)
        if type(generation) is not int or generation < 1 or generation > slot.retrieved_generation:
            raise SetupConflict("Only a retrieved credential generation can be acknowledged")
        slot.acknowledged_generation = max(slot.acknowledged_generation, generation)
        if generation == slot.secret_generation:
            slot.secret = None
            slot.secret_state = "acknowledged"
