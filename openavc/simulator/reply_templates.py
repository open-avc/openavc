"""The reply text a driver's response pattern matches, for the simulator to send.

The auto-generated simulator (``yaml_auto``) answers a query the way the device
would: with the text the driver's response rule matches, the value where the rule
reads it. That text is rebuilt here from the pattern's source, one representative
string: literals stay, anchors and word boundaries vanish, a class or shorthand
becomes one member character, a quantified atom repeats its fewest allowed times
(an optional one is dropped), an alternation takes its first branch carrying a
slot, else its first that can be written, and each capture group the caller names
becomes a placeholder (``{value}``, ``{child_id}``) in place of its content. A
``{name}`` slot whose group is a fixed run of digits carries the width as a
zero-pad spec (``{value:03d}``), which ``fill_template`` renders.

Anything the walk cannot model faithfully (a lookaround, a back-reference, a
negated class that survives, a slot that must repeat, a possessive quantifier,
the verbose flag) gives None, and the simulator then has no answer for that rule
rather than one the driver's own regex would reject. Group numbering must stay
exactly ``re``'s, so every branch of an alternation is parsed though only one is
emitted. A slot is placed by where its group was emitted, never found again by
its text, so a wire token the pattern's own text also writes stays one slot.

Whatever the walk builds is then held to the pattern itself (``_reads_back``):
filled with a sample (a ``{name}`` slot its group's own text, a wire token
itself), the pattern must match it the way the driver searches a line, each
slot group capturing exactly what went in. A reply that fails is None, so the
walk can never hand the simulator text its own pattern rejects (an anchor
mid-pattern, a word boundary between two letters, an earlier branch that takes
the line). Only the simulator calls this. Standard library only.
"""

from __future__ import annotations

import itertools
import re
from typing import Any


class _Unsupported(Exception):
    """Regex construct the emit reconstruction cannot model."""


_SHORTHAND_REPS = {"d": "0", "w": "0", "s": " ", "S": "0"}
_ESCAPE_CHARS = {"a": "\a", "r": "\r", "n": "\n", "t": "\t", "f": "\f", "v": "\v"}
#: What an escape outside a class emits: a shorthand's member, a control
#: character, or nothing for a word boundary.
_ESCAPE_EMITS = {**_SHORTHAND_REPS, **_ESCAPE_CHARS, "b": "", "B": ""}
#: Inside a class, ``\b`` is a backspace and the anchors are no escape at all.
_CLASS_ESCAPE_EMITS = {**_SHORTHAND_REPS, **_ESCAPE_CHARS, "b": "\b"}
#: The escapes naming one character by its code: how many hex digits follow.
_CODE_ESCAPES = {"x": 2, "u": 4, "U": 8}
#: The fewest repeats each one-character quantifier allows.
_SIMPLE_QUANTIFIERS = {"?": 0, "*": 0, "+": 1}
#: A quantifier: one of those, or ``{n}`` / ``{n,}`` / ``{n,m}`` / ``{,m}`` /
#: ``{,}`` (no lower bound is zero); then ``?`` when it is lazy. A ``{`` that
#: starts none of them is a literal, ``{}`` among them.
_QUANTIFIER_RE = re.compile(r"(?:([?*+])|\{(?:(\d+)(?:,\d*)?|,\d*)\})\??")
_GLOBAL_FLAGS_RE = re.compile(r"\(\?([aiLmsux]+)\)")
_SCOPED_FLAGS_RE = re.compile(r"\(\?([aiLmsux]+):")
_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")
#: Where a slot is held while the walk runs: one private-use character per
#: slot, which a pattern that writes one itself is refused for.
_MARK_BASE = 0xE000
_MARK_LAST = 0xF8FF
#: What a ``{name}`` slot whose group has no text of its own (a negated
#: class, ``\D``) is tried with, in order, when the walk checks its reply.
_PROBES = ("0", "a", "A", " ")


def _is_mark(ch: str) -> bool:
    return _MARK_BASE <= ord(ch) <= _MARK_LAST


def _class_emit(body: str) -> str | None:
    """One representative character for a [...] class body, or None.

    The first member decides: a literal char, a range's start, an escape read
    as it is outside a class (``_escape_emit``) but for ``\b``, a backspace
    here. A negated class has no safe representative.
    """
    if not body or body.startswith("^"):
        return None
    if body[0] == "\\":
        emitted, _ = _escape_emit(body, 0, _CLASS_ESCAPE_EMITS)
        return emitted or None
    return body[0]


def _class_members_start(src: str, i: int) -> int:
    """Where the members of the class opened at ``src[i] == '['`` start: past a
    negating ``^``, and past a ``]`` right after it, which is a member there."""
    j = i + 1
    if j < len(src) and src[j] == "^":
        j += 1
    if j < len(src) and src[j] == "]":
        j += 1
    return j


def _class_end(src: str, i: int) -> int:
    """Index of the ']' closing the class opened at src[i] == '['."""
    j = _class_members_start(src, i)
    while j < len(src):
        if src[j] == "]":
            return j
        j += 2 if src[j] == "\\" else 1
    raise _Unsupported


def _escape_emit(
    src: str, i: int, emits: dict[str, str] = _ESCAPE_EMITS
) -> tuple[str | None, int]:
    """Emit the escape at ``src[i] == '\\'``: a shorthand's member, a control
    character, nothing for a word boundary (``emits``: a class passes its
    own, where ``\\b`` is a backspace), the character ``\\xHH``,
    ``\\uHHHH`` or ``\\UHHHHHHHH`` names, or an escaped punctuation mark
    itself. None for a back-reference or an octal code, ``\\D`` and ``\\W``
    (no safe member), the anchors ``\\A`` and ``\\Z``, ``\\N{...}`` and
    any other letter (which ``re`` refuses)."""
    if i + 1 >= len(src):
        raise _Unsupported
    e = src[i + 1]
    if e in emits:
        return emits[e], i + 2
    if e in _CODE_ESCAPES:
        return _code_escape(src, i + 2, _CODE_ESCAPES[e])
    if e.isascii() and e.isalnum():
        return None, i + 2
    return e, i + 2  # escaped literal (punctuation)


def _code_escape(src: str, start: int, digits: int) -> tuple[str | None, int]:
    """The character a ``\\x`` / ``\\u`` / ``\\U`` escape names by the
    ``digits`` hex digits from ``start``; None when they are not all there
    (``re`` refuses it) or name no character a reply can carry."""
    code = src[start : start + digits]
    if len(code) < digits or not _HEX_DIGITS.issuperset(code):
        return None, start
    value = int(code, 16)
    if value > 0x10FFFF or _is_mark(chr(value)):
        return None, start + digits
    return chr(value), start + digits


def _group_opening(src: str, i: int) -> tuple[bool, int]:
    """Whether the group opened at ``src[i] == '('`` captures, and where its
    body starts: ``(?:`` and a scoped flag group (``(?i:``) do not, a plain or
    named group (``(?P<name>``) does. Any other ``(?`` (a lookaround, a
    conditional, a comment, ``(?P=name)``) is refused."""
    if src.startswith("(?:", i):
        return False, i + 3
    if src.startswith("(?P<", i):
        k = src.find(">", i)
        if k < 0:
            raise _Unsupported
        return True, k + 1
    if not src.startswith("(?", i):
        return True, i + 1
    scoped = _SCOPED_FLAGS_RE.match(src, i)
    if not scoped:
        raise _Unsupported  # lookarounds, conditionals, (?P=...)
    _refuse_verbose(scoped.group(1))
    return False, scoped.end()


def _refuse_verbose(flags: str) -> None:
    """The verbose flag makes whitespace and ``#`` comments insignificant,
    which the walk does not model."""
    if "x" in flags:
        raise _Unsupported


_DIGIT_RUN_RE = re.compile(r"(?:\\d|\[0-9\]|[0-9])(?:\{(\d+)\})?")


def _fixed_digit_width(body: str) -> int | None:
    """N when a capture group's body is exactly N decimal digits (``\\d{3}``,
    ``[0-9]{2}``, ``\\d\\d``, a literal ``00000``), else None. A device that always sends three
    digits sends ``030``, never ``30``, and a driver reading it with
    ``(\\d{3})`` rejects the shorter form."""
    pos = width = 0
    while pos < len(body):
        m = _DIGIT_RUN_RE.match(body, pos)
        if not m:
            return None
        width += int(m.group(1)) if m.group(1) else 1
        pos = m.end()
    return width if width > 1 else None


def _parse_quantifier(src: str, i: int) -> tuple[int, int] | None:
    """Parse a quantifier at src[i]; return (min_repeats, next_i) or None.
    More than 8 required repeats are refused, and so is a possessive
    quantifier (a ``+`` after it), which never gives back what it took."""
    m = _QUANTIFIER_RE.match(src, i)
    if not m:
        return None
    if m.group(1):
        n = _SIMPLE_QUANTIFIERS[m.group(1)]
    else:
        n = int(m.group(2)) if m.group(2) else 0
    if n > 8 or src.startswith("+", m.end()):
        raise _Unsupported
    return n, m.end()


class _PatternWalk:
    """One walk over a response pattern's source, emitting the text it matches.

    Capture groups are numbered as the walk opens them, which is the order
    ``re`` numbers them in; ``slots`` names the groups to emit as a placeholder
    in place of their content. The walk records how many groups it opened
    (``groups``), which slot groups it reached (``found``) and the fixed digit
    width of each (``widths``), for the caller to check and apply.

    ``branches``, ``seq``, ``atom`` and ``group`` call one another, one level of
    the pattern's nesting at a time. Each returns its emission, None when that
    part cannot be represented, and the index after it, and raises
    ``_Unsupported`` for a construct the walk refuses outright.

    While the walk runs, each slot is a mark of its own (``marks``), so which
    piece carries a slot is known by where the slot went, never by searching
    for its text; ``placed`` writes the placeholders in at the end. Each slot
    group's own emission is kept (``samples``) for ``reads_back``."""

    def __init__(self, src: str, slots: dict[int, str]) -> None:
        self.src = src
        self.slots = dict(slots)
        self.marks = {n: chr(_MARK_BASE + k) for k, n in enumerate(self.slots)}
        self.groups = 0
        self.found: set[int] = set()
        self.widths: dict[int, int] = {}
        self.samples: dict[int, str | None] = {}

    def emission(self) -> str | None:
        """The whole pattern's emission, slots as their marks, or None when it
        cannot be modeled or would not read back (``reads_back``)."""
        if any(_is_mark(ch) for ch in self.src):
            return None
        try:
            emission, i = self.branches(0, depth=0)
        except _Unsupported:
            return None
        if emission is None or i != len(self.src):
            return None
        if self.found != set(self.slots):
            return None
        if any(mark not in emission for mark in self.marks.values()):
            return None
        return emission if self.reads_back(emission) else None

    def carries_slot(self, piece: str) -> bool:
        """True when a slot was emitted into ``piece``."""
        return any(mark in piece for mark in self.marks.values())

    def placed(self, emission: str) -> str:
        """``emission`` with each mark replaced by its slot's placeholder, a
        ``{name}`` slot whose group is a fixed run of digits written with
        that width as a zero-pad spec (``{value:03d}``)."""
        for group, mark in self.marks.items():
            ph = self.slots[group]
            width = self.widths.get(group)
            if width and _SLOT_NAME_RE.fullmatch(ph):
                ph = f"{ph[:-1]}:0{width}d}}"
            emission = emission.replace(mark, ph)
        return emission

    def reads_back(self, emission: str) -> bool:
        """True when the pattern, compiled as the driver compiles it, finds
        ``emission`` with its slots filled (``_fills``) and each slot group
        captures exactly what went in its slot. A pattern ``re`` refuses
        reads nothing back."""
        try:
            rx = re.compile(self.src)
        except re.error:
            return False
        for fill in self._fills():
            line = emission
            for group, text in fill.items():
                line = line.replace(self.marks[group], text)
            m = rx.search(line)
            if m and all(m.group(group) == text for group, text in fill.items()):
                return True
        return False

    def _fills(self) -> list[dict[int, str]]:
        """What the slots are filled with to check a reply: a wire token is
        its own text, a ``{name}`` slot its group's own emission, or each of
        ``_PROBES`` when its group has none."""
        choices = []
        for group, ph in self.slots.items():
            if not _SLOT_NAME_RE.fullmatch(ph):
                choices.append((ph,))
            elif self.samples.get(group) is not None:
                choices.append((self.samples[group],))
            else:
                choices.append(_PROBES)
        return [dict(zip(self.slots, combo)) for combo in itertools.product(*choices)]

    def branches(self, i: int, depth: int) -> tuple[str | None, int]:
        """Emit an alternation: all branches are parsed (group numbering must
        stay exact), the emission comes from the first branch carrying a slot,
        else the first representable branch."""
        emission, i = self.seq(i, depth)
        emissions = [emission]
        while i < len(self.src) and self.src[i] == "|":
            emission, i = self.seq(i + 1, depth)
            emissions.append(emission)
        with_slot = [e for e in emissions if e is not None and self.carries_slot(e)]
        if with_slot:
            return with_slot[0], i
        return next((e for e in emissions if e is not None), None), i

    def seq(self, i: int, depth: int) -> tuple[str | None, int]:
        """Emit atoms until '|', a closing ')' (inside a group), or the end;
        a ')' with no group open is refused there (``_ends_seq``)."""
        pieces: list[str | None] = []
        while i < len(self.src) and not self._ends_seq(i, depth):
            piece, i = self.atom(i)
            reps, i = self._repeats(piece, i)
            if reps:
                pieces.append(None if piece is None else piece * reps)
        if any(p is None for p in pieces):
            return None, i
        return "".join(pieces), i  # type: ignore[arg-type]

    def _ends_seq(self, i: int, depth: int) -> bool:
        """True at a '|' or at the ')' closing the group being read; a ')'
        with no group open is unbalanced."""
        c = self.src[i]
        if c == ")" and depth == 0:
            raise _Unsupported
        return c in "|)"

    def _repeats(self, piece: str | None, i: int) -> tuple[int, int]:
        """How many times the atom just read is emitted, and where the next one
        starts: its quantifier's fewest repeats, else once. A slot is never
        dropped, and one that must repeat is refused: the field would go in
        twice."""
        quantifier = _parse_quantifier(self.src, i)
        reps, i = quantifier if quantifier else (1, i)
        if piece is None or not self.carries_slot(piece):
            return reps, i
        if reps > 1:
            raise _Unsupported
        return 1, i

    def atom(self, i: int) -> tuple[str | None, int]:
        """Emit one atom starting at src[i]. None means unrepresentable —
        fatal only if the atom survives into the final emission."""
        src = self.src
        c = src[i]
        if c in "^$":
            return "", i + 1
        if c == ".":
            return "x", i + 1
        if c == "\\":
            return _escape_emit(src, i)
        if c == "[":
            j = _class_end(src, i)
            return _class_emit(src[i + 1 : j]), j + 1
        if c == "(":
            return self.group(i)
        return c, i + 1

    def group(self, i: int) -> tuple[str | None, int]:
        """Emit the group opened at src[i] == '(': its body's emission, or the
        slot's placeholder when it is a slot group."""
        flags = _GLOBAL_FLAGS_RE.match(self.src, i)
        if flags:
            _refuse_verbose(flags.group(1))
            return "", flags.end()  # global flags like (?i)
        capturing, start = _group_opening(self.src, i)
        number = 0
        if capturing:
            self.groups += 1
            number = self.groups
        emission, end = self.branches(start, depth=1)
        if end >= len(self.src) or self.src[end] != ")":
            raise _Unsupported
        if capturing and number in self.slots:
            return self._slot(number, self.src[start:end], emission), end + 1
        return emission, end + 1

    def _slot(self, number: int, body: str, emission: str | None) -> str:
        """Slot group ``number``'s mark, recording that the walk reached it,
        the width its body fixes and the body's own ``emission``."""
        self.found.add(number)
        self.samples[number] = emission
        width = _fixed_digit_width(body)
        if width:
            self.widths[number] = width
        return self.marks[number]


def emit_template_multi(pattern: str, placeholders: dict[int, str]) -> str | None:
    """Reconstruct the reply text a response pattern matches, substituting
    each capture group in ``placeholders`` with its placeholder text.

    The multi-slot core behind ``emit_template``: a child-addressed reply
    needs the child id group AND the value group templated in one emission
    (``'Out(\\d+) In(\\d+) Vid'`` with ``{1: '{child_id}', 2: '{value}'}`` →
    ``'Out{child_id} In{value} Vid'``). Every targeted group must survive
    into the final emission; returns None when the pattern can't be modeled
    or the emission would not read back through it.

    A ``{name}`` placeholder whose group is a fixed run of digits carries the
    width as a zero-pad spec (``'Gain(\\d{3})'`` → ``'Gain{value:03d}'``);
    ``fill_template`` renders it.
    """
    walk = _PatternWalk(pattern, placeholders)
    emission = walk.emission()
    return None if emission is None else walk.placed(emission)


_SLOT_NAME_RE = re.compile(r"\{\w+\}")
_SLOT_RE = re.compile(r"\{(\w+)(?::0(\d+)d)?\}")


def fill_template(template: str, values: dict[str, Any]) -> str:
    """Substitute ``{name}`` and ``{name:0Nd}`` slots in an emitted template.

    A width slot zero-pads a whole number (or a string of digits) to N; any
    other value goes in as ``str()``. Braces that are not a slot named in
    ``values`` stay as they are, so a protocol's own literal braces
    (``{Lead Vox}``) survive."""

    def repl(m: re.Match) -> str:
        name = m.group(1)
        if name not in values:
            return m.group(0)
        value = values[name]
        if m.group(2):
            return _zero_pad(value, int(m.group(2)))
        return str(value)

    return _SLOT_RE.sub(repl, template)


def slot_text(template: str, name: str, value: Any) -> str | None:
    """The text the first {name} slot in template renders value
    as, padding included, the way fill_template fills it; None when the
    template has no such slot."""
    for m in _SLOT_RE.finditer(template):
        if m.group(1) == name:
            return _zero_pad(value, int(m.group(2))) if m.group(2) else str(value)
    return None


def _zero_pad(value: Any, width: int) -> str:
    """``value`` as a whole number zero-padded to ``width``; anything that is
    not a whole number goes in as ``str()``."""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, int):
        return f"{value:0{width}d}"
    text = str(value)
    if re.fullmatch(r"-?\d+", text):
        return f"{int(text):0{width}d}"
    return text


def emit_template(pattern: str, group: int = 1) -> str | None:
    """Reconstruct the reply text a response pattern matches, with capture
    group ``group`` as a ``{value}`` placeholder.

    ``'In(\\d+) All'`` → ``'In{value} All'``; the group number comes from
    the response's ``$N`` reference, so a multi-group pattern places the
    value where that state key actually reads it. Returns None when the
    pattern can't be modeled — the caller falls back rather than emitting
    garbage the driver's own regex would reject.
    """
    return emit_template_multi(pattern, {group: "{value}"})


def emit_literal(pattern: str) -> str | None:
    """Reconstruct the literal reply text a capture-less response pattern
    matches (``'^Amt1$'`` → ``'Amt1'``). None when the pattern contains a
    capturing group (a fixed literal can't represent a captured field) or
    a construct the reconstruction can't model.
    """
    walk = _PatternWalk(pattern, {})
    emission = walk.emission()
    return None if walk.groups else emission
