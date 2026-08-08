"""
Shared regex-safety validation.

Rejects invalid or catastrophic-backtracking (ReDoS) patterns before they run
against attacker-influenced input on a synchronous path. Two callers share this
check:

    - the driver loader, where ``responses[]`` and ``auth:`` patterns are
      matched against raw pre-auth device bytes, and
    - the cloud alert monitor, where the ``matches`` operator runs a
      cloud-pushed pattern on every state change, inline on the event loop.

A ReDoS pattern in either place blocks its thread/loop, so both validate
patterns up front with the same detector. Structural detection is the primary
check; a short, time-boxed empirical probe is a secondary backstop for shapes
the structural rules don't model.
"""

from __future__ import annotations

import re
import threading
from typing import Any

# A group close followed by a quantifier (`)+`, `)*`, `)?`, `){`). Used only to
# decide whether to run the (secondary) empirical probe — the structural
# detectors below are the primary, reliable check.
_REDOS_RISK_RE = re.compile(r"\)[*+?{]")

# Nested quantifier where a single quantified atom (a char, escape, or class)
# sits inside a quantified group: (.+)+, (\d+)*, ([a-z]+)+. These are the
# textbook exponential-backtracking shapes. The atom restriction avoids
# flagging benign repeats like (In\d+)+ whose literal prefix disambiguates.
_NESTED_QUANT_RE = re.compile(r"\((?:\\.|\[[^\]]*\]|[^()\[\]\\*+?])[*+]\)[*+]")

# A flat alternation group immediately followed by + or *. Captures the
# alternation body so we can check for the overlap that makes (a|a)+ and
# (foo|foobar)* blow up — duplicate or prefix-overlapping branches.
_ALT_QUANT_RE = re.compile(r"\(([^()]*\|[^()]*)\)[*+]")

# Short non-matching inputs for the secondary empirical probe. A safe regex
# finishes these instantly; some backtracking shapes the structural checks
# don't model will still blow past the threshold here.
_REDOS_PROBE_STRINGS = ("a" * 25, "x" * 25 + "!", "0123456789" * 3, "\t \n" * 10)


def redos_structural_reason(pattern: str) -> str | None:
    """Detect the catastrophic-backtracking shapes by structure (reliable)."""
    if _NESTED_QUANT_RE.search(pattern):
        return "has a nested quantifier"
    for m in _ALT_QUANT_RE.finditer(pattern):
        alts = m.group(1).split("|")
        if len(set(alts)) != len(alts):
            return "has duplicate alternatives under a quantifier"
        for a in alts:
            for b in alts:
                if a and a != b and b.startswith(a):
                    return "has overlapping alternatives under a quantifier"
    return None


def _regex_search_exceeds(compiled: "re.Pattern[str]", test_str: str, budget: float) -> bool:
    """Run ``compiled.search(test_str)`` in a worker thread, time-boxed.

    Returns True if the search has not returned within ``budget`` seconds. The
    worker is a daemon and the probe strings are short and fixed, so a runaway
    search terminates on its own shortly after; the point is that the
    *validation call itself* is bounded and never freezes the request thread.
    The old in-line timing measured elapsed time only AFTER ``search`` returned,
    so it could not bound the very operation it was meant to police — a probe
    string that backtracked for ~2s blocked the caller for that whole time
    before the threshold was even checked.
    """
    done = threading.Event()

    def _run() -> None:
        try:
            compiled.search(test_str)
        finally:
            done.set()

    threading.Thread(target=_run, daemon=True).start()
    return not done.wait(budget)


def regex_safety_error(label: str, pattern: Any) -> str | None:
    """Compile ``pattern`` and check it for catastrophic backtracking.

    Returns an error string (prefixed with ``label``) when the pattern is
    invalid or shows exponential blow-up; ``None`` when it looks safe.
    Structural detection is the primary check; the empirical probe is a
    secondary backstop for shapes the structural rules don't model.
    """
    if not isinstance(pattern, str):
        return f"{label}: pattern must be a string, got {type(pattern).__name__}"
    try:
        compiled = re.compile(pattern)
    except re.error as e:
        return f"{label}: invalid regex '{pattern}': {e}"

    reason = redos_structural_reason(pattern)
    if reason is None and _REDOS_RISK_RE.search(pattern):
        for test_str in _REDOS_PROBE_STRINGS:
            if _regex_search_exceeds(compiled, test_str, 0.1):
                reason = "shows runaway backtracking on a short input"
                break

    if reason:
        return (
            f"{label}: regex '{pattern}' {reason}; this can cause catastrophic "
            f"backtracking against hostile input"
        )
    return None
