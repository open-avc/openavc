import { describe, expect, it } from "vitest";

import {
  buildJsonResponse,
  containsFromText,
  containsToText,
  getJsonRows,
  jsonChildPropFromText,
} from "./responseBuilderHelpers";

const stateVars = {
  no_link: { type: "boolean" },
  warning_count: { type: "integer" },
};

describe("json rule contains", () => {
  it("reads contains from a set spec and writes it back", () => {
    const rule = {
      json: true,
      set: {
        no_link: { key: "warnings", contains: "NoLink" },
        warning_count: "warnings",
      },
    };
    const rows = getJsonRows(rule, stateVars);
    expect(rows).toEqual([
      { state: "no_link", path: "warnings", type: "boolean", contains: "NoLink" },
      { state: "warning_count", path: "warnings", type: "integer" },
    ]);
    expect(buildJsonResponse(rule, rows, [], stateVars)).toEqual(rule);
  });

  it("keeps contains in the mappings form", () => {
    const rule = {
      json: true,
      mappings: [
        { state: "no_link", key: "warnings", type: "boolean", contains: "NoLink" },
        { state: "no_link", key: "faults", type: "boolean", contains: "NoLink" },
      ],
    };
    const rows = getJsonRows(rule, stateVars);
    expect(rows[1].contains).toBe("NoLink");
    // Duplicate state names force the mappings form; contains survives.
    expect(buildJsonResponse(rule, rows, [], stateVars)).toEqual(rule);
  });

  it("parses the input as the scalar the device reports", () => {
    expect(containsFromText("NoLink")).toBe("NoLink");
    expect(containsFromText("7")).toBe(7);
    expect(containsFromText("true")).toBe(true);
    expect(containsFromText("  ")).toBeUndefined();
    expect(containsToText(7)).toBe("7");
    expect(containsToText(undefined)).toBe("");
  });

  it("editing a child prop path keeps its contains", () => {
    const original = { key: "/api/channel/0/warnings", contains: "NoLink" };
    expect(jsonChildPropFromText("/api/channel/1/warnings", original)).toEqual({
      key: "/api/channel/1/warnings",
      contains: "NoLink",
    });
    expect(jsonChildPropFromText("plain", "old")).toBe("plain");
  });
});
