import { describe, expect, it } from "vitest";
import { problemsToFix } from "./driverProblems";

describe("problemsToFix", () => {
  it("counts one problem in the singular and puts it on its own line", () => {
    expect(problemsToFix(["State variable 'relay': unknown type 'bool'; use 'boolean'"])).toBe(
      "The driver check found 1 problem to fix:\n• State variable 'relay': unknown type 'bool'; use 'boolean'"
    );
  });

  it("lists several, one a line", () => {
    expect(problemsToFix(["a", "b"])).toBe("The driver check found 2 problems to fix:\n• a\n• b");
  });
});
