import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, renderHook } from "@testing-library/react";

// A tab whose connection comes back to a start that failed says so, with the
// server's reason, instead of waiting with nothing loaded; once a start
// succeeds the notice goes and the tab loads.

const lifecycle = vi.hoisted(() => ({
  connect: [] as Array<() => void>,
  disconnect: [] as Array<() => void>,
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
  onDisconnect: vi.fn((h: () => void) => {
    lifecycle.disconnect.push(h);
    return () => {};
  }),
}));

vi.mock("../api/serverReady", () => ({ waitForServerReady: vi.fn() }));

import { useWebSocket } from "./useWebSocket";
import { waitForServerReady } from "../api/serverReady";
import { useToastStore } from "../store/toastStore";
import { useProjectStore } from "../store/projectStore";
import { usePluginStore } from "../store/pluginStore";
import { usePanelDevicesStore } from "../store/panelDevicesStore";
import type { StartupStatus } from "../api/startupStatus";

const FAILED: StartupStatus = {
  phase: "failed", version: "0.41.0", error: "Loading the project failed: bad JSON", held: true, retryIn: null,
};
const READY: StartupStatus = { phase: "ready", version: "0.41.0", error: null, held: false, retryIn: null };

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

async function reconnect() {
  await act(async () => {
    lifecycle.disconnect.forEach((h) => h());
    lifecycle.connect.forEach((h) => h());
  });
}

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
});
