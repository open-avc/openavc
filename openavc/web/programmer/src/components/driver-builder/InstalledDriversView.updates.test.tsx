import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

// Installed Drivers offers the update Browse Community offers: a button on the
// row, a count at the top, and the same rule for an update this OpenAVC cannot
// install. catalogUpdates.ts decides; this pins that the list asks it.

const liveState: Record<string, unknown> = { "system.version": "0.37.0" };

vi.mock("../../store/connectionStore", () => ({
  useConnectionStore: (selector: (s: { liveState: Record<string, unknown> }) => unknown) =>
    selector({ liveState }),
}));

vi.mock("../../store/projectStore", () => ({
  useProjectStore: (selector: (s: { project: { devices: unknown[] } }) => unknown) =>
    selector({ project: { devices: [] } }),
}));

vi.mock("../../store/navigationStore", () => ({
  useNavigationStore: (selector: (s: { navigateTo: () => void }) => unknown) =>
    selector({ navigateTo: () => {} }),
}));

const storeState = {
  registeredDrivers: [] as unknown[],
  installedDrivers: [] as unknown[],
  communityDrivers: [] as unknown[],
  definitions: [] as unknown[],
  installedDriverId: null as string | null,
  updatingDriverIds: new Set<string>(),
  loadRegisteredDrivers: vi.fn(),
  loadInstalledDrivers: vi.fn(),
  loadDefinitions: vi.fn(),
  loadCommunityDrivers: vi.fn(),
  uninstallDriver: vi.fn(),
  updateDriver: vi.fn(async () => [] as string[]),
  setInstalledDriverId: vi.fn((id: string | null) => {
    storeState.installedDriverId = id;
  }),
};

vi.mock("../../store/driverBuilderStore", () => ({
  useDriverBuilderStore: Object.assign(
    (selector: (s: typeof storeState) => unknown) => selector(storeState),
    { getState: () => storeState },
  ),
}));

import { InstalledDriversView } from "./InstalledDriversView";

function driver(id: string, version: string) {
  return { id, name: id.replace("_", " "), manufacturer: "Acme", category: "display", version };
}

function setup(catalogMin?: string) {
  storeState.registeredDrivers = [driver("acme_widget", "1.4.3"), driver("acme_cam", "1.0.0")];
  storeState.installedDrivers = [
    { id: "acme_widget", name: "acme widget", format: "python", filename: "acme_widget.py", version: "1.4.3" },
    { id: "acme_cam", name: "acme cam", format: "avcdriver", filename: "acme_cam.avcdriver", version: "1.0.0" },
  ];
  storeState.communityDrivers = [
    {
      ...driver("acme_widget", "1.5.0"),
      file: "displays/acme_widget.py",
      min_platform_version: catalogMin,
    },
    { ...driver("acme_cam", "1.0.0"), file: "cameras/acme_cam.avcdriver" },
  ];
  storeState.installedDriverId = null;
}

describe("Installed Drivers: a newer catalog version", () => {
  beforeEach(() => vi.clearAllMocks());

  it("puts Update on the row and a count at the top", () => {
    setup();
    render(<InstalledDriversView />);
    expect(screen.getByRole("button", { name: "Update to v1.5.0" })).toBeTruthy();
    expect(screen.getByText("1 update available")).toBeTruthy();
    // The current driver gets nothing.
    expect(screen.getAllByRole("button", { name: /Update to/ })).toHaveLength(1);
  });

  it("updates from the catalog file, with the version floor the catalog states", async () => {
    setup("0.30.0");
    render(<InstalledDriversView />);
    fireEvent.click(screen.getByRole("button", { name: "Update to v1.5.0" }));
    await waitFor(() =>
      expect(storeState.updateDriver).toHaveBeenCalledWith(
        "acme_widget",
        "https://raw.githubusercontent.com/open-avc/openavc-drivers/main/displays/acme_widget.py",
        "0.30.0",
      ),
    );
    expect(storeState.setInstalledDriverId).toHaveBeenCalledWith("acme_widget");
  });

  it("offers no button for a version this OpenAVC cannot run, and does not count it", () => {
    setup("0.38.0");
    render(<InstalledDriversView />);
    expect(screen.queryByRole("button", { name: /Update to/ })).toBeNull();
    expect(screen.getByText("v1.5.0 needs OpenAVC v0.38.0")).toBeTruthy();
    expect(screen.queryByText(/updates? available/)).toBeNull();
  });

  it("shows nothing when the catalog could not be read", () => {
    setup();
    storeState.communityDrivers = [];
    render(<InstalledDriversView />);
    expect(screen.queryByRole("button", { name: /Update to/ })).toBeNull();
    expect(screen.queryByText(/updates? available/)).toBeNull();
  });
});
