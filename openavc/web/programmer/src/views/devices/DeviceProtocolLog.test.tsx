import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";

// The device page's Protocol log. What shipped broken: it matched any log
// line whose text contained the device id, so with the Simulator UI open an
// HTTP library's request line for /api/devices/audio_1 showed in audio_1's
// log, and audio_10's lines showed there too.

const mocks = vi.hoisted(() => ({ entries: [] as unknown[] }));

vi.mock("../../store/logStore", () => ({
  useLogStore: (selector: (s: unknown) => unknown) =>
    selector({ logEntries: mocks.entries }),
}));

import { DeviceProtocolLog } from "./DeviceProtocolLog";

let nextId = 1;
function entry(source: string, message: string, device = "", category = "device") {
  return { id: nextId++, timestamp: 1_760_000_000, level: "INFO", source, category, message, device };
}

describe("the device's Protocol log", () => {
  it("lists this device's lines and nothing that only names it", () => {
    mocks.entries = [
      entry("openavc.transport.tcp", "[audio_1] TX: get /amp/power", "audio_1"),
      entry("openavc.transport.tcp", "[audio_10] TX: get /amp/power", "audio_10"),
      entry("openavc.core.device_manager", "Reconnected device: audio_1", "", "device"),
      entry(
        "httpx",
        'HTTP Request: GET http://127.0.0.1:19500/api/devices/audio_1 "HTTP/1.1 200 OK"',
        "",
        "system",
      ),
    ];
    render(<DeviceProtocolLog deviceId="audio_1" />);

    expect(screen.getByText("[audio_1] TX: get /amp/power")).toBeTruthy();
    expect(screen.getByText("Reconnected device: audio_1")).toBeTruthy();
    expect(screen.queryByText("[audio_10] TX: get /amp/power")).toBeNull();
    expect(screen.queryByText(/HTTP Request/)).toBeNull();
  });

  it("says so when the device has no lines yet", () => {
    mocks.entries = [entry("httpx", "HTTP Request: GET http://x/api/devices/audio_1", "", "system")];
    render(<DeviceProtocolLog deviceId="audio_1" />);
    expect(screen.getByText("No log entries for this device yet.")).toBeTruthy();
  });
});
