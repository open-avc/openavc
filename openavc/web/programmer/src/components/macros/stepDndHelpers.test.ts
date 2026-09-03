import { describe, it, expect } from "vitest";
import {
  getStepIds,
  carryStepId,
  applyStepReorder,
  adjustExpandedAfterMove,
} from "./stepDndHelpers";

/**
 * The one id space behind the step list: SortableContext's items, the ids each
 * row registers with dnd-kit, and the React keys all read from it. Two rows
 * sharing an id break the drag; a row whose id changes under an edit is torn
 * down mid-keystroke and takes the caret with it. Both halves are pinned here.
 */

type Step = { action: string; seconds?: number };

function freshMap() {
  return {
    idMap: new WeakMap<Step, string>(),
    counter: { current: 0 },
  };
}

describe("step ids", () => {
  it("gives identical-looking steps ids of their own", () => {
    const { idMap, counter } = freshMap();
    const steps: Step[] = [{ action: "delay" }, { action: "delay" }];
    const ids = getStepIds(steps, idMap, counter);
    expect(new Set(ids).size).toBe(2);
  });

  it("keeps a step's id when it moves", () => {
    const { idMap, counter } = freshMap();
    const a: Step = { action: "delay", seconds: 1 };
    const b: Step = { action: "delay", seconds: 2 };
    const before = getStepIds([a, b], idMap, counter);
    const after = getStepIds([b, a], idMap, counter);
    expect(after).toEqual([before[1], before[0]]);
  });

  it("keeps a step's id when an edit replaces the object", () => {
    const { idMap, counter } = freshMap();
    const original: Step = { action: "delay", seconds: 1 };
    const [id] = getStepIds([original], idMap, counter);

    const edited: Step = { ...original, seconds: 10 };
    carryStepId(idMap, original, edited);

    expect(getStepIds([edited], idMap, counter)).toEqual([id]);
  });

  it("gives an added or duplicated step an id of its own", () => {
    const { idMap, counter } = freshMap();
    const original: Step = { action: "delay", seconds: 1 };
    const [id] = getStepIds([original], idMap, counter);

    // A duplicate is a copy of the same content, and must not inherit the id:
    // two rows sharing one would collide as React keys and as dnd-kit ids.
    const copy: Step = { ...original };
    const ids = getStepIds([original, copy], idMap, counter);
    expect(ids[0]).toBe(id);
    expect(ids[1]).not.toBe(id);
  });

  it("has nothing to carry when the object was not replaced", () => {
    const { idMap, counter } = freshMap();
    const step: Step = { action: "delay" };
    const [id] = getStepIds([step], idMap, counter);
    carryStepId(idMap, step, step);
    expect(getStepIds([step], idMap, counter)).toEqual([id]);
  });
});

describe("reordering by drag", () => {
  it("moves the dragged step to the drop position", () => {
    const steps = ["a", "b", "c"];
    const result = applyStepReorder(steps, ["s0", "s1", "s2"], "s0", "s2");
    expect(result).toEqual({ steps: ["b", "c", "a"], oldIndex: 0, newIndex: 2 });
  });

  it("does nothing on a drop that changes nothing or an id it does not know", () => {
    const steps = ["a", "b"];
    expect(applyStepReorder(steps, ["s0", "s1"], "s0", "s0")).toBeNull();
    expect(applyStepReorder(steps, ["s0", "s1"], "s0", "s9")).toBeNull();
  });

  it("keeps the open step open on the step it was opened on", () => {
    expect(adjustExpandedAfterMove(null, 0, 2)).toBeNull();
    // The open step is the one dragged.
    expect(adjustExpandedAfterMove(1, 1, 3)).toBe(3);
    // Another step moves from above it to below it.
    expect(adjustExpandedAfterMove(2, 0, 3)).toBe(1);
    // ...and back the other way.
    expect(adjustExpandedAfterMove(2, 4, 1)).toBe(3);
    // A move that steps over neither side leaves it alone.
    expect(adjustExpandedAfterMove(0, 2, 3)).toBe(0);
  });
});
