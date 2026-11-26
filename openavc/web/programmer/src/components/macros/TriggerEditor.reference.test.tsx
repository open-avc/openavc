import { afterEach, describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";

// A guard whose value names a key ($var.wanted) is judged against that key's
// value, as the trigger engine judges it, and the preview shows both.

import { ConditionPreview } from "./TriggerEditor";
import { useConnectionStore } from "../../store/connectionStore";

afterEach(() => {
  useConnectionStore.setState({ connected: false, liveState: {} });
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
