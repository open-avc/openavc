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
