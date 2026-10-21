import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

/**
 * A button's Set Variable or Control action aimed at a control's Visible
 * override takes Yes / No, stored as true / false. Typed into a text box, "No"
 * was stored as the word and the panel, which hides a control only on false,
 * kept showing it.
 */

const mocks = vi.hoisted(() => ({
  projectState: {
    project: {
      devices: [],
      device_groups: [],
      macros: [],
      variables: [],
      ui: { pages: [{ id: "main", name: "Main", elements: [{ id: "btn_1", type: "button", label: "Power" }] }] },
    },
  },
}));

vi.mock("../../../api/restClient", () => ({
  getDevice: vi.fn(async () => ({})),
  listPlugins: vi.fn(async () => []),
  getScriptFunctions: vi.fn(async () => []),
}));

vi.mock("../../../store/projectStore", () => {
  const state = mocks.projectState;
  return {
    useProjectStore: Object.assign(
      (selector: (s: unknown) => unknown) => selector(state),
      { getState: () => state },
    ),
  };
});

vi.mock("../../../store/connectionStore", () => {
  const state = { liveState: {}, connected: true };
  return {
    useConnectionStore: Object.assign(
      (selector: (s: unknown) => unknown) => selector(state),
      { getState: () => state },
    ),
  };
});

import { ActionPicker } from "./ActionPicker";
import type { ProjectConfig } from "../../../api/types";

const project = mocks.projectState.project as unknown as ProjectConfig;

function renderAction(value: Record<string, unknown>) {
  const onChange = vi.fn();
  render(<ActionPicker value={value} project={project} onChange={onChange} />);
  return onChange;
}

describe("a button setting a control's Visible", () => {
  it("chooses Yes or No and stores the boolean", () => {
    const onChange = renderAction({ action: "state.set", key: "ui.btn_1.visible", value: "No" });
    const choice = screen.getByRole("combobox", { name: "Value" }) as HTMLSelectElement;
    expect(choice.value).toBe("");
    fireEvent.change(choice, { target: { value: "false" } });
    expect(onChange).toHaveBeenCalledWith({ action: "state.set", key: "ui.btn_1.visible", value: false });
  });

  it("shows a stored boolean as its word", () => {
    renderAction({ action: "state.set", key: "ui.btn_1.visible", value: true });
    const choice = screen.getByRole("combobox", { name: "Value" }) as HTMLSelectElement;
    expect(choice.value).toBe("true");
    expect(choice.selectedOptions[0].textContent).toBe("Yes");
  });

  it("keeps a text value for the Label override", () => {
    renderAction({ action: "state.set", key: "ui.btn_1.label", value: "Off" });
    expect(screen.queryByRole("combobox", { name: "Value" })).toBeNull();
    expect(screen.getByPlaceholderText("Value...")).toHaveValue("Off");
  });
});
