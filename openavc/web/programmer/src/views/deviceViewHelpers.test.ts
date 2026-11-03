import { describe, it, expect } from "vitest";
import { computeStatusCounts, deviceStatusOf } from "./deviceViewHelpers";

describe("deviceStatusOf", () => {
  it("reads a connected device as online", () => {
    expect(deviceStatusOf("proj", { "device.proj.connected": true })).toBe("online");
  });

  it("reads a device that is not connected as offline", () => {
    expect(deviceStatusOf("proj", { "device.proj.connected": false })).toBe("offline");
    expect(deviceStatusOf("proj", {})).toBe("offline");
  });

  it("reads a device put to sleep as asleep, not offline", () => {
    expect(
      deviceStatusOf("proj", {
        "device.proj.connected": false,
        "device.proj.asleep": true,
      }),
    ).toBe("asleep");
  });

  it("reads the server's null asleep as offline", () => {
    expect(
      deviceStatusOf("proj", {
        "device.proj.connected": false,
        "device.proj.asleep": null,
      }),
    ).toBe("offline");
  });

  it("puts orphaned ahead of everything else", () => {
    expect(
      deviceStatusOf("proj", {
        "device.proj.orphaned": true,
        "device.proj.asleep": true,
      }),
    ).toBe("orphaned");
  });
});

describe("computeStatusCounts", () => {
  it("counts an asleep device under asleep and not under offline", () => {
    const counts = computeStatusCounts(
      [{ id: "a" }, { id: "b" }, { id: "c" }, { id: "d" }],
      {
        "device.a.connected": true,
        "device.b.connected": false,
        "device.c.connected": false,
        "device.c.asleep": true,
        "device.d.orphaned": true,
      },
    );
    expect(counts).toEqual({ total: 4, online: 1, offline: 1, asleep: 1, orphaned: 1 });
  });
});
