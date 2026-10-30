import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";

// With no internet the server cannot reach the community library. That used
// to read "No community drivers available yet.", an empty library rather than
// a connection problem, with no Retry. The store holds the server's sentence
// as communityError (fetchCommunityDrivers throws it).

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
  updatingDriverIds: new Set<string>(),
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

const UNREACHABLE =
  "Could not reach the community driver library at raw.githubusercontent.com. " +
  "Check that this system can reach the internet.";

describe("Browse Community with no internet", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    storeState.communityDrivers = [];
    storeState.communityError = UNREACHABLE;
  });

  it("says the library could not be reached, and how else to add a driver", () => {
    render(<CommunityBrowser />);
    expect(screen.getByText("Could not load the community drivers.", { exact: false })).toBeTruthy();
    expect(screen.getByText(UNREACHABLE)).toBeTruthy();
    expect(screen.getByText(/Import from File on the Create tab/)).toBeTruthy();
    expect(screen.queryByText("No community drivers available yet.")).toBeNull();
  });

  it("offers Retry, which fetches the library again", () => {
    render(<CommunityBrowser />);
    screen.getByRole("button", { name: /Retry/ }).click();
    expect(storeState.loadCommunityDrivers).toHaveBeenCalled();
  });
});
