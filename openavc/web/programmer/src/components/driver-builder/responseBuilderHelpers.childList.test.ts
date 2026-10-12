import { describe, expect, it } from "vitest";

import type { DriverDefinition, DriverResponseDef } from "../../api/types";
import {
  buildResponse,
  childListValueFromParts,
  childListValueMap,
  childListValueToText,
  refOrNumberFromText,
  withChildList,
} from "./responseBuilderHelpers";
import {
  buildQueryEntry,
  carryQueryExtras,
  listReadOptions,
  queryPageSize,
  withPageSize,
} from "./queryEntryHelpers";

const list = {
  type: "output",
  element: "(\\d+)>(\\d+)",
  separator: ",",
  id: "$2",
  state: { input: "$1" },
};

describe("child_list on a response rule", () => {
  it("survives a rebuild of the rule's mappings", () => {
    const rule: DriverResponseDef = {
      match: "^~\\d+@VID (.+)$",
      set: { power: "$1" },
      child_list: list,
    };
    const rebuilt = buildResponse(
      rule.match as string,
      [{ group: 1, state: "power", type: "string" }],
      rule,
      { power: { type: "string" } },
    );
    expect(rebuilt.child_list).toEqual(list);
  });

  it("keeps only_when through a rebuild too", () => {
    const only_when = { key: "mode", operator: "eq", value: "matrix" };
    const rule: DriverResponseDef = {
      match: "^Out(\\d+)$",
      mappings: [{ group: 1, state: "power", type: "string" }],
      only_when,
    };
    const rebuilt = buildResponse(
      "^Out(\\d+) $",
      [{ group: 1, state: "power", type: "string" }],
      rule,
      { power: { type: "string" } },
    );
    expect(rebuilt.only_when).toEqual(only_when);
  });

  it("is set and removed whole", () => {
    const rule: DriverResponseDef = { match: "^Ties (.+)$", child_list: list };
    expect(withChildList(rule, undefined)).toEqual({ match: "^Ties (.+)$" });
    expect(withChildList({ match: "x" }, list).child_list).toEqual(list);
  });

  it("reads a capture ref, a number, or nothing from a start input", () => {
    expect(refOrNumberFromText("$2")).toBe("$2");
    expect(refOrNumberFromText(" 17 ")).toBe(17);
    expect(refOrNumberFromText("")).toBeUndefined();
  });

  it("keeps a value map on a capture and drops it on a literal", () => {
    const mapped = childListValueFromParts("$1", { "1": "true", "0": "false" });
    expect(mapped).toEqual({ group: 1, map: { "1": "true", "0": "false" } });
    expect(childListValueToText(mapped)).toBe("$1");
    expect(childListValueMap(mapped)).toEqual({ "1": "true", "0": "false" });
    expect(childListValueFromParts("$1", undefined)).toBe("$1");
    expect(childListValueFromParts("on", { "1": "true" })).toBe("on");
  });
});

describe("list reads and paged queries", () => {
  it("offers the child variables a child_list rule fills", () => {
    const draft = {
      responses: [
        { match: "^Ties (.+)$", child_list: { ...list, state: { input: "$1", audio_input: "$1" } } },
        { match: "^Pwr(\\d)$", set: { power: "$1" } },
      ],
    } as unknown as DriverDefinition;
    expect(listReadOptions(draft)).toEqual(["output.input", "output.audio_input"]);
  });

  it("sets and clears a page size on a per-child entry only", () => {
    const entry = buildQueryEntry("VC{child_id}\\r", "output", "", undefined, "input");
    const paged = withPageSize(entry, 16);
    expect(queryPageSize(paged)).toBe(16);
    expect(queryPageSize(withPageSize(paged, 1))).toBeUndefined();
    expect(withPageSize("PWR?\\r", 16)).toBe("PWR?\\r");
  });

  it("keeps the page size when the send or the pairing changes", () => {
    const paged = withPageSize(
      buildQueryEntry("VC{child_id}\\r", "output", "", undefined, "input"),
      16,
    );
    const edited = carryQueryExtras(
      paged,
      buildQueryEntry("VC{child_id}*1\\r", "output", "", undefined, "input"),
    );
    expect(queryPageSize(edited)).toBe(16);
    const once = carryQueryExtras(paged, buildQueryEntry("VC1\\r", "", "", undefined, ""));
    expect(once).toBe("VC1\\r");
  });
});
