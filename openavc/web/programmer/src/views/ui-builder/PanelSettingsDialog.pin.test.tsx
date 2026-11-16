import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

// A PIN is what the panel's lock screen can take, as the project schema
// declares it: the digits 0 to 9, as many as the table says. The field keeps
// nothing else, so it never saves a PIN nobody could type at the panel.

import { PROJECT_VALUES } from "../../api/projectValues.gen";
import type { UISettings } from "../../api/types";
import { PanelSettingsDialog } from "./PanelSettingsDialog";

const PIN = PROJECT_VALUES.UISettings.lock_code;

const settings = {
  theme: "dark", theme_id: "", theme_overrides: {}, accent_color: "", font_family: "",
  lock_code: "", idle_timeout_seconds: 0, idle_page: "main", page_transition: "none",
  page_transition_duration: 200, element_entry: "none", element_stagger_ms: 30,
  element_stagger_style: "fade-up",
} as UISettings;

describe("Panel Settings: Lock Code", () => {
  it("keeps the digits typed, as many as a PIN holds", () => {
    const onUpdate = vi.fn();
    render(
      <PanelSettingsDialog settings={settings} pages={[]} themeVariables={null} onUpdate={onUpdate} onClose={() => {}} />,
    );
    const box = screen.getByPlaceholderText("Leave empty for no lock") as HTMLInputElement;
    fireEvent.change(box, { target: { value: "1 2a3-4567890" } });
    expect(box.value).toBe("1234567890".slice(0, PIN.max_length));

    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(onUpdate).toHaveBeenCalledWith(expect.objectContaining({ lock_code: "123456" }));
  });

  it("says how long a PIN may be", () => {
    render(
      <PanelSettingsDialog settings={settings} pages={[]} themeVariables={null} onUpdate={() => {}} onClose={() => {}} />,
    );
    expect(screen.getByText(`A PIN of up to ${PIN.max_length} digits to lock the touch panel. Leave empty to disable.`)).toBeTruthy();
  });
});
