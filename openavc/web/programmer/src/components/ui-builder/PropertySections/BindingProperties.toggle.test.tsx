import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { BindingProperties } from "./BindingProperties";
import type { ProjectConfig, UIElement } from "../../../api/types";

// A toggle button's On Label / Off Label are written up in the project format
// and in the UI Builder guide as changing the button text per state, and the
// panel honors them -- but the two fields were only ever offered to control
// surfaces, so on the button the docs describe there was nowhere to type them.

const project = {
  devices: [{ id: "amp", name: "Amp" }],
  macros: [],
  ui: { pages: [{ id: "main", name: "Main" }] },
} as unknown as ProjectConfig;

function toggleButton(): UIElement {
  return {
    id: "mute", type: "button", label: "Mute Ch 1",
    bindings: { do: { press: [{
      action: "device.command", device: "amp", command: "mute_on",
      mode: "toggle", toggle_key: "device.amp.mute", toggle_value: true,
    }] } },
  } as unknown as UIElement;
}

describe("a panel button in Toggle mode", () => {
  it("offers the On Label and Off Label the panel draws", () => {
    render(<BindingProperties element={toggleButton()} project={project} onChange={vi.fn()} />);
    expect(screen.getByText("On Label")).toBeTruthy();
    expect(screen.getByText("Off Label")).toBeTruthy();
  });

  it("says what the button does on the panel when no appearance is set", () => {
    render(<BindingProperties element={toggleButton()} project={project} onChange={vi.fn()} />);
    expect(screen.getByText(/lights in the accent color/)).toBeTruthy();
  });

  it("says nothing of the sort for a button that is not a toggle", () => {
    const tap = toggleButton();
    (tap.bindings as Record<string, Record<string, Record<string, unknown>[]>>)
      .do.press[0] = { action: "device.command", device: "amp", command: "mute_on" };
    render(<BindingProperties element={tap} project={project} onChange={vi.fn()} />);
    expect(screen.queryByText("On Label")).toBeNull();
    expect(screen.queryByText(/lights in the accent color/)).toBeNull();
  });
});
