import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

/**
 * A control's own Set Variable or Control action aimed at a control override
 * (ui.<id>.*) can clear it. The panel puts the control back as authored only
 * when the key holds null; an emptied value draws a button with no words.
 */

const mocks = vi.hoisted(() => ({
  projectState: {
    project: {
      devices: [],
      device_groups: [],
      macros: [],
      variables: [{ id: "room_mode", type: "string", default: "" }],
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
import { ButtonBindingEditor } from "../../shared/ButtonBindingEditor";
import type { ProjectConfig } from "../../../api/types";

const project = mocks.projectState.project as unknown as ProjectConfig;

function renderAction(value: Record<string, unknown>) {
  const onChange = vi.fn();
  render(<ActionPicker value={value} project={project} onChange={onChange} />);
  return onChange;
}

const choice = () => screen.queryByTitle("Set the control or clear it") as HTMLSelectElement | null;

describe("Set Variable or Control action on a control override", () => {
  it("Clear saves null and hides the value box", () => {
    const onChange = renderAction({ action: "state.set", key: "ui.btn_1.label", value: "Meeting" });
    expect(choice()!.value).toBe("set");
    fireEvent.change(choice()!, { target: { value: "clear" } });
    expect(onChange).toHaveBeenLastCalledWith({ action: "state.set", key: "ui.btn_1.label", value: null });
  });

  it("a saved clear opens as Clear, with no value box", () => {
    renderAction({ action: "state.set", key: "ui.btn_1.bg_color", value: null });
    expect(choice()!.value).toBe("clear");
    expect(screen.getByText("Puts the control back as authored.")).toBeInTheDocument();
    expect(screen.queryByPlaceholderText("Value...")).toBeNull();
  });

  it("Set to goes back to a value", () => {
    const onChange = renderAction({ action: "state.set", key: "ui.btn_1.label", value: null });
    fireEvent.change(choice()!, { target: { value: "set" } });
    expect(onChange).toHaveBeenLastCalledWith({ action: "state.set", key: "ui.btn_1.label", value: "" });
  });

  it("a variable has no Clear, and its value box still reads numbers as numbers", () => {
    const onChange = renderAction({ action: "state.set", key: "var.room_mode", value: "" });
    expect(choice()).toBeNull();
    fireEvent.change(screen.getByPlaceholderText("Value..."), { target: { value: "42" } });
    expect(onChange).toHaveBeenLastCalledWith({ action: "state.set", key: "var.room_mode", value: 42 });
  });

  it("the action's summary says the override is cleared", () => {
    render(
      <ButtonBindingEditor
        bindings={{ press: [{ action: "state.set", key: "ui.btn_1.label", value: null }] }}
        project={project}
        onBindingsChange={vi.fn()}
      />,
    );
    expect(screen.getByText("ui.btn_1.label cleared")).toBeInTheDocument();
  });
});
