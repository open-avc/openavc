import { describe, it, expect } from "vitest";
import { advertisedBridgePorts, devicesOnBridgePort } from "./bridgePorts";

const dev = (id: string) => ({ id, driver: "acme_widget", name: id, config: {}, enabled: true });

describe("devicesOnBridgePort", () => {
  const devices = [dev("a"), dev("b"), dev("c"), dev("d")];
  const connections = {
    a: { bridge: "itach", bridge_port: "1:1" },
    b: { bridge: "itach", bridge_port: "1:2" },
    c: { bridge: "other", bridge_port: "1:1" },
    d: { host: "10.0.0.9" },
  };

  it("lists the devices that name both the bridge and the port, in project order", () => {
    const both = { ...connections, d: { bridge: "itach", bridge_port: "1:1" } };
    expect(devicesOnBridgePort(devices, both, "itach", "1:1").map((d) => d.id)).toEqual(["a", "d"]);
  });

  it("leaves out another bridge's port of the same name, and another port", () => {
    expect(devicesOnBridgePort(devices, connections, "itach", "1:1").map((d) => d.id)).toEqual(["a"]);
    expect(devicesOnBridgePort(devices, connections, "itach", "1:3")).toEqual([]);
  });

  it("finds nothing without a connections table", () => {
    expect(devicesOnBridgePort(devices, undefined, "itach", "1:1")).toEqual([]);
  });
});

describe("advertisedBridgePorts", () => {
  it("reads the driver's ports, and none when it advertises none", () => {
    const ports = [{ id: "1:1", kind: "ir" }];
    expect(advertisedBridgePorts({ bridge: { ports } })).toBe(ports);
    expect(advertisedBridgePorts({ bridge: {} })).toEqual([]);
    expect(advertisedBridgePorts({})).toEqual([]);
    expect(advertisedBridgePorts(undefined)).toEqual([]);
  });
});
