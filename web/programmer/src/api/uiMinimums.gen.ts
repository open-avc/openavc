// GENERATED FILE - DO NOT EDIT.
// Rendered from the control-minimum rules (server/ui/control_minimums.py).
// Regenerate with:  python -m server.ui.minimums_gen
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
  note: string;
}

/** The screen every percentage is reasoned about against. */
export const UI_REFERENCE = { widthPx: 1280, heightPx: 800 };

/** The panel's rem base: style measurements are px / 14. */
export const REM_BASE_PX = 14;

export const CONTROL_MINIMUMS: Record<string, ControlMinimumRule> =
{
  "status_led": {
    "baseWidthPx": 20,
    "baseHeightPx": 20,
    "internals": [
      {
        "part": "led-dot",
        "widthPx": 20,
        "heightPx": 20,
        "origin": "declared",
        "source": "panel-elements.css .led-dot 1.4286rem"
      }
    ],
    "scalesWith": null,
    "captionWidthBonusPx": 9,
    "note": "Dot is 20 and never shrinks. A caption adds an 8px gap plus a sliver of text, so a labelled LED needs 29 before any of the caption is legible; how much more is content, not a minimum."
  },
  "fader": {
    "baseWidthPx": 72,
    "baseHeightPx": 100,
    "internals": [
      {
        "part": "fader-handle",
        "widthPx": 44,
        "heightPx": 44,
        "origin": "declared",
        "source": "panel-elements.css .fader-handle 3.1429rem"
      },
      {
        "part": "fader-scale",
        "widthPx": 28,
        "heightPx": null,
        "origin": "declared",
        "source": "panel-elements.css .fader-scale 2rem"
      }
    ],
    "scalesWith": null,
    "captionWidthBonusPx": 0.0,
    "note": ""
  },
  "slider": {
    "baseWidthPx": 24,
    "baseHeightPx": 37,
    "internals": [],
    "scalesWith": {
      "part": "slider thumb",
      "property": "thumb_size",
      "defaultPx": 44.0,
      "widthCoefficient": 1.0,
      "heightCoefficient": 1.0,
      "source": "panel.js:1696 / --thumb-size (a ::-webkit-slider-thumb pseudo-element)",
      "fromTheme": true
    },
    "captionWidthBonusPx": 0.0,
    "note": ""
  },
  "list": {
    "baseWidthPx": 28,
    "baseHeightPx": 33,
    "internals": [],
    "scalesWith": {
      "part": "list-item",
      "property": "item_height",
      "defaultPx": 44.0,
      "widthCoefficient": 0.0,
      "heightCoefficient": 1.0,
      "source": "panel.js:2099 item_height",
      "fromTheme": false
    },
    "captionWidthBonusPx": 0.0,
    "note": "Row height does not change how wide a list has to be."
  },
  "matrix": {
    "baseWidthPx": 277,
    "baseHeightPx": 234,
    "internals": [
      {
        "part": "matrix-cell",
        "widthPx": 44,
        "heightPx": 44,
        "origin": "declared",
        "source": "panel.js:2294 cell_size"
      }
    ],
    "scalesWith": null,
    "captionWidthBonusPx": 0.0,
    "note": "Constant, NOT a function of the crosspoint count: 2x2, 3x3 and 4x4 all floor here, because .matrix-scroll scrolls the grid internally once it runs out of room."
  },
  "level_meter": {
    "baseWidthPx": 13,
    "baseHeightPx": 80,
    "internals": [
      {
        "part": "meter-segment",
        "widthPx": null,
        "heightPx": 2,
        "origin": "declared",
        "source": "panel-elements.css .meter-segment min-height"
      }
    ],
    "scalesWith": null,
    "captionWidthBonusPx": 0.0,
    "note": ""
  },
  "keypad": {
    "baseWidthPx": 84,
    "baseHeightPx": 221,
    "internals": [
      {
        "part": "keypad-key",
        "widthPx": null,
        "heightPx": 36,
        "origin": "font-driven",
        "source": "panel-elements.css .keypad-key font-size 1.2857rem + padding"
      }
    ],
    "scalesWith": null,
    "captionWidthBonusPx": 0.0,
    "note": ""
  },
  "select": {
    "baseWidthPx": 44,
    "baseHeightPx": 51,
    "internals": [
      {
        "part": "native control",
        "widthPx": null,
        "heightPx": 30,
        "origin": "font-driven",
        "source": "panel-elements.css select/input padding + inherited font"
      }
    ],
    "scalesWith": null,
    "captionWidthBonusPx": 0.0,
    "note": ""
  },
  "text_input": {
    "baseWidthPx": 44,
    "baseHeightPx": 51,
    "internals": [
      {
        "part": "native control",
        "widthPx": null,
        "heightPx": 30,
        "origin": "font-driven",
        "source": "panel-elements.css select/input padding + inherited font"
      }
    ],
    "scalesWith": null,
    "captionWidthBonusPx": 0.0,
    "note": ""
  }
} as const;

/** Every type that has a floor at all. */
export const TYPES_WITH_MINIMUMS = Object.keys(CONTROL_MINIMUMS);
