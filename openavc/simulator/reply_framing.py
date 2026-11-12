"""Whether every reply the simulator sends ends where the driver splits the
stream: the simulator validator's ``reply_framing`` check.

A driver on a delimited transport reads only what its delimiter completes, so
a reply without one reaches it as nothing at all. Unlike the validator's other
reply checks, this one does not read the definition: it builds a real simulator
from it (``yaml_auto.YAMLAutoSimulator``, imported only when a delimiter makes
the check apply), sends it the lines the driver sends (``driver_sends.
probed_lines``) and judges the bytes that come back. A simulator that cannot be
built, or a probe it raises on, is the other checks' finding, not this one's;
its log is muted while it runs.

Each reply falls one of three ways: it ends with the delimiter (counted); it
carries no delimiter at all (the error: a frame that never completes); or its
lines end with the delimiter and text trails the last one, which is a prompt
the device prints after each reply when every reply also opens with the
delimiter and a response rule reads the prompt alone, and otherwise a line read
one reply late.
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Callable
from typing import Any

from openavc.simulator.driver_sends import effective_config, probed_lines
from openavc.simulator.reply_reading import ReplyRules, frame_delimiter
from openavc.simulator.validation_result import ValidationResult, examples

_FRAMING_EXAMPLES = 3


def check_reply_framing(
    result: ValidationResult,
    commands: dict,
    queries: list,
    driver_def: dict,
) -> None:
    """Check every simulator reply ends where the driver splits the stream.

    A driver on a delimited transport reads replies by splitting incoming
    bytes on its ``delimiter``. A reply that arrives without one never
    completes a frame, so the driver parses **nothing at all** — no state, no
    liveness answer — while the simulator, the connection, and every other
    check of the validator look correct. That is the whole failure: the simulator
    is right about the protocol and unreadable anyway.

    Which replies carry it is not uniform, and that is the trap. The simulator
    appends the delimiter to the handlers it generates from ``commands`` /
    ``responses``, to a query handler's answer, and to every push it sends. The
    two reply forms an author writes by hand — an explicit ``respond:``
    template and a script ``handler:`` calling ``respond()`` — are returned
    **verbatim**, so on those the terminator is theirs to add. No other check
    can see it missing, because a reply that never arrives contradicts no
    response rule: ``response_parsing`` compares reply text against the
    driver's patterns, and a frame that never completes has no text to compare.

    A reply whose lines end in the delimiter and which trails text after the
    last one is read, all but that text, and is judged apart
    (``_report_trailing_text``): a prompt the device prints after each reply
    is its protocol, anything else is a line read one reply late.

    So this check asks the simulator instead of reading it. It feeds the
    driver's own liveness, poll and command strings to a real simulator built
    from this definition and looks at the bytes that come back — which is also
    why it sees replies the page cannot: one built from a capture group or an
    f-string is unresolvable as source and perfectly concrete as output.
    """
    delimiter = frame_delimiter(driver_def)
    if delimiter is None:
        return
    raw_delimiter = driver_def.get("delimiter")

    # Built lazily: importing the simulator pulls in its server bases, and a
    # caller who only wants the other checks should not pay for them.
    try:
        from openavc.simulator.yaml_auto import YAMLAutoSimulator
    except Exception:  # pragma: no cover - the simulator package is shipped
        return

    probes = probed_lines(commands, queries, driver_def)
    if not probes:
        return
    heard = _ask_the_simulator(
        lambda: YAMLAutoSimulator(
            driver_def.get("id", result.driver_id), {}, driver_def=driver_def
        ),
        probes,
        delimiter,
    )
    if heard is not None:
        _report_framing(result, heard, raw_delimiter, driver_def)


@dataclasses.dataclass
class _Heard:
    """What the simulator sent back to the probes, judged against the
    delimiter."""

    #: Probes answered with something.
    replies: int = 0
    #: Each reply with no delimiter in it at all, as an example.
    unframed: list[str] = dataclasses.field(default_factory=list)
    #: Each reply whose lines end in the delimiter but which carries something
    #: after the last one: (example, the trailing text, whether the reply
    #: also opens with the delimiter).
    trailing: list[tuple[str, bytes, bool]] = dataclasses.field(default_factory=list)

    def take(self, probe: str, reply: bytes, terminator: bytes) -> None:
        """One probe's non-empty reply."""
        self.replies += 1
        if reply.endswith(terminator):
            return
        example = f"{probe.strip()!r} -> {reply[:40]!r}"
        if terminator in reply:
            self.trailing.append(
                (example, reply.rsplit(terminator, 1)[1], reply.startswith(terminator))
            )
        else:
            self.unframed.append(example)


def _ask_the_simulator(
    build: Callable[[], Any], probes: list[str], delimiter: str,
) -> _Heard | None:
    """Each probe's reply from a simulator ``build`` makes, or None when it
    cannot be built (an unbuildable simulator is the other checks' finding).

    The simulator's log is muted while it runs. A handler that raises logs a
    traceback, and a driver whose sample values do not suit every command has
    a few. They are ``handler_syntax``'s to report, not this check's, and a
    wall of them would bury its finding.
    """
    sim_log = logging.getLogger("openavc.simulator.yaml_auto")
    previous_level = sim_log.level
    sim_log.setLevel(logging.CRITICAL)
    try:
        try:
            sim = build()
        except Exception:
            return None
        terminator = delimiter.encode()
        heard = _Heard()
        for probe in probes:
            try:
                reply = sim.handle_command(probe.encode())
            except Exception:
                continue
            if reply:
                heard.take(probe, reply, terminator)
        return heard
    finally:
        sim_log.setLevel(previous_level)


def _report_framing(
    result: ValidationResult, heard: _Heard, raw_delimiter: str, driver_def: dict,
) -> None:
    """Replies that trail text after their last delimiter are judged apart;
    replies with no delimiter at all are the error; otherwise, when every
    reply ends in it, the count is noted."""
    if heard.trailing:
        _report_trailing_text(result, heard.trailing, raw_delimiter, driver_def)
    if heard.unframed:
        _report_unframed(result, heard, raw_delimiter)
    elif heard.replies and not heard.trailing:
        replies = heard.replies
        result.info(
            "reply_framing",
            f"{replies} simulator repl{'y' if replies == 1 else 'ies'} checked, "
            f"all terminated with {raw_delimiter!r}."
        )


def _report_unframed(result: ValidationResult, heard: _Heard, raw_delimiter: str) -> None:
    """The error: replies that never complete a frame."""
    unframed = heard.unframed
    shown = examples(unframed, _FRAMING_EXAMPLES, "; +{} more", "; ")
    result.error(
        "reply_framing",
        f"{len(unframed)} of {heard.replies} simulator replies come back without the "
        f"{raw_delimiter!r} this driver splits incoming bytes on, so the driver "
        f"never completes a frame and reads nothing they carry: {shown}. "
        f"A reply written by hand — a respond: template or a respond() call in "
        f"a handler — is sent verbatim, so end it with the delimiter; only the "
        f"handlers the simulator generates are terminated for you."
    )


def _report_trailing_text(
    result: ValidationResult,
    trailing: list[tuple[str, bytes, bool]],
    raw_delimiter: str,
    driver_def: dict,
) -> None:
    """Judge replies that end with text after their last delimiter.

    Every line before that text is read at once; the text itself waits for
    the next delimiter. A device that prints a prompt after each reply
    (``volume=55`` CR LF ``>``) does exactly this, and when every reply also
    opens with the delimiter, the next reply completes the prompt as a line of
    its own, which a response rule can read (or acknowledge with ``set: {}``).
    That is the device's protocol, so it is noted, not flagged. Anything else
    is a reply line that lost its delimiter: it is read one reply late, joined
    to the start of the next one.
    """
    rules = ReplyRules.regex_only(
        driver_def.get("responses") or [], effective_config(driver_def)
    )
    shown = examples([example for example, _, _ in trailing], _FRAMING_EXAMPLES, "; +{} more", "; ")
    texts = sorted({text.decode("utf-8", errors="replace").strip() for _, text, _ in trailing})

    def read_alone(text: bytes) -> bool:
        line = text.decode("utf-8", errors="replace").strip()
        return rules.matches_a_pattern(line)

    if all(opens and read_alone(text) for _, text, opens in trailing):
        result.info(
            "reply_framing",
            f"{len(trailing)} simulator repl{'y ends' if len(trailing) == 1 else 'ies end'} "
            f"with {', '.join(repr(t) for t in texts)} after the last {raw_delimiter!r}, "
            f"as a device that prints a prompt after each reply does; every reply "
            f"opens with {raw_delimiter!r}, so the next one completes it as a line "
            f"a response rule reads: {shown}."
        )
        return
    result.warning(
        "reply_framing",
        f"{len(trailing)} simulator repl{'y ends' if len(trailing) == 1 else 'ies end'} "
        f"with text after the last {raw_delimiter!r}, which reaches the driver only "
        f"when the next reply arrives, joined to its first line: {shown}. End the "
        f"reply with the delimiter, or, for a prompt the device prints after each "
        f"reply, open every reply with the delimiter and give the prompt a "
        f"response rule of its own."
    )
