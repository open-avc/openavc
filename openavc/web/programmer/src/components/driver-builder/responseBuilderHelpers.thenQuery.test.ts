import { describe, expect, it } from "vitest";

import type { DriverResponseDef } from "../../api/types";
import {
  buildResponse,
  routedChildType,
  withThenQuery,
} from "./responseBuilderHelpers";
import { namesRoutedChild } from "./queryEntryHelpers";

describe("then_query on a response rule", () => {
  it("survives a rebuild of the rule's mappings", () => {
    const rule: DriverResponseDef = {
      match: "^CHG$",
      set: {},
      then_query: [{ each_child: "output", send: "IN? {child_id}\\r", query_for: "input" }],
    };
    const rebuilt = buildResponse("^CHG (\\d)$", [], rule, {});
    expect(rebuilt.then_query).toEqual(rule.then_query);
  });

  it("is removed when its last query is", () => {
    const rule: DriverResponseDef = { match: "^CHG$", then_query: ["PWR?\\r"] };
    expect(withThenQuery(rule, ["VOL?\\r"]).then_query).toEqual(["VOL?\\r"]);
    expect("then_query" in withThenQuery(rule, [])).toBe(false);
  });

  it("names the one child type the rule writes", () => {
    const one: DriverResponseDef = {
      match: "^AVAIL (\\d) YES$",
      child_set: [{ type: "output", id: "$1", state: { available: "true" } }],
    };
    expect(routedChildType(one)).toBe("output");
    expect(routedChildType({ match: "^CHG$" })).toBeUndefined();
    const two: DriverResponseDef = {
      match: "^LINK (\\d) (\\d)$",
      child_set: [
        { type: "output", id: "$1", state: { input: "$2" } },
        { type: "input", id: "$2", state: { routed: "true" } },
      ],
    };
    expect(routedChildType(two)).toBeUndefined();
  });

  it("recognises {child_id} with or without a format", () => {
    expect(namesRoutedChild("NAME? {child_id}\\r")).toBe(true);
    expect(namesRoutedChild("NAME? {child_id:02d}\\r")).toBe(true);
    expect(namesRoutedChild("NAME? ALL\\r")).toBe(false);
  });
});
