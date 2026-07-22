import { describe, it, expect } from "vitest";
import { formatLogsForExport, logExportFilename } from "./logViewHelpers";

const entry = (over: Partial<{ timestamp: number; level: string; category: string; message: string }>) => ({
  timestamp: 1_700_000_000,
  level: "INFO",
  category: "system",
  message: "hello",
  ...over,
});

describe("formatLogsForExport", () => {
  it("renders one line per entry with an ISO timestamp, level, category, message", () => {
    const text = formatLogsForExport([entry({})]);
    // 1_700_000_000s -> 2023-11-14T22:13:20.000Z
    expect(text).toBe("[2023-11-14T22:13:20.000Z] INFO system: hello");
  });

  it("joins multiple entries with newlines, in order", () => {
    const text = formatLogsForExport([
      entry({ message: "first" }),
      entry({ level: "ERROR", category: "device", message: "second" }),
    ]);
    expect(text.split("\n")).toEqual([
      "[2023-11-14T22:13:20.000Z] INFO system: first",
      "[2023-11-14T22:13:20.000Z] ERROR device: second",
    ]);
  });

  it("exports the FULL set, not just the last 200 the table renders", () => {
    const many = Array.from({ length: 300 }, (_, i) => entry({ message: `m${i}` }));
    const lines = formatLogsForExport(many).split("\n");
    expect(lines).toHaveLength(300);
    expect(lines[0]).toContain("m0");
    expect(lines[299]).toContain("m299");
  });

  it("returns an empty string for no entries", () => {
    expect(formatLogsForExport([])).toBe("");
  });
});

describe("logExportFilename", () => {
  it("builds a sortable, Windows-safe name from the given time", () => {
    // Month is 0-indexed: 6 = July.
    expect(logExportFilename(new Date(2026, 6, 22, 14, 30, 5))).toBe("openavc-log-20260722-143005.txt");
  });

  it("zero-pads every field", () => {
    expect(logExportFilename(new Date(2026, 0, 2, 3, 4, 5))).toBe("openavc-log-20260102-030405.txt");
  });
});
