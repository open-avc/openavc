import { describe, it, expect } from "vitest";
import { driverMatchesSearch } from "./InstalledDriversView";

// The Installed list's search box: a driver is listed when every word typed
// appears in its name, manufacturer or category, wherever each one is.

const SWITCH = { name: "Acme Brindle Network Switch", manufacturer: "Acme", category: "switchers" };

describe("the Installed list's search", () => {
  it("lists every driver when nothing, or only spaces, is typed", () => {
    expect(driverMatchesSearch(SWITCH, "")).toBe(true);
    expect(driverMatchesSearch(SWITCH, "   ")).toBe(true);
  });

  it("matches one word anywhere in the name, manufacturer or category, ignoring case", () => {
    expect(driverMatchesSearch(SWITCH, "brindle")).toBe(true);
    expect(driverMatchesSearch(SWITCH, "ACME")).toBe(true);
    expect(driverMatchesSearch(SWITCH, "switchers")).toBe(true);
    expect(driverMatchesSearch(SWITCH, "tuner")).toBe(false);
  });

  it("matches several words that are not next to each other", () => {
    expect(driverMatchesSearch(SWITCH, "brindle switch")).toBe(true);
    expect(driverMatchesSearch(SWITCH, " acme   switchers ")).toBe(true);
  });

  it("leaves out a driver missing any of the words", () => {
    expect(driverMatchesSearch(SWITCH, "brindle tuner")).toBe(false);
  });
});
