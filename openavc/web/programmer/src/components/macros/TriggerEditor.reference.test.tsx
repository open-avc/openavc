import { afterEach, describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";

// A guard whose value names a key ($var.wanted) is judged against that key's
// value, as the trigger engine judges it, and the preview shows both.

import { ConditionPreview, TriggerEditor } from "./TriggerEditor";
import { useConnectionStore } from "../../store/connectionStore";
import { useProjectStore } from "../../store/projectStore";
import type { TriggerConfig } from "../../api/types";

afterEach(() => {
  useConnectionStore.setState({ connected: false, liveState: {} });
  useProjectStore.setState({ project: null });
});

const guard = [{ key: "device.proj.power", operator: "eq", value: "$var.wanted" }];

describe("the trigger editor's Evaluated now", () => {
  it("passes a guard whose value names a key holding the same value", () => {
    useConnectionStore.setState({ connected: true, liveState: { "device.proj.power": "on", "var.wanted": "ON" } });
    render(<ConditionPreview conditions={guard} />);
    expect(screen.getByText(/Evaluated now/).textContent).toContain("ALL TRUE");
    expect(screen.getByText("var.wanted")).toBeTruthy();
  });

  it("fails it while the named key holds another value", () => {
    useConnectionStore.setState({ connected: true, liveState: { "device.proj.power": "on", "var.wanted": "off" } });
    render(<ConditionPreview conditions={guard} />);
    expect(screen.getByText(/Evaluated now/).textContent).toContain("FALSE");
  });

  it("makes no decision while the named key has no value, as the engine does", () => {
    useConnectionStore.setState({ connected: true, liveState: { "device.proj.power": "on" } });
    render(<ConditionPreview conditions={[{ ...guard[0], operator: "ne" }]} />);
    expect(screen.getByText(/Evaluated now/).textContent).toContain("FALSE");
  });
});

describe("the trigger editor's value boxes with a value that names a key", () => {
  const variables = [{ id: "level", type: "number" }, { id: "flag", type: "boolean" }];
  const trigger = (patch: Partial<TriggerConfig>): TriggerConfig => ({
    id: "t1", type: "state_change", enabled: true, state_key: "var.level", conditions: [], ...patch,
  });

  it("keeps a State Change trigger's $ value in a text box on a number key, and names the key", () => {
    useProjectStore.setState({ project: { variables } as never });
    useConnectionStore.setState({ liveState: { "var.limit": 40 } });
    render(<TriggerEditor trigger={trigger({ state_operator: "gte", state_value: "$var.limit" })} onChange={() => {}} />);
    const box = screen.getByPlaceholderText("Value") as HTMLInputElement;
    expect([box.type, box.value]).toEqual(["text", "$var.limit"]);
    expect(screen.getByText(/Compared with var\.limit/).textContent).toBe("Compared with var.limit: 40");
  });

  it("keeps a guard's $ value in a text box on a true or false key", () => {
    useProjectStore.setState({ project: { variables } as never });
    render(
      <TriggerEditor
        trigger={trigger({ state_operator: "any", conditions: [{ key: "var.flag", operator: "eq", value: "$var.other" }] })}
        onChange={() => {}}
      />,
    );
    expect((screen.getByPlaceholderText("Value") as HTMLInputElement).value).toBe("$var.other");
  });
});
