import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";

// Chrome filled the Programmer's saved sign-in into this page: the user name
// into the Remote Command Allowlist field (the text box just above a password
// box) and the password into the Shared auth key. One press of "+" or Save
// would then have allowed a command pattern named after the user, or stored
// the admin password as the key every peer has to know.

const projectState = {
  project: {
    isc: {
      enabled: false,
      shared_state: [] as string[],
      peers: [] as string[],
      allowed_remote_commands: [] as string[],
      auth_key: "",
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
    selector({ liveState: {} }),
}));

vi.mock("../api/restClient", () => ({ getTunnelPrefix: () => "" }));

import { ISCView } from "./ISCView";

describe("Inter-System page and the browser's saved sign-in", () => {
  it("keeps the browser from filling in its pattern, peer and key fields", () => {
    render(<ISCView />);
    const field = (placeholder: string) =>
      screen.getByPlaceholderText(placeholder) as HTMLInputElement;

    expect(field("Shared auth key (required)").getAttribute("autocomplete")).toBe("new-password");
    for (const placeholder of [
      "e.g. device.projector1.* or var.*",
      "e.g. 192.168.1.10:8080",
      "e.g. projector1.* or *.power_off",
    ]) {
      expect(field(placeholder).getAttribute("autocomplete")).toBe("off");
    }
  });
});
