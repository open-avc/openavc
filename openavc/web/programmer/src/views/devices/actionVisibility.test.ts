import { describe, it, expect } from "vitest";
import { evalVisibleWhen } from "./actionVisibility";
import type { ActionVisibleWhen } from "../../api/types";

const state = { "device.amp.mode": "on" };
const when = (vw: unknown) => evalVisibleWhen(vw as ActionVisibleWhen, state, "amp");

describe("evalVisibleWhen: an operator nobody knows", () => {
  it("never holds, so an action shown only by it stays hidden", () => {
    const unknown = { key: "device.$id.mode", operator: "contains", value: "on" };
    expect(when({ key: "device.$id.mode", operator: "eq", value: "on" })).toBe(true);
    expect(when(unknown)).toBe(false);
    expect(when({ all: [{ key: "device.$id.mode", operator: "eq", value: "on" }, unknown] })).toBe(false);
    expect(when({ any: [unknown] })).toBe(false);
  });
});
