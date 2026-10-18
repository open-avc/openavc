import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

/**
 * A Set Variable or Control step aimed at a control override (ui.<id>.*) can
 * clear it. The panel puts the control back as authored only when the key
 * holds null; an emptied text value draws a button with no words, so the step
 * needs a way to write null.
 */

const mocks = vi.hoisted(() => ({
  projectState: {
    project: {
      devices: [],
      device_groups: [],
      variables: [{ id: "room_mode", type: "string", default: "" }],
      ui: { pages: [{ id: "main", name: "Main", elements: [{ id: "btn_1", type: "button", label: "Power" }] }] },
    },
  },
}));

vi.mock("../../api/restClient", () => ({
  getDevice: vi.fn(async () => ({})),
  listPlugins: vi.fn(async () => []),
}));

vi.mock("../../store/projectStore", () => {
  const state = mocks.projectState;
  return {
    useProjectStore: Object.assign(
      (selector: (s: unknown) => unknown) => selector(state),
      { getState: () => state },
    ),
  };
});

vi.mock("../../store/connectionStore", () => {
  const state = { liveState: {}, connected: true };
  return {
    useConnectionStore: Object.assign(
      (selector: (s: unknown) => unknown) => selector(state),
      { getState: () => state },
    ),
  };
});

import { StepEditor } from "./StepEditor";
import { getStepType } from "./macroHelpers";
import type { MacroStep } from "../../api/types";

function renderStep(step: MacroStep) {
  const onChange = vi.fn();
  render(<StepEditor step={step} macros={[]} currentMacroId="m1" onChange={onChange} />);
  return onChange;
}

function typeSelect(): HTMLSelectElement {
  return screen.getByTitle("How the value is stored") as HTMLSelectElement;
}

describe("clearing a control override from a macro step", () => {
  it("offers Clear on a control override and saves null", () => {
    const onChange = renderStep({ action: "state.set", key: "ui.btn_1.label", value: "Meeting" });
    fireEvent.change(typeSelect(), { target: { value: "clear" } });
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ key: "ui.btn_1.label", value: null }));
  });

  it("shows a null override as Clear, with no value box", () => {
    renderStep({ action: "state.set", key: "ui.btn_1.bg_color", value: null });
    expect(typeSelect().value).toBe("clear");
    expect(screen.getByText("Puts the control back as authored.")).toBeTruthy();
    expect(screen.queryByPlaceholderText("Value")).toBeNull();
  });

  it("switches back from Clear to a text value", () => {
    const onChange = renderStep({ action: "state.set", key: "ui.btn_1.label", value: null });
    fireEvent.change(typeSelect(), { target: { value: "text" } });
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ value: "" }));
  });

  it("offers no Clear on a key that is not a control override", () => {
    renderStep({ action: "state.set", key: "system.mode", value: "on" });
    expect([...typeSelect().options].map((o) => o.value)).toEqual(["text", "number", "boolean"]);
  });

  it("names a cleared override in the step's summary", () => {
    const summary = getStepType("state.set")!.summary;
    expect(summary({ action: "state.set", key: "ui.btn_1.label", value: null })).toBe("ui.btn_1.label cleared");
    expect(summary({ action: "state.set", key: "ui.btn_1.label", value: "Meeting" })).toBe('ui.btn_1.label = "Meeting"');
  });
});
