"""Whether the simulator uses a driver's hand-written ``command_handlers`` the
way they are written.

The simulator reads them in ``yaml_auto._read_section_handlers``: an entry
with ``address`` and ``handler`` is an OSC script; otherwise ``receive`` (or
``match``) is the line's pattern, with ``handler`` a Python script and without
one a template, whose ``respond:`` and ``set_state:`` text
``template_handler.fill`` fills (``{N}`` a capture group, ``{state.key}`` a
value the device holds). An entry whose pattern or code does not compile is
skipped, with only a line in the simulator's log.

Two passes over the entries, all of the first before any of the second:
whether each ``handler:`` compiles (``check_handler_code``), then each
template's surface (``check_templates``): whether it can fire, its
``set_state:`` targets, and the placeholders its text names.

This validator reads an entry in ways the simulator does not, and so passes
some the simulator then skips or reads differently:

- ``STATE_REF`` takes a key of word characters only, where the simulator's
  ``template_handler.STATE_REF`` fills any key without a brace, so a child's
  ``{state.output.2.input}`` goes unchecked.
- Every entry with an ``address`` is passed over by ``check_templates``,
  where the simulator reads one with no ``handler`` as a line handler.
- A pattern is compiled as written, where the simulator compiles
  ``^<pattern>$``, so one that compiles only as written (an inline flag such
  as ``(?i)``, which must come first) passes here and the simulator skips it.
- A script handler's pattern is not compiled here at all (the template check
  stops at the ``handler:``), so one that does not compile passes here and the
  simulator skips the handler.
- A ``handler:`` that is not text is compiled as its text here, where the
  simulator hands the value to ``compile`` as it is.
"""

from __future__ import annotations

import re

from openavc.simulator.validation_result import ValidationResult

#: A ``{state.key}`` reference in a template's text, as this validator reads
#: one (word characters only; not ``template_handler.STATE_REF``, the
#: simulator's, which takes any key without a brace). The type check's boolean
#: rule in ``validate`` reads with it too.
STATE_REF = re.compile(r"\{state\.(\w+)\}")
#: A ``{N}`` capture group reference in a template's text.
_CAPTURE_REF = re.compile(r"\{(\d+)\}")


def check_handler_code(result: ValidationResult, sim_handlers: list) -> None:
    """Each entry is a mapping, and each ``handler:`` body compiles.

    The simulator skips a handler whose code has a syntax error (with only a
    server-side log line), so a typo silently kills the handler at runtime.
    """
    for h in sim_handlers:
        if isinstance(h, dict):
            _check_code(result, h)
        else:
            result.error("command_handlers", f"command_handlers entries must be mappings, got: {h!r}")


def _check_code(result: ValidationResult, entry: dict) -> None:
    """One entry's ``handler:`` body, named by its address or pattern."""
    code = entry.get("handler")
    if code is None:
        return
    try:
        compile(str(code), "<handler>", "exec")
    except SyntaxError as e:
        label = entry.get("address") or entry.get("receive") or entry.get("match") or "?"
        result.error(
            "handler_syntax",
            f"handler for {label!r} has a Python syntax error "
            f"(line {e.lineno}): {e.msg} — the simulator will skip this handler"
        )


def check_templates(result: ValidationResult, sim_handlers: list, known_keys: set[str]) -> None:
    """Each template handler (``receive:`` + ``respond:`` / ``set_state:``).

    Catches set_state: targets that aren't state keys (the sim would write a
    junk key), {N} capture references beyond what the receive: pattern
    captures (left as literal text at runtime), and {state.X} references to
    unknown keys (also left literal). A key is unknown only when the device
    has state keys to compare it with.
    """
    for h in sim_handlers:
        if not isinstance(h, dict) or "address" in h:
            continue  # OSC handlers have no receive:/set_state: surface
        _Template(result, h, known_keys).check()


class _Template:
    """One line handler's template surface, and the result its findings go to."""

    def __init__(self, result: ValidationResult, entry: dict, known_keys: set[str]):
        self.result = result
        self.entry = entry
        self.known_keys = known_keys
        self.pattern = entry.get("receive") or entry.get("match", "")
        self.set_state = entry.get("set_state")
        self.respond = entry.get("respond")

    def check(self) -> None:
        if not self._is_a_template():
            return
        group_count = self._group_count()
        if group_count is None:
            return
        for text in self._texts():
            self._capture_refs(text, group_count)
            self._state_refs(text)

    def _is_a_template(self) -> bool:
        """Whether this entry's template text is used: not beside a
        ``handler:`` (the script answers instead), not without a pattern
        (nothing ever matches it). Either is said when it carries text."""
        if "handler" in self.entry:
            self._text_unused(
                f"handler {self.pattern!r}: set_state:/respond: are ignored "
                f"when handler: is present — move that logic into the handler code"
            )
            return False
        if not self.pattern:
            self._text_unused(
                "handler with respond:/set_state: has no receive: or match: "
                "pattern — it can never fire"
            )
            return False
        return True

    def _text_unused(self, message: str) -> None:
        if self.set_state or self.respond:
            self.result.warning("command_handlers", message)

    def _group_count(self) -> int | None:
        """How many groups the pattern captures, or None (said) when it is not a regex."""
        try:
            return re.compile(self.pattern).groups
        except re.error as e:
            self.result.error(
                "command_handlers",
                f"invalid regex in handler pattern {self.pattern!r}: {e} — "
                f"the simulator will skip this handler"
            )
            return None

    def _texts(self) -> list[str]:
        """Each ``set_state:`` value as text (every target checked first),
        then the ``respond:`` text."""
        texts = self._set_state_texts() if isinstance(self.set_state, dict) else []
        if isinstance(self.respond, str):
            texts.append(self.respond)
        return texts

    def _set_state_texts(self) -> list[str]:
        texts: list[str] = []
        for key, value in self.set_state.items():
            if self.known_keys and key not in self.known_keys:
                self.result.warning(
                    "set_state",
                    f"handler {self.pattern!r}: set_state target '{key}' is not "
                    f"a state variable or initial_state key — the value will be "
                    f"written to a key nothing reads"
                )
            texts.append(str(value))
        return texts

    def _capture_refs(self, text: str, group_count: int) -> None:
        for m in _CAPTURE_REF.finditer(text):
            n = int(m.group(1))
            if n > group_count:
                self.result.error(
                    "set_state",
                    f"handler {self.pattern!r}: template {text!r} references "
                    f"capture group {{{n}}} but the pattern has only "
                    f"{group_count} group(s) — the placeholder stays literal at runtime"
                )

    def _state_refs(self, text: str) -> None:
        for m in STATE_REF.finditer(text):
            if self.known_keys and m.group(1) not in self.known_keys:
                self.result.warning(
                    "set_state",
                    f"handler {self.pattern!r}: template {text!r} references "
                    f"{{state.{m.group(1)}}} which is not a state key — the "
                    f"placeholder stays literal at runtime"
                )

