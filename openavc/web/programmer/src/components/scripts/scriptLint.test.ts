import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { lintPayload, issuesAtLine, issueSummary, useScriptLint } from "./scriptLint";
import type { ScriptIssue } from "./scriptLint";

vi.mock("../../api/restClient", () => ({
  validateScripts: vi.fn(),
}));
import * as api from "../../api/restClient";

const validateScripts = api.validateScripts as unknown as ReturnType<typeof vi.fn>;

function issue(over: Partial<ScriptIssue> = {}): ScriptIssue {
  return {
    line: 4,
    event: "custom.select_source",
    message: 'Nothing in this project emits "custom.select_source", so this handler runs only if an outside system emits it over the API.',
    ...over,
  };
}

describe("lintPayload", () => {
  it("names every script, and carries text only for the open one", () => {
    expect(lintPayload(["router", "picker"], "router", "@on_event('custom.x')")).toEqual([
      { id: "router", source: "@on_event('custom.x')" },
      { id: "picker" },
    ]);
  });

  it("names every script even with none open, so the list still gets marks", () => {
    expect(lintPayload(["router", "picker"], null, "")).toEqual([
      { id: "router" },
      { id: "picker" },
    ]);
  });

  it("changes when the open source does, so an edit re-asks", () => {
    const before = JSON.stringify(lintPayload(["a"], "a", "x = 1"));
    const after = JSON.stringify(lintPayload(["a"], "a", "x = 2"));
    expect(after).not.toBe(before);
  });
});

describe("issuesAtLine", () => {
  const issues = [issue({ line: 4 }), issue({ line: 12, event: "custom.other" })];

  it("picks the handlers on one line", () => {
    expect(issuesAtLine(issues, 12)).toEqual([issues[1]]);
    expect(issuesAtLine(issues, 5)).toEqual([]);
  });

  it("treats a script with nothing wrong as nothing wrong", () => {
    expect(issuesAtLine(undefined, 4)).toEqual([]);
  });
});

describe("issueSummary", () => {
  it("counts handlers, singular and plural", () => {
    expect(issueSummary([issue()])).toBe("1 handler");
    expect(issueSummary([issue(), issue({ line: 9 })])).toBe("2 handlers");
  });
});

describe("useScriptLint", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    validateScripts.mockReset();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("asks straight away, because the untouched project is the guilty one", async () => {
    validateScripts.mockResolvedValue({ router: { issues: [issue()] } });
    const { result } = renderHook(() => useScriptLint(["router"], "router", "src"));

    expect(validateScripts).toHaveBeenCalledTimes(1);
    await act(async () => {});
    expect(result.current).toEqual({ router: [issue()] });
  });

  it("keeps a script with no issues out of the result", async () => {
    validateScripts.mockResolvedValue({
      router: { issues: [] },
      picker: { issues: [issue()] },
    });
    const { result } = renderHook(() => useScriptLint(["router", "picker"], null, ""));
    await act(async () => {});
    expect(Object.keys(result.current)).toEqual(["picker"]);
  });

  it("asks nothing when there are no scripts", () => {
    renderHook(() => useScriptLint([], null, ""));
    expect(validateScripts).not.toHaveBeenCalled();
  });

  it("debounces an edit rather than asking per keystroke", async () => {
    validateScripts.mockResolvedValue({ router: { issues: [] } });
    const { rerender } = renderHook(
      ({ src }) => useScriptLint(["router"], "router", src),
      { initialProps: { src: "a" } },
    );
    expect(validateScripts).toHaveBeenCalledTimes(1);

    rerender({ src: "ab" });
    rerender({ src: "abc" });
    expect(validateScripts).toHaveBeenCalledTimes(1);

    await act(async () => {
      vi.advanceTimersByTime(1000);
    });
    expect(validateScripts).toHaveBeenCalledTimes(2);
  });

  it("keeps the last result when the request fails", async () => {
    validateScripts.mockResolvedValueOnce({ router: { issues: [issue()] } });
    const { result, rerender } = renderHook(
      ({ src }) => useScriptLint(["router"], "router", src),
      { initialProps: { src: "a" } },
    );
    await act(async () => {});
    expect(result.current).toEqual({ router: [issue()] });

    // A dropped connection says nothing about the scripts, and clearing the
    // marks would read as "you fixed it".
    validateScripts.mockRejectedValueOnce(new Error("offline"));
    rerender({ src: "ab" });
    await act(async () => {
      vi.advanceTimersByTime(1000);
    });
    expect(result.current).toEqual({ router: [issue()] });
  });
});
