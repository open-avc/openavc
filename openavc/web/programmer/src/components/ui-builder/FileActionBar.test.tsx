import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { FileActionBar } from "./FileActionBar";

const actions = () => ({
  saveChanges: vi.fn(), saveAsCustom: vi.fn(), discard: vi.fn(), exportFile: vi.fn(), askDelete: vi.fn(),
});

/** Every state the bar is drawn in: custom or built-in, edited or not, an action running or not, a name or a blank one. */
const STATES = [true, false].flatMap((isCustom) =>
  [true, false].flatMap((isDirty) =>
    [true, false].flatMap((busy) =>
      ["Sample", "   "].map((name) => ({ isCustom, isDirty, busy, name })))));

const label = (s: (typeof STATES)[number]) =>
  `${s.isCustom ? "custom" : "built-in"}, ${s.isDirty ? "edited" : "unchanged"}, ${s.busy ? "busy" : "idle"}, name ${JSON.stringify(s.name)}`;

describe("the Theme Studio's file buttons", () => {
  it.each(STATES.map((s) => [label(s), s] as const))(
    "look held exactly when they cannot be pressed: %s",
    (_label, state) => {
      render(<FileActionBar {...state} actions={actions()} />);
      for (const button of screen.getAllByRole("button")) {
        const name = button.textContent;
        const looksHeld = button.style.cursor === "not-allowed";
        const faded = button.style.opacity !== "" && Number(button.style.opacity) < 1;
        expect({ name, looksHeld, faded }).toEqual({ name, looksHeld: button.disabled, faded: button.disabled });
      }
    },
  );

  it.each([true, false])("says why Save as Copy or Save as Custom is held while the name is blank (custom: %s)", (isCustom) => {
    render(<FileActionBar isCustom={isCustom} isDirty busy={false} name="  " actions={actions()} />);
    expect(screen.getByRole("button", { name: /Save as (Copy|Custom)/ }).title).toBe("Enter a theme name");
  });
});
