"""The panel's table of old Lucide icon names agrees with the sprite it draws from.

The UI Builder previews icons through lucide-react, which still answers to
names Lucide has since renamed (home, tv-2, unlock); the panel draws from its
sprite (``web/panel/icons.svg``), which carries only the new ones. The panel
reads an old name through ``modules/icon_aliases.gen.js``, written by
``web/programmer/scripts/build-icons.mjs`` beside the sprite, so the two must
not drift: every name the table maps to is in the sprite, and no name it maps
from is one the sprite already has.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

PANEL = Path(__file__).resolve().parents[1] / "openavc" / "web" / "panel"
TABLE = PANEL / "modules" / "icon_aliases.gen.js"
SPRITE = PANEL / "icons.svg"


def _table() -> dict[str, str]:
    text = TABLE.read_text(encoding="utf-8")
    body = re.search(r"Object\.freeze\((\{.*\})\);", text, re.S)
    assert body, f"{TABLE} is not the shape build-icons.mjs writes"
    return json.loads(body.group(1))


def _sprite_ids() -> set[str]:
    return set(re.findall(r'<symbol[^>]*\bid="([^"]+)"', SPRITE.read_text(encoding="utf-8")))


def test_every_old_name_reaches_an_icon_the_sprite_has() -> None:
    ids = _sprite_ids()
    table = _table()
    assert table, "the table is empty"
    assert sorted(old for old, new in table.items() if new not in ids) == []
    assert sorted(old for old in table if old in ids) == []


def test_the_names_saved_projects_hold_are_in_it() -> None:
    table = _table()
    assert table["home"] == "house"
    assert table["tv-2"] == "tv-minimal"
    assert table["unlock"] == "lock-open"


def test_the_panel_draws_through_it() -> None:
    words = (PANEL / "modules" / "words.js").read_text(encoding="utf-8")
    assert "from './icon_aliases.gen.js'" in words
    assert "icons.svg#${spriteId(iconName)}" in words


def test_the_page_reviews_know_every_name_the_panel_draws() -> None:
    """Both page reviews warn on an icon name the panel draws nothing for, from
    lists build-icons.mjs writes beside the sprite and the table: the Builder's
    old names and the AI review's whole list must be exactly what the panel
    draws, or a review warns on an icon that draws, or keeps quiet on one that
    does not."""
    from openavc.ui.panel_icon_names import PANEL_ICON_NAMES

    table = _table()
    assert PANEL_ICON_NAMES == _sprite_ids() | set(table)
    builder = (PANEL.parent / "programmer" / "src" / "components" / "ui-builder"
               / "spriteIconNames.ts").read_text(encoding="utf-8")
    old = re.search(r"OLD_ICON_NAMES: string\[\] = (\[.*?\]);", builder, re.S)
    assert old, "spriteIconNames.ts is not the shape build-icons.mjs writes"
    assert sorted(json.loads(old.group(1))) == sorted(table)


def test_what_the_review_counts_as_drawing_nothing() -> None:
    """Text naming neither list. An uploaded picture, an empty icon, none, and a
    value that is not text (which names no icon on the panel) are not."""
    from openavc.ui.review_icons import draws_nothing

    assert draws_nothing("powr")
    assert not draws_nothing("house")
    assert not draws_nothing("home")
    assert not draws_nothing("assets://logo.png")
    assert not draws_nothing("")
    assert not draws_nothing(None)
    assert not draws_nothing(7)
