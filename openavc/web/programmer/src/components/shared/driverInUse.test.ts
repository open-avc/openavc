import { describe, expect, it } from "vitest";
import { ApiError } from "../../api/errors";
import { devicesOnDriver, inUseDevices } from "./driverInUse";

describe("devicesOnDriver", () => {
  const devices = [
    { id: "room_a", name: "Room A Projector", driver: "acme_widget" },
    { id: "room_b", name: "Room B Display", driver: "acme_other" },
    { id: "lobby_1", name: "", driver: "acme_widget" },
  ];

  it("names each project device on the driver, by its id when it has no name", () => {
    expect(devicesOnDriver(devices, "acme_widget")).toEqual([
      { id: "room_a", name: "Room A Projector" },
      { id: "lobby_1", name: "lobby_1" },
    ]);
  });

  it("is empty with no project or no device on the driver", () => {
    expect(devicesOnDriver(undefined, "acme_widget")).toEqual([]);
    expect(devicesOnDriver(devices, "acme_gadget")).toEqual([]);
  });
});

describe("inUseDevices", () => {
  const refusal = (body: object) => new ApiError(409, JSON.stringify(body));

  it("reads the devices a delete refused as in use names", () => {
    const devices = [{ id: "room_a", name: "Room A Projector" }];
    expect(inUseDevices(refusal({ detail: "...", code: "driver_in_use", devices }))).toEqual(devices);
  });

  it("is null for any other failure", () => {
    expect(inUseDevices(refusal({ detail: "Busy", code: "driver_changed", revision: "r2" }))).toBeNull();
    expect(inUseDevices(refusal({ detail: "...", code: "driver_in_use", devices: [{ id: 1 }] }))).toBeNull();
    expect(inUseDevices(new Error("offline"))).toBeNull();
  });
});
