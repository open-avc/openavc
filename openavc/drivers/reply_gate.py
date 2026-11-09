"""A YAML reply rule's ``only_when:`` gate: whether the rule may read this
reply now, judged against the state store.

The rules worth knowing before changing anything here:

* **The gate** is one ``{key, operator, value}`` condition, or an ``all:``
  (every one holds) or ``any:`` (one holds) list of them: the shape a driver
  action's ``visible_when`` takes. ``operator`` defaults to ``eq`` and is
  judged by ``eval_operator``, the macro and trigger conditions' own.
* **A key** that is a bare name is this device's own state variable, which
  is what an author writing ``key: examx_mode`` means. One that already
  carries a namespace (``var.``, ``device.<other>.`` and the rest of
  ``VALID_KEY_PREFIXES``) is read as written, after ``$id`` becomes this
  device's id, as in ``visible_when``.
* **A malformed gate opens rather than closes**: a condition with no key, a
  group whose items are none of them objects. A driver whose condition is
  nonsense reports what it always reported, rather than going silent on the
  one state variable somebody was watching. A group that is empty or not a
  list is not read at all, so the next one (or the gate as a single
  condition) decides; ``all`` is read before ``any``.
* **No gate** opens, and is answered first: it is nearly every rule.

``ReplyGate`` is a mixin ``ConfigurableDriver`` inherits, so every name
stays on the driver object; the regex, JSON and OSC reply rules each ask
``_rule_applies``. A poll or start-up entry's ``when_state:`` is another gate
with a shape of its own (``query_entries``).
"""

from __future__ import annotations

from typing import Any

from openavc.core.condition_eval import eval_operator
from openavc.core.state_store import VALID_KEY_PREFIXES


class ReplyGate:
    """The ``only_when:`` gate, for ``ConfigurableDriver``."""

    # Supplied by BaseDriver; declared here for the methods.
    device_id: str
    state: Any

    def _condition_key(self, key: str) -> str:
        """One condition key, as a state-store key."""
        resolved = key.replace("$id", self.device_id)
        if resolved.startswith(VALID_KEY_PREFIXES):
            return resolved
        return f"device.{self.device_id}.{resolved}"

    def _condition_holds(self, cond: dict[str, Any]) -> bool:
        """One ``{key, operator, value}`` leaf."""
        key = cond.get("key")
        if not isinstance(key, str) or not key:
            return True
        actual = self.state.get(self._condition_key(key))
        return eval_operator(
            str(cond.get("operator") or "eq"), actual, cond.get("value")
        )

    def _rule_applies(self, condition: dict[str, Any] | None) -> bool:
        """Whether a response rule's ``only_when:`` gate is open right now."""
        if not condition:
            return True
        for group_key, require_all in (("all", True), ("any", False)):
            group = condition.get(group_key)
            if isinstance(group, list) and group:
                results = [
                    self._condition_holds(c)
                    for c in group
                    if isinstance(c, dict)
                ]
                if not results:
                    return True
                return all(results) if require_all else any(results)
        return self._condition_holds(condition)
