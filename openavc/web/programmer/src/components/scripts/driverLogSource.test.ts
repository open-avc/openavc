import { describe, expect, it } from "vitest";
import type { PythonDriverInfo } from "../../api/types";
import { driverLogSource } from "./driverLogSource";

function listed(id: string, filename: string): PythonDriverInfo {
  return {
    id, filename, name: id, manufacturer: "", category: "",
    loaded: true, load_error: null, devices_using: [],
  };
}

describe("driverLogSource", () => {
  it("matches a driver's lines by the file it is kept in, not its id", () => {
    const drivers = [listed("acme_widget", "acme_widget_upload.py")];
    expect(driverLogSource(drivers, "acme_widget")).toBe("openavc_driver_acme_widget_upload");
  });

  it("is the id's name for a driver named for its id", () => {
    expect(driverLogSource([listed("acme_widget", "acme_widget.py")], "acme_widget"))
      .toBe("openavc_driver_acme_widget");
  });

  it("falls back to the id for a driver not in the list", () => {
    expect(driverLogSource([], "acme_widget")).toBe("openavc_driver_acme_widget");
  });
});
