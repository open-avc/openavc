import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

// What a project value may hold, and what a blank one is, is the project
// schema's, rendered into projectValues.gen.ts. The Builder's fields take
// their ranges and blanks from that table and from nowhere else, so a range
// changed on the schema reaches every field that edits it. Here the table is
// swapped for one with other numbers, and each field follows it.

vi.mock("./projectValues.gen", () => ({
  PROJECT_VALUES: {
    UIElement: { display_decimals: { default: null, min: 0, max: 7, integer: true } },
    MonitorConfig: { display_decimals: { default: null, min: 0, max: 5, integer: true } },
    UISettings: {
      idle_timeout_seconds: { default: 0, min: 0, max: 600, integer: true },
      idle_page: { default: "lobby" },
      show_error_messages: { default: false },
      page_transition: { default: "fade" },
      page_transition_duration: { default: 300, min: 100, max: 900, integer: true },
      element_entry: { default: "stagger" },
      element_stagger_ms: { default: 40, min: 20, max: 150, integer: true },
      element_stagger_style: { default: "scale" },
    },
  },
}));

import type { UIElement, UIPage, UISettings } from "./types";
import { ShownDecimalsRow } from "../components/ui-builder/PropertySections/ValueFields";
import { MonitorControl } from "../components/shared/MonitorControl";
import { PanelSettingsDialog } from "../views/ui-builder/PanelSettingsDialog";

function typeAndLeave(box: HTMLInputElement, text: string) {
  fireEvent.focus(box);
  fireEvent.change(box, { target: { value: text } });
  fireEvent.blur(box);
}

function renderPanelSettings(overrides: Partial<UISettings> = {}, pages: UIPage[] = []) {
  const settings = {
    theme: "dark", theme_id: "", theme_overrides: {}, accent_color: "", font_family: "",
    lock_code: "", idle_timeout_seconds: 0, idle_page: "main", page_transition: "slide-left",
    page_transition_duration: 250, element_entry: "stagger", element_stagger_ms: 30,
    element_stagger_style: "fade-up", ...overrides,
  } as UISettings;
  const onUpdate = vi.fn();
  render(<PanelSettingsDialog settings={settings} pages={pages} themeVariables={null} onUpdate={onUpdate} onClose={() => {}} />);
  const save = () => fireEvent.click(screen.getByRole("button", { name: "Save" }));
  return { onUpdate, save };
}

describe("The Builder's fields read the project schema's table", () => {
  it("Shown decimals takes the element's range", () => {
    const onChange = vi.fn();
    const element = { id: "level", type: "slider" } as UIElement;
    render(<ShownDecimalsRow element={element} onChange={onChange} placeholder="1" />);
    typeAndLeave(screen.getByPlaceholderText("1") as HTMLInputElement, "50");
    expect(onChange).toHaveBeenLastCalledWith({ display_decimals: 7 });
  });

  it("a monitor's Decimals takes the monitor's range", () => {
    const onChange = vi.fn();
    const key = "device.amp.level";
    render(
      <MonitorControl
        stateKey={key}
        declared={{ label: "Level", unit: "dB", type: "number" }}
        monitors={[{ key, type: "number" }]}
        onChange={onChange}
        open
      />,
    );
    typeAndLeave(screen.getByPlaceholderText("As reported") as HTMLInputElement, "50");
    const [monitors] = onChange.mock.calls.at(-1)!;
    expect(monitors.find((m: { key: string }) => m.key === key).display_decimals).toBe(5);
  });

  it("Idle Timeout takes the setting's range and says its top", () => {
    const { onUpdate, save } = renderPanelSettings();
    typeAndLeave(screen.getByDisplayValue("0") as HTMLInputElement, "3000000");
    save();
    expect(onUpdate).toHaveBeenCalledWith(expect.objectContaining({ idle_timeout_seconds: 600 }));
    expect(screen.getByText(/up to 600 \(one day\)/)).toBeTruthy();
  });

  it("Transition Duration takes the setting's range", () => {
    const { onUpdate, save } = renderPanelSettings();
    typeAndLeave(screen.getByDisplayValue("250") as HTMLInputElement, "5000");
    save();
    expect(onUpdate).toHaveBeenCalledWith(expect.objectContaining({ page_transition_duration: 900 }));
  });

  it("Stagger Delay takes the setting's range", () => {
    const { onUpdate, save } = renderPanelSettings();
    typeAndLeave(screen.getByDisplayValue("30") as HTMLInputElement, "999");
    save();
    expect(onUpdate).toHaveBeenCalledWith(expect.objectContaining({ element_stagger_ms: 150 }));
  });

  it("a blank motion setting shows the table's blank", () => {
    renderPanelSettings({
      page_transition: "", page_transition_duration: 0, element_entry: "",
      element_stagger_ms: 0, element_stagger_style: "",
    });
    expect(screen.getByDisplayValue("Fade")).toBeTruthy();
    expect(screen.getByDisplayValue("300")).toBeTruthy();
    expect(screen.getByDisplayValue("Stagger")).toBeTruthy();
    expect(screen.getByDisplayValue("40")).toBeTruthy();
    expect(screen.getByDisplayValue("Scale")).toBeTruthy();
  });

  it("a blank idle page and an unset failure message show the table's blanks", () => {
    const pages = ["main", "lobby"].map((id) => ({ id, name: id, elements: [] }) as unknown as UIPage);
    renderPanelSettings({ idle_page: "", show_error_messages: undefined }, pages);
    expect(screen.getByDisplayValue("lobby (lobby)")).toBeTruthy();
    expect((screen.getByRole("checkbox") as HTMLInputElement).checked).toBe(false);
  });
});
