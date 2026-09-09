import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { FeedbackBindingEditor } from "./FeedbackBindingEditor";

const stores = vi.hoisted(() => ({
  project: { project: { variables: [{ id: "mode", label: "Mode" }], devices: [] } },
  connection: { liveState: { "var.mode": "idle" }, stateVersion: 1 },
}));
vi.mock("../../../store/projectStore", () => ({
  useProjectStore: (select: (s: typeof stores.project) => unknown) => select(stores.project),
}));
vi.mock("../../../store/connectionStore", () => ({
  useConnectionStore: Object.assign(
    (select: (s: typeof stores.connection) => unknown) => select(stores.connection),
    { getState: () => stores.connection },
  ),
}));
vi.mock("../IconPicker", () => ({ IconPicker: () => null }));
vi.mock("../AssetPicker", () => ({ AssetPicker: () => null }));
vi.mock("../../shared/InlineColorPicker", () => ({ InlineColorPicker: () => null }));

const initial = {
  source: "state", key: "var.mode", default_state: "idle",
  states: {
    idle: { bg_color: "#123456", text_color: "#abcdef", label: "Waiting", icon: "clock" },
    ready: { bg_color: "#654321", text_color: "#fedcba", label: "Ready", button_image: "ready.png" },
  },
};

function Harness({ changed }: { changed: (value: Record<string, unknown>) => void }) {
  const [value, setValue] = useState<Record<string, unknown>>(initial);
  return <FeedbackBindingEditor value={value} onClear={vi.fn()} onChange={(next) => {
    changed(next);
    setValue(next);
  }} />;
}

beforeEach(() => {
  stores.connection.liveState["var.mode"] = "idle";
  stores.connection.stateVersion = 1;
});

describe("multi-state appearance names", () => {
  it.each(["presenting", "__proto__"])("commits %j on Enter without losing appearance or order", async (newName) => {
    const user = userEvent.setup();
    const changed = vi.fn();
    render(<Harness changed={changed} />);
    const name = screen.getByDisplayValue("idle");
    await user.clear(name);
    await user.type(name, ` ${newName} `);
    expect(name).toHaveValue(` ${newName} `);
    expect(changed).not.toHaveBeenCalled();
    await user.keyboard("{Enter}");
    expect(changed).toHaveBeenCalledExactlyOnceWith({
      ...initial, default_state: newName,
      states: { [newName]: initial.states.idle, ready: initial.states.ready },
    });
    expect(Object.keys(changed.mock.calls[0][0].states)).toEqual([newName, "ready"]);
    expect(screen.getByDisplayValue(newName)).toBeInTheDocument();
  });

  it("commits a non-default name on Tab and keeps the existing default", async () => {
    const user = userEvent.setup();
    const changed = vi.fn();
    render(<Harness changed={changed} />);
    const name = screen.getByDisplayValue("ready");
    await user.clear(name);
    await user.type(name, "available");
    await user.tab();
    expect(changed).toHaveBeenCalledExactlyOnceWith({
      ...initial, states: { idle: initial.states.idle, available: initial.states.ready },
    });
  });

  it("keeps an unfinished name when a new live reading renders the editor", async () => {
    const user = userEvent.setup();
    const changed = vi.fn();
    const view = render(<Harness changed={changed} />);
    const name = screen.getByDisplayValue("idle");
    await user.clear(name);
    await user.type(name, "other");
    stores.connection.liveState["var.mode"] = "ready";
    stores.connection.stateVersion++;
    view.rerender(<Harness changed={changed} />);
    expect(name).toHaveValue("other");
    expect(changed).not.toHaveBeenCalled();
    await user.tab();
    expect(changed.mock.calls[0][0].default_state).toBe("other");
  });

  it.each([
    ["   ", "Enter a state name."],
    [" ready ", "That state name is already in use."],
  ])("rejects %j without losing either state and allows a corrected name", async (draft, message) => {
    const user = userEvent.setup();
    const changed = vi.fn();
    render(<Harness changed={changed} />);
    const name = screen.getByDisplayValue("idle");
    await user.clear(name);
    await user.type(name, draft);
    await user.tab();
    expect(changed).not.toHaveBeenCalled();
    expect(name).toHaveValue("idle");
    expect(screen.getByDisplayValue("ready")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(message);
    await user.clear(name);
    await user.type(name, "other");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    await user.keyboard("{Enter}");
    expect(changed).toHaveBeenCalledExactlyOnceWith({
      ...initial, default_state: "other",
      states: { other: initial.states.idle, ready: initial.states.ready },
    });
  });
});
