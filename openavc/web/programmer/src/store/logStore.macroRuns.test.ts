import { beforeEach, describe, expect, it } from "vitest";
import { useLogStore } from "./logStore";

beforeEach(() => {
  useLogStore.getState().clearMacroRuns();
  useLogStore.getState().resetMacroProgress();
});

describe("macro invocations", () => {
  it("keeps the parent cancellable and attributes the outcome after child progress", () => {
    const store = useLogStore.getState();
    store.startMacroRun("parent");
    store.startMacroRun("child");
    store.setMacroProgress({ macroId: "child" });
    store.finishMacroRun("child", "error");
    expect(useLogStore.getState().runningMacros).toEqual({ parent: 1 });
    store.finishMacroRun("parent", "error");
    expect(useLogStore.getState().lastRun?.macroId).toBe("parent");
    expect(useLogStore.getState().runningMacros).toEqual({});
  });

  it("keeps overlapping invocations running until all finish", () => {
    const store = useLogStore.getState();
    store.startMacroRun("macro");
    store.startMacroRun("macro");
    store.finishMacroRun("macro", "completed");
    expect(useLogStore.getState().runningMacros).toEqual({ macro: 1 });
    store.finishMacroRun("macro", "cancelled");
    expect(useLogStore.getState().runningMacros).toEqual({});
  });

  it("discards in-flight counts when the connection cannot supply their outcomes", () => {
    const store = useLogStore.getState();
    store.startMacroRun("parent");
    store.startMacroRun("child");
    store.clearMacroRuns();
    expect(useLogStore.getState().runningMacros).toEqual({});
    expect(useLogStore.getState().macroProgress.status).toBe("idle");
  });
});
