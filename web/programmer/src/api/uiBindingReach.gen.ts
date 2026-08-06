// GENERATED FILE - DO NOT EDIT.
// Rendered from the binding-reach tables (server/ui/page_review.py).
// Regenerate with:  python -m server.ui.review_gen
// A test compares this file against a fresh render, so hand edits fail CI.
//
// `show` accepts the same four slots for every element type, and each render
// function in web/panel/panel.js reads only some of them. A slot the renderer
// never looks at is silently inert: the element draws, the state key resolves,
// and the thing the author asked for simply never happens. That is how a label
// ended up carrying ONLINE / OFFLINE state text the panel has no code to draw.
//
// `visible_when` is absent on purpose -- it is registered for every element
// from the page tree, so it is honored everywhere and is never worth a warning.

/** Which `show` slots each element type's renderer actually reads. */
export const HONORED_SHOW_SLOTS: Record<string, string[]> =
{
  "button": [
    "look"
  ],
  "camera_preset": [
    "look"
  ],
  "clock": [
    "value"
  ],
  "fader": [
    "value"
  ],
  "gauge": [
    "value"
  ],
  "group": [],
  "image": [],
  "keypad": [],
  "label": [
    "value"
  ],
  "level_meter": [
    "value"
  ],
  "list": [
    "items",
    "value"
  ],
  "matrix": [],
  "page_nav": [],
  "plugin": [],
  "select": [
    "look",
    "value"
  ],
  "slider": [
    "value"
  ],
  "status_led": [
    "look"
  ],
  "text_input": [
    "value"
  ]
};

/**
 * Types whose `look` binding renders per-state TEXT.
 *
 * Everything else that reads `look` takes only colour from it -- a status LED
 * tints its dot, a select styles its options -- so a `states[].label` on those
 * never appears anywhere.
 */
export const STATE_LABEL_TYPES: string[] = ["button", "camera_preset"];

/** The slots worth naming in a message, in the order a reader expects them. */
export const REVIEWED_SHOW_SLOTS = ["value", "look", "items"] as const;
