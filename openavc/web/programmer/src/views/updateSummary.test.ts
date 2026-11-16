import { describe, expect, it } from "vitest";
import type { UpdateNote } from "../api/projectClient";
import { updateSummary } from "./updateSummary";

const note = (kind: UpdateNote["kind"]): UpdateNote =>
  ({ kind, message: "m", location: "Main > x", page_id: "main", element_id: "x" });

describe("the update notice's summary", () => {
  it("counts the changes made and the issues Validate lists now", () => {
    expect(updateSummary([note("changed"), note("changed"), note("found")], 1)).toBe("2 changes; 1 issue to review");
  });

  it("counts a found value only through Validate, which lists it", () => {
    expect(updateSummary([note("found")], 3)).toBe("3 issues to review");
  });

  it("leaves out a half that is zero, or not yet known", () => {
    expect(updateSummary([note("changed")], 0)).toBe("1 change");
    expect(updateSummary([note("changed")], null)).toBe("1 change");
    expect(updateSummary([note("found")], null)).toBe("");
  });
});
