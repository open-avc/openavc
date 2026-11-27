import { describe, it, expect } from "vitest";
import { PRESENCE_WORDS, presenceKey, type PresenceWord } from "../components/shared/devicePresence";
import { STATUS_CHIPS, computeStatusCounts, deviceStatusOf, type DeviceListStatus } from "./deviceViewHelpers";

const CHIP: Record<PresenceWord, DeviceListStatus> = {
  disabled: "disabled",
  waiting_for_driver: "waiting_for_driver",
  online: "online",
  restarting: "restarting",
  asleep: "asleep",
  paused: "paused",
  offline: "offline",
  connecting: "offline",
};

describe("deviceStatusOf", () => {
  it.each(PRESENCE_WORDS)("puts a device that reads %s under its chip", (word) => {
    expect(deviceStatusOf("proj", { [presenceKey("proj")]: word })).toBe(CHIP[word]);
  });

  it("reads the word, not the parts it is made of", () => {
    expect(
      deviceStatusOf("proj", {
        "device.proj.connected": false,
        "device.proj.paused": true,
        [presenceKey("proj")]: "asleep",
      }),
    ).toBe("asleep");
  });

  it("counts a device with no word yet as offline", () => {
    expect(deviceStatusOf("proj", {})).toBe("offline");
  });
});

describe("STATUS_CHIPS", () => {
  it("has a chip for every status, labelled", () => {
    expect(STATUS_CHIPS.map((c) => [c.key, c.label])).toEqual([
      ["online", "Online"],
      ["offline", "Offline"],
      ["restarting", "Restarting"],
      ["paused", "Paused"],
      ["asleep", "Asleep"],
      ["disabled", "Disabled"],
      ["waiting_for_driver", "Orphaned"],
    ]);
    expect(new Set(STATUS_CHIPS.map((c) => c.key))).toEqual(new Set(Object.values(CHIP)));
  });
});

describe("computeStatusCounts", () => {
  it("counts each device once, under its chip", () => {
    const devices = PRESENCE_WORDS.map((word) => ({ id: word }));
    const live = Object.fromEntries(PRESENCE_WORDS.map((word) => [presenceKey(word), word]));
    expect(computeStatusCounts(devices, live)).toEqual({
      total: 8,
      online: 1,
      offline: 2,
      restarting: 1,
      paused: 1,
      asleep: 1,
      disabled: 1,
      waiting_for_driver: 1,
    });
  });
});
