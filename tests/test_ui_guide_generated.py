"""The on-demand authoring guide is generated, and says only what is measured.

``server/ui/guide_gen.py`` renders ``server/ui/panel_authoring_guide.md`` from
``RULES`` plus the finger and binding tables. Same arrangement as
tests/test_ui_minimums_generated.py: the committed artifact is compared against a
fresh render, so a rule edited without regenerating -- or an artifact edited by
hand -- fails here.

The second half of this file is about what the guide must NOT say, which the
byte-compare cannot see. A generated file can be faithfully generated and still
be wrong in a way that costs real work: publish a floor for a type that has
none, or print one number for a floor that is a formula, and every layout under
that invented limit gets rejected for nothing.
"""

from __future__ import annotations

import re
from pathlib import Path

from openavc.ui.control_minimums import (
    REFERENCE_HEIGHT_PX,
    REFERENCE_WIDTH_PX,
    RULES,
    minimum_box,
)
from openavc.ui.guide_gen import ARTIFACT, render
from openavc.ui.page_review import (
    HONORED_SHOW_SLOTS,
    TOUCH_MIN_MM,
    TOUCHABLE_TYPES,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _artifact() -> str:
    return (REPO_ROOT / ARTIFACT).read_text(encoding="utf-8")


def _sections() -> dict[str, str]:
    """The guide split on its `## ` headings, so a check can name its section."""
    out: dict[str, str] = {}
    title = "(preamble)"
    for line in _artifact().splitlines():
        if line.startswith("## "):
            title = line[3:].strip()
            out[title] = ""
        else:
            out[title] = out.get(title, "") + line + "\n"
    return out


def _is_separator(line: str) -> bool:
    return set(line) <= set("|- ")


def _rows(section: str) -> list[list[str]]:
    """Table rows in one section, as cell lists, header and rule row dropped.

    The header is the line a `|---|` follows, which is worth detecting rather
    than matching on its text: one of these tables uses an element type as its
    first column heading.
    """
    lines = [line for line in section.splitlines() if line.startswith("|")]
    rows = []
    for i, line in enumerate(lines):
        header = i + 1 < len(lines) and _is_separator(lines[i + 1])
        if _is_separator(line) or header:
            continue
        rows.append([c.strip() for c in line.strip("|").split("|")])
    return rows


FLOOR_SECTIONS = (
    "Controls with a fixed floor",
    "A status LED's floor changes when it draws a caption",
    "Controls whose floor depends on a size you set",
)


# --- The artifact is current -----------------------------------------------


def test_committed_guide_matches_a_fresh_render():
    path = REPO_ROOT / ARTIFACT
    assert path.is_file(), f"{ARTIFACT} is missing -- run 'python -m openavc.ui.guide_gen'"
    assert path.read_text(encoding="utf-8") == render(), (
        f"{ARTIFACT} is stale. Regenerate it with "
        f"'python -m openavc.ui.guide_gen' (never edit it by hand)"
    )


def test_the_artifact_path_is_part_of_a_cross_repo_contract():
    """The cloud fetches this file by URL, so a rename is a silent 404 there.

    Nothing in this repository can fail when that happens -- the caller just
    authors without the numbers, which is the whole failure this guide exists to
    prevent. So the path is pinned here, and moving it means coming through this
    test and updating the URL that reads it.
    """
    assert ARTIFACT == "server/ui/panel_authoring_guide.md"


# --- What it must not say --------------------------------------------------


def test_no_type_without_a_rule_is_given_a_floor():
    """Most element types have no fixed internals, and inventing a floor for one
    rejects layouts that draw correctly.

    What this catches is a floor row that reached the guide without a rule
    behind it -- a hand-written row, or a template that names a type. A bogus
    entry in ``RULES`` itself is not catchable from here (the guide and this test
    read the same table); the guard for that is
    ``tests/e2e/test_control_minimums.py``, which fails when a recorded floor
    still renders whole one pixel under it.
    """
    floorless = set(HONORED_SHOW_SLOTS) - set(RULES)
    assert floorless, "the fixture is only meaningful while some type has no rule"
    sections = _sections()
    for title in FLOOR_SECTIONS:
        for cells in _rows(sections[title]):
            named = cells[0]
            assert named not in floorless, f"{named} has no floor, but {title} gives it one"


def test_every_floorless_type_is_named_as_having_none():
    """Silence would be read as "no rule found", which is a different claim."""
    listed = _sections()["Types with no floor at all"]
    for name in set(HONORED_SHOW_SLOTS) - set(RULES):
        assert f"`{name}`" in listed, f"{name} has no floor and the guide never says so"


def test_every_type_with_a_rule_appears_in_exactly_one_floor_section():
    sections = _sections()
    for name in RULES:
        hits = [
            title
            for title in FLOOR_SECTIONS
            if name in sections[title]
        ]
        assert len(hits) == 1, f"{name} appears in {hits or 'no floor section'}"


def test_a_floor_that_is_a_formula_is_printed_as_one():
    """slider and list scale with a size the author sets. A flat number for
    either is wrong for every element that sets it."""
    scaling = {n: r for n, r in RULES.items() if r.scales_with}
    assert scaling, "the fixture is only meaningful while some floor scales"
    rows = {
        cells[0]: cells
        for cells in _rows(_sections()["Controls whose floor depends on a size you set"])
    }
    for name, rule in scaling.items():
        assert name in rows, f"{name} scales with an authored size and is not in that table"
        floor_cell = rows[name][1]
        assert rule.scales_with.property in floor_cell, (
            f"{name}'s floor is printed as {floor_cell!r}, which hides that it "
            f"moves with {rule.scales_with.property}"
        )


def test_the_caption_floor_is_given_as_two_boxes():
    """A status LED that draws a caption needs a wider box than a bare one, and
    one number for both is wrong in whichever direction it is rounded."""
    bare = minimum_box({"type": "status_led"})
    labelled = minimum_box({"type": "status_led", "label": "Zone 1"})
    assert bare and labelled and labelled.width_px > bare.width_px
    section = _sections()["A status LED's floor changes when it draws a caption"]
    assert f"{bare.width_px:.0f} x {bare.height_px:.0f}" in section
    assert f"{labelled.width_px:.0f} x {labelled.height_px:.0f}" in section


def test_a_font_driven_number_is_never_presented_as_declared():
    """These have no declared floor at all -- the value is what the default
    theme produces, and a theme with larger type moves it."""
    boxes = [minimum_box({"type": name}) for name in RULES]
    assert all(box is not None for box in boxes)
    parts = {
        i.part for box in boxes for i in box.internals if i.origin == "font-driven"
    }
    assert parts, "the fixture is only meaningful while some internal is font-driven"
    text = _artifact()
    theme_section = _sections()["Some numbers above are the theme's, not a declared size"]
    for part in parts:
        assert part in theme_section, f"{part} is font-driven and the guide never says so"
        # ...and it is marked where the tables use it, not only in the footnote.
        marked = re.findall(rf"{re.escape(part)}[^|\n]*", text)
        assert any("font-driven" in m for m in marked), f"{part} is unmarked in the tables"


def test_the_guide_keeps_its_anchor_and_its_arithmetic():
    """Numbers with no reference screen and no conversion are what the panel
    that started all this was authored from."""
    text = _artifact()
    assert f"{REFERENCE_WIDTH_PX} x {REFERENCE_HEIGHT_PX}" in text, "no reference screen"
    assert "percent = px / parent_px * 100" in text, "no percent-to-pixel conversion"
    assert "that container's box" in text, "does not say the parent is the container"


def test_the_finger_rule_carries_its_own_type_list():
    section = _sections()["The finger rule"]
    assert f"{TOUCH_MIN_MM:.0f} mm" in section
    for name in TOUCHABLE_TYPES:
        assert f"`{name}`" in section, f"{name} is touchable and the finger rule omits it"


def test_binding_reach_covers_every_type_the_panel_renders():
    """A slot the renderer never reads is silently inert, so the table is only
    useful if it is complete."""
    rows = {cells[0]: cells[1] for cells in _rows(_sections()["What each type's `show` bindings actually render"])}
    assert set(rows) == set(HONORED_SHOW_SLOTS)
    for name, honored in HONORED_SHOW_SLOTS.items():
        if not honored:
            assert rows[name] == "nothing", f"{name} reads nothing and the guide implies otherwise"
        for slot in honored:
            assert f"`show.{slot}`" in rows[name], f"{name} reads {slot} and the guide omits it"
