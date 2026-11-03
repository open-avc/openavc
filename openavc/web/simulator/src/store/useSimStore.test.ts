import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, renderHook } from "@testing-library/react";
import type { DeviceInfo, LogEntry } from "./api";

// What shipped: the Protocol Log showed only what arrived over the socket after
// the page connected. A driver's connect, sign-in and subscriptions all happen
// before the Simulator window loads, so a talking device read "No protocol
// traffic yet" until its next poll.

const api = vi.hoisted(() => ({
  fetchDevices: vi.fn<() => Promise<DeviceInfo[]>>(),
  fetchLog: vi.fn<(id: string, limit?: number) => Promise<LogEntry[]>>(),
}));

vi.mock("./api", () => ({ BASE: "", ...api }));
vi.mock("./session", () => ({ authSubprotocols: () => undefined }));

import { useSimStore } from "./useSimStore";

class FakeSocket {
  static OPEN = 1;
  static all: FakeSocket[] = [];
  readyState = 0;
  onopen: (() => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(public url: string) {
    FakeSocket.all.push(this);
  }
  open() {
    this.readyState = 1;
    this.onopen?.();
  }
  send(msg: object) {
    this.onmessage?.({ data: JSON.stringify(msg) });
  }
  close() {
    this.readyState = 3;
    this.onclose?.();
  }
  static latest() {
    return FakeSocket.all[FakeSocket.all.length - 1];
  }
}

function entry(device_id: string, timestamp: number, data_text: string): LogEntry {
  return { timestamp, device_id, direction: "in", data: data_text, data_text, client_id: "c1" };
}

const DEVICE = { device_id: "dsp_1" } as DeviceInfo;

async function flush() {
  await act(async () => {
    for (let i = 0; i < 10; i++) await Promise.resolve();
  });
}

beforeEach(() => {
  vi.useFakeTimers();
  FakeSocket.all = [];
  vi.stubGlobal("WebSocket", FakeSocket);
  api.fetchDevices.mockResolvedValue([DEVICE]);
  api.fetchLog.mockResolvedValue([]);
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("Protocol Log", () => {
  it("shows what a device said before the page connected", async () => {
    const burst = [entry("dsp_1", 100.1, "SESSION set verbose true"), entry("dsp_1", 100.2, "DEVICE get version")];
    api.fetchLog.mockResolvedValue(burst);
    const { result, unmount } = renderHook(() => useSimStore());

    act(() => FakeSocket.latest().open());
    await flush();

    expect(api.fetchLog).toHaveBeenCalledWith("dsp_1", 500);
    expect(result.current.log.map((e) => e.data_text)).toEqual(["SESSION set verbose true", "DEVICE get version"]);
    unmount();
  });

  it("shows an entry once when it arrives live and in the stored copy", async () => {
    let answer: (v: LogEntry[]) => void = () => {};
    api.fetchLog.mockReturnValue(new Promise((r) => { answer = r; }));
    const { result, unmount } = renderHook(() => useSimStore());

    act(() => FakeSocket.latest().open());
    await flush();
    const late = entry("dsp_1", 100.3, "Level1 get level 1");
    // Logged while the stored copy was on its way: it arrives on the socket
    // AND in the answer.
    act(() => FakeSocket.latest().send({ type: "protocol", ...late }));
    answer([entry("dsp_1", 100.1, "DEVICE get version"), late]);
    await flush();

    expect(result.current.log.map((e) => e.data_text)).toEqual(["DEVICE get version", "Level1 get level 1"]);
    unmount();
  });

  it("keeps what Clear removed cleared when the socket reconnects", async () => {
    api.fetchLog.mockResolvedValue([entry("dsp_1", 100.1, "DEVICE get version")]);
    const { result, unmount } = renderHook(() => useSimStore());
    act(() => FakeSocket.latest().open());
    await flush();
    expect(result.current.log).toHaveLength(1);

    act(() => result.current.clearLog());
    expect(result.current.log).toHaveLength(0);

    // The simulator restarts or the socket blips; the stored log now holds one
    // newer entry as well.
    api.fetchLog.mockResolvedValue([
      entry("dsp_1", 100.1, "DEVICE get version"),
      entry("dsp_1", 130.0, "DEVICE get version (probe)"),
    ]);
    act(() => FakeSocket.latest().close());
    await flush();
    act(() => { vi.advanceTimersByTime(2000); });
    act(() => FakeSocket.latest().open());
    await flush();

    expect(result.current.log.map((e) => e.data_text)).toEqual(["DEVICE get version (probe)"]);
    unmount();
  });
});
