import { describe, it, expect, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";

// "Evaluated now" answers with the runtime's rule: a device reporting "ON"
// meets a condition written "on", as the trigger engine decides. It used to
// compare with JavaScript ==, and said the trigger would not fire.

import { ConditionPreview } from "./TriggerEditor";
import { useConnectionStore } from "../../store/connectionStore";

beforeEach(() => {
  useConnectionStore.setState({
    connected: true,
    liveState: { "device.proj.power": "ON", "device.proj.lamp_hours": "1200" },
  });
});

describe("Evaluated now", () => {
  it("passes a condition the runtime passes", () => {
    render(
      <ConditionPreview
        conditions={[
          { key: "device.proj.power", operator: "eq", value: "on" },
          { key: "device.proj.lamp_hours", operator: "gte", value: 1000 },
        ]}
      />,
    );
    expect(screen.getByText(/Evaluated now: ALL TRUE/)).toBeTruthy();
  });

  it("fails a condition on a value not reported yet", () => {
    render(<ConditionPreview conditions={[{ key: "device.proj.input", operator: "ne", value: "hdmi1" }]} />);
    expect(screen.getByText(/Evaluated now: FALSE/)).toBeTruthy();
  });
});
