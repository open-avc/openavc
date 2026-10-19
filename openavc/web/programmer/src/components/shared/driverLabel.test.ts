import { describe, expect, it } from "vitest";
import { driverLabel } from "./driverLabel";

const PRODUCT = "openavc_builtin_3ccbe3eec92b9c851f9e57dff5ed9d4341894e5c3f04d86298e76db04f296232";

describe("driverLabel", () => {
  it("reads product hardware by name", () => {
    expect(driverLabel(PRODUCT)).toBe("Product hardware");
  });

  it("reads every other driver by its ID", () => {
    expect(driverLabel("pjlink_class1")).toBe("pjlink_class1");
    expect(driverLabel("openavc_builtin_widget")).toBe("openavc_builtin_widget");
    expect(driverLabel(`${PRODUCT}0`)).toBe(`${PRODUCT}0`);
    expect(driverLabel(undefined)).toBe("");
  });
});
