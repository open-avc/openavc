import { describe, expect, it } from "vitest";
import { instancesForSource } from "./childEntityTypesHelpers";

const followed = {
  count_from: "outputs",
  count_from_state: "num_outputs",
  label: "Output {id}",
  presence: "reported" as const,
};

describe("instancesForSource", () => {
  it("keeps the followed count only for a config-field count", () => {
    expect(instancesForSource("count_from", followed, ["frame_size"])).toEqual({
      count_from: "frame_size",
      count_from_state: "num_outputs",
      label: "Output {id}",
      presence: "reported",
    });
  });

  it.each([
    ["count", { count: 2 }],
    ["ids", { ids: [] }],
    ["ids_from", { ids_from: "frame_size" }],
  ] as const)("drops it for %s, which fixes the roster", (next, written) => {
    expect(instancesForSource(next, followed, ["frame_size"])).toEqual({
      ...written,
      label: "Output {id}",
      presence: "reported",
    });
  });

  it("clears the block for no roster", () => {
    expect(instancesForSource("none", followed, [])).toBeUndefined();
  });
});

describe("a new config field for the source", () => {
  it("keeps the followed count when the count's field changes", () => {
    expect(instancesForSource("count_from", followed, ["frame_size"])).toEqual({
      count_from: "frame_size",
      count_from_state: "num_outputs",
      label: "Output {id}",
      presence: "reported",
    });
  });

  it("never carries it to an ID-list field", () => {
    expect(instancesForSource("ids_from", followed, ["zones"])).toEqual({
      ids_from: "zones",
      label: "Output {id}",
      presence: "reported",
    });
  });
});

describe("a roster beside a fixed source", () => {
  it("does not carry a stray followed count to count_from", () => {
    expect(instancesForSource("count_from", { count: 4, count_from_state: "n" }, ["f"])).toEqual({
      count_from: "f",
    });
  });
});
