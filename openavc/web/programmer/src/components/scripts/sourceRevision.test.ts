import { describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { ApiError } from "../../api/errors";
import { useSourceRevision } from "./sourceRevision";

const CHANGED = "This driver was changed by another session since you opened it.";

function changedElsewhere(revision: string): ApiError {
  return new ApiError(409, JSON.stringify({ detail: CHANGED, code: "driver_changed", revision }));
}

describe("The revision the Code view's open driver was read at", () => {
  it("is the read's, then what each save stored", () => {
    const { result } = renderHook(() => useSourceRevision());

    act(() => result.current.read("r1"));
    expect(result.current.readAt.current).toBe("r1");
    act(() => result.current.read("r2"));
    expect(result.current.readAt.current).toBe("r2");
    act(() => result.current.read(undefined));
    expect(result.current.readAt.current).toBeNull();
  });

  it("takes a save refused as made from an older copy, and names which button it was", () => {
    const { result } = renderHook(() => useSourceRevision());

    let taken = false;
    act(() => { taken = result.current.refused(changedElsewhere("r2"), true); });

    expect(taken).toBe(true);
    expect(result.current.conflict).toEqual({ message: CHANGED, revision: "r2", reload: true });
  });

  it("leaves any other failure to the caller", () => {
    const { result } = renderHook(() => useSourceRevision());

    let taken = true;
    act(() => {
      taken = result.current.refused(new ApiError(404, JSON.stringify({ detail: "Not found" })), false);
    });

    expect(taken).toBe(false);
    expect(result.current.conflict).toBeNull();
  });

  it("saves mine over it naming the revision saved now", () => {
    const { result } = renderHook(() => useSourceRevision());
    act(() => result.current.read("r1"));
    act(() => { result.current.refused(changedElsewhere("r2"), false); });
    const retry = vi.fn(() => expect(result.current.readAt.current).toBe("r2"));

    act(() => result.current.saveMine(retry));

    expect(retry).toHaveBeenCalledTimes(1);
    expect(result.current.conflict).toBeNull();
  });

  it("keeps editing at the revision it was read at", () => {
    const { result } = renderHook(() => useSourceRevision());
    act(() => result.current.read("r1"));
    act(() => { result.current.refused(changedElsewhere("r2"), false); });

    act(() => result.current.dismiss());

    expect(result.current.conflict).toBeNull();
    expect(result.current.readAt.current).toBe("r1");
  });
});
