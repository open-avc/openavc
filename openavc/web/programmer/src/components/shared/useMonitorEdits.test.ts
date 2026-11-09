import { describe, it, expect, vi } from "vitest";
import { renderHook } from "@testing-library/react";
import { useProjectStore } from "../../store/projectStore";
import { useMonitorEdits } from "./useMonitorEdits";

describe("useMonitorEdits", () => {
  it("makes a change to the monitors one undoable step, saved after 1.5 s", () => {
    const updateWithUndo = vi.fn();
    const debouncedSave = vi.fn();
    useProjectStore.setState({ updateWithUndo, debouncedSave } as never);
    const { result } = renderHook(() => useMonitorEdits());
    result.current([{ key: "var.room_temp" }], "Monitor var.room_temp");
    expect(updateWithUndo).toHaveBeenCalledWith(
      { monitors: [{ key: "var.room_temp" }] },
      "Monitor var.room_temp",
    );
    expect(debouncedSave).toHaveBeenCalledWith(1500);
    expect(updateWithUndo.mock.invocationCallOrder[0]).toBeLessThan(
      debouncedSave.mock.invocationCallOrder[0],
    );
  });

  it("hands back the same function from one render to the next", () => {
    const { result, rerender } = renderHook(() => useMonitorEdits());
    const first = result.current;
    rerender();
    expect(result.current).toBe(first);
  });
});
