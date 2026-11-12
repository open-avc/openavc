import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, within } from "@testing-library/react";

// A driver file in the library that never registered (it failed to import,
// or was written for an older OpenAVC) is still listed: it says it did not
// load, offers the catalog's update and Uninstall, and its update is counted.

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

function setup() {
  storeState.registeredDrivers = [
    { id: "acme_cam", name: "Acme Cam", manufacturer: "Acme", category: "camera", version: "1.0.0" },
  ];
  storeState.installedDrivers = [
    { id: "acme_cam", name: "Acme Cam", format: "avcdriver", filename: "acme_cam.avcdriver", version: "1.0.0" },
    { id: "acme_widget", name: "acme_widget", format: "python", filename: "acme_widget.py", version: "1.4.4" },
    { id: "acme_orphan", name: "acme_orphan", format: "avcdriver", filename: "acme_orphan.avcdriver", version: "" },
  ];
  storeState.communityDrivers = [
    {
      id: "acme_widget", name: "Acme Widget", manufacturer: "Acme", category: "display",
      version: "1.4.11", file: "displays/acme_widget.py",
    },
  ];
  storeState.installedDriverId = null;
}

function row(name: string): HTMLElement {
  return screen.getByText(name).closest("div[style*='cursor: pointer']") as HTMLElement;
}

describe("Installed Drivers: a driver that did not load", () => {
  beforeEach(() => vi.clearAllMocks());

  it("is listed under its catalog name, says it did not load, and offers the update", () => {
    setup();
    render(<InstalledDriversView />);

    const widget = row("Acme Widget");
    expect(within(widget).getByText("Did not load")).toBeTruthy();
    expect(within(widget).getByRole("button", { name: "Update to v1.4.11" })).toBeTruthy();
    expect(screen.getByText("1 update available")).toBeTruthy();
    expect(screen.getByText("3 drivers")).toBeTruthy();
  });

  it("is listed by its installed name when the catalog does not have it", () => {
    setup();
    render(<InstalledDriversView />);

    const orphan = row("acme_orphan");
    expect(within(orphan).getByText("Did not load")).toBeTruthy();
    expect(within(orphan).queryByRole("button")).toBeNull();
  });

  it("opens to what to do about it, with Uninstall and the code", () => {
    setup();
    const { rerender } = render(<InstalledDriversView />);
    fireEvent.click(screen.getByText("Acme Widget"));
    rerender(<InstalledDriversView />);

    expect(
      screen.getByText(
        "This driver did not load, so devices can't use it. Update it, or uninstall it and " +
          "install it again from Browse Community. The Log has the reason.",
      ),
    ).toBeTruthy();
    expect(screen.getByRole("button", { name: /Uninstall/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /Open in Code Editor/ })).toBeTruthy();
  });

  it("is found by search like any other driver", () => {
    setup();
    render(<InstalledDriversView />);
    fireEvent.change(screen.getByPlaceholderText("Search drivers..."), { target: { value: "widget" } });

    expect(screen.getByText("Acme Widget")).toBeTruthy();
    expect(screen.queryByText("Acme Cam")).toBeNull();
  });
});
