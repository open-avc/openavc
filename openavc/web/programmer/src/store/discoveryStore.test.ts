import { describe, expect, it } from "vitest";
import { finishedScanStatus, scanHasEnded } from "./discoveryStore";

describe("finishedScanStatus", () => {
  it("keeps a stopped scan cancelled for every Programmer, not only the one that stopped it", () => {
    expect(finishedScanStatus("cancelled")).toBe("cancelled");
  });

  it("keeps a scan that covered less than was asked partial", () => {
    expect(finishedScanStatus("partial")).toBe("partial");
  });

  it("reads anything else as complete", () => {
    expect(finishedScanStatus("complete")).toBe("complete");
    expect(finishedScanStatus(undefined)).toBe("complete");
    expect(finishedScanStatus("running")).toBe("complete");
  });
});

describe("scanHasEnded", () => {
  it("is true for a finished or stopped scan, false otherwise", () => {
    expect(["complete", "partial", "cancelled"].map(scanHasEnded)).toEqual([true, true, true]);
    expect(["idle", "running"].map(scanHasEnded)).toEqual([false, false]);
  });
});
