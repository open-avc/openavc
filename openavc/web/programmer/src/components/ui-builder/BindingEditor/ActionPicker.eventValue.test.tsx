import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

/**
 * A Set Variable or Control action on a slot that delivers a value (a keypad's
 * submit, a list's select) can store that value: its "$" offers the slot's own
 * tokens, the same as a Device Command parameter. A change binding keeps its
 * "Use element's selected value" choice for that instead.
 */

const mocks = vi.hoisted(() => ({
  projectState: {
    project: {
      devices: [],
      device_groups: [],
      macros: [],
      variables: [{ id: "entered_pin", type: "string", default: "" }],
      ui: { pages: [{ id: "main", name: "Main", elements: [{ id: "keypad_1", type: "keypad" }] }] },
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
const SUBMIT_TOKENS = [{ key: "value", label: "value: the entered digits" }];

const dollar = () => screen.getByTitle("Use a dynamic value read from state at runtime");

describe("Set Variable or Control on a slot that delivers a value", () => {
  it("offers the entered digits when $ is turned on", () => {
    const onChange = vi.fn();
    render(
      <ActionPicker
        value={{ action: "state.set", key: "var.entered_pin", value: "" }}
        project={project}
        onChange={onChange}
        eventTokens={SUBMIT_TOKENS}
      />,
    );
    fireEvent.click(dollar());
    expect(onChange).toHaveBeenLastCalledWith({
      action: "state.set",
      key: "var.entered_pin",
      value: "$value",
    });
  });

  it("lists the slot's own value in the $ picker", () => {
    render(
      <ActionPicker
        value={{ action: "state.set", key: "var.entered_pin", value: "$value" }}
        project={project}
        onChange={vi.fn()}
        eventTokens={SUBMIT_TOKENS}
      />,
    );
    fireEvent.click(screen.getByText("value"));
    expect(screen.getByText("value: the entered digits")).toBeTruthy();
  });

  it("leaves a change binding's $ on state keys, beside its selected-value choice", () => {
    const onChange = vi.fn();
    render(
      <ActionPicker
        value={{ action: "state.set", key: "var.entered_pin", value: "" }}
        project={project}
        onChange={onChange}
        forChangeBinding
        eventTokens={SUBMIT_TOKENS}
      />,
    );
    expect(screen.getByText("Use element's selected value")).toBeTruthy();
    fireEvent.click(dollar());
    expect(onChange).toHaveBeenLastCalledWith({
      action: "state.set",
      key: "var.entered_pin",
      value: "$var.",
    });
  });
});
