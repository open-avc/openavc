import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { BindingProperties } from "./BindingProperties";
import type { ProjectConfig, UIElement } from "../../../api/types";

// Which cards the binding card draws for a control comes from one table, what
// each control type binds. These pin what it draws per type today: the Value,
// Items and Appearance cards under SHOWS, which editor the Appearance card
// opens, and the action lists under DOES, in their order. The three Appearance
// editors are stood in for by their names, so the test reads which one a type
// gets without depending on what each draws inside.

vi.mock("../BindingEditor/FeedbackBindingEditor", () => ({
  FeedbackBindingEditor: () => <div>editor: feedback</div>,
}));
vi.mock("../BindingEditor/SelectFeedbackEditor", () => ({
  SelectFeedbackEditor: () => <div>editor: select feedback</div>,
}));
vi.mock("../BindingEditor/ColorBindingEditor", () => ({
  ColorBindingEditor: () => <div>editor: color</div>,
}));

const project = {
  devices: [{ id: "amp", name: "Amp" }],
  macros: [],
  ui: { pages: [{ id: "main", name: "Main" }] },
} as unknown as ProjectConfig;

function cardsOf(type: string, extra: Record<string, unknown> = {}) {
  const element = { id: "c1", type, bindings: {}, ...extra } as unknown as UIElement;
  return render(<BindingProperties element={element} project={project} onChange={vi.fn()} />);
}

/** Where each text first appears in the card, so an order can be read. */
function positions(container: HTMLElement, texts: string[]): number[] {
  const all = container.textContent ?? "";
  return texts.map((t) => all.indexOf(t));
}

describe("the binding card's cards, per control type", () => {
  it("gives a list its selected item, its rows and its row tap", () => {
    cardsOf("list");
    expect(screen.getByText("Selected item")).toBeTruthy();
    expect(screen.getByText("Items")).toBeTruthy();
    expect(screen.getByText("On row tap")).toBeTruthy();
    expect(screen.queryByText(/^editor: /)).toBeNull();
  });

  it("gives a matrix its four route lists, in order, and nothing under SHOWS", () => {
    const { container } = cardsOf("matrix");
    const at = positions(container, ["Video route", "Audio route", "Mute", "Audio mute"]);
    expect(at.every((i) => i >= 0)).toBe(true);
    expect([...at].sort((a, b) => a - b)).toEqual(at);
    expect(screen.queryByText("Value")).toBeNull();
    expect(screen.queryByText("Items")).toBeNull();
    expect(screen.queryByText(/^editor: /)).toBeNull();
  });

  it("opens each kind of Appearance in its own editor", () => {
    const cases: [string, string][] = [
      ["select", "editor: select feedback"],
      ["status_led", "editor: color"],
      ["label", "editor: feedback"],
      ["image", "editor: feedback"],
      ["group", "editor: feedback"],
      ["page_nav", "editor: feedback"],
      ["camera_preset", "editor: feedback"],
    ];
    for (const [type, editor] of cases) {
      const { unmount } = cardsOf(type);
      expect(screen.getAllByText(/^editor: /).map((e) => e.textContent), type).toEqual([editor]);
      unmount();
    }
  });

  it("gives no Appearance to a type that takes none", () => {
    for (const type of ["slider", "fader", "text_input", "gauge", "level_meter", "keypad", "list", "matrix", "clock", "custom"]) {
      const { unmount } = cardsOf(type);
      expect(screen.queryByText(/^editor: /), type).toBeNull();
      unmount();
    }
  });

  it("names each type's action list as the table does", () => {
    const cases: [string, string][] = [
      ["slider", "On change"],
      ["fader", "On change"],
      ["text_input", "On change"],
      ["keypad", "On submit"],
      ["camera_preset", "On press"],
    ];
    for (const [type, label] of cases) {
      const { unmount } = cardsOf(type);
      expect(screen.getByText(label), type).toBeTruthy();
      unmount();
    }
  });
});
