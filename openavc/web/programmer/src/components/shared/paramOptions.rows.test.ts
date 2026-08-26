import { describe, it, expect } from "vitest";
import { parseStateOptionList, parseStateOptionRows } from "./paramOptions";

// The two readers of one published list. `parseStateOptionList` answers "what
// can I choose"; `parseStateOptionRows` answers "what is there". The gap
// between them is the whole convention: a row with no `value` is on screen to
// say why something that ought to be here is not, and every picker written
// before that idea drops it untouched.

const LIST = JSON.stringify([
  { value: "auto-vmix-output-2", label: "vMix Output 2 - Preview", group: "vMix" },
  {
    value: "auto-chazy-encoder-001",
    label: "Podium PC",
    group: "Encoders",
    status: "offline",
    detail: "This device is not connected right now.",
  },
  {
    id: "auto-vmix-output-3",
    label: "vMix Output 3 - MultiView",
    group: "vMix",
    status: "needs_setup",
    detail: "Enter the SRT Port shown beside it in vMix.",
    setup: { device: "video_1", field: "srt_port_3" },
  },
]);

describe("a published option list that has something to say", () => {
  it("hides the rows that cannot be picked from the plain reader", () => {
    // The compatibility property, and the reason no version gate was needed:
    // a picker that predates all of this shows exactly what it showed before.
    expect(parseStateOptionList(LIST).map((o) => o.value)).toEqual([
      "auto-vmix-output-2",
      "auto-chazy-encoder-001",
    ]);
  });

  it("keeps them for a reader that can explain them", () => {
    const rows = parseStateOptionRows(LIST);
    expect(rows).toHaveLength(3);
    const explained = rows.filter((r) => r.value === undefined);
    expect(explained).toHaveLength(1);
    expect(explained[0].id).toBe("auto-vmix-output-3");
    expect(explained[0].status).toBe("needs_setup");
    expect(explained[0].setup).toEqual({ device: "video_1", field: "srt_port_3" });
  });

  it("carries the group and the mark on a row that can still be picked", () => {
    // An offline camera is still the camera a page is being built against, so
    // it stays choosable and is marked rather than hidden.
    const row = parseStateOptionRows(LIST).find((r) => r.value === "auto-chazy-encoder-001");
    expect(row?.group).toBe("Encoders");
    expect(row?.status).toBe("offline");
  });

  it("still reads a plain list of strings", () => {
    expect(parseStateOptionRows(JSON.stringify(["a", "b"]))).toEqual([
      { value: "a", label: "a" },
      { value: "b", label: "b" },
    ]);
  });

  it("skips a row with neither a value nor an id, having nothing to key it by", () => {
    expect(parseStateOptionRows(JSON.stringify([{ label: "orphan" }]))).toEqual([]);
  });

  it("ignores a malformed setup block rather than half-offering a field", () => {
    const rows = parseStateOptionRows(
      JSON.stringify([{ id: "x", label: "X", setup: { device: "d" } }]),
    );
    expect(rows[0].setup).toBeUndefined();
  });

  it("survives anything that is not a list", () => {
    expect(parseStateOptionRows("not json")).toEqual([]);
    expect(parseStateOptionRows(JSON.stringify({ a: 1 }))).toEqual([]);
    expect(parseStateOptionRows(undefined)).toEqual([]);
  });
});
