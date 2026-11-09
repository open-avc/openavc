import { describe, it, expect } from "vitest";
import { reviewPage, reviewMasterElement, reviewWarningsByElement } from "./uiBuilderHelpers";
import type { MasterElement, UIElement, UIPage } from "../../api/types";

// The Builder's page review walks a page twice: element by element (the checks
// an element gets on its own) and arrangement by arrangement (each box, the
// siblings sharing a container, the masters underneath). A master element gets
// the same element checks and the same per-box checks against its own boxes.
// These pin the order those checks report in and the edges of both walks,
// including the options only the Builder passes: the arrangement on the canvas
// and the elements one edit touched. tests/test_ui_review_parity.py holds the
// sentences to the AI side's; tests/test_ui_page_review_walk_pinned.py pins the
// same cases there.

const PX_W = 12.8;
const PX_H = 8;
const FULL = { x: 0, y: 0, w: 100, h: 100 };

type Box = { x: number; y: number; w: number; h: number };

function page(
  elements: Record<string, unknown>[],
  placements: Record<string, Box>,
  extraLayouts: Record<string, unknown>[] = [],
): UIPage {
  return {
    id: "main",
    name: "Main",
    elements: elements as unknown as UIElement[],
    layouts: [
      { id: "landscape", orientation: "landscape", primary: true, placements, hidden: [] },
      ...extraLayouts,
    ],
  } as unknown as UIPage;
}

const sequence = (findings: { kind: string; elementId: string }[]) =>
  findings.map((f) => [f.kind, f.elementId]);

const KEYPAD = {
  id: "pad",
  type: "keypad",
  icon: "x",
  auto_send: true,
  auto_send_delay_ms: 200,
  bindings: { show: { look: { key: "var.a" } }, do: { press: { action: "delay" } } },
};
const KEYPAD_ORDER = [
  ["binding_not_rendered", "pad"],
  ["binding_not_dispatched", "pad"],
  ["property_not_rendered", "pad"],
  ["keypad_sends_early", "pad"],
];

const LED = { id: "led", type: "status_led", label: "Ready" };
const tinyLed = { x: 0, y: 0, w: 28 / PX_W, h: 44 / PX_H };
const TINY_LED =
  "led (status_led) is 28x44px at the 1280x800 reference, too small for what it draws: " +
  "28px wide, needs 29px (led-dot is 20px). Give it w at least 2.27% of the page.";

describe("the element checks", () => {
  it("report in one order on a page", () => {
    expect(sequence(reviewPage(page([KEYPAD], { pad: FULL })))).toEqual(KEYPAD_ORDER);
  });

  it("report in the same order and words on a master", () => {
    const onPage = reviewPage(page([KEYPAD], { pad: FULL }));
    const asMaster = reviewMasterElement(KEYPAD as unknown as MasterElement);
    expect(sequence(asMaster)).toEqual(KEYPAD_ORDER);
    expect(asMaster.map((f) => f.message)).toEqual(onPage.map((f) => f.message));
  });

  it("name an unknown type first and still read its actions", () => {
    const findings = reviewMasterElement({
      id: "w",
      type: "widgetx",
      bindings: { do: { press: { action: "delay" } } },
    } as unknown as MasterElement);
    expect(sequence(findings)).toEqual([
      ["unknown_element_type", "w"],
      ["binding_not_dispatched", "w"],
    ]);
  });

  it("say nothing to draw after the binding and action findings", () => {
    const findings = reviewMasterElement({
      id: "lb",
      type: "label",
      bindings: { show: { items: { key_pattern: "x" } }, do: { press: { action: "delay" } } },
    } as unknown as MasterElement);
    expect(sequence(findings)).toEqual([
      ["binding_not_rendered", "lb"],
      ["binding_not_dispatched", "lb"],
      ["nothing_to_draw", "lb"],
    ]);
  });

  it("point a label's per-state icon at its own icon", () => {
    const findings = reviewMasterElement({
      id: "lab",
      type: "label",
      text: "A",
      bindings: { show: { look: { key: "var.a", states: { on: { icon: "star" } } } } },
    } as unknown as MasterElement);
    expect(findings.map((f) => f.message)).toEqual([
      "lab (label): show.look sets an icon for on, but a label takes only color and text " +
        "from show.look, so that icon never appears. Set the element's own icon -- it draws " +
        "that one, it just cannot change it per state.",
    ]);
  });

  it("are all a page with no arrangement gets", () => {
    const bare = {
      id: "bare",
      name: "Bare",
      elements: [
        { id: "lbl", type: "label", text: "A", bindings: { show: { items: { key_pattern: "x" } } } },
      ],
    } as unknown as UIPage;
    expect(sequence(reviewPage(bare))).toEqual([["binding_not_rendered", "lbl"]]);
  });
});

describe("the elements one edit touched", () => {
  it("are the only ones whose own checks run", () => {
    const other = { ...KEYPAD, id: "other" };
    const findings = reviewPage(page([KEYPAD, other], { pad: FULL, other: FULL }), {
      touched: new Set(["other"]),
    });
    // ...and the overlap they share, which is the touched one's to fix.
    expect(sequence(findings)).toEqual([
      ...KEYPAD_ORDER.map(([kind]) => [kind, "other"]),
      ["overlap", "other"],
    ]);
  });

  it("are the only ones whose boxes are measured", () => {
    const leds = [LED, { ...LED, id: "led2" }];
    const findings = reviewPage(page(leds, { led: tinyLed, led2: { ...tinyLed, x: 50 } }), {
      touched: new Set(["led2"]),
    });
    expect(sequence(findings)).toEqual([["too_small_for_contents", "led2"]]);
  });

  it("leave out an overlap between two untouched elements", () => {
    const labels = [
      { id: "a", type: "label", text: "A" },
      { id: "b", type: "label", text: "B" },
      { id: "c", type: "label", text: "C" },
    ];
    const boxes = {
      a: { x: 10, y: 10, w: 20, h: 10 },
      b: { x: 15, y: 10, w: 20, h: 10 },
      c: { x: 60, y: 60, w: 10, h: 10 },
    };
    expect(reviewPage(page(labels, boxes), { touched: new Set(["c"]) })).toEqual([]);
    expect(sequence(reviewPage(page(labels, boxes), { touched: new Set(["b"]) }))).toEqual([
      ["overlap", "a"],
    ]);
  });
});

describe("the arrangement reviewed", () => {
  const compact = {
    id: "compact",
    orientation: "landscape",
    primary: false,
    placements: { led: tinyLed },
    hidden: [],
  };

  it("is every arrangement when none is named, the variant's own words on it", () => {
    expect(reviewPage(page([LED], { led: FULL }, [compact])).map((f) => f.message)).toEqual([
      TINY_LED.replace(/\.$/, " in the 'compact' arrangement."),
    ]);
  });

  it("is only the one named", () => {
    const p = page([LED], { led: tinyLed }, [{ ...compact, placements: { led: FULL } }]);
    expect(reviewPage(p, { layoutId: "compact" })).toEqual([]);
    expect(reviewPage(p, { layoutId: "landscape" }).map((f) => f.message)).toEqual([TINY_LED]);
  });

  it("is the primary when the name is unknown, and on the canvas when none is open", () => {
    const p = page([LED], { led: tinyLed }, [{ ...compact, placements: { led: FULL } }]);
    expect(reviewPage(p, { layoutId: "nope" }).map((f) => f.message)).toEqual([TINY_LED]);
    expect([...reviewWarningsByElement(p, null)]).toEqual([["led", TINY_LED]]);
  });
});

describe("the container chain", () => {
  const nested = [
    { ...LED, parent: "inner" },
    { id: "inner", type: "group", parent: "outer" },
    { id: "outer", type: "group" },
  ];
  const tooSmallIn = (name: string) =>
    "led (status_led) is 28x44px at the 1280x800 reference, too small for what it draws: " +
    `28px wide, needs 29px (led-dot is 20px). '${name}' is 28px wide, too small to hold it ` +
    `at any width: give '${name}' w at least 2.27% of the page.`;

  it("stops at a container this arrangement does not place", () => {
    const findings = reviewPage(page(nested, { led: FULL, inner: tinyLed }));
    expect(findings.map((f) => [f.kind, f.elementId, f.message])).toEqual([
      ["too_small_for_contents", "led", tooSmallIn("inner")],
      [
        "no_placement",
        "outer",
        "outer (group) has no placement, so it fills the page edge to edge and covers " +
          "whatever is already there. Give it {x, y, w, h}.",
      ],
    ]);
  });

  it("reaches the outermost container that is placed", () => {
    const findings = reviewPage(page(nested, { led: FULL, inner: FULL, outer: tinyLed }));
    expect(findings.map((f) => f.message)).toEqual([tooSmallIn("outer")]);
  });

  it("stops at an unplaced container in the middle, short of a placed one above it", () => {
    const chain = [
      { ...LED, parent: "c1" },
      { id: "c1", type: "group", parent: "c2" },
      { id: "c2", type: "group", parent: "c3" },
      { id: "c3", type: "group" },
    ];
    const findings = reviewPage(
      page(chain, { led: FULL, c1: tinyLed, c3: { x: 0, y: 0, w: 50, h: 50 } }),
    );
    expect(findings.map((f) => [f.kind, f.elementId, f.message])).toEqual([
      ["too_small_for_contents", "led", tooSmallIn("c1")],
      [
        "no_placement",
        "c2",
        "c2 (group) has no placement, so it fills its container 'c3' edge to edge and " +
          "covers whatever is already there. Give it {x, y, w, h}.",
      ],
    ]);
  });
});

describe("a hidden element", () => {
  it("overlaps nothing", () => {
    const labels = [
      { id: "a", type: "label", text: "A" },
      { id: "b", type: "label", text: "B" },
    ];
    const boxes = { a: { x: 10, y: 10, w: 20, h: 10 }, b: { x: 15, y: 10, w: 20, h: 10 } };
    expect(sequence(reviewPage(page(labels, boxes)))).toEqual([["overlap", "a"]]);
    const hiding = page(labels, boxes);
    (hiding.layouts as unknown as { hidden: string[] }[])[0].hidden = ["b"];
    expect(reviewPage(hiding)).toEqual([]);
  });

  it("covers no master", () => {
    const nav = {
      id: "nav", type: "label", text: "Nav", pages: "*",
      placements: { landscape: { x: 0, y: 0, w: 20, h: 10 } },
    } as unknown as MasterElement;
    const labels = [{ id: "a", type: "label", text: "A" }];
    const boxes = { a: { x: 5, y: 2, w: 10, h: 5 } };
    expect(sequence(reviewPage(page(labels, boxes), { masters: [nav] }))).toEqual([
      ["covers_master", "a"],
    ]);
    const hiding = page(labels, boxes);
    (hiding.layouts as unknown as { hidden: string[] }[])[0].hidden = ["a"];
    expect(reviewPage(hiding, { masters: [nav] })).toEqual([]);
  });
});

describe("the binding checks", () => {
  it("count an empty per-state label as text the type drops", () => {
    const findings = reviewMasterElement({
      id: "led",
      type: "status_led",
      bindings: { show: { look: { key: "var.a", states: { on: { label: "" } } } } },
    } as unknown as MasterElement);
    expect(findings.map((f) => [f.kind, f.message])).toEqual([
      [
        "binding_not_rendered",
        "led (status_led): show.look.states sets a label for on, but a status_led takes " +
          "only color from show.look, so that text never appears. Use the element's own " +
          "label, or a label element bound to the same key.",
      ],
    ]);
  });

  it("excuse only show.value from its key when the source is a macro's progress", () => {
    const label = (slot: string) =>
      ({
        id: "lbl",
        type: "label",
        text: "A",
        bindings: { show: { [slot]: { source: "macro_progress" } } },
      }) as unknown as MasterElement;
    expect(reviewMasterElement(label("value"))).toEqual([]);
    expect(reviewMasterElement(label("look")).map((f) => [f.kind, f.message])).toEqual([
      [
        "binding_without_key",
        "lbl (label): show.look has no state key, so it reads nothing and never updates. " +
          "Its appearance stays on whatever an unmatched state gives it. Pick the state key " +
          "it should read, or remove the binding.",
      ],
    ]);
  });
});

describe("a master's own boxes", () => {
  const ledMaster = (placements?: Record<string, unknown>) =>
    ({ ...LED, id: "m", ...(placements ? { placements } : {}) }) as unknown as MasterElement;

  it("are optional: without them only the element checks run", () => {
    expect(sequence(reviewMasterElement(KEYPAD as unknown as MasterElement))).toEqual(KEYPAD_ORDER);
    expect(reviewMasterElement(ledMaster())).toEqual([]);
  });

  it("are passed over when one is not a finite size", () => {
    expect(reviewMasterElement(ledMaster({ landscape: { w: Infinity, h: 5 } }))).toEqual([]);
  });

  it("are measured per orientation and said once", () => {
    const findings = reviewMasterElement(
      ledMaster({ landscape: { x: 0, y: 0, w: 1, h: 1 }, portrait: { w: 1, h: 1 } }),
    );
    expect(findings.map((f) => f.message)).toEqual([
      "m (status_led) is 13x8px at the 1280x800 reference, too small for what it draws: " +
        "13px wide, needs 29px (led-dot is 20px); 8px tall, needs 20px (led-dot is 20px). " +
        "Give it w at least 2.27% and h at least 2.5% of the page in the landscape arrangement.",
    ]);
  });

  it("read a missing width as the whole screen", () => {
    const findings = reviewMasterElement(ledMaster({ landscape: { h: 1 } }));
    expect(findings).toHaveLength(1);
    expect(findings[0].message).toContain("1280x8px");
  });
});
