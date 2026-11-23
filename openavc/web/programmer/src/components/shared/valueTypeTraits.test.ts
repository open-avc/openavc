import { describe, it, expect } from "vitest";
import { VALUE_TYPE_OPTIONS, isNumericType, isRealType, valueTypeTraits } from "./valueTypeTraits";

describe("value type traits", () => {
  it("offers the types in the pickers' order, Float beside Number", () => {
    expect(VALUE_TYPE_OPTIONS.map((o) => o.label)).toEqual([
      "String", "Integer", "Number", "Float", "Boolean", "Enum",
    ]);
  });

  it("reads float as a real number, like number", () => {
    expect(isNumericType("float") && isRealType("float")).toBe(true);
    expect(isNumericType("integer") && !isRealType("integer")).toBe(true);
  });

  it("reads a missing type as string and an unknown one as no row", () => {
    expect(valueTypeTraits(undefined)?.label).toBe("String");
    expect(valueTypeTraits("double")).toBeUndefined();
  });
});
