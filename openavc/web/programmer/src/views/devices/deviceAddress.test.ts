import { describe, expect, it } from "vitest";

import { formatDeviceAddress } from "./deviceAddress";

describe("formatDeviceAddress", () => {
  it("joins a network host and port", () => {
    expect(
      formatDeviceAddress({ transport: "tcp", host: "10.0.0.5", port: 4000, bridge: "" }),
    ).toBe("10.0.0.5:4000");
  });

  it("names a serial device by its port", () => {
    expect(
      formatDeviceAddress({ transport: "serial", host: "", port: "COM3", bridge: "" }),
    ).toBe("COM3");
  });

  it("names the bridge a device is reached through", () => {
    expect(
      formatDeviceAddress(
        { transport: "tcp", host: "10.0.0.7", port: 4999, bridge: "itach" },
        (id) => (id === "itach" ? "Rack iTach" : id),
      ),
    ).toBe("10.0.0.7:4999 through Rack iTach");
  });

  it("is empty when there is no address", () => {
    expect(formatDeviceAddress({ transport: "tcp", host: "", port: 4000, bridge: "" })).toBe("");
    expect(formatDeviceAddress(undefined)).toBe("");
  });

  it("shows a host alone when no port is known", () => {
    expect(
      formatDeviceAddress({ transport: "tcp", host: "10.0.0.5", port: null, bridge: "" }),
    ).toBe("10.0.0.5");
  });
});
