import { describe, expect, it } from "vitest";
import { buildTableValue, existingRows, type ColumnDef } from "./ConfigTableEditor";

const columns: Record<string, ColumnDef> = {
  name: { type: "string", label: "Name", required: true },
  address: { type: "integer", label: "Address", min: 1, max: 255 },
  enabled: { type: "boolean", label: "Enabled" },
};

describe("a table field's rows", () => {
  it("reads a stored value as rows and writes the rows back typed", () => {
    const stored = [{ name: "Fader 1", address: 16, enabled: true }, { name: "Mute" }];
    const rows = existingRows(stored, Object.keys(columns));
    expect(rows).toEqual([
      { name: "Fader 1", address: "16", enabled: "true" },
      { name: "Mute", address: "", enabled: "" },
    ]);
    expect(buildTableValue(rows, columns, "object")).toEqual({
      rows: [{ name: "Fader 1", address: 16, enabled: true }, { name: "Mute" }],
    });
  });

  it("drops a blank row and names the first problem by row and column", () => {
    expect(buildTableValue([{ name: "", address: "", enabled: "" }], columns, "object"))
      .toEqual({ rows: [] });
    expect(buildTableValue([{ name: "", address: "3", enabled: "" }], columns, "object"))
      .toEqual({ error: 'object 1: "Name" is required.' });
    expect(buildTableValue([{ name: "A", address: "300", enabled: "" }], columns, "object"))
      .toEqual({ error: 'object 1: "Address" must be ≤ 255.' });
  });

  it("starts empty from anything that is not a list", () => {
    expect(existingRows(undefined, ["name"])).toEqual([]);
    expect(existingRows("not a list", ["name"])).toEqual([]);
  });
});
