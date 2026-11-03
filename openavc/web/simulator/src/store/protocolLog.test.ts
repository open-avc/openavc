import { describe, it, expect } from "vitest";
import type { LogEntry } from "./api";
import { mergeStoredLog } from "./protocolLog";

function entry(device_id: string, timestamp: number, data_text = "x"): LogEntry {
  return { timestamp, device_id, direction: "out", data: data_text, data_text, client_id: "" };
}

describe("mergeStoredLog", () => {
  it("interleaves two devices' stored entries by time", () => {
    const merged = mergeStoredLog(
      [],
      [entry("a", 1), entry("a", 3), entry("b", 2), entry("b", 4)],
      0,
      500,
    );
    expect(merged.map((e) => `${e.device_id}${e.timestamp}`)).toEqual(["a1", "b2", "a3", "b4"]);
  });

  it("keeps the newest entries when the total passes the cap", () => {
    const stored = Array.from({ length: 8 }, (_, i) => entry("a", i + 1));
    expect(mergeStoredLog([], stored, 0, 5).map((e) => e.timestamp)).toEqual([4, 5, 6, 7, 8]);
  });

  it("hands back the same list when nothing is new, so the page does not redraw", () => {
    const shown = [entry("a", 1)];
    expect(mergeStoredLog(shown, [entry("a", 1)], 0, 500)).toBe(shown);
    expect(mergeStoredLog(shown, [entry("a", 0.5)], 1, 500)).toBe(shown);
  });
});
