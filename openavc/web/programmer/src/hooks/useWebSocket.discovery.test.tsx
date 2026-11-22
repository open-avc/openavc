import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, renderHook } from "@testing-library/react";

// A Discovery scan's messages, as they arrive on the socket, reach the
// discovery store: a scan this tab did not start shows as running from its
// discovery.started, and ends from its discovery.complete.

const socket = vi.hoisted(() => ({
  handlers: [] as Array<(msg: Record<string, unknown>) => void>,
}));

vi.mock("../api/wsClient", () => ({
  connect: vi.fn(),
  disconnect: vi.fn(),
  send: vi.fn(),
  onMessage: vi.fn((h: (msg: Record<string, unknown>) => void) => {
    socket.handlers.push(h);
    return () => {};
  }),
  onConnect: vi.fn(() => () => {}),
  onDisconnect: vi.fn(() => () => {}),
}));

vi.mock("../api/serverReady", () => ({ waitForServerReady: vi.fn() }));
vi.mock("../api/startupStatus", () => ({
  askStartupStatus: vi.fn(async () => ({ answered: false, status: null })),
}));

import { useWebSocket } from "./useWebSocket";
import { useDiscoveryStore } from "../store/discoveryStore";

function receive(msg: Record<string, unknown>) {
  act(() => socket.handlers.forEach((h) => h(msg)));
}

beforeEach(() => {
  socket.handlers.length = 0;
  useDiscoveryStore.getState().clear();
});

describe("useWebSocket — a Discovery scan's messages", () => {
  it("shows a scan started elsewhere as running, then ended", () => {
    renderHook(() => useWebSocket());

    receive({
      type: "discovery.started",
      scan_id: "scan_4_1700000200",
      subnets: ["10.0.0.0/24"],
      started_at: 1700000200,
      budget_seconds: 120,
    });
    expect(useDiscoveryStore.getState().status).toBe("running");
    expect(useDiscoveryStore.getState().budgetSeconds).toBe(120);

    receive({
      type: "discovery.phase",
      phase: "ping_sweep",
      phase_number: 1,
      total_phases: 8,
      message: "Scanning for live hosts...",
      progress: 0.1,
      warnings: ["mDNS listener unavailable"],
    });
    expect(useDiscoveryStore.getState().phase).toBe("ping_sweep");
    expect(useDiscoveryStore.getState().warnings).toEqual(["mDNS listener unavailable"]);

    receive({
      type: "discovery.complete",
      scan_id: "scan_4_1700000200",
      status: "cancelled",
      total_devices: 0,
      duration_seconds: 12,
      warnings: [],
    });
    expect(useDiscoveryStore.getState().status).toBe("cancelled");
  });
});
