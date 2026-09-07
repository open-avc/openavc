import { beforeEach, describe, expect, it } from "vitest";
import { useLogStore } from "./logStore";

/**
 * How a trigger's last run is held.
 *
 * Unlike the "just fired" flash beside it this is the SERVER's record, read
 * back from `GET /api/triggers` — so the rules that matter are that a re-read
 * replaces rather than merges, and that a dropped socket does not erase it.
 */

beforeEach(() => {
  useLogStore.setState({ triggerRuns: {} });
});

describe("trigger run records", () => {
  it("replaces the map on a re-read, so a deleted trigger's record goes too", () => {
    const store = useLogStore.getState();
    store.setTriggerRuns({ a: { outcome: "failed" }, b: { outcome: "completed" } });

    store.setTriggerRuns({ b: { outcome: "completed" } });

    expect(useLogStore.getState().triggerRuns).toEqual({ b: { outcome: "completed" } });
  });

  it("updates one trigger without disturbing the others", () => {
    const store = useLogStore.getState();
    store.setTriggerRuns({ a: { outcome: "completed" }, b: { outcome: "completed" } });

    store.setTriggerRun("a", { outcome: "failed" });

    expect(useLogStore.getState().triggerRuns.a.outcome).toBe("failed");
    expect(useLogStore.getState().triggerRuns.b.outcome).toBe("completed");
  });

  it("survives the disconnect sweep that clears macro run state", () => {
    // clearMacroRuns runs on every WS drop. Macro progress is a live session
    // thing and belongs in it; a trigger's last run is a fact about last night
    // and would come straight back on the next read anyway.
    const store = useLogStore.getState();
    store.setTriggerRuns({ a: { outcome: "failed" } });

    store.clearMacroRuns();

    expect(useLogStore.getState().triggerRuns.a.outcome).toBe("failed");
  });
});

describe("which outcomes mean the trigger is not working", () => {
  it("is one rule, so the card and the dashboard cannot disagree", async () => {
    const { isFailedRun } = await import("../components/macros/triggerRuns");

    expect(isFailedRun("failed")).toBe(true);
    expect(isFailedRun("error")).toBe(true);
    // Ordinary operation: a cancel_group preemption is the feature somebody
    // set the group up for, and an overlap guard refusing a second run is the
    // guard working. Marking these would cry wolf on behaving triggers.
    expect(isFailedRun("cancelled")).toBe(false);
    expect(isFailedRun("skipped")).toBe(false);
    expect(isFailedRun("completed")).toBe(false);
    expect(isFailedRun(undefined)).toBe(false);
  });
});
