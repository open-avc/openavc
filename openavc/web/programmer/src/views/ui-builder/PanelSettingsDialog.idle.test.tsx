import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

// The panel's idle timer cannot hold a delay much past 24 days: one longer
// overflows and fires at once, sending the panel to its idle page after every
// touch. The project takes at most a day, and so does this field.

import type { UISettings } from "../../api/types";
import { PanelSettingsDialog } from "./PanelSettingsDialog";

const settings = {
  theme: "dark", theme_id: "", theme_overrides: {}, accent_color: "", font_family: "",
  lock_code: "", idle_timeout_seconds: 0, idle_page: "main", page_transition: "none",
  page_transition_duration: 200, element_entry: "none", element_stagger_ms: 30,
  element_stagger_style: "fade-up",
} as UISettings;

describe("Panel Settings: Idle Timeout", () => {
  it("brings a timeout longer than a day down to a day", () => {
    const onUpdate = vi.fn();
    render(
      <PanelSettingsDialog settings={settings} pages={[]} themeVariables={null} onUpdate={onUpdate} onClose={() => {}} />,
    );
    const box = screen.getByDisplayValue("0") as HTMLInputElement;
    fireEvent.focus(box);
    fireEvent.change(box, { target: { value: "3000000" } });
    fireEvent.blur(box);
    expect(box.value).toBe("86400");

    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(onUpdate).toHaveBeenCalledWith(expect.objectContaining({ idle_timeout_seconds: 86400 }));
  });
});
