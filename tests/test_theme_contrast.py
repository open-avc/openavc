"""Every text colour in the IDE palette has to be readable on every surface it
can land on.

This is a real defect that shipped, not a hypothetical: ``--text-muted`` sat at
3.8:1 on white and is what hint text under a form field is drawn in -- the
"4-6 digit PIN to lock the touch panel" line under the Lock Code box is 11px in
exactly that colour. Light grey on white, at 11px, is the report that started
this ("extremely hard to read them").

The check is arithmetic rather than judgement, so it belongs in a test: WCAG's
contrast ratio between each *text* token and each *background* token, both
themes, asserted at AA for normal text (4.5:1). Backgrounds are read out of the
same file, so adding a surface automatically widens the check.

Only tokens actually used as text are covered. ``--border-color`` is deliberately
not: it is a line, and holding a border to a text ratio would force a palette
nobody wants. ``--color-error`` / ``--color-warning`` / ``--color-success`` ARE
covered, because a grep of the frontend puts them overwhelmingly on ``color:``
(71 of 72 uses of the error token) rather than on fills.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

TOKENS = (
    Path(__file__).resolve().parent.parent
    / "openavc" / "web" / "programmer" / "src" / "styles" / "tokens.css"
)

# AA for normal-size text. The hint text this check exists for is 11px, so the
# large-text allowance (3:1) never applies to it.
AA_NORMAL = 4.5

# Drawn as text somewhere in the IDE. --border-color is excluded on purpose.
TEXT_TOKENS = (
    "--text-primary",
    "--text-secondary",
    "--text-muted",
    "--accent",
    "--color-success",
    "--color-warning",
    "--color-error",
    "--color-info",
)

# Backgrounds a text token can be drawn on. --bg-selected and --accent-bg are
# excluded: they are filled with --text-on-accent, a different pairing.
SURFACE_TOKENS = ("--bg-base", "--bg-primary", "--bg-surface", "--bg-elevated", "--bg-hover")


def _relative_luminance(hex_colour: str) -> float:
    raw = hex_colour.lstrip("#")
    channels = [int(raw[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    r, g, b = linear
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(fg: str, bg: str) -> float:
    a, b = _relative_luminance(fg), _relative_luminance(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def _parse_themes() -> dict[str, dict[str, str]]:
    """Split tokens.css into its two blocks and collect the hex variables.

    ``:root`` is the dark theme and ``[data-theme="light"]`` overrides it, so the
    light theme is the dark one updated with its own block -- which is also how
    the browser resolves it. A token defined only in :root is therefore checked
    against the light surfaces too, which is the point: that is exactly the shape
    a half-updated palette has.
    """
    css = TOKENS.read_text(encoding="utf-8")
    light_at = css.index('[data-theme="light"]')

    def hexes(block: str) -> dict[str, str]:
        return dict(re.findall(r"(--[a-z-]+):\s*(#[0-9a-fA-F]{6})\s*;", block))

    dark = hexes(css[:light_at])
    light = {**dark, **hexes(css[light_at:])}
    return {"dark": dark, "light": light}


THEMES = _parse_themes()


def test_the_palette_actually_parsed() -> None:
    # A regex that silently matches nothing would make every check below pass.
    for name, theme in THEMES.items():
        assert theme.get("--text-muted"), f"{name}: --text-muted not found"
        assert theme.get("--bg-surface"), f"{name}: --bg-surface not found"
    assert THEMES["dark"]["--bg-surface"] != THEMES["light"]["--bg-surface"], (
        "both themes resolved to the same surface colour -- the split is wrong"
    )


@pytest.mark.parametrize("theme_name", sorted(THEMES))
@pytest.mark.parametrize("text_token", TEXT_TOKENS)
def test_text_is_readable_on_every_surface(theme_name: str, text_token: str) -> None:
    theme = THEMES[theme_name]
    fg = theme.get(text_token)
    if fg is None:
        pytest.skip(f"{text_token} is not defined for the {theme_name} theme")

    failures = []
    for surface in SURFACE_TOKENS:
        bg = theme.get(surface)
        if bg is None:
            continue
        ratio = contrast_ratio(fg, bg)
        if ratio < AA_NORMAL:
            failures.append(f"{surface} ({bg}): {ratio:.2f}:1")

    assert not failures, (
        f"{theme_name} theme: {text_token} ({fg}) is below AA ({AA_NORMAL}:1) on "
        + ", ".join(failures)
        + ". Pick a colour that clears it on every surface rather than only on the "
          "one the control happens to sit on today."
    )


# Whether every var(--x) the IDE reads is a token that EXISTS is the other half
# of this, and it already has a home: tests/test_ide_tokens.py. It is not
# duplicated here. The split is that this file asks whether the palette is
# readable, and that one asks whether the components are actually using it.
