import { describe, expect, it } from "vitest";

import { connectionTestText, formatDeviceAddress } from "./deviceAddress";

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

describe("connectionTestText", () => {
  const at = { transport: "tcp", host: "192.168.4.250", port: 4321, bridge: "" };

  it("names the address it reached", () => {
    expect(
      connectionTestText({ success: true, error: null, latency_ms: 70, connection: at }),
    ).toBe("Connected to 192.168.4.250:4321 (70 ms)");
  });

  it("names the bridge a success went through", () => {
    expect(
      connectionTestText(
        { success: true, error: null, latency_ms: 9, connection: { ...at, bridge: "itach" } },
        (id) => (id === "itach" ? "Rack iTach" : id),
      ),
    ).toBe("Connected to 192.168.4.250:4321 through Rack iTach (9 ms)");
  });

  it("shows no time for a device answered from the connection it has", () => {
    expect(
      connectionTestText({
        success: true, error: null, latency_ms: null, connection: at,
        in_use_by: "Lobby Display",
        note: "The device is connected now. The test did not open a second connection to it.",
      }),
    ).toBe("Connected to 192.168.4.250:4321");
  });

  it("shows a failure as the server's sentence, which names the address once", () => {
    const error = "Can't reach 192.168.4.250:4321. Check the IP address and network.";
    expect(
      connectionTestText({ success: false, error, latency_ms: null, connection: at }),
    ).toBe(error);
  });

  it("shows a settings problem without an address in front of it", () => {
    const error =
      "No port is set for this device, and its driver does not supply one. " +
      "Enter the port in the device's Connection Settings.";
    expect(
      connectionTestText({
        success: false, error, latency_ms: null, connection: { ...at, port: null },
      }),
    ).toBe(error);
  });
});
