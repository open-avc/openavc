import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";

// A macro's id is generated (macro_1788707883504_3) and appears nowhere else in
// the IDE. Two summarizers printed it anyway: the collapsed Run Macro step in
// the macro editor, and the action-slot header in the UI Builder's button
// binding editor. A Device Command step beside them collapsed to "Display →
// power_on" by name, because that table's summary was handed the devices and
// never the macros.

vi.mock("../ui-builder/BindingEditor/ActionPicker", () => ({ ActionPicker: () => null }));
vi.mock("../ui-builder/BindingEditor/FeedbackBindingEditor", () => ({
  FeedbackBindingEditor: () => null,
}));
vi.mock("../shared/VariableKeyPicker", () => ({ VariableKeyPicker: () => null }));
vi.mock("../shared/ActionListEditor", () => ({
  ActionListEditor: () => null,
  ActionTestButton: () => null,
}));
vi.mock("../../store/connectionStore", () => ({
  useConnectionStore: (selector: (s: unknown) => unknown) =>
    selector({ liveState: {}, connected: true }),
}));

import { macroLabel, getStepType } from "./macroHelpers";
import { ButtonBindingEditor } from "../shared/ButtonBindingEditor";

const MACROS = [
  { id: "macro_1788707883504_3", name: "System On", steps: [] },
  { id: "macro_1788707883504_9", name: "", steps: [] },
];

describe("macroLabel", () => {
  it("gives the macro's name for an id that resolves", () => {
    expect(macroLabel("macro_1788707883504_3", MACROS)).toBe("System On");
  });

  it("marks a callee that is not there, rather than hiding it", () => {
    // A step pointing at a deleted macro is a defect. A friendly name in its
    // place would be worse than the id.
    expect(macroLabel("macro_gone", MACROS)).toBe("macro_gone (missing)");
  });

  it("falls back to the id for a macro that has no name yet", () => {
    expect(macroLabel("macro_1788707883504_9", MACROS)).toBe("macro_1788707883504_9");
  });

  it("does not claim a callee is missing when there is no list to look in", () => {
    expect(macroLabel("macro_1788707883504_3", undefined)).toBe("macro_1788707883504_3");
  });

  it("says nothing definite about a step with no callee picked", () => {
    expect(macroLabel("", MACROS)).toBe("?");
    expect(macroLabel(undefined, MACROS)).toBe("?");
  });
});

describe("the collapsed Run Macro step", () => {
  it("summarizes by name, the way a Device Command step already did", () => {
    const runMacro = getStepType("macro")!;
    const summary = runMacro.summary(
      { action: "macro", macro: "macro_1788707883504_3" } as never,
      [],
      MACROS as never,
    );
    expect(summary).toBe("System On");
  });
});

describe("the UI Builder's action slot header", () => {
  it("names the macro a toggle's off half calls", () => {
    render(
      <ButtonBindingEditor
        bindings={{
          press: [
            {
              action: "macro",
              macro: "macro_1788707883504_3",
              mode: "toggle",
              toggle_key: "var.system",
              off_action: { action: "macro", macro: "macro_gone" },
            },
          ],
        }}
        project={{ macros: MACROS, devices: [], pages: [] } as never}
        onBindingsChange={vi.fn()}
      />,
    );

    expect(screen.getByText("Macro: System On")).toBeInTheDocument();
    // And the deleted callee stays visible as a problem.
    expect(screen.getByText("Macro: macro_gone (missing)")).toBeInTheDocument();
  });
});
