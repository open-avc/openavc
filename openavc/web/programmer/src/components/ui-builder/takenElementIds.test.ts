import { afterEach, describe, expect, it, vi } from "vitest";

// The ids a control may not be given are one rule, takenElementIds: every
// door that names a control asks it rather than building the set itself. The
// rule is wrapped, so a case can hand a door a taken set of its own and see
// the door obey it; the unwrapped cases show what the rule itself keeps clear.

vi.mock("./takenElementIds", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./takenElementIds")>();
  return { ...actual, takenElementIds: vi.fn(actual.takenElementIds) };
});

import { takenElementIds } from "./takenElementIds";
import { validateElementId } from "./projectRewrites";
import { addControl, type EditPages } from "../../views/ui-builder/elementEdits";
import { takenIds } from "../../views/ui-builder/paletteDrop";
import { el, page } from "../../test/uiBuilderHarness";
import type { MasterElement, ProjectConfig, UIPage } from "../../api/types";

const owner = vi.mocked(takenElementIds);
const master = (id: string) => ({ id, type: "button", pages: "*" }) as unknown as MasterElement;

function startingPages(): UIPage[] {
  return [
    page("main", [el("btn_1", "button")], { btn_1: { x: 0, y: 0, w: 20, h: 10 } }),
    page("other", [el("button_1", "button")], { button_1: { x: 0, y: 0, w: 20, h: 10 } }),
  ];
}

/** A palette add on the first page; the id the new control was given. */
function paletteAdd(masters: MasterElement[]): string {
  let pages = startingPages();
  const project = { ui: { pages, master_elements: masters } } as unknown as ProjectConfig;
  const editPages: EditPages = (mutate) => {
    pages = mutate(pages);
  };
  addControl(
    { page: pages[0], editPages, project, masters },
    "button",
    { parentId: null, placement: { x: 50, y: 50, w: 20, h: 10 } },
  );
  const made = pages[0].elements.filter((e) => e.id !== "btn_1");
  expect(made).toHaveLength(1);
  return made[0].id;
}

// Back to the rule itself, with no handed-in set left over for the next case.
afterEach(() => owner.mockReset());

describe("the palette's drop and click-to-add", () => {
  it("name a new control past the ids the one rule says are taken", () => {
    const pages = startingPages();
    const masters = [master("button_2")];
    owner.mockReturnValueOnce(new Set(["button_1", "button_2", "button_3"]));
    expect(paletteAdd(masters)).toBe("button_4");
    expect(owner).toHaveBeenCalledWith(pages, ["button_2"]);
  });

  it("hand back the rule's set as it is", () => {
    const pages = startingPages();
    const fromTheRule = new Set(["anything"]);
    owner.mockReturnValueOnce(fromTheRule);
    expect(takenIds(pages, [master("button_2")])).toBe(fromTheRule);
  });

  it("keep clear of another page's controls and of every master", () => {
    expect(paletteAdd([master("button_2")])).toBe("button_3");
  });
});

describe("a rename", () => {
  it("refuses an id the one rule says is taken", () => {
    const pages = startingPages();
    owner.mockReturnValueOnce(new Set(["btn_1", "spare"]));
    expect(validateElementId("spare", "btn_1", pages, [master("button_2")])).toBe(
      'An element with ID "spare" already exists.',
    );
    expect(owner).toHaveBeenCalledWith(pages, ["button_2"]);
  });

  it("may keep its own id, and refuses another page's control or a master's", () => {
    const pages = startingPages();
    const masters = [master("button_2")];
    expect(validateElementId("btn_1", "btn_1", pages, masters)).toBeNull();
    expect(validateElementId("btn_9", "btn_1", pages, masters)).toBeNull();
    expect(validateElementId("button_1", "btn_1", pages, masters)).toBe(
      'An element with ID "button_1" already exists.',
    );
    expect(validateElementId("button_2", "btn_1", pages, masters)).toBe(
      'An element with ID "button_2" already exists.',
    );
  });
});
