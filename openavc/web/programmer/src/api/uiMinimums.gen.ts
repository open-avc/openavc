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
export const UI_REFERENCE = { widthPx: 1280, heightPx: 800 };

/** The panel's rem base: style measurements are px / 14. */
export const REM_BASE_PX = 14;

/** Every condition a ControlConditionalPart may name. */
export const CONTROL_MINIMUM_CONDITIONS = ["label", "presets", "lock_column", "mute_column"] as const;

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
    "repeated": [],
    "conditionals": [],
    "styleProperty": "",
    "styles": {},
    "styleDefault": "",
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
    "repeated": [],
    "conditionals": [],
    "styleProperty": "",
    "styles": {},
    "styleDefault": "",
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
    "repeated": [],
    "conditionals": [],
    "styleProperty": "",
    "styles": {},
    "styleDefault": "",
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
    "repeated": [],
    "conditionals": [],
    "styleProperty": "",
    "styles": {},
    "styleDefault": "",
    "note": "Row height does not change how wide a list has to be."
  },
  "matrix": {
    "baseWidthPx": 95,
    "baseHeightPx": 63,
    "internals": [
      {
        "part": "matrix-cell",
        "widthPx": 44,
        "heightPx": 44,
        "origin": "declared",
        "source": "panel.js MATRIX_CELL_MIN_PX"
      }
    ],
    "scalesWith": null,
    "captionWidthBonusPx": 0.0,
    "repeated": [
      {
        "part": "matrix-cell",
        "sizePx": 44,
        "gapPx": 1,
        "countKey": "sources",
        "countIn": "matrix_config",
        "defaultCount": 0,
        "axis": "width",
        "sizeProperty": "cell_size",
        "origin": "declared",
        "source": "panel.js MATRIX_CELL_MIN_PX + .matrix-grid gap 1px"
      },
      {
        "part": "matrix-cell",
        "sizePx": 44,
        "gapPx": 1,
        "countKey": "destinations",
        "countIn": "matrix_config",
        "defaultCount": 0,
        "axis": "height",
        "sizeProperty": "cell_size",
        "origin": "declared",
        "source": "panel.js MATRIX_CELL_MIN_PX + .matrix-grid gap 1px"
      }
    ],
    "conditionals": [
      {
        "part": "matrix-label",
        "axis": "height",
        "sizePx": 23,
        "when": "label",
        "origin": "font-driven",
        "source": "panel-elements.css .panel-matrix gap + .matrix-label line box"
      },
      {
        "part": "matrix-presets",
        "axis": "height",
        "sizePx": 36,
        "when": "presets",
        "origin": "font-driven",
        "source": "panel-elements.css .matrix-presets padding + .matrix-preset-btn"
      },
      {
        "part": "lock column",
        "axis": "width",
        "sizePx": 45,
        "when": "lock_column",
        "origin": "declared",
        "source": "panel.js renderMatrix extraColDefs + .matrix-grid gap"
      },
      {
        "part": "mute column",
        "axis": "width",
        "sizePx": 45,
        "when": "mute_column",
        "origin": "declared",
        "source": "panel.js renderMatrix extraColDefs + .matrix-grid gap"
      }
    ],
    "styleProperty": "matrix_style",
    "styles": {
      "list": {
        "baseWidthPx": 148,
        "baseHeightPx": 9,
        "internals": [
          {
            "part": "matrix-list-row",
            "widthPx": null,
            "heightPx": 28,
            "origin": "font-driven",
            "source": "panel-elements.css .matrix-list-select padding + inherited font"
          }
        ],
        "scalesWith": null,
        "captionWidthBonusPx": 0.0,
        "repeated": [
          {
            "part": "matrix-list-row",
            "sizePx": 28,
            "gapPx": 6,
            "countKey": "destinations",
            "countIn": "matrix_config",
            "defaultCount": 0,
            "axis": "height",
            "sizeProperty": "",
            "origin": "font-driven",
            "source": "panel-elements.css .matrix-list gap 0.4286rem + row height"
          }
        ],
        "conditionals": [
          {
            "part": "matrix-label",
            "axis": "height",
            "sizePx": 23,
            "when": "label",
            "origin": "font-driven",
            "source": "panel-elements.css .panel-matrix gap + .matrix-label line box"
          },
          {
            "part": "matrix-presets",
            "axis": "height",
            "sizePx": 36,
            "when": "presets",
            "origin": "font-driven",
            "source": "panel-elements.css .matrix-presets padding + .matrix-preset-btn"
          },
          {
            "part": "matrix-lock-btn",
            "axis": "width",
            "sizePx": 32,
            "when": "lock_column",
            "origin": "font-driven",
            "source": "panel-elements.css .matrix-lock-btn + .matrix-list-row gap"
          },
          {
            "part": "matrix-mute-btn",
            "axis": "width",
            "sizePx": 28,
            "when": "mute_column",
            "origin": "font-driven",
            "source": "panel-elements.css .matrix-mute-btn + .matrix-list-row gap"
          }
        ],
        "styleProperty": "",
        "styles": {},
        "styleDefault": "",
        "note": "A list matrix is one dropdown per destination, so its width does not move with the input count at all -- sixteen sources are sixteen options, not sixteen columns. Recording the crosspoint floor for both styles is what the old constant did, and it told a 16-input list it needed 792px when it needs 180. The lock and mute buttons differ in width here because they are glyphs rather than grid tracks, and an unlock glyph is wider than an M."
      }
    },
    "styleDefault": "crosspoint",
    "note": "A function of the counts, which is the whole point of it: 27 + inputs x (cell + 1) wide, 46 + outputs x (cell + 1) tall, plus the lock and mute columns and the element's own label row. The cell is 44 -- the touch floor it will not go below, whatever room it is given -- unless style.cell_size authors another size, in which case the slope moves with it and stays exact. Everything that is TEXT is declared rather than measured from the text: the name column keeps 80px and ellipsises past it, the source legend is one strip that scrolls sideways rather than a block that wraps, and so is the preset bar. Otherwise every one of them would put somebody's typing in this number."
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
    "repeated": [],
    "conditionals": [],
    "styleProperty": "",
    "styles": {},
    "styleDefault": "",
    "note": ""
  },
  "keypad": {
    "baseWidthPx": 86,
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
    "repeated": [],
    "conditionals": [],
    "styleProperty": "",
    "styles": {},
    "styleDefault": "",
    "note": "86 wide rather than the 84 first recorded. The enter key's glyph is wider than a digit, so the grid's three equal columns stop being equal -- that column takes the room it needs and the two digit columns divide what is left, which is what actually gets crushed. How much it needs depends on the font, so this is the widest of the machines measured: 84 is right where that glyph is narrow and two pixels short where it is not. A keypad can never floor below 84 on any machine, because that is where three equal columns reach 20px."
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
    "repeated": [],
    "conditionals": [],
    "styleProperty": "",
    "styles": {},
    "styleDefault": "",
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
    "repeated": [],
    "conditionals": [],
    "styleProperty": "",
    "styles": {},
    "styleDefault": "",
    "note": ""
  }
} as const;

/** Every type that has a floor at all. */
export const TYPES_WITH_MINIMUMS = Object.keys(CONTROL_MINIMUMS);
