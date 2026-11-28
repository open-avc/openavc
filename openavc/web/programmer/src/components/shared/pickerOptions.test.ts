import { describe, expect, it } from "vitest";
import { PAGE_RULES } from "../../api/projectValues.gen";
import { getStepType } from "../macros/macroHelpers";
import { NAVIGATE_SPECIAL_GROUP, pageGroups } from "./pickerOptions";

const ids = (groups: ReturnType<typeof pageGroups>) =>
  groups.map((g) => [g.label, g.options.map((o) => o.value)]);

describe("pageGroups", () => {
  it("lists regular pages first, then overlays and sidebars", () => {
    const pages = [
      { id: "dlg", page_type: "overlay" },
      { id: "home", page_type: "page" },
      { id: "side", page_type: "sidebar" },
      { id: "fresh" },
    ];
    expect(ids(pageGroups(pages))).toEqual([
      ["Pages", ["home", "fresh"]],
      ["Overlays / Sidebars", ["dlg", "side"]],
    ]);
  });

  it("lists a page of a type the panel does not know with the pages, as it draws", () => {
    const pages = [{ id: "home" }, { id: "confirm", page_type: "modal" }, { id: "dlg", page_type: "overlay" }];
    expect(ids(pageGroups(pages))).toEqual([
      ["Pages", ["home", "confirm"]],
      ["Overlays / Sidebars", ["dlg"]],
    ]);
  });

  it("leaves out a group with nothing in it", () => {
    expect(ids(pageGroups([{ id: "dlg", page_type: "overlay" }]))).toEqual([["Overlays / Sidebars", ["dlg"]]]);
    expect(pageGroups([])).toEqual([]);
  });
});

describe("the panel's own moves", () => {
  const moves = Object.values(PAGE_RULES.navigation_words);

  it("are offered by the move picker, each once, as the page rules spell them", () => {
    expect(NAVIGATE_SPECIAL_GROUP.options.map((o) => o.value)).toEqual(moves);
  });

  it("each read as a move of their own in a Navigate Panel step, never a page", () => {
    const summary = getStepType("ui.navigate")!.summary!;
    const said = moves.map((page) => summary({ action: "ui.navigate", page }, [], []));
    expect(said).toEqual(["Back", "Dismiss overlay"]);
    expect(summary({ action: "ui.navigate", page: "home" }, [], [])).toBe("Go to home");
  });
});
