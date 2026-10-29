import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

/**
 * A control surface's key can set a user variable but never a panel control's
 * override: the surface writes only variables and its own plugin state, and
 * drops the rest. It offered every control's overrides anyway, so a key set to
 * one saved, looked configured, and did nothing when pressed.
 */

const mocks = vi.hoisted(() => ({
  projectState: {
    project: {
      devices: [],
      device_groups: [],
      macros: [],
      variables: [{ id: "room_active", type: "boolean", label: "Room Active" }],
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

function openKeyList(variablesOnly: boolean) {
  render(
    <ActionPicker
      value={{ action: "state.set", key: "" }}
      project={project}
      onChange={vi.fn()}
      variablesOnly={variablesOnly}
    />,
  );
  fireEvent.click(screen.getByText(variablesOnly ? "Select a variable..." : "Select a variable or control..."));
}

describe("Set Variable on a control surface", () => {
  it("offers the project's variables and no control overrides", () => {
    openKeyList(true);
    expect(screen.getByText("Room Active")).toBeTruthy();
    expect(screen.queryAllByText(/Power · Main/)).toHaveLength(0);
  });

  it("is named for what it sets", () => {
    render(<ActionPicker value={{ action: "" }} project={project} onChange={vi.fn()} variablesOnly />);
    const options = Array.from(document.querySelectorAll("option")).map((o) => o.textContent);
    expect(options).toContain("Set Variable");
    expect(options).not.toContain("Set Variable or Control");
  });

  it("still offers the overrides on a panel control", () => {
    openKeyList(false);
    expect(screen.getByText("Room Active")).toBeTruthy();
    expect(screen.queryAllByText(/Power · Main/).length).toBeGreaterThan(0);
  });
});

describe("a Stream Deck key's own editor", () => {
  // A deck key is edited by the shared button editor, the one a panel button
  // uses, so the option has to reach the picker through it.
  it("offers variables only for the key's action", () => {
    render(
      <ButtonBindingEditor
        bindings={{ press: [{ action: "state.set", key: "" }] }}
        project={project}
        onBindingsChange={vi.fn()}
        variablesOnly
        surfaceOrder
      />,
    );
    fireEvent.click(screen.getByText("Press Action"));
    fireEvent.click(screen.getByText("Select a variable..."));
    expect(screen.getByText("Room Active")).toBeTruthy();
    expect(screen.queryAllByText(/Power · Main/)).toHaveLength(0);
  });
});
