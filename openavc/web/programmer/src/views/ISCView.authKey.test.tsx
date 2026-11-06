import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

// The mesh key is saved on this system; the project holds the marker. The
// page shows "Saved on this system" with Show and Change. Show asks the
// system for the key only when pressed and never puts it in the project
// store; Change saves a new one on its own, never through the project save.

const MARKER = "<saved on this system>";

const projectState = {
  project: {
    isc: {
      enabled: false,
      shared_state: [] as string[],
      peers: [] as string[],
      allowed_remote_commands: [] as string[],
      auth_key: MARKER,
    },
  },
  update: vi.fn(),
  debouncedSave: vi.fn(),
  load: vi.fn(async () => {}),
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
vi.mock("../api/iscClient", () => ({
  getIscAuthKey: vi.fn(async () => "k3Yq-7vPz-Lm2a-9rTe"),
  saveIscAuthKey: vi.fn(async () => {}),
}));
vi.mock("../store/toastStore", () => ({ showError: vi.fn() }));

import { getIscAuthKey, saveIscAuthKey } from "../api/iscClient";
import { ApiError } from "../api/errors";
import { ISCView } from "./ISCView";

let keyState = "saved";

beforeEach(() => {
  keyState = "saved";
  projectState.project.isc.auth_key = MARKER;
  vi.mocked(getIscAuthKey).mockClear();
  vi.mocked(saveIscAuthKey).mockClear();
  vi.mocked(saveIscAuthKey).mockImplementation(async () => {});
  projectState.update.mockClear();
  projectState.debouncedSave.mockClear();
  projectState.load.mockClear();
  vi.stubGlobal("fetch", vi.fn(async (url: string) => ({
    json: async () => (url.endsWith("/api/isc/status") ? { enabled: false, auth_key: keyState } : { instances: [] }),
  })));
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the mesh key saved on this system", () => {
  it("shows Saved on this system with Show and Change, and asks for the key only on Show", async () => {
    render(<ISCView />);
    expect(await screen.findByText("Saved on this system")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Change Shared auth key" })).toBeTruthy();
    expect(getIscAuthKey).not.toHaveBeenCalled();
    for (const input of document.querySelectorAll("input")) {
      expect((input as HTMLInputElement).value).not.toBe(MARKER);
    }

    await userEvent.click(screen.getByRole("button", { name: "Show" }));
    expect(await screen.findByText("k3Yq-7vPz-Lm2a-9rTe")).toBeTruthy();
    expect(getIscAuthKey).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Copy" })).toBeTruthy();

    await userEvent.click(screen.getByRole("button", { name: "Hide" }));
    expect(screen.queryByText("k3Yq-7vPz-Lm2a-9rTe")).toBeNull();
    expect(screen.getByText("Saved on this system")).toBeTruthy();
    expect(projectState.update).not.toHaveBeenCalled();
  });

  it("saves a new key on its own and reads the project back, never through the project save", async () => {
    render(<ISCView />);
    await userEvent.click(await screen.findByRole("button", { name: "Change Shared auth key" }));
    const box = screen.getByLabelText("Shared auth key") as HTMLInputElement;
    expect(box.placeholder).toBe("Leave blank to keep the saved one");
    await userEvent.type(box, "new-mesh-key");
    await userEvent.click(screen.getByRole("button", { name: "Save Shared auth key" }));

    await waitFor(() => expect(saveIscAuthKey).toHaveBeenCalledWith("new-mesh-key"));
    await waitFor(() => expect(projectState.load).toHaveBeenCalled());
    expect(projectState.update).not.toHaveBeenCalled();
    expect(projectState.debouncedSave).not.toHaveBeenCalled();
    expect(await screen.findByText("Saved on this system")).toBeTruthy();
  });

  it("keeps the box open with the system's reason when it refuses the key", async () => {
    vi.mocked(saveIscAuthKey).mockRejectedValueOnce(
      new ApiError(400, JSON.stringify({ detail: "Enter the new key." })),
    );
    render(<ISCView />);
    await userEvent.click(await screen.findByRole("button", { name: "Change Shared auth key" }));
    await userEvent.type(screen.getByLabelText("Shared auth key"), "x");
    await userEvent.click(screen.getByRole("button", { name: "Save Shared auth key" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Enter the new key.");
    expect((screen.getByLabelText("Shared auth key") as HTMLInputElement).value).toBe("x");
  });
});

describe("a mesh key this system does not have", () => {
  it("asks for it, saying it is not saved here", async () => {
    keyState = "needs_login";
    render(<ISCView />);
    expect(await screen.findByText("Not saved on this system. Enter it here.")).toBeTruthy();
    expect(screen.queryByText("Saved on this system")).toBeNull();
    expect(screen.queryByText(/No auth key set/)).toBeNull();
    await userEvent.type(screen.getByLabelText("Shared auth key"), "the-mesh-key");
    await userEvent.click(screen.getByRole("button", { name: "Save Shared auth key" }));
    await waitFor(() => expect(saveIscAuthKey).toHaveBeenCalledWith("the-mesh-key"));
  });
});

describe("no mesh key at all", () => {
  it("warns that ISC rejects every connection, and offers the box", async () => {
    keyState = "none";
    projectState.project.isc.auth_key = "";
    render(<ISCView />);
    expect(screen.getByText(/No auth key set/)).toBeTruthy();
    const box = screen.getByPlaceholderText("Shared auth key (required)") as HTMLInputElement;
    expect(box.type).toBe("password");
    expect((screen.getByRole("button", { name: "Save Shared auth key" }) as HTMLButtonElement).disabled).toBe(true);
  });
});
