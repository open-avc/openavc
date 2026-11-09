"""What a command's name says when the command declares nothing.

A driver command may declare what it does (``sets:``, ``query_for:``), and the
auto-generated simulator (``yaml_auto``) honors that first. A command that
declares nothing is read by its name instead: ``infer_state_var`` finds the
state variable it targets (``set_volume`` -> ``volume``) and ``name_switch``
the value a switching name sets (``power_on`` -> True). Only the simulator
guesses (the validator's coverage checks see the guess through the simulator
they build); the runtime never does.

Standard library only (``tests/test_compiled_protocol_purity.py``).
"""

from __future__ import annotations


# ── Command semantics: name-inference fallback ──
#
# When a command declares no `sets:` / `query_for:`, the auto-generated
# simulator falls back to reading the command's name: the state variable it
# targets, and the value a switching name sets. Declared semantics always win.
# The words are listed once, here.

#: The words that switch a variable, by the value each sets: on, off, or over
#: (None: a toggle, whose new value the simulator leaves alone).
_SWITCH_PREFIXES: dict[str, bool | None] = {"enable_": True, "disable_": False}
_SWITCH_SUFFIXES: dict[str, bool | None] = {"_on": True, "_off": False, "_toggle": None}

#: What comes off a command's name to find its variable: the first prefix that
#: fits, then the first suffix that fits.
_PREFIXES = ("set_", "get_", "query_", *_SWITCH_PREFIXES)
_SUFFIXES = tuple(_SWITCH_SUFFIXES)

#: Common AV names for a variable, looked up by the stripped name, then by the
#: whole name.
_ALIASES = {
    "route": "input",
    "route_all": "input",
    "unmute": "mute",
    "vol": "volume",
    "video_mute": "video_mute",
    "audio_mute": "mute",
}


def _subject(cmd_name: str) -> str:
    """A command's name without its first fitting prefix and suffix."""
    name = next((cmd_name[len(p):] for p in _PREFIXES if cmd_name.startswith(p)), cmd_name)
    return next((name[: -len(s)] for s in _SUFFIXES if name.endswith(s)), name)


def infer_state_var(cmd_name: str, state_vars: set[str]) -> str | None:
    """Infer which state variable a command targets from its name.

    Examples:
        "set_volume" → "volume"
        "mute_on" → "mute"
        "power_off" → "power"
        "query_input" → "input"
        "route_all" → "input" (if "input" in state_vars, best guess for routing)
    """
    name = _subject(cmd_name)
    if name in state_vars:
        return name
    alias = _ALIASES.get(name) or _ALIASES.get(cmd_name)
    return alias if alias and alias in state_vars else None


def name_switch(cmd_name: str) -> tuple[bool, bool | None]:
    """What a command's name switches its variable to.

    ``(True, value)`` when the name carries a switching word: True for
    ``_on`` / ``enable_``, False for ``_off`` / ``disable_``, None for
    ``_toggle`` (nothing is set). The on words are asked first, so
    ``enable_power_off`` switches on. ``(False, None)`` when it carries none.
    """
    for value in (True, False, None):
        if _says(cmd_name, value):
            return True, value
    return False, None


def _says(cmd_name: str, value: bool | None) -> bool:
    """Whether a word that switches to ``value`` ends or starts the name."""
    return any(
        cmd_name.endswith(suffix) for suffix, sets in _SWITCH_SUFFIXES.items() if sets is value
    ) or any(
        cmd_name.startswith(prefix) for prefix, sets in _SWITCH_PREFIXES.items() if sets is value
    )
