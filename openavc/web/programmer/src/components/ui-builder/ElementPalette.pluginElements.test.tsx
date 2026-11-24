import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { DndContext } from "@dnd-kit/core";

const mocks = vi.hoisted(() => ({ elements: [] as unknown[] }));

vi.mock("../../store/pluginStore", () => {
  const state = {
    get extensions() {
      return { views: [], device_panels: [], status_cards: [], context_actions: [], panel_elements: mocks.elements };
    },
  };
  return { usePluginStore: (selector: (s: unknown) => unknown) => selector(state) };
});

import { ElementPalette } from "./ElementPalette";

function element(pluginId: string, type: string, label: string) {
  return { id: "", type, label, plugin_id: pluginId, plugin_name: `Acme ${pluginId}` };
}

// The panel loads a plugin element by its plugin id and type, and draws a
// placeholder when either holds anything but letters, digits, _ and -: the
// palette does not offer one it would never draw.
describe("the palette's plugin elements", () => {
  it("offers only the elements the panel draws", () => {
    mocks.elements = [
      element("acme_widget", "meter", "Acme Meter"),
      element("acme_widget", "my:widget", "Colon Widget"),
      element("acme_widget", "has space", "Spaced Widget"),
      element("acme_widget", "level-bar", "Acme Level"),
    ];
    render(<DndContext><ElementPalette onAdd={() => {}} /></DndContext>);
    expect(screen.getByText("Acme Meter")).toBeTruthy();
    expect(screen.getByText("Acme Level")).toBeTruthy();
    expect(screen.queryByText("Colon Widget")).toBeNull();
    expect(screen.queryByText("Spaced Widget")).toBeNull();
  });

  it("draws no plugin section when none would draw", () => {
    mocks.elements = [element("acme_widget", "my:widget", "Colon Widget")];
    render(<DndContext><ElementPalette onAdd={() => {}} /></DndContext>);
    expect(screen.queryByText("Colon Widget")).toBeNull();
    expect(screen.queryByText(/plugin/i)).toBeNull();
  });
});
