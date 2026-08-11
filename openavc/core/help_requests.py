"""Asking for help from inside the room.

A macro step raises this. That is the whole product decision: nothing appears
on a panel that the programmer did not put there, so a client who does not want
a help button never gets one, and a client who does gets it with their own
label in their own place. The three ways in are a panel button's ``do``, a
trigger firing with nobody in the room, and a script -- and the trigger is the
one worth more than the button, because a room that has failed to power on
three times can ask before anyone thinks to.

**Local first, and honest about the rest.** The state keys and the local event
happen the instant the step runs, so a local macro can respond and the panel
reacts with no round trip. The cloud relay is a separate best-effort step: if
it fails the state says ``unreachable`` rather than staying at ``requested``,
because a panel claiming help is coming when nothing left the building is worse
than one saying it could not reach the cloud.

**Nothing is queued.** A help request that arrives forty minutes late arrives
after the class ended, and without the context that made it true.

**The cooldown is not an abuse guard.** A wall panel being leaned on is the
programmer's problem to gate, and Aaron settled that: they own the page. What
the cooldown defends against is a *trigger loop* -- "device offline" firing
every poll through a lecture -- which is an authoring mistake whose cost would
otherwise land on whoever is reading the mail.

The room learns whether anyone heard: acknowledging in the cloud comes back
down and lands in ``system.help_acknowledged_by``, so a page can say "Help
requested 9:02. Ben acknowledged 9:04." A button with no visible effect is a
black hole, and it gets pressed four more times.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING

from openavc.utils.logger import get_logger

if TYPE_CHECKING:
    from openavc.core.engine import Engine

log = get_logger(__name__)

# The state keys a panel binds to. One current request per instance: a room has
# one help state, and a list of them is a report, which lives in the cloud.
KEY_STATE = "system.help_state"
KEY_MESSAGE = "system.help_message"
KEY_REQUESTED_AT = "system.help_requested_at"
KEY_ACKNOWLEDGED_AT = "system.help_acknowledged_at"
KEY_ACKNOWLEDGED_BY = "system.help_acknowledged_by"

# system.help_state values.
IDLE = "idle"
REQUESTED = "requested"
ACKNOWLEDGED = "acknowledged"
UNREACHABLE = "unreachable"

# Every key this module owns, so startup can declare them and a panel binding
# never reads a key that does not exist yet.
ALL_KEYS = (
    KEY_STATE, KEY_MESSAGE, KEY_REQUESTED_AT,
    KEY_ACKNOWLEDGED_AT, KEY_ACKNOWLEDGED_BY,
)

DEFAULT_COOLDOWN_SECONDS = 300
DEFAULT_MESSAGE = "Someone asked for help"
MAX_MESSAGE_LEN = 500

# The event a local macro or script can listen for. Fires on every raise,
# including one the cloud never receives, because the local half is what the
# room can act on.
EVENT_RAISED = "help.requested"
EVENT_ACKNOWLEDGED = "help.acknowledged"


class HelpRequests:
    """Raises help requests and tracks the current one."""

    def __init__(self, engine: Engine):
        self._engine = engine
        self._last_raised_at: float = 0.0
        self._current_id: str = ""

    def declare_keys(self) -> None:
        """Publish the idle state at startup so a bound label draws something.

        Without this a panel built against these keys shows blanks until the
        first press, which reads as a broken binding rather than a quiet room.
        """
        state = self._engine.state
        state.set(KEY_STATE, IDLE)
        state.set(KEY_MESSAGE, None)
        state.set(KEY_REQUESTED_AT, None)
        state.set(KEY_ACKNOWLEDGED_AT, None)
        state.set(KEY_ACKNOWLEDGED_BY, None)

    async def raise_request(
        self,
        message: str = "",
        severity: str = "warning",
        cooldown: float | None = None,
    ) -> dict[str, Any]:
        """Ask for help. Returns what happened, for the caller's log.

        ``cooldown`` of 0 disables the guard, which a room that genuinely needs
        to shout twice should be able to do.
        """
        window = DEFAULT_COOLDOWN_SECONDS if cooldown is None else max(0.0, float(cooldown))
        now = time.monotonic()
        if window and self._last_raised_at and (now - self._last_raised_at) < window:
            remaining = int(window - (now - self._last_raised_at))
            log.info(
                "Help request suppressed: one was raised %ds ago and the "
                "cooldown has %ds left. Set cooldown to 0 on the step to "
                "always send.",
                int(now - self._last_raised_at), remaining,
            )
            return {"raised": False, "reason": "cooldown", "retry_in": remaining}

        self._last_raised_at = now
        text = (message or DEFAULT_MESSAGE).strip()[:MAX_MESSAGE_LEN]
        request_id = str(uuid.uuid4())
        self._current_id = request_id
        raised_at = datetime.now(timezone.utc).isoformat()

        state = self._engine.state
        state.set(KEY_STATE, REQUESTED)
        state.set(KEY_MESSAGE, text)
        state.set(KEY_REQUESTED_AT, raised_at)
        state.set(KEY_ACKNOWLEDGED_AT, None)
        state.set(KEY_ACKNOWLEDGED_BY, None)

        await self._engine.events.emit(EVENT_RAISED, {
            "help_id": request_id,
            "message": text,
            "severity": severity,
            "at": raised_at,
        })
        log.info("Help requested: %s", text)

        delivered = await self._relay(request_id, text, severity)
        if not delivered:
            # Only downgrade if nothing has moved on since -- an acknowledgement
            # that beat the relay's own reply must not be overwritten.
            if state.get(KEY_STATE) == REQUESTED and self._current_id == request_id:
                state.set(KEY_STATE, UNREACHABLE)

        return {"raised": True, "help_id": request_id, "delivered": delivered}

    async def _relay(self, request_id: str, message: str, severity: str) -> bool:
        """Hand the request to the cloud. Never raises; the room comes first."""
        agent = getattr(self._engine, "cloud_agent", None)
        if agent is None or not getattr(agent, "connected", False):
            log.warning(
                "Help request %s was raised locally but the cloud is not "
                "connected, so nobody outside the room has been told.",
                request_id,
            )
            return False
        try:
            from openavc.cloud.protocol import ALERT, build_alert_payload
            await agent.send_message(ALERT, build_alert_payload(
                alert_id=f"help:{request_id}",
                rule_id="help",
                severity=severity,
                category="help",
                device_id=None,
                message=message,
                detail={
                    "help_id": request_id,
                    "raised_by": "macro",
                    "project": getattr(self._engine.project, "name", "") or "",
                },
            ))
            return True
        except Exception:
            log.warning(
                "Help request %s could not be sent to the cloud.",
                request_id, exc_info=True,
            )
            return False

    async def handle_acknowledged(self, payload: dict[str, Any]) -> None:
        """A person in the cloud said they are on it. Tell the room.

        This is the half that makes the feature real rather than plumbing, and
        it is why the cloud gained a downstream message: acknowledgement lived
        only in the portal, and nobody in a lecture hall is reading a portal.
        """
        help_id = str(payload.get("help_id") or "")
        if self._current_id and help_id and help_id != self._current_id:
            log.debug("Acknowledgement for an older help request (%s), ignoring", help_id)
            return

        who = str(payload.get("acknowledged_by") or "").strip() or "OpenAVC Cloud"
        at = str(payload.get("acknowledged_at") or "") or datetime.now(timezone.utc).isoformat()

        state = self._engine.state
        state.set(KEY_STATE, ACKNOWLEDGED)
        state.set(KEY_ACKNOWLEDGED_BY, who)
        state.set(KEY_ACKNOWLEDGED_AT, at)
        await self._engine.events.emit(EVENT_ACKNOWLEDGED, {
            "help_id": help_id, "acknowledged_by": who, "acknowledged_at": at,
        })
        log.info("Help request acknowledged by %s", who)

    def clear(self) -> None:
        """Put the room back to idle. The programmer decides when that is."""
        self._current_id = ""
        state = self._engine.state
        state.set(KEY_STATE, IDLE)
        state.set(KEY_MESSAGE, None)
        state.set(KEY_REQUESTED_AT, None)
        state.set(KEY_ACKNOWLEDGED_AT, None)
        state.set(KEY_ACKNOWLEDGED_BY, None)
