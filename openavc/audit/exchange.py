"""A command's own exchange: which message in its window was the command's,
the driver's next request after it, and which signs of a refusal answer the
command rather than that request.

A command's window holds more than its own exchange: a poll that lands in it,
a follow-up query the driver sends on its own. Every send in the window is
placed by where it sits in the device's traffic list, never by the clock:
``time.time()`` ticks every 15.6 ms on Windows before Python 3.13, so a reply
that landed just before the send can carry the send's own timestamp. The
command's own message is the last one sent before the driver's call returned,
else the first after it (a command queued behind a poll goes out after the
polling messages already on the way); the driver's next request is the send
after that.

A sign of a refusal (an error the driver published, a ``last_error`` it wrote)
belongs to the command when it came before the driver's next request and
while the command's reply could still be on the way, ``REPLY_WINDOW_SECONDS``
after the call returned. Anything later answers a request the driver sent on
its own: kept apart, with what the driver had just sent, and never put on the
command.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from openavc.audit.observe import REPLY_WINDOW_SECONDS
from openavc.audit.report_text import traffic_text
from openavc.core.device_traffic import TX, serialize_entry

if TYPE_CHECKING:
    from openavc.audit.commands import CommandTrial

# How much of the request a later refusal answered is quoted.
REQUEST_TEXT_CHARS = 80
# The replies no response rule matched: how many are quoted, and how much of each.
UNMATCHED_EXAMPLES = 5
UNMATCHED_EXAMPLE_CHARS = 200


def frames_in(trial: "CommandTrial", observer: Any) -> list[Any]:
    """The window's traffic without its raw chunks (what the driver was
    handed); none before any connection carried the command."""
    if observer is None:
        return []
    return [e for e in trial.traffic_in(observer) if not e.chunk]


def window_sends(trial: "CommandTrial", observer: Any) -> list[tuple[int, Any]]:
    """Every message sent in the window (raw chunks left out), each with its
    place in the device's traffic list."""
    if observer is None:
        return []
    start = trial.marks.get("traffic", 0)
    return [
        (start + i, e) for i, e in enumerate(trial.traffic_in(observer))
        if e.direction == TX and not e.chunk
    ]


def own_send_index(trial: "CommandTrial", sends: list[tuple[int, Any]]) -> int | None:
    """Where in ``sends`` the command's own message is: the last one sent
    before the call returned, else the first after it. None when nothing was
    sent."""
    if not sends:
        return None
    returned = trial.marks.get("returned", trial.marks.get("traffic", 0))
    before = sum(1 for at, _ in sends if at < returned)
    return before - 1 if before else 0


def own_send(trial: "CommandTrial", observer: Any, frames: list[Any]) -> int:
    """Where in ``frames`` (the window without its raw chunks) the command's
    own message went out; 0 when none did."""
    sends = window_sends(trial, observer)
    index = own_send_index(trial, sends)
    if index is None:
        return 0
    own = sends[index][1]
    return next((i for i, e in enumerate(frames) if e is own), 0)


def next_request(trial: "CommandTrial", sends: list[tuple[int, Any]]) -> int | None:
    """Where the driver's next request after the command's own message sits
    in the device's traffic list; None when it sent none in the window."""
    index = own_send_index(trial, sends)
    if index is None or index + 1 >= len(sends):
        return None
    return sends[index + 1][0]


@dataclass(frozen=True)
class OwnExchange:
    """The stretch of the window a refusal of the command itself can come in:
    before the driver's next request, and until the command's reply can no
    longer be on the way."""

    next_request: int | None
    deadline: float

    def holds(self, sign: dict[str, Any]) -> bool:
        if self.next_request is not None and sign.get("position", 0) > self.next_request:
            return False
        return sign["t"] <= self.deadline


def own_exchange(trial: "CommandTrial", sends: list[tuple[int, Any]]) -> OwnExchange:
    returned = trial.returned_at or trial.sent_at
    return OwnExchange(next_request(trial, sends), returned + REPLY_WINDOW_SECONDS)


def refusals(trial: "CommandTrial", observer: Any) -> dict[str, Any]:
    """Which signs of a refusal answer the command (how many errors the driver
    published, how many ``last_error`` writes, what the device said) and
    which answer a request the driver sent on its own after it (``later``)."""
    sends = window_sends(trial, observer)
    own = own_exchange(trial, sends)
    redactor = observer.redactor() if observer is not None else None
    own_errors = [e for e in trial.device_errors if own.holds(e)]
    own_writes = [w for w in trial.error_writes if own.holds(w) and w.get("error")]
    return {
        "device_errors": len(own_errors),
        "last_error_writes": len(own_writes),
        "last_error": what_it_said(own_writes, own_errors),
        "later": later_refusals(trial, own, sends, redactor),
    }


def what_it_said(own_writes: list[dict[str, Any]], own_errors: list[dict[str, Any]]) -> str | None:
    """The refusal in the device's words: the last ``last_error`` the driver
    wrote, else the last error it published that has any text."""
    if own_writes:
        return own_writes[-1]["error"]
    return next((e["error"] for e in reversed(own_errors) if e.get("error")), None)


def later_refusals(
    trial: "CommandTrial", own: OwnExchange, sends: list[tuple[int, Any]], redactor: Any,
) -> list[dict[str, Any]]:
    """Each refusal that answered a later request, once per text in the
    order they came, with how many times it came and the request it answered."""
    later: dict[str, dict[str, Any]] = {}
    for position, t, text in later_signs(trial, own):
        kept = later.get(str(text))
        if kept is not None:
            kept["count"] += 1
            continue
        later[str(text)] = {
            "error": text,
            "after": round(t - trial.sent_at, 1),
            "count": 1,
            "request": request_before(position, sends, redactor),
        }
    return list(later.values())


def later_signs(trial: "CommandTrial", own: OwnExchange) -> list[tuple[int, float, Any]]:
    """The signs of a refusal outside the command's own exchange, as
    ``(position, t, text)`` in the order they happened."""
    errors = [
        (e.get("position", 0), e["t"], e.get("error") or "")
        for e in trial.device_errors if not own.holds(e)
    ]
    writes = [
        (w.get("position", 0), w["t"], w["error"])
        for w in trial.error_writes if not own.holds(w) and w.get("error")
    ]
    return sorted(errors + writes, key=lambda s: (s[0], s[1]))


def request_before(position: int, sends: list[tuple[int, Any]], redactor: Any) -> str:
    """The request a later refusal answered, as it reads: the last one sent
    before it. Empty when none was, or the traffic cannot be shown."""
    request = next((e for at, e in reversed(sends) if at < position), None)
    if request is None or redactor is None:
        return ""
    return traffic_text(serialize_entry(request, redactor), REQUEST_TEXT_CHARS)


def unmatched_replies(trial: "CommandTrial", observer: Any) -> dict[str, Any]:
    """The replies in the window no response rule matched: how many, and the
    first few as they read."""
    events = []
    if observer is not None:
        events = [e for e in trial.events_in(observer) if e.kind == "unmatched_response"]
    return {
        "unmatched": len(events),
        "unmatched_examples": [
            str(e.detail.get("text", ""))[:UNMATCHED_EXAMPLE_CHARS]
            for e in events[:UNMATCHED_EXAMPLES]
        ],
    }
