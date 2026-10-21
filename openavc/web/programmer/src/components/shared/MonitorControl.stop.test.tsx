import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

// One click on Monitored removed the monitor with everything set on it (its
// name, what normal looks like, the delay) and nothing to undo it. A monitor
// with settings now asks first; one made by a single click still goes in one.

import { MonitorControl } from "./MonitorControl";
import type { MonitorConfig } from "../../api/types";

const declared = { label: "Lamp Hours", unit: "h", type: "integer" };

function renderWith(monitor: MonitorConfig) {
  const onChange = vi.fn();
  render(
    <MonitorControl
      stateKey="device.proj.lamp_hours"
      declared={declared}
      monitors={[monitor]}
      onChange={onChange}
    />,
  );
  return onChange;
}

describe("Monitored, clicked to stop", () => {
  it("asks first when the monitor has settings, naming them", () => {
    const onChange = renderWith({
      key: "device.proj.lamp_hours", label: "Lamp", unit: "h", type: "integer",
      normal_max: 2000, duration_seconds: 600,
    });
    fireEvent.click(screen.getByRole("button", { name: /Monitored/ }));
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByText("Stop monitoring Lamp?")).toBeTruthy();
    expect(screen.getByText(/Shown as, Normal is, how long it has to stay wrong/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Stop monitoring" }));
    expect(onChange).toHaveBeenCalledWith([], "Stop monitoring device.proj.lamp_hours");
  });

  it("keeps the monitor when the question is cancelled", () => {
    const onChange = renderWith({ key: "device.proj.lamp_hours", ...declared, normal_max: 2000 });
    fireEvent.click(screen.getByRole("button", { name: /Monitored/ }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.queryByText(/Stop monitoring Lamp Hours\?/)).toBeNull();
  });

  it("goes at once when nothing was set beyond the one click", () => {
    const onChange = renderWith({ key: "device.proj.lamp_hours", ...declared });
    fireEvent.click(screen.getByRole("button", { name: /Monitored/ }));
    expect(onChange).toHaveBeenCalledWith([], "Stop monitoring device.proj.lamp_hours");
  });
});
