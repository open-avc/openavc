"""Whether the driver reads what a hand-written simulator handler answers: the
simulator validator's ``response_parsing`` check.

A text handler in ``simulator.command_handlers`` answers with a ``respond:``
template, or a script ``handler:`` whose ``respond()`` calls pass it literal
text. This reads each reply as source, never by running the handler: a
template's ``{state.key}`` refs are filled from ``initial_state``, the reply is
split into the lines the driver reads, and each line is asked of the driver's
response rules (``reply_reading.ReplyRules.reads``).

A reply no rule reads is a finding only when the handler answers a query the
driver polls (``send_coverage.query_answering_handlers``) and no reply it sends
is read: the state behind that query never updates. Every other unread reply
has the shape of a write acknowledgement and is counted once, with examples. A
reply this cannot resolve (a capture group ref, an f-string, a concatenation)
is evidence neither way, so a handler with one is never said to answer with
nothing the driver reads.

This reads a handler differently from the simulator in two ways worth knowing
before changing either:

- A script handler's code is dedented before it is compiled here; the
  simulator (``yaml_auto``) and ``handler_checks.check_handler_code`` compile it
  as written, so an indented body is read here and skipped there.
- ``{state.key}`` is filled with ``str()`` of the key's initial value, where
  the simulator's ``template_handler.fill`` fills the capture groups first and
  puts in the device's own number for the value.
"""

from __future__ import annotations

import ast
import dataclasses
import re
import textwrap
from collections.abc import Iterator

from openavc.simulator.driver_sends import effective_config
from openavc.simulator.reply_reading import ReplyRules
from openavc.simulator.send_coverage import query_answering_handlers
from openavc.simulator.validation_result import ValidationResult, examples

# How many distinct acknowledgement-shaped responses the grouped info names
# before it stops listing. The count is always exact; the examples are there
# to recognise the shape, not to enumerate it.
_ACK_EXAMPLES = 4


def check_response_parsing(
    result: ValidationResult,
    sim_handlers: list,
    sim_initial: dict,
    driver_def: dict,
) -> None:
    """Check that simulator responses match driver response patterns.

    This is the round-trip check: we simulate what the handler would respond
    with, then verify the response matches at least one response pattern.

    A reply that matches nothing is only a *finding* when the driver asked a
    question and got no answer it can read. On a protocol that acknowledges
    every write — ``<cmd> ACK`` / ``<cmd> NAK <code>``, which is most of the
    AV serial and TCP world — the acknowledgement carries no state by
    construction, so no response rule exists or should. Warning on each one
    buried the real findings: the Audio-Technica mixers reported 40-odd
    apiece, and 324 of the 597 warnings over the whole shipped corpus were
    this one check firing on correct drivers. Same reasoning as the
    ``command_coverage`` downgrade — a check that fires permanently on
    correct drivers trains an author to skim the channel that might say
    something real.

    So the finding is stated per *handler*, in terms of what the driver does
    rather than what the reply looks like — no keyword list, no per-protocol
    allowance for "ACK":

    * The handler answers a ``polling.queries`` entry (or an ``on_connect``
      entry that declares ``query_for``), and **none** of its replies matches
      a response rule. The driver polls that query to learn a value and can
      read nothing that comes back: the state variable behind it stays at its
      startup value forever. Warning.
    * Anything else: a reply to a write, or an error branch of a query
      handler whose real answer does parse. Counted and shown once, so "not
      checked" is still visible rather than implied.

    ``on_connect`` is deliberately not treated as a query list. It is a
    bring-up sequence that mixes reads with arming writes — arming push
    notifications, setting a mode — and those writes are acknowledged like
    any other. An entry that declares ``query_for`` has said it is a read.
    """
    responses = driver_def.get("responses", [])
    if not responses:
        return

    rules = ReplyRules.of(responses, effective_config(driver_def), driver_def)
    answering = query_answering_handlers(sim_handlers, driver_def)
    acknowledgements: list[str] = []

    for handler_def in _text_handlers(sim_handlers):
        replies = _read_handler_replies(handler_def, sim_initial, rules)
        if not replies.unread:
            continue
        if id(handler_def) in answering and replies.none_read:
            _warn_unread_answer(result, handler_def, replies.unread[0])
        else:
            _add_unseen(acknowledgements, replies.unread)

    if acknowledgements:
        _report_acknowledgements(result, acknowledgements)


def _text_handlers(sim_handlers: list) -> Iterator[dict]:
    """The handlers that answer with text lines: OSC handlers (``address:``)
    respond with (address, args) tuples, and a malformed entry is
    ``handler_checks.check_handler_code``'s to report."""
    for handler_def in sim_handlers:
        if isinstance(handler_def, dict) and "address" not in handler_def:
            yield handler_def


def _add_unseen(seen: list[str], items: list[str]) -> None:
    """Add each item ``seen`` does not hold yet, in order."""
    for item in items:
        if item not in seen:
            seen.append(item)


# A placeholder still in a reply after its {state.key} refs are filled: a
# capture group ref ({1}, {2}) on a respond: template.
_CAPTURE_REF = re.compile(r"\{\d+\}")


def _left_unresolved(response_text: str, scripted: bool) -> bool:
    """A reply still holding a capture group ref, or (a script handler's
    literal) any ``{...}``, which this cannot resolve."""
    if _CAPTURE_REF.search(response_text):
        return True
    return scripted and _has_braces(response_text)


def _has_braces(text: str) -> bool:
    return "{" in text and "}" in text


@dataclasses.dataclass
class _HandlerReplies:
    """What one text handler answers, as the driver reads it."""

    #: Each reply line no response rule reads, once, in the order sent.
    unread: list[str] = dataclasses.field(default_factory=list)
    #: Whether a response rule reads any line it sends.
    any_read: bool = False
    #: Whether a reply could not be resolved here (built from a capture
    #: group, an f-string or a concatenation). The answer to a query is
    #: nearly always one of those, and it is not evidence either way.
    any_unresolvable: bool = False

    def take(self, response_text: str, rules: ReplyRules) -> None:
        """One resolved reply, split into the lines the driver reads."""
        for line in rules.lines(response_text):
            if rules.reads(line):
                self.any_read = True
            elif line not in self.unread:
                self.unread.append(line)

    @property
    def none_read(self) -> bool:
        """Whether "none of its replies parses" may be said of the handler:
        no reply is read, and none could not be resolved."""
        return not self.any_read and not self.any_unresolvable


def _reply_templates(handler_def: dict) -> tuple[list[str], bool]:
    """The reply templates a text handler sends, and whether one of them
    could not be read: a ``respond:`` template, else the literals a script
    ``handler:`` passes to ``respond()``, else none."""
    respond_template = handler_def.get("respond")
    handler_code = handler_def.get("handler")
    if respond_template:
        return [respond_template], False
    if handler_code:
        return _extract_respond_calls(handler_code)
    return [], False


def _read_handler_replies(
    handler_def: dict, sim_initial: dict, rules: ReplyRules
) -> _HandlerReplies:
    """Each reply a handler sends, its ``{state.key}`` refs filled from the
    initial state, split into the lines the driver reads and matched against
    its rules. A reply still holding a capture group ref, or (a script
    handler's literal) any ``{...}``, is unresolvable rather than read."""
    templates, unresolvable = _reply_templates(handler_def)
    replies = _HandlerReplies(any_unresolvable=unresolvable)
    scripted = bool(handler_def.get("handler"))
    for template in templates:
        response_text = _resolve_state_refs(template, sim_initial)
        if _left_unresolved(response_text, scripted):
            replies.any_unresolvable = True
        else:
            replies.take(response_text, rules)
    return replies


def _resolve_state_refs(template: str, state: dict) -> str:
    """Resolve {state.key} references in a template."""
    result = template
    for key, value in state.items():
        result = result.replace(f"{{state.{key}}}", str(value))
    return result


def _warn_unread_answer(result: ValidationResult, handler_def: dict, reply: str) -> None:
    """The finding: a handler answering a query the driver asks with nothing
    a response rule reads."""
    pattern_key = handler_def.get("receive") or handler_def.get("match", "?")
    result.warning(
        "response_parsing",
        f"Simulator answers a query this driver polls with a reply no "
        f"response rule matches: handler={pattern_key!r}, "
        f"response={reply!r}. Nothing reads that answer, so "
        f"the state behind the query never updates."
    )


def _report_acknowledgements(result: ValidationResult, acknowledgements: list[str]) -> None:
    """The replies no rule reads that answer no polled query, counted once:
    the shape of a write acknowledgement."""
    shown = examples([repr(a) for a in acknowledgements], _ACK_EXAMPLES, " (+{} more)")
    result.info(
        "response_parsing",
        f"{len(acknowledgements)} simulator reply/replies match no driver "
        f"response rule, none of them the only answer to a polled query — "
        f"the shape of a write acknowledgement, which carries no state: "
        f"{shown}. A polled query whose answer nothing parses warns "
        f"instead."
    )


def _extract_respond_calls(handler_code: str) -> tuple[list[str], bool]:
    """The literal strings an inline handler responds with.

    Returns ``(literals, unreadable)`` — ``unreadable`` is True when the
    handler responds with something this cannot evaluate: an f-string, a
    concatenation, a variable, or code that does not parse at all. Both
    halves matter. The literals are what gets matched against the driver's
    response rules; ``unreadable`` is what stops "none of this handler's
    replies parses" being said about a handler whose real answer simply
    could not be read.

    Parsed rather than pattern-matched, because a regex over the source finds
    the *first* closing quote: ``respond("(PWR!" + str(x) + "\\r")`` gave it
    ``(PWR!``, which then matched no response rule and warned — 14 of the
    corpus's warnings were that, all on drivers whose replies are fine.
    """
    tree = _compiled_tree(handler_code)
    if tree is None:
        return [], True
    literals: list[str] = []
    unreadable = False
    for arg in _respond_arguments(tree):
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            literals.append(arg.value)
        else:
            unreadable = True
    return literals, unreadable


def _compiled_tree(handler_code: str) -> ast.Module | None:
    """The handler's syntax tree, dedented, or None when it does not compile.

    compile(), not ast.parse(): a bare `return` parses but does not compile,
    and the simulator skips the whole handler when it does not compile.
    handler_checks.check_handler_code is what reports that; here it just means
    nothing can be read, which is not the same as nothing being answered.
    """
    code = textwrap.dedent(handler_code)
    try:
        compile(code, "<handler>", "exec")
        return ast.parse(code)
    except SyntaxError:
        return None


def _respond_arguments(tree: ast.Module) -> Iterator[ast.expr]:
    """The first positional argument of each ``respond(...)`` call, in the
    order ``ast.walk`` meets them; a call with none (``respond()``, or only
    keywords) is passed over."""
    for node in ast.walk(tree):
        if _calls_respond(node) and node.args:
            yield node.args[0]


def _calls_respond(node: ast.AST) -> bool:
    """A call of the bare name ``respond``; ``self.respond(...)`` is not one."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "respond"
    )
