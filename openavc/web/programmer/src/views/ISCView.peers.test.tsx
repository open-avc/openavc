import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";

// The server answers GET /api/isc/instances with {"instances": [...]}, and the
// page read only a bare list, so it never showed a peer: "Discovered Peers (0)"
// and "No peers discovered yet", and a Connected count of 0, while two systems
// were connected and sharing state.

const projectState = {
  project: {
    isc: {
      enabled: true,
      shared_state: ["var.*"] as string[],
      peers: [] as string[],
      allowed_remote_commands: [] as string[],
      auth_key: "a-shared-key",
    },
  },
  update: vi.fn(),
  debouncedSave: vi.fn(),
};

vi.mock("../store/projectStore", () => ({
  useProjectStore: Object.assign(
    (selector: (s: typeof projectState) => unknown) => selector(projectState),
    { getState: () => projectState },
  ),
}));

vi.mock("../store/connectionStore", () => ({
  useConnectionStore: (selector: (s: { liveState: Record<string, unknown> }) => unknown) =>
    selector({ liveState: { "system.isc.enabled": true } }),
}));

vi.mock("../api/restClient", () => ({ getTunnelPrefix: () => "" }));

import { ISCView } from "./ISCView";

const answers: Record<string, unknown> = {
  "/api/isc/status": {
    enabled: true,
    instance_id: "aaaa1111-0000-4000-8000-000000000001",
    instance_name: "Room A",
    peer_count: 1,
    connected_count: 1,
    shared_patterns: ["var.*"],
    auth_key_set: true,
    accepts_peers: true,
  },
  "/api/isc/instances": {
    instances: [
      {
        instance_id: "bbbb2222-0000-4000-8000-000000000002",
        name: "Room B",
        host: "10.20.30.7",
        port: 8080,
        version: "0.37.0",
        connected: true,
        source: "discovered",
        last_seen: 1,
      },
    ],
  },
};

describe("Inter-System page peer list", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => ({
      json: async () => answers[url],
    })));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("lists the peers the server reports, and counts the connected one", async () => {
    render(<ISCView />);

    expect(await screen.findByText("Room B")).toBeTruthy();
    expect(screen.getByText("10.20.30.7:8080 · discovered · connected")).toBeTruthy();
    expect(screen.getByText("Discovered Peers (1)")).toBeTruthy();
    expect(screen.queryByText(/No peers discovered yet/)).toBeNull();
    const connected = screen.getByText("Connected").nextElementSibling;
    expect(connected?.textContent).toBe("1");
  });
});
