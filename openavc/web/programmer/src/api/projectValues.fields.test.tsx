import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

// What a project value may hold, and what a blank one is, is the project
// schema's, rendered into projectValues.gen.ts. The Builder's fields take
// their ranges and blanks from that table and from nowhere else, so a range
// changed on the schema reaches every field that edits it. Here the table is
// swapped for one with other numbers, and each field follows it; so are the
// page rules beside it, whose overlay box is what a blank side shows.

vi.mock("./projectValues.gen", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./projectValues.gen")>();
  return {
    ...actual,
    PROJECT_VALUES: {
      UIElement: {
        display_decimals: { default: null, min: 0, max: 7, integer: true },
        "bindings.do.press.hold_repeat_ms": { default: 250 },
        "bindings.do.press.hold_threshold_ms": { default: 650 },
      },
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
        lock_code: { default: "", max_length: 4, digits: true },
      },
      OverlayConfig: {
        width: { default: null, min: 10, max: 90, integer: false },
        height: { default: null, min: 15, max: 80, integer: false },
      },
      TriggerConfig: {
        delay_seconds: { default: 3, min: 1, finite: true, integer: false },
        debounce_seconds: { default: 4, min: 2, finite: true, integer: false },
        cooldown_seconds: { default: 6, min: 0.5, finite: true, integer: false },
      },
      MacroConfig: {
        cooldown_seconds: { default: 2, min: 1, finite: true, integer: false },
      },
    },
    // Only the box is swapped: the page types beside it stay the schema's, so
    // a page still reads as the type it is written as.
    PAGE_RULES: {
      ...actual.PAGE_RULES,
      overlay_box: { overlay: { width: 41.5, height: 27.5 }, sidebar: { width: 22 } },
    },
  };
});

import type { MacroConfig, ProjectConfig, TriggerConfig, UIElement, UIPage, UISettings } from "./types";
import { ShownDecimalsRow } from "../components/ui-builder/PropertySections/ValueFields";
import { MonitorControl } from "../components/shared/MonitorControl";
import { PanelSettingsDialog } from "../views/ui-builder/PanelSettingsDialog";
import { PropertiesPanel } from "../components/ui-builder/PropertiesPanel";
import { TriggerEditor } from "../components/macros/TriggerEditor";
import { MacroCooldown } from "../components/macros/MacroCooldown";
import { ButtonBindingEditor } from "../components/shared/ButtonBindingEditor";

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

  it("Lock Code keeps the digits a PIN takes, as many as it holds, and says how many", () => {
    const { onUpdate, save } = renderPanelSettings();
    fireEvent.change(screen.getByPlaceholderText("Leave empty for no lock"), { target: { value: "1a2 345" } });
    save();
    expect(onUpdate).toHaveBeenCalledWith(expect.objectContaining({ lock_code: "1234" }));
    expect(screen.getByText(/A PIN of up to 4 digits/)).toBeTruthy();
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

  it("an overlay's Width and Height take the overlay box's range", () => {
    const onPageChange = vi.fn();
    const page = {
      id: "dialog", name: "Dialog", page_type: "overlay", elements: [], layouts: [],
      overlay: { position: "center" },
    } as unknown as UIPage;
    const project = { ui: { settings: {}, pages: [page], master_elements: [] } } as unknown as ProjectConfig;
    render(<PropertiesPanel element={null} page={page} project={project} onChange={vi.fn()} onPageChange={onPageChange} />);
    typeAndLeave(screen.getByPlaceholderText("41.5") as HTMLInputElement, "200");
    expect(onPageChange).toHaveBeenLastCalledWith({ overlay: { position: "center", width: 90 } });
    typeAndLeave(screen.getByPlaceholderText("27.5") as HTMLInputElement, "2");
    expect(onPageChange).toHaveBeenLastCalledWith({ overlay: { position: "center", height: 15 } });
  });

  it("a page made an overlay or a sidebar starts at the page rules' box", () => {
    const onPageChange = vi.fn();
    const page = { id: "p", name: "P", elements: [], layouts: [] } as unknown as UIPage;
    const project = { ui: { settings: {}, pages: [page], master_elements: [] } } as unknown as ProjectConfig;
    render(<PropertiesPanel element={null} page={page} project={project} onChange={vi.fn()} onPageChange={onPageChange} />);
    const type = screen.getByDisplayValue("Page") as HTMLSelectElement;
    fireEvent.change(type, { target: { value: "overlay" } });
    expect(onPageChange).toHaveBeenLastCalledWith(expect.objectContaining({
      overlay: expect.objectContaining({ width: 41.5, height: 27.5 }),
    }));
    fireEvent.change(type, { target: { value: "sidebar" } });
    expect(onPageChange).toHaveBeenLastCalledWith(expect.objectContaining({
      overlay: expect.objectContaining({ width: 22 }),
    }));
  });

  it("a sidebar's blank Width shows the page rules' sidebar width", () => {
    const page = {
      id: "drawer", name: "Drawer", page_type: "sidebar", elements: [], layouts: [], overlay: {},
    } as unknown as UIPage;
    const project = { ui: { settings: {}, pages: [page], master_elements: [] } } as unknown as ProjectConfig;
    render(<PropertiesPanel element={null} page={page} project={project} onChange={vi.fn()} onPageChange={vi.fn()} />);
    expect(screen.getByPlaceholderText("22")).toBeTruthy();
  });
});

describe("The trigger editor's times read the project schema's table", () => {
  function renderTrigger(trigger: Partial<TriggerConfig>) {
    const onChange = vi.fn();
    render(
      <TriggerEditor
        trigger={{ id: "t1", type: "startup", enabled: true, ...trigger } as TriggerConfig}
        onChange={onChange}
      />,
    );
    return onChange;
  }

  it("a startup trigger saved with no delay shows the table's blank", () => {
    renderTrigger({});
    expect(screen.getByDisplayValue("3")).toBeTruthy();
  });

  it("a startup trigger's Delay takes the floor", () => {
    const onChange = renderTrigger({ delay_seconds: 5 });
    typeAndLeave(screen.getByDisplayValue("5") as HTMLInputElement, "-5");
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ delay_seconds: 1 }));
  });

  it("a state change trigger's Delay and Debounce take their floors", () => {
    const onChange = renderTrigger({
      type: "state_change", state_key: "var.room", state_operator: "any",
      delay_seconds: 7, debounce_seconds: 8,
    });
    typeAndLeave(screen.getByDisplayValue("7") as HTMLInputElement, "-1");
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ delay_seconds: 1 }));
    typeAndLeave(screen.getByDisplayValue("8") as HTMLInputElement, "0");
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ debounce_seconds: 2 }));
  });

  it("Cooldown takes its floor and shows its blank", () => {
    const onChange = renderTrigger({});
    fireEvent.click(screen.getByText("Advanced"));
    const cooldown = screen.getByDisplayValue("6") as HTMLInputElement;
    typeAndLeave(cooldown, "-2");
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ cooldown_seconds: 0.5 }));
  });
});

describe("A macro's Cooldown reads the project schema's table", () => {
  function renderCooldown(macro: Partial<MacroConfig>) {
    const onUpdate = vi.fn();
    render(<MacroCooldown macro={{ id: "m1", name: "M", steps: [], ...macro } as MacroConfig} onUpdate={onUpdate} />);
    return onUpdate;
  }

  it("a macro saved with no cooldown shows the table's blank", () => {
    renderCooldown({});
    expect(screen.getByDisplayValue("2")).toBeTruthy();
  });

  it("a typed value below the floor comes back to it", () => {
    const onUpdate = renderCooldown({ cooldown_seconds: 9 });
    typeAndLeave(screen.getByDisplayValue("9") as HTMLInputElement, "-3");
    expect(onUpdate).toHaveBeenLastCalledWith(expect.objectContaining({ cooldown_seconds: 1 }));
  });

  it("the blank is stored as no value", () => {
    const onUpdate = renderCooldown({ cooldown_seconds: 9 });
    typeAndLeave(screen.getByDisplayValue("9") as HTMLInputElement, "2");
    expect(onUpdate.mock.calls.at(-1)![0].cooldown_seconds).toBeUndefined();
  });
});

describe("A button's press timings read the project schema's table", () => {
  const project = { devices: [], macros: [], ui: { pages: [{ id: "main", name: "Main" }] } } as unknown as ProjectConfig;

  function renderPress(mode: string, extra: Record<string, unknown> = {}) {
    const onBindingsChange = vi.fn();
    render(
      <ButtonBindingEditor
        bindings={{ press: [{ action: "macro", macro: "m", mode, ...extra }] }}
        project={project}
        onBindingsChange={onBindingsChange}
      />,
    );
    return onBindingsChange;
  }

  it("Repeat every shows the table's blank, and a cleared one stores it", () => {
    const onBindingsChange = renderPress("hold_repeat");
    fireEvent.change(screen.getByDisplayValue("250"), { target: { value: "" } });
    expect(onBindingsChange.mock.calls.at(-1)![0].press[0].hold_repeat_ms).toBe(250);
  });

  it("Long press threshold shows the table's blank, and a cleared one stores it", () => {
    const onBindingsChange = renderPress("tap_hold", { hold_action: {} });
    fireEvent.change(screen.getByDisplayValue("650"), { target: { value: "" } });
    expect(onBindingsChange.mock.calls.at(-1)![0].press[0].hold_threshold_ms).toBe(650);
  });

  it("a timing written is shown as written", () => {
    renderPress("hold_repeat", { hold_repeat_ms: 400 });
    expect(screen.getByDisplayValue("400")).toBeTruthy();
  });
});
