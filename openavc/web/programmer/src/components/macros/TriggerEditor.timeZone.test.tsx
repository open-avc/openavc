import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";

// A schedule's times are read on the system's clock, which can be in another
// zone than the browser editing it.

import { ScheduleTimeZone } from "./TriggerEditor";
import { useConnectionStore } from "../../store/connectionStore";

describe("a schedule's time zone", () => {
  it("names the system's zone", () => {
    useConnectionStore.setState({ connected: true, liveState: { "system.time_zone": "America/New_York" } });
    render(<ScheduleTimeZone />);
    expect(screen.getByText("This system's time zone: America/New_York")).toBeTruthy();
  });

  it("says nothing when the system has not reported one", () => {
    useConnectionStore.setState({ connected: true, liveState: {} });
    const { container } = render(<ScheduleTimeZone />);
    expect(container.textContent).toBe("");
  });
});
