import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";

// Starting simulation opens the Simulator UI in a new tab. Once that tab was
// closed, nothing in the IDE opened it again while simulation ran: the
// sidebar's simulation button had become Stop. A second button opens it
// again, signed in the same way as the first time.

const mocks = vi.hoisted(() => ({
  liveState: {} as Record<string, unknown>,
  showError: vi.fn(),
}));

vi.mock("../../store/pluginStore", () => ({
  usePluginStore: (selector: (s: unknown) => unknown) =>
    selector({ plugins: [], extensions: { views: [] } }),
}));

vi.mock("../../store/connectionStore", () => ({
  useConnectionStore: (selector: (s: unknown) => unknown) =>
    selector({ connected: true, liveState: mocks.liveState }),
}));

vi.mock("../../store/projectStore", () => ({
  useProjectStore: (selector: (s: unknown) => unknown) => selector({ dirty: false }),
}));

vi.mock("../../store/toastStore", () => ({ showError: mocks.showError }));

vi.mock("../../api/restClient", () => ({ getTunnelPrefix: () => "" }));

vi.mock("../../api/auth", () => ({
  getSessionToken: () => "session-token",
  hasSession: () => true,
  logout: vi.fn(),
}));

vi.mock("../shared/Modal", () => ({
  Modal: ({ children }: { children: ReactNode }) => <div role="dialog">{children}</div>,
}));

import { Sidebar } from "./Sidebar";

const SIMULATING = {
  "system.simulation_active": true,
  "system.simulation_ui_url": "/simulator/",
};

beforeEach(() => {
  mocks.liveState = {};
  mocks.showError.mockReset();
  localStorage.removeItem("sim_confirm_dismissed");
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("Sidebar: the Simulator UI tab", () => {
  it("opens again while simulation runs", async () => {
    mocks.liveState = { ...SIMULATING };
    const open = vi.spyOn(window, "open").mockReturnValue({} as Window);
    render(<Sidebar activeView="dashboard" onViewChange={vi.fn()} />);

    await userEvent.click(screen.getByRole("button", { name: "Open Simulator UI" }));

    expect(open).toHaveBeenCalledWith(
      new URL("/simulator/", window.location.href).href,
      "openavc-simulator",
    );
  });

  it("hands the reopened tab this session's sign-in", async () => {
    mocks.liveState = { ...SIMULATING };
    vi.spyOn(window, "open").mockReturnValue({} as Window);
    const post = vi.spyOn(window, "postMessage").mockImplementation(() => {});
    render(<Sidebar activeView="dashboard" onViewChange={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: "Open Simulator UI" }));

    window.dispatchEvent(
      new MessageEvent("message", {
        data: { type: "openavc:simulator-ready" },
        origin: window.location.origin,
        source: window,
      }),
    );

    expect(post).toHaveBeenCalledWith(
      { type: "openavc:simulator-token", token: "session-token" },
      window.location.origin,
    );
  });

  it("gives the address when the browser blocks the tab", async () => {
    mocks.liveState = { ...SIMULATING };
    vi.spyOn(window, "open").mockReturnValue(null);
    render(<Sidebar activeView="dashboard" onViewChange={vi.fn()} />);

    await userEvent.click(screen.getByRole("button", { name: "Open Simulator UI" }));

    expect(mocks.showError).toHaveBeenCalledWith(
      expect.stringContaining(new URL("/simulator/", window.location.href).href),
    );
  });

  it("is not offered while nothing is simulated", () => {
    render(<Sidebar activeView="dashboard" onViewChange={vi.fn()} />);
    expect(screen.queryByRole("button", { name: "Open Simulator UI" })).toBeNull();
  });

  it("is mentioned before simulation starts", async () => {
    render(<Sidebar activeView="dashboard" onViewChange={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: "Simulate Devices" }));
    expect(screen.getByText("The Simulator UI opens in a new browser tab")).toBeTruthy();
  });
});
