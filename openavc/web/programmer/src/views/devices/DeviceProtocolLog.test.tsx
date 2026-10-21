import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, render, screen } from "@testing-library/react";

// The device page's Protocol log. What shipped broken: it matched any log
// line whose text contained the device id, so with the Simulator UI open an
// HTTP library's request line for /api/devices/audio_1 showed in audio_1's
// log, and audio_10's lines showed there too. And it read only the IDE's live
// list, the system's last few hundred lines: a device whose lines were older
// read "No log entries for this device yet.", and an open page lost them as
// the other devices' lines pushed them out.

const mocks = vi.hoisted(() => ({
  earlier: [] as unknown[],
  asked: [] as unknown[][],
}));

vi.mock("../../api/systemClient", () => ({
  getRecentLogs: (...args: unknown[]) => {
    mocks.asked.push(args);
    return Promise.resolve(mocks.earlier);
  },
}));

import { useLogStore } from "../../store/logStore";
import { DeviceProtocolLog } from "./DeviceProtocolLog";

function entry(source: string, message: string, device = "", category = "device", timestamp = 1_760_000_000) {
  return { timestamp, level: "INFO", source, category, message, device };
}

beforeEach(() => {
  mocks.earlier = [];
  mocks.asked = [];
  useLogStore.setState({ logEntries: [], logPaused: false });
});

describe("the device's Protocol log", () => {
  it("lists this device's lines and nothing that only names it", () => {
    useLogStore.getState().addLogBatch([
      entry("openavc.transport.tcp", "[audio_1] TX: get /amp/power", "audio_1"),
      entry("openavc.transport.tcp", "[audio_10] TX: get /amp/power", "audio_10"),
      entry("openavc.core.device_manager", "Reconnected device: audio_1", "", "device"),
      entry(
        "httpx",
        'HTTP Request: GET http://127.0.0.1:19500/api/devices/audio_1 "HTTP/1.1 200 OK"',
        "",
        "system",
      ),
    ]);
    render(<DeviceProtocolLog deviceId="audio_1" />);

    expect(screen.getByText("[audio_1] TX: get /amp/power")).toBeTruthy();
    expect(screen.getByText("Reconnected device: audio_1")).toBeTruthy();
    expect(screen.queryByText("[audio_10] TX: get /amp/power")).toBeNull();
    expect(screen.queryByText(/HTTP Request/)).toBeNull();
  });

  it("says so when the device has no lines yet", () => {
    useLogStore.getState().addLogBatch([
      entry("httpx", "HTTP Request: GET http://x/api/devices/audio_1", "", "system"),
    ]);
    render(<DeviceProtocolLog deviceId="audio_1" />);
    expect(screen.getByText("No log entries for this device yet.")).toBeTruthy();
  });

  it("shows the lines the server kept from before the page loaded", async () => {
    // The live stream started with the system's last hundred lines, none of
    // them this device's: its refused login was a minute ago.
    useLogStore.getState().addLogBatch([entry("openavc.transport.tcp", "[audio_2] TX: poll", "audio_2")]);
    mocks.earlier = [
      { timestamp: 1_759_999_900, level: "WARNING", source: "openavc.drivers.base", category: "device",
        message: "[audio_1] Authentication failed", device: "audio_1" },
    ];
    render(<DeviceProtocolLog deviceId="audio_1" />);

    expect(await screen.findByText("[audio_1] Authentication failed")).toBeTruthy();
    expect(screen.queryByText("No log entries for this device yet.")).toBeNull();
    expect(mocks.asked).toEqual([[50, "", "audio_1"]]);
  });

  it("shows a line the server kept and the live stream brought once, oldest first", async () => {
    const live = entry("openavc.transport.tcp", "[audio_1] RX: on", "audio_1");
    useLogStore.getState().addLogBatch([live]);
    mocks.earlier = [
      { timestamp: 1_759_999_000, level: "INFO", source: "openavc.drivers.base", category: "device",
        message: "[audio_1] Connected via tcp", device: "audio_1" },
      { ...live },
    ];
    render(<DeviceProtocolLog deviceId="audio_1" />);

    await screen.findByText("[audio_1] Connected via tcp");
    const lines = screen.getAllByText(/\[audio_1\]/).map((el) => el.textContent);
    expect(lines).toEqual(["[audio_1] Connected via tcp", "[audio_1] RX: on"]);
  });

  it("keeps the device's lines on an open page while other devices push them out of the live list", async () => {
    render(<DeviceProtocolLog deviceId="audio_1" />);
    act(() => {
      useLogStore.getState().addLogEntry(
        entry("openavc.drivers.base", "[audio_1] Disconnected", "audio_1", "device", 1_760_000_001),
      );
    });
    expect(screen.getByText("[audio_1] Disconnected")).toBeTruthy();

    // The live list keeps 500 lines; another device's polling fills it.
    act(() => {
      useLogStore.getState().addLogBatch(
        Array.from({ length: 600 }, (_, i) =>
          entry("openavc.transport.tcp", `[audio_2] TX: poll ${i}`, "audio_2", "device", 1_760_000_002 + i),
        ),
      );
    });
    expect(useLogStore.getState().logEntries.some((e) => e.device === "audio_1")).toBe(false);
    expect(screen.getByText("[audio_1] Disconnected")).toBeTruthy();
  });
});
