"""THE guard each of a YAML driver's reply rules runs under.

One reply is read by several rules: every json rule whose keys the body
carries, the first text rule that matches (and the after_json rules after a
json read), one OSC rule for each message of a bundle. Unguarded, a rule that
raises part-way through (a value too large for its declared type, a throttle
that cannot be timed) ends the reply there: what the rules before it wrote
stays, the rules after it never run, the reply is not counted as read, and
the poll loop's unread-replies check then says the driver reads none of what
the device sends. The raise itself reaches only the transport's log line,
which names no device, or on HTTP fails the request the device answered.

The rules worth knowing before changing anything here:

* A rule that raises is skipped alone. What it wrote before the raise stays,
  the next rule still runs, and the reply counts as read: a rule matched it,
  so it is in this driver's language. Each form counts after the rule has
  run, from what the rule says it read, and reads a raise as read; the one
  matched reply that is not read is a rule whose only work was children it
  could place none of (``child_placement.reply_read``).
* The failing rule is reported by what its author wrote (its ``match:``, its
  ``address:``, or what a json rule sets), with the device's secrets masked
  (a ``match:`` is filled from the config): in the log as a warning naming
  the device, on ``device.error.<id>`` with the same sentence and
  ``source: response_rule``, both once per rule for the life of the driver (a
  rule that fails on one reply fails on every reply like it), and to a
  contract observer every time (``rule_failed``), so a device audit and the
  Driver Builder's test count it.
* The ``device.error`` goes out as a task of its own. The bus waits for its
  handlers, a script's included, and a reply's reading must not wait on
  them: the transport reads the device's next message only after this one
  (``transport/handler_runs.py``).
* A strict-mode contract violation (``UndeclaredStateError``) is not a rule
  failure: it raises on through, as strict mode exists to make it.

``ReplyGuard`` is a mixin ``ConfigurableDriver`` inherits, so every name
stays on the driver object. From its host it needs ``device_id``, ``events``
and ``_observe``. The rules read under it (``text_replies``,
``json_replies``, ``osc_wire``) enter ``_rule_guard``.
"""

from __future__ import annotations

import asyncio
from types import TracebackType
from typing import Any, Callable

from openavc.drivers.contract_events import RULE_FAILED
from openavc.drivers.state_writes import UndeclaredStateError
from openavc.utils.log_redaction import get_secret_registry
from openavc.utils.logger import get_logger

# These lines have always logged under the YAML driver runtime's name, which
# is where a device's log view files them.
log = get_logger("openavc.drivers.configurable")

# The three forms a reply rule takes, as the rule tables hold them.
TEXT = "text"
JSON = "json"
OSC = "osc"

# The ``source`` of the ``device.error`` a failing rule raises.
RESPONSE_RULE_SOURCE = "response_rule"


def _json_rule_text(rule: Any) -> str:
    """A json rule by what it sets: its state variables, then the child
    types it routes to."""
    mappings, _throttle, _require, _condition, child_mappings = rule
    names = [m["state"] for m in mappings if isinstance(m, dict) and m.get("state")]
    names += [cm["type"] for cm in child_mappings]
    return f"json rule setting {', '.join(dict.fromkeys(names))}"


_RULE_TEXT: dict[str, Callable[[Any], str]] = {
    TEXT: lambda rule: f"match: {rule[0].pattern}",
    OSC: lambda rule: f"address: {rule[0]}",
    JSON: _json_rule_text,
}


def rule_text(form: str, rule: Any) -> str:
    """A compiled rule as its author wrote it, to name it to a person."""
    return _RULE_TEXT[form](rule)


def error_text(exc: BaseException) -> str:
    """An exception as a sentence part: its type, and its text when it has
    one (an empty one would say nothing)."""
    name = type(exc).__name__
    text = str(exc)
    return f"{name}: {text}" if text else name


def rule_failure_sentence(rule: str, error: str) -> str:
    """What the log and ``device.error`` say about a rule that failed."""
    return (
        f'Response rule "{rule}" failed on a reply and was skipped: {error}. '
        f"Check this rule in the driver."
    )


class _RuleGuard:
    """One rule's run: a raise is reported to the driver and goes no further,
    except a strict-mode contract violation."""

    __slots__ = ("_driver", "_form", "_rule", "_reply")

    def __init__(self, driver: ReplyGuard, form: str, rule: Any, reply: dict[str, Any]) -> None:
        self._driver = driver
        self._form = form
        self._rule = rule
        self._reply = reply

    def __enter__(self) -> None:
        return None

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        if not isinstance(exc, Exception) or isinstance(exc, UndeclaredStateError):
            return False
        self._driver._rule_failed(self._form, self._rule, exc, self._reply)
        return True


class ReplyGuard:
    """Run a reply rule under its own guard and report one that fails, for
    ``ConfigurableDriver``."""

    # Supplied by BaseDriver; declared here for the methods.
    device_id: str
    events: Any
    _observe: Callable[..., None]

    def _init_reply_guard(self) -> None:
        # Each failing rule reported so far, by its text: reported once.
        self._failed_rules: set[str] = set()
        # The device.error emits still running (held so none is collected).
        self._rule_failure_emits: set[asyncio.Task[None]] = set()

    def _rule_guard(self, form: str, rule: Any, **reply: Any) -> _RuleGuard:
        """``with self._rule_guard(form, rule, text=...):`` around one rule's
        run. ``reply`` is what the observer is told of the reply it read."""
        return _RuleGuard(self, form, rule, reply)

    def _rule_failed(
        self, form: str, rule: Any, exc: Exception, reply: dict[str, Any],
    ) -> None:
        secrets = get_secret_registry()
        where = secrets.redact_for(self.device_id, rule_text(form, rule))
        error = secrets.redact_for(self.device_id, error_text(exc))
        self._observe(RULE_FAILED, rule=where, error=error, **reply)
        if where in self._failed_rules:
            log.debug(f"[{self.device_id}] Response rule {where!r} failed again: {error}")
            return
        self._failed_rules.add(where)
        sentence = rule_failure_sentence(where, error)
        log.warning(f"[{self.device_id}] {sentence}", exc_info=exc)
        self._emit_rule_failure(sentence)

    def _emit_rule_failure(self, sentence: str) -> None:
        """``device.error`` for a failing rule, as a task of its own; nothing
        when no loop runs (a synchronous caller: the log has it)."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(self._announce_rule_failure(sentence))
        self._rule_failure_emits.add(task)
        task.add_done_callback(self._rule_failure_emits.discard)

    async def _announce_rule_failure(self, sentence: str) -> None:
        try:
            await self.events.emit(
                f"device.error.{self.device_id}",
                {"device_id": self.device_id, "error": sentence,
                 "source": RESPONSE_RULE_SOURCE},
            )
        except Exception:
            log.exception(f"[{self.device_id}] Failed to emit device.error")
