"""Every ``var(--x)`` the Programmer IDE reads has to be a token that exists.

CSS custom properties fail silently. ``var(--bg-secondary, #232342)`` against a
token file that never defined ``--bg-secondary`` does not warn, does not throw
and does not fall back to anything sensible -- it paints ``#232342``, forever,
and no theme switch will ever touch it. That is not hypothetical: it is why the
sign-in card renders a purple that appears in no token file and in no theme.

So the failure this guards against is not a crash. It is a colour that looks
deliberate, ignores both themes, and survives every restyle because nothing can
see it. One typo in a property name is enough, and the more names drift, the
more of the UI quietly stops being themeable.

The scan is deliberately coarse -- a regex over the source, not a CSS parser --
and it fails loud rather than guessing. If a refactor makes the parse wrong,
teach the parse; do not loosen the assertion.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PROGRAMMER = Path(__file__).resolve().parents[1] / "openavc" / "web" / "programmer"
TOKENS = PROGRAMMER / "src" / "styles" / "tokens.css"
SOURCE_ROOT = PROGRAMMER / "src"

# A custom property being DEFINED looks like `  --name: value;` at the start of a
# declaration. A property being READ looks like `var(--name` anywhere.
DEFINITION = re.compile(r"^\s*(--[a-zA-Z0-9-]+)\s*:", re.MULTILINE)
REFERENCE = re.compile(r"var\(\s*(--[a-zA-Z0-9-]+)")

#: The names the IDE reads today that its token file does not define.
#:
#: Every one of these silently paints its ``var(--name, fallback)`` default and
#: ignores both themes, which is a real bug -- but fixing them means changing
#: colours, and this IDE was deliberately reverted to its pre-restyle
#: appearance (2026-08-20), so they are RECORDED rather than repaired. See
#: openavc-backlog.md for the entry that owns them.
#:
#: The list is a ceiling, not a licence: the assertion below fails on a name
#: that is NOT in it, so new drift is still caught the day it appears. Deleting
#: a name from here when you fix it is the point; adding one needs a reason.
KNOWN_UNDEFINED: frozenset[str] = frozenset(
{
    "--accent-color",
    "--accent-contrast",
    "--accent-text",
    "--bg-info",
    "--bg-input",
    "--bg-main",
    "--bg-secondary",
    "--bg-warning",
    "--border",
    "--border-radius-lg",
    "--color-accent",
    "--color-danger",
    "--danger",
    "--danger-dim",
    "--error",
    "--font-primary",
    "--font-sans",
    "--font-size-md",
    "--font-size-xs",
    "--font-sm",
    "--ink",
    "--muted",
    "--radius",
    "--radius-md",
    "--radius-sm",
    "--sage-deep",
    "--status-error",
    "--status-error-bg",
    "--status-success",
    "--success",
    "--text",
    "--warning"
}
)

# The one family of names that is correctly absent from the IDE's token file.
# `--panel-*` belongs to the panel, which is a separate document with its own
# stylesheet; the IDE only ever writes those names INTO panel data (a theme
# gallery's element colours, the project stylesheet's help text) for the panel
# to resolve on its own side. Defining them here would be the actual mistake.
PANEL_NAMESPACE = "--panel-"


def _defined_tokens() -> set[str]:
    return set(DEFINITION.findall(TOKENS.read_text(encoding="utf-8")))


def _source_files() -> list[Path]:
    return sorted(
        p
        for ext in ("*.tsx", "*.ts", "*.css")
        for p in SOURCE_ROOT.rglob(ext)
        if "node_modules" not in p.parts
    )


def test_the_token_file_is_where_it_is_expected() -> None:
    """A moved token file would make every other test here vacuously pass."""
    assert TOKENS.is_file(), f"no token file at {TOKENS}"
    assert _defined_tokens(), "parsed no token definitions -- the parse is wrong"


def test_every_token_the_ide_reads_is_one_the_ide_defines() -> None:
    defined = _defined_tokens()
    missing: dict[str, list[str]] = {}

    for path in _source_files():
        for name in REFERENCE.findall(path.read_text(encoding="utf-8")):
            if name.startswith(PANEL_NAMESPACE) or name in defined:
                continue
            if name in KNOWN_UNDEFINED:
                continue
            missing.setdefault(name, []).append(
                str(path.relative_to(PROGRAMMER.parents[2]))
            )

    if missing:
        lines = [
            f"{len(missing)} NEW token name(s) are read but never defined in "
            f"{TOKENS.name}. Each one silently paints its fallback and ignores "
            f"both themes. (The pre-existing ones are recorded in "
            f"KNOWN_UNDEFINED above; these are not.)",
            "",
        ]
        for name in sorted(missing):
            where = sorted(set(missing[name]))
            shown = ", ".join(where[:3]) + (f" (+{len(where) - 3} more)" if len(where) > 3 else "")
            lines.append(f"  {name}  --  {shown}")
        lines += ["", "Fix: rename the reference to the token that exists, or define it."]
        pytest.fail("\n".join(lines))


def test_both_themes_define_the_same_token_names() -> None:
    """A token defined for one theme and not the other is a half-themed colour.

    The light block deliberately restates only what CHANGES, so it is a subset
    by design -- what must not happen is the reverse: a name that exists only
    under ``[data-theme="light"]`` is undefined in the default dark theme.
    """
    text = TOKENS.read_text(encoding="utf-8")
    light_start = text.find('[data-theme="light"]')
    assert light_start != -1, "no light-theme block in the token file"

    dark_names = set(DEFINITION.findall(text[:light_start]))
    light_names = set(DEFINITION.findall(text[light_start:]))

    dark_only_missing = light_names - dark_names
    assert not dark_only_missing, (
        "these tokens are defined only for the light theme, so the dark theme "
        f"has no value for them at all: {sorted(dark_only_missing)}"
    )
