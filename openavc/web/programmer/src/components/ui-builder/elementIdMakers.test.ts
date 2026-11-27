import { describe, expect, it } from "vitest";

// Every door that names a control makes an id the platform's rule takes
// (ELEMENT_ID_RULE, judged in elementId.test.ts), so a control the Builder
// makes is never one Validate then reports, nor one the AI's doors refuse.

import { ELEMENT_ID_RULE } from "../../api/projectValues.gen";
import { createDefaultElement, generateId } from "./newElement";
import {
  ELEMENT_TYPES,
  demoteFromMaster,
  duplicateElementInPage,
  duplicatePage,
  pasteIntoPage,
  promoteToMaster,
} from "./uiBuilderHelpers";
import { el, page } from "../../test/uiBuilderHarness";
import type { MasterElement, UIPage } from "../../api/types";

const BOX = { x: 10, y: 10, w: 20, h: 20 };
const ids = (pages: UIPage[]) => pages.flatMap((p) => p.elements.map((e) => e.id));

describe("every id the Builder makes fits the rule", () => {
  const PLUGIN_SPELLINGS = [
    "plugin:acme_tiles:status_tile",
    "plugin:Acme-Tiles:Status-Tile",
    "plugin:acme:a:b",
    "plugin:acme",
    "plugin:",
  ];

  it("a control off the palette, a plugin element among them", () => {
    for (const type of [...ELEMENT_TYPES.map((t) => t.type), ...PLUGIN_SPELLINGS]) {
      expect(createDefaultElement(type, new Set()).id, type).toMatch(new RegExp(ELEMENT_ID_RULE.pattern));
    }
  });

  it("a plugin element's id is its whole spelling made to fit", () => {
    expect(createDefaultElement("plugin:acme_tiles:status_tile", new Set()).id).toBe(
      "plugin_acme_tiles_status_tile_1",
    );
    expect(createDefaultElement("plugin:Acme-Tiles:Status-Tile", new Set()).id).toBe(
      "plugin_acme_tiles_status_tile_1",
    );
  });

  it("a type that already fits is named as it always was", () => {
    expect(generateId("button", new Set(["button_1"]))).toBe("button_2");
    expect(generateId("status_led", new Set())).toBe("status_led_1");
  });

  it("a type with nothing that fits is named a control", () => {
    expect(generateId("123", new Set())).toBe("control_1");
    expect(generateId("", new Set())).toBe("control_1");
  });

  it("a copy of a page whose id breaks it", () => {
    const pages = [page("Main-Lobby", [el("btn", "button")], { btn: BOX })];
    const copied = duplicatePage(pages, "Main-Lobby", []);
    const made = ids(copied).filter((id) => id !== "btn");
    expect(made).toEqual(["button_main_lobby_copy_1"]);
  });

  it("a duplicate, a paste, a promote and a demote under a taken id", () => {
    const plugin = el("plugin:acme:tile_1", "plugin");
    const pages = [page("main", [el("btn", "button"), plugin], { btn: BOX, "plugin:acme:tile_1": BOX })];
    const fits = new RegExp(ELEMENT_ID_RULE.pattern);

    const duplicated = duplicateElementInPage(pages, { pageId: "main", layoutId: null }, "plugin:acme:tile_1", []);
    expect(ids(duplicated).at(-1)).toMatch(fits);

    const pasted = pasteIntoPage(pages, { pageId: "main", layoutId: null }, { elements: [plugin], placements: {} }, []);
    expect(ids(pasted).at(-1)).toMatch(fits);

    const masters = [{ id: "btn", type: "button", pages: "*" }] as unknown as MasterElement[];
    const promoted = promoteToMaster(pages, [], "main", "btn");
    expect(promoted.masterElements.map((m) => m.id)).toEqual(["btn"]);
    const demoted = demoteFromMaster(pages, masters, "btn", "main");
    expect(ids(demoted.pages).filter((id) => id !== "plugin:acme:tile_1").every((id) => fits.test(id))).toBe(true);
  });
});
