import { describe, it, expect } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { useConnectionTestResult } from "./useConnectionTestResult";

// A Test Connection result describes the moment it was run. Once the device
// connects or drops, a red "could not connect" beside a device that reads
// online (or a green one beside an offline device) is wrong, so it goes.
describe("the device page's Test Connection result", () => {
  const failed = { success: false, error: "Connection timed out (5s)", latency_ms: null };

  it("is cleared when the device comes online", () => {
    const { result, rerender } = renderHook(
      ({ connected }) => useConnectionTestResult("tv1", connected),
      { initialProps: { connected: false } },
    );
    act(() => result.current[1](failed));
    expect(result.current[0]).toEqual(failed);

    rerender({ connected: true });
    expect(result.current[0]).toBeNull();
  });

  it("stays while nothing about the connection changes", () => {
    const { result, rerender } = renderHook(
      ({ connected }) => useConnectionTestResult("tv1", connected),
      { initialProps: { connected: false } },
    );
    act(() => result.current[1](failed));
    rerender({ connected: false });
    expect(result.current[0]).toEqual(failed);
  });

  it("is not carried over to another device", () => {
    const { result, rerender } = renderHook(
      ({ id }) => useConnectionTestResult(id, false),
      { initialProps: { id: "tv1" } },
    );
    act(() => result.current[1](failed));
    rerender({ id: "tv2" });
    expect(result.current[0]).toBeNull();
  });
});
