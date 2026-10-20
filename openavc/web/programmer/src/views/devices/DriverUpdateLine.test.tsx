import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

// The device page names its driver; when the catalog has a newer version, the
// line under that name says so and updates it, the same update Installed
// Drivers and Browse Community run.

const liveState: Record<string, unknown> = { "system.version": "0.37.0" };

vi.mock("../../store/connectionStore", () => ({
  useConnectionStore: (selector: (s: { liveState: Record<string, unknown> }) => unknown) =>
    selector({ liveState }),
}));

const storeState = {
  installedDrivers: [] as unknown[],
  communityDrivers: [] as unknown[],
  communityLoaded: false,
  communityLoading: false,
  updatingDriverIds: new Set<string>(),
  loadCommunityDrivers: vi.fn(),
  loadInstalledDrivers: vi.fn(),
  updateDriver: vi.fn(async () => {
    // What the store does after a landed update: the installed list reloads.
    storeState.installedDrivers = [{ ...INSTALLED, version: "1.5.0" }];
    return [] as string[];
  }),
};

vi.mock("../../store/driverBuilderStore", () => ({
  useDriverBuilderStore: Object.assign(
    (selector: (s: typeof storeState) => unknown) => selector(storeState),
    { getState: () => storeState },
  ),
}));

import { DriverUpdateLine } from "./DriverUpdateLine";

const INSTALLED = {
  id: "acme_widget",
  name: "Acme Widget",
  format: "python",
  filename: "acme_widget.py",
  version: "1.4.3",
};
const ENTRY = {
  id: "acme_widget",
  name: "Acme Widget",
  file: "displays/acme_widget.py",
  version: "1.5.0",
};

describe("the device page's driver update line", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    storeState.installedDrivers = [INSTALLED];
    storeState.communityDrivers = [ENTRY];
    storeState.communityLoaded = false;
  });

  it("names both versions and offers Update", () => {
    render(<DriverUpdateLine driverId="acme_widget" onUpdated={() => {}} />);
    expect(screen.getByText("Driver update available: v1.5.0 (installed v1.4.3)")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Update" })).toBeTruthy();
  });

  it("says what it updated to, and has the device page fetch the device again", async () => {
    const onUpdated = vi.fn();
    const { rerender } = render(<DriverUpdateLine driverId="acme_widget" onUpdated={onUpdated} />);
    fireEvent.click(screen.getByRole("button", { name: "Update" }));
    await waitFor(() => expect(onUpdated).toHaveBeenCalled());
    rerender(<DriverUpdateLine driverId="acme_widget" onUpdated={onUpdated} />);
    expect(screen.getByText("Driver updated to v1.5.0.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Update" })).toBeNull();
  });

  it("draws nothing for a current driver or an unread catalog", () => {
    storeState.communityDrivers = [];
    const { container, rerender } = render(
      <DriverUpdateLine driverId="acme_widget" onUpdated={() => {}} />,
    );
    expect(container.textContent).toBe("");
    storeState.communityDrivers = [{ ...ENTRY, version: "1.4.3" }];
    rerender(<DriverUpdateLine driverId="acme_widget" onUpdated={() => {}} />);
    expect(container.textContent).toBe("");
  });

  it("asks for the catalog once a session, not once per device opened", () => {
    render(<DriverUpdateLine driverId="acme_widget" onUpdated={() => {}} />);
    expect(storeState.loadCommunityDrivers).toHaveBeenCalledTimes(1);
    storeState.communityLoaded = true;
    render(<DriverUpdateLine driverId="acme_other" onUpdated={() => {}} />);
    expect(storeState.loadCommunityDrivers).toHaveBeenCalledTimes(1);
  });
});
