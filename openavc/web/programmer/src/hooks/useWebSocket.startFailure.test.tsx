import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, renderHook } from "@testing-library/react";

// A tab whose connection is kept out by, or comes back to, a start that
// failed says so, with the server's reason, instead of waiting with nothing
// loaded; once a start succeeds the notice goes and the tab loads.

type Close = { code: number; reason: string };

const lifecycle = vi.hoisted(() => ({
  connect: [] as Array<() => void>,
  disconnect: [] as Array<(close: Close) => void>,
}));

vi.mock("../api/wsClient", () => ({
  connect: vi.fn(),
  disconnect: vi.fn(),
  send: vi.fn(),
  onMessage: vi.fn(() => () => {}),
  onConnect: vi.fn((h: () => void) => {
    lifecycle.connect.push(h);
    return () => {};
  }),
  onDisconnect: vi.fn((h: (close: Close) => void) => {
    lifecycle.disconnect.push(h);
    return () => {};
  }),
}));

vi.mock("../api/serverReady", () => ({ waitForServerReady: vi.fn() }));
vi.mock("../api/startupStatus", () => ({
  askStartupStatus: vi.fn(async () => ({ answered: false, status: null })),
}));

import { useWebSocket } from "./useWebSocket";
import { waitForServerReady } from "../api/serverReady";
import { askStartupStatus } from "../api/startupStatus";
import { useToastStore } from "../store/toastStore";
import { useProjectStore } from "../store/projectStore";
import { usePluginStore } from "../store/pluginStore";
import { usePanelDevicesStore } from "../store/panelDevicesStore";
import type { StartupStatus } from "../api/startupStatus";

const FAILED: StartupStatus = {
  phase: "failed", version: "0.41.0", error: "Loading the project failed: bad JSON", held: true, retryIn: null, problems: [],
};
const READY: StartupStatus = { phase: "ready", version: "0.41.0", error: null, held: false, retryIn: null, problems: [] };

const projectLoad = vi.fn(async () => false);

beforeEach(() => {
  lifecycle.connect.length = 0;
  lifecycle.disconnect.length = 0;
  useToastStore.setState({ toasts: [] });
  projectLoad.mockClear();
  useProjectStore.setState({ load: projectLoad } as never);
  usePluginStore.setState({ load: vi.fn(async () => true) } as never);
  usePanelDevicesStore.setState({ load: vi.fn(async () => {}) } as never);
});

const DROPPED: Close = { code: 1006, reason: "" };

async function reconnect() {
  await act(async () => {
    lifecycle.disconnect.forEach((h) => h(DROPPED));
    lifecycle.connect.forEach((h) => h());
  });
}

describe("useWebSocket on its first connect", () => {
  it("says what the start went on past", async () => {
    vi.mocked(askStartupStatus).mockResolvedValueOnce({
      answered: true,
      status: {
        ...READY,
        problems: [{ stage: "cloud_agent", detail: "The connection to OpenAVC Cloud did not start." }],
      },
    });
    renderHook(() => useWebSocket());
    await act(async () => lifecycle.connect.forEach((h) => h()));

    const toasts = useToastStore.getState().toasts;
    expect(toasts).toHaveLength(1);
    expect(toasts[0].message).toContain("The connection to OpenAVC Cloud did not start.");
    expect(projectLoad).toHaveBeenCalledTimes(1);
  });
});

describe("useWebSocket after a restart", () => {
  it("shows a failed start's reason, then loads once a start succeeds", async () => {
    renderHook(() => useWebSocket());
    await act(async () => lifecycle.connect.forEach((h) => h()));
    expect(projectLoad).toHaveBeenCalledTimes(1);

    vi.mocked(waitForServerReady).mockResolvedValueOnce(FAILED);
    await reconnect();
    const toasts = useToastStore.getState().toasts;
    expect(toasts).toHaveLength(1);
    expect(toasts[0].message).toContain("Loading the project failed: bad JSON");
    expect(projectLoad).toHaveBeenCalledTimes(1);

    vi.mocked(waitForServerReady).mockResolvedValueOnce(READY);
    await reconnect();
    expect(useToastStore.getState().toasts).toHaveLength(0);
    expect(projectLoad).toHaveBeenCalledTimes(2);
  });

  it("shows a failed start's reason while the server keeps the socket out", async () => {
    renderHook(() => useWebSocket());
    await act(async () => lifecycle.connect.forEach((h) => h()));

    // This server turns the socket away until it is ready, so the tab hears
    // of the failure when the socket closes, not from a reconnect.
    vi.mocked(askStartupStatus).mockResolvedValueOnce({ answered: true, status: FAILED });
    await act(async () => lifecycle.disconnect.forEach((h) => h({ code: 4503, reason: "failed" })));
    const toasts = useToastStore.getState().toasts;
    expect(toasts).toHaveLength(1);
    expect(toasts[0].message).toContain("Loading the project failed: bad JSON");

    vi.mocked(waitForServerReady).mockResolvedValueOnce(READY);
    await act(async () => lifecycle.connect.forEach((h) => h()));
    expect(useToastStore.getState().toasts).toHaveLength(0);
    expect(projectLoad).toHaveBeenCalledTimes(2);
  });
});
