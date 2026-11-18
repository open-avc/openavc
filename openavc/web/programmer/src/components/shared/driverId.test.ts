import { describe, expect, it } from "vitest";
import { cleanedDriverId, isDriverId, typedDriverId } from "./driverId";

describe("the driver id rule", () => {
  it("takes lowercase letters, digits and underscores, judged whole", () => {
    expect(isDriverId("acme_widget_2")).toBe(true);
    for (const id of ["Acme", "acme-widget", "acme.widget", "acme widget", "acme_widget\n", "", 5]) {
      expect(isDriverId(id)).toBe(false);
    }
  });

  it("leaves out what an id cannot hold as it is typed", () => {
    expect(typedDriverId("Acme-Widget_2")).toBe("acmewidget_2");
  });

  it("makes other text an id: lowercase, one underscore per run, none at either end", () => {
    expect(cleanedDriverId(" Acme.Widget -- Pro ")).toBe("acme_widget_pro");
    expect(cleanedDriverId("...")).toBe("");
  });
});
