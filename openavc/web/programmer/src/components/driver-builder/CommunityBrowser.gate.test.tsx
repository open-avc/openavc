import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";

// Pins the WIRING, not the decision. blockedByPlatform is unit-tested next
// door; what actually shipped broken was that nothing passed
// min_platform_version to the card at all, so the button stayed live and the
// only signal was a 422 after the click. A test of the helper alone would pass
// with the prop still unwired.

const liveState: Record<string, unknown> = {};

vi.mock("../../store/connectionStore", () => ({
  useConnectionStore: (
    selector: (s: { liveState: Record<string, unknown> }) => unknown,
  ) => selector({ liveState }),
}));

const storeState = {
  communityDrivers: [] as unknown[],
  installedDrivers: [] as unknown[],
  communityLoading: false,
  communityError: null as string | null,
  loadCommunityDrivers: vi.fn(),
  loadInstalledDrivers: vi.fn(),
};

vi.mock("../../store/driverBuilderStore", () => ({
  useDriverBuilderStore: Object.assign(
    (selector: (s: typeof storeState) => unknown) => selector(storeState),
    { getState: () => storeState },
  ),
}));

import { CommunityBrowser } from "./CommunityBrowser";

const DRIVER = {
  id: "acme_widget",
  name: "Acme Widget",
  manufacturer: "Acme",
  author: "OpenAVC",
  category: "display",
  transport: "tcp",
  version: "1.0.0",
  description: "A widget.",
  file: "displays/acme_widget.py",
  format: "python",
  tags: [],
  min_platform_version: "0.25.0",
};

function setup(runningVersion: string, minPlatform: string | null) {
  for (const k of Object.keys(liveState)) delete liveState[k];
  liveState["system.version"] = runningVersion;
  storeState.communityDrivers = [{ ...DRIVER, min_platform_version: minPlatform }];
  storeState.installedDrivers = [];
}

describe("Browse Community: a driver this platform cannot run", () => {
  beforeEach(() => vi.clearAllMocks());

  it("says which release it needs, before anything is clicked", () => {
    setup("0.24.1", "0.25.0");
    render(<CommunityBrowser />);
    expect(screen.getByText(/Needs OpenAVC v0\.25\.0/)).toBeTruthy();
  });

  it("does not offer a live Install button", () => {
    setup("0.24.1", "0.25.0");
    render(<CommunityBrowser />);
    const install = screen.getByRole("button", { name: /Install/i });
    expect((install as HTMLButtonElement).disabled).toBe(true);
  });

  it("leaves an installable driver completely alone", () => {
    // The counterweight. If this greyed out too, the gate would be worse than
    // the bug it replaces.
    setup("0.25.0", "0.25.0");
    render(<CommunityBrowser />);
    expect(screen.queryByText(/Needs OpenAVC/)).toBeNull();
    const install = screen.getByRole("button", { name: /Install/i });
    expect((install as HTMLButtonElement).disabled).toBe(false);
  });

  it("leaves a driver that declares no minimum alone", () => {
    setup("0.24.1", null);
    render(<CommunityBrowser />);
    expect(screen.queryByText(/Needs OpenAVC/)).toBeNull();
    const install = screen.getByRole("button", { name: /Install/i });
    expect((install as HTMLButtonElement).disabled).toBe(false);
  });

  it("does not grey the catalog out before the first state snapshot arrives", () => {
    // liveState is empty on first paint. Reading that as "too old" would
    // disable every driver in the library on every page load.
    setup("", "0.25.0");
    render(<CommunityBrowser />);
    const install = screen.getByRole("button", { name: /Install/i });
    expect((install as HTMLButtonElement).disabled).toBe(false);
  });
});
