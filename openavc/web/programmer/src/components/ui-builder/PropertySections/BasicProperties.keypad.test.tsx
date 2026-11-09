import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { ProjectConfig, UIElement } from "../../../api/types";
import { BasicProperties } from "./BasicProperties";

// A keypad's sending fields show the panel's own defaults: four digits and a
// 1500 ms Send Delay, which is also where the page review starts warning.

const project = { devices: [], macros: [], ui: { pages: [] } } as unknown as ProjectConfig;

function renderKeypad(extra: Partial<UIElement> = {}) {
  const element = { id: "pin", type: "keypad", ...extra } as UIElement;
  return render(
    <BasicProperties element={element} project={project} pages={[]} onChange={() => {}} />,
  );
}

describe("keypad sending fields", () => {
  it("offers four digits when none is set", () => {
    renderKeypad();
    expect(screen.getByPlaceholderText("4")).toHaveProperty("value", "");
  });

  it("shows the default Send Delay and says where it starts sending early", () => {
    renderKeypad({ auto_send: true });
    expect(screen.getByDisplayValue("1500")).toBeTruthy();
    expect(
      screen.getByText(
        "How long after the last key press a shorter entry is sent. " +
          "Under 1500 ms, a code can be sent before it is finished.",
      ),
    ).toBeTruthy();
  });

  it("shows a Send Delay the author set", () => {
    renderKeypad({ auto_send: true, auto_send_delay_ms: 800 });
    expect(screen.getByDisplayValue("800")).toBeTruthy();
  });

  it("hides the Send Delay while Send without Enter is off", () => {
    renderKeypad();
    expect(screen.queryByDisplayValue("1500")).toBeNull();
  });
});
