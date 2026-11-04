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
  devices: [] as Array<{ id: string; name: string; driver: string; config: Record<string, unknown> }>,
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
  useProjectStore: (selector: (s: unknown) => unknown) => selector({ dirty: false, project: { devices: mocks.devices } }),
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
  mocks.devices = [
    { id: "display", name: "Test Display", driver: "acme", config: {} },
    { id: "controller", name: "Test Controller", driver: "acme", config: {} },
  ];
  localStorage.removeItem("sim_confirm_dismissed");
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
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

  it("asks for devices even when the old confirmation was dismissed, then sends only the selection", async () => {
    localStorage.setItem("sim_confirm_dismissed", "true");
    const fetchMock = vi.fn().mockResolvedValue(new Response('{"ui_url":"/simulator/"}'));
    vi.stubGlobal("fetch", fetchMock);
    vi.spyOn(window, "open").mockReturnValue(null);
    render(<Sidebar activeView="dashboard" onViewChange={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: "Simulate Devices" }));
    expect(fetchMock).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("checkbox", { name: /Test Controller/ }));
    await userEvent.click(screen.getByRole("button", { name: "Start Simulation" }));
    expect(fetchMock).toHaveBeenCalledWith("/api/simulation/start", expect.objectContaining({
      body: JSON.stringify({ device_ids: ["display"] }),
    }));
  });

  it("keeps all-devices mode when all are selected, including devices hidden by search", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response('{"ui_url":"/simulator/"}'));
    vi.stubGlobal("fetch", fetchMock);
    vi.spyOn(window, "open").mockReturnValue(null);
    render(<Sidebar activeView="dashboard" onViewChange={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: "Simulate Devices" }));
    await userEvent.type(screen.getByRole("searchbox", { name: "Find devices" }), "display");
    expect(screen.getAllByRole("checkbox")).toHaveLength(1);
    await userEvent.click(screen.getByRole("button", { name: "Start Simulation" }));
    expect(fetchMock).toHaveBeenCalledWith("/api/simulation/start", expect.objectContaining({
      body: JSON.stringify({ device_ids: null }),
    }));
  });

  it("requires a selection, and select-all restores it", async () => {
    render(<Sidebar activeView="dashboard" onViewChange={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: "Simulate Devices" }));
    await userEvent.click(screen.getByRole("button", { name: "Clear selection" }));
    expect(screen.getByRole("button", { name: "Start Simulation" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Select all" }));
    expect(screen.getByRole("button", { name: "Start Simulation" })).toBeEnabled();
  });

  it("cannot start an empty project", async () => {
    mocks.devices = [];
    render(<Sidebar activeView="dashboard" onViewChange={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: "Simulate Devices" }));
    expect(screen.getByRole("button", { name: "Start Simulation" })).toBeDisabled();
  });

  it("selects devices loaded after the dialog opens, until a person changes the selection", async () => {
    const loadedDevices = mocks.devices;
    mocks.devices = [];
    const props = { activeView: "dashboard", onViewChange: vi.fn() };
    const { rerender } = render(<Sidebar {...props} />);
    await userEvent.click(screen.getByRole("button", { name: "Simulate Devices" }));
    mocks.devices = loadedDevices;
    rerender(<Sidebar {...props} />);
    expect(screen.getAllByRole("checkbox")).toHaveLength(2);
    for (const checkbox of screen.getAllByRole("checkbox")) expect(checkbox).toBeChecked();
    await userEvent.click(screen.getByRole("checkbox", { name: /Test Controller/ }));
    mocks.devices = [...loadedDevices, { id: "new", name: "New Device", driver: "acme", config: {} }];
    rerender(<Sidebar {...props} />);
    expect(screen.getByRole("checkbox", { name: /Test Display/ })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: /Test Controller/ })).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: /New Device/ })).not.toBeChecked();
  });
});
