import { describe, it, expect } from "vitest";
import { asleepHeadline, summarizeDashboardDevices } from "./dashboardDevices";

const DEVICES = [
  { id: "proj", name: "Projector" },
  { id: "disp", name: "Lobby Display" },
  { id: "pc", name: "vMix PC" },
  { id: "amp", name: "Amplifier", enabled: false },
];

describe("summarizeDashboardDevices", () => {
  it("lists an asleep device on its own line, not among the disconnected", () => {
    const s = summarizeDashboardDevices(DEVICES, {
      "device.proj.connected": true,
      "device.disp.connected": false,
      "device.pc.connected": false,
      "device.pc.asleep": true,
    });
    expect(s.connected).toBe(1);
    expect(s.enabled).toBe(3);
    expect(s.disconnected).toEqual(["Lobby Display"]);
    expect(s.asleep).toEqual(["vMix PC"]);
  });

  it("reports nothing disconnected when the only device away is asleep", () => {
    const s = summarizeDashboardDevices(DEVICES.slice(0, 1).concat(DEVICES[2]), {
      "device.proj.connected": true,
      "device.pc.connected": false,
      "device.pc.asleep": true,
    });
    expect(s.disconnected).toEqual([]);
    expect(s.asleep).toEqual(["vMix PC"]);
  });

  it("counts a device whose asleep is the server's null as disconnected", () => {
    const s = summarizeDashboardDevices([DEVICES[2]], {
      "device.pc.connected": false,
      "device.pc.asleep": null,
    });
    expect(s.disconnected).toEqual(["vMix PC"]);
    expect(s.asleep).toEqual([]);
  });

  it("leaves disabled devices out of every figure", () => {
    const s = summarizeDashboardDevices([DEVICES[3]], {
      "device.amp.connected": false,
      "device.amp.asleep": true,
    });
    expect(s).toEqual({ connected: 0, enabled: 0, disconnected: [], asleep: [] });
  });
});

describe("asleepHeadline", () => {
  it("agrees in number", () => {
    expect(asleepHeadline(1)).toBe("1 device asleep");
    expect(asleepHeadline(2)).toBe("2 devices asleep");
  });
});
