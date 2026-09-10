import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import {
  duplicateElementInPage,
  duplicateElementsInPage,
  clipboardForSelection,
  pasteIntoPage,
  getPlacement,
} from "./uiBuilderHelpers";
import { useUIBuilderStore } from "../../store/uiBuilderStore";
import { useProjectStore } from "../../store/projectStore";
import type { ProjectConfig, UIElement, UIPage } from "../../api/types";

// A container is its contents. Everything below is about that one sentence
// surviving the two paths a copy can take -- Duplicate, and Copy/Paste -- with
// the geometry intact, because geometry is not on the element: a child's box is
// percentages OF ITS CONTAINER, and the number means something different the
// moment the copy lands somewhere else.

const el = (id: string, type: string, parent: string | null = null): UIElement =>
  ({ id, type, bindings: {}, parent }) as unknown as UIElement;

/** A card: a container with a button and a label in it, plus a loose control. */
function cardPage(): UIPage {
  return {
    id: "main",
    name: "Main",
    elements: [
      el("card", "group"),
      el("btn", "button", "card"),
      el("lbl", "label", "card"),
      el("loose", "button"),
    ],
    layouts: [
      {
        id: "landscape",
        orientation: "landscape",
        primary: true,
        hidden: [],
        placements: {
          card: { x: 10, y: 10, w: 40, h: 40 },
          btn: { x: 5, y: 5, w: 50, h: 20 },
          lbl: { x: 5, y: 40, w: 90, h: 20 },
          loose: { x: 70, y: 70, w: 20, h: 10 },
        },
      },
    ],
  } as unknown as UIPage;
}

const pageOf = (pages: UIPage[]) => pages.find((p) => p.id === "main")!;

/** Whatever the mutation added, in the order it added it. */
function added(before: UIPage, after: UIPage): UIElement[] {
  const was = new Set(before.elements.map((e) => e.id));
  return after.elements.filter((e) => !was.has(e.id));
}

describe("duplicating a container", () => {
  it("brings its contents, parented to the copy and placed the same", () => {
    const before = cardPage();
    const after = pageOf(duplicateElementInPage([before], "main", "card"));
    const copies = added(before, after);

    expect(copies.map((e) => e.type)).toEqual(["group", "button", "label"]);
    const [group, button, label] = copies;
    expect(group.parent ?? null).toBeNull();
    expect(button.parent).toBe(group.id);
    expect(label.parent).toBe(group.id);

    // The children keep their container-relative boxes byte for byte -- the
    // container already moved, and moving them again would slide the contents
    // around inside the copy.
    expect(getPlacement(after, button.id)).toEqual({ x: 5, y: 5, w: 50, h: 20 });
    expect(getPlacement(after, label.id)).toEqual({ x: 5, y: 40, w: 90, h: 20 });
    // Only the top copy takes the down-right nudge.
    expect(getPlacement(after, group.id)).toEqual({ x: 12.5, y: 12.5, w: 40, h: 40 });
  });

  it("gives every copied element a fresh id", () => {
    const before = cardPage();
    const after = pageOf(duplicateElementInPage([before], "main", "card"));
    const copies = added(before, after);

    const originals = new Set(before.elements.map((e) => e.id));
    for (const c of copies) expect(originals.has(c.id)).toBe(false);
    expect(new Set(after.elements.map((e) => e.id)).size).toBe(after.elements.length);
  });

  it("lands the contents after the container, in the order they were in", () => {
    const before = cardPage();
    const after = pageOf(duplicateElementInPage([before], "main", "card"));
    const copies = added(before, after);
    const at = (id: string) => after.elements.findIndex((e) => e.id === id);

    expect(at(copies[0].id)).toBeLessThan(at(copies[1].id));
    expect(at(copies[1].id)).toBeLessThan(at(copies[2].id));
  });

  it("leaves the original alone", () => {
    const before = cardPage();
    const after = pageOf(duplicateElementInPage([before], "main", "card"));

    for (const original of before.elements) {
      const still = after.elements.find((e) => e.id === original.id)!;
      expect(still.parent ?? null).toBe(original.parent ?? null);
      expect(getPlacement(after, original.id)).toEqual(getPlacement(before, original.id));
    }
  });

  it("copies a container inside a container to full depth", () => {
    const before = {
      id: "main",
      name: "Main",
      elements: [
        el("outer", "group"),
        el("inner", "group", "outer"),
        el("deep", "button", "inner"),
      ],
      layouts: [
        {
          id: "landscape",
          orientation: "landscape",
          primary: true,
          hidden: [],
          placements: {
            outer: { x: 0, y: 0, w: 60, h: 60 },
            inner: { x: 10, y: 10, w: 50, h: 50 },
            deep: { x: 20, y: 20, w: 40, h: 40 },
          },
        },
      ],
    } as unknown as UIPage;

    const after = pageOf(duplicateElementInPage([before], "main", "outer"));
    const [outer, inner, deep] = added(before, after);

    expect(inner.parent).toBe(outer.id);
    expect(deep.parent).toBe(inner.id);
    expect(getPlacement(after, inner.id)).toEqual({ x: 10, y: 10, w: 50, h: 50 });
    expect(getPlacement(after, deep.id)).toEqual({ x: 20, y: 20, w: 40, h: 40 });
  });

  it("rewires a reference between two children to the copied sibling", () => {
    const before = cardPage();
    before.elements[2] = {
      ...before.elements[2],
      bindings: { show: { value: { source: "state", key: "ui.btn.value" } } },
    } as unknown as UIElement;

    const after = pageOf(duplicateElementInPage([before], "main", "card"));
    const [, button, label] = added(before, after);

    const key = (
      (label.bindings as Record<string, Record<string, Record<string, string>>>).show.value
    ).key;
    expect(key).toBe(`ui.${button.id}.value`);
    // And the original still reads the original.
    const originalKey = (
      (after.elements.find((e) => e.id === "lbl")!.bindings as Record<
        string,
        Record<string, Record<string, string>>
      >).show.value
    ).key;
    expect(originalKey).toBe("ui.btn.value");
  });

  it("never takes an id a master element already owns", () => {
    const before = cardPage();
    // The ids this page would otherwise hand out, claimed by masters. Masters
    // share the ui.<id> runtime namespace, so a collision is a real one.
    const masters = ["group_1", "button_1", "label_1"];
    const after = pageOf(duplicateElementInPage([before], "main", "card", masters));

    for (const c of added(before, after)) expect(masters).not.toContain(c.id);
  });
});

describe("duplicating a selection", () => {
  it("does not copy a child twice when its container is selected too", () => {
    const before = cardPage();
    const after = pageOf(duplicateElementsInPage([before], "main", ["card", "btn"]));
    const copies = added(before, after);

    expect(copies).toHaveLength(3);
    expect(copies.map((e) => e.type)).toEqual(["group", "button", "label"]);
    expect(copies[1].parent).toBe(copies[0].id);
  });

  it("still copies two unrelated selections", () => {
    const before = cardPage();
    const after = pageOf(duplicateElementsInPage([before], "main", ["card", "loose"]));

    expect(added(before, after)).toHaveLength(4);
  });
});

describe("copy and paste", () => {
  it("keeps the internal parenting of a copied container", () => {
    const before = cardPage();
    const clip = clipboardForSelection(before, ["card"]);
    expect(clip.elements.map((e) => e.id)).toEqual(["card", "btn", "lbl"]);

    const after = pageOf(pasteIntoPage([before], "main", clip));
    const [group, button, label] = added(before, after);

    expect(group.parent ?? null).toBeNull();
    expect(button.parent).toBe(group.id);
    expect(label.parent).toBe(group.id);
    // A child that went back into its container keeps container percentages.
    expect(getPlacement(after, button.id)).toEqual({ x: 5, y: 5, w: 50, h: 20 });
    expect(getPlacement(after, label.id)).toEqual({ x: 5, y: 40, w: 90, h: 20 });
  });

  it("ejects a child copied without its container, at the size the eye saw", () => {
    const before = cardPage();
    const clip = clipboardForSelection(before, ["btn"]);

    // card is 40x40 at (10,10); btn is 5%,5% 50x20 of that.
    expect(clip.placements.btn).toEqual({ x: 12, y: 12, w: 20, h: 8 });

    const after = pageOf(pasteIntoPage([before], "main", clip));
    const [pasted] = added(before, after);
    expect(pasted.parent ?? null).toBeNull();
    const box = getPlacement(after, pasted.id);
    expect(box.w).toBe(20);
    expect(box.h).toBe(8);
  });

  it("widens a selection to what is inside it, and stores each box in its own space", () => {
    const before = cardPage();
    const clip = clipboardForSelection(before, ["card", "loose"]);

    expect(clip.elements.map((e) => e.id)).toEqual(["card", "btn", "lbl", "loose"]);
    // Kept its container: stored relative. Page level: stored as drawn.
    expect(clip.placements.btn).toEqual({ x: 5, y: 5, w: 50, h: 20 });
    expect(clip.placements.card).toEqual({ x: 10, y: 10, w: 40, h: 40 });
    expect(clip.placements.loose).toEqual({ x: 70, y: 70, w: 20, h: 10 });
  });

  it("never takes an id a master element already owns", () => {
    const before = cardPage();
    const masters = ["group_1", "button_1", "label_1"];
    const clip = clipboardForSelection(before, ["card"]);
    const after = pageOf(pasteIntoPage([before], "main", clip, masters));

    for (const c of added(before, after)) expect(masters).not.toContain(c.id);
  });

  it("rewires a reference between two pasted children to the pasted sibling", () => {
    const before = cardPage();
    before.elements[2] = {
      ...before.elements[2],
      bindings: { show: { value: { source: "state", key: "ui.btn.value" } } },
    } as unknown as UIElement;

    const clip = clipboardForSelection(before, ["card"]);
    const after = pageOf(pasteIntoPage([before], "main", clip));
    const [, button, label] = added(before, after);

    const key = (
      (label.bindings as Record<string, Record<string, Record<string, string>>>).show.value
    ).key;
    expect(key).toBe(`ui.${button.id}.value`);
  });
});

describe("undo after duplicating a container", () => {
  const project = (pages: UIPage[]): ProjectConfig =>
    ({
      devices: [],
      macros: [],
      variables: [],
      ui: { pages, settings: {}, master_elements: [] },
    }) as unknown as ProjectConfig;

  beforeEach(() => {
    vi.useFakeTimers();
    useUIBuilderStore.setState({ undoStack: [], redoStack: [] });
  });

  afterEach(() => {
    vi.clearAllTimers();
    vi.useRealTimers();
    useProjectStore.setState({ project: null, dirty: false });
  });

  it("takes the whole copy back on one press", () => {
    const before = cardPage();
    useProjectStore.setState({ project: project([before]), dirty: false });

    // Exactly what the view does: one undo entry, one mutation.
    const builder = useUIBuilderStore.getState();
    builder.pushUndo({ pages: useProjectStore.getState().project!.ui.pages }, "Duplicate element");
    const duplicated = duplicateElementInPage(
      useProjectStore.getState().project!.ui.pages,
      "main",
      "card",
    );
    useProjectStore.getState().update({
      ui: { ...useProjectStore.getState().project!.ui, pages: duplicated },
    });
    expect(useProjectStore.getState().project!.ui.pages[0].elements).toHaveLength(7);

    useUIBuilderStore.getState().undo();

    const rolled = useProjectStore.getState().project!.ui.pages[0];
    expect(rolled.elements.map((e) => e.id)).toEqual(["card", "btn", "lbl", "loose"]);
    expect(useUIBuilderStore.getState().undoStack).toHaveLength(0);
  });
});
