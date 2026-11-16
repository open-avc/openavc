import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

// A blank panel setting is drawn from somewhere else: the accent and the font
// from the theme (with the project's theme overrides over it), the idle page
// and the failure message from the project schema's blanks. Panel Settings
// shows what the panel draws, so an author on a gold theme sees gold.

import type { UIPage, UISettings } from "../../api/types";
import { PanelSettingsDialog } from "./PanelSettingsDialog";

const GOLD = { accent: "#D4AF37", font_family: "Georgia, 'Times New Roman', serif" };

function settingsWith(over: Partial<UISettings> = {}): UISettings {
  return {
    theme: "dark", theme_id: "luxury", theme_overrides: {}, accent_color: "", font_family: "",
    lock_code: "", idle_timeout_seconds: 0, idle_page: "main", page_transition: "none",
    page_transition_duration: 200, element_entry: "none", element_stagger_ms: 30,
    element_stagger_style: "fade-up",
    ...over,
  } as UISettings;
}

function page(id: string): UIPage {
  return { id, name: id[0].toUpperCase() + id.slice(1), elements: [] } as unknown as UIPage;
}

function open(settings: UISettings, themeVariables: Record<string, unknown> | null, pages: UIPage[] = []) {
  const onUpdate = vi.fn();
  render(
    <PanelSettingsDialog
      settings={settings}
      pages={pages}
      themeVariables={themeVariables}
      onUpdate={onUpdate}
      onClose={() => {}}
    />,
  );
  return onUpdate;
}

function accentPicker(): HTMLInputElement {
  return document.querySelector('input[type="color"]') as HTMLInputElement;
}

function accentText(): HTMLInputElement {
  return accentPicker().nextElementSibling as HTMLInputElement;
}

function select(label: string): HTMLSelectElement {
  const field = screen.getByText(label).parentElement as HTMLElement;
  return field.querySelector("select") as HTMLSelectElement;
}

describe("Panel Settings: a blank accent", () => {
  it("shows the theme's accent, which is what the panel draws", () => {
    open(settingsWith(), GOLD);
    expect(accentPicker().value).toBe("#d4af37");
    expect(accentText().value).toBe("");
    expect(accentText().placeholder).toBe("#D4AF37");
  });

  it("shows the project's theme override over the theme's accent", () => {
    open(settingsWith({ theme_overrides: { accent: "#ff0000" } }), GOLD);
    expect(accentPicker().value).toBe("#ff0000");
    expect(accentText().placeholder).toBe("#ff0000");
  });

  it("shows the panel stylesheet's accent when the theme names none", () => {
    open(settingsWith(), { font_family: GOLD.font_family });
    expect(accentPicker().value).toBe("#2196f3");
  });

  it("shows the stylesheet's accent when the theme cannot be read", () => {
    open(settingsWith(), null);
    expect(accentPicker().value).toBe("#2196f3");
  });

  it("shows an accent the project sets over the theme and its overrides", () => {
    open(settingsWith({ accent_color: "#00ff00", theme_overrides: { accent: "#ff0000" } }), GOLD);
    expect(accentPicker().value).toBe("#00ff00");
    expect(accentText().value).toBe("#00ff00");
  });

  it("shows a set accent that is not six-digit hex in the picker as the colour it is", () => {
    open(settingsWith({ accent_color: "#f00" }), GOLD);
    expect(accentPicker().value).toBe("#ff0000");
  });

  it("leaves the setting blank when opened and saved untouched", () => {
    const onUpdate = open(settingsWith(), GOLD);
    const save = screen.getByRole("button", { name: "Save" }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    fireEvent.click(save);
    expect(onUpdate).not.toHaveBeenCalled();
  });
});

describe("Panel Settings: a blank font", () => {
  it("shows the theme's font, which is what the panel draws", () => {
    open(settingsWith(), GOLD);
    const font = select("Font Family");
    expect(font.value).toBe("");
    expect(font.selectedOptions[0].textContent).toBe("Theme font (Georgia)");
  });

  it("names the theme's font by its listed name when the list has it", () => {
    open(settingsWith(), { accent: "#2196F3", font_family: "system-ui, sans-serif" });
    expect(select("Font Family").selectedOptions[0].textContent).toBe("Theme font (System UI)");
  });

  it("names the panel's own font when the theme has none", () => {
    open(settingsWith(), null);
    expect(select("Font Family").selectedOptions[0].textContent).toBe("Theme font (Inter)");
  });

  it("goes back to the theme's font when that choice is picked", () => {
    const onUpdate = open(settingsWith({ font_family: "monospace" }), GOLD);
    fireEvent.change(select("Font Family"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(onUpdate).toHaveBeenCalledWith(expect.objectContaining({ font_family: "" }));
  });

  it("shows a set font the list does not offer as itself", () => {
    open(settingsWith({ font_family: "'Courier New', monospace" }), GOLD);
    const font = select("Font Family");
    expect(font.value).toBe("'Courier New', monospace");
    expect(font.selectedOptions[0].textContent).toBe("Courier New");
  });
});

describe("Panel Settings: the schema's blanks", () => {
  it("shows the idle page the panel goes to for a blank one", () => {
    open(settingsWith({ idle_page: "" }), GOLD, [page("lobby"), page("main")]);
    expect(select("Idle Page").value).toBe("main");
  });

  it("shows the failure message on when the project leaves it unset", () => {
    open(settingsWith({ show_error_messages: undefined }), GOLD);
    expect((screen.getByRole("checkbox") as HTMLInputElement).checked).toBe(true);
  });
});
