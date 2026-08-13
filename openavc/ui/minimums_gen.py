"""Render the control-minimum rules into the Programmer IDE's types.

Produces ``openavc/web/programmer/src/api/uiMinimums.gen.ts`` from ``RULES`` in
``control_minimums.py``. Data only -- the rows, not the arithmetic over them --
so there is no second copy of the logic to drift. The Builder's resolver
consumes these rows; the numbers themselves exist once, in Python, measured
against a real browser and pinned by ``tests/e2e/test_control_minimums.py``.

Run:  python -m openavc.ui.minimums_gen
A test compares the committed file against a fresh render, so editing the
rules without regenerating (or hand-editing the artifact) fails CI.
"""

from __future__ import annotations

import json
from pathlib import Path

from openavc.ui.control_minimums import (
    CONDITIONS,
    LAYOUTS,
    REFERENCE_HEIGHT_PX,
    REFERENCE_WIDTH_PX,
    REM_BASE_PX,
    RULES,
)

ARTIFACT = "openavc/web/programmer/src/api/uiMinimums.gen.ts"

BANNER = """\
// GENERATED FILE - DO NOT EDIT.
// Rendered from the control-minimum rules (openavc/ui/control_minimums.py).
// Regenerate with:  python -m openavc.ui.minimums_gen
// A test compares this file against a fresh render, so hand edits fail CI.
//
// These are the parts of a control that do NOT shrink when its box does. They
// were measured in a real browser against the real panel, not derived from the
// stylesheets -- padding, gaps and flex behaviour do not add up by hand, and
// three of the numbers live in panel.js rather than in any stylesheet.
//
// Types absent from this table have no fixed internals at all: a button, a
// label, an image. Those are limited by their text, which is unbounded and
// theme-dependent, and is therefore not a minimum box.

"""

TYPES = """\
/** A part that keeps its size no matter how small the box gets. */
export interface ControlFixedInternal {
  /** The rendered class, so a warning can name what is being crushed. */
  part: string;
  widthPx: number | null;
  heightPx: number | null;
  /**
   * `declared` -- a constant in the stylesheet or the renderer.
   * `font-driven` -- falls out of the theme's font size plus padding, so this
   * is what the DEFAULT theme produces and a larger font moves it.
   */
  origin: "declared" | "font-driven";
  /** Where the number actually lives, so it can be checked. */
  source: string;
}

/** A part drawn once per item the element's config asks for. */
export interface ControlRepeatedInternal {
  part: string;
  sizePx: number;
  /** The gap after each item: the floor's slope is size + gap. */
  gapPx: number;
  /** The key holding the count, inside `countIn`. */
  countKey: string;
  countIn: string;
  defaultCount: number;
  axis: "width" | "height";
  /** A key in `element.style` (in rem) that overrides `sizePx`. */
  sizeProperty: string;
  origin: "declared" | "font-driven";
  source: string;
  /**
   * How the count becomes a number of parts along this axis. One of
   * CONTROL_MINIMUM_LAYOUTS: `linear` is one part per item; `grid_columns` /
   * `grid_rows` are the tile wall, where one list fills both axes.
   */
  layout: string;
}

/** Chrome that is there or not, depending on how the element is configured. */
export interface ControlConditionalPart {
  part: string;
  axis: "width" | "height";
  sizePx: number;
  /** One of CONTROL_MINIMUM_CONDITIONS. */
  when: string;
  origin: "declared" | "font-driven";
  source: string;
}

/** An internal whose size is authored rather than fixed. */
export interface ControlScalingInternal {
  part: string;
  /** The element property that overrides it (a rem value). */
  property: string;
  defaultPx: number;
  /** Measured slope of the floor against this internal's size. */
  widthCoefficient: number;
  heightCoefficient: number;
  source: string;
  /** Whether a theme default applies when the element says nothing. */
  fromTheme: boolean;
}

export interface ControlMinimumRule {
  /** The floor before any authored internal or caption is added. */
  baseWidthPx: number;
  baseHeightPx: number;
  internals: ControlFixedInternal[];
  scalesWith: ControlScalingInternal | null;
  /** Extra width once the control draws a caption beside its control. */
  captionWidthBonusPx: number;
  repeated: ControlRepeatedInternal[];
  conditionals: ControlConditionalPart[];
  /**
   * An element property that picks a different rule -- `matrix_style`. The rule
   * carrying this IS the default style; `styles` holds only the alternatives.
   */
  styleProperty: string;
  styles: Record<string, ControlMinimumRule>;
  /** What the style this rule IS is called, for anything that has to name it. */
  styleDefault: string;
  note: string;
}

/** The screen every percentage is reasoned about against. */
export const UI_REFERENCE = { widthPx: %(ref_w)d, heightPx: %(ref_h)d };

/** The panel's rem base: style measurements are px / %(rem_base)d. */
export const REM_BASE_PX = %(rem_base)d;

/** Every condition a ControlConditionalPart may name. */
export const CONTROL_MINIMUM_CONDITIONS = %(conditions)s as const;

/** Every layout a ControlRepeatedInternal may name. */
export const CONTROL_MINIMUM_LAYOUTS = %(layouts)s as const;

"""


def _internal(i) -> dict:
    return {
        "part": i.part,
        "widthPx": i.width_px,
        "heightPx": i.height_px,
        "origin": i.origin,
        "source": i.source,
    }


def _repeated(r) -> dict:
    return {
        "part": r.part,
        "sizePx": r.size_px,
        "gapPx": r.gap_px,
        "countKey": r.count_key,
        "countIn": r.count_in,
        "defaultCount": r.default_count,
        "axis": r.axis,
        "sizeProperty": r.size_property,
        "origin": r.origin,
        "source": r.source,
        "layout": r.layout,
    }


def _conditional(c) -> dict:
    return {
        "part": c.part,
        "axis": c.axis,
        "sizePx": c.size_px,
        "when": c.when,
        "origin": c.origin,
        "source": c.source,
    }


def _rule(r) -> dict:
    return {
        "baseWidthPx": r.base_width_px,
        "baseHeightPx": r.base_height_px,
        "internals": [_internal(i) for i in r.internals],
        "scalesWith": None if r.scales_with is None else {
            "part": r.scales_with.part,
            "property": r.scales_with.property,
            "defaultPx": r.scales_with.default_px,
            "widthCoefficient": r.scales_with.width_coefficient,
            "heightCoefficient": r.scales_with.height_coefficient,
            "source": r.scales_with.source,
            "fromTheme": r.scales_with.from_theme,
        },
        "captionWidthBonusPx": r.caption_width_bonus_px,
        "repeated": [_repeated(i) for i in r.repeated],
        "conditionals": [_conditional(c) for c in r.conditionals],
        "styleProperty": r.style_property,
        "styles": {name: _rule(sub) for name, sub in r.styles.items()},
        "styleDefault": r.style_default,
        "note": r.note,
    }


def render() -> str:
    body = json.dumps(
        {name: _rule(rule) for name, rule in RULES.items()},
        indent=2,
        ensure_ascii=False,
    )
    types = TYPES % {
        "ref_w": REFERENCE_WIDTH_PX,
        "ref_h": REFERENCE_HEIGHT_PX,
        "rem_base": REM_BASE_PX,
        "conditions": json.dumps(list(CONDITIONS)),
        "layouts": json.dumps(list(LAYOUTS)),
    }
    table = (
        "export const CONTROL_MINIMUMS: Record<string, ControlMinimumRule> =\n"
        f"{body} as const;\n\n"
        "/** Every type that has a floor at all. */\n"
        "export const TYPES_WITH_MINIMUMS = Object.keys(CONTROL_MINIMUMS);\n"
    )
    return BANNER + types + table


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    path = repo_root / ARTIFACT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(), encoding="utf-8", newline="\n")
    print(f"wrote {ARTIFACT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
