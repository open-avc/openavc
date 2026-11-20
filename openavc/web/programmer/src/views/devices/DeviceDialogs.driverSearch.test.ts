import { describe, it, expect } from "vitest";
import { driverPickerShows } from "./DeviceDialogs";
import type { DriverInfo } from "../../api/types";

// The driver picker in Add Device and Edit Device: a driver is listed when
// every word typed appears in its name (its id when it has none), its
// manufacturer or its category, wherever each one is, as the Installed list
// and Browse Community find one.

const SWITCH: DriverInfo = {
  id: "acme_brindle",
  name: "Acme Brindle Network Switch",
  manufacturer: "Acme",
  category: "switcher",
  commands: {},
  config_schema: {},
};

const UNNAMED: DriverInfo = { id: "acme_relay_box", name: "", manufacturer: "Acme", category: "utility", commands: {}, config_schema: {} };

describe("the Add Device driver picker's search", () => {
  it("lists every driver when nothing, or only spaces, is typed", () => {
    expect(driverPickerShows(SWITCH, "")).toBe(true);
    expect(driverPickerShows(SWITCH, "   ")).toBe(true);
  });

  it("matches one word anywhere in the name, manufacturer or category, ignoring case", () => {
    expect(driverPickerShows(SWITCH, "Network")).toBe(true);
    expect(driverPickerShows(SWITCH, "SWITCHER")).toBe(true);
    expect(driverPickerShows(SWITCH, "tuner")).toBe(false);
  });

  it("matches several words that are not next to each other", () => {
    expect(driverPickerShows(SWITCH, "brindle switch")).toBe(true);
    expect(driverPickerShows(SWITCH, "switch acme")).toBe(true);
    expect(driverPickerShows(SWITCH, "acme switcher")).toBe(true);
  });

  it("leaves out a driver missing any of the words", () => {
    expect(driverPickerShows(SWITCH, "brindle tuner")).toBe(false);
  });

  it("searches the id of a driver with no name", () => {
    expect(driverPickerShows(UNNAMED, "relay")).toBe(true);
    expect(driverPickerShows(UNNAMED, "acme relay")).toBe(true);
  });
});
