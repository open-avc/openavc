"""Whether a YAML driver's HTTP username and password are being refused.

The rules worth knowing before changing anything here:

* **Every refused request is a failed login on the device**, and devices
  lock an account, or the controller's address, after a few. So the verdict
  comes as soon as the evidence allows, as the typed ``auth_failed``, and
  nothing more is sent: ``BaseDriver`` drops the connection on it at once and
  then waits for the credentials to change.
* **One refusal is not a verdict.** An account is often refused an endpoint
  or two it may not read while the password is right (401, 403, or 407 from
  a proxy in front of the device). It is settled three ways:

  1. a query that answered on this connection is now refused 401 or 407: the
     device stopped accepting the credential (a 403 there names a page the
     account may no longer read, and is left to 3);
  2. the ``_REFUSALS_BEFORE_ANY_ANSWER``-th distinct query is refused with
     nothing answered yet: the device never accepted it;
  3. a poll cycle whose every counted request was refused. This catches a
     list shorter than 2's count, and a device that answers a changed
     password with 403.

  A query refused from the start once something else has answered is a page
  the account may not read: it never counts, in a cycle's tally either, so a
  cycle that sent only such pages is no verdict.
* **What answered on the last connection proves nothing about this one**:
  the tally starts again at every connect.

``HttpCredential`` is a mixin ``ConfigurableDriver`` inherits, so every name
stays on the driver object. The host calls ``_init_http_credential()`` in
``__init__`` and ``_reset_http_credential()`` before each dial, hands every
answer ``_dispatch_query`` gets to ``_judge_http_answer``, and brackets a poll
with ``_open_http_cycle`` and ``_close_http_cycle``, calling
``_drop_http_cycle`` when a line fails and ends it. That is start-up, the
poll, and a response rule's ``then_query`` lines, which send through
``_dispatch_query`` too: one sent while a poll is under way counts in that
poll's tally, and a refusal it settles ends only its own round (the poll
that follows judges the connection).
"""

from __future__ import annotations

from typing import Any

from openavc.drivers.base import ConnectionFaultError
from openavc.utils.logger import get_logger

# These lines have always logged under the YAML driver runtime's name, which
# the IDE's log view shows as their source.
log = get_logger("openavc.drivers.configurable")

# The statuses that refuse a request's credentials: 401 from the device, 407
# from a proxy in front of it, 403 for the devices that answer a bad login
# with Forbidden rather than Unauthorized.
_AUTH_REJECT_STATUS = frozenset({401, 403, 407})

# The two of those that say the credential itself did not authenticate.
_CREDENTIAL_REFUSED_STATUS = frozenset({401, 407})

# Distinct queries refused, with nothing yet answered on the connection, that
# settle a wrong password. Every refused request counts toward a device's
# lockout, so this is kept low; it is above one because an account is often
# refused an endpoint or two it may not read while the password is right, and
# those may come first in a driver's start-up or poll list.
_REFUSALS_BEFORE_ANY_ANSWER = 3

# What the device card says when rule 2 or 3 settles it.
_REFUSED = (
    "The device refused the username and password. "
    "Check them in the device settings."
)


class HttpCredential:
    """The HTTP credential's verdict, for ``ConfigurableDriver``."""

    # Supplied by BaseDriver; declared here for the methods.
    device_id: str

    def _init_http_credential(self) -> None:
        # The queries that answered on this connection, the distinct queries
        # refused while none had, and the poll cycle in flight as
        # [requests, refusals] (None outside one).
        self._http_answered: set[str] = set()
        self._http_refused_unanswered: set[str] = set()
        self._http_cycle: list[int] | None = None

    def _reset_http_credential(self) -> None:
        """A new connection: what answered on the last one proves nothing
        about the credential on this one."""
        self._http_answered = set()
        self._http_refused_unanswered = set()

    def _open_http_cycle(self, is_http: bool) -> None:
        """Start counting a poll cycle's answers (an HTTP poll only)."""
        self._http_cycle = [0, 0] if is_http else None

    def _drop_http_cycle(self) -> None:
        """A line failed and the cycle ends: its count is moot."""
        self._http_cycle = None

    def _close_http_cycle(self) -> None:
        """End the cycle; raise ``auth_failed`` when every request it counted
        was refused (rule 3)."""
        cycle = self._http_cycle
        self._http_cycle = None
        if cycle is None:
            return
        requests, refusals = cycle
        if requests > 0 and refusals == requests:
            raise ConnectionFaultError(_REFUSED, code="auth_failed")

    def _count_in_cycle(self, refused: bool) -> None:
        cycle = self._http_cycle
        if cycle is not None:
            cycle[0] += 1
            if refused:
                cycle[1] += 1

    def _judge_http_answer(self, query: str, response: Any) -> None:
        """Weigh one start-up or poll answer against the credential, raising
        ``auth_failed`` the moment rule 1 or 2 settles it."""
        status = getattr(response, "status_code", None)
        if not isinstance(status, int):
            return
        if status not in _AUTH_REJECT_STATUS:
            self._http_answered.add(query)
            self._count_in_cycle(refused=False)
            return
        if query in self._http_answered:
            self._count_in_cycle(refused=True)
            if status in _CREDENTIAL_REFUSED_STATUS:
                self._stopped_accepting(query, status)
            return
        if self._http_answered:
            log.debug(
                f"[{self.device_id}] '{query}' refused (HTTP {status}); "
                f"other queries answer, so the account may not read it"
            )
            return
        self._count_in_cycle(refused=True)
        self._http_refused_unanswered.add(query)
        if len(self._http_refused_unanswered) >= _REFUSALS_BEFORE_ANY_ANSWER:
            log.warning(
                f"[{self.device_id}] {len(self._http_refused_unanswered)} "
                f"queries refused and none answered; dropping the connection"
            )
            raise ConnectionFaultError(_REFUSED, code="auth_failed")

    def _stopped_accepting(self, query: str, status: int) -> None:
        """Rule 1: a query that answered on this connection is refused now."""
        log.warning(
            f"[{self.device_id}] '{query}' answered earlier on this "
            f"connection and is now refused (HTTP {status}); dropping "
            f"the connection"
        )
        raise ConnectionFaultError(
            "The device stopped accepting the username and password. "
            "Check them in the device settings.",
            code="auth_failed",
        )
